"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { failure, send } from "@/app/(workspace)/sales/pipeline-format";

const EMPTY = { purpose: "service", line1: "", unit: "", city: "", region: "", postal_code: "", country_code: "", reason: "" };
const FIELDS = [["line1", "Street address", 255], ["unit", "Unit", 80], ["city", "City", 120], ["region", "State / region", 120], ["postal_code", "Postal code", 20]] as const;

export function AddressForm({ contactId }: { contactId: string }) {
  const router = useRouter();
  const [form, setForm] = useState(EMPTY);
  const [state, setState] = useState({ busy: false, message: "" });
  const set = (key: keyof typeof EMPTY) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [key]: event.target.value });

  async function submit(event: React.FormEvent) {
    event.preventDefault(); setState({ busy: true, message: "" });
    const body = Object.fromEntries(Object.entries(form).map(([key, value]) => [key, value.trim() || null]));
    try {
      await send(`/crm/contacts/${contactId}/addresses`, { ...body, country_code: form.country_code.trim().toUpperCase() || null });
      setForm(EMPTY); setState({ busy: false, message: "Address added." }); router.refresh();
    } catch (error) { setState({ busy: false, message: failure(error) }); }
  }

  return <details className="wf-details">
    <summary>Add an address</summary>
    <form className="wf-form" onSubmit={submit}>
      <label>Purpose<select value={form.purpose} onChange={set("purpose")}><option value="service">Service</option><option value="billing">Billing</option><option value="other">Other</option></select></label>
      {FIELDS.map(([key, label, max]) => <label key={key}>{label}<input value={form[key]} onChange={set(key)} maxLength={max} /></label>)}
      <label>Country code<input value={form.country_code} onChange={set("country_code")} maxLength={2} pattern="[A-Za-z]{2}" placeholder="US" title="Two-letter country code" /></label>
      <label>Reason<input value={form.reason} onChange={set("reason")} required minLength={3} maxLength={1000} /></label>
      <button className="primary-button" disabled={state.busy || ![...FIELDS.map(([key]) => form[key]), form.country_code].some((value) => value.trim())}>Add address</button>
      {state.message && <p className="wf-meta" role="status">{state.message}</p>}
    </form>
  </details>;
}
