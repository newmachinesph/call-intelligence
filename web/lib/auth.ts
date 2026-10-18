import { betterAuth } from "better-auth";
import { admin, jwt } from "better-auth/plugins";
import { Pool, type PoolConfig } from "pg";

function required(name: "AUTH_DATABASE_URL" | "BETTER_AUTH_URL" | "BETTER_AUTH_SECRET") {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required for private authentication`);
  return value;
}

const baseURL = required("BETTER_AUTH_URL");
const authOrigin = new URL(baseURL);
const alternateLoopbackHost = authOrigin.hostname === "127.0.0.1" ? "localhost"
  : authOrigin.hostname === "localhost" ? "127.0.0.1" : null;
const alternateLoopbackOrigin = alternateLoopbackHost
  ? `${authOrigin.protocol}//${alternateLoopbackHost}${authOrigin.port ? `:${authOrigin.port}` : ""}`
  : null;
const databaseUrl = required("AUTH_DATABASE_URL");
const databaseCa = process.env.AUTH_DATABASE_CA_B64
  ? Buffer.from(process.env.AUTH_DATABASE_CA_B64, "base64").toString("utf8")
  : undefined;
// Vercel can start multiple instances. Keep each instance's share of the
// database's small connection pool bounded.
const poolConfig: PoolConfig = { connectionString: databaseUrl, max: 1 };
if (databaseCa) {
  // pg treats SSL options in a connection string as replacements for `ssl`.
  // Keep the provider URL while supplying its CA for certificate verification.
  const parsed = new URL(databaseUrl);
  parsed.searchParams.delete("sslmode");
  parsed.searchParams.delete("sslrootcert");
  poolConfig.connectionString = parsed.toString();
  poolConfig.ssl = { ca: databaseCa, rejectUnauthorized: true };
}
const pool = new Pool(poolConfig);

export const auth = betterAuth({
  baseURL,
  trustedOrigins: alternateLoopbackOrigin ? [alternateLoopbackOrigin] : [],
  secret: required("BETTER_AUTH_SECRET"),
  database: pool,
  emailAndPassword: { enabled: true, disableSignUp: true },
  // Signed 5-minute session cache: avoids an auth-database round trip on every request.
  session: { cookieCache: { enabled: true, maxAge: 5 * 60 } },
  plugins: [
    admin(),
    jwt({
      jwks: { keyPairConfig: { alg: "ES256" } },
      jwt: { issuer: baseURL, audience: baseURL, expirationTime: "15m" },
    }),
  ],
});
