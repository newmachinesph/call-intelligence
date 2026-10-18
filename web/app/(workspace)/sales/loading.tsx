// Also covers /sales/calls/* while they load, so it stays generic.
export default function Loading() {
  return (
    <div className="content-column" aria-busy="true">
      <p className="sr-only" role="status">Loading call data…</p>
      <div className="skeleton skeleton-heading" />
      <div className="metric-grid">{[0, 1, 2, 3].map((key) => <div key={key} className="surface metric-card skeleton" />)}</div>
      <div className="surface panel-card skeleton skeleton-tall" />
    </div>
  );
}
