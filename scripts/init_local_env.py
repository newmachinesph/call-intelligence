"""Create ignored local env files without overwriting saved credentials."""

from pathlib import Path
from secrets import token_urlsafe


ROOT = Path(__file__).resolve().parents[1]


def create(template: str, destination: str, generated: set[str]) -> None:
    output = ROOT / destination
    if output.exists():
        print(f"Kept existing {destination}")
        return

    lines = (ROOT / template).read_text(encoding="utf-8").splitlines()
    rendered = []
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key in generated and not value:
            line = f"{key}={token_urlsafe(48)}"
        rendered.append(line)
    output.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    print(f"Created ignored {destination}")


if __name__ == "__main__":
    create("web/.env.example", "web/.env.local", {"BETTER_AUTH_SECRET"})
    create("api/.env.example", "api/.env", {"PROCESSING_SERVICE_SECRET"})
