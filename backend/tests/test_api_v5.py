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

import csv
import os
import time
import uuid
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("GFNA_TEST_DB"),
    reason="需要 PostgreSQL 与演示数据；设置 GFNA_TEST_DB=1 后运行",
)

os.environ.setdefault("SCHEDULER_ENABLED", "0")
os.environ.setdefault("AUTO_CREATE_TABLES", "0")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.models.strategy import (  # noqa: E402
    BacktestGrid, BacktestGridCell, BacktestRun, StgRun, StgSignal,
)
from app.db.session import sync_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.strategy.strategies import SPECS  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def wait_backtest(client, bt_id: str, timeout: float = 120.0) -> dict:
    """P2-4：POST 只入队，轮询 GET 直到 success/failed。

    TestClient 会等 BackgroundTasks 执行完才返回响应，通常首次 GET 即成功；
    保留轮询是为了在真实异步执行下也能稳定通过。
    """
    deadline = time.monotonic() + timeout
    while True:
        resp = client.get(f"/api/v1/backtest/{bt_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("success", "failed"):
            break
        assert time.monotonic() < deadline, f"回测 {bt_id} 等待超时"
        time.sleep(0.2)
    assert body["status"] == "success", body.get("error")
    return body


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


def test_sparkline_batch_matches_single_kline(client):
    """P2-5：整页批量收盘序列必须与逐股 kline 的 close 完全一致。"""
    latest = client.get("/api/v1/market/dashboard").json()
    sectors = latest["sectors"]
    codes: list[str] = []
    for sector in sectors[:3]:
        detail = client.get(f"/api/v1/market/sector/{sector['sector_code']}").json()
        codes = [member["code"] for member in (detail.get("members") or [])]
        if len(codes) >= 2:
            break
    if not codes:
        pytest.skip("演示数据无板块成分")

    picked = codes[:2]
    resp = client.get("/api/v1/stocks/sparkline/batch", params={
        "codes": ",".join(picked),
        "days": 30,
        "end": latest["trade_date"],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["end"] == latest["trade_date"]
    assert body["days"] == 30
    assert body["adjust_basis"] == "raw"
    assert [item["code"] for item in body["items"]] == [c.zfill(6) for c in picked]

    by_code = {item["code"]: item["closes"] for item in body["items"]}
    for code in picked:
        kline = client.get(f"/api/v1/stocks/{code}/kline", params={
            "limit": 30,
            "end": latest["trade_date"],
        }).json()
        expected = [item["close"] for item in kline["items"]]
        assert by_code[code.zfill(6)] == expected, (
            f"{code} 批量收盘序列必须与单股 kline 一致"
        )
        assert len(expected) <= 30


def test_sparkline_batch_validation(client):
    invalid = client.get("/api/v1/stocks/sparkline/batch", params={"codes": "600519,abc"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_PARAM"

    too_many = ",".join(f"{i:06d}" for i in range(201))
    resp = client.get("/api/v1/stocks/sparkline/batch", params={"codes": too_many})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_PARAM"


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
    job = resp.json()
    assert job["bt_id"]
    assert job["status"] in ("queued", "running", "success")
    assert job["poll"] == f"/api/v1/backtest/{job['bt_id']}"

    body = wait_backtest(client, job["bt_id"])
    metrics = body["metrics"] or {}
    for key in ("total_return", "annual_return", "max_drawdown", "sharpe"):
        assert key in metrics, f"绩效指标缺少 {key}"
    assert body["equity_curve"], "应有净值曲线"
    assert metrics["max_drawdown"] <= 0

    # T9 派生视图：POST 即返回，避免前端二次请求
    assert body["extras_available"] is True
    monthly = body["monthly_returns"]
    assert monthly and monthly[0]["ym"]
    compounded = 1.0
    for item in monthly:
        if item["ret"] is not None:
            compounded *= 1.0 + item["ret"] / 100.0
    assert abs(compounded - 1.0 - metrics["total_return"]) < 0.001, "月度复利应与 total_return 自洽"

    drawdowns = [item["drawdown"] for item in body["drawdown_curve"]]
    assert len(drawdowns) == len(body["equity_curve"])
    assert min(drawdowns) == pytest.approx(metrics["max_drawdown"] * 100, abs=1e-4)

    assert len(body["rolling_excess_20d"]) == len(body["equity_curve"])
    assert all(item["value"] is None for item in body["rolling_excess_20d"][:20])

    exposure = body["industry_exposure"]
    assert exposure, "有成交时应能重建行业分布"
    for snapshot in exposure:
        weights = sum(item["weight"] for item in snapshot["industries"])
        assert weights == pytest.approx(100.0, abs=0.1), "行业权重 + 现金必须为 100%"
        assert any(item["name"] == "现金" for item in snapshot["industries"])

    again = client.get(f"/api/v1/backtest/{body['bt_id']}")
    assert again.status_code == 200
    again_body = again.json()
    assert again_body["bt_id"] == body["bt_id"]
    assert again_body["monthly_returns"] == monthly
    assert again_body["extras_available"] is True

    # CSV 的月度/回撤 block 与界面同口径（百分数，2 位小数）
    export = client.get(f"/api/v1/backtest/{body['bt_id']}/export.csv")
    assert export.status_code == 200
    lines = export.content.decode("utf-8-sig").splitlines()
    month_index = lines.index("# monthly returns (%)")
    assert lines[month_index + 1] == "month,strategy,benchmark,excess"
    row = next(csv.reader([lines[month_index + 2]]))
    assert row[0] == monthly[0]["ym"]
    assert float(row[1]) == round(monthly[0]["ret"], 2)
    if monthly[0]["benchmark"] is not None:
        assert float(row[2]) == round(monthly[0]["benchmark"], 2)

    dd_index = lines.index("# drawdown (%)")
    assert lines[dd_index + 1] == "date,nav,peak,drawdown"
    dd_lines: list[list[str]] = []
    for line in lines[dd_index + 2:]:
        if not line or line.startswith("#"):
            break
        dd_lines.append(next(csv.reader([line])))
    dd_map = {row[0]: float(row[3]) for row in dd_lines}
    worst = min(body["drawdown_curve"], key=lambda item: item["drawdown"])
    assert dd_map[worst["date"]] == round(worst["drawdown"], 2)

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


def test_backtest_extras_unavailable_for_legacy_record(client):
    """T9：旧记录没有曲线/成交时不能假装能派生，必须返回 extras_available=false。"""
    bt_id = f"BT-LEGACY-{uuid.uuid4().hex[:8]}"
    with Session(sync_engine) as session:
        session.add(BacktestRun(
            bt_id=bt_id,
            algo_code="a3",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            params={},
            metrics={},
            equity_curve=[],
            trades=[],
            status="success",
        ))
        session.commit()
    try:
        resp = client.get(f"/api/v1/backtest/{bt_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["extras_available"] is False
        for key in ("monthly_returns", "drawdown_curve", "rolling_excess_20d", "industry_exposure"):
            assert body[key] == []

        export = client.get(f"/api/v1/backtest/{bt_id}/export.csv")
        assert export.status_code == 200
        text = export.content.decode("utf-8-sig")
        assert "# monthly returns (%)" in text
        assert "# drawdown (%)" in text
    finally:
        with Session(sync_engine) as session:
            session.execute(delete(BacktestRun).where(BacktestRun.bt_id == bt_id))
            session.commit()


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


# ============================================================
# 参数敏感性矩阵（T10）
# ============================================================

def test_backtest_grid_end_to_end_and_reuse(client):
    latest = client.get("/api/v1/market/dashboard").json()["trade_date"]
    end = date.fromisoformat(latest)
    requested_start = (end - timedelta(days=180)).isoformat()

    # 先跑一次默认参数单回测，作为“同一格就是同一次回测”的基准
    single = client.post(
        f"/api/v1/backtest/a3?rebalance_days=20&max_positions=20"
        f"&start={requested_start}&end={latest}"
    )
    assert single.status_code == 200, single.text
    base = wait_backtest(client, single.json()["bt_id"])

    payload = {
        "start_date": base["start_date"],
        "end_date": base["end_date"],
        "rebalance_days": [20, 30],
        "max_positions": [5, 20],
    }
    with Session(sync_engine) as session:
        before = session.scalar(select(func.count()).select_from(BacktestRun))

    resp = client.post("/api/v1/backtest/a3/grid", json=payload)
    assert resp.status_code == 200, resp.text
    grid = resp.json()
    assert grid["total"] == 4
    assert grid["elapsed_ms"] == 0
    assert grid["queued"] == 4 - grid["reused"]
    assert grid["reused"] >= 1
    queued_cells = [c for c in grid["cells"] if not c["reused"]]
    assert all(c["status"] == "queued" for c in queued_cells)

    detail = client.get(f"/api/v1/backtest/grid/{grid['grid_id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["progress"] == {"done": 4, "total": 4, "failed": 0}
    assert body["start_date"] == base["start_date"]
    assert body["end_date"] == base["end_date"]
    assert body["axes"]["rebalance_days"] == [20, 30]
    assert body["axes"]["max_positions"] == [5, 20]
    assert all(cell["aligned"] for cell in body["cells"])

    by_key = {(c["rebalance_days"], c["max_positions"]): c for c in body["cells"]}
    first_reused = {c["bt_id"]: c["reused"] for c in body["cells"]}
    default_cell = by_key[(20, 20)]
    assert default_cell["bt_id"] == base["bt_id"], "默认参数格必须复用同一行回测"
    for key in ("total_return", "excess_return", "max_drawdown", "sharpe",
                "win_rate", "trade_count"):
        assert default_cell["metrics"][key] == base["metrics"][key]

    # 每格 bt_id 均可独立 GET（同一套回测详情/派生视图）
    first_elapsed = {}
    for cell in body["cells"]:
        one = client.get(f"/api/v1/backtest/{cell['bt_id']}")
        assert one.status_code == 200, one.text
        assert one.json()["status"] == "success"
        first_elapsed[cell["bt_id"]] = cell["elapsed_ms"]

    with Session(sync_engine) as session:
        after_first = session.scalar(select(func.count()).select_from(BacktestRun))

    # 重发同参数矩阵 → 全部命中复用，不新增回测行
    again = client.post("/api/v1/backtest/a3/grid", json=payload).json()
    assert again["grid_id"] != grid["grid_id"]
    assert again["reused"] == 4 and again["queued"] == 0
    assert again["elapsed_ms"] == 0
    assert {c["bt_id"] for c in again["cells"]} == set(first_elapsed)

    with Session(sync_engine) as session:
        after_second = session.scalar(select(func.count()).select_from(BacktestRun))
    assert after_second == after_first, "复用矩阵不应新增 backtest_run 行"

    again_detail = client.get(f"/api/v1/backtest/grid/{again['grid_id']}").json()
    assert again_detail["progress"] == {"done": 4, "total": 4, "failed": 0}
    for cell in again_detail["cells"]:
        assert cell["reused"] is True
        assert cell["elapsed_ms"] == first_elapsed[cell["bt_id"]], "复用不得重算耗时"

    # P2-2：复用只新增关联，旧矩阵链接仍须可打开且保持原样
    old_detail = client.get(f"/api/v1/backtest/grid/{grid['grid_id']}")
    assert old_detail.status_code == 200, old_detail.text
    old_body = old_detail.json()
    assert old_body["progress"] == {"done": 4, "total": 4, "failed": 0}
    assert {c["bt_id"] for c in old_body["cells"]} == set(first_elapsed)
    assert {c["bt_id"]: c["reused"] for c in old_body["cells"]} == first_reused
    for cell in old_body["cells"]:
        assert cell["elapsed_ms"] == first_elapsed[cell["bt_id"]]


def test_backtest_grid_rejects_invalid_axes(client):
    latest = client.get("/api/v1/market/dashboard").json()["trade_date"]
    start = (date.fromisoformat(latest) - timedelta(days=60)).isoformat()

    too_many = client.post("/api/v1/backtest/a3/grid", json={
        "start_date": start, "end_date": latest,
        "rebalance_days": [1, 2, 3, 4, 5], "max_positions": [1, 2, 3, 4, 5],
    })
    assert too_many.status_code == 422, too_many.text
    error = too_many.json()["error"]
    assert error["code"] == "INVALID_PARAM"
    assert "24" in error["message"]

    out_of_range = client.post("/api/v1/backtest/a3/grid", json={
        "start_date": start, "end_date": latest,
        "rebalance_days": [0], "max_positions": [5],
    })
    assert out_of_range.status_code == 422
    assert out_of_range.json()["error"]["code"] == "INVALID_PARAM"

    unknown_algo = client.post("/api/v1/backtest/zz/grid", json={
        "start_date": start, "end_date": latest,
        "rebalance_days": [20], "max_positions": [5],
    })
    assert unknown_algo.status_code == 404
    assert unknown_algo.json()["error"]["code"] == "UNKNOWN_ALGO"

    missing = client.get("/api/v1/backtest/grid/GRID-NOT-EXIST")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


def test_backtest_grid_marks_failed_cells(client):
    """真实引擎失败时：单格落 failed + error，接口仍 200，不抛出。"""
    end = date.today() + timedelta(days=1)
    while end.weekday() != 6:  # 找到未来的周日
        end += timedelta(days=1)
    start = end - timedelta(days=1)  # 周六 ~ 周日，日历为空
    resp = client.post("/api/v1/backtest/a3/grid", json={
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "rebalance_days": [20], "max_positions": [5, 10],
    })
    assert resp.status_code == 200, resp.text
    grid = resp.json()
    detail = client.get(f"/api/v1/backtest/grid/{grid['grid_id']}").json()
    assert detail["progress"] == {"done": 2, "total": 2, "failed": 2}
    for cell in detail["cells"]:
        assert cell["status"] == "failed"
        assert cell["metrics"] is None
        assert "交易日数量不足" in (cell["error"] or "")


def test_backtest_grid_marks_unaligned_cells(client):
    """同矩阵内区间不一致的格必须 aligned=false（热力图剔除）。"""
    latest = client.get("/api/v1/market/dashboard").json()["trade_date"]
    end = date.fromisoformat(latest)
    start = (end - timedelta(days=60)).isoformat()
    grid_id = str(uuid.uuid4())
    ids = [f"BT-GRID-{uuid.uuid4().hex[:10]}" for _ in range(3)]
    try:
        with Session(sync_engine) as session:
            session.add(BacktestGrid(
                grid_id=grid_id,
                algo_code="a3",
                start_date=date(2019, 12, 1),
                end_date=date.fromisoformat(latest),
                axes={"rebalance_days": [10, 20, 30], "max_positions": [5]},
            ))
            cells = (
                (10, latest, start),
                (20, latest, start),
                (30, "2020-01-02", "2019-12-01"),
            )
            for index, (rb, cell_end, cell_start) in enumerate(cells):
                session.add(BacktestRun(
                    bt_id=ids[index],
                    algo_code="a3",
                    start_date=date.fromisoformat(cell_start),
                    end_date=date.fromisoformat(cell_end),
                    params={"calendar_source": "db"},
                    metrics={"total_return": 0.1, "excess_return": 0.02,
                             "max_drawdown": -0.05, "sharpe": 1.0,
                             "win_rate": 0.5, "trade_count": 3},
                    status="success",
                    elapsed_ms=100 * (index + 1),
                ))
            session.flush()
            session.add_all([
                BacktestGridCell(
                    grid_id=grid_id, bt_id=ids[index],
                    rebalance_days=rb, max_positions=5, reused=False,
                )
                for index, (rb, _cell_end, _cell_start) in enumerate(cells)
            ])
            session.commit()

        body = client.get(f"/api/v1/backtest/grid/{grid_id}").json()
        by_rb = {cell["rebalance_days"]: cell for cell in body["cells"]}
        assert by_rb[10]["aligned"] is True
        assert by_rb[20]["aligned"] is True
        assert by_rb[30]["aligned"] is False
        assert body["progress"] == {"done": 3, "total": 3, "failed": 0}
        assert body["total_elapsed_ms"] == 600
    finally:
        with Session(sync_engine) as session:
            session.execute(delete(BacktestRun).where(BacktestRun.bt_id.in_(ids)))
            session.execute(delete(BacktestGrid).where(BacktestGrid.grid_id == grid_id))
            session.commit()
