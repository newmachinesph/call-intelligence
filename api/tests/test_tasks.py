"""Task list helpers: UTC due buckets, stable cursors and source-derived task kind."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from call_intelligence_api.task_routes import (
    decode_cursor,
    due_bucket,
    encode_cursor,
    task_kind,
)

NOW = datetime(2026, 3, 10, 15, 30, tzinfo=UTC)


def test_due_buckets_use_utc_day_boundaries():
    assert due_bucket(None, NOW) == "unknown"
    assert due_bucket(NOW - timedelta(minutes=1), NOW) == "overdue"
    assert due_bucket(NOW - timedelta(days=3), NOW) == "overdue"
    assert due_bucket(NOW, NOW) == "today"
    assert due_bucket(datetime(2026, 3, 10, 23, 59, tzinfo=UTC), NOW) == "today"
    assert due_bucket(datetime(2026, 3, 11, 0, 0, tzinfo=UTC), NOW) == "upcoming"
    # 20:00 on 10 March in UTC-5 is 01:00 on 11 March UTC, so it is not "today".
    eastern = datetime(2026, 3, 10, 20, 0, tzinfo=UTC) + timedelta(hours=5)
    assert due_bucket(eastern, NOW) == "upcoming"


def test_cursor_round_trip_and_rejection():
    item_id = uuid4()
    assert decode_cursor(encode_cursor(NOW, item_id)) == (NOW, item_id)
    for bad in ("not-base64!", encode_cursor(NOW, item_id)[:-3], "x" * 600):
        with pytest.raises(HTTPException) as info:
            decode_cursor(bad)
        assert info.value.status_code == 422


def test_task_kind_comes_from_commitment_source_or_defaults():
    assert task_kind([{"source_kind": "transcript", "commitment_kind": "callback"}]) == "callback"
    assert task_kind([{"source_kind": "transcript"}]) == "follow_up"
    assert task_kind(None) == "follow_up"
