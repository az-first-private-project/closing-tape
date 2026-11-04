#!/usr/bin/env python
"""同花顺板块历史回补（2022-09 起）：日线 + 成分 + 涨跌家数/龙头。

在 ``backend`` 目录执行::

    python scripts/backfill_sector_ths.py                 # 默认 2022-09-01 ~ 上一交易日
    python scripts/backfill_sector_ths.py --start 2020-01-01
    python scripts/backfill_sector_ths.py --phase history   # 只跑某个阶段
    python scripts/backfill_sector_ths.py --limit 5         # 小范围冒烟

成分来源：THS 详情页只放前 5 页。总页数 ≤10 的板块用「升序+降序」取全；
更大的板块按名称映射改用东财 clist 完整成分（``var/em_mapping.json`` 缓存）。

断点：``backend/var/sector_backfill_progress.json``。
不变式：每个板块 upsert 并 commit 成功后才推进断点；断点范围（start/end）变化时整份重置。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]

PROGRESS_PATH = BACKEND_DIR / "var" / "sector_backfill_progress.json"
PHASES = ("history", "members", "aggregate")


# =====================================================================
# 断点
# =====================================================================

def _empty_progress(start: date, end: date) -> dict[str, Any]:
    return {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "history": {"completed": {}, "failed": {}},
        "members": {"completed": {}, "failed": {}},
        "updated_at": None,
    }


def load_progress(start: date, end: date) -> dict[str, Any]:
    if not PROGRESS_PATH.exists():
        return _empty_progress(start, end)
    try:
        data = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_progress(start, end)
    if data.get("range") != {"start": start.isoformat(), "end": end.isoformat()}:
        print(f"[resume] 区间变化，重置断点：{data.get('range')} -> "
              f"{start.isoformat()}..{end.isoformat()}")
        return _empty_progress(start, end)
    data.setdefault("history", {"completed": {}, "failed": {}})
    data.setdefault("members", {"completed": {}, "failed": {}})
    return data


def save_progress(progress: dict[str, Any]) -> None:
    progress["updated_at"] = datetime.now().isoformat(timespec="seconds")
    tmp = PROGRESS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows 下杀软/索引器会短暂占用目标文件，os.replace 偶发 WinError 5
    for attempt in range(5):
        try:
            os.replace(tmp, PROGRESS_PATH)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))


# =====================================================================
# 写入
# =====================================================================

async def upsert_history(rows: list[dict[str, Any]]) -> int:
    from app.db.models.fact import FactSectorDaily
    from app.db.session import AsyncSessionLocal
    from app.ingest.base import upsert_rows

    if not rows:
        return 0
    async with AsyncSessionLocal() as session:
        written = await upsert_rows(
            session, FactSectorDaily, rows, ["sector_code", "trade_date"]
        )
        await session.commit()
    return written


async def sync_members(
    sector_code: str,
    sector_name: str,
    sector_type: str,
    members: list[dict[str, str]],
    effective_from: date,
    partial: bool = False,
) -> dict[str, Any]:
    """SCD2 同步：新增开区间成分，移除者关闭区间（全部 commit 后才返回）。"""
    from sqlalchemy import select, update

    from app.db.models.dim import DimSectorMember
    from app.db.session import AsyncSessionLocal
    from app.ingest.base import upsert_rows

    new_codes = {item["stock_code"] for item in members}
    async with AsyncSessionLocal() as session:
        open_rows = (await session.execute(
            select(DimSectorMember.stock_code, DimSectorMember.is_partial).where(
                DimSectorMember.sector_code == sector_code,
                DimSectorMember.effective_to.is_(None),
            )
        )).all()
        partial_codes = {row[0] for row in open_rows if row[1]}
        complete_codes = {row[0] for row in open_rows if not row[1]}
        if partial and complete_codes:
            # 已有完整名单时残缺名单不得覆盖，否则会把已关闭的 partial 区间重开
            return {"added": 0, "closed": 0, "total": len(new_codes), "skipped": True}

        closed = 0
        if partial:
            to_add = new_codes - partial_codes
            to_close = partial_codes - new_codes
        else:
            # 完整名单落地：先结束被取代的 partial 区间，再按完整名单做差集，
            # 否则与完整名单相同的 partial 股票会被误判为"已存在"而无法升级。
            if partial_codes:
                result = await session.execute(
                    update(DimSectorMember)
                    .where(
                        DimSectorMember.sector_code == sector_code,
                        DimSectorMember.is_partial.is_(True),
                        DimSectorMember.effective_to.is_(None),
                    )
                    .values(effective_to=effective_from)
                )
                closed += result.rowcount or 0
            to_add = new_codes - complete_codes
            to_close = complete_codes - new_codes

        added = 0
        if to_add:
            added = await upsert_rows(
                session,
                DimSectorMember,
                [
                    {
                        "sector_code": sector_code,
                        "stock_code": code,
                        "effective_from": effective_from,
                        "effective_to": None,
                        "sector_name": sector_name[:64],
                        "sector_type": sector_type,
                        "is_partial": partial,
                    }
                    for code in sorted(to_add)
                ],
                ["sector_code", "stock_code", "effective_from"],
                update_columns=["effective_to", "sector_name", "sector_type", "is_partial"],
            )
        if to_close:
            result = await session.execute(
                update(DimSectorMember)
                .where(
                    DimSectorMember.sector_code == sector_code,
                    DimSectorMember.stock_code.in_(sorted(to_close)),
                    DimSectorMember.effective_to.is_(None),
                )
                .values(effective_to=effective_from)
            )
            closed += result.rowcount or 0
        await session.commit()
    return {"added": added, "closed": closed, "total": len(new_codes)}


# =====================================================================
# 阶段
# =====================================================================

async def _fetch_with_retry(fn, *args, attempts: int = 4):
    """网络抖动（DNS/连接重置）重试；全部失败才抛最后一个异常。"""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await asyncio.to_thread(fn, *args)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            await asyncio.sleep(1.0 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


async def phase_history(boards, start: date, end: date, progress: dict,
                        limit: int | None) -> None:
    from app.ingest.ths_board import fetch_board_history, history_rows

    done = progress["history"]["completed"]
    failed = progress["history"]["failed"]
    pending = [b for b in boards if b["sector_code"] not in done]
    if limit:
        pending = pending[:limit]
    print(f"[history] 待处理 {len(pending)}/{len(boards)} 个板块", flush=True)

    for index, board in enumerate(pending, start=1):
        code = board["sector_code"]
        try:
            # 区间首日涨跌幅要拿前一交易日收盘来算，只取 [start, end] 会让 start 当天 pct_chg 为空
            frame = await _fetch_with_retry(
                fetch_board_history, board, start - timedelta(days=10), end
            )
            rows = [
                row for row in history_rows(frame, board)
                if start <= row["trade_date"] <= end
            ]
            written = await upsert_history(rows)
            done[code] = written
            failed.pop(code, None)
            status = f"{written} 行"
        except Exception as exc:  # noqa: BLE001 —— 单板块失败不阻断
            failed[code] = f"{type(exc).__name__}: {exc}"[:200]
            status = f"失败 {type(exc).__name__}: {str(exc)[:80]}"
        save_progress(progress)
        if index % 10 == 0 or index == len(pending):
            print(f"[history] {index}/{len(pending)} {board['sector_name']} {status}"
                  f"（失败累计 {len(failed)}）", flush=True)
        await asyncio.sleep(0.1)


async def phase_members(boards, start: date, progress: dict,
                        limit: int | None, mapping: dict[str, dict]) -> None:
    from app.ingest.em_board import fetch_board_members as fetch_em_members
    from app.ingest.ths_board import fetch_board_members, member_page_count

    done = progress["members"]["completed"]
    failed = progress["members"]["failed"]
    pending = [
        b for b in boards
        if b["sector_code"] not in done
        # 之前只能拿到 THS 部分名单、现在有东财映射的板块，重跑升级为完整名单
        or (
            isinstance(done.get(b["sector_code"]), dict)
            and done[b["sector_code"]].get("partial")
            and b["sector_code"] in mapping
        )
    ]
    if limit:
        pending = pending[:limit]
    print(f"[members] 待处理 {len(pending)}/{len(boards)} 个板块", flush=True)

    for index, board in enumerate(pending, start=1):
        code = board["sector_code"]
        try:
            total_pages, _ = await _fetch_with_retry(member_page_count, board)
            em = mapping.get(code)
            if total_pages > 10 and em:
                # 大板块：THS 只能看到前端 100/50 只，改用东财完整名单
                fetched: list[dict[str, str]] = []
                for em_code in em["em_codes"]:
                    fetched.extend(
                        await _fetch_with_retry(fetch_em_members, em_code, attempts=3)
                    )
                seen: dict[str, str] = {}
                for item in fetched:
                    seen.setdefault(item["stock_code"], item["stock_name"])
                members = [
                    {"stock_code": key, "stock_name": value}
                    for key, value in sorted(seen.items())
                ]
                meta = {"source": "em", "partial": False}
            else:
                members, meta = await _fetch_with_retry(
                    fetch_board_members, board, attempts=3
                )
            result = await sync_members(
                code, board["sector_name"], board["sector_type"], members, start,
                partial=meta["partial"],
            )
            done[code] = {
                "count": result["total"],
                "source": meta["source"],
                "partial": meta["partial"],
            }
            failed.pop(code, None)
            flag = "（不完整）" if meta["partial"] else ""
            status = (
                f"{result['total']} 只[{meta['source']}]{flag}"
                f"（+{result['added']}/-{result['closed']}）"
            )
        except Exception as exc:  # noqa: BLE001
            failed[code] = f"{type(exc).__name__}: {exc}"[:200]
            status = f"失败 {type(exc).__name__}: {str(exc)[:80]}"
        save_progress(progress)
        if index % 10 == 0 or index == len(pending):
            print(f"[members] {index}/{len(pending)} {board['sector_name']} {status}"
                  f"（失败累计 {len(failed)}）", flush=True)
        await asyncio.sleep(0.1)


_AGGREGATE_SQL = """
WITH member AS (
    SELECT DISTINCT sector_code, stock_code
    FROM dim_sector_member
    WHERE sector_type IS NOT NULL
      AND sector_code NOT IN :partial
      AND effective_from <= :end
      AND (effective_to IS NULL OR effective_to > :start)
),
pairs AS (
    SELECT m.sector_code, d.trade_date, d.code, d.pct_chg
    FROM member m
    JOIN fact_stock_daily d ON d.code = m.stock_code
    WHERE d.trade_date BETWEEN :start AND :end
),
agg AS (
    SELECT sector_code, trade_date,
           COUNT(*) FILTER (WHERE pct_chg > 0) AS up_count,
           COUNT(*) FILTER (WHERE pct_chg < 0) AS down_count,
           MAX(pct_chg) AS leading_pct
    FROM pairs
    GROUP BY 1, 2
),
leader AS (
    SELECT DISTINCT ON (sector_code, trade_date)
           sector_code, trade_date, code, pct_chg
    FROM pairs
    WHERE pct_chg IS NOT NULL
    ORDER BY sector_code, trade_date, pct_chg DESC
)
UPDATE fact_sector_daily f
SET up_count = a.up_count,
    down_count = a.down_count,
    leading_stock = COALESCE(s.name, l.code),
    leading_stock_pct = l.pct_chg,
    updated_at = now()
