"""Local durable worker for one explicitly selected call ID."""

from __future__ import annotations

import argparse
from uuid import UUID

from sqlalchemy.orm import Session

from call_intelligence_api.auth import domain_engine
from call_intelligence_api.call_models import CallRecord
from call_intelligence_api.processing import process_call


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("call_id", type=UUID)
    args = parser.parse_args()
    with Session(domain_engine()) as session:
        call = session.get(CallRecord, args.call_id)
        if call is None or call.deletion_status != "active":
            raise SystemExit("Call is unavailable")
        generation = call.requested_generation
    try:
        process_call(args.call_id, generation)
    except Exception as exc:  # noqa: BLE001  # keep private model/provider details out of terminal output
        raise SystemExit(f"Processing failed ({type(exc).__name__}); inspect safe stage status") from None
    print(f"call_id={args.call_id} processed_generation={generation}")


if __name__ == "__main__":
    main()
