"""
API 认证

提供基于 Bearer Token 的认证。Token 来源优先级：
  1. 环境变量 EAS_API_TOKEN
  2. 配置文件 config/api_token 中的内容
  3. 自动生成并写入 config/api_token（首次启动时）

设计约束：所有写操作与敏感读操作端点必须依赖 require_token。
WebSocket 无法自定义请求头，因此通过查询参数 ?token=... 校验。
"""

import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import Header, HTTPException, status

from logging_config import get_logger

logger = get_logger(__name__)

ENV_VAR = "EAS_API_TOKEN"
TOKEN_FILENAME = "api_token"

_cached_token: Optional[str] = None


def _token_file() -> Path:
    """定位 token 文件，与 config/models.yaml 同目录。"""
    for candidate in (Path("config"), Path("../config")):
        if candidate.is_dir():
            return candidate / TOKEN_FILENAME
    # 两者都不存在时，创建 config/
    Path("config").mkdir(parents=True, exist_ok=True)
    return Path("config") / TOKEN_FILENAME


def load_token() -> str:
    """
    获取当前 API token，结果会被缓存。

    没有任何来源时自动生成一个 32 字节 URL-safe token 并持久化，
    避免出现「无认证裸奔」的默认状态。
    """
    global _cached_token
    if _cached_token:
        return _cached_token

    env_token = os.environ.get(ENV_VAR, "").strip()
    if env_token:
        _cached_token = env_token
        return _cached_token

    path = _token_file()
    if path.exists():
        file_token = path.read_text(encoding="utf-8").strip()
        if file_token:
            _cached_token = file_token
            return _cached_token

    generated = secrets.token_urlsafe(32)
    path.write_text(generated, encoding="utf-8")
    _cached_token = generated
    logger.info("已生成新的 API token 并写入 {}", path)
    return _cached_token


def reset_token_cache() -> None:
    """清空缓存，供测试或轮换 token 后调用。"""
    global _cached_token
    _cached_token = None


def verify_token(candidate: Optional[str]) -> bool:
    """常数时间比较，避免时序侧信道。"""
    if not candidate:
        return False
    return secrets.compare_digest(candidate, load_token())


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


async def require_token(
    authorization: Optional[str] = Header(default=None),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
) -> None:
    """
    FastAPI 依赖：校验 Bearer token。

    接受 `Authorization: Bearer <token>` 或 `X-API-Token: <token>`。
    """
    supplied = _extract_bearer(authorization) or (x_api_token or "").strip()

    if not verify_token(supplied):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少或无效的 API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