FROM agg a
LEFT JOIN leader l ON l.sector_code = a.sector_code AND l.trade_date = a.trade_date
LEFT JOIN dim_stock s ON s.code = l.code
WHERE f.sector_code = a.sector_code
  AND f.trade_date = a.trade_date
  AND f.trade_date BETWEEN :start AND :end
"""

_LEADING_ONLY_SQL = """
WITH member AS (
    SELECT DISTINCT sector_code, stock_code
    FROM dim_sector_member
    WHERE sector_type IS NOT NULL
      AND sector_code IN :partial
      AND effective_from <= :end
      AND (effective_to IS NULL OR effective_to > :start)
),
pairs AS (
    SELECT m.sector_code, d.trade_date, d.code, d.pct_chg
    FROM member m
    JOIN fact_stock_daily d ON d.code = m.stock_code
    WHERE d.trade_date BETWEEN :start AND :end
),
leader AS (
    SELECT DISTINCT ON (sector_code, trade_date)
           sector_code, trade_date, code, pct_chg
    FROM pairs
    WHERE pct_chg IS NOT NULL
    ORDER BY sector_code, trade_date, pct_chg DESC
)
UPDATE fact_sector_daily f
SET leading_stock = COALESCE(s.name, l.code),
    leading_stock_pct = l.pct_chg,
    updated_at = now()
