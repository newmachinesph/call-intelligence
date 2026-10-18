import { toNextJsHandler } from "better-auth/next-js";

export const runtime = "nodejs";

async function handle(request: Request, method: "GET" | "POST") {
  if (!process.env.AUTH_DATABASE_URL || !process.env.BETTER_AUTH_URL) {
    return Response.json(
      { error: { code: "auth_unconfigured", message: "Private access is not configured." } },
      { status: 503 },
    );
  }
  const { auth } = await import("@/lib/auth");
  const handlers = toNextJsHandler(auth);
  return handlers[method](request);
}

export async function GET(request: Request) { return handle(request, "GET"); }
export async function POST(request: Request) { return handle(request, "POST"); }
