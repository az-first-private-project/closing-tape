#!/usr/bin/env python
"""导入浏览器抓取的 THS 板块成分（var/ths_members/batch_result_*.json）。

同花顺成分页第 6 页起需要登录，Python 直连会被 WAF 拦；改由真实浏览器在页面内
fetch 逐板块抓取，POST 到本地 collector 落盘，本脚本再把 JSON 落库。

不变式：每个板块 sync_members（SCD2 upsert）commit 成功后才推进断点。

用法（backend 目录）::

    python scripts/import_ths_members.py            # 导入全部批次
    python scripts/import_ths_members.py --dry-run  # 只统计不写库
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backfill_sector_ths import PROGRESS_PATH, save_progress, sync_members  # noqa: E402

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]

MEMBERS_DIR = BACKEND_DIR / "var" / "ths_members"


# =====================================================================
# 解析（兼容 collector 可能的几种包裹形状）
# =====================================================================

def load_boards_by_name() -> dict[str, dict[str, str]]:
    path = BACKEND_DIR / "var" / "sector_board_list.json"
    boards = json.loads(path.read_text(encoding="utf-8"))
    return {b["sector_name"]: b for b in boards if b.get("sector_name")}


def _member_rows(raw: Any) -> list[dict[str, str]]:
    """兼容 [{stock_code,stock_name}] / [[code,name]] / {code: name} 三种形状。

    浏览器 fetch().text() 按 UTF-8 解码 GBK 页面，个股名称会是乱码；
    代码不受影响，且 dim_sector_member 不存个股名称，乱码名称直接丢弃。
    """
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        items: Iterable[Any] = raw.items()
    else:
        items = raw or []
    for item in items:
        if isinstance(item, dict):
            code = str(item.get("stock_code") or item.get("code") or "").strip()
            name = str(item.get("stock_name") or item.get("name") or "").strip()
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            code, name = str(item[0]).strip(), str(item[1]).strip()
        else:
            continue
        if "\ufffd" in name:
            name = ""
        if code.isdigit() and len(code) <= 6:
            out[code.zfill(6)] = name
    return [{"stock_code": c, "stock_name": n} for c, n in sorted(out.items())]


def iter_results(payload: Any) -> Iterable[tuple[str, list[dict[str, str]], int]]:
    """把一批 JSON 归一化成 (板块名, 成员, 总页数)。"""
    if isinstance(payload, dict):
        for key in ("results", "boards", "data"):
            if key in payload:
                payload = payload[key]
                break
    entries: list[tuple[Any, Any]]
    if isinstance(payload, dict):
        entries = list(payload.items())
    elif isinstance(payload, list):
        entries = [((item or {}).get("name") or (item or {}).get("sector_name"), item)
                   for item in payload]
    else:
        return
    for name, item in entries:
        if not name or not isinstance(item, dict):
            continue
        yield (
            str(name),
            _member_rows(item.get("members")),
            int(item.get("total_pages") or 1),
        )


def is_complete(members: list[dict[str, str]], total_pages: int,
                sector_type: str) -> bool:
    """按 THS 每页条数校验名单是否抓全（中间页 403 会让成员数不达标）。

    行业 20 条/页、概念 10 条/页；第 1 页满页才可能分页，所以完整名单的
    成员数下限是 (pages-1)*size+1。总页数 >10 的板块只抓双向各 5 页，
    按设计就是 partial，满额 10 页即视为"抓取完整"。
    """
    size = 20 if sector_type == "industry" else 10
    n = len(members)
    if total_pages > 10:
        return n == 10 * size
    if total_pages <= 1:
        return n >= 1
    return n >= (total_pages - 1) * size + 1


def _better(candidate: tuple[str, list[dict[str, str]], int, str],
            current: tuple[str, list[dict[str, str]], int, str],
            sector_type: str) -> bool:
    """同一板块在多个批次文件里出现时择优：完整 > 不完整，非 partial > partial，成员多 > 少。"""
    def rank(entry: tuple[str, list[dict[str, str]], int, str]) -> tuple:
        _name, members, pages, _file = entry
        return (
            is_complete(members, pages, sector_type),
            pages <= 10,
            len(members),
        )

    return rank(candidate) > rank(current)


# =====================================================================
# 主流程
# =====================================================================

def load_progress() -> dict[str, Any]:
    data = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    data.setdefault("members", {"completed": {}, "failed": {}})
    return data


async def main(dry_run: bool, force: bool = False, skip_partial: bool = False,
               names: list[str] | None = None) -> int:
    from app.core.logging import setup_logging
    from app.db.session import dispose_engines

    setup_logging()
    files = sorted(MEMBERS_DIR.glob("batch_result_*.json")) + \
        sorted(MEMBERS_DIR.glob("v2_*.json"))
    if not files:
        print(f"没有批次文件：{MEMBERS_DIR}/batch_result_*.json")
        return 1

    boards_by_name = load_boards_by_name()
    only = set(names or [])
    # dry-run 也读真实断点：否则所有板块都显示“新”，无法判断待导入范围
    progress = load_progress()
    completed = progress["members"]["completed"]
    failed = progress["members"]["failed"]
    start = datetime.strptime(progress["range"]["start"], "%Y-%m-%d").date() \
        if progress.get("range") else datetime(2022, 9, 1).date()

    best: dict[str, tuple[str, list[dict[str, str]], int, str]] = {}
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for name, members, total_pages in iter_results(payload):
            if only and name not in only:
                continue
            board = boards_by_name.get(name)
            if board is None:
                print(f"[skip] 板块清单无此名称：{name}")
                continue
            if not members:
                continue
            code = board["sector_code"]
            candidate = (name, members, total_pages, path.name)
            current = best.get(code)
            if current is None or _better(candidate, current, board["sector_type"]):
                best[code] = candidate

    imported = skipped = 0
    damaged: list[str] = []
    try:
        for code, (name, members, total_pages, _file) in best.items():
            board = boards_by_name[name]
            if code in completed and not force:
                skipped += 1
                continue
            if not is_complete(members, total_pages, board["sector_type"]):
                print(f"[skip] {name} 名单不完整（{len(members)} 只 / {total_pages} 页）")
                damaged.append(name)
                skipped += 1
                continue
            partial = total_pages > 10
            if partial and skip_partial:
                # 只抓了前/后各 5 页的名单不能当完整名单用于市值聚合
                print(f"[skip] {name} 仅抓到 {len(members)} 只（{total_pages} 页，partial）")
                skipped += 1
                continue
            if dry_run:
                done = completed.get(code)
                mark = "" if done else " (新)"
                print(f"[dry] {name:<12} {len(members):>4} 只 partial={partial}{mark}")
                imported += 1
                continue
            result = await sync_members(
                code, board["sector_name"], board["sector_type"], members, start,
                partial=partial,
            )
            completed[code] = {
                "count": result["total"],
                "source": "ths",
                "partial": partial,
            }
            failed.pop(code, None)
            save_progress(progress)  # sync_members 已 commit，断点只能在其后推进
            imported += 1
            if imported % 25 == 0:
                print(f"[import] 已导入 {imported} 个板块", flush=True)
    finally:
        await dispose_engines()

    partial_count = sum(
        1 for c, _v in best.items()
        if isinstance(completed.get(c), dict) and completed[c].get("partial")
    )
    print(json.dumps({
        "files": [p.name for p in files],
        "imported": imported,
        "skipped": skipped,
        "damaged": damaged,
        "boards_seen": len(best),
        "completed_total": len(completed),
        "partial": partial_count,
        "dry_run": dry_run,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="导入 THS 板块成分 JSON")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="忽略断点重导全部")
    parser.add_argument("--skip-partial", action="store_true",
                        help="跳过 pages>10 的 partial 名单（不用于市值聚合）")
    parser.add_argument("--name", action="append", default=[],
                        help="只导入指定板块名（可重复），避免触碰未列入目标的缓存")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.dry_run, args.force, args.skip_partial,
                                      args.name)))