FROM leader l
LEFT JOIN dim_stock s ON s.code = l.code
WHERE f.sector_code = l.sector_code
  AND f.trade_date = l.trade_date
  AND f.trade_date BETWEEN :start AND :end
"""

_INDUSTRY_SQL = """
WITH ind AS (
    SELECT DISTINCT ON (stock_code) stock_code, sector_name
    FROM dim_sector_member
    WHERE sector_type = 'industry' AND effective_to IS NULL
    ORDER BY stock_code, effective_from DESC
)
UPDATE dim_stock s
SET industry_l1 = ind.sector_name
FROM ind
WHERE ind.stock_code = s.code
  AND s.industry_l1 IS DISTINCT FROM ind.sector_name
"""


async def phase_aggregate(start: date, end: date, partial_codes: set[str] | None = None) -> None:
    from sqlalchemy import bindparam, text

    from app.db.session import AsyncSessionLocal

    partial = sorted(partial_codes or set())
    async with AsyncSessionLocal() as session:
        print(f"[aggregate] 聚合涨跌家数/龙头（跳过不完整名单 {len(partial)} 个板块）…", flush=True)
        started = time.perf_counter()
        stmt = text(_AGGREGATE_SQL).bindparams(bindparam("partial", expanding=True))
        result = await session.execute(
            stmt, {"start": start, "end": end, "partial": partial or ["__none__"]}
        )
        await session.commit()
        print(f"[aggregate] 板块日线更新 {result.rowcount} 行，"
              f"{time.perf_counter() - started:.1f}s", flush=True)

        if partial:
            stmt = text(_LEADING_ONLY_SQL).bindparams(bindparam("partial", expanding=True))
            await session.execute(
                text(
                    "UPDATE fact_sector_daily SET up_count = NULL, down_count = NULL, "
                    "updated_at = now() "
                    "WHERE sector_code IN :partial AND trade_date BETWEEN :start AND :end"
                ).bindparams(bindparam("partial", expanding=True)),
                {"start": start, "end": end, "partial": partial},
            )
            result = await session.execute(stmt, {"start": start, "end": end, "partial": partial})
            await session.commit()
            print(f"[aggregate] 不完整名单板块仅回填龙头 {result.rowcount} 行", flush=True)

        result = await session.execute(text(_INDUSTRY_SQL))
        await session.commit()
        print(f"[aggregate] dim_stock.industry_l1 更新 {result.rowcount} 行", flush=True)


# =====================================================================
# 入口
# =====================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同花顺板块历史回补")
    parser.add_argument("--start", default="2022-09-01", help="开始日期（含）")
    parser.add_argument("--end", default=None, help="结束日期（含），默认上一交易日")
    parser.add_argument("--phase", choices=("all",) + PHASES, default="all")
    parser.add_argument("--limit", type=int, default=None, help="每阶段最多处理多少个板块")
    parser.add_argument("--refresh-list", action="store_true", help="忽略缓存重新拉板块清单")
    parser.add_argument("--refresh-mapping", action="store_true",
                        help="忽略缓存重建 THS→东财板块映射")
    return parser.parse_args()


async def load_em_mapping(boards: list[dict[str, str]], refresh: bool) -> dict[str, dict]:
    """THS→东财板块映射（缓存到 var/em_mapping.json）。失败时返回空映射。"""
    path = BACKEND_DIR / "var" / "em_mapping.json"
    if path.exists() and not refresh:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("mapping") is not None:
                return data["mapping"]
        except ValueError:
            pass
    from app.ingest.em_board import build_em_mapping

    mapping: dict[str, dict] = {}
    for attempt in range(3):
        try:
            mapping = await asyncio.to_thread(build_em_mapping, boards)
            break
        except Exception as exc:  # noqa: BLE001 —— 映射失败只影响大板块补全
            print(f"[mapping] 东财映射构建失败（第 {attempt + 1} 次）：{exc}", flush=True)
            await asyncio.sleep(3.0 * (attempt + 1))
    if not mapping:
        return {}
    path.write_text(
        json.dumps(
            {"generated_at": datetime.now().isoformat(timespec="seconds"),
             "mapping": mapping},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return mapping


async def _resolve_end_async(value: str | None) -> date:
    if value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    from sqlalchemy import text

    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        row = (await session.execute(text(
            "SELECT max(cal_date) FROM trade_calendar "
            "WHERE is_open = true AND cal_date < CURRENT_DATE"
        ))).first()
    return (row[0] if row and row[0] else None) or (date.today() - timedelta(days=1))


async def amain(args: argparse.Namespace) -> int:
    from app.core.logging import setup_logging
    from app.db.session import dispose_engines
    from app.ingest.ths_board import fetch_board_list

    setup_logging()
    try:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = await _resolve_end_async(args.end)
        if start > end:
            print(f"start {start} 晚于 end {end}")
            return 2

        list_path = BACKEND_DIR / "var" / "sector_board_list.json"
        boards: list[dict[str, str]] | None = None
        if list_path.exists() and not args.refresh_list:
            try:
                cached = json.loads(list_path.read_text(encoding="utf-8"))
                if isinstance(cached, list) and cached:
                    boards = cached
            except ValueError:
                boards = None
        if boards is None:
            print("[list] 拉取同花顺板块清单 …", flush=True)
            boards = await asyncio.to_thread(fetch_board_list)
            list_path.write_text(
                json.dumps(boards, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(f"[list] 行业+概念共 {len(boards)} 个板块；区间 {start} … {end}", flush=True)

        progress = load_progress(start, end)
        print(f"[resume] 历史已完成 {len(progress['history']['completed'])}，"
              f"成分已完成 {len(progress['members']['completed'])}", flush=True)

        partial_codes = {
            code
            for code, item in progress["members"]["completed"].items()
            if isinstance(item, dict) and item.get("partial")
        }
        if args.phase in ("all", "history"):
            await phase_history(boards, start, end, progress, args.limit)
        if args.phase in ("all", "members"):
            mapping = await load_em_mapping(
                boards, args.refresh_mapping or args.refresh_list
            )
            print(f"[mapping] THS→东财匹配 {len(mapping)} 个板块", flush=True)
            await phase_members(boards, start, progress, args.limit, mapping)
            partial_codes = {
                code
                for code, item in progress["members"]["completed"].items()
                if isinstance(item, dict) and item.get("partial")
            }
        if args.phase in ("all", "aggregate"):
            await phase_aggregate(start, end, partial_codes)

        partial = len(partial_codes)
        summary = {
            "range": progress["range"],
            "history_done": len(progress["history"]["completed"]),
            "history_failed": len(progress["history"]["failed"]),
            "members_done": len(progress["members"]["completed"]),
            "members_partial": partial,
            "members_failed": len(progress["members"]["failed"]),
            "boards": len(boards),
        }
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 0
    finally:
        await dispose_engines()


def main() -> int:
    return asyncio.run(amain(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
