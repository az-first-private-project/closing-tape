"""
采集层基础抽象（方案 A8 地基）
==============================
本模块只依赖标准库 + ``app.core``（config / errors / logging），**不 import pandas / akshare**。
这样做的原因：

1. CI 环境没有 akshare / pandas 时，采集层的基础类型仍然可以 import；
2. 编排层（``service.py``）与具体数据源（``akshare_provider`` / ``baostock_provider``）解耦，
   换数据源只需要新增一个 Provider，不动业务代码；
3. 限频、重试、降级链三个横切能力集中实现，避免每个采集函数各写一遍（方案 A8）。

核心概念（方案 5 原则 2：Provider 抽象 + 降级链）::

    Provider       数据源能力协议（覆盖方案 A1–A7），未实现的能力抛 NotImplementedError
    BaseProvider   提供默认 NotImplementedError 的基类，方便"只实现一部分"的兜底源
    ProviderChain  按 [primary, fallback] 顺序尝试，逐源记录成败（akshare → baostock）
    FetchResult    所有采集方法的统一返回体
    RateLimiter    异步令牌间隔限频（snapshot 60s / history 0.5s）
    retry_async    指数退避 + 抖动重试（同步/异步 callable 都支持）

一致性约定：
    - Provider 的能力方法一律是**同步阻塞**的（akshare / baostock 都是同步库）。
      由 ``ProviderChain`` / ``retry_async`` 统一用 ``asyncio.to_thread`` 放到线程池执行，
      事件循环不会被网络 IO 阻塞；Provider 内部不需要 async 化。
    - 所有金额/成交量转换放在 Provider 的 normalize 阶段完成（入库口径唯一）。
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import random
import time
from dataclasses import dataclass, field
from datetime import date
from typing import (
    Any,
    Callable,
    Iterable,
    NoReturn,
    Protocol,
    Sequence,
    TypeVar,
    runtime_checkable,
)

from app.core.config import (
    FETCH_BACKOFF_BASE_SEC,
    FETCH_MAX_RETRIES,
    HISTORY_REQUESTS_PER_SEC,
    SNAPSHOT_MIN_INTERVAL_SEC,
)
from app.core.errors import ProviderError
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

# 需要重试的异常：网络类 + ProviderError（``_safe_call`` 会把底层一切异常包成 ProviderError，
# 所以只要把 ProviderError 纳入重试，就能覆盖 akshare 抛出的各种 requests / JSON 解析错误）。
# 不包含 QualityBlockedError / DataUnavailableError —— 质量不合格与数据未就绪重试没有意义。
RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
    ProviderError,
)


# ============================================================
# 统一返回体
# ============================================================
@dataclass
class FetchResult:
    """一次采集调用的统一返回体。

    Attributes:
        rows: 成功写入/返回的行数。
        total: 期望或请求的总数（例如全市场股票数），用于计算覆盖率。
        failed: 失败计数（例如单只股票回补失败、单行缺主键被丢弃）。
        elapsed_ms: 耗时（毫秒）。
        detail: 附加信息。约定键：
            - ``data``     : 归一化后的 DataFrame（Provider 返回、编排层消费后通常不落库）
            - ``source``   : 实际生效的数据源名（akshare / baostock）
            - ``attempts`` : 降级链逐源记录（ProviderChain 写入）
            - ``skipped``  : 跳过原因（非交易日 / 已有数据 / 限频 / DB 锁被占用）
        quality_status: 采集层的质量结论（``ok`` / ``stale`` …）。非 ``ok`` 时说明
            采集函数已把精确质量记录写入 ``meta_data_quality``，编排层不得再用
            成功标记覆盖（否则部分缺失会被洗成绿色）。
    """

    rows: int = 0
    total: int | None = None
    failed: int = 0
    elapsed_ms: int = 0
    detail: dict[str, Any] = field(default_factory=dict)
    quality_status: str = "ok"

    @property
    def ok(self) -> bool:
        """是否成功（有数据且无失败）。空数据集请显式看 ``rows`` / ``detail``。"""
        return self.failed == 0 and self.rows > 0

    @property
    def source(self) -> str | None:
        """实际生效的数据源名。"""
        source = self.detail.get("source")
        return str(source) if source else None

    @property
    def is_skipped(self) -> bool:
        """是否被跳过（非交易日、已有数据、限频、DB 锁）。"""
        return bool(self.detail.get("skipped"))

    def summary(self) -> dict[str, Any]:
        """返回**可 JSON 序列化**的摘要（去掉 DataFrame 等不可序列化对象）。

        API 层（``pipeline._summary`` / admin 接口）直接把返回值塞进响应体，
        所以这里必须过滤掉 ``detail["data"]``，避免 FastAPI 序列化 500。
        """
        safe_detail = {
            key: value
            for key, value in self.detail.items()
            if key not in ("data", "attempts") and _is_json_safe(value)
        }
        payload = {
            "rows": self.rows,
            "total": self.total,
            "failed": self.failed,
            "elapsed_ms": self.elapsed_ms,
            "detail": safe_detail,
        }
        if self.quality_status != "ok":
            payload["quality_status"] = self.quality_status
        return payload


def _is_json_safe(value: Any) -> bool:
    """粗略判断值是否可 JSON 序列化（dataframe/ndarray 会被排除）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_json_safe(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_safe(v) for k, v in value.items())
    return False


