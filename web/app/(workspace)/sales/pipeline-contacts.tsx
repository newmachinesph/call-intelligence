"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import type { components } from "@/contracts/api";
import { failure, send } from "@/app/(workspace)/sales/pipeline-format";
import { Dialog, useLoad } from "@/app/(workspace)/sales/pipeline-shared";

type Contact = components["schemas"]["ContactResponse"];
type Opportunity = components["schemas"]["OpportunityResponse"];

const identifiers = (contact: Contact) => contact.identifiers.map((item) => `${item.value}${item.verified ? " (verified)" : ""}`).join(", ") || "No phone or email";

export function ContactsTable() {
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState({ q: "", cursors: [] as string[] });
  const cursor = query.cursors.at(-1);
  const { data, error } = useLoad<components["schemas"]["ContactListResponse"]>(
    `/crm/contacts?limit=30${query.q ? `&q=${encodeURIComponent(query.q)}` : ""}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`);
  return <section className="surface wf-card" aria-labelledby="contacts-heading">
    <div className="wf-card-head"><h2 id="contacts-heading">Contacts</h2>
      <form role="search" className="wf-search" onSubmit={(event) => { event.preventDefault(); setQuery({ q: draft.trim(), cursors: [] }); }}>
        <label className="sr-only" htmlFor="contact-search">Search contacts by name, phone or email</label>
        <input id="contact-search" type="search" value={draft} maxLength={100} placeholder="Name, phone or email" onChange={(event) => setDraft(event.target.value)} />
        <button type="submit" className="wf-secondary">Search</button>
      </form></div>
    {error && <p className="wf-error" role="alert">{error}</p>}
    {!data ? !error && <p className="wf-meta">Loading contacts…</p> : data.items.length ? <div className="table-scroll"><table className="wf-table">
      <thead><tr><th scope="col">Name</th><th scope="col">Phone / email</th><th scope="col">Identity</th></tr></thead>
      <tbody>{data.items.map((contact) => <tr key={contact.id}>
        <td><Link href={`/sales/contacts/${contact.id}`}>{contact.display_name ?? "Unnamed contact"}</Link></td>
        <td>{identifiers(contact)}</td>
        <td>{contact.identity_candidate_ids.length ? <span className="wf-warn-text">{contact.identity_candidate_ids.length} possible duplicate(s)</span> : "No duplicate found"}</td>
      </tr>)}</tbody>
    </table></div> : <p className="wf-empty">{query.q ? `No contacts match “${query.q}”.` : "No contacts yet."}</p>}
    <nav className="wf-pager" aria-label="Contact pages">
      {query.cursors.length > 0 && <button type="button" className="text-button" onClick={() => setQuery({ ...query, cursors: query.cursors.slice(0, -1) })}>Previous</button>}
      {data?.next_cursor && <button type="button" className="text-button" onClick={() => setQuery({ ...query, cursors: [...query.cursors, data.next_cursor!] })}>Next</button>}
    </nav>
  </section>;
}

export function CreateRecords({ contacts, onOpportunity }: { contacts: Contact[]; onOpportunity: (id: string) => void }) {
  const router = useRouter();
  const [open, setOpen] = useState<"contact" | "opportunity" | null>(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"phone" | "email">("phone");
  const [value, setValue] = useState("");
  const [created, setCreated] = useState<Contact | null>(null);
  const [contactId, setContactId] = useState("");
  const [service, setService] = useState("");
  const [state, setState] = useState({ busy: false, message: "" });
  const choices = created && !contacts.some((item) => item.id === created.id) ? [created, ...contacts] : contacts;

  function show(next: "contact" | "opportunity") { setOpen(next); setState({ busy: false, message: "" }); if (next === "contact") { setCreated(null); setName(""); setValue(""); } }
  async function createContact(event: React.FormEvent) {
    event.preventDefault(); setState({ busy: true, message: "" });
    try {
      const contact = await send<Contact>("/crm/contacts", { display_name: name.trim() || null, identifiers: value.trim() ? [{ kind, value: value.trim() }] : [] });
      setCreated(contact); setContactId(contact.id); setName(""); setValue(""); setState({ busy: false, message: "" }); router.refresh();
    } catch (error) { setState({ busy: false, message: failure(error) }); }
  }
  async function createOpportunity(event: React.FormEvent) {
    event.preventDefault(); setState({ busy: true, message: "" });
    try {
      const opportunity = await send<Opportunity>("/crm/opportunities", { contact_id: contactId, service_interest: service.trim() || null });
      setService(""); setOpen(null); router.refresh(); onOpportunity(opportunity.id);
    } catch (error) { setState({ busy: false, message: failure(error) }); }
  }

  return <div className="row-actions">
    <button type="button" className="wf-secondary" onClick={() => show("contact")}>New contact</button>
    <button type="button" className="primary-button" onClick={() => show("opportunity")}>New opportunity</button>
    <Dialog open={open === "contact"} title="New contact" onClose={() => setOpen(null)}>
      {created ? <div className="wf-form">
        <p role="status">Created {created.display_name ?? "contact"}.</p>
        {created.identity_candidate_ids.length > 0 && <div className="wf-warn" role="alert">
          <strong>Possible duplicate identity.</strong> The same phone or email exists on {created.identity_candidate_ids.length} other contact(s). Review before linking calls; records are never merged automatically.
          <ul>{created.identity_candidate_ids.map((id) => <li key={id}><Link href={`/sales/contacts/${id}`}>Open possible duplicate</Link></li>)}</ul>
        </div>}
        <div className="row-actions"><button type="button" className="primary-button" onClick={() => show("opportunity")}>Create opportunity for this contact</button>
          <Link href={`/sales/contacts/${created.id}`}>Open contact</Link></div>
      </div> : <form className="wf-form" onSubmit={createContact}>
        <label>Name<input value={name} onChange={(event) => setName(event.target.value)} maxLength={200} autoFocus /></label>
        <label>Identifier type<select value={kind} onChange={(event) => setKind(event.target.value as "phone" | "email")}><option value="phone">Phone</option><option value="email">Email</option></select></label>
        <label>{kind === "phone" ? "Phone" : "Email"}<input value={value} type={kind === "email" ? "email" : "tel"} onChange={(event) => setValue(event.target.value)} maxLength={255} /></label>
        <p className="wf-meta">A name or a phone/email is required. Matching phone or email on another contact is flagged, not merged.</p>
        {state.message && <p className="wf-error" role="alert">{state.message}</p>}
        <button className="primary-button" disabled={state.busy || (!name.trim() && !value.trim())}>Create contact</button>
      </form>}
    </Dialog>
    <Dialog open={open === "opportunity"} title="New opportunity" onClose={() => setOpen(null)}>
      <form className="wf-form" onSubmit={createOpportunity}>
        <label>Contact<select value={contactId} onChange={(event) => setContactId(event.target.value)} required autoFocus>
          <option value="">Select a contact</option>{choices.map((item) => <option key={item.id} value={item.id}>{item.display_name ?? identifiers(item)}</option>)}</select></label>
        {!choices.length && <p className="wf-meta">Create a contact first.</p>}
        <label>Service interest<input value={service} onChange={(event) => setService(event.target.value)} maxLength={255} placeholder="e.g. annual service plan" /></label>
        <p className="wf-meta">Starts at New. Stages are local reported outcomes.</p>
        {state.message && <p className="wf-error" role="alert">{state.message}</p>}
        <button className="primary-button" disabled={state.busy || !contactId}>Create opportunity</button>
      </form>
    </Dialog>
  </div>;
}
