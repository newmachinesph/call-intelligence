"""Address candidate checks and reviews; never edits the transcript, analysis or rubric points."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

import certifi
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from call_intelligence_api.auth import (
    AuthenticatedMember,
    domain_engine,
    get_current_member,
)
from call_intelligence_api.call_models import AnalysisRun, CallRecord, ReviewEvent
from call_intelligence_api.call_routes import (
    AddressCheckResponse,
    address_check_response,
)
from call_intelligence_api.crm_models import AddressCheck, Contact, ContactAddress

router = APIRouter(prefix="/api/v1", tags=["addresses"])
Member = Annotated[AuthenticatedMember, Depends(get_current_member)]

VALIDATE_URL = "https://addressvalidation.googleapis.com/v1:validateAddress"
# developers.google.com/maps/documentation/address-validation/coverage (IN/JP are pre-GA).
GOOGLE_COVERAGE = frozenset({
    "AR", "AT", "AU", "BE", "BG", "BR", "CA", "CH", "CL", "CO", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GB",
    "HR", "HU", "IE", "IN", "IT", "JP", "LT", "LU", "LV", "MX", "MY", "NL", "NO", "NZ", "PL", "PR", "PT", "SE",
    "SG", "SI", "SK", "US",
})
FIELDS = ("line1", "unit", "city", "region", "postal_code", "country_code")
VERDICT_KEYS = (
    "inputGranularity", "validationGranularity", "geocodeGranularity", "addressComplete",
    "hasUnconfirmedComponents", "hasInferredComponents", "hasReplacedComponents",
    "hasSpellCorrectedComponents", "possibleNextAction",
)
# ponytail: Google routinely infers these without changing what was spoken; any other inference needs review.
BENIGN_INFERRED = frozenset({"country", "postal_code_suffix"})


class AddressSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    google_maps_api_key: SecretStr | None = None
    google_address_validation_enabled: bool = False
    google_address_validation_regions: str | None = None

    def regions(self) -> frozenset[str]:
        if not self.google_address_validation_regions:
            return GOOGLE_COVERAGE
        return frozenset(code.strip().upper() for code in self.google_address_validation_regions.split(",") if code.strip())


class AddressValidationRequest(BaseModel):
    candidate_index: int = Field(ge=0, le=100)
    purpose: Literal["service", "billing", "other"]
    country_code: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")


class AddressFields(BaseModel):
    line1: str | None = Field(default=None, max_length=255)
    unit: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=20)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


class AddressReviewRequest(BaseModel):
    check_id: UUID
    expected_version: int
    decision: Literal["accept", "correct", "reject"]
    fields: AddressFields | None = None
    reason: str = Field(min_length=3, max_length=1000)
    contact_id: UUID | None = None


class AddressCheckResult(AddressCheckResponse):
    request_id: UUID


def _result(check: AddressCheck) -> AddressCheckResult:
    return AddressCheckResult(**address_check_response(check).model_dump(), request_id=uuid4())


def map_verdict(response: dict) -> tuple[str, dict | None]:
    """Map Google validateAddress signals to an app status plus the necessary suggestion fields."""
    result = response.get("result") or {}
    verdict = result.get("verdict") or {}
    address = result.get("address") or {}
    components = address.get("addressComponents") or []
    missing = set(address.get("missingComponentTypes") or [])
    unconfirmed = address.get("unconfirmedComponentTypes") or []
    suggestion = None
    if address.get("formattedAddress"):
        postal = address.get("postalAddress") or {}
        lines = postal.get("addressLines") or []
        by_type = {item.get("componentType"): (item.get("componentName") or {}).get("text") for item in components}
        suggestion = {
            "formatted_address": address["formattedAddress"],
            "line1": lines[0] if lines else None,
            "unit": by_type.get("subpremise"),
            "city": postal.get("locality"),
            "region": postal.get("administrativeArea"),
            "postal_code": postal.get("postalCode"),
            "country_code": postal.get("regionCode"),
            "components": [
                {
                    "type": item.get("componentType"),
                    "text": (item.get("componentName") or {}).get("text"),
                    "confirmation_level": item.get("confirmationLevel"),
                    "inferred": bool(item.get("inferred")),
                    "replaced": bool(item.get("replaced")),
                    "spell_corrected": bool(item.get("spellCorrected")),
                }
                for item in components
            ],
            "missing_component_types": sorted(missing),
            "unconfirmed_component_types": unconfirmed,
        }
    granularity = verdict.get("validationGranularity")
    if granularity in (None, "GRANULARITY_UNSPECIFIED", "OTHER") or missing - {"subpremise"}:
        return "insufficient_information", suggestion
    inferred = {item.get("componentType") for item in components if item.get("inferred")}
    material_inference = bool(verdict.get("hasInferredComponents")) and not (inferred and inferred <= BENIGN_INFERRED)
    validated = (
        verdict.get("addressComplete") is True
        and granularity in ("PREMISE", "SUB_PREMISE")
        and not missing
        and not unconfirmed
        and not address.get("unresolvedTokens")
        and not verdict.get("hasUnconfirmedComponents")
        and not verdict.get("hasReplacedComponents")
        and not verdict.get("hasSpellCorrectedComponents")
        and not material_inference
        and bool(components)
        and all(item.get("confirmationLevel") == "CONFIRMED" for item in components)
        and verdict.get("possibleNextAction") in (None, "ACCEPT")
    )
    return ("validated_components" if validated else "needs_confirmation"), suggestion


def google_payload(candidate: dict, country_code: str) -> dict:
    """Only address lines, locality, region, postal code and country leave the application."""
    address: dict = {
        "regionCode": country_code,
        "addressLines": [value for value in (candidate.get("line1"), candidate.get("unit")) if value],
    }
    for source, target in (("city", "locality"), ("region", "administrativeArea"), ("postal_code", "postalCode")):
        if candidate.get(source):
            address[target] = candidate[source]
    return {"address": address}


def _call_google(payload: dict, api_key: str) -> dict:
    request = urllib.request.Request(
        VALIDATE_URL,
        data=json.dumps(payload).encode("utf-8"),
        # Header rather than ?key= so the key never appears in URLs or proxy logs.
        headers={"Content-Type": "application/json", "X-Goog-Api-Key": api_key},
        method="POST",
    )
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=10, context=context) as response:  # fixed https URL
        return json.loads(response.read())


def _locked_call(session: Session, member: AuthenticatedMember, call_id: UUID) -> CallRecord:
    call = session.scalar(select(CallRecord).where(CallRecord.id == call_id).with_for_update())
    if call is None or call.organization_id != member.organization_id or call.deletion_status != "active":
        raise HTTPException(status_code=404, detail="Call not found")
    return call


@router.post("/calls/{call_id}/address-validation", response_model=AddressCheckResult)
def validate_address(
    call_id: UUID,
    body: AddressValidationRequest,
    member: Member,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> AddressCheckResult:
    if not idempotency_key or len(idempotency_key) > 120:
        raise HTTPException(status_code=422, detail="Idempotency-Key is required")
    config = AddressSettings()
    if not (config.google_address_validation_enabled and config.google_maps_api_key):
        raise HTTPException(status_code=409, detail={
            "code": "address_validation_not_configured", "message": "Address validation is not configured"})
    with Session(domain_engine()) as session, session.begin():
        call = _locked_call(session, member, call_id)
        existing = session.scalar(select(AddressCheck).where(
            AddressCheck.call_id == call.id,
            AddressCheck.result["idempotency_key"].astext == idempotency_key,
        ))
        if existing is not None:
            if existing.candidate.get("candidate_index") != body.candidate_index:
                raise HTTPException(status_code=409, detail="Idempotency key was used for a different candidate")
            return _result(existing)
        run = session.get(AnalysisRun, call.active_analysis_id) if call.active_analysis_id else None
        candidates = ((run.intake or {}).get("address_candidates") or []) if run else []
        if body.candidate_index >= len(candidates):
            raise HTTPException(status_code=404, detail="Address candidate not found")
        spoken = candidates[body.candidate_index]
        country = (body.country_code or spoken.get("country_code") or "").upper() or None
        local_status = (
            "insufficient_information" if not country or not spoken.get("line1")
            else "unsupported_region" if country not in config.regions()
            else None
        )
        check = AddressCheck(
            organization_id=call.organization_id,
            call_id=call.id,
            purpose=body.purpose,
            candidate={**spoken, "candidate_index": body.candidate_index,
                       "analysis_id": str(run.id), "transcript_id": str(run.transcript_id)},
            country_code=country,
            status=local_status or "not_checked",
            provider=None if local_status else "google",
            checked_at=datetime.now(UTC) if local_status else None,
            result={"idempotency_key": idempotency_key},
        )
        session.add(check)
        session.flush()
        if local_status:
            return _result(check)
        check_id = check.id
        payload = google_payload(spoken, country)

    failure: tuple[int, Exception] | None = None
    try:
        raw = _call_google(payload, config.google_maps_api_key.get_secret_value())
        status, suggestion = map_verdict(raw)
        verdict = (raw.get("result") or {}).get("verdict") or {}
        stored = {"suggestion": suggestion, "verdict": {key: verdict.get(key) for key in VERDICT_KEYS}}
    except urllib.error.HTTPError as exc:
        failure = (503 if exc.code == 429 or exc.code >= 500 else 502, exc)
    except (OSError, ValueError) as exc:  # connection errors, timeouts, malformed JSON
        failure = (503, exc)
    if failure:
        status, stored = "check_failed", {}

    with Session(domain_engine()) as session, session.begin():
        check = session.get(AddressCheck, check_id, with_for_update=True)
        live = session.get(CallRecord, check.call_id) if check else None
        if check is None or live is None or live.deletion_status != "active":
            raise HTTPException(status_code=404, detail="Call not found")
        check.status = status
        check.checked_at = datetime.now(UTC)
        check.result = {"idempotency_key": idempotency_key, **stored}
        response = _result(check)
    if failure:
        raise HTTPException(status_code=failure[0], detail={
            "code": "provider_unavailable", "message": "Address validation provider is unavailable",
            "retryable": True, "call_id": str(call_id)}) from failure[1]
    return response


@router.post("/calls/{call_id}/address-review", response_model=AddressCheckResult)
def review_address(call_id: UUID, body: AddressReviewRequest, member: Member) -> AddressCheckResult:
    if (body.decision == "correct") != (body.fields is not None):
        raise HTTPException(status_code=422, detail="Fields are required for a correction and only for a correction")
    if body.decision == "reject" and body.contact_id:
        raise HTTPException(status_code=422, detail="A rejected address cannot be saved to a contact")
    with Session(domain_engine()) as session, session.begin():
        call = _locked_call(session, member, call_id)
        check = session.scalar(select(AddressCheck).where(
            AddressCheck.id == body.check_id,
            AddressCheck.call_id == call.id,
            AddressCheck.organization_id == member.organization_id,
        ).with_for_update())
        if check is None:
            raise HTTPException(status_code=404, detail="Address check not found")
        if check.version != body.expected_version:
            raise HTTPException(status_code=409, detail="Address check changed; refresh first")
        if body.decision == "accept":
            source = (check.result or {}).get("suggestion") or check.candidate
            fields = {key: source.get(key) for key in FIELDS}
            fields["country_code"] = fields["country_code"] or check.country_code
        elif body.decision == "correct":
            fields = body.fields.model_dump()
        else:
            fields = None
        prior_decision = check.review_decision
        check.review_decision = body.decision
        check.reviewed_fields = fields
        check.reviewed_by = member.user_id
        check.reviewed_at = datetime.now(UTC)
        check.version += 1
        if body.contact_id:
            contact = session.scalar(select(Contact).where(
                Contact.id == body.contact_id, Contact.organization_id == member.organization_id))
            if contact is None:
                raise HTTPException(status_code=404, detail="Contact not found")
            check.contact_id = contact.id
            session.add(ContactAddress(
                organization_id=call.organization_id, contact_id=contact.id, purpose=check.purpose,
                **fields, provenance="address_review", source_call_id=call.id,
            ))
        session.add(ReviewEvent(
            organization_id=call.organization_id,
            call_id=call.id,
            actor_user_id=member.user_id,
            kind="address_review",
            prior_version=body.expected_version,
            new_version=check.version,
            change={"check_id": str(check.id), "prior_decision": prior_decision, "decision": body.decision,
                    "contact_id": str(body.contact_id) if body.contact_id else None},
            reason=body.reason,
        ))
        session.flush()
        return _result(check)