# ============================================================
# 限频
# ============================================================
class RateLimiter:
    """异步令牌间隔限频器。

    语义是 **最小间隔**（不是令牌桶容量）：任意两次 ``acquire()`` 之间至少间隔
    ``min_interval_sec`` 秒。原因：AKShare 的合规边界是"固定节流"而不是"允许突发"，
    见方案 5.1（全市场快照 ≤ 1 次/分钟、历史回补 ≤ 2 请求/秒）。

    用法::

        limiter = make_history_limiter()      # 0.5s / 次
        await limiter.acquire()
        await retry_async(provider.fetch_stock_history, code, start, end)

        if not snapshot_limiter.try_acquire():
            return FetchResult(detail={"skipped": "rate_limited"})

    线程安全说明：只在事件循环线程内使用；``asyncio.Lock`` 会绑定首次使用的 loop，
    而 CLI / 测试可能用多次 ``asyncio.run()``（每次新 loop），所以换 loop 时重建锁。
    """

    def __init__(self, min_interval_sec: float, *, name: str = "limiter") -> None:
        self.min_interval = max(0.0, float(min_interval_sec))
        self.name = name
        self._next_at = 0.0
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---------- 内部 ----------
    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not loop:
            self._lock = asyncio.Lock()
            self._loop = loop
        return self._lock

    # ---------- 对外 ----------
    async def acquire(self) -> float:
        """等待并占用一个令牌，返回实际等待秒数。"""
        lock = self._get_lock()
        async with lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_at = max(now, self._next_at) + self.min_interval
            return max(0.0, wait)

    def try_acquire(self) -> bool:
        """非阻塞尝试占用令牌；不可用时立即返回 False。"""
        now = time.monotonic()
        if now < self._next_at:
            return False
        self._next_at = now + self.min_interval
        return True

    def wait_seconds(self) -> float:
        """距离下一次可 acquire 还需等待的秒数（0 表示立即可用）。"""
        return max(0.0, self._next_at - time.monotonic())

    def reset(self) -> None:
        """重置节流窗口（测试 / 手动强制刷新时用）。"""
        self._next_at = 0.0


def make_snapshot_limiter() -> RateLimiter:
    """全市场快照限频器：默认 60s / 次（``SNAPSHOT_MIN_INTERVAL_SEC``）。"""
    return RateLimiter(SNAPSHOT_MIN_INTERVAL_SEC, name="snapshot")


def make_history_limiter() -> RateLimiter:
    """历史回补限频器：默认 2 请求/秒，即 0.5s / 次（``HISTORY_REQUESTS_PER_SEC``）。"""
    per_sec = HISTORY_REQUESTS_PER_SEC if HISTORY_REQUESTS_PER_SEC > 0 else 2.0
    return RateLimiter(1.0 / per_sec, name="history")


# 进程级单例：同一进程内所有采集任务共享节流窗口，避免多个任务同时打接口。
SNAPSHOT_LIMITER: RateLimiter = make_snapshot_limiter()
HISTORY_LIMITER: RateLimiter = make_history_limiter()


