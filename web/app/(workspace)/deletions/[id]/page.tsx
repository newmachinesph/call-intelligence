import Link from "next/link";

import { DeletionProgress } from "./deletion-progress";
import "./deletions.css";

export default async function DeletionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <div className="content-column">
      <Link className="back-link" href="/sales?view=calls">← Call Log</Link>
      <header className="page-heading"><p className="eyebrow">Owner action</p><h1>Call deletion</h1></header>
      <DeletionProgress jobId={id} />
    </div>
  );
}
