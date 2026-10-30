"""国内财经新闻采集（打开网站自动触发 + 定时任务共用）。

来源（均为公开 JSON 接口，无需登录）：
  - 新浪财经滚动
  - 同花顺快讯
  - 东方财富财经导读
  - 华尔街见闻 7x24

与旧 Scrapy 爬虫的区别：直接取各家 JSON 快讯流，不抓详情页，
单轮 4 个来源并行、10 秒级完成；正文/摘要使用接口自带 digest，
``url_hash`` 唯一约束保证重复打开网站不会产生重复文章。

入库限量（config）：单轮最多新增 ``NEWS_MAX_NEW_PER_RUN`` 条（取最新的），
库内最多保留 ``NEWS_MAX_ARTICLES`` 条，超出部分按 ``publish_time`` 从旧到新淘汰。
"""
from __future__ import annotations

import asyncio
import html
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core import config
from app.core.logging import get_logger
from app.db.models import NewsArticle, NewsSource

logger = get_logger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA, "Accept": "application/json, text/plain, */*"}
_TZ_CN = timezone(timedelta(hours=8))
_MAX_ITEMS = 50
_MAX_CONTENT = 5000
_MAX_SUMMARY = 300

AUTO_THROTTLE_SEC = 600  # 同一服务端 10 分钟内只跑一轮

# 运行态（单进程；多 worker 时靠 crawl_logs 的 running 记录兜底）
_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "last_result": None,
    "last_error": None,
}


# =====================================================================
# 文本 / 时间工具
# =====================================================================

def _clean(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _epoch(value: Any) -> datetime | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1e12:  # 毫秒
        number /= 1000
    if number <= 0:
        return None
    return datetime.fromtimestamp(number, tz=timezone.utc)


def _datetime_cn(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=_TZ_CN)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_TZ_CN)


# =====================================================================
# 各来源抓取（同步，在线程中执行）
# =====================================================================

def _get_json(url: str, params: dict | None = None, *, referer: str | None = None,
              attempts: int = 3) -> Any:
    headers = dict(_HEADERS)
    if referer:
        headers["Referer"] = referer
    last: Exception | None = None
    for n in range(attempts):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 —— DNS/连接抖动重试
            last = exc
            time.sleep(0.8 * (n + 1))
    raise RuntimeError(f"{url} 抓取失败：{last}")


def _fetch_sina() -> list[dict]:
    payload = _get_json(
        "https://feed.mix.sina.com.cn/api/roll/get",
        {"pageid": "153", "lid": "2516", "num": str(_MAX_ITEMS), "page": "1"},
        referer="https://finance.sina.com.cn/",
    )
    items = ((payload.get("result") or {}).get("data")) or []
    out = []
    for item in items:
        title = _clean(item.get("title"))
        url = str(item.get("url") or "").strip()
        text = _clean(item.get("intro")) or title
        if not title or not url:
            continue
        out.append({
            "title": title,
            "url": url,
            "text": text,
            "author": _clean(item.get("media_name")) or None,
            "publish_time": _epoch(item.get("ctime")),
        })
    return out


def _fetch_ths() -> list[dict]:
    payload = _get_json(
        "https://news.10jqka.com.cn/tapp/news/push/stock/",
        {"page": "1", "tag": "", "track": "website", "pagesize": str(_MAX_ITEMS)},
        referer="https://news.10jqka.com.cn/",
    )
    items = ((payload.get("data") or {}).get("list")) or []
    out = []
    for item in items:
        title = _clean(item.get("title"))
        url = str(item.get("url") or item.get("importUrl") or "").strip()
        text = _clean(item.get("digest")) or title
        if not title or not url:
            continue
        out.append({
            "title": title,
            "url": url,
            "text": text,
            "author": _clean(item.get("author")) or None,
            "publish_time": _epoch(item.get("ctime")) or _datetime_cn(item.get("rtime")),
        })
    return out


