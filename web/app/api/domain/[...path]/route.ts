import { headers } from "next/headers";
import { NextRequest } from "next/server";

import { apiToken } from "@/lib/api-token";
import { isCrossOriginWrite } from "@/lib/same-origin";

const baseURL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  if (isCrossOriginWrite(request)) return Response.json({ error: "Cross-origin request refused" }, { status: 403 });
  const token = await apiToken(await headers());
  if (!token) return Response.json({ error: "Sign-in required" }, { status: 401 });
  const { path } = await context.params;
  if (!path.length || path.some((piece) => piece === ".." || piece.includes("/"))) {
    return Response.json({ error: "Invalid path" }, { status: 400 });
  }
  const url = new URL(`${baseURL}/api/v1/${path.map(encodeURIComponent).join("/")}`);
  url.search = request.nextUrl.search;
  const outgoing = new Headers({ Authorization: `Bearer ${token}` });
  const contentType = request.headers.get("content-type");
  if (contentType) outgoing.set("Content-Type", contentType);
  const idempotency = request.headers.get("idempotency-key");
  if (idempotency) outgoing.set("Idempotency-Key", idempotency);
  const upstream = await fetch(url, {
    method: request.method,
    headers: outgoing,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
    cache: "no-store",
  });
  return new Response(upstream.body, {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json", "Cache-Control": "no-store" },
  });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
