"use client";

import { useEffect, useRef } from "react";

// ponytail: native <dialog>.showModal() gives the focus trap (inert background) and Escape-to-close; we only restore focus.
export function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const opener = document.activeElement as HTMLElement | null;
    if (dialog && !dialog.open) dialog.showModal();
    return () => opener?.focus(); // unmount removes the dialog from the top layer
  }, []);
  return (
    <dialog ref={ref} className="cd-drawer" aria-labelledby="cd-drawer-title" onClose={onClose}
      onClick={(event) => { if (event.target === ref.current) onClose(); }}>
      <div className="cd-drawer-body">
        <header className="cd-drawer-head">
          <h2 id="cd-drawer-title">{title}</h2>
          <button type="button" className="text-button" onClick={onClose}>Close</button>
        </header>
        {children}
      </div>
    </dialog>
  );
}
