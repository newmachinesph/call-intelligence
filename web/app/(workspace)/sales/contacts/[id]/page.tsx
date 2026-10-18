import Link from "next/link";
import { notFound } from "next/navigation";

import type { components } from "@/contracts/api";
import { AddressForm } from "@/app/(workspace)/sales/contacts/[id]/address-form";
import { when, words } from "@/app/(workspace)/sales/pipeline-format";
import { domainFetch } from "@/lib/domain";
import "../../../workflows.css";

type Contact = components["schemas"]["ContactDetailResponse"];
type Address = components["schemas"]["AddressResponse"];

const PURPOSES = ["service", "billing", "other"] as const;
const addressLines = (item: Address) => [[item.line1, item.unit].filter(Boolean).join(", "), [item.city, item.region, item.postal_code].filter(Boolean).join(" "), item.country_code]
  .filter(Boolean).join(" · ") || "No address fields recorded";

async function contact(id: string): Promise<Contact | null> {
  if (!/^[0-9a-f-]{36}$/i.test(id)) return null;
  const response = await domainFetch(`/crm/contacts/${encodeURIComponent(id)}`);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Call service returned ${response.status}`);
  return response.json();
}

export default async function ContactPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const data = await contact(id);
  if (!data) notFound();
  // ponytail: first 10 candidates by name; the rest still show as links.
  const candidates = await Promise.all(data.identity_candidate_ids.slice(0, 10).map((candidate) => contact(candidate).catch(() => null)));
  const nameOf = (candidate: string) => candidates.find((item) => item?.id === candidate)?.display_name ?? "Unnamed contact";

  return <div className="content-column">
    <Link className="back-link" href="/sales?view=pipeline">← Pipeline</Link>
    <header className="page-heading"><p className="eyebrow">Contact · local CRM</p><h1>{data.display_name ?? "Unnamed contact"}</h1>
      <p className="wf-meta">External CRM: not connected. This record lives in this workspace only.</p></header>
    <div className="wf-contact-grid">
      <section className="surface wf-card" aria-labelledby="identity-heading"><h2 id="identity-heading">Identifiers</h2>
        {data.identifiers.length ? <ul className="wf-list">{data.identifiers.map((item) => <li key={`${item.kind}-${item.value}`}>
          <strong>{item.kind === "phone" ? "Phone" : "Email"}:</strong> {item.value} · {item.verified ? "Verified" : "Not verified"} · source: {words(item.provenance)}
        </li>)}</ul> : <p className="wf-meta">No phone or email recorded.</p>}
        <h3>Possible duplicate identities</h3>
        {data.identity_candidate_ids.length ? <><p className="wf-warn">These contacts share a phone or email. Records are never merged automatically; review before linking calls.</p>
          <ul className="wf-list">{data.identity_candidate_ids.map((candidate) => <li key={candidate}><Link href={`/sales/contacts/${candidate}`}>{nameOf(candidate)}</Link></li>)}</ul></>
          : <p className="wf-meta">No other contact shares these identifiers.</p>}
      </section>
      <section className="surface wf-card" aria-labelledby="address-heading"><h2 id="address-heading">Addresses</h2>
        {PURPOSES.map((purpose) => {
          const items = data.addresses.filter((item) => item.purpose === purpose);
          return <div key={purpose}><h3>{purpose === "service" ? "Service" : purpose === "billing" ? "Billing" : "Other"}</h3>
            {items.length ? <ul className="wf-list">{items.map((item) => <li key={item.id}>{addressLines(item)}
              <small>Source: {words(item.provenance)}{item.source_call_id && <> · <Link href={`/sales/calls/${item.source_call_id}`}>source call</Link></>}</small></li>)}</ul>
              : <p className="wf-meta">None recorded.</p>}</div>;
        })}
        <p className="wf-meta">A recorded or validated address does not prove identity, occupancy or ownership.</p>
        <AddressForm contactId={data.id} />
      </section>
    </div>
    <section className="surface wf-card" aria-labelledby="opportunities-heading"><h2 id="opportunities-heading">Opportunities</h2>
      {data.opportunities.length ? <ul className="wf-rows">{data.opportunities.map((item) => <li className="wf-row" key={item.id}><div className="wf-row-main">
        <strong>{item.service_interest ?? "Service not specified"}</strong>
        <p className="wf-row-meta"><span className="status-pill">{item.stage}</span><span>{item.manual_lock ? "Automation locked" : "Automation on"}</span><Link href="/sales?view=pipeline">Open Pipeline</Link></p>
      </div></li>)}</ul> : <p className="wf-empty">No opportunities for this contact.</p>}
      <p className="wf-meta">Stages are local reported outcomes; Won does not prove payment or a booked appointment.</p>
    </section>
    <section className="surface wf-card" aria-labelledby="retention-heading"><h2 id="retention-heading">Retention cases</h2>
      {data.retention_cases.length ? <ul className="wf-list">{data.retention_cases.map((item) => <li key={item.id}>
        Intent: {words(item.intent)} · Outcome: {words(item.outcome)} · <Link href={`/sales/calls/${item.call_id}`}>{item.call_label ?? "Source call"}</Link>
      </li>)}</ul> : <p className="wf-empty">No retention cases.</p>}
    </section>
    <section className="surface wf-card" aria-labelledby="calls-heading"><h2 id="calls-heading">Linked calls</h2>
      {data.linked_calls.length ? <div className="table-scroll"><table className="wf-table">
        <thead><tr><th scope="col">Call</th><th scope="col">Call date</th><th scope="col">Type</th><th scope="col">Outcome</th></tr></thead>
        <tbody>{data.linked_calls.map((call) => <tr key={call.call_id}><td><Link href={`/sales/calls/${call.call_id}`}>{call.call_label}</Link></td>
          <td>{when(call.call_at, "Unknown")}</td><td>{words(call.primary_type, "Pending")}</td><td>{words(call.outcome)}</td></tr>)}</tbody>
      </table></div> : <p className="wf-empty">No calls linked. Link a reviewed call from an opportunity in Pipeline.</p>}
    </section>
    <section className="surface wf-card" aria-labelledby="timeline-heading"><h2 id="timeline-heading">Timeline</h2>
      {data.timeline.length ? <ol className="wf-timeline">{data.timeline.map((item, index) => <li key={index}>
        <strong>{words(item.kind)}</strong> · <small>{when(item.at)}</small><p>{item.summary}</p>
        {item.call_id && <Link href={`/sales/calls/${item.call_id}`}>Open call</Link>}
      </li>)}</ol> : <p className="wf-empty">No activity yet.</p>}
    </section>
  </div>;
}
