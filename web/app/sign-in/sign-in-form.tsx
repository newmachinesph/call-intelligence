"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { authClient } from "@/lib/auth-client";

export function SignInForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      const result = await authClient.signIn.email({ email, password });
      if (result.error) {
        setError("Unable to sign in with those credentials.");
        return;
      }
      router.replace("/");
      router.refresh();
    } catch {
      setError("Sign in is temporarily unavailable. Please try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="auth-page">
      <div className="auth-panel">
        <div className="brand auth-brand">
          <span className="brand-mark" aria-hidden="true">P</span>
          <span><strong>Call Intelligence</strong><small>Sales call review</small></span>
        </div>
        <p className="eyebrow">Private workspace</p>
        <h1>Welcome back</h1>
        <p className="auth-intro">Sign in with your invited account.</p>
        <form onSubmit={submit}>
          <label htmlFor="email">Email</label>
          <input id="email" type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} />
          <label htmlFor="password">Password</label>
          <input id="password" type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
          {error && <p className="auth-error" role="alert">{error}</p>}
          <button className="auth-submit" type="submit" disabled={pending}>{pending ? "Signing in…" : "Sign in"}</button>
        </form>
        <p className="auth-note">Access is limited to invited reviewers.</p>
      </div>
    </main>
  );
}
