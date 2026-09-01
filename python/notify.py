"""IM 通知通道（入口层）：计划完成/失败/暂停、待审批、设计书就绪时主动推送。

设计原则：
- 多通道适配：飞书 / 钉钉 / 企业微信群机器人、Telegram bot、通用 webhook——
  全部只是 HTTP POST，无新增依赖（httpx 已是既有依赖）；
- fail-open：任何通知失败只记日志与本地审计，绝不打断执行循环或端点成功路径；
- 防轰炸：同 (event, dedup_key) 在 cooldown 窗口内只发一次（test 事件豁免）；
- 未配置任何通道时降级为本地审计（data/notifications.jsonl），机制仍可验证；
  用户在 config（建议 models.local.yaml）填 webhook 后即生效。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from logging_config import get_logger

logger = get_logger(__name__)

_DEFAULT_EVENTS = [
    "plan_completed",
    "plan_failed",
    "plan_paused",
    "approval_required",
    "design_ready",
]
_DEFAULT_COOLDOWN = 300.0
_AUDIT_FILENAME = "notifications.jsonl"
_POST_TIMEOUT = 10.0


def _feishu_sign(secret: str, timestamp: int) -> str:
    """飞书自定义机器人签名：HMAC-SHA256(key=timestamp\\nsecret)，无消息体，base64。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _dingtalk_sign(secret: str, timestamp_ms: int) -> str:
    """钉钉自定义机器人签名：HMAC-SHA256(key=secret, data=timestamp\\nsecret)，base64+urlencode。"""
    string_to_sign = f"{timestamp_ms}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return urllib.parse.quote_plus(base64.b64encode(digest))


