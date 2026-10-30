"""
API 依赖（鉴权、请求 ID）
========================
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header, Request

from app.core.config import ADMIN_API_KEY
from app.core.errors import AppError, ErrorCode


class AdminAuthError(AppError):
    code = ErrorCode.BAD_REQUEST
    status_code = 403


async def require_admin(
    request: Request,
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
) -> None:
    """管理接口鉴权。

    规则：
      - 配置了 ADMIN_API_KEY：必须严格匹配；
      - 未配置：只允许本机来源（127.0.0.1 / ::1），避免开放的公网部署裸奔。
    """
    if ADMIN_API_KEY:
        if x_admin_key != ADMIN_API_KEY:
            raise AdminAuthError("管理密钥无效", detail={"header": "X-Admin-Key"})
        return

    client_host = request.client.host if request.client else ""
    if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise AdminAuthError(
            "未配置 ADMIN_API_KEY，管理接口仅允许本机访问",
            detail={"hint": "在 backend/.env 设置 ADMIN_API_KEY 后可用 X-Admin-Key 头调用"},
        )


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


AdminDep = Annotated[None, Depends(require_admin)]

__all__ = ["require_admin", "AdminDep", "new_request_id", "AdminAuthError"]
