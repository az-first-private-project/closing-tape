#!/usr/bin/env python
"""用新浪板块成分补齐 THS 概念板块的成分（仅精确同名匹配）。

同花顺详情页按 IP 限流，q.10jqka.com.cn 长时间 403；新浪的板块接口可用，
且概念名称与 THS 有大量完全同名的情况。本脚本只导入**名称完全一致**的板块，
避免跨供应商口径串味；写入沿用 ``backfill_sector_ths.sync_members``
（SCD2、commit 成功后才推进断点）。

在 ``backend`` 目录执行::

    python scripts/import_sina_members.py --dry-run
    python scripts/import_sina_members.py --limit 20
    python scripts/import_sina_members.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import date
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]

from backfill_sector_ths import (  # noqa: E402
    PROGRESS_PATH, load_progress, save_progress, sync_members,
)

API = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Referer": "http://vip.stock.finance.sina.com.cn/mkt/",
}
NUM = 100
MAX_PAGES = 20
PAUSE_SEC = 0.35
EFFECTIVE_FROM = date(2022, 9, 1)


def _get_json(client, path: str, params: dict, attempts: int = 4):
    """新浪接口偶发 DNS 解析失败，做有限重试；最终解析为 JSON。"""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = client.get(API + path, params=params, headers=HEADERS, timeout=12)
            resp.raise_for_status()
            return json.loads(resp.text)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.6 * (attempt + 1))
    raise last  # type: ignore[misc]


def fetch_members(client, node: str) -> list[dict[str, str]] | None:
    """返回完整成分；页数达到上限仍未见尾页则返回 None（视为不完整）。"""
    members: dict[str, str] = {}
    for page in range(1, MAX_PAGES + 1):
        rows = _get_json(client, "Market_Center.getHQNodeData", {
            "page": page, "num": NUM, "sort": "symbol", "asc": 1,
            "node": node, "symbol": "", "_s_r_a": "page",
        })
        if not rows:
            return [{"stock_code": c, "stock_name": n} for c, n in sorted(members.items())]
        for row in rows:
            code = str(row.get("code") or "").strip()
            if len(code) == 6 and code.isdigit():
                members[code] = str(row.get("name") or "")
        if len(rows) < NUM:
            return [{"stock_code": c, "stock_name": n} for c, n in sorted(members.items())]
        time.sleep(PAUSE_SEC)
    return None


def build_node_map() -> dict[str, str]:
    """板块名 → 新浪 node，优先 gn_，其次 chgn_。"""
    nodes = json.loads((BACKEND_DIR / "var" / "sina_nodes.json").read_text(encoding="utf-8"))
    chosen: dict[str, str] = {}
    for name, _extra, node in nodes:
        if not name or not node:
            continue
        prefer_gn = node.startswith("gn_")
        if name not in chosen or (prefer_gn and not chosen[name].startswith("gn_")):
            chosen[name] = node
    return chosen


async def amain(args: argparse.Namespace) -> int:
    boards = json.loads(
        (BACKEND_DIR / "var" / "sector_board_list.json").read_text(encoding="utf-8")
    )
    # 未显式指定区间时沿用断点里的区间，避免误触发整份断点重置
    saved = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    start = date.fromisoformat(args.start or saved["range"]["start"])
    end = date.fromisoformat(args.end or saved["range"]["end"])
    progress = load_progress(start, end)
    completed = progress["members"]["completed"]
    failed = progress["members"]["failed"]

    node_map = build_node_map()
    candidates = [
        b for b in boards
        if b.get("sector_type") == "concept"
        and b["sector_code"] not in completed
        and b["sector_name"] in node_map
    ]
    candidates.sort(key=lambda b: b["sector_name"])
    if args.name:
        candidates = [b for b in candidates if b["sector_name"] in set(args.name)]
    if args.limit:
        candidates = candidates[: args.limit]

    print(f"[sina] 待导入 {len(candidates)} 个概念板块（精确同名）", flush=True)
    if args.dry_run:
        for b in candidates:
            print(f"  {b['sector_name']} -> {node_map[b['sector_name']]}")
        return 0

    imported = skipped = 0
    with httpx.Client() as client:
        for index, board in enumerate(candidates, 1):
            name, code = board["sector_name"], board["sector_code"]
            node = node_map[name]
            try:
                members = await asyncio.to_thread(fetch_members, client, node)
            except Exception as exc:  # noqa: BLE001
                failed[code] = {"name": name, "node": node,
                                "error": f"{type(exc).__name__}: {exc}"[:160]}
                print(f"[{index}/{len(candidates)}] ! {name} 抓取失败：{exc}", flush=True)
                save_progress(progress)
                continue

            if not members:
                skipped += 1
                print(f"[{index}/{len(candidates)}] - {name} 无成分或翻页不完整，跳过", flush=True)
                continue

            stats = await sync_members(code, name, "concept", members, EFFECTIVE_FROM)
            completed[code] = {"count": len(members), "source": "sina",
                               "partial": False, "node": node}
            failed.pop(code, None)
            imported += 1
            save_progress(progress)
            print(f"[{index}/{len(candidates)}] + {name} {len(members)} 只"
                  f"（新增 {stats['added']}，关闭 {stats['closed']}）", flush=True)
            await asyncio.sleep(PAUSE_SEC)

    print(f"[sina] 完成：导入 {imported}，跳过 {skipped}，失败 {len(failed)}；"
          f"断点已更新 {PROGRESS_PATH.name}", flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="新浪概念成分补全（精确同名）")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--name", nargs="*", default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain(parse_args())))
