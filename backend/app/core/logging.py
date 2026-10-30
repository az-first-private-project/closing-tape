"""
结构化日志（方案 F3）
=====================
- LOG_JSON=1 时输出 JSON 行，便于采集/回测日志入库与检索；
- 默认输出人类可读格式；
- `bind_run(run_id=...)` 用 contextvar 让一次任务内的日志自动带 run_id / job 标识。

不使用 structlog（若未安装则自动降级为 logging），保证零硬依赖。
"""
from __future__ import annotations

import json
import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator

from app.core.config import LOG_JSON, LOG_LEVEL

# 当前上下文里的一次运行标识（run_id / job / dataset）
_ctx: ContextVar[dict[str, Any]] = ContextVar("gfna_log_ctx", default={})

_STD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(_ctx.get())
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                try:
                    json.dumps(value, default=str)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = _ctx.get()
        prefix = " ".join(f"[{k}={v}]" for k, v in ctx.items() if v is not None)
        base = super().format(record)
        return f"{base} {prefix}".rstrip() if prefix else base


def setup_logging() -> None:
    """进程启动时调用一次。幂等。"""
    root = logging.getLogger()
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    root.setLevel(level)

    # 清掉旧 handler，避免 uvicorn reload 重复输出
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    if LOG_JSON:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(_TextFormatter(
            fmt="%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
    root.addHandler(handler)

    # 降噪
    for noisy in ("httpx", "httpcore", "openai", "urllib3", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@contextmanager
def bind_run(**fields: Any) -> Iterator[None]:
    """在代码块内给所有日志附加字段，例如 run_id 与阶段名。"""
    token = _ctx.set({**_ctx.get(), **{k: v for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _ctx.reset(token)


__all__ = ["setup_logging", "get_logger", "bind_run"]
