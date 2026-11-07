"""数据不变量只读检查（W0-3）。

输出 JSON，存在 violation 时退出码 1；warning 不影响退出码。
只读事务：脚本不会写入任何业务表或元数据。

用法（backend 目录）：

    ../.venv/Scripts/python.exe scripts/check_invariants.py
    ../.venv/Scripts/python.exe scripts/check_invariants.py > var/invariants.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text  # noqa: E402

from app.db.session import AsyncSessionLocal, dispose_engines  # noqa: E402
from app.quality.invariants import collect_invariants  # noqa: E402


async def amain(compact: bool = False) -> int:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            report = await collect_invariants(session)
            payload = report.as_dict()
            print(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=None if compact else 2,
                    separators=(",", ":") if compact else None,
                    default=str,
                ),
                flush=True,
            )
            return 0 if report.ok else 1
    finally:
        await dispose_engines()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GFNA 数据不变量只读检查")
    parser.add_argument(
        "--compact", action="store_true", help="单行 JSON（便于日志采集）"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(amain(args.compact))


if __name__ == "__main__":
    raise SystemExit(main())
