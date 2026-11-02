from __future__ import annotations

from datetime import date

import pytest

from scripts.reconcile_freshness import _ensure_recent


def test_recent_data_allowed():
    _ensure_recent(date(2026, 10, 30), today=date(2026, 11, 2))


def test_boundary_exactly_ten_days_allowed():
    _ensure_recent(date(2026, 10, 23), today=date(2026, 11, 2))


def test_stale_data_refused():
    with pytest.raises(SystemExit):
        _ensure_recent(date(2026, 10, 22), today=date(2026, 11, 2))
