"""微信推送：支持 Server酱 / PushPlus / 企业微信机器人 / 控制台打印。"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger("us-stock-news")


def _post(url: str, payload: dict, timeout: int = 15) -> tuple[bool, str]:
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        return r.ok, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _send_serverchan(sendkey: str, title: str, content: str) -> tuple[bool, str]:
    if not sendkey:
        return False, "未配置 serverchan.sendkey"
    ok, msg = _post(f"https://sctapi.ftqq.com/{sendkey}.send",
                    {"title": title, "desp": content})
    return ok, msg


def _send_pushplus(token: str, title: str, content: str) -> tuple[bool, str]:
    if not token:
        return False, "未配置 pushplus.token"
    ok, msg = _post("https://www.pushplus.plus/send",
                    {"token": token, "title": title,
                     "content": content, "template": "markdown"})
    return ok, msg


def _send_wecom(webhook: str, content: str) -> tuple[bool, str]:
    if not webhook:
        return False, "未配置 wecom.webhook"
    # 企业微信 markdown 单条上限 4096 字节，超长截断
    if len(content.encode("utf-8")) > 4000:
        content = content[:1800] + "\n\n…（内容过长已截断）"
    ok, msg = _post(webhook, {"msgtype": "markdown", "markdown": {"content": content}})
    return ok, msg


def push(cfg: dict, title: str, content: str) -> tuple[bool, str]:
    """按优先级尝试推送，返回 (是否成功, 说明)。"""
    push_cfg = cfg.get("push", {})
    channel = (push_cfg.get("channel") or "console").lower()

    if channel == "console":
        print("\n" + "=" * 50)
        print("【待推送内容】")
        print("标题:", title)
        print("-" * 50)
        print(content)
        print("=" * 50 + "\n")
        return True, "console 模式：已打印到屏幕（未真正推送）"

    # 显式指定 channel
    if channel == "serverchan":
        return _send_serverchan(push_cfg.get("serverchan", {}).get("sendkey", ""), title, content)
    if channel == "pushplus":
        return _send_pushplus(push_cfg.get("pushplus", {}).get("token", ""), title, content)
    if channel == "wecom":
        return _send_wecom(push_cfg.get("wecom", {}).get("webhook", ""), content)

    # 未识别或想做兜底：按 serverchan > pushplus > wecom 顺序尝试
    for name, fn in (
        ("serverchan", lambda: _send_serverchan(push_cfg.get("serverchan", {}).get("sendkey", ""), title, content)),
        ("pushplus", lambda: _send_pushplus(push_cfg.get("pushplus", {}).get("token", ""), title, content)),
        ("wecom", lambda: _send_wecom(push_cfg.get("wecom", {}).get("webhook", ""), content)),
    ):
        ok, msg = fn()
        if ok:
            return True, f"通过 {name} 推送成功"
    return False, "所有已配置渠道均不可用，请检查 config.yaml 中的 token/webhook"
