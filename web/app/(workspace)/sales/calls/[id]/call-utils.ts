import type { components } from "@/contracts/api";
import { readError } from "@/lib/api-error";

export type Schemas = components["schemas"];
export type Detail = Schemas["CallDetailResponse"];
export type Transcript = Schemas["TranscriptResponse"];
export type Segment = Schemas["SegmentResponse"];
export type Seek = (seconds: number, segmentId?: string) => void;

export const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
export const words = (value: string | null | undefined, fallback = "Unknown") => value ? value.replaceAll("_", " ") : fallback;

// Long calls are transcribed in pieces ("chunk2:A"); speakers are only comparable within one piece.
export function speakerName(label: string | null | undefined): string {
  const piece = label?.match(/^chunk(\d+):(.+)$/);
  return piece ? `Speaker ${piece[2]} · part ${Number(piece[1]) + 1}` : `Speaker ${label ?? "?"}`;
}

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

// Client-side call through the authenticated proxy; non-2xx throws ApiError with the envelope message and code.
export async function api<T>(path: string, method = "GET", body?: unknown, headers?: Record<string, string>): Promise<{ status: number; data: T }> {
  const response = await fetch(`/api/domain${path}`, {
    method, cache: "no-store",
    headers: body === undefined ? headers : { "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const code = await response.clone().json().then((payload) => payload?.error?.code, () => undefined);
    throw new ApiError(await readError(response), response.status, typeof code === "string" ? code : undefined);
  }
  return { status: response.status, data: await response.json() as T };
}

export const failure = (error: unknown, conflict = "This call changed since the page loaded. Refresh to load the latest version, then try again.") =>
  error instanceof ApiError && error.status === 409 ? `${conflict} (${error.message})` : error instanceof Error ? error.message : "Request failed. Try again.";

export type Evidence = { seconds: number; segmentId?: string; quote?: string };

// Only evidence from the active transcript version is playable; older references are labelled instead.
export function evidenceOf(ref: Record<string, unknown>, activeVersion: number | null): Evidence | "stale" | null {
  if (ref.source_kind !== "transcript" || typeof ref.start_seconds !== "number") return null;
  if (ref.transcript_version !== activeVersion) return "stale";
  const ids = Array.isArray(ref.segment_ids) ? ref.segment_ids : [];
  return { seconds: ref.start_seconds, segmentId: typeof ids[0] === "string" ? ids[0] : undefined, quote: typeof ref.quote === "string" ? ref.quote : undefined };
}
