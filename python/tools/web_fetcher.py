"""
Web资源获取工具
用于下载文件和获取网页内容
"""

import ipaddress
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urljoin, urlparse

from tools.workspace import resolve_workspace_path

_ALLOWED_SCHEMES = {"http", "https"}
_DEFAULT_TIMEOUT = 30  # 秒
_MAX_DOWNLOAD_SIZE = 200 * 1024 * 1024  # 200MB
_MAX_CONTENT_SIZE = 20 * 1024 * 1024  # 20MB
_READ_CHUNK = 64 * 1024


def _is_private_or_internal_ip(ip_str: str) -> bool:
    """判断是否为内网/保留/本机地址，防止 SSRF 访问内部服务"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # 无法解析视为不安全
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_url(url: str):
    """校验 URL 协议与目标地址，返回错误信息，无错误时返回 None"""
    if not url or not isinstance(url, str):
        return "url 不能为空"

    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return f"不支持的协议: {parsed.scheme!r}，仅允许 http/https"

    hostname = parsed.hostname
    if not hostname:
        return "无法解析URL中的主机名"

    try:
        resolved_ips = socket.getaddrinfo(hostname, None)
    except Exception as e:
        return f"无法解析主机名: {hostname!r} ({e})"

    for info in resolved_ips:
        ip_str = info[4][0]
        if _is_private_or_internal_ip(ip_str):
            return f"拒绝访问内网/本机地址: {hostname} -> {ip_str}"

    return None


class _RedirectBlockedError(urllib.error.HTTPError):
    """重定向目标未通过 SSRF 校验（fail-closed 标记异常）。"""


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向守卫：每个跳转目标都重新过 _validate_url，失败则拒绝跳转。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute = urljoin(req.get_full_url(), newurl)
        error = _validate_url(absolute)
        if error:
            raise _RedirectBlockedError(
                absolute, code, f"重定向目标被拒绝: {error}", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _safe_opener():
    """携带重定向守卫的 opener（显式使用，不污染全局 urlopen）。"""
    return urllib.request.build_opener(_SafeRedirectHandler)


class WebFetcher:
    """Web资源获取工具"""
    
    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        """获取工具元数据"""
        return {
            "tool_id": "web_fetcher",
            "name": "Web资源获取工具",
            "description": "下载文件和获取网页内容",
            "category": "search",
            "version": "1.0.0",
            "capabilities": {
                "instructions": [
                    {
                        "name": "download_file",
                        "description": "下载文件",
                        "parameters": {
                            "url": "文件URL",
                            "path": "保存路径"
                        }
                    },
                    {
                        "name": "fetch_content",
                        "description": "获取网页内容",
                        "parameters": {
                            "url": "网页URL"
                        }
                    }
                ]
            }
        }
    
    @staticmethod
    def download_file(url: str, path: str, timeout: int = _DEFAULT_TIMEOUT) -> Dict[str, Any]:
        """下载文件"""
        try:
            error = _validate_url(url)
            if error:
                return {
                    "success": False,
                    "message": error,
                    "error": "Invalid or blocked URL",
                    "blocked": True
                }

            file_path = resolve_workspace_path(path)

            # 确保父目录存在
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # 下载文件（带超时与大小限制，避免无限制下载导致磁盘被打满或请求挂死）
            req = urllib.request.Request(url, headers={"User-Agent": "EAS-WebFetcher/1.0"})
            with _safe_opener().open(req, timeout=timeout) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > _MAX_DOWNLOAD_SIZE:
                    return {
                        "success": False,
                        "message": f"文件过大，拒绝下载（{content_length} 字节 > 上限 {_MAX_DOWNLOAD_SIZE} 字节）",
                        "error": "File too large"
                    }

                downloaded = 0
                with open(file_path, "wb") as f:
                    while True:
                        chunk = response.read(_READ_CHUNK)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        if downloaded > _MAX_DOWNLOAD_SIZE:
                            f.close()
                            file_path.unlink(missing_ok=True)
                            return {
                                "success": False,
                                "message": f"文件过大，下载中断（已下载 {downloaded} 字节 > 上限 {_MAX_DOWNLOAD_SIZE} 字节）",
                                "error": "File too large"
                            }
                        f.write(chunk)

            # 获取文件大小
            size = file_path.stat().st_size

            return {
                "success": True,
                "message": f"文件下载成功: {path}",
                "path": str(file_path.absolute()),
                "size": size
            }
        except _RedirectBlockedError as e:
            return {
                "success": False,
                "message": f"下载失败: {e}",
                "error": str(e),
                "blocked": True,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"下载失败: {str(e)}",
                "error": str(e)
            }

    @staticmethod
    def fetch_content(url: str, timeout: int = _DEFAULT_TIMEOUT) -> Dict[str, Any]:
        """获取网页内容"""
        try:
            error = _validate_url(url)
            if error:
                return {
                    "success": False,
                    "message": error,
                    "error": "Invalid or blocked URL",
                    "blocked": True
                }

            req = urllib.request.Request(url, headers={"User-Agent": "EAS-WebFetcher/1.0"})
            with _safe_opener().open(req, timeout=timeout) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > _MAX_CONTENT_SIZE:
                    return {
                        "success": False,
                        "message": f"内容过大，拒绝获取（{content_length} 字节 > 上限 {_MAX_CONTENT_SIZE} 字节）",
                        "error": "Content too large"
                    }

                chunks = []
                total = 0
                while True:
                    chunk = response.read(_READ_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_CONTENT_SIZE:
                        return {
                            "success": False,
                            "message": f"内容过大，获取中断（已读 {total} 字节 > 上限 {_MAX_CONTENT_SIZE} 字节）",
                            "error": "Content too large"
                        }
                    chunks.append(chunk)
                raw = b"".join(chunks)
                content = raw.decode("utf-8", errors="replace")

            return {
                "success": True,
                "message": "内容获取成功",
                "content": content,
                "size": len(content)
            }
        except _RedirectBlockedError as e:
            return {
                "success": False,
                "message": f"获取内容失败: {e}",
                "error": str(e),
                "blocked": True,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"获取内容失败: {str(e)}",
                "error": str(e)
            }
    
    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具指令"""
        inputs = inputs or {}
        if instruction == "download_file":
            return WebFetcher.download_file(
                url=inputs.get("url", ""),
                path=inputs.get("path", ""),
                timeout=inputs.get("timeout", _DEFAULT_TIMEOUT)
            )
        elif instruction == "fetch_content":
            return WebFetcher.fetch_content(
                url=inputs.get("url", ""),
                timeout=inputs.get("timeout", _DEFAULT_TIMEOUT)
            )
        else:
            return {
                "success": False,
                "message": f"未知指令: {instruction}",
                "error": "Invalid instruction"
            }
