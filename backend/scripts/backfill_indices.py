"""指数历史回补（T1：中证500 / 中证1000 等）。

为什么单独一个脚本：``collect_indices()`` 只写**目标交易日**一行，新加入
``INDEX_MAP`` 的指数没有历史行，20d/60d/YTD 会永远为空。这里按指数拉新浪
全历史并分批 upsert，断点只记录"该指数已全部提交完毕"，不记录半截进度：

    逐块 upsert + commit → 整只指数写完 → 更新断点（tmp + replace）

用法（backend 目录）：

    ../.venv/Scripts/python.exe scripts/backfill_indices.py --dry-run
    ../.venv/Scripts/python.exe scripts/backfill_indices.py
    ../.venv/Scripts/python.exe scripts/backfill_indices.py --codes sh000905,sh000852
    ../.venv/Scripts/python.exe scripts/backfill_indices.py --force
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import func, select  # noqa: E402

from app.core import config  # noqa: E402
from app.core.constants import INDEX_MAP  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.models.fact import MarketIndex  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engines  # noqa: E402
from app.ingest.akshare_provider import _normalize_index  # noqa: E402
from app.ingest.base import upsert_rows  # noqa: E402
from app.ingest.quality import QualityReport, record_quality  # noqa: E402

logger = get_logger(__name__)

CN_TZ = ZoneInfo("Asia/Shanghai")
_STATE = Path(config.BACKEND_DIR) / "var" / "index_backfill_progress.json"
_WRITE_CHUNK_ROWS = 1000
_MAX_ATTEMPTS = 3


def _save_state(state: dict) -> None:
    """原子写断点；Windows 上 os.replace 偶发被占用，短暂重试。"""
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    for attempt in range(6):
        try:
            os.replace(tmp, _STATE)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))


def _load_state() -> dict:
    try:
        return json.loads(_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _target_codes(raw: str | None) -> list[str]:
    if raw:
        codes = [item.strip() for item in raw.split(",") if item.strip()]
    else:
        codes = [code for code, (_, is_a_share) in INDEX_MAP.items() if is_a_share]
    unknown = [code for code in codes if code not in INDEX_MAP]
    if unknown:
        raise SystemExit(f"未知指数代码（不在 INDEX_MAP）：{unknown}")
    return codes


def _to_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


async def _db_coverage(codes: list[str]) -> dict[str, dict]:
    """断点对账：断点说完成 ≠ 库里真有数据。"""
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(
                MarketIndex.index_code,
                func.count().label("rows"),
                func.min(MarketIndex.trade_date).label("start"),
                func.max(MarketIndex.trade_date).label("end"),
            )
            .where(MarketIndex.index_code.in_(codes))
            .group_by(MarketIndex.index_code)
        )).all()
    return {
        code: {
            "rows": int(count or 0),
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
        }
        for code, count, start, end in rows
    }


async def _fetch_history(code: str):
    """新浪全历史（akshare 同步调用，放到线程里避免阻塞事件循环）。"""
    import akshare as ak

    last_error: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return await asyncio.to_thread(ak.stock_zh_index_daily, symbol=code)
        except Exception as exc:  # noqa: BLE001 —— 网络抖动重试后仍失败才抛出
            last_error = exc
            logger.warning("指数 %s 拉取失败（第 %d/%d 次）：%s",
                           code, attempt, _MAX_ATTEMPTS, exc)
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(2 * attempt)
    raise RuntimeError(f"{code} 拉取失败：{last_error}")


def _frame_to_rows(frame, code: str) -> list[dict]:
    index_name = INDEX_MAP[code][0]
    rows: list[dict] = []
    for record in frame.to_dict("records"):
        trade_date = _to_date(record.get("trade_date"))
        close = record.get("close")
        if trade_date is None or close is None:
            continue
        try:
            if float(close) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        rows.append({
            "index_code": code,
            "trade_date": trade_date,
            "index_name": index_name,
            "open": record.get("open"),
            "high": record.get("high"),
            "low": record.get("low"),
            "close": close,
            "pre_close": record.get("pre_close"),
            "pct_chg": record.get("pct_chg"),
            "volume": int(record["volume"]) if record.get("volume") is not None else None,
            "amount": record.get("amount"),
            "src": "akshare",
        })
    return rows


async def _write_rows(rows: list[dict]) -> int:
    """逐块提交：单只指数 5000+ 行，压在一个事务里既占 WAL 又看不到进度。"""
    written = 0
    async with AsyncSessionLocal() as session:
        for offset in range(0, len(rows), _WRITE_CHUNK_ROWS):
            chunk = rows[offset:offset + _WRITE_CHUNK_ROWS]
            written += await upsert_rows(
                session, MarketIndex, chunk, ["index_code", "trade_date"]
            )
            await session.commit()
    return written


async def _refresh_quality(codes: list[str], completed: int, failed: dict[str, str]) -> None:
    coverage = await _db_coverage(codes)
    total_rows = sum(item["rows"] for item in coverage.values())
    max_date = max((item["end"] for item in coverage.values() if item["end"]), default=None)
    report = QualityReport(
        dataset="index_daily",
        rows=total_rows,
        expected=len(codes),
        coverage=round(completed / len(codes), 4) if codes else 0.0,
    )
    for code, error in failed.items():
        report.add(
            "index_backfill_failed", count=1, severity="error",
            message=f"{code}: {error}", samples=[code],
        )
    async with AsyncSessionLocal() as session:
        await record_quality(
            session,
            "index_daily",
            report,
            _to_date(max_date),
            "ok" if not failed else "stale",
        )
        await session.commit()


async def run(args) -> int:
    codes = _target_codes(args.codes)
    state = {} if args.force else _load_state()
    completed: dict[str, dict] = dict(state.get("completed") or {})
    failed: dict[str, str] = dict(state.get("failed") or {})
    rows_written = int(state.get("rows_written") or 0)

    # 断点对账：完成标记存在但库里没有行 → 视为未完成重跑
    db_coverage = await _db_coverage(codes)
    for code in list(completed):
        if code not in codes:
            continue
        if db_coverage.get(code, {}).get("rows", 0) <= 0:
            logger.warning("断点显示 %s 已完成，但库中无数据，重新回补", code)
            completed.pop(code, None)

    pending = [code for code in codes if code not in completed]
    logger.info(
        "指数回补：目标 %d 只，断点已完成 %d，待处理 %d，失败 %d",
        len(codes), len(completed), len(pending), len(failed),
    )
    if args.dry_run:
        print(json.dumps({
            "codes": codes,
            "completed": completed,
            "pending": pending,
            "failed": failed,
            "rows_written": rows_written,
            "database": db_coverage,
            "checkpoint": str(_STATE),
        }, ensure_ascii=False, indent=2))
        return 0

    def persist() -> None:
        _save_state({
            "version": 1,
            "completed": completed,
            "failed": failed,
            "rows_written": rows_written,
            "updated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        })

    persist()
    started_at = time.perf_counter()
    for index, code in enumerate(pending, start=1):
        code_started = time.perf_counter()
        try:
            raw = await _fetch_history(code)
            frame = _normalize_index(raw, code, INDEX_MAP[code][0])
            rows = _frame_to_rows(frame, code)
            if not rows:
                raise RuntimeError("新浪返回空数据或全部为无效收盘价")
            written = await _write_rows(rows)
        except Exception as exc:  # noqa: BLE001 —— 单只失败不阻断其他指数
            failed[code] = f"{type(exc).__name__}: {exc}"
            persist()
            logger.error("指数 %s 回补失败：%s", code, exc)
            continue

        failed.pop(code, None)
        rows_written += written
        completed[code] = {
            "rows": len(rows),
            "start": rows[0]["trade_date"].isoformat(),
            "end": rows[-1]["trade_date"].isoformat(),
            "written": written,
        }
        persist()
        logger.info(
            "指数 %s 完成：%d 行（%s ~ %s），用时 %.1fs",
            code, written, completed[code]["start"], completed[code]["end"],
            time.perf_counter() - code_started,
        )
        # 新浪限频：指数间留间隔，避免连续 7 次请求被拒
        if index < len(pending):
            await asyncio.sleep(1)

    await _refresh_quality(codes, len(completed), failed)
    logger.info(
        "指数回补结束：完成 %d/%d，失败 %d，累计写入 %d 行，用时 %.1f 分钟",
        len(completed), len(codes), len(failed), rows_written,
        (time.perf_counter() - started_at) / 60,
    )
    return 0 if not failed else 1


async def _amain(args) -> int:
    try:
        return await run(args)
    finally:
        await dispose_engines()


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="指数历史回补（断点续传）")
    parser.add_argument("--codes", default=None,
                        help="逗号分隔的指数代码，默认 INDEX_MAP 里全部 A 股指数")
    parser.add_argument("--force", action="store_true", help="忽略断点全量重跑")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划与断点状态")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
