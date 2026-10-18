"use client";

import { useEffect, useRef, useState } from "react";

import { failure, load } from "@/app/(workspace)/sales/pipeline-format";

// Native modal <dialog>: the browser traps focus, closes on Escape and restores focus on close.
export function Dialog({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void; children: React.ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (open && dialog && !dialog.open) dialog.showModal();
    if (!open && dialog?.open) dialog.close();
  }, [open]);
  return <dialog ref={ref} className="wf-dialog" aria-label={title} onClose={onClose}>
    <div className="wf-dialog-head"><h2>{title}</h2><button type="button" className="text-button" onClick={onClose}>Close</button></div>
    {open && children}
  </dialog>;
}

// Client read of a domain path; `reload` refetches after a write. A null path loads nothing.
export function useLoad<T>(path: string | null) {
  const [state, setState] = useState<{ path: string | null; data: T | null; error: string }>({ path: null, data: null, error: "" });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let live = true;
    load<T>(path).then((data) => { if (live) setState({ path, data, error: "" }); })
      .catch((error) => { if (live) setState({ path, data: null, error: failure(error) }); });
    return () => { live = false; };
  }, [path, tick]);
  const current = state.path === path;
  return { data: current ? state.data : null, error: current ? state.error : "", loading: Boolean(path) && !current, reload: () => setTick((value) => value + 1) };
}
