# Local configuration

Examples contain no credentials. The ignored local values are saved in `apps/web/.env.local` and `apps/api/.env`; a synthetic test owner's login is in ignored `data/private/bootstrap-owner.txt`. The names form the proposed application configuration contract. Only variables read by an implemented adapter are active; reserved entries are labeled in the examples.

Web: provision a secure Better Auth secret, auth database URL and actual web/API origins. API: set domain database, trusted JWT issuer/audience/JWKS/algorithm, OpenAI key/model settings, R2, Workflow and Vector. Match JWT settings to the actual auth configuration. Configure the Upstash Workflow route's deployed base URL and signed ingress correctly. Use a separate service credential for processing requests where appropriate.

Sentry SDK DSNs identify projects; the build upload auth token remains secret. Source-map upload is opt-in with `SENTRY_UPLOAD_SOURCEMAPS=1` on the deployment build. API and web error envelopes are scrubbed in code. Google Address Validation and Redis rate limiting are not connected. The API enforces 25 MB and three-hour upload limits and 4,000-character chat questions. `TEST_SPEND_CAP_USD` is reserved, not an enforced budget; do not batch-process customer files under an assumed hard cap.

Never put secret values under NEXT_PUBLIC. Keep the API embedding dimension aligned with the actual vector index. The application must fail with clear configuration errors for missing required integration variables and show truthful unavailable states for optional providers.
