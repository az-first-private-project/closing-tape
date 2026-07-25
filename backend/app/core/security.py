"""
安全模块
-------
API Key 验证中间件 (可选启用)
用于保护管理类接口，如手动触发爬虫
"""
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
import os

# 从环境变量读取管理 API Key
# 如果不设置，则使用默认值 (仅用于开发环境)
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "gfna-admin-key-change-me")

# 定义 API Key 请求头名称
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)):
    """
    验证请求中的 API Key 是否有效

    参数:
        api_key: 从请求头 X-API-Key 中提取的密钥

    返回:
        str: 验证通过则返回密钥本身

    异常:
        HTTPException 403: 密钥无效或未提供
    """
    # 如果没有配置 ADMIN_API_KEY，则跳过验证 (开发模式)
    if not ADMIN_API_KEY:
        return None

    if api_key is None:
        raise HTTPException(
            status_code=403,
            detail="请提供 API Key (请求头: X-API-Key)",
        )

    if api_key != ADMIN_API_KEY:
        raise HTTPException(
            status_code=403,
            detail="API Key 无效，拒绝访问",
        )

    return api_key