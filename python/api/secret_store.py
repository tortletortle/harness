"""Encrypted, account-local storage of model provider API keys.

Keys are typed directly on the configuration page and persisted to
``config/keys.enc.json``, encrypted with Windows DPAPI so only the current
Windows account/machine can read them back. The value is never written into
the tracked YAML configs, never returned by the API, and never sent back to
the browser.

Precedence at runtime stays unchanged: the environment variable
``EAS_LLM_API_KEY_<PROVIDER>`` wins over the persisted value. On non-Windows
platforms there is no supported plaintext fallback: saving raises and loading
returns an empty store, so the service keeps running without persisted keys.
"""
from __future__ import annotations
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

_FILENAME = "keys.enc.json"
_FORMAT = 1
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class SecretStoreError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.status = status
        super().__init__(message)


def _is_windows() -> bool:
    return sys.platform == "win32"


def _win_crypt32():
    """Return (crypt32, kernel32) with explicit signatures set once."""
    import ctypes
    from ctypes import wintypes

    if not hasattr(_win_crypt32, "_cache"):
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        # Without these signatures ctypes truncates the returned pointer on
        # 64-bit Python and LocalFree raises OverflowError.
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel32.LocalFree.restype = wintypes.HLOCAL
        crypt32.CryptProtectData.restype = wintypes.BOOL
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        _win_crypt32._cache = (crypt32, kernel32)
    return _win_crypt32._cache


def _protect(data: bytes) -> bytes:
    """DPAPI-encrypt ``data`` under the current Windows account."""
    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    crypt32, kernel32 = _win_crypt32()
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.c_void_p))
    blob_out = _DATA_BLOB(0, None)
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        None,  # description
        None,  # optional entropy
        None,  # reserved
        None,  # no prompt struct -> UI forbidden
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise SecretStoreError("Windows 数据保护（DPAPI）加密失败。")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _unprotect(blob: bytes) -> bytes:
    """Reverse :func:`_protect`; fails if the current account/machine differs."""
    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    crypt32, kernel32 = _win_crypt32()
    buf = ctypes.create_string_buffer(blob, len(blob))
    blob_in = _DATA_BLOB(len(blob), ctypes.cast(buf, ctypes.c_void_p))
    blob_out = _DATA_BLOB(0, None)
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,  # output description (unused)
        None,  # optional entropy
        None,  # reserved
        None,  # no prompt struct
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise SecretStoreError("密钥解密失败：当前 Windows 账户或机器无法读取该加密文件。", 500)
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def env_key_name(provider: str) -> str:
    return f"EAS_LLM_API_KEY_{provider.upper()}"


def env_url_name(provider: str) -> str:
    return f"EAS_LLM_BASE_URL_{provider.upper()}"


def clear_process_env_var(name: str) -> bool:
    removed = False
    for key in [item for item in os.environ if item == name]:
        os.environ.pop(key, None)
        removed = True
    return removed


def clear_process_env_key(provider: str) -> bool:
    """Drop the process-level override so a just-saved store key can take effect."""
    return clear_process_env_var(env_key_name(provider))


def clear_process_env_url(provider: str) -> bool:
    return clear_process_env_var(env_url_name(provider))


def retire_dotenv_var(dotenv_path: Path, env_name: str) -> bool:
    """Comment out ``env_name=`` in ``.env`` so restart does not revive it."""
    try:
        original = dotenv_path.read_text(encoding="utf-8")
    except OSError:
        return False
    changed = False
    lines = []
    for line in original.splitlines(keepends=True):
        body = line.lstrip(" \t")
        newline = ""
        core = body
        if body.endswith("\n"):
            newline = "\n"
            core = body[:-1]
            if core.endswith("\r"):
                core = core[:-1]
                newline = "\r\n"
        stripped = core.strip()
        if stripped.startswith("#") or not stripped:
            lines.append(line)
            continue
        if stripped.startswith("export "):
            assignment = stripped[7:].lstrip()
            prefix = core[: core.find(assignment)]
        else:
            assignment = stripped
            prefix = core[: core.find(assignment)]
        if "=" not in assignment:
            lines.append(line)
            continue
        name = assignment.split("=", 1)[0].strip()
        if name != env_name:
            lines.append(line)
            continue
        indent = line[: len(line) - len(body)]
        lines.append(f"{indent}# retired-by-config-ui {prefix}{assignment}{newline or os.linesep}")
        changed = True
    if not changed:
        return False
    try:
        dotenv_path.write_text("".join(lines), encoding="utf-8")
    except OSError:
        return False
    return True


def retire_dotenv_assignment(dotenv_path: Path, provider: str) -> bool:
    """Comment out ``EAS_LLM_API_KEY_<PROVIDER>=`` in ``.env``."""
    return retire_dotenv_var(dotenv_path, env_key_name(provider))


def retire_dotenv_url(dotenv_path: Path, provider: str) -> bool:
    return retire_dotenv_var(dotenv_path, env_url_name(provider))


def keys_path(directory: Path) -> Path:
    return directory / _FILENAME


def load_api_keys(directory: Path) -> dict:
    """Return ``{provider: plaintext}`` for the stored, decrypted keys.

    Missing file / non-Windows platform -> ``{}``. A malformed file raises
    :class:`SecretStoreError` without echoing content.
    """
    if not _is_windows():
        return {}
    path = keys_path(directory)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        if raw.get("format") != _FORMAT or not isinstance(raw.get("values"), dict):
            raise ValueError("unsupported format")
        out = {}
        for name, blob_b64 in raw["values"].items():
            if not isinstance(name, str) or not isinstance(blob_b64, str):
                continue
            value = _unprotect(base64.b64decode(blob_b64))
            try:
                out[name] = value.decode("utf-8")
            except ValueError:
                raise SecretStoreError("密钥存储内容无效，请重新保存。", 500) from None
        return out
    except (OSError, ValueError, json.JSONDecodeError):
        raise SecretStoreError("无法读取加密密钥文件，请检查文件格式或权限。", 500) from None


def save_api_keys(directory: Path, updates: dict) -> None:
    """Merge ``updates`` into the encrypted store; empty string removes a key."""
    if not _is_windows():
        raise SecretStoreError("当前平台不支持加密保存密钥；请改用环境变量。", 500)
    current = load_api_keys(directory)
    for name, value in updates.items():
        value = (value or "").strip()
        if value:
            current[name] = value
        else:
            current.pop(name, None)
    payload = {
        "format": _FORMAT,
        "values": {k: base64.b64encode(_protect(v.encode("utf-8"))).decode("ascii") for k, v in current.items()},
    }
    path = keys_path(directory)
    temp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".keys-", suffix=".json", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        temp = None
    except OSError:
        raise SecretStoreError("密钥文件保存失败，请检查目录权限。", 500) from None
    finally:
        if temp and Path(temp).exists():
            Path(temp).unlink(missing_ok=True)


def effective_key(directory: Path, provider: str) -> str:
    """Runtime key for a provider: environment wins, else persisted store."""
    env = os.getenv(env_key_name(provider))
    if env:
        return env
    try:
        return load_api_keys(directory).get(provider, "")
    except SecretStoreError:
        return ""
