"use client";

import { useState } from "react";

import { authClient } from "@/lib/auth-client";

export function SignOutButton() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  return (
    <>
      <button
        className="sign-out"
        type="button"
        disabled={pending}
        aria-label={pending ? "Signing out" : "Sign out"}
        title="Sign out"
        onClick={async () => {
          setPending(true);
          setError(false);
          try {
            const result = await authClient.signOut();
            if (result.error) { setError(true); return; }
            window.location.replace("/sign-in");
          } catch {
            setError(true);
          } finally {
            setPending(false);
          }
        }}
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" /></svg>
      </button>
      {error && <p className="sign-out-error" role="alert">Could not sign out. Try again.</p>}
    </>
  );
}
