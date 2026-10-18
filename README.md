# Call Intelligence

Upload sales and customer-service call recordings and get back a diarized transcript you can play from any line, the call type and outcome, extracted facts, a rubric scorecard and grade where every point links to the audio that proves it, coaching, questions across all your calls, and follow-up tasks. The AI only assesses evidence; points, grades and CRM changes are computed deterministically in Python.

| Folder | What | Deploys to |
| --- | --- | --- |
| `web/` | Next.js 16 app: Better Auth sign-in, all screens, authenticated proxy to the API | Any Next.js host (Vercel config included, root directory `web`) |
| `api/` | FastAPI domain service: uploads, processing (transcribe → analyze → index), scoring, chat, CRM, metrics | Container (`api/Dockerfile`) |
| `scripts/` | Local dev helpers (PowerShell) and contract generation | — |

## How it works

1. **Upload.** The browser uploads MP3s straight to a private Cloudflare R2 bucket with a short-lived signed URL. The API checks size, hash and that the file decodes (ffprobe), then stores an immutable copy. Nothing is analyzed yet.
2. **Process (explicit click).** Processing is durable and fenced: an outbox event is handed to Upstash Workflow (QStash), and each stage holds a lease so retries and duplicates cannot double-publish.
   - **Transcribe:** OpenAI diarized transcription; long calls are split at silences.
   - **Analyze:** GPT-4.1-mini, temperature 0, structured output. It classifies the call (sales, customer service, retention), extracts facts and outcome with transcript evidence, and assesses each criterion of the configured rubric. Python validates every citation and computes points and grade.
   - **Index:** transcript chunks are embedded into Upstash Vector for questions.
3. **Review.** Transcripts are playable from any line. Every scorecard item links to the audio that proves it. Coaching shows one strength and up to three improvements. Reviewers can correct type, outcome, rep, criteria or transcript; changes are versioned and audited, and a type correction re-scores with the right rubric.
4. **Use.** The results feed several screens:
   - **Home and Sales:** real metrics with numerators and exclusions.
   - **Assistant:** questions across calls, with citations and exact SQL counts.
   - **Tasks:** follow-ups created from promises made on the call. Approving one never sends a message.
   - **Local pipeline and retention records:** conservative, audited automatic updates.
   - **Team:** rep profiles.
   - **Address checks:** Google Address Validation, with reviewer confirmation.

## Key decisions

- **Assessment and scoring are separate.** The model only assesses evidence; points, grades and CRM changes are deterministic Python. Unknown stays unknown, and a scorecard with unresolved items stays provisional.
- **Evidence or nothing.** A "met" criterion, fact or chat answer must cite transcript segments that the server resolves to real, current audio times.
- **Cost control.** Uploads are free; processing is an explicit, confirmed action. Daily token and audio budgets, plus a per-user question limit, cap spend.
- **Privacy.** Audio lives in a private R2 bucket and domain records in PostgreSQL. Telemetry carries no call content. Deleting a call purges storage, vectors and derived data, and fenced workers cannot resurrect it.

## Scoring rubrics

Scoring is driven by `api/resources/rubrics/example-v1.json`: three rubrics (`sales`, `office` for customer service, `retention`) of one-point criteria, each with a plain-language definition, and gold / green / below-standard thresholds. The example is generic; replace it with your own before relying on grades. See [`api/resources/rubrics/README.md`](api/resources/rubrics/README.md) for the format and the rules the code enforces.

For more detail than the definitions give, point `MANUAL_TEXT_DIR` at a folder of optional plain-text guides named `<rubric id>-guide.txt`; the evaluator reads the one for the rubric it is scoring.

## Known limitations

- **Accuracy needs human measurement.** Hand-label a sample of calls and track agreement per criterion and call type before trusting grades.
- **Speaker roles on long calls are inferred.** Chunks are labeled per piece and their roles are inferred from what each speaker says.
- **Some grades are provisional by design.** Criteria the rubric doesn't fully define stay unresolved rather than being guessed.
- **The CRM is local only.** No external CRM sync; "Won" does not prove payment or a booked appointment.

## Local development

Node 24 + pnpm, Python 3.13 + uv, FFmpeg/ffprobe on PATH. You also need PostgreSQL, a Cloudflare R2 bucket, an OpenAI API key, and Upstash QStash and Vector.

```powershell
python scripts/init_local_env.py          # creates ignored web/.env.local and api/.env from the examples
pnpm --dir web install --frozen-lockfile
uv sync --project api --locked
powershell -File scripts/dev-api.ps1      # API on :8000
powershell -File scripts/dev-web.ps1      # web on :3000
```

Fill the env files (see `CONFIGURATION.md` and the two `.env.example` files). Migrations: Better Auth owns the auth database schema; `uv run --locked alembic upgrade head` in `api/` owns the domain schema. `api/scripts/seed_local_org.py` creates a local demo organization and owner. After changing API routes or models, run `scripts/generate-contracts.ps1` (writes `web/contracts/`).

## Checks

`api/`: `uv run --locked ruff check call_intelligence_api tests` and `uv run --locked pytest -q`. `web/`: `pnpm lint`, `pnpm exec tsc --noEmit`, `pnpm build`. CI runs the same (`.github/workflows/ci.yml`).

## Deployment notes

- Browser uploads go straight to the private R2 bucket with a 5-minute signed URL, so the bucket needs a CORS rule allowing `PUT` with a `content-type` header from the web origin(s).
- The web app's `BETTER_AUTH_SECRET` must match the one that encrypted the JWT signing keys in the auth database.
- Migrations never run on boot; run `alembic upgrade head` deliberately.
- Keep the API's embedding dimension aligned with the vector index.

## License

MIT. See [LICENSE](LICENSE).