def _fetch_eastmoney() -> list[dict]:
    payload = _get_json(
        "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns",
        {
            "client": "web", "biz": "web_news_col", "column": "350",
            "order": "1", "needInteractData": "0",
            "page_size": str(_MAX_ITEMS), "req_trace": "1",
            "fields": "code,showTime,title,url,digest",
        },
        referer="https://finance.eastmoney.com/",
    )
    items = ((payload.get("data") or {}).get("list")) or []
    out = []
    for item in items:
        title = _clean(item.get("title"))
        url = str(item.get("url") or "").strip()
        text = _clean(item.get("digest")) or title
        if not title or not url:
            continue
        out.append({
            "title": title,
            "url": url,
            "text": text,
            "author": None,
            "publish_time": _datetime_cn(item.get("showTime")),
        })
    return out


def _fetch_wallstreetcn() -> list[dict]:
    payload = _get_json(
        "https://api-one.wallstcn.com/apiv1/content/lives",
        {"channel": "global-channel", "limit": str(_MAX_ITEMS)},
        referer="https://wallstreetcn.com/livenews",
    )
    items = ((payload.get("data") or {}).get("items")) or []
    out = []
    for item in items:
        text = _clean(item.get("content_text") or item.get("content"))
        title = _clean(item.get("title")) or text[:60]
        url = str(item.get("uri") or "").strip()
        if not title or not url:
            continue
        author = _clean(((item.get("author") or {}).get("display_name"))) or None
        out.append({
            "title": title,
            "url": url,
            "text": text or title,
            "author": author,
            "publish_time": _epoch(item.get("display_time")),
        })
    return out


SOURCES: list[dict[str, Any]] = [
    {"name": "新浪财经", "base_url": "https://finance.sina.com.cn", "fetch": _fetch_sina},
    {"name": "同花顺", "base_url": "https://news.10jqka.com.cn", "fetch": _fetch_ths},
    {"name": "东方财富", "base_url": "https://finance.eastmoney.com", "fetch": _fetch_eastmoney},
    {"name": "华尔街见闻", "base_url": "https://wallstreetcn.com", "fetch": _fetch_wallstreetcn},
]


# =====================================================================
# 入库
# =====================================================================

async def _ensure_sources(session) -> dict[str, int]:
    rows = (await session.execute(select(NewsSource))).scalars().all()
    by_name = {row.name: row.id for row in rows}
    for spec in SOURCES:
        if spec["name"] in by_name:
            continue
        source = NewsSource(
            name=spec["name"],
            base_url=spec["base_url"],
            country="CN",
            language="zh",
            is_active=1,
        )
        session.add(source)
        await session.flush()
        by_name[spec["name"]] = source.id
    return by_name


def _to_row(item: dict, source_id: int, crawl_time: datetime) -> dict | None:
    url = item["url"][:2048]
    title = item["title"][:500]
    if not url or not title:
        return None
    text = (item.get("text") or title)[:_MAX_CONTENT]
    return {
        "source_id": source_id,
        "title": title,
        "url": url,
        "url_hash": NewsArticle.hash_url(url),
        "content": text,
        "summary": text[:_MAX_SUMMARY],
        "author": (str(item.get("author") or "")[:200] or None),
        "publish_time": item.get("publish_time") or crawl_time,
        "crawl_time": crawl_time,
        "is_processed": 1,
    }


