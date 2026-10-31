"""自选状态校验单测（V6 T8，无需数据库）。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core.errors import InvalidParamError
from app.engine.watch_state import (
    MAX_SEEN_HITS,
    normalize_hits_codes,
    normalize_seen_hits,
)


def test_normalize_seen_dedupes_and_keeps_order():
    raw = ["b2:2026-10-26", "b3:2026-10-26", "b2:2026-10-26"]
    assert normalize_seen_hits(raw) == ["b2:2026-10-26", "b3:2026-10-26"]


def test_normalize_seen_accepts_uppercase_algo():
    assert normalize_seen_hits(["B2:2026-10-26"]) == ["B2:2026-10-26"]


@pytest.mark.parametrize(
    "value",
    [
        "",
        "b2",
        "b2:2026/10/26",
        "b2:2026-13-01",
        "b2:2026-02-30",
        "b-2:2026-10-26",
        "b2:2026-10-26:extra",
        ":2026-10-26",
        "b2:",
        "x" * 33 + ":2026-10-26",
        123,
        None,
        True,
    ],
)
def test_normalize_seen_rejects_invalid(value):
    with pytest.raises(InvalidParamError) as exc:
        normalize_seen_hits([value])
    assert exc.value.status_code == 422
    assert exc.value.code == "INVALID_PARAM"


def test_normalize_seen_limit():
    base = date(2026, 1, 1)
    unique = [f"b2:{(base + timedelta(days=i)).isoformat()}" for i in range(MAX_SEEN_HITS + 1)]
    assert len(normalize_seen_hits(unique[:MAX_SEEN_HITS])) == MAX_SEEN_HITS
    with pytest.raises(InvalidParamError):
        normalize_seen_hits(unique)


def test_codes_reuse_hits_validation():
    assert normalize_hits_codes(["000739", "000739", "600000"]) == ["000739", "600000"]
    with pytest.raises(InvalidParamError):
        normalize_hits_codes(["'; drop"])
