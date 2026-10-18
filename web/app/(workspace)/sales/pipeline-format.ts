import { readError } from "@/lib/api-error";

// Browser-side write through the domain proxy; throws the API's message (with status) so forms keep their drafts.
export async function send<T>(path: string, body: object, method: "POST" | "PATCH" = "POST", idempotencyKey?: string): Promise<T> {
  const response = await fetch(`/api/domain${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}) },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw Object.assign(new Error(await readError(response)), { status: response.status });
  return response.json() as Promise<T>;
}

export async function load<T>(path: string): Promise<T> {
  const response = await fetch(`/api/domain${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error(await readError(response));
  return response.json() as Promise<T>;
}

export const words = (value: string | null | undefined, empty = "Unknown") => value ? value.replaceAll("_", " ") : empty;
export const clock = (seconds: unknown) => {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
};
export const when = (iso: string | null | undefined, empty = "Date unknown") => iso ? new Date(iso).toLocaleString() : empty;
export const failure = (error: unknown) => error instanceof Error ? error.message : "Request failed.";
