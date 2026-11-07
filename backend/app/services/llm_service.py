"""
DeepSeek 服务（V5 重构）
========================
三件事：
  1. `generate_summary`     英文新闻 → 中文摘要（保留 V2 行为）
  2. `judge_sentiment`      新闻 → 情感分 -1..+1（V5 新增：新闻终于真正进入情绪因子）
  3. `chat_json` / `chat_text`  通用调用，供 AI 日报使用

V5 修复的坑：
  - **客户端懒加载**：V2 在模块导入时 `OpenAI(api_key="")` 会直接抛异常，导致没配 Key 时整个后端起不来
  - **JSON 模式**：日报要求结构化输出，用 `response_format={"type":"json_object"}` 降低解析失败率
  - **失败降级**：任何 LLM 故障都返回 None/空串，绝不让主链路 500（方案：AI 是增强不是依赖）
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from app.core.config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, LLM_ENABLED,
    LLM_MAX_RETRIES, LLM_MAX_TOKENS, LLM_TEMPERATURE,
)
from app.core.errors import ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

_client = None


def _clean_no_proxy(raw: str) -> str:
    """去掉 NO_PROXY 中的空项、重复项与 IPv6 方括号。

    httpx 会把每个例外项当 URL 解析，``[::1]`` 在 ``normalize_port`` 中报
    ``Invalid port: ':1]'``；``::1`` 才是它接受的无括号写法。
    """
    seen: set[str] = set()
    items: list[str] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        if item.startswith("[") and item.endswith("]") and ":" in item:
            item = item[1:-1]
        if not item or item in seen:
            continue
        seen.add(item)
        items.append(item)
    return ",".join(items)


def sanitize_proxy_env() -> bool:
    """清洗进程内的 NO_PROXY/no_proxy，返回是否发生修改。

    Windows 默认环境常见 ``[::1]`` 与 ``::1`` 并存；不清洗会让 OpenAI/httpx
    客户端初始化失败，AI 日报静默降级为模板。启动时与客户端懒加载前都会调用。
    """
    changed = False
    for name in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(name)
        if not raw:
            continue
        cleaned = _clean_no_proxy(raw)
        if cleaned != raw:
            os.environ[name] = cleaned
            changed = True
    if changed:
        logger.warning(
            "NO_PROXY 含 httpx 无法解析的方括号 IPv6 项，已清洗为: %s",
            os.environ.get("NO_PROXY"),
        )
    return changed


def get_client():
    """懒加载 OpenAI 兼容客户端。未配置 Key 时返回 None（调用方必须处理）。"""
    global _client
    if _client is None:
        sanitize_proxy_env()
        if not DEEPSEEK_API_KEY:
            return None
        try:
            from openai import OpenAI
            _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL,
                             timeout=60.0, max_retries=0)
        except Exception as exc:                       # noqa: BLE001
            logger.error("初始化 DeepSeek 客户端失败: %s", exc)
            return None
    return _client


def available() -> bool:
    return bool(LLM_ENABLED and DEEPSEEK_API_KEY and get_client() is not None)


# ============================================================
# 通用调用
# ============================================================

def _call(messages: list[dict], *, max_tokens: int, temperature: float,
          json_mode: bool = False) -> str | None:
    client = get_client()
    if client is None:
        logger.warning("DeepSeek 未配置，跳过调用")
        return None

    kwargs: dict[str, Any] = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_error = None
    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content
            if content and content.strip():
                return content.strip()
            logger.warning("DeepSeek 返回空内容（第 %d 次）", attempt)
        except Exception as exc:                       # noqa: BLE001
            last_error = exc
            logger.warning("DeepSeek 调用失败（第 %d/%d 次）: %s",
                           attempt, LLM_MAX_RETRIES, exc)
            if attempt < LLM_MAX_RETRIES:
                time.sleep(2 ** attempt)
    logger.error("DeepSeek 调用全部失败，最后错误: %s", last_error)
    return None


def chat_text(prompt: str, *, system: str = "", max_tokens: int = 2000,
              temperature: float = 0.3) -> str | None:
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": prompt})
    return _call(messages, max_tokens=max_tokens, temperature=temperature)


def chat_json(prompt: str, *, system: str = "", max_tokens: int = 2000,
              temperature: float = 0.2) -> dict | None:
    """要求模型输出 JSON 对象并解析。失败返回 None。"""
    messages = [{"role": "system", "content": system or "你是专业分析师，只输出 JSON。"}]
    messages.append({"role": "user", "content": prompt})
    raw = _call(messages, max_tokens=max_tokens, temperature=temperature, json_mode=True)
    if raw is None:
        return None
    return _parse_json(raw)


def _parse_json(raw: str) -> dict | None:
    text = raw.strip()
    # 兼容 ```json 包裹
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            logger.warning("无法解析为 JSON: %s", raw[:200])
            return None
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            logger.warning("JSON 二次解析失败: %s", raw[:200])
            return None
    return data if isinstance(data, dict) else None


# ============================================================
# 新闻摘要（保留 V2 能力）
# ============================================================

SUMMARY_SYSTEM_PROMPT = (
    "你是一位专业财经新闻编辑，拥有 20 年金融分析经验。"
    "把英文财经新闻提炼成简洁、客观、专业的中文摘要："
    "1) 提取最重要的 3–5 个核心观点；2) 保留关键数字（涨跌幅、金额、指数点位）；"
    "3) 语言简练、不用口语；4) 保持中立、不添加主观判断；5) 200 字以内。"
)


def generate_summary(text: str, max_retries: int | None = None) -> str:
    """英文新闻 → 200 字中文摘要。失败返回空串（不抛异常）。"""
    if not text or len(text.strip()) < 50:
        return ""
    result = chat_text(
        f"请将以下财经新闻总结为中文，200 字以内：\n\n{text[:6000]}",
        system=SUMMARY_SYSTEM_PROMPT,
        max_tokens=LLM_MAX_TOKENS,
        temperature=LLM_TEMPERATURE,
    )
    return result or ""


def generate_batch_summaries(articles: list, batch_size: int = 5) -> dict:
    results: dict[int, str] = {}
    for i, article in enumerate(articles, 1):
        content = getattr(article, "content", None)
        results[article.id] = generate_summary(content) if content else ""
        if i % batch_size == 0:
            time.sleep(1)
    return results


# ============================================================
# 新闻情感（V5 新增：情绪因子的真实输入）
# ============================================================

SENTIMENT_SYSTEM_PROMPT = (
    "你是 A 股量化分析师。判断每条财经新闻对 A 股市场/相关板块的利好利空程度，"
    "只输出 JSON 对象，字段为 items 数组，每项含 index(整数)、score(-1..1 小数)、label(利好/利空/中性)。"
)


def judge_sentiment_batch(articles: list[dict]) -> list[float | None]:
    """批量判定新闻情感。

    articles: [{"title": str, "summary": str|None}, ...]
    返回与输入等长的 -1..1 列表（失败项为 None）。
    """
    if not articles:
        return []
    if not available():
        return [None] * len(articles)

    lines = []
    for i, item in enumerate(articles, 1):
        summary = (item.get("summary") or "")[:120]
        lines.append(f"[{i}] {item.get('title', '')}\n    摘要: {summary}")

    prompt = (
        "请判断下列财经新闻对 A 股整体或相关板块的情感倾向。\n\n"
        + "\n".join(lines)
        + "\n\n输出 JSON：{\"items\":[{\"index\":1,\"score\":0.6,\"label\":\"利好\"}]}"
        "\nscore: -1(严重利空) ~ +1(重大利好)，0 为中性。index 必须与输入编号一致。"
    )
    data = chat_json(prompt, system=SENTIMENT_SYSTEM_PROMPT, max_tokens=800,
                     temperature=0.1)
    if not data:
        return [None] * len(articles)

    scores: list[float | None] = [None] * len(articles)
    for item in data.get("items", []):
        try:
            idx = int(item.get("index")) - 1
            score = float(item.get("score"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(scores):
            scores[idx] = max(-1.0, min(1.0, score))
    return scores


def judge_sentiment(title: str, summary: str | None = None) -> float | None:
    """单条新闻情感。"""
    result = judge_sentiment_batch([{"title": title, "summary": summary}])
    return result[0] if result else None


__all__ = [
    "available", "get_client", "chat_text", "chat_json", "generate_summary",
    "generate_batch_summaries", "judge_sentiment", "judge_sentiment_batch",
    "sanitize_proxy_env", "ErrorCode",
]
