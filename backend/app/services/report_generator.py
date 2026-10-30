"""
AI 复盘日报（V5 重写）
=====================
方案 C6：**独立表 + 结构化输出（结论/依据/风险）+ 强制引用具体数字**。

V4 的三个问题在这里终结：
  - 报告被塞进"第一个板块的 ai_summary"（串数据）→ 现在写 `daily_report` 独立表
  - 读取 `limit(1)` 可能取到板块短评 → 现在按 `trade_date` 主键精确读
  - 输出是一大段 Markdown，无法校验是否引用数据 → 现在要求 JSON：结论/依据/风险分开
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import DEEPSEEK_MODEL
from app.core.logging import get_logger
from app.db.models import DailyReport
from app.services import llm_service

logger = get_logger(__name__)

SYSTEM_PROMPT = """你是 A 股量化分析师，为个人投资者撰写每日复盘。
硬性要求：
1. 每一个判断都必须引用输入数据里的具体数字（指数涨跌幅、板块评分、成交额、资金流向），不得凭空发挥；
2. 语气专业克制，不用"必将/肯定"这类词；
3. 区分【事实】与【推测】：推测必须写明依据；
4. 必须给出风险提示；
5. 只输出 JSON，不要任何额外文字。"""


def _fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def build_prompt(data: dict) -> str:
    """把 report_input 的结构化数据渲染成 prompt。数据缺失时明确写"缺失"而不是省略。"""
    lines: list[str] = [f"交易日：{data.get('trade_date')}", ""]

    lines.append("【指数】")
    indices = data.get("indices") or []
    if indices:
        for item in indices:
            lines.append(f"- {item.get('index_name')}: {_fmt(item.get('close'))} "
                         f"({_fmt(item.get('pct_chg'), 2, '%')})")
    else:
        lines.append("- 缺失")
    lines.append("")

    breadth = data.get("breadth") or {}
    lines.append("【全市场宽度】")
    if breadth:
        lines.append(f"- 上涨 {breadth.get('up', 'N/A')} 家 / 下跌 {breadth.get('down', 'N/A')} 家 / "
                     f"平盘 {breadth.get('flat', 'N/A')} 家")
        lines.append(f"- 涨停 {breadth.get('limit_up', 'N/A')} 家 / 跌停 {breadth.get('limit_down', 'N/A')} 家，"
                     f"中位涨跌幅 {_fmt(breadth.get('median_pct'), 2, '%')}")
    else:
        lines.append("- 缺失")
    lines.append("")

    for label, key in (("领涨板块", "top_sectors"), ("领跌板块", "bottom_sectors")):
        lines.append(f"【{label}】")
        items = data.get(key) or []
        if items:
            for i, s in enumerate(items[:8], 1):
                lines.append(
                    f"{i}. {s.get('sector_name')} 涨跌 {_fmt(s.get('pct_chg'), 2, '%')} | "
                    f"技术 {_fmt(s.get('technical_score'), 1)} 情绪 {_fmt(s.get('sentiment_score'), 1)} "
                    f"消息 {_fmt(s.get('news_score'), 1)} 综合 {_fmt(s.get('composite_score'), 1)}"
                    + (f" | 龙头 {s.get('leading_stock')}" if s.get("leading_stock") else "")
                )
        else:
            lines.append("- 缺失")
        lines.append("")

    margin = data.get("margin") or {}
    lines.append("【资金面】")
    if margin:
        lines.append(f"- 融资余额 {_fmt(margin.get('margin_balance'), 2)} 亿元，"
                     f"较前日变化 {_fmt(margin.get('margin_change'), 2)} 亿元")
        north = margin.get("northward_net")
        lines.append("- 北向资金：" + ("数据暂停披露（2024-08 起）" if north is None
                                       else f"净额 {_fmt(north, 2)} 亿元"))
    else:
        lines.append("- 缺失")
    lines.append("")

    news = data.get("news_sentiment") or {}
    lines.append("【新闻情绪】")
    if news and news.get("count"):
        lines.append(f"- 样本 {news.get('count')} 条，平均情感 {_fmt(news.get('mean'), 3)}（-1 极空 ~ +1 极多）")
        for title in (news.get("top_titles") or [])[:5]:
            lines.append(f"  · {title}")
    else:
        lines.append("- 无可用新闻样本（新闻链路为可选输入，缺失时不要编造消息面判断）")
    lines.append("")

    coverage = data.get("sector_scores_coverage") or {}
    lines.append("【数据完整度】")
    lines.append(f"- 已评分板块 {coverage.get('scored', 'N/A')} / 总计 {coverage.get('total', 'N/A')}")
    lines.append("")

    lines.append("请输出如下 JSON：")
    lines.append('{"summary_md": "Markdown 正文，包含## 今日综述 / ## 板块结构 / ## 资金面 / ## 风险提示",')
    lines.append(' "conclusions": [{"text": "结论", "evidence": "引用的具体数字"}],')
    lines.append(' "risks": ["风险 1", "风险 2"],')
    lines.append(' "data_gaps": ["缺失的数据项"]}')
    return "\n".join(lines)


def build_fallback_report(data: dict, reason: str = "") -> dict:
    """LLM 不可用时的降级报告：纯数据摘要，绝不编造观点。"""
    trade_date = data.get("trade_date")
    lines = [f"# {trade_date} 盘后数据摘要", ""]
    if reason:
        lines.append(f"> ⚠️ AI 日报不可用（{reason}），以下为基于本地数据的客观摘要。")
        lines.append("")

    lines.append("## 指数")
    indices = data.get("indices") or []
    if indices:
        for item in indices:
            lines.append(f"- {item.get('index_name')}: {_fmt(item.get('close'))} "
                         f"({_fmt(item.get('pct_chg'), 2, '%')})")
    else:
        lines.append("- 无数据")

    breadth = data.get("breadth") or {}
    if breadth:
        lines.append("")
        lines.append("## 市场宽度")
        lines.append(f"- 上涨 {breadth.get('up')} / 下跌 {breadth.get('down')} / "
                     f"涨停 {breadth.get('limit_up')} / 跌停 {breadth.get('limit_down')}")
        lines.append(f"- 中位涨跌幅 {_fmt(breadth.get('median_pct'), 2, '%')}")

    lines.append("")
    lines.append("## 领涨板块")
    top = data.get("top_sectors") or []
    if top:
        for s in top[:5]:
            lines.append(f"- {s.get('sector_name')}: {_fmt(s.get('pct_chg'), 2, '%')}，"
                         f"综合 {_fmt(s.get('composite_score'), 1)}/10")
    else:
        lines.append("- 无数据")

    lines.append("")
    lines.append("## 领跌板块")
    bottom = data.get("bottom_sectors") or []
    if bottom:
        for s in bottom[:5]:
            lines.append(f"- {s.get('sector_name')}: {_fmt(s.get('pct_chg'), 2, '%')}，"
                         f"综合 {_fmt(s.get('composite_score'), 1)}/10")
    else:
        lines.append("- 无数据")

    lines.append("")
    lines.append("---")
    lines.append("**回测 ≠ 实盘。本报告为数据摘要，不构成投资建议。**")

    return {
        "content": "\n".join(lines),
        "structured": {
            "conclusions": [],
            "risks": ["AI 日报未生成，缺少观点性判断"],
            "data_gaps": [reason] if reason else [],
        },
        "is_fallback": True,
    }


async def generate_report(trade_date: date, db: AsyncSession) -> dict:
    """生成并落库日报。返回契约 3.1 的 `report` 结构。"""
    from app.engine.report_input import build_daily_report_input

    data = await build_daily_report_input(trade_date)
    prompt = build_prompt(data)

    if not llm_service.available():
        result = build_fallback_report(data, reason="DeepSeek 未配置或不可用")
        model = None
    else:
        parsed = llm_service.chat_json(prompt, system=SYSTEM_PROMPT,
                                      max_tokens=3000, temperature=0.35)
        if parsed and parsed.get("summary_md"):
            result = {
                "content": str(parsed["summary_md"]),
                "structured": {
                    "conclusions": parsed.get("conclusions") or [],
                    "risks": parsed.get("risks") or [],
                    "data_gaps": parsed.get("data_gaps") or [],
                },
                "is_fallback": False,
            }
            model = DEEPSEEK_MODEL
        else:
            result = build_fallback_report(data, reason="LLM 返回无法解析")
            model = None

    if not (result.get("content") or "").strip():
        result = build_fallback_report(data, reason="LLM 返回空内容")
        model = None

    stmt = pg_insert(DailyReport).values(
        trade_date=trade_date,
        content=result["content"],
        structured=result["structured"],
        model=model,
        is_fallback=result["is_fallback"],
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[DailyReport.trade_date],
        set_={
            "content": result["content"],
            "structured": result["structured"],
            "model": model,
            "is_fallback": result["is_fallback"],
        },
    )
    await db.execute(stmt)
    await db.commit()

    if result["is_fallback"]:
        logger.warning("%s 日报为降级模板（%d 字）", trade_date, len(result["content"]))
    else:
        logger.info("%s 日报生成成功（%d 字）", trade_date, len(result["content"]))

    return {
        "trade_date": trade_date.isoformat(),
        "content": result["content"],
        "structured": result["structured"],
        "model": model,
        "is_fallback": result["is_fallback"],
    }


async def get_report(trade_date: date, db: AsyncSession) -> dict | None:
    row = (await db.execute(
        select(DailyReport).where(DailyReport.trade_date == trade_date)
    )).scalar_one_or_none()
    if row is None:
        return None
    return {
        "content": row.content,
        "structured": row.structured,
        "model": row.model,
        "is_fallback": bool(row.is_fallback),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


__all__ = ["generate_report", "get_report", "build_prompt", "build_fallback_report"]
