"""
选股 API（方案 E3：选股中心改造）
================================
GET  /api/v1/algo/list                    策略清单（4 张卡片）
GET  /api/v1/algo/ladder                  策略天梯（信号级 5/10/20 日前瞻收益）
GET  /api/v1/algo/{code}                  执行/读取策略结果（参数真生效）
GET  /api/v1/algo/{code}/runs             历史 run
GET  /api/v1/algo/runs/{run_id}           单次 run 明细
GET  /api/v1/algo/{code}/export.csv       结果导出
POST /api/v1/algo/{code}/refresh          忽略缓存重跑

与 V4 的差异（对应方案缺陷 #13 / #27）：
  - 参数不再是"白名单装饰"，而是进入候选池、排序与缓存键
  - 返回体带 run 元信息（run_id / 耗时 / universe / data_version）
"""
from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidParamError, NotFoundError, UnknownAlgoError
from app.db.session import get_db
from app.strategy.runner import get_run, list_runs, run_strategy
from app.strategy.strategies import SPECS, list_specs

router = APIRouter(prefix="/api/v1/algo", tags=["选股"])


# ============================================================
# 工具
# ============================================================

def _collect_params(
    algo_code: str,
    limit: int,
    query: dict[str, Any],
) -> dict[str, Any]:
    """把 HTTP query 里属于该策略 schema 的参数收集起来。"""
    spec = SPECS.get(algo_code)
    params: dict[str, Any] = {"limit": limit}
    if spec is None:
        return params
    for field in spec.params_schema:
        key = field["key"]
        if key == "limit":
            continue
        raw = query.get(key)
        if raw is None or raw == "":
            continue
        ftype = field.get("type", "number")
        if ftype == "number":
            try:
                params[key] = float(raw)
            except (TypeError, ValueError):
                raise InvalidParamError(f"参数 {key} 必须是数字，收到 {raw!r}")
        elif ftype == "integer":
            try:
                params[key] = int(raw)
            except (TypeError, ValueError):
                raise InvalidParamError(f"参数 {key} 必须是整数，收到 {raw!r}")
        elif ftype == "select" and field.get("options"):
            options = field["options"]
            if raw not in options and str(raw) not in [str(o) for o in options]:
                raise InvalidParamError(
                    f"参数 {key} 取值非法，可选：{options}"
                )
            params[key] = raw
        else:
            params[key] = raw
    return params


def _ensure_algo(algo_code: str) -> None:
    if algo_code not in SPECS:
        raise UnknownAlgoError(
            f"未知策略: {algo_code}，可用: {sorted(SPECS.keys())}",
            detail={"available": sorted(SPECS.keys())},
        )


# ============================================================
# 元信息
# ============================================================

@router.get("/list")
async def get_algo_list() -> list[dict]:
    """策略清单。参数 schema 由后端单一维护，前端据此渲染表单。"""
    return [spec.as_algo_item() for spec in list_specs()]


@router.get("/health")
async def algo_health() -> dict:
    """健康检查：不再依赖任何外部 CLI。"""
    return {
        "engine": "local",
        "cli_available": False,
        "note": "V5 起选股完全在本地库计算，不再调用 westock-tool CLI",
        "algos": {code: spec.name for code, spec in SPECS.items()},
    }


