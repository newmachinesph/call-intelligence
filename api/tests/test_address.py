"""Synthetic Google Address Validation responses mapped to app statuses."""

from call_intelligence_api.address_routes import google_payload, map_verdict


def component(kind: str, text: str, level: str = "CONFIRMED", **flags: bool) -> dict:
    return {"componentType": kind, "componentName": {"text": text}, "confirmationLevel": level, **flags}


def response(verdict: dict, components: list[dict], **address: object) -> dict:
    return {"result": {"verdict": verdict, "address": {
        "formattedAddress": "1 Synthetic Way, Testville, TS 00000, USA",
        "postalAddress": {"regionCode": "US", "addressLines": ["1 Synthetic Way"], "locality": "Testville",
                          "administrativeArea": "TS", "postalCode": "00000"},
        "addressComponents": components, **address}}}


BASE = [component("street_number", "1"), component("route", "Synthetic Way"), component("locality", "Testville"),
        component("postal_code", "00000")]
CLEAN = {"validationGranularity": "PREMISE", "addressComplete": True, "possibleNextAction": "ACCEPT"}


def test_complete_address_with_benign_inference_is_validated() -> None:
    status, suggestion = map_verdict(response(
        CLEAN | {"hasInferredComponents": True},
        [*BASE, component("postal_code_suffix", "1234", inferred=True), component("country", "USA", inferred=True)],
    ))
    assert status == "validated_components"
    assert suggestion["line1"] == "1 Synthetic Way" and suggestion["country_code"] == "US"
    assert {item["type"] for item in suggestion["components"] if item["inferred"]} == {"postal_code_suffix", "country"}


def test_missing_unit_needs_confirmation() -> None:
    status, suggestion = map_verdict(response(
        {"validationGranularity": "PREMISE", "addressComplete": False, "possibleNextAction": "CONFIRM_ADD_SUBPREMISES"},
        BASE, missingComponentTypes=["subpremise"]))
    assert status == "needs_confirmation"
    assert suggestion["missing_component_types"] == ["subpremise"]


def test_replaced_street_number_needs_confirmation() -> None:
    status, suggestion = map_verdict(response(
        CLEAN | {"hasReplacedComponents": True, "possibleNextAction": "CONFIRM"},
        [component("street_number", "11", replaced=True), *BASE[1:]]))
    assert status == "needs_confirmation"
    assert next(item for item in suggestion["components"] if item["type"] == "street_number")["replaced"]


def test_material_inferred_component_needs_confirmation() -> None:
    status, _ = map_verdict(response(
        CLEAN | {"hasInferredComponents": True},
        [*BASE[:3], component("postal_code", "00000", inferred=True)]))
    assert status == "needs_confirmation"


def test_suspicious_component_needs_confirmation_even_when_verdict_looks_clean() -> None:
    status, _ = map_verdict(response(CLEAN, [component("street_number", "1", "UNCONFIRMED_AND_SUSPICIOUS"), *BASE[1:]]))
    assert status == "needs_confirmation"


def test_route_granularity_is_not_validated() -> None:
    assert map_verdict(response(CLEAN | {"validationGranularity": "ROUTE"}, BASE))[0] == "needs_confirmation"


def test_other_granularity_missing_street_number_or_empty_response_is_insufficient() -> None:
    other = response({"validationGranularity": "OTHER", "addressComplete": False}, BASE)
    assert map_verdict(other)[0] == "insufficient_information"
    no_number = response({"validationGranularity": "ROUTE"}, BASE[1:], missingComponentTypes=["street_number"])
    assert map_verdict(no_number)[0] == "insufficient_information"
    assert map_verdict({}) == ("insufficient_information", None)


def test_payload_sends_only_address_fields() -> None:
    candidate = {"purpose": "service", "spoken_text": "synthetic spoken words", "line1": "1 Synthetic Way",
                 "unit": "Apt 2", "city": "Testville", "region": None, "postal_code": "00000",
                 "country_code": None, "evidence_segment_ids": ["x"], "analysis_id": "y"}
    assert google_payload(candidate, "US") == {"address": {
        "regionCode": "US", "addressLines": ["1 Synthetic Way", "Apt 2"], "locality": "Testville",
        "postalCode": "00000"}}
