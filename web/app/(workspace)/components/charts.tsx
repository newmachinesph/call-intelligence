import Link from "next/link";

type BarRow = { label: string; count: number; denominator?: number | null; href?: string };

const share = (count: number, of: number) => (of > 0 ? Math.round((count / of) * 100) : 0);

// Single-series horizontal bars (one hue, so no legend); every value is also text and in the table.
export function BarList({ title, caption, rows, denominator, unit = "calls", empty }: {
  title: string; caption: string; rows: BarRow[]; denominator?: number; unit?: string; empty: string;
}) {
  const max = Math.max(1, ...rows.map((row) => row.count));
  const of = (row: BarRow) => row.denominator ?? denominator ?? null;
  return (
    <figure className="chart">
      <figcaption><strong>{title}</strong><span>{caption}</span></figcaption>
      {rows.length ? <>
        <ul className="bar-list">
          {rows.map((row) => {
            const total = of(row);
            const width = total ? share(row.count, total) : share(row.count, max);
            const value = total ? `${row.count} of ${total} ${unit} (${share(row.count, total)}%)` : `${row.count} ${unit}`;
            return <li key={row.label}>
              <div className="bar-label">{row.href ? <Link href={row.href}>{row.label}</Link> : <span>{row.label}</span>}<span>{value}</span></div>
              <div className="bar-track" title={`${row.label}: ${value}`} aria-hidden="true"><span className="bar-fill" style={{ width: `${width}%` }} /></div>
            </li>;
          })}
        </ul>
        <details className="chart-table"><summary>Data table</summary>
          <table><thead><tr><th scope="col">Label</th><th scope="col">Count</th><th scope="col">Denominator</th></tr></thead>
            <tbody>{rows.map((row) => <tr key={row.label}><th scope="row">{row.label}</th><td>{row.count}</td><td>{of(row) ?? "—"}</td></tr>)}</tbody></table>
        </details>
      </> : <p className="chart-empty">{empty}</p>}
    </figure>
  );
}

// Daily count columns. ponytail: plots the latest 30 days with data; the table lists every day.
export function ActivityColumns({ title, caption, points, empty }: {
  title: string; caption: string; points: { date: string; count: number }[]; empty: string;
}) {
  const shown = [...points].sort((a, b) => a.date.localeCompare(b.date)).slice(-30);
  const max = Math.max(1, ...shown.map((point) => point.count));
  return (
    <figure className="chart">
      <figcaption><strong>{title}</strong><span>{caption}</span></figcaption>
      {shown.length ? <>
        <div className="column-chart" aria-hidden="true">
          {shown.map((point) => <span key={point.date} title={`${point.date}: ${point.count}`} className="column-slot"><span className="column-fill" style={{ height: `${Math.max(4, (point.count / max) * 100)}%` }} /></span>)}
        </div>
        <div className="column-axis" aria-hidden="true"><span>{shown[0].date}</span><span>Peak {max}</span><span>{shown.at(-1)!.date}</span></div>
        <p className="sr-only">{shown.length} days plotted, peak {max} per day. The data table lists every day.</p>
        <details className="chart-table"><summary>Data table</summary>
          <table><thead><tr><th scope="col">Date</th><th scope="col">Count</th></tr></thead>
            <tbody>{points.map((point) => <tr key={point.date}><th scope="row">{point.date}</th><td>{point.count}</td></tr>)}</tbody></table>
        </details>
      </> : <p className="chart-empty">{empty}</p>}
    </figure>
  );
}
