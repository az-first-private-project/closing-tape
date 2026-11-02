from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.freshness import classify, summarize


def _row(hours_ago: float, status: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        last_success_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        status=status,
    )


def test_classify_ok_within_ttl() -> None:
    assert classify("stock_daily", _row(1)) == "ok"


def test_classify_stale_beyond_ttl() -> None:
    assert classify("stock_daily", _row(31)) == "stale"


def test_classify_keeps_failure_status() -> None:
    assert classify("stock_daily", _row(1, status="quality_failed")) == "quality_failed"


def test_summarize_ready_only_when_all_critical_ok() -> None:
    items = [
        {"display_name": "个股日线", "status": "ok", "is_critical": True},
        {"display_name": "新闻", "status": "unknown", "is_critical": False},
    ]
    assert summarize(items)["ready"] is True


def test_summarize_not_ready_when_critical_stale() -> None:
    items = [
        {"display_name": "个股日线", "status": "ok", "is_critical": True},
        {"display_name": "技术指标", "status": "stale", "is_critical": True},
    ]
    summary = summarize(items)
    assert summary["ready"] is False
    assert "技术指标" in summary["message"]


def test_summarize_not_ready_without_critical_datasets() -> None:
    assert summarize([])["ready"] is False
