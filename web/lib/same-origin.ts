// CSRF defense in depth for cookie-authenticated proxies: state-changing requests must come from this app's origin.
// A missing Origin is a non-browser client (browsers always send it on cross-site POST), which cookies alone can't forge.
function allowedOrigins(): Set<string> {
  const base = new URL(process.env.BETTER_AUTH_URL ?? "http://127.0.0.1:3000");
  const origins = new Set([base.origin]);
  for (const [from, to] of [["127.0.0.1", "localhost"], ["localhost", "127.0.0.1"]]) {
    if (base.hostname === from) origins.add(`${base.protocol}//${to}${base.port ? `:${base.port}` : ""}`);
  }
  return origins;
}

export function isCrossOriginWrite(request: Request): boolean {
  if (request.method === "GET" || request.method === "HEAD") return false;
  const origin = request.headers.get("origin");
  return origin !== null && !allowedOrigins().has(origin);
}
