import { headers } from "next/headers";

import { auth } from "@/lib/auth";
import { domainFetch } from "@/lib/domain";

export const runtime = "nodejs";

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  try {
    const session = await auth.api.getSession({ headers: await headers() });
    if (!session) return new Response(null, { status: 401 });
    const authorized = await domainFetch(`/calls/${encodeURIComponent(id)}/audio-url`);
    if (!authorized.ok) {
      return new Response(null, { status: authorized.status === 404 ? 404 : authorized.status === 401 ? 401 : 503 });
    }

    const { url } = await authorized.json() as { url: string };
    const range = request.headers.get("range");
    const audio = await fetch(url, {
      cache: "no-store",
      headers: range ? { Range: range } : undefined,
    });
    if (audio.status !== 200 && audio.status !== 206) return new Response(null, { status: 502 });

    const responseHeaders = new Headers({
      "Content-Type": "audio/mpeg",
      "Cache-Control": "private, no-store",
      "Accept-Ranges": "bytes",
    });
    for (const name of ["Content-Length", "Content-Range"]) {
      const value = audio.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    return new Response(audio.body, { status: audio.status, headers: responseHeaders });
  } catch {
    return new Response(null, { status: 503 });
  }
}