def _build_providers(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 notify 配置收集已填 webhook/token 的通道；空值跳过。"""
    providers: List[Dict[str, Any]] = []
    feishu = cfg.get("feishu") or {}
    if str(feishu.get("webhook") or "").strip():
        providers.append(
            {"type": "feishu", "name": "feishu", "webhook": str(feishu["webhook"]).strip(),
             "secret": str(feishu.get("secret") or "")}
        )
    dingtalk = cfg.get("dingtalk") or {}
    if str(dingtalk.get("webhook") or "").strip():
        providers.append(
            {"type": "dingtalk", "name": "dingtalk", "webhook": str(dingtalk["webhook"]).strip(),
             "secret": str(dingtalk.get("secret") or "")}
        )
    wecom = cfg.get("wecom") or {}
    if str(wecom.get("webhook") or "").strip():
        providers.append({"type": "wecom", "name": "wecom", "webhook": str(wecom["webhook"]).strip()})
    telegram = cfg.get("telegram") or {}
    if str(telegram.get("bot_token") or "").strip() and str(telegram.get("chat_id") or "").strip():
        providers.append(
            {"type": "telegram", "name": "telegram",
             "bot_token": str(telegram["bot_token"]).strip(), "chat_id": str(telegram["chat_id"]).strip()}
        )
    webhook = cfg.get("webhook") or {}
    if str(webhook.get("url") or "").strip():
        providers.append({"type": "webhook", "name": "webhook", "webhook": str(webhook["url"]).strip()})
    return providers


def _render_body(provider: Dict[str, Any], event: str, title: str, text: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    full = f"【{title}】\n{text}" if title else text
    ptype = provider["type"]
    if ptype == "feishu":
        body: Dict[str, Any] = {"msg_type": "text", "content": {"text": full}}
        if provider.get("secret"):
            ts = int(time.time())
            body["timestamp"] = ts
            body["sign"] = _feishu_sign(provider["secret"], ts)
        return body
    if ptype == "dingtalk":
        return {"msgtype": "text", "text": {"content": full}}
    if ptype == "wecom":
        return {"msgtype": "text", "text": {"content": full}}
    if ptype == "telegram":
        return {"chat_id": provider["chat_id"], "text": full}
    # 通用 webhook：结构化 JSON 原样交付
    clean_payload = {k: v for k, v in (payload or {}).items() if not k.startswith("_")}
    return {"event": event, "title": title, "text": text, "payload": clean_payload}


class Notifier:
    """IM 通知器：多通道 POST + cooldown 去重 + 本地审计，fail-open。"""

    def __init__(
        self,
        config: Dict[str, Any],
        audit_path: Optional[Path] = None,
        client_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        cfg = config or {}
        self.enabled = cfg.get("enabled") is not False
        events = cfg.get("events") or _DEFAULT_EVENTS
        self.events = {str(e) for e in events}
        try:
            self.cooldown = float(cfg.get("cooldown_seconds", _DEFAULT_COOLDOWN) or 0)
        except (TypeError, ValueError):
            self.cooldown = _DEFAULT_COOLDOWN
        self.providers = _build_providers(cfg)
        self.audit_path = audit_path
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=_POST_TIMEOUT, proxy=None))
        self._last_sent: Dict[str, float] = {}

    @classmethod
    def from_config(
        cls,
        config: Dict[str, Any],
        audit_dir: Optional[Path] = None,
        client_factory: Optional[Callable[[], Any]] = None,
    ) -> "Notifier":
        cfg = (config or {}).get("notify") or {}
        audit_path = Path(audit_dir) / _AUDIT_FILENAME if audit_dir is not None else None
        return cls(cfg, audit_path=audit_path, client_factory=client_factory)

    # ---- 判定 ----

    def _should_send(self, event: str, dedup_key: Optional[str]) -> tuple[Optional[str], str]:
        if not self.enabled:
            return None, "notify disabled"
        if event != "test" and self.events and event not in self.events:
            return None, f"event not subscribed: {event}"
        key = f"{event}:{dedup_key or '-'}"
        if event != "test":
            now = time.time()
            last = self._last_sent.get(key)
            if last is not None and now - last < self.cooldown:
                return None, f"cooldown（剩 {self.cooldown - (now - last):.0f}s）"
        return key, "ok"

    # ---- 发送 ----

    async def send(
        self,
        event: str,
        title: str,
        text: str,
        payload: Optional[Dict[str, Any]] = None,
        dedup_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        key, reason = self._should_send(event, dedup_key)
        if key is None:
            logger.debug("通知跳过 [{}]: {}", event, reason)
            return []
        self._last_sent[key] = time.time()

        results: List[Dict[str, Any]] = []
        if self.providers:
            for provider in self.providers:
                results.append(await self._send_one(provider, event, title, text, payload))
        else:
            results.append({"provider": "audit", "ok": True, "status": "no channel configured"})
        self._audit(event, title, text, payload, results)
        logger.info(
            "通知 [{}] 发送完成: {}",
            event,
            ", ".join(f"{r['provider']}={'ok' if r.get('ok') else 'fail'}" for r in results),
        )
        return results

    async def _send_one(
        self, provider: Dict[str, Any], event: str, title: str, text: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        name = provider["name"]
        try:
            body = _render_body(provider, event, title, text, payload)
            if provider["type"] == "telegram":
                url = f"https://api.telegram.org/bot{provider['bot_token']}/sendMessage"
            else:
                url = provider["webhook"]
            if provider["type"] == "dingtalk" and provider.get("secret"):
                ts = str(round(time.time() * 1000))
                sign = _dingtalk_sign(provider["secret"], int(ts))
                url += ("&" if "?" in url else "?") + f"timestamp={ts}&sign={sign}"
            async with self.client_factory() as client:
                resp = await client.post(url, json=body)
            ok = resp.is_success
            detail = ""
            try:
                data = resp.json()
                if provider["type"] in ("dingtalk", "wecom") and data.get("errcode") not in (None, 0):
                    ok, detail = False, str(data.get("errmsg", ""))[:120]
                elif provider["type"] == "feishu" and data.get("code") not in (None, 0):
                    ok, detail = False, str(data.get("msg", ""))[:120]
                elif provider["type"] == "telegram" and data.get("ok") is False:
                    ok, detail = False, str(data.get("description", ""))[:120]
            except Exception:
                pass
            result: Dict[str, Any] = {"provider": name, "ok": ok, "status": resp.status_code}
            if detail:
                result["detail"] = detail
            return result
        except Exception as exc:
            return {"provider": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:160]}

    # ---- 审计 ----

    def _audit(
        self,
        event: str,
        title: str,
        text: str,
        payload: Optional[Dict[str, Any]],
        results: List[Dict[str, Any]],
    ) -> None:
        if self.audit_path is None:
            return
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "event": event,
                "title": title,
                "text": text[:500],
                "payload": payload or {},
                "results": results,
            }
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("通知审计写入失败: {}", exc)


__all__ = ["Notifier", "_feishu_sign", "_dingtalk_sign"]
