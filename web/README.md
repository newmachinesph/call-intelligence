# Call Intelligence web

The Next.js client lives here. Use the [root setup and verification guide](../../README.md) for the full web, API, database, and private storage workflow. The client requires the FastAPI service and the configured Better Auth database.

For local development, install from the locked dependency file and run `pnpm dev` in this directory. Copy the proposed variables from `config/web.env.example` into the ignored `.env.local` file, keeping all service credentials on the server side.
