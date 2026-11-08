"""
统一错误模型与响应契约（方案 E1）
==================================
原则：**数据缺失返回明确原因，而不是 500 或空数组。**

约定响应体：
    {
      "ok": false,
      "error": {"code": "DATA_NOT_READY", "message": "...", "detail": {...}},
      "request_id": "..."
    }
成功响应保持裸业务对象（不包一层 data），以兼容既有前端；错误统一走上面的结构。
"""
from __future__ import annotations

from typing import Any


class ErrorCode:
    """错误码常量。前端可据此做差异化提示。"""

    # 数据类
    DATA_NOT_READY = "DATA_NOT_READY"          # 数据尚未采集/未生成
    DATA_STALE = "DATA_STALE"                  # 数据过期
    DATA_QUALITY = "DATA_QUALITY"              # 质量校验未通过
    NO_TRADE_DATE = "NO_TRADE_DATE"            # 该日非交易日或无数据
    # 参数类
    BAD_REQUEST = "BAD_REQUEST"
    UNKNOWN_ALGO = "UNKNOWN_ALGO"
    INVALID_PARAM = "INVALID_PARAM"
    # 资源类
    NOT_FOUND = "NOT_FOUND"
    # 依赖类
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    DB_UNAVAILABLE = "DB_UNAVAILABLE"
    # 系统
    INTERNAL = "INTERNAL"
    BUSY = "BUSY"


class AppError(Exception):
    """所有业务异常的基类，被全局 handler 转成统一响应。"""

    code: str = ErrorCode.INTERNAL
    status_code: int = 500

    def __init__(self, message: str, *, detail: Any = None, code: str | None = None,
                 status_code: int | None = None, suggestion: str | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.suggestion = suggestion
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code

    def to_payload(self, request_id: str | None = None) -> dict:
        body: dict[str, Any] = {
            "ok": False,
            "error": {"code": self.code, "message": self.message},
        }
        if self.suggestion:
            # C2：前端四个反馈要素（错误码/原因/建议/重试）中的「建议」。
            # 仅追加可选字段；detail.next_action 保留原有位置。
            body["error"]["suggestion"] = self.suggestion
        if self.detail is not None:
            body["error"]["detail"] = self.detail
        if request_id:
            body["request_id"] = request_id
        return body


class NotFoundError(AppError):
    code = ErrorCode.NOT_FOUND
    status_code = 404


class DataUnavailableError(AppError):
    """数据还没准备好：应返回 200 + 明确原因，还是 409？

    V5 选择 409 —— 让前端能区分「接口坏了」与「数据没来」，
    同时 body 里带 next_action 告诉用户该做什么。
    """

    code = ErrorCode.DATA_NOT_READY
    status_code = 409

    def __init__(self, message: str, *, next_action: str = "", detail: Any = None,
                 suggestion: str | None = None):
        super().__init__(
            message,
            detail={"next_action": next_action, **(detail or {})} if next_action else detail,
            suggestion=suggestion or (next_action or None),
        )


class QualityBlockedError(AppError):
    code = ErrorCode.DATA_QUALITY
    status_code = 409


class ProviderError(AppError):
    code = ErrorCode.PROVIDER_UNAVAILABLE
    status_code = 503


class UnknownAlgoError(AppError):
    code = ErrorCode.UNKNOWN_ALGO
    status_code = 404


class InvalidParamError(AppError):
    code = ErrorCode.INVALID_PARAM
    status_code = 422


__all__ = [
    "ErrorCode", "AppError", "NotFoundError", "DataUnavailableError",
    "QualityBlockedError", "ProviderError", "UnknownAlgoError", "InvalidParamError",
]
