# -*- coding: utf-8 -*-
"""IM 通知通道测试：多通道渲染、cooldown 去重、事件过滤、fail-open、本地审计。"""
import base64
import hashlib
import hmac
import json
import sys
import urllib.parse
from pathlib import Path

import httpx
import pytest

PYTHON_DIR = Path(__file__).resolve().parent.parent.parent / "python"
sys.path.insert(0, str(PYTHON_DIR))

from notify import Notifier, _dingtalk_sign, _feishu_sign  # noqa: E402


def _make_notifier(cfg, handler, audit_path=None):
    transport = httpx.MockTransport(handler)
    return Notifier(
        cfg, audit_path=audit_path, client_factory=lambda: httpx.AsyncClient(transport=transport)
    )


def test_feishu_sign_matches_spec():
    ts = 1700000000
    expected = base64.b64encode(
        hmac.new(f"{ts}\nsec".encode(), digestmod=hashlib.sha256).digest()
    ).decode()
    assert _feishu_sign("sec", ts) == expected


def test_dingtalk_sign_deterministic():
    ts = 1700000000123
    s = f"{ts}\nsec"
    expected = urllib.parse.quote_plus(
        base64.b64encode(hmac.new(b"sec", s.encode(), hashlib.sha256).digest())
    )
    assert _dingtalk_sign("sec", ts) == expected


def test_build_providers_only_non_empty():
    n = Notifier(
        {
            "feishu": {"webhook": "https://feishu.example/hook", "secret": "s1"},
            "dingtalk": {"webhook": ""},
            "wecom": {"webhook": ""},
            "telegram": {"bot_token": "t", "chat_id": ""},
            "webhook": {"url": "https://generic.example/hook"},
        }
    )
    assert [p["type"] for p in n.providers] == ["feishu", "webhook"]


@pytest.mark.asyncio
async def test_send_posts_to_all_channels_and_audits(tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, json.loads(request.content.decode())))
        return httpx.Response(200, json={"code": 0, "errcode": 0})

    audit = tmp_path / "notifications.jsonl"
    n = _make_notifier(
        {
            "feishu": {"webhook": "https://feishu.example/hook"},
            "wecom": {"webhook": "https://wecom.example/hook"},
        },
        handler,
        audit_path=audit,
    )
    results = await n.send("plan_completed", "计划 Completed：abc", "正文", payload={"plan_id": "abc"})
    assert [r["provider"] for r in results] == ["feishu", "wecom"]
    assert all(r["ok"] for r in results)
    assert seen[0][1]["msg_type"] == "text"
    assert "计划 Completed：abc" in seen[0][1]["content"]["text"]
    assert seen[1][1]["msgtype"] == "text"
    rec = json.loads(audit.read_text(encoding="utf-8").strip())
    assert rec["event"] == "plan_completed" and rec["payload"]["plan_id"] == "abc"


@pytest.mark.asyncio
async def test_cooldown_dedups_same_key(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"errcode": 0})

    n = _make_notifier(
        {"wecom": {"webhook": "https://wecom.example/hook"}, "cooldown_seconds": 300}, handler
    )
    r1 = await n.send("plan_failed", "t", "x", dedup_key="p1")
    r2 = await n.send("plan_failed", "t", "x", dedup_key="p1")
    r3 = await n.send("plan_failed", "t", "x", dedup_key="p2")
    assert len(r1) == 1 and r2 == [] and len(r3) == 1


@pytest.mark.asyncio
async def test_event_filter_and_test_bypass(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"errcode": 0})

    n = _make_notifier(
        {"wecom": {"webhook": "https://wecom.example/hook"}, "events": ["plan_failed"]}, handler
    )
    assert await n.send("plan_completed", "t", "x", dedup_key="a") == []  # 未订阅事件
    r = await n.send("test", "t", "x")  # test 豁免事件过滤
    assert len(r) == 1


@pytest.mark.asyncio
async def test_fail_open_on_channel_error(tmp_path):
    def handler(request):
        raise httpx.ConnectError("boom")

    n = _make_notifier({"wecom": {"webhook": "https://wecom.example/hook"}}, handler)
    results = await n.send("plan_failed", "t", "x", dedup_key="p")
    assert results[0]["ok"] is False
    assert "ConnectError" in results[0]["error"]


@pytest.mark.asyncio
async def test_no_channel_falls_back_to_audit(tmp_path):
    n = _make_notifier({}, None, audit_path=tmp_path / "notifications.jsonl")
    results = await n.send("plan_failed", "t", "x", dedup_key="p")
    assert results == [{"provider": "audit", "ok": True, "status": "no channel configured"}]
    assert (tmp_path / "notifications.jsonl").exists()


@pytest.mark.asyncio
async def test_channel_business_error_counts_as_fail(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"errcode": 93000, "errmsg": "invalid webhook"})

    n = _make_notifier({"wecom": {"webhook": "https://wecom.example/hook"}}, handler)
    results = await n.send("plan_failed", "t", "x", dedup_key="p")
    assert results[0]["ok"] is False
    assert "invalid webhook" in results[0]["detail"]
