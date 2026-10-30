"""
Baostock 兜底数据源（方案 5：Baostock 兜底校验）
=================================================
Baostock 的定位是**兜底与校验**，不是主力：

1. 历史日线复权口径规范（``adjustflag``：1 后复权 / 2 前复权 / 3 不复权），
   用来交叉校验 akshare 的 ``adj_factor = 后复权收盘/不复权收盘``；
2. akshare 失败时提供日线兜底；
3. 交易日历、指数日线也有，可作为 akshare 的备用源。

能力范围（其余能力继承 ``BaseProvider`` 抛 ``NotImplementedError``，
降级链会自动跳过）::

    fetch_stock_history      支持（A3 兜底）
    fetch_trade_calendar     支持（A7 兜底）
    fetch_index_daily        支持（A6 兜底，仅 A 股指数）
    fetch_stock_universe     不支持（baostock 无行业/市值快照）
    fetch_daily_snapshot     不支持（baostock 无全市场快照）
    fetch_sector_*           不支持（baostock 无板块数据）
    fetch_moneyflow/margin   不支持（baostock 无资金流）

baostock 未安装时 ``available()`` 返回 False，**模块仍可 import**，不崩。

单位口径：baostock 的 ``volume`` 单位**已经是股**、``amount`` 单位**元**，
不需要像 akshare 那样 ×100；``pctChg`` 单位是百分数（如 1.23 表示 1.23%）。
"""
from __future__ import annotations

import threading
import time
from datetime import date, timedelta
from typing import Any

import pandas as pd

try:  # pragma: no cover —— 依赖是否安装由运行环境决定
    import baostock as bs
except ImportError:  # pragma: no cover
    bs = None  # type: ignore[assignment]

from app.core import config
from app.core.constants import exchange_of
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.ingest.akshare_provider import HISTORY_COLUMNS, _num, _stock_code, _to_date
from app.ingest.base import BaseProvider, FetchResult

logger = get_logger(__name__)

# baostock 日线字段（个股；指数字段更少）
STOCK_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,"
    "adjustflag,turn,tradestatus,pctChg,isST"
)
INDEX_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,pctChg"


def _bs_stock_code(code: str) -> str:
    """6 位代码 → baostock 代码（``sh.600519`` / ``sz.000001`` / ``bj.920001``）。"""
    code = _stock_code(code) or str(code).zfill(6)
    return f"{exchange_of(code).lower()}.{code}"


def _bs_index_code(index_code: str) -> str:
    """``sh000001`` → ``sh.000001``（baostock 指数代码格式）。"""
    text = str(index_code).strip().lower()
    if "." in text:
        return text
    return f"{text[:2]}.{text[2:]}"


