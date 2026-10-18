"""Export the actual FastAPI OpenAPI schema for TypeScript generation."""

import json
from pathlib import Path

from call_intelligence_api.main import app

OUTPUT = Path(__file__).resolve().parents[2] / "web" / "contracts" / "openapi.json"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
