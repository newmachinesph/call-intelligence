// Reads the API error envelope {error:{message}}; falls back to legacy {detail} and the proxy's {error:"..."}.
export async function readError(response: Response, fallback = `Request failed (${response.status})`): Promise<string> {
  const payload = await response.json().catch(() => null);
  const error = payload?.error;
  if (typeof error?.message === "string") return error.message;
  if (typeof error === "string") return error;
  if (typeof payload?.detail === "string") return payload.detail;
  return fallback;
}