# ============================================================
# 重试
# ============================================================
async def _invoke(func: Callable[..., Any], args: Sequence[Any], kwargs: dict[str, Any]) -> Any:
    """调用目标函数：协程直接 await，同步函数丢到线程池（不阻塞事件循环）。"""
    if inspect.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    result = await asyncio.to_thread(func, *args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


async def retry_async(
    func: Callable[..., T],
    /,
    *args: Any,
    retries: int | None = None,
    base_delay: float | None = None,
    max_delay: float = 30.0,
    jitter: float = 0.3,
    timeout: float | None = None,
    exceptions: tuple[type[BaseException], ...] | None = None,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
    **kwargs: Any,
) -> T:
    """指数退避 + 抖动的重试执行器（同步 / 异步 callable 均可）。

    为什么自己写而不用 tenacity：requirements 里的 tenacity 并非所有环境都装了，
    而这段逻辑 30 行就能写完，少一个硬依赖（方案 F1 依赖收敛）。

    Args:
        func: 目标函数（同步方法会经 ``asyncio.to_thread`` 执行）。
        retries: 重试次数（不含首次），默认 ``config.FETCH_MAX_RETRIES``。
        base_delay: 首次退避秒数，默认 ``config.FETCH_BACKOFF_BASE_SEC``。
        max_delay: 单次退避上限。
        jitter: 抖动比例（0.3 表示在 delay 上随机加 0~30%，防止多任务同步重试打爆接口）。
        timeout: 单次尝试超时（秒）；None 表示不限。注意线程无法被强杀，
            超时只是不再等待，调用方需容忍后台线程继续跑完。
        exceptions: 需要重试的异常类型，默认 :data:`RETRYABLE_EXCEPTIONS`。
        on_retry: 重试回调 ``(第几次, 异常, 本次等待秒数)``，用于日志/测试。
        *args / **kwargs: 透传给 ``func``。

    Returns:
        ``func`` 的返回值。

    Raises:
        最后一次尝试的异常（原样抛出，不包装，便于上层区分 ProviderError 等）。
    """
    retry_times = FETCH_MAX_RETRIES if retries is None else max(0, int(retries))
    delay_base = FETCH_BACKOFF_BASE_SEC if base_delay is None else max(0.0, float(base_delay))
    retry_on = exceptions or RETRYABLE_EXCEPTIONS

    last_exc: BaseException | None = None
    for attempt in range(retry_times + 1):
        try:
            call = _invoke(func, args, kwargs)
            if timeout is not None and timeout > 0:
                return await asyncio.wait_for(call, timeout=timeout)
            return await call
        except asyncio.CancelledError:
            # 任务被取消（例如 API 断开）不应重试
            raise
        except retry_on as exc:  # type: ignore[misc]  # 动态元组 isinstance 合法
            last_exc = exc
            if attempt >= retry_times:
                break
            delay = min(max_delay, delay_base * (2 ** attempt))
            if jitter > 0:
                delay += random.uniform(0.0, jitter * delay)
            logger.warning(
                "调用失败，第 %d/%d 次重试：%s（%.2fs 后）",
                attempt + 1,
                retry_times,
                f"{type(exc).__name__}: {exc}",
                delay,
            )
            if on_retry is not None:
                try:
                    on_retry(attempt + 1, exc, delay)
                except Exception:  # noqa: BLE001 —— 回调异常不影响主流程
                    logger.debug("on_retry 回调异常", exc_info=True)
            await asyncio.sleep(delay)

    assert last_exc is not None  # 逻辑上不可能为 None
    raise last_exc


def _sync_retry(
    func: Callable[..., T],
    args: Sequence[Any],
    kwargs: dict[str, Any],
    *,
    retries: int | None,
    base_delay: float | None,
    max_delay: float,
    jitter: float,
    exceptions: tuple[type[BaseException], ...] | None,
) -> T:
    """同步版重试（仅用于给同步函数加 ``@retry()`` 装饰器时）。"""
    retry_times = FETCH_MAX_RETRIES if retries is None else max(0, int(retries))
    delay_base = FETCH_BACKOFF_BASE_SEC if base_delay is None else max(0.0, float(base_delay))
    retry_on = exceptions or RETRYABLE_EXCEPTIONS
    last_exc: BaseException | None = None
    for attempt in range(retry_times + 1):
        try:
            return func(*args, **kwargs)
        except retry_on as exc:  # type: ignore[misc]
            last_exc = exc
            if attempt >= retry_times:
                break
            delay = min(max_delay, delay_base * (2 ** attempt))
            if jitter > 0:
                delay += random.uniform(0.0, jitter * delay)
            logger.warning("调用失败，第 %d/%d 次重试（同步）：%s", attempt + 1, retry_times, exc)
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def retry(
    *,
    retries: int | None = None,
    base_delay: float | None = None,
    max_delay: float = 30.0,
    jitter: float = 0.3,
    exceptions: tuple[type[BaseException], ...] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """``retry_async`` 的装饰器形态（同步 / 异步函数都可用）。

    示例::

        @retry(retries=2, base_delay=1.0)
        def fetch_something() -> FetchResult: ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await retry_async(
                    func,
                    *args,
                    retries=retries,
                    base_delay=base_delay,
                    max_delay=max_delay,
                    jitter=jitter,
                    exceptions=exceptions,
                    **kwargs,
                )

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return _sync_retry(
                func,
                args,
                kwargs,
                retries=retries,
                base_delay=base_delay,
                max_delay=max_delay,
                jitter=jitter,
                exceptions=exceptions,
            )

        return sync_wrapper

    return decorator


# ============================================================
# Provider 协议 / 基类
# ============================================================
@runtime_checkable
class Provider(Protocol):
    """数据源能力协议（方案 A1–A7）。

    能力方法展开为 9 个，覆盖方案里的 6 大能力域：

    ============  ==================================================
    方案编号       方法
    ============  ==================================================
    A1 股票池      fetch_stock_universe
    A2 全市场快照   fetch_daily_snapshot
    A3 历史日线     fetch_stock_history
    A4 板块         fetch_sector_list / fetch_sector_members
    A5 资金与两融    fetch_moneyflow / fetch_margin
    A6 指数         fetch_index_daily
    A7 交易日历     fetch_trade_calendar
    ============  ==================================================

    为什么不写成一个 ``fetch(kind=...)``：拆开之后降级链可以**按能力**降级
    （baostock 只支持历史日线 / 日历 / 指数，其余能力自然抛 NotImplementedError，
    编排层能精确知道"哪一类数据没有兜底源"，日志和监控才有意义）。

    方法均为**同步阻塞**，由 ``ProviderChain`` 放到线程池执行。
    """

    name: str

    def available(self) -> bool:
        """数据源是否可用（依赖已安装、联网开关打开）。不可用时降级链跳过本源。"""
        ...

    # ---- A1 股票池 ----
    def fetch_stock_universe(self) -> FetchResult:
        """拉全市场标的（含退市、ST、市值、行业归属）。"""
        ...

    # ---- A2 全市场快照 ----
    def fetch_daily_snapshot(self, trade_date: date) -> FetchResult:
        """一次请求拿全市场当日快照。"""
        ...

    # ---- A3 历史日线 ----
    def fetch_stock_history(self, code: str, start: date, end: date) -> FetchResult:
        """单只股票历史日线（含 adj_factor = 后复权收盘 / 不复权收盘）。"""
        ...

    # ---- A4 板块 ----
    def fetch_sector_list(self, trade_date: date) -> FetchResult:
        """板块列表 + 板块日线（当日快照或历史单日）。"""
        ...

    def fetch_sector_members(
        self, sector_code: str, sector_name: str, sector_type: str
    ) -> FetchResult:
        """单板块当前成分股。

        注意：AKShare 接口参数是**板块名称**而不是 BK 代码（历史坑）。
        """
        ...

    # ---- A5 资金与两融 ----
    def fetch_moneyflow(self, trade_date: date) -> FetchResult:
        """个股主力资金流（今日 + 5 日，单位元）。"""
        ...

    def fetch_margin(self, trade_date: date) -> FetchResult:
        """两融余额 + 北向净流入（亿元；北向可能为空，属预期内）。"""
        ...

    # ---- A6 指数 ----
    def fetch_index_daily(self, trade_date: date, include_us: bool = False) -> FetchResult:
        """指数日线（A 股四指数 + 沪深300；可选美股）。"""
        ...

    # ---- A7 交易日历 ----
    def fetch_trade_calendar(self, start: date, end: date) -> FetchResult:
        """真实交易日历（只返回开市日期列表）。"""
        ...


class BaseProvider:
    """Provider 默认实现：所有能力都抛 ``NotImplementedError``。

    兜底源（baostock）只需覆盖自己能提供的几个方法，其余保持默认即可；
    降级链会捕获 ``NotImplementedError`` 并继续尝试下一个源（不当作失败）。
    """

    name: str = "base"

    def available(self) -> bool:
        return True

    # ---------- 内部 ----------
    def _unsupported(self, method: str) -> NoReturn:
        raise NotImplementedError(f"{self.name} 不支持能力方法 {method}")

    # ---------- A1–A7 默认实现 ----------
    def fetch_stock_universe(self) -> FetchResult:
        self._unsupported("fetch_stock_universe")

    def fetch_daily_snapshot(self, trade_date: date) -> FetchResult:
        self._unsupported("fetch_daily_snapshot")

    def fetch_stock_history(self, code: str, start: date, end: date) -> FetchResult:
        self._unsupported("fetch_stock_history")

    def fetch_sector_list(self, trade_date: date) -> FetchResult:
        self._unsupported("fetch_sector_list")

    def fetch_sector_members(
        self, sector_code: str, sector_name: str, sector_type: str
    ) -> FetchResult:
        self._unsupported("fetch_sector_members")

    def fetch_moneyflow(self, trade_date: date) -> FetchResult:
        self._unsupported("fetch_moneyflow")

    def fetch_margin(self, trade_date: date) -> FetchResult:
        self._unsupported("fetch_margin")

    def fetch_index_daily(self, trade_date: date, include_us: bool = False) -> FetchResult:
        self._unsupported("fetch_index_daily")

    def fetch_trade_calendar(self, start: date, end: date) -> FetchResult:
        self._unsupported("fetch_trade_calendar")


# ============================================================
# 降级链
# ============================================================
class ProviderChain:
    """按 ``[primary, fallback, ...]`` 顺序尝试的数据源降级链（方案 5 原则 2）。

    行为约定（很重要，编排层依赖这些语义）：

    1. 源 ``available()`` 为 False → 记录 ``unavailable``，跳过；
    2. 源抛 ``NotImplementedError`` → 记录 ``not_implemented``，跳过（不算失败）；
    3. 源抛其他异常（含 ProviderError）→ 先按指数退避重试，仍失败则记录 ``failed``，
       继续尝试下一个源；
    4. 源返回 ``rows > 0`` → 立即返回（部分失败 ``failed > 0`` 也算可用，交由质量校验判断）；
    5. 源返回 ``rows == 0`` 且无失败 → 记录 ``empty`` 继续尝试兜底源；全部为空时返回
       这个空结果（**不抛异常**，例如北向资金长期为空属于预期内）；
    6. 全部源失败 → 抛 ``ProviderError``，``detail["attempts"]`` 带完整降级记录。

    每次调用的降级记录写在 ``FetchResult.detail["attempts"]`` 里，同时保留在
    ``chain.last_attempts`` 上，方便任务监控页展示"这次数据来自哪个源"。
    """

    def __init__(
        self,
        providers: Sequence[Provider],
        *,
        name: str = "default",
        max_retries: int | None = None,
    ) -> None:
        self.providers: list[Provider] = [p for p in providers if p is not None]
        self.name = name
        self.max_retries = FETCH_MAX_RETRIES if max_retries is None else int(max_retries)
        self.last_attempts: list[dict[str, Any]] = []

    @property
    def source_names(self) -> list[str]:
        return [getattr(p, "name", type(p).__name__) for p in self.providers]

    async def call(
        self,
        method: str,
        /,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> FetchResult:
        """按序调用各数据源的 ``method``，返回第一个可用结果。"""
        attempts: list[dict[str, Any]] = []
        empty_result: FetchResult | None = None

        for provider in self.providers:
            provider_name = getattr(provider, "name", type(provider).__name__)

            try:
                if not provider.available():
                    attempts.append({"source": provider_name, "status": "unavailable"})
                    continue
            except Exception as exc:  # noqa: BLE001 —— 探测可用性失败不该阻断降级链
                logger.warning("数据源 %s 可用性探测失败：%s", provider_name, exc)
                attempts.append(
                    {
                        "source": provider_name,
                        "status": "unavailable",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue

            func = getattr(provider, method, None)
            if func is None:
                attempts.append({"source": provider_name, "status": "not_implemented"})
                continue

            started = time.perf_counter()
            try:
                result = await retry_async(
                    func,
                    *args,
                    retries=self.max_retries,
                    timeout=timeout,
                    **kwargs,
                )
            except NotImplementedError as exc:
                attempts.append(
                    {"source": provider_name, "status": "not_implemented", "error": str(exc)}
                )
                continue
            except Exception as exc:  # noqa: BLE001 —— 一个源挂掉必须继续尝试兜底源
                elapsed = int((time.perf_counter() - started) * 1000)
                logger.warning(
                    "数据源 %s 调用 %s 失败（%dms）：%s: %s",
                    provider_name,
                    method,
                    elapsed,
                    type(exc).__name__,
                    exc,
                )
                attempts.append(
                    {
                        "source": provider_name,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                        "elapsed_ms": elapsed,
                    }
                )
                continue

            if not isinstance(result, FetchResult):
                # 防御：Provider 实现错误时给出明确报错，而不是让上层收到奇怪对象
                attempts.append(
                    {
                        "source": provider_name,
                        "status": "failed",
                        "error": f"返回类型错误: {type(result).__name__}（应为 FetchResult）",
                    }
                )
                continue

            elapsed = int((time.perf_counter() - started) * 1000)
            result.detail.setdefault("source", provider_name)
            if result.rows > 0:
                attempts.append(
                    {
                        "source": provider_name,
                        "status": "ok" if result.failed == 0 else "partial",
                        "rows": result.rows,
                        "failed": result.failed,
                        "elapsed_ms": elapsed,
                    }
                )
                result.detail["attempts"] = attempts
                self.last_attempts = attempts
                return result

            # 空结果：可能是数据本身为空（北向/区间无交易日），也可能是源异常，继续兜底
            empty_result = result
            attempts.append(
                {
                    "source": provider_name,
                    "status": "empty",
                    "failed": result.failed,
                    "elapsed_ms": elapsed,
                }
            )

        self.last_attempts = attempts
        if empty_result is not None:
            empty_result.detail["attempts"] = attempts
            return empty_result
        raise ProviderError(
            f"所有数据源均不可用：{self.name}.{method}",
            detail={"attempts": attempts, "chain": self.name},
        )

    # ---------- 类型化便捷方法（编排层用，避免到处写字符串）----------
    async def fetch_stock_universe(self) -> FetchResult:
        return await self.call("fetch_stock_universe")

    async def fetch_daily_snapshot(
        self, trade_date: date, *, timeout: float | None = None
    ) -> FetchResult:
        return await self.call("fetch_daily_snapshot", trade_date, timeout=timeout)

    async def fetch_stock_history(
        self, code: str, start: date, end: date, *, timeout: float | None = None
    ) -> FetchResult:
        return await self.call("fetch_stock_history", code, start, end, timeout=timeout)

    async def fetch_sector_list(
        self, trade_date: date, *, timeout: float | None = None
    ) -> FetchResult:
        return await self.call("fetch_sector_list", trade_date, timeout=timeout)

    async def fetch_sector_members(
        self,
        sector_code: str,
        sector_name: str,
        sector_type: str,
        *,
        timeout: float | None = None,
    ) -> FetchResult:
        return await self.call(
            "fetch_sector_members",
            sector_code,
            sector_name,
            sector_type,
            timeout=timeout,
        )

    async def fetch_moneyflow(
        self, trade_date: date, *, timeout: float | None = None
    ) -> FetchResult:
        return await self.call("fetch_moneyflow", trade_date, timeout=timeout)

    async def fetch_margin(
        self, trade_date: date, *, timeout: float | None = None
    ) -> FetchResult:
        return await self.call("fetch_margin", trade_date, timeout=timeout)

    async def fetch_index_daily(
        self, trade_date: date, include_us: bool = False, *, timeout: float | None = None
    ) -> FetchResult:
        return await self.call(
            "fetch_index_daily", trade_date, include_us=include_us, timeout=timeout
        )

    async def fetch_trade_calendar(
        self, start: date, end: date, *, timeout: float | None = None
    ) -> FetchResult:
        return await self.call("fetch_trade_calendar", start, end, timeout=timeout)


# ============================================================
# DB 工具（幂等 upsert）
# ============================================================
def dedupe_rows(rows: Iterable[dict[str, Any]], keys: Sequence[str]) -> list[dict[str, Any]]:
    """按唯一键去重（后者覆盖前者），保持插入顺序。

    为什么必须做：PostgreSQL 的 ``INSERT ... ON CONFLICT DO UPDATE`` 在**同一条语句**内
    不允许对同一个冲突键更新两次（报 "ON CONFLICT DO UPDATE command cannot affect
    row a second time"）。上游快照/合并数据出现重复代码是很常见的，所以必须在
    upsert 前统一去重，这也是"重复执行不产生重复行"的兜底。
    """
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        unique[tuple(row.get(key) for key in keys)] = row
    return list(unique.values())


def chunked(items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    """把列表切成固定大小的小块（避免单条 SQL 参数过多 / 事务过大）。"""
    step = max(1, int(size))
    for offset in range(0, len(items), step):
        yield items[offset: offset + step]


async def upsert_rows(
    db: Any,
    model: Any,
    rows: Sequence[dict[str, Any]],
    index_elements: Sequence[str],
    *,
    update_columns: Sequence[str] | None = None,
    chunk_size: int = 500,
    update_timestamp: bool = True,
) -> int:
    """幂等写入：``INSERT ... ON CONFLICT (index_elements) DO UPDATE``。

    方案 A8 硬要求：同一采集任务重跑不产生重复行、不产生错误。
    这里统一用 PostgreSQL 方言的 upsert（本项目只支持 PG，分区表也依赖 PG）。

    Args:
        db: ``AsyncSession``。
        model: ORM 模型类。
        rows: 待写入的行字典列表（键必须是模型列名）。
        index_elements: 冲突判定键（例如 ``["code", "trade_date"]``）。
        update_columns: 冲突时更新哪些列；None 表示"除冲突键外的全部列"。
        chunk_size: 单条语句最多多少行。
        update_timestamp: 模型若有 ``updated_at`` 列则自动刷新为 ``now()``。

    Returns:
        实际提交的行数（去重后）。
    """
    if not rows:
        return 0

    # 延迟 import：保证 base.py 在没装 sqlalchemy 的极简环境里也能 import（CI 语法检查）
    from sqlalchemy import func
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    unique_rows = dedupe_rows(rows, index_elements)
    table = model.__table__
    index_set = set(index_elements)

    if update_columns is None:
        # 以第一行的键为列集合；不同批次的键集合可能不同，因此逐块计算
        base_columns = list(unique_rows[0].keys())
        update_columns = [col for col in base_columns if col not in index_set]
    update_cols = list(update_columns)

    total = 0
    for chunk in chunked(unique_rows, chunk_size):
        chunk_keys = set(chunk[0].keys())
        cols = [col for col in update_cols if col in chunk_keys and col not in index_set]
        stmt = pg_insert(model).values(list(chunk))
        excluded = stmt.excluded
        set_: dict[str, Any] = {col: excluded[col] for col in cols}
        if update_timestamp and "updated_at" in table.columns:
            set_["updated_at"] = func.now()
        if not set_:
            # 只有冲突键、没有可更新列：退化为 DO NOTHING
            stmt = stmt.on_conflict_do_nothing(index_elements=list(index_elements))
        else:
            stmt = stmt.on_conflict_do_update(
                index_elements=list(index_elements), set_=set_
            )
        await db.execute(stmt)
        total += len(chunk)
    return total


__all__ = [
    "FetchResult",
    "RateLimiter",
    "make_snapshot_limiter",
    "make_history_limiter",
    "SNAPSHOT_LIMITER",
    "HISTORY_LIMITER",
    "RETRYABLE_EXCEPTIONS",
    "retry_async",
    "retry",
    "Provider",
    "BaseProvider",
    "ProviderChain",
    "dedupe_rows",
    "chunked",
    "upsert_rows",
]
