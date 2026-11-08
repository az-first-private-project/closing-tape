"""纸面组合 API 集成测试（需要独立 gfna_test；未开启时跳过）。

测试进程由 `tests/conftest.py` 强制连接 `_test` 库，不会写生产库。

    set GFNA_TEST_DB=1
    set SCHEDULER_ENABLED=0
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_paper_api.py -q
"""
from __future__ import annotations

import os

import pytest

TEST_DB_ENABLED = os.getenv("GFNA_TEST_DB", "").strip().lower() in {
    "1", "true", "yes", "on",
}

pytestmark = pytest.mark.skipif(
    not TEST_DB_ENABLED,
    reason="需要独立 _test 库；设置 GFNA_TEST_DB=1 后运行",
)

os.environ.setdefault("SCHEDULER_ENABLED", "0")
os.environ.setdefault("AUTO_CREATE_TABLES", "0")

from fastapi.testclient import TestClient  # noqa: E402

from app.api.v1 import paper as paper_api  # noqa: E402
from app.engine.paper import enabled_algos  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_list_covers_all_enabled_algos(client) -> None:
    resp = client.get("/api/v1/paper/portfolios")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body["items"]
    assert body["total"] == len(items)

    codes = {item["algo_code"] for item in items}
    assert set(enabled_algos()) <= codes
    for item in items:
        assert item["status"] in {"pending", "waiting", "active", "failed"}
        assert {"tracking_days", "freshness", "nav", "excess_return"} <= set(item)
        assert "ready" in item["freshness"]


def test_unknown_algo_returns_404(client) -> None:
    resp = client.get("/api/v1/paper/portfolio/zzz")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "UNKNOWN_ALGO"

    resp = client.post("/api/v1/paper/portfolio/zzz/sync")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "UNKNOWN_ALGO"


def test_disabled_algo_is_rejected(client, monkeypatch) -> None:
    monkeypatch.setattr(paper_api, "is_enabled", lambda code: False)
    resp = client.get("/api/v1/paper/portfolio/b3")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_PARAM"


def test_detail_passes_days_through(client, monkeypatch) -> None:
    async def fake_detail(db, algo_code, days=250):
        return {"algo_code": algo_code, "days": days, "caliber": "test"}

    monkeypatch.setattr(paper_api, "is_enabled", lambda code: True)
    monkeypatch.setattr(paper_api, "get_paper_detail", fake_detail)

    resp = client.get("/api/v1/paper/portfolio/b3?days=7")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"algo_code": "b3", "days": 7, "caliber": "test"}

    assert client.get("/api/v1/paper/portfolio/b3?days=0").status_code == 422
    assert client.get("/api/v1/paper/portfolio/b3?days=2001").status_code == 422


def test_sync_is_accepted_and_runs_background(client, monkeypatch) -> None:
    called: list[str] = []

    async def fake_sync(algo_code: str) -> None:
        called.append(algo_code)

    monkeypatch.setattr(paper_api, "is_enabled", lambda code: True)
    monkeypatch.setattr(paper_api, "sync_paper_background", fake_sync)

    resp = client.post("/api/v1/paper/portfolio/b3/sync")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["algo"] == "b3"
    assert body["mode"] == "background"
    assert called == ["b3"]


def test_existing_portfolio_series_has_no_gaps_or_dupes(client) -> None:
    """若本库已有同步完成的组合，序列日期必须递增且与跟踪天数一致。"""
    items = client.get("/api/v1/paper/portfolios").json()["items"]
    active = [
        item for item in items
        if item["status"] == "active" and item["tracking_days"] > 0
    ]
    if not active:
        pytest.skip("本库尚无已完成纸面组合同步")

    portfolio = active[0]
    detail = client.get(
        f"/api/v1/paper/portfolio/{portfolio['algo_code']}?days=2000"
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    dates = [point["date"] for point in body["series"]]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))
    assert len(dates) == portfolio["tracking_days"]
    assert body["caliber"]
    assert {"holdings", "pending_orders", "vs_backtest", "freshness"} <= set(body)
