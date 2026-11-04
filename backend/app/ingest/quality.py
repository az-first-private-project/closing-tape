"""
数据质量校验（方案 A9）
========================
采集进来的数据不能直接信任：AKShare 依赖东财/新浪页面结构，字段缺失、重复行、
涨跌幅越界、价格跳变都可能发生。本模块负责在**落库前**做体检，并把体检结果写进
``meta_data_quality``（前端数据新鲜度徽标的唯一来源，方案 E2）。

设计原则：
    1. **阈值全部来自 ``app.core.config.QUALITY_*``**，不散落魔法数字；
    2. 区分 ``error`` 与 ``warning``：error 会阻断本次写入（``QualityBlockedError``），
       warning 只记录进 ``anomalies``（例如个股首日上市涨幅超 30%，属于真实数据）；
    3. 校验函数是**纯函数**（输入 DataFrame，输出 QualityReport），方便单测、不联网；
    4. 写库只走 ``record_quality``，且失败时**不清空上一次成功时间**
       （前端要能区分"上次成功"与"这次失败"）。

数据集命名与 ``app/pipeline.py`` 的 ``TASK_DATASET`` 对齐，避免新鲜度出现两条重复记录。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Sequence

import pandas as pd

from app.core import config
from app.core.logging import get_logger
from app.ingest.base import upsert_rows

logger = get_logger(__name__)

# 数据集 -> 中文展示名（前端新鲜度徽标直接展示，A10/E2）
DATASET_LABELS: dict[str, str] = {
    "stock_universe": "股票池",
    "stock_daily": "个股日线",
    "sector_daily": "板块日线",
    "sector_members": "板块成分",
    "moneyflow": "个股资金流",
    "valuation": "历史估值(PE/PB/股息率)",
    "margin": "两融与北向",
    "index_daily": "指数日线",
    "trade_calendar": "交易日历",
    "adj_factor_check": "复权因子交叉校验",
    "daily_ingest": "每日采集编排",
}

# 个股日线必需列（入库前质量校验用）
DAILY_REQUIRED_COLUMNS: tuple[str, ...] = (
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
)


# ============================================================
# 报告结构
# ============================================================
@dataclass
class QualityReport:
    """一次数据集质量体检的结果。

    Attributes:
        dataset: 数据集名（与 :data:`DATASET_LABELS` / pipeline 的 dataset 对齐）。
        rows: 实际行数。
        expected: 期望行数（用于覆盖率）；None 表示无法估计。
        coverage: 覆盖率 0–1（``rows / expected``，上限 1）。
        anomalies: 异常明细，每项形如
            ``{"type": str, "count": int, "severity": "error"|"warning",
               "message": str, "samples": list}``。
        ok: 是否通过。任何 ``severity="error"`` 的异常都会把它置 False。
    """

    dataset: str
    rows: int = 0
    expected: int | None = None
    coverage: float = 0.0
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = True

    # ---------- 构造辅助 ----------
    def add(
        self,
        kind: str,
        *,
        count: int = 1,
        severity: str = "warning",
        message: str = "",
        samples: Sequence[Any] | None = None,
    ) -> None:
        """追加一条异常。``severity="error"`` 会把 ``ok`` 置为 False。"""
        self.anomalies.append(
            {
                "type": kind,
                "count": int(count),
                "severity": severity,
                "message": message,
                "samples": list(samples or []),
            }
        )
        if severity == "error":
            self.ok = False

    # ---------- 输出 ----------
    def error_text(self) -> str | None:
        """把 error 级异常拼成一段可读文本，写入 ``meta_data_quality.error``。"""
        errors = [item for item in self.anomalies if item.get("severity") == "error"]
        if not errors:
            return None
        parts = [
            f"{item['type']}({item['count']}): {item.get('message', '')}".strip()
            for item in errors
        ]
        return "; ".join(parts)[:2000]

    def to_detail(self) -> dict[str, Any]:
        """JSON 可序列化的详情（存 ``meta_data_quality.detail``）。"""
        return {
            "rows": self.rows,
            "expected": self.expected,
            "coverage": self.coverage,
            "ok": self.ok,
            "anomalies": self.anomalies[:20],
        }


def make_error_report(dataset: str, error: str, *, rows: int = 0) -> QualityReport:
    """为"采集直接抛异常"的场景构造一个 error 报告（provider_error）。"""
    report = QualityReport(dataset=dataset, rows=rows, expected=None, coverage=0.0)
    report.add("provider_error", severity="error", message=error)
    return report


# ============================================================
# 通用小工具
# ============================================================
def _num_series(df: pd.DataFrame, column: str) -> pd.Series | None:
    """安全取数值列（不存在返回 None，无法转数值的变 NaN）。"""
    if column not in df.columns:
        return None
    return pd.to_numeric(df[column], errors="coerce")


def _missing_ratio(df: pd.DataFrame, column: str) -> float:
    """某列缺失率；列不存在视为 100% 缺失。"""
    if column not in df.columns:
        return 1.0
    if len(df) == 0:
        return 1.0
    return float(df[column].isna().mean())


def _traded_mask(df: pd.DataFrame) -> pd.Series:
    """当日有成交痕迹（成交量>0 或成交额>0）的行。

    东财快照榜单会混入停牌/退市整理标的：它们有昨收、但最新价/成交量/成交额全空。
    这类行不属于"数据缺失"，必须与"有成交却缺价格"的源故障区分开。
    """
    volume = _num_series(df, "volume")
    amount = _num_series(df, "amount")
    if volume is None and amount is None:
        return pd.Series(False, index=df.index)
    traded = pd.Series(False, index=df.index)
    if volume is not None:
        traded |= volume.fillna(0) > 0
    if amount is not None:
        traded |= amount.fillna(0) > 0
    return traded


def _suspended_mask(df: pd.DataFrame) -> pd.Series:
    """停牌判定：优先用 is_suspended 列；否则用 volume<=0 兜底。

    停牌日价格不动，因此"价格跳变"检查必须排除停牌，否则大量正常停牌会被误报。
    """
    if "is_suspended" in df.columns:
        return df["is_suspended"].fillna(False).astype(bool)
    volume = _num_series(df, "volume")
    if volume is None:
        return pd.Series(False, index=df.index)
    return volume.fillna(0) <= 0


def _samples(df: pd.DataFrame, mask: pd.Series, code_column: str = "code",
             limit: int = 5) -> list[Any]:
    """从命中掩码的行里取少量样本（优先代码），便于人工排查。"""
    try:
        hit = df.loc[mask]
    except (KeyError, IndexError):  # pragma: no cover —— 防御异常索引
        return []
    if hit.empty:
        return []
    if code_column in hit.columns:
        values = hit[code_column].head(limit).tolist()
    else:
        values = hit.index[:limit].tolist()
    return [str(v) for v in values]


# ============================================================
# A2 全市场快照校验
# ============================================================
def check_snapshot(
    df: pd.DataFrame | None,
    expect_rows: int | None = None,
    cfg: Any = None,
) -> QualityReport:
    """全市场当日快照质量校验（方案 A9）。

    检查项：
        1. 有效行数（有收盘价）是否达到 ``QUALITY_MIN_SNAPSHOT_ROWS``；
        2. 覆盖率 ``有效行 / expected`` 是否达到 ``QUALITY_MIN_COVERAGE``；
           停牌/退市整理行（无最新价且无成交）不计入有效行；
        3. 关键列（code/close/pre_close）缺失率是否超过 ``QUALITY_MAX_MISSING_RATIO``；
           缺失率只统计"有成交"的行，"有成交却缺价格"才算源故障；
        4. ``|pct_chg|`` 是否超过 ``QUALITY_MAX_ABS_PCT_CHG``（北交所 30% 涨跌停）；
           个股上市首日无涨跌停限制，属于**真实数据**，因此记为 warning 不阻断；
        5. 价格跳变：``|close/pre_close - 1| > QUALITY_PRICE_JUMP_RATIO`` 且非停牌（warning，
           除权除息日会真实发生，不能阻断，但必须留下记录人工核对）；
        6. 重复代码（error，重复行会破坏主键与统计口径）。
    """
    cfg = cfg or config
    min_rows = int(getattr(cfg, "QUALITY_MIN_SNAPSHOT_ROWS", 3500))
    min_coverage = float(getattr(cfg, "QUALITY_MIN_COVERAGE", 0.95))
    max_missing = float(getattr(cfg, "QUALITY_MAX_MISSING_RATIO", 0.05))
    max_pct = float(getattr(cfg, "QUALITY_MAX_ABS_PCT_CHG", 30.0))
    jump_ratio = float(getattr(cfg, "QUALITY_PRICE_JUMP_RATIO", 0.5))

    expected = max(int(expect_rows or 0), min_rows)

    if df is None or df.empty:
        report = QualityReport(
            dataset="stock_daily", rows=0, expected=expected, coverage=0.0
        )
        report.add("empty_frame", severity="error", message="全市场快照为空")
        return report

    # 覆盖率只统计"有当日收盘价"的有效行：停牌/退市整理标的没有当日行情，
    # 若按原始行数计覆盖率，源混入的退市股越多反而越"达标"。
    close_series = _num_series(df, "close")
    usable_mask = (
        close_series.notna() if close_series is not None
        else pd.Series(True, index=df.index)
    )
    traded_mask = _traded_mask(df)
    rows = int(usable_mask.sum())
    coverage = min(rows / expected, 1.0) if expected > 0 else 0.0
    report = QualityReport(
        dataset="stock_daily",
        rows=rows,
        expected=expected,
        coverage=round(coverage, 4),
    )

    if rows < min_rows:
        report.add(
            "row_count_below_threshold",
            count=rows,
            severity="error",
            message=f"快照有效行 {rows} < 阈值 {min_rows} 行",
        )
    if coverage < min_coverage:
        report.add(
            "coverage_below_threshold",
            count=rows,
            severity="error",
            message=f"覆盖率 {coverage:.2%} < 阈值 {min_coverage:.0%}（期望 {expected} 行）",
        )
    suspended_count = int((~traded_mask).sum())
    if suspended_count:
        report.add(
            "suspended_rows",
            count=suspended_count,
            severity="warning",
            message=f"{suspended_count} 行无成交（停牌/退市整理），不计入覆盖率与缺失率",
            samples=_samples(df, ~traded_mask),
        )

    # 3. 关键列缺失率；主键列缺失直接 error
    if "code" not in df.columns:
        report.add("missing_column", severity="error", message="快照缺少 code 列")
    else:
        missing_code = int(df["code"].isna().sum())
        if missing_code:
            report.add(
                "missing_key_column",
                count=missing_code,
                severity="error",
                message=f"code 缺失 {missing_code} 行",
            )
        duplicated = int(df["code"].duplicated().sum())
        if duplicated:
            dup_samples = _samples(df, df["code"].duplicated(keep=False))
            report.add(
                "duplicate_rows",
                count=duplicated,
                severity="error",
                message=f"快照存在 {duplicated} 行重复代码",
                samples=dup_samples,
            )

    for column in ("close", "pre_close"):
        # 缺失率只在"确有成交"的行里统计：停牌/退市整理行的空值是正常表现，
        # 而有成交却缺价格才是需要阻断的源故障。
        if column not in df.columns:
            ratio, missing = 1.0, int(traded_mask.sum())
        elif bool(traded_mask.any()):
            ratio = float(df.loc[traded_mask, column].isna().mean())
            missing = int(df.loc[traded_mask, column].isna().sum())
        else:
            ratio, missing = 1.0, 0
        if ratio > max_missing:
            report.add(
                "missing_ratio_exceeded",
                count=missing,
                severity="error",
                message=f"有成交行 {column} 缺失率 {ratio:.2%} > 阈值 {max_missing:.0%}",
            )

    # 4. 涨跌幅越界
    pct = _num_series(df, "pct_chg")
    if pct is not None:
        bad = pct.abs() > max_pct
        bad_count = int(bad.fillna(False).sum())
        if bad_count:
            report.add(
                "pct_chg_out_of_range",
                count=bad_count,
                severity="warning",
                message=f"{bad_count} 行 |涨跌幅| > {max_pct:.0f}%（可能为首日上市/除权，需人工核对）",
                samples=_samples(df, bad.fillna(False)),
            )

    # 5. 价格跳变（相对昨收）
    close = _num_series(df, "close")
    pre_close = _num_series(df, "pre_close")
    if close is not None and pre_close is not None:
        valid = close.notna() & pre_close.notna() & (pre_close > 0)
        change = (close / pre_close - 1).where(valid)
        jumped = change.abs() > jump_ratio
        jumped = jumped.fillna(False) & ~_suspended_mask(df)
        jump_count = int(jumped.sum())
        if jump_count:
            report.add(
                "price_jump",
                count=jump_count,
                severity="warning",
                message=f"{jump_count} 行相邻日价格变动 > {jump_ratio:.0%}（可能是除权除息，需核对 adj_factor）",
                samples=_samples(df, jumped),
            )

    # 非正价格（停牌/异常数据）
    if close is not None:
        non_positive = int((close.notna() & (close <= 0)).sum())
        if non_positive:
            report.add(
                "non_positive_price",
                count=non_positive,
                severity="warning",
                message=f"{non_positive} 行收盘价 <= 0",
            )

    if report.ok:
        logger.info("快照质量校验通过 rows=%d coverage=%.2f%%", rows, coverage * 100)
    else:
        logger.error("快照质量校验未通过：%s", report.error_text())
    return report


# ============================================================
# A3 个股历史日线校验
# ============================================================
def check_daily_frame(
    df: pd.DataFrame | None,
    expected_dates: Sequence[date] | None = None,
    *,
    dataset: str = "stock_daily",
    code: str | None = None,
    cfg: Any = None,
) -> QualityReport:
    """单只股票历史日线质量校验（方案 A9）。

    检查项：
        1. 空表 / 必需列缺失（error）；
        2. 交易日历比对：在 ``[df 最小日, df 最大日]`` 范围内缺失的交易日比例
           （> ``QUALITY_MAX_MISSING_RATIO`` 记 error，否则 warning）。
           只在自身上下限范围内比对，因为次新股（上市晚）与退市股（退市早）
           本来就不可能覆盖完整 5 年；
        3. OHLC 逻辑：``low <= open/close <= high``（违反记 error，数据不可用）；
        4. ``volume >= 0``、``amount >= 0``（负值记 error）；
        5. 涨跌幅越界（warning，同快照）；
        6. 重复交易日（error）。
    """
    cfg = cfg or config
    max_missing = float(getattr(cfg, "QUALITY_MAX_MISSING_RATIO", 0.05))
    max_pct = float(getattr(cfg, "QUALITY_MAX_ABS_PCT_CHG", 30.0))

    tag = f"[{code}] " if code else ""
    rows = 0 if df is None else int(len(df))
    report = QualityReport(dataset=dataset, rows=rows, expected=None, coverage=0.0)

    if df is None or df.empty:
        report.add("empty_frame", severity="error", message=f"{tag}历史日线为空")
        return report

    missing_columns = [col for col in DAILY_REQUIRED_COLUMNS if col not in df.columns]
    if missing_columns:
        report.add(
            "missing_columns",
            severity="error",
            message=f"{tag}缺少必需列: {missing_columns}",
        )
        return report

    frame = df.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
    frame = frame.dropna(subset=["trade_date"])
    if frame.empty:
        report.add("invalid_trade_date", severity="error", message=f"{tag}trade_date 全部无效")
        return report

    # 重复交易日
    duplicated = int(frame["trade_date"].duplicated().sum())
    if duplicated:
        report.add(
            "duplicate_trade_date",
            count=duplicated,
            severity="error",
            message=f"{tag}存在 {duplicated} 个重复交易日",
        )

    # 交易日历比对
    if expected_dates:
        start = min(frame["trade_date"])
        end = max(frame["trade_date"])
        expected_in_range = {d for d in expected_dates if start <= d <= end}
        actual = set(frame["trade_date"])
        missing_days = sorted(expected_in_range - actual)
        report.expected = len(expected_in_range) or None
        if expected_in_range:
            missing_ratio = len(missing_days) / len(expected_in_range)
            report.coverage = round(1.0 - missing_ratio, 4)
            if missing_days:
                severity = "error" if missing_ratio > max_missing else "warning"
                report.add(
                    "missing_trade_days",
                    count=len(missing_days),
                    severity=severity,
                    message=(
                        f"{tag}缺失 {len(missing_days)}/{len(expected_in_range)} 个交易日"
                        f"（{missing_ratio:.2%}，阈值 {max_missing:.0%}）"
                    ),
                    samples=[d.isoformat() for d in missing_days[:5]],
                )
    else:
        report.coverage = 1.0

    # OHLC 逻辑
    low = _num_series(frame, "low")
    high = _num_series(frame, "high")
    open_ = _num_series(frame, "open")
    close = _num_series(frame, "close")
    if all(series is not None for series in (low, high, open_, close)):
        assert low is not None and high is not None and open_ is not None and close is not None
        violation = (
            (low > high)
            | (low > open_)
            | (low > close)
            | (high < open_)
            | (high < close)
        )
        violation = violation.fillna(False)
        count = int(violation.sum())
        if count:
            report.add(
                "ohlc_violation",
                count=count,
                severity="error",
                message=f"{tag}{count} 行不满足 low<=open/close<=high",
                samples=_samples(frame, violation, code_column="code"),
            )

    # 负成交量 / 负成交额
    volume = _num_series(frame, "volume")
    if volume is not None:
        negative = int((volume.fillna(0) < 0).sum())
        if negative:
            report.add(
                "negative_volume",
                count=negative,
                severity="error",
                message=f"{tag}{negative} 行成交量为负",
            )
    amount = _num_series(frame, "amount")
    if amount is not None:
        negative = int((amount.fillna(0) < 0).sum())
        if negative:
            report.add(
                "negative_amount",
                count=negative,
                severity="error",
                message=f"{tag}{negative} 行成交额为负",
            )

    # 涨跌幅越界
    pct = _num_series(frame, "pct_chg")
    if pct is not None:
        bad = pct.abs() > max_pct
        bad_count = int(bad.fillna(False).sum())
        if bad_count:
            report.add(
                "pct_chg_out_of_range",
                count=bad_count,
                severity="warning",
                message=f"{tag}{bad_count} 行 |涨跌幅| > {max_pct:.0f}%",
                samples=_samples(frame, bad.fillna(False), code_column="code"),
            )

    return report


# ============================================================
# A1 股票池校验
# ============================================================
def check_universe(
    df: pd.DataFrame | None,
    active_before: int = 0,
    cfg: Any = None,
) -> QualityReport:
    """股票池质量校验：行数下限、覆盖率、重复代码、名称缺失率。

    覆盖率以 ``max(QUALITY_MIN_UNIVERSE, 库内现有正常标的数)`` 为分母：
    如果本次只拿到 1000 只（接口半残），覆盖率会显著低于 95% 从而阻断写入，
    避免把已有股票池"写残"。
    """
    cfg = cfg or config
    min_universe = int(getattr(cfg, "QUALITY_MIN_UNIVERSE", 4000))
    min_coverage = float(getattr(cfg, "QUALITY_MIN_COVERAGE", 0.95))
    max_missing = float(getattr(cfg, "QUALITY_MAX_MISSING_RATIO", 0.05))

    rows = 0 if df is None else int(len(df))
    expected = max(min_universe, int(active_before or 0), rows)
    coverage = rows / expected if expected > 0 else 0.0
    report = QualityReport(
        dataset="stock_universe",
        rows=rows,
        expected=expected,
        coverage=round(min(coverage, 1.0), 4),
    )

    if df is None or df.empty:
        report.add("empty_frame", severity="error", message="股票池为空")
        return report
    if rows < min_universe:
        report.add(
            "row_count_below_threshold",
            count=rows,
            severity="error",
            message=f"股票池 {rows} 只 < 阈值 {min_universe} 只",
        )
    if coverage < min_coverage:
        report.add(
            "coverage_below_threshold",
            count=rows,
            severity="error",
            message=f"股票池覆盖率 {coverage:.2%} < {min_coverage:.0%}（期望 {expected}）",
        )
    if "code" in df.columns:
        duplicated = int(df["code"].duplicated().sum())
        if duplicated:
            report.add(
                "duplicate_rows",
                count=duplicated,
                severity="error",
                message=f"股票池重复代码 {duplicated} 个",
                samples=_samples(df, df["code"].duplicated(keep=False)),
            )
    else:
        report.add("missing_column", severity="error", message="股票池缺少 code 列")

    if "name" in df.columns:
        ratio = float(df["name"].isna().mean())
        if ratio > max_missing:
            report.add(
                "missing_ratio_exceeded",
                severity="error",
                message=f"name 缺失率 {ratio:.2%} > {max_missing:.0%}",
            )
    return report


# ============================================================
# 写 meta_data_quality
# ============================================================
async def record_quality(
    db: Any,
    dataset: str,
    report: QualityReport,
    trade_date: date | None,
    status: str,
) -> int:
    """把质量报告 upsert 进 ``meta_data_quality``（方案 A9 / A10 / E2）。

    Args:
        db: ``AsyncSession``。
        dataset: 数据集名（同 :data:`DATASET_LABELS`）。
        report: 质量报告。
        trade_date: 本次数据对应交易日（日历/股票池可为 None）。
        status: ``ok / stale / quality_failed / error / unknown``（契约 0.4）。

    Returns:
        写入行数（恒为 1）。

    注意：``status != "ok"`` 时**不覆盖** ``last_success_at`` 与 ``last_trade_date``，
    保证前端能同时看到"上次成功时间"与"本次失败原因"。
    """
    # 延迟 import：让 quality 模块的纯函数校验可以在没有 sqlalchemy 的极简环境里 import
    from app.db.models.meta import MetaDataQuality

    now = datetime.now(timezone.utc)
    error_text = None if status == "ok" else report.error_text()
    row: dict[str, Any] = {
        "dataset": dataset,
        "display_name": DATASET_LABELS.get(dataset, dataset),
        "last_attempt_at": now,
        "rows": report.rows,
        "coverage": report.coverage,
        "anomalies": len(report.anomalies),
        "status": status,
        "error": error_text,
        "detail": report.to_detail(),
    }
    if status == "ok":
        row["last_success_at"] = now
        row["last_trade_date"] = trade_date
        update_columns = list(row.keys())
    else:
        # 失败不清空历史成功信息（前端要区分"上次成功"和"这次失败"）
        update_columns = [
            "display_name",
            "last_attempt_at",
            "rows",
            "coverage",
            "anomalies",
            "status",
            "error",
            "detail",
        ]
        row["last_trade_date"] = trade_date

    written = await upsert_rows(
        db,
        MetaDataQuality,
        [row],
        ["dataset"],
        update_columns=update_columns,
        update_timestamp=False,
    )
    logger.info(
        "质量记录 dataset=%s status=%s rows=%s coverage=%.2f%% anomalies=%d",
        dataset,
        status,
        report.rows,
        report.coverage * 100,
        len(report.anomalies),
    )
    return written


__all__ = [
    "DATASET_LABELS",
    "DAILY_REQUIRED_COLUMNS",
    "QualityReport",
    "make_error_report",
    "check_snapshot",
    "check_daily_frame",
    "check_universe",
    "record_quality",
]
