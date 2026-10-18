"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

import { api, ApiError, failure, words, type Detail, type Schemas } from "./call-utils";

type Check = Schemas["AddressCheckResponse"];
type Decision = Schemas["AddressReviewRequest"]["decision"];
const FIELDS = [["line1", "Street"], ["unit", "Unit"], ["city", "City"], ["region", "Region"], ["postal_code", "Postal code"], ["country_code", "Country"]] as const;
const STATUS_WORDS: Record<string, string> = {
  not_checked: "Not checked yet",
  validated_components: "Address components validated (does not confirm the customer's service address)",
  needs_confirmation: "Needs confirmation: the provider changed or inferred parts",
  insufficient_information: "Not enough address information to check",
  unsupported_region: "Country not supported for checking",
  check_failed: "Check failed: provider unavailable",
};
const text = (value: unknown) => typeof value === "string" && value.trim() ? value : null;

function CheckRow({ check, callId }: { check: Check; callId: string }) {
  const router = useRouter();
  const suggestion = check.suggestion ?? {};
  const original: Record<string, unknown> = { ...check.candidate, country_code: text(check.candidate.country_code) ?? check.country_code };
  const acceptable = !!check.suggestion || check.status === "validated_components";
  const [decision, setDecision] = useState<Decision>(acceptable ? "accept" : "correct");
  const [fields, setFields] = useState<Record<string, string>>(() => Object.fromEntries(FIELDS.map(([key]) => [key, text(suggestion[key]) ?? text(original[key]) ?? ""])));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function review(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setMessage("");
    try {
      await api(`/calls/${callId}/address-review`, "POST", {
        check_id: check.id, expected_version: check.version, decision, reason: reason.trim(),
        fields: decision === "correct" ? Object.fromEntries(Object.entries(fields).map(([key, value]) => [key, value.trim() || null])) : undefined,
      });
      setMessage("Address review saved. The transcript is unchanged.");
      router.refresh();
    } catch (error) { setMessage(failure(error)); }
    finally { setBusy(false); }
  }

  return (
    <article className="cd-address">
      <p className="cd-address-meta"><strong>{words(check.purpose)} address</strong> · {STATUS_WORDS[check.status] ?? words(check.status)}
        {check.checked_at && ` · checked ${new Date(check.checked_at).toLocaleString()}`}{check.provider && ` · ${check.provider}`}</p>
      {text(check.candidate.spoken_text) && <p className="cd-hint">Heard on the call: “{text(check.candidate.spoken_text)}”</p>}
      <div className="cd-table-scroll">
        <table className="cd-address-table">
          <thead><tr><th scope="col">Field</th><th scope="col">From the call</th><th scope="col">Provider suggestion</th></tr></thead>
          <tbody>{FIELDS.map(([key, label]) => {
            const before = text(original[key]); const after = check.suggestion ? text(suggestion[key]) : null;
            const changed = !!check.suggestion && before !== after;
            return <tr key={key} className={changed ? "cd-changed" : undefined}><th scope="row">{label}</th><td>{before ?? "Not stated"}</td>
              <td>{check.suggestion ? after ?? "None" : "No suggestion"}{changed && <span className="cd-badge cd-badge-warn">Changed</span>}</td></tr>;
          })}</tbody>
        </table>
      </div>
      {check.review_decision ? <p className="cd-hint">Reviewed: {words(check.review_decision)}. This records the intended address only; it does not prove identity or occupancy.</p> : (
        <form className="cd-form" onSubmit={review}>
          <fieldset className="cd-radios"><legend>Decision</legend>
            {(["accept", "correct", "reject"] as const).map((value) => <label key={value}><input type="radio" name={`decision-${check.id}`} checked={decision === value} onChange={() => setDecision(value)} disabled={value === "accept" && !acceptable} /> {value === "accept" ? "Accept suggestion" : value === "correct" ? "Correct fields" : "Reject"}</label>)}
          </fieldset>
          {decision === "correct" && <div className="cd-field-grid">{FIELDS.map(([key, label]) => <label key={key}>{label}<input value={fields[key]} onChange={(event) => setFields((current) => ({ ...current, [key]: event.target.value }))} maxLength={key === "country_code" ? 2 : 200} /></label>)}</div>}
          <label>Reason (required)<input value={reason} onChange={(event) => setReason(event.target.value)} required minLength={3} maxLength={1000} /></label>
          <button type="submit" className="primary-button" disabled={busy}>{busy ? "Saving…" : "Save address review"}</button>
          <p className="form-message" role="status">{message}</p>
        </form>
      )}
    </article>
  );
}

export function AddressCard({ detail }: { detail: Detail }) {
  const router = useRouter();
  const candidates = (detail.analysis?.address_candidates ?? []) as { purpose?: string; spoken_text?: string; line1?: string | null }[];
  const [index, setIndex] = useState("0");
  const [purpose, setPurpose] = useState<"service" | "billing" | "other">(candidates[0]?.purpose === "billing" ? "billing" : candidates[0]?.purpose === "other" ? "other" : "service");
  const [country, setCountry] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<React.ReactNode>("");

  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setMessage("");
    try {
      const { data } = await api<Check>(`/calls/${detail.call.id}/address-validation`, "POST",
        { candidate_index: Number(index), purpose, country_code: country.trim().toUpperCase() || null }, { "Idempotency-Key": crypto.randomUUID() });
      setMessage(`Check recorded: ${STATUS_WORDS[data.status] ?? words(data.status)}.`);
      router.refresh();
    } catch (error) {
      setMessage(error instanceof ApiError && error.code === "address_validation_not_configured"
        ? <>Google Address Validation isn&apos;t connected. Call analysis and chat still work.</>
        : error instanceof ApiError && error.status === 404 ? "No spoken address with that number was extracted from this call." : failure(error));
    } finally { setBusy(false); }
  }

  return (
    <section className="surface cd-card" aria-labelledby="cd-address-heading">
      <p className="status-label">Addresses</p>
      <h2 id="cd-address-heading">Address checks</h2>
      <p className="cd-hint">A valid address does not prove who called or that they live there. Changed street, number or unit values need an explicit review.</p>
      {detail.address_checks.length ? detail.address_checks.map((item) => <CheckRow key={`${item.id}-${item.version}`} check={item} callId={detail.call.id} />)
        : <p className="cd-empty">No address has been checked for this call.</p>}
      {candidates.length === 0 ? <p className="cd-empty">No spoken address was extracted from this call.</p> : <form className="cd-form cd-check-form" onSubmit={check}>
        <label>Spoken address<select value={index} onChange={(event) => { setIndex(event.target.value); const next = candidates[Number(event.target.value)]?.purpose; if (next === "service" || next === "billing" || next === "other") setPurpose(next); }}>
          {candidates.map((item, position) => <option key={position} value={position}>{words(item.purpose ?? "other")}: {text(item.spoken_text) ?? text(item.line1) ?? `Address ${position + 1}`}</option>)}</select></label>
        <label>Purpose<select value={purpose} onChange={(event) => setPurpose(event.target.value as typeof purpose)}><option value="service">Service</option><option value="billing">Billing</option><option value="other">Other</option></select></label>
        <label>Country (if known)<input value={country} onChange={(event) => setCountry(event.target.value)} maxLength={2} placeholder="e.g. US" /></label>
        <button type="submit" className="primary-button" disabled={busy || !detail.analysis}>{busy ? "Checking…" : "Check address"}</button>
      </form>}
      <p className="form-message" role="status">{message}</p>
    </section>
  );
}
