import "server-only";

import { createHash } from "node:crypto";

import { auth } from "@/lib/auth";

// ponytail: per-process reuse of the 15-minute API JWT, keyed by a hash of the session cookie. Minting one per
// request cost several remote auth-database round trips. Reuse stays below the 5-minute session cookie cache,
// so a revoked session loses API access about as fast as it loses the session itself.
const REUSE_MS = 4 * 60 * 1000;
const tokens = new Map<string, { token: string; expires: number }>();

function sessionKey(requestHeaders: Headers): string | null {
  const match = (requestHeaders.get("cookie") ?? "").match(/(?:^|;\s*)(?:__Secure-)?better-auth\.session_token=([^;]+)/);
  return match ? createHash("sha256").update(match[1]).digest("hex") : null;
}

export async function apiToken(requestHeaders: Headers): Promise<string | null> {
  const key = sessionKey(requestHeaders);
  const cached = key ? tokens.get(key) : undefined;
  if (cached && cached.expires > Date.now()) return cached.token;
  const result = await auth.api.getToken({ headers: requestHeaders }).catch(() => null);
  if (!result?.token) {
    if (key) tokens.delete(key);
    return null;
  }
  if (key) {
    if (tokens.size >= 1000) tokens.delete(tokens.keys().next().value as string);
    tokens.set(key, { token: result.token, expires: Date.now() + REUSE_MS });
  }
  return result.token;
}
