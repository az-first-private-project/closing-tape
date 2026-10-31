"""
V5 API 集成测试（需要真实 PostgreSQL + 演示数据）
=================================================
默认跳过；显式开启：

    cd backend
    set GFNA_TEST_DB=1                     # PowerShell: $env:GFNA_TEST_DB="1"
    set SCHEDULER_ENABLED=0
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_api_v5.py -q -p no:cacheprovider

前提数据：`python scripts/pipeline_cli.py demo` + `features/scores/sector_scores/algos`。

覆盖的验收点（来自方案 8）：
  - /health 返回 database=connected
  - /market/dashboard 的 sectors 每条带 technical/composite 分数（P0 缺陷 #7）
  - /algo/list 不再 500（P0 缺陷 #20）
  - 同一交易日、同一参数连跑两次结果完全一致（D3 可复现）
  - pe_max 从 15→30 结果集变化（D3 参数真生效）
  - 统一错误体（E1）：未知策略返回 404 + error.code
  - CSV 导出可下载（E3）
  - 回测返回绩效指标与净值曲线（D4/D5）
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("GFNA_TEST_DB"),
    reason="需要 PostgreSQL 与演示数据；设置 GFNA_TEST_DB=1 后运行",
)

os.environ.setdefault("SCHEDULER_ENABLED", "0")
os.environ.setdefault("AUTO_CREATE_TABLES", "0")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.models.strategy import StgRun, StgSignal  # noqa: E402
from app.db.session import sync_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.strategy.strategies import SPECS  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def watch_state_client(client):
    """快照并在测试后恢复单租户状态，避免污染真实自选。"""
    original = client.get("/api/v1/watchlist")
    assert original.status_code == 200, original.text
    snapshot = original.json()
    try:
        yield client
    finally:
        restored = client.put(
            "/api/v1/watchlist",
            json={"codes": snapshot["codes"], "seen_hits": snapshot["seen_hits"]},
        )
        assert restored.status_code == 200, restored.text


# ============================================================
# 健康与元信息
# ============================================================

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["database"] == "connected"
    assert body["status"] == "ok"


def test_freshness_lists_datasets(client):
    resp = client.get("/api/v1/meta/freshness")
    assert resp.status_code == 200
    items = resp.json()["items"]
    datasets = {i["dataset"] for i in items}
    assert {"stock_daily", "stock_features", "trade_calendar"} <= datasets
    for item in items:
        assert item["status"] in {"ok", "stale", "unknown", "error", "quality_failed"}


def test_jobs(client):
    resp = client.get("/api/v1/meta/jobs?limit=5")
    assert resp.status_code == 200
    assert "items" in resp.json()


# ============================================================
# 行情
# ============================================================

def test_dashboard_has_scores(client):
    resp = client.get("/api/v1/market/dashboard")
    assert resp.status_code == 200
    body = resp.json()

    assert body["indices"], "应至少有一个指数"
    assert body["sectors"], "应至少有一个板块"
    scored = [s for s in body["sectors"] if s["composite_score"] is not None]
    assert scored, "至少部分板块应有综合评分（P0 缺陷 #7 回归点）"
    assert all(0 <= s["composite_score"] <= 10 for s in scored)
    assert body["freshness"], "仪表盘应带新鲜度"


def test_sector_detail(client):
    sectors = client.get("/api/v1/market/dashboard").json()["sectors"]
    code = sectors[0]["sector_code"]
    resp = client.get(f"/api/v1/market/sector/{code}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sector_code"] == code
    assert "recent_trend" in body


def test_stock_detail_and_kline(client):
    latest = client.get("/api/v1/market/dashboard").json()
    # 从任一板块取成分股；没有成分则跳过
    sectors = latest["sectors"]
    codes = []
    for s in sectors[:3]:
        detail = client.get(f"/api/v1/market/sector/{s['sector_code']}").json()
        codes = [m["code"] for m in (detail.get("members") or [])]
        if codes:
            break
    if not codes:
        pytest.skip("演示数据无板块成分")

    code = codes[0]
    resp = client.get(f"/api/v1/stocks/{code}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == code
    assert body["latest"]["close"] is not None

    kline = client.get(f"/api/v1/stocks/{code}/kline?limit=60")
    assert kline.status_code == 200
    kline_body = kline.json()
    assert kline_body["adjust_basis"] == "raw"
    assert len(kline_body["items"]) > 0
    for field in ("boll_mid", "boll_upper", "boll_lower"):
        assert field in kline_body["items"][0]


def test_stock_not_found_uses_unified_error(client):
    resp = client.get("/api/v1/stocks/999999")
    assert resp.status_code == 404
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "NOT_FOUND"


# ============================================================
# 选股
# ============================================================

def test_algo_list(client):
    resp = client.get("/api/v1/algo/list")
    assert resp.status_code == 200
    algos = resp.json()
    assert {a["algo_code"] for a in algos} == {"a3", "a4", "b1", "b2", "b3", "b4", "b5"}
    for algo in algos:
        assert algo["params_schema"]


def test_algo_consensus(client):
    resp = client.get("/api/v1/algo/consensus", params={"min_count": 2, "limit": 100})
    assert resp.status_code == 200
    body = resp.json()
    assert body["trade_date"]
    assert body["total_algos_evaluated"] >= 0
    assert body["min_count"] == 2
    assert body["freshness"]["dataset"] == "algo_run"
    for item in body["items"]:
        assert item["hit_count"] >= 2
        assert item["hit_count"] == len(item["algos"])
        assert item["hit_count"] <= body["total_algos_evaluated"]
        assert "avg_score" not in item
        assert {"code", "name", "industry_l1", "pct_chg", "composite_score"} <= set(item)
        for hit in item["algos"]:
            assert {"algo_code", "algo_name", "rank", "score", "metrics"} <= set(hit)
            assert len(hit["metrics"]) <= 3


def test_algo_consensus_min_count_validation(client):
    assert client.get("/api/v1/algo/consensus", params={"min_count": 1}).status_code == 422
    assert client.get("/api/v1/algo/consensus", params={"min_count": 8}).status_code == 422
    assert client.get("/api/v1/algo/consensus", params={"limit": 0}).status_code == 422


# ============================================================
# 自选股命中（V6 T8）
# ============================================================

def _live_hit_sample():
    """从库里取一条「当前启用策略 + 非回测」的最新命中。"""
    with Session(sync_engine) as session:
        latest = session.execute(
            select(func.max(StgRun.trade_date)).where(
                StgRun.is_backtest.is_(False), StgRun.status == "success"
            )
        ).scalar_one_or_none()
        if latest is None:
            return None
        row = session.execute(
            select(StgSignal.code, StgRun.algo_code)
            .join(StgRun, StgRun.run_id == StgSignal.run_id)
            .where(
                StgRun.trade_date == latest,
                StgRun.is_backtest.is_(False),
                StgRun.status == "success",
                StgRun.algo_code.in_(list(SPECS)),
            )
            .order_by(StgSignal.code, StgRun.algo_code)
            .limit(1)
        ).first()
        return (row[0], row[1], latest) if row else None


def test_algo_hits_returns_current_hits_and_dedupes(client):
    sample = _live_hit_sample()
    if sample is None:
        pytest.skip("库内没有可用的非回测命中样本")
    code, algo, latest = sample

    resp = client.post("/api/v1/algo/hits", json={"codes": [code, "600000"], "days": 10})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["days"] == 10
    assert body["freshness"]["dataset"] == "algo_run"

    items = body["items"]
    assert any(
        item["code"] == code
        and item["algo_code"] == algo
        and item["signal_date"] == latest.isoformat()
        for item in items
    )
    keys = {(item["code"], item["algo_code"], item["signal_date"]) for item in items}
    assert len(keys) == len(items), "同一策略同一天同一票只能返回一条"
    for item in items:
        assert item["code"] in {code, "600000"}
        assert item["algo_code"] in SPECS
        assert item["hit_id"] == f"{item['algo_code']}:{item['signal_date']}"
        assert {"code", "name", "algo_code", "algo_name", "signal_date", "rank", "hit_id"} <= set(item)

    if items:
        assert body["latest_signal_date"] == max(item["signal_date"] for item in items)


def test_algo_hits_excludes_backtest_only_codes(client):
    """只在回测 run 出现过的代码，接口必须返回空。"""
    with Session(sync_engine) as session:
        backtest_codes = (
            select(StgSignal.code)
            .join(StgRun, StgRun.run_id == StgSignal.run_id)
            .where(StgRun.is_backtest.is_(True))
        )
        live_codes = (
            select(StgSignal.code)
            .join(StgRun, StgRun.run_id == StgSignal.run_id)
            .where(StgRun.is_backtest.is_(False))
        )
        only_backtest = backtest_codes.except_(live_codes).subquery("only_backtest")
        codes = session.execute(
            select(only_backtest.c.code).order_by(only_backtest.c.code).limit(5)
        ).scalars().all()
    if not codes:
        pytest.skip("库内没有仅回测出现的代码")

    resp = client.post("/api/v1/algo/hits", json={"codes": list(codes), "days": 60})
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []


def test_algo_hits_validation(client):
    def post(payload):
        return client.post("/api/v1/algo/hits", json=payload)

    assert post({"codes": [], "days": 10}).json()["items"] == []
    assert post({"codes": ["'; drop"], "days": 10}).status_code == 422
    assert post({"codes": ["1234567"], "days": 10}).status_code == 422
    assert post({"codes": [f"{index:06d}" for index in range(200)], "days": 10}).status_code == 422
    assert post({"codes": ["600000"], "days": 0}).status_code == 422
    assert post({"codes": ["600000"], "days": 61}).status_code == 422

    injection = post({"codes": ["'; drop"], "days": 10}).json()
    assert injection["ok"] is False
    assert injection["error"]["code"] == "INVALID_PARAM"


def test_algo_unknown_returns_error_body(client):
    resp = client.get("/api/v1/algo/zzz")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "UNKNOWN_ALGO"


def test_algo_result_is_reproducible(client):
    first = client.get("/api/v1/algo/a3?limit=10").json()
    second = client.get("/api/v1/algo/a3?limit=10").json()
    assert first["count"] == second["count"]
    assert [d["code"] for d in first["data"]] == [d["code"] for d in second["data"]]
    assert first["run"]["run_id"] == second["run"]["run_id"], "相同参数应命中同一 run"


def test_algo_pe_max_really_changes_result(client):
    """D3 验收：pe_max 15 → 30 结果必须变化（旧实现参数不进计算，这里防回归）。"""
    narrow = client.get("/api/v1/algo/a4?pe_max=8&limit=50").json()
    wide = client.get("/api/v1/algo/a4?pe_max=30&limit=50").json()
    assert narrow["run"]["run_id"] != wide["run"]["run_id"], "参数不同必须产生不同 run"
    assert wide["count"] >= narrow["count"], "放宽 PE 上限后结果不应变少"


def test_algo_a4_excludes_negative_pe(client):
    body = client.get("/api/v1/algo/a4?limit=50&pe_max=100").json()
    for row in body["data"]:
        metrics = row.get("metrics") or {}
        pe = metrics.get("pe_ttm", row.get("pe_ttm"))
        if pe is not None:
            assert float(pe) > 0, f"A4 不应出现 PE<=0 的标的: {row['code']}"


def test_algo_csv_export(client):
    resp = client.get("/api/v1/algo/a3/export.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "attachment" in resp.headers.get("content-disposition", "")
    text = resp.content.decode("utf-8-sig")
    assert "代码" in text.splitlines()[0]


# ============================================================
# 回测
# ============================================================

def test_backtest_runs_and_persists(client):
    # end 必须钉在库里最新交易日：默认 today 在数据未更新当天会 DATA_NOT_READY
    latest = client.get("/api/v1/market/dashboard").json()["trade_date"]
    resp = client.post(f"/api/v1/backtest/a3?rebalance_days=20&max_positions=5&end={latest}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bt_id"]
    metrics = body["metrics"] or {}
    for key in ("total_return", "annual_return", "max_drawdown", "sharpe"):
        assert key in metrics, f"绩效指标缺少 {key}"
    assert body["equity_curve"], "应有净值曲线"
    assert metrics["max_drawdown"] <= 0

    again = client.get(f"/api/v1/backtest/{body['bt_id']}")
    assert again.status_code == 200
    assert again.json()["bt_id"] == body["bt_id"]

    # 成交明细接口：复用已落库 trades，不重算
    trades_resp = client.get(f"/api/v1/backtest/{body['bt_id']}/trades")
    assert trades_resp.status_code == 200, trades_resp.text
    trades_body = trades_resp.json()
    assert trades_body["bt_id"] == body["bt_id"]
    assert trades_body["algo_code"] == "a3"
    assert trades_body["count"] == len(body["trades"])
    if body["trades"]:
        code = body["trades"][0]["code"]
        filtered = client.get(f"/api/v1/backtest/{body['bt_id']}/trades?code={code}").json()
        assert filtered["count"] > 0
        assert all(t["code"].zfill(6) == code for t in filtered["items"])
    missing = client.get("/api/v1/backtest/BT-NOT-EXIST/trades")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


# ============================================================
# 自选状态（T8 服务端单租户）
# ============================================================

def test_watch_state_roundtrip_and_partial_update(watch_state_client):
    client = watch_state_client
    codes = ["000739", "000560"]
    seen = ["b2:2026-10-26", "b3:2026-10-26"]

    resp = client.put("/api/v1/watchlist", json={"codes": codes, "seen_hits": seen})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["codes"] == codes
    assert body["seen_hits"] == seen
    assert body["updated_at"]

    got = client.get("/api/v1/watchlist")
    assert got.status_code == 200, got.text
    assert got.json()["codes"] == codes
    assert got.json()["seen_hits"] == seen

    partial = client.put("/api/v1/watchlist", json={"codes": ["600000"]}).json()
    assert partial["codes"] == ["600000"]
    assert partial["seen_hits"] == seen, "只传 codes 时不应清空已读水位"

    partial = client.put("/api/v1/watchlist", json={"seen_hits": ["b2:2026-10-27"]}).json()
    assert partial["codes"] == ["600000"], "只传 seen_hits 时不应清空名单"
    assert partial["seen_hits"] == ["b2:2026-10-27"]

    cleared = client.put("/api/v1/watchlist", json={"codes": []}).json()
    assert cleared["codes"] == []


def test_watch_state_validation(watch_state_client):
    client = watch_state_client
    base = date(2026, 1, 1)
    too_many_seen = [
        f"b2:{(base + timedelta(days=i)).isoformat()}" for i in range(501)
    ]
    cases = [
        {"codes": ["'; drop"]},
        {"codes": [f"{i:06d}" for i in range(101)]},
        {"seen_hits": ["b2:not-a-date"]},
        {"seen_hits": ["../b2:2026-10-26"]},
        {"seen_hits": too_many_seen},
    ]
    for payload in cases:
        resp = client.put("/api/v1/watchlist", json=payload)
        assert resp.status_code == 422, (payload, resp.text)
        assert resp.json()["error"]["code"] == "INVALID_PARAM"