class BaostockProvider(BaseProvider):
    """Baostock 兜底数据源。"""

    name = "baostock"

    def __init__(self) -> None:
        self._logged_in = False
        self._login_lock = threading.Lock()

    # ---------- 可用性 / 登录 ----------
    def available(self) -> bool:
        """baostock 已安装且允许联网时为 True。"""
        return bs is not None and bool(config.INGEST_NETWORK_ENABLED)

    def _ensure_login(self) -> None:
        """懒登录（baostock 是全局单例会话，成功一次后进程内复用）。"""
        if self._logged_in:
            return
        if bs is None:  # pragma: no cover —— available() 已拦截
            raise ProviderError("baostock 未安装", detail={"provider": "baostock"})
        if not config.INGEST_NETWORK_ENABLED:  # pragma: no cover
            raise ProviderError(
                "INGEST_NETWORK_ENABLED=0，禁止联网采集", detail={"provider": "baostock"}
            )
        with self._login_lock:
            if self._logged_in:
                return
            try:
                result = bs.login()
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(
                    f"baostock 登录异常: {type(exc).__name__}: {exc}",
                    detail={"provider": "baostock"},
                ) from exc
            self._check_result(result, "login")
            self._logged_in = True
            logger.info("baostock 登录成功")

    def close(self) -> None:
        """退出 baostock 会话（脚本退出前调用；失败忽略）。"""
        if bs is None or not self._logged_in:
            return
        try:
            bs.logout()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._logged_in = False

    # ---------- 内部工具 ----------
    @staticmethod
    def _check_result(result: Any, what: str) -> None:
        """baostock 用 error_code 表示成功（"0"）。"""
        code = getattr(result, "error_code", "1")
        if str(code) != "0":
            message = getattr(result, "error_msg", "")
            raise ProviderError(
                f"baostock 返回错误[{what}]: {code} {message}",
                detail={"provider": "baostock", "what": what, "error_code": str(code)},
            )

    def _safe_call(self, func: Any, *args: Any, what: str = "", **kwargs: Any) -> Any:
        """统一包裹 baostock 调用：异常 → ProviderError（上层重试/降级）。"""
        label = what or getattr(func, "__name__", "baostock_call")
        try:
            result = func(*args, **kwargs)
        except NotImplementedError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"baostock 调用失败[{label}]: {type(exc).__name__}: {exc}",
                detail={"provider": "baostock", "what": label},
            ) from exc
        self._check_result(result, label)
        return result

    def _rows_to_frame(self, result: Any, what: str) -> pd.DataFrame:
        """把 baostock 的 ResultData 逐行转 DataFrame。"""
        rows: list[list[str]] = []
        while str(getattr(result, "error_code", "1")) == "0" and result.next():
            rows.append(result.get_row_data())
        fields = list(getattr(result, "fields", []))
        return pd.DataFrame(rows, columns=fields)

    def _query_k_data(
        self,
        code: str,
        fields: str,
        start: date,
        end: date,
        *,
        adjustflag: str = "3",
    ) -> pd.DataFrame:
        """``query_history_k_data_plus`` 的薄封装。"""
        self._ensure_login()
        result = self._safe_call(
            bs.query_history_k_data_plus,
            code,
            fields,
            what=f"query_history_k_data_plus[{code}]",
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
            frequency="d",
            adjustflag=adjustflag,
        )
        return self._rows_to_frame(result, f"query_history_k_data_plus[{code}]")

    # ---------- A3 历史日线（兜底 + 复权校验）----------
    def fetch_stock_history(self, code: str, start: date, end: date) -> FetchResult:
        """不复权（adjustflag=3）+ 后复权（adjustflag=1）两次查询，算 adj_factor。

        baostock 的 ``volume`` 单位是股、``amount`` 单位元，无需换算（与 akshare 不同）。
        """
        started = time.perf_counter()
        bs_code = _bs_stock_code(code)
        raw = self._query_k_data(bs_code, STOCK_FIELDS, start, end, adjustflag="3")
        hfq = self._query_k_data(bs_code, STOCK_FIELDS, start, end, adjustflag="1")

        hfq_map: dict[date, float] = {}
        if not hfq.empty:
            for row in hfq.to_dict("records"):
                trade_date = _to_date(row.get("date"))
                close = _num(row.get("close"))
                if trade_date is not None and close is not None:
                    hfq_map[trade_date] = close

        records: list[dict[str, Any]] = []
        prev_close: float | None = None
        for row in raw.to_dict("records"):
            trade_date = _to_date(row.get("date"))
            close = _num(row.get("close"))
            if trade_date is None or close is None or close <= 0:
                continue
            pre_close = _num(row.get("preclose")) or prev_close
            pct = _num(row.get("pctChg"))
            if pct is None and pre_close:
                pct = round((close / pre_close - 1) * 100, 3)
            hfq_close = hfq_map.get(trade_date)
            records.append(
                {
                    "trade_date": trade_date,
                    "open": _num(row.get("open")),
                    "high": _num(row.get("high")),
                    "low": _num(row.get("low")),
                    "close": close,
                    "pre_close": pre_close,
                    "pct_chg": pct,
                    "amplitude": None,  # baostock 日线无振幅字段，留空不造假
                    "volume": int(_num(row.get("volume")))  # 单位已是股
                    if _num(row.get("volume")) is not None
                    else None,
                    "amount": _num(row.get("amount")),  # 单位已是元
                    "turnover_rate": _num(row.get("turn")),
                    "adj_factor": round(hfq_close / close, 6)
                    if hfq_close is not None and close > 0
                    else None,
                }
            )
            prev_close = close

        frame = pd.DataFrame(records, columns=list(HISTORY_COLUMNS))
        if not frame.empty:
            frame = frame.sort_values("trade_date").reset_index(drop=True)
        elapsed = int((time.perf_counter() - started) * 1000)
        return FetchResult(
            rows=len(frame),
            total=len(frame),
            elapsed_ms=elapsed,
            detail={"data": frame, "source": self.name, "code": code,
                    "range": [start.isoformat(), end.isoformat()]},
        )

    # ---------- A7 交易日历（兜底）----------
    def fetch_trade_calendar(self, start: date, end: date) -> FetchResult:
        """``query_trade_dates``：只返回开市日期。"""
        started = time.perf_counter()
        self._ensure_login()
        result = self._safe_call(
            bs.query_trade_dates,
            what="query_trade_dates",
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
        )
        # fields: calendar_date,is_trading_day
        dates: list[date] = []
        while str(getattr(result, "error_code", "1")) == "0" and result.next():
            row = result.get_row_data()
            if len(row) >= 2 and str(row[1]).strip() == "1":
                parsed = _to_date(row[0])
                if parsed is not None:
                    dates.append(parsed)
        frame = pd.DataFrame({"trade_date": sorted(set(dates))}, columns=["trade_date"])
        elapsed = int((time.perf_counter() - started) * 1000)
        return FetchResult(
            rows=len(frame),
            total=len(frame),
            elapsed_ms=elapsed,
            detail={"data": frame, "source": self.name,
                    "range": [start.isoformat(), end.isoformat()]},
        )

    # ---------- A6 指数日线（兜底，仅 A 股）----------
    def fetch_index_daily(self, trade_date: date, include_us: bool = False) -> FetchResult:
        """A 股指数日线兜底（baostock 没有美股）。``include_us`` 仅保持签名一致。"""
        from app.core.constants import INDEX_MAP

        started = time.perf_counter()
        records: list[dict[str, Any]] = []
        failed = 0
        for index_code, (index_name, is_a_share) in INDEX_MAP.items():
            if not is_a_share:
                continue
            try:
                # 拉前后 10 天，保证能算出 pre_close（涨跌幅）
                frame = self._query_k_data(
                    _bs_index_code(index_code),
                    INDEX_FIELDS,
                    trade_date - timedelta(days=10),
                    trade_date + timedelta(days=1),
                )
            except ProviderError as exc:
                failed += 1
                logger.warning("baostock 指数获取失败[%s]：%s", index_code, exc)
                continue
            prev_close: float | None = None
            for row in frame.to_dict("records"):
                row_date = _to_date(row.get("date"))
                close = _num(row.get("close"))
                if row_date == trade_date:
                    pre_close = _num(row.get("preclose")) or prev_close
                    pct = _num(row.get("pctChg"))
                    if pct is None and close is not None and pre_close:
                        pct = round((close / pre_close - 1) * 100, 3)
                    records.append(
                        {
                            "index_code": index_code,
                            "index_name": index_name,
                            "trade_date": row_date,
                            "open": _num(row.get("open")),
                            "high": _num(row.get("high")),
                            "low": _num(row.get("low")),
                            "close": close,
                            "pre_close": pre_close,
                            "pct_chg": pct,
                            "volume": int(_num(row.get("volume")))
                            if _num(row.get("volume")) is not None
                            else None,
                            "amount": _num(row.get("amount")),
                        }
                    )
                    break
                if close is not None:
                    prev_close = close

        frame = pd.DataFrame(records)
        elapsed = int((time.perf_counter() - started) * 1000)
        return FetchResult(
            rows=len(frame),
            total=len(frame),
            failed=failed,
            elapsed_ms=elapsed,
            detail={"data": frame, "source": self.name,
                    "trade_date": trade_date.isoformat(),
                    "include_us": False},
        )


__all__ = ["BaostockProvider", "STOCK_FIELDS", "INDEX_FIELDS"]
