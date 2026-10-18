import "server-only";

import { headers } from "next/headers";

import { apiToken } from "@/lib/api-token";

const baseURL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export async function domainFetch(path: string, init?: RequestInit): Promise<Response> {
  const token = await apiToken(await headers());
  if (!token) return Response.json({ detail: "Sign-in is required" }, { status: 401 });
  return fetch(`${baseURL}/api/v1${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      ...Object.fromEntries(new Headers(init?.headers).entries()),
      Authorization: `Bearer ${token}`,
    },
  });
}

export async function domainGet<T>(path: string): Promise<T> {
  const response = await domainFetch(path);
  if (!response.ok) throw new Error(`Call service returned ${response.status}`);
  return response.json() as Promise<T>;
}