async def collect_news(session) -> dict:
    """抓取四个来源并写库，单轮/总量双重限量。返回 {"found","new","trimmed","sources"}。"""
    started = time.perf_counter()
    results = await asyncio.gather(
        *(asyncio.to_thread(spec["fetch"]) for spec in SOURCES),
        return_exceptions=True,
    )

    crawl_time = datetime.now(timezone.utc)
    source_ids = await _ensure_sources(session)
    report: dict[str, dict] = {}
    candidates: dict[str, dict] = {}

    for spec, result in zip(SOURCES, results):
        name = spec["name"]
        if isinstance(result, Exception):
            report[name] = {"found": 0, "new": 0, "error": f"{type(result).__name__}: {result}"[:300]}
            logger.warning("新闻来源 %s 抓取失败：%s", name, result)
            continue
        unique: dict[str, dict] = {}
        for item in result:
            row = _to_row(item, source_ids[name], crawl_time)
            if row:
                unique.setdefault(row["url_hash"], row)
        for url_hash, row in unique.items():
            candidates.setdefault(url_hash, row)
        report[name] = {"found": len(unique), "new": 0, "error": None}

    # 只取库里没有的，按发布时间从新到旧截断到单轮上限
    existing: set[str] = set()
    if candidates:
        existing = set((await session.execute(
            select(NewsArticle.url_hash).where(NewsArticle.url_hash.in_(list(candidates)))
        )).scalars().all())
    fresh = [row for hashed, row in candidates.items() if hashed not in existing]
    fresh.sort(key=lambda row: row["publish_time"], reverse=True)
    rows = fresh[: config.NEWS_MAX_NEW_PER_RUN]

    trimmed = 0
    if rows:
        statement = (
            pg_insert(NewsArticle)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["url_hash"])
            .returning(NewsArticle.id, NewsArticle.source_id)
        )
        inserted = (await session.execute(statement)).all()
        id_to_name = {value: key for key, value in source_ids.items()}
        for _, source_id in inserted:
            name = id_to_name.get(source_id)
            if name:
                report[name]["new"] += 1

    # 栈式保留：超出上限时删掉最旧的，只留最新 NEWS_MAX_ARTICLES 条
    await session.flush()
    trimmed = (await session.execute(
        text(
            """
            DELETE FROM news_articles
            WHERE id IN (
                SELECT id FROM news_articles
                ORDER BY publish_time DESC NULLS LAST, id DESC
                OFFSET :keep
            )
            """
        ),
        {"keep": config.NEWS_MAX_ARTICLES},
    )).rowcount or 0
    await session.commit()

    total_new = sum(item["new"] for item in report.values())
    total_found = sum(item["found"] for item in report.values())
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    summary = {
        "found": total_found,
        "new": total_new,
        "trimmed": trimmed,
        "sources": report,
        "elapsed_ms": elapsed_ms,
    }
    logger.info(
        "新闻采集完成：发现 %d 条，新增 %d 条，淘汰 %d 条，%dms",
        total_found, total_new, trimmed, elapsed_ms,
    )
    return summary


# =====================================================================
# 自动触发状态（给 /crawl/auto 用）
# =====================================================================

def snapshot() -> dict:
    now = datetime.now(timezone.utc)
    running = bool(_state["running"])
    started = _state["started_at"]
    finished = _state["finished_at"]
    cooldown = 0
    if started is not None:
        elapsed = (now - started).total_seconds()
        cooldown = max(0, int(AUTO_THROTTLE_SEC - elapsed))
    return {
        "running": running,
        "started_at": started.isoformat() if started else None,
        "finished_at": finished.isoformat() if finished else None,
        "cooldown": cooldown,
        "last_result": _state["last_result"],
        "last_error": _state["last_error"],
    }


def cooldown_remaining() -> int:
    started = _state["started_at"]
    if started is None:
        return 0
    return max(0, int(AUTO_THROTTLE_SEC - (datetime.now(timezone.utc) - started).total_seconds()))


def mark_started() -> None:
    _state.update({
        "running": True,
        "started_at": datetime.now(timezone.utc),
        "last_error": None,
    })


async def run_auto_collect() -> None:
    """BackgroundTasks 入口：自开 session，成功/失败都落 crawl_logs。"""
    from app.db.models import CrawlLog
    from app.db.session import AsyncSessionLocal

    error: str | None = None
    result: dict | None = None
    try:
        async with AsyncSessionLocal() as session:
            result = await collect_news(session)
            session.add(CrawlLog(
                status="success",
                articles_found=result["found"],
                articles_new=result["new"],
                finished_at=datetime.now(timezone.utc),
            ))
            await session.commit()
    except Exception as exc:  # noqa: BLE001 —— 失败只影响本轮新闻
        error = f"{type(exc).__name__}: {exc}"[:500]
        logger.exception("自动新闻采集失败")
        try:
            async with AsyncSessionLocal() as session:
                session.add(CrawlLog(
                    status="failed",
                    error_message=error,
                    finished_at=datetime.now(timezone.utc),
                ))
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("写 crawl_logs 失败")
    finally:
        _state.update({
            "running": False,
            "finished_at": datetime.now(timezone.utc),
            "last_result": result,
            "last_error": error,
        })


__all__ = [
    "AUTO_THROTTLE_SEC",
    "collect_news",
    "run_auto_collect",
    "snapshot",
    "cooldown_remaining",
    "mark_started",
]