@router.get("/ladder")
async def get_algo_ladder(
    window: int = Query(60, description="回看交易日数：20/60/120"),
    horizon: int = Query(20, description="收益口径：5/10/20"),
    trade_date: Optional[date] = Query(None, description="截止交易日，默认最新统计日"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """策略天梯：信号级 5/10/20 日前瞻收益与胜率（事后统计）。"""
    from app.engine.signal_perf import HORIZONS, LADDER_WINDOWS, LadderAlgo, ladder_snapshot
    from app.services.freshness import get_freshness_items

    if window not in LADDER_WINDOWS:
        raise InvalidParamError(
            f"window 只支持 {sorted(LADDER_WINDOWS)}，收到 {window}",
            detail={"field": "window", "value": window},
        )
    if horizon not in HORIZONS:
        raise InvalidParamError(
            f"horizon 只支持 {sorted(HORIZONS)}，收到 {horizon}",
            detail={"field": "horizon", "value": horizon},
        )

    algos = [LadderAlgo(code, spec.name) for code, spec in SPECS.items()]
    snapshot = await ladder_snapshot(
        db, algos, window=window, horizon=horizon, trade_date=trade_date
    )
    freshness = await get_freshness_items(db, ["signal_perf"])
    snapshot["freshness"] = freshness[0] if freshness else None
    snapshot["note"] = (
        "信号级收益：T 日收盘出信号、T+1 开盘买入；"
        "胜率为 ret_H > 0 的占比，与回测 FIFO 回合胜率不是同一口径"
    )
    return snapshot


@router.get("/consensus")
async def get_algo_consensus(
    min_count: int = Query(2, ge=2, le=7, description="最少命中策略数（2–7）"),
    limit: int = Query(100, ge=1, le=500, description="返回条数"),
    trade_date: Optional[date] = Query(None, description="交易日，默认最新行情日"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """多策略共识榜（V6 T6）。

    只统计当日成功且非回测的 run；同一策略多次参数 run 命中同一票只算一次。
    不提供跨策略平均分——各策略 score 量纲不同。
    """
    from app.engine.consensus import consensus_signals
    from app.services.freshness import get_freshness_items

    payload = await consensus_signals(
        db, trade_date, min_count=min_count, limit=limit
    )
    freshness = await get_freshness_items(db, ["algo_run"])
    payload["min_count"] = min_count
    payload["freshness"] = freshness[0] if freshness else None
    payload["note"] = "各策略评分量纲不同，榜单按命中策略数排序，不做跨策略分数平均。"
    return payload


# ============================================================
# 结果
# ============================================================

@router.get("/{algo_code}")
async def get_algo_result(
    algo_code: str,
    request: Request,
    trade_date: Optional[date] = Query(None, description="交易日，默认最新"),
    limit: int = Query(20, ge=1, le=1000, description="返回条数"),
    force_refresh: bool = Query(False, description="忽略缓存重算"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """执行或读取策略结果。

    策略参数从 query string 按 `params_schema` 收集（B1–B5 参数各异），
    参数不同 → 缓存必然不命中 → 重新计算并生成新的 run_id。
    """
    _ensure_algo(algo_code)
    params = _collect_params(algo_code, limit, dict(request.query_params))
    return await run_strategy(
        algo_code=algo_code,
        trade_date=trade_date,
        params=params,
        force=force_refresh,
        db=db,
    )


@router.post("/{algo_code}/refresh")
async def refresh_algo(
    algo_code: str,
    trade_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """忽略缓存，用默认参数重跑。"""
    _ensure_algo(algo_code)
    return await run_strategy(algo_code=algo_code, trade_date=trade_date,
                              params={}, force=True, db=db)


@router.get("/{algo_code}/runs")
async def get_algo_runs(
    algo_code: str,
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    """历史运行记录（不含信号明细）。"""
    _ensure_algo(algo_code)
    return {"items": await list_runs(algo_code, limit=limit)}


@router.get("/runs/{run_id}")
async def get_run_detail(run_id: str) -> dict:
    """单次运行的完整结果（用于复现）。"""
    detail = await get_run(run_id)
    if detail is None:
        raise NotFoundError(f"run 不存在: {run_id}", detail={"run_id": run_id})
    return detail


# ============================================================
# 导出
# ============================================================

@router.get("/{algo_code}/export.csv")
async def export_algo_csv(
    algo_code: str,
    request: Request,
    trade_date: Optional[date] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """结果导出为 CSV（UTF-8 BOM，Excel 直接打开不乱码）。"""
    _ensure_algo(algo_code)
    params = _collect_params(algo_code, limit, dict(request.query_params))
    result = await run_strategy(algo_code=algo_code, trade_date=trade_date,
                                params=params, force=False, db=db)

    # 动态列：固定列 + metrics 里出现的全部 key（并集，保证不丢字段）
    metric_keys: list[str] = []
    for row in result.get("data", []):
        for key in (row.get("metrics") or {}):
            if key == "composite_score":
                continue  # 已作为固定列「综合得分(0-10)」导出
            if key not in metric_keys:
                metric_keys.append(key)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["排名", "代码", "名称", "综合得分(0-10)", "排序分"] + metric_keys)
    for row in result.get("data", []):
        metrics = row.get("metrics") or {}
        composite = row.get("composite_score")
        if composite is None:
            composite = metrics.get("composite_score")
        writer.writerow(
            [row.get("rank"), row.get("code"), row.get("name"), composite, row.get("score")]
            + [metrics.get(k) for k in metric_keys]
        )

    run = result.get("run") or {}
    filename = f"{algo_code}_{result.get('trade_date') or 'latest'}_{(run.get('run_id') or '')[:8]}.csv"
    payload = "\ufeff" + buffer.getvalue()
    return Response(
        content=payload.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = ["router"]
