from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Any

from core.sdk import AutomationProvider, BasePlugin, OperationResult

DEFAULT_EVENTS = ["task.completed", "task.failed"]

EVENT_CATEGORY = {
    "task.completed": "任务完成",
    "task.failed": "任务失败",
    "task.started": "任务开始",
    "task.created": "任务创建",
}

CATEGORY_EMOJI = {
    "任务完成": "✅",
    "任务失败": "❌",
    "任务开始": "▶️",
    "任务创建": "📝",
    "系统通知": "🔔",
}

MERGE_WINDOW_SECONDS = 5


class DingdingBotAutomationPlugin(BasePlugin, AutomationProvider):
    plugin_id = "automation.dingding_bot"
    plugin_name = "钉钉 Bot"
    plugin_version = "1.0.0"

    def __init__(self) -> None:
        self._runtime_config: dict[str, Any] = {}
        self._pending: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._flush_timer: threading.Timer | None = None

    def set_runtime_config(self, config: dict[str, Any]) -> None:
        self._runtime_config = self._normalize_runtime_config(config)

    def validate_runtime_config(self, config: dict[str, Any]) -> OperationResult:
        normalized = self._normalize_runtime_config(config)
        errors: list[str] = []
        if not str(normalized.get("webhook_url") or "").strip():
            errors.append("缺少必填配置：webhook_url")
        if errors:
            return OperationResult(success=False, message="插件配置校验失败。", errors=errors)
        return OperationResult(success=True, message="插件配置校验通过。", data=normalized)

    def health(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "message": "钉钉 Bot 通知插件运行正常。",
            "details": {
                "configured": self._is_configured(),
                "subscribed_events": self.subscribed_events(),
            },
        }

    def subscribed_events(self) -> list[str]:
        raw = str(self._runtime_config.get("enabled_events") or ",".join(DEFAULT_EVENTS))
        values = [item.strip() for item in raw.split(",") if item.strip()]
        return values or list(DEFAULT_EVENTS)

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        event_type = str(event.get("event_type") or "unknown")
        title, content = self._build_message(event)

        if self._is_configured():
            category = self._category_for(event_type)
            with self._lock:
                self._pending.append({
                    "category": category,
                    "title": title,
                    "content": content,
                    "time": time.strftime("%H:%M:%S"),
                })
                if self._flush_timer is not None:
                    self._flush_timer.cancel()
                self._flush_timer = threading.Timer(MERGE_WINDOW_SECONDS, self._flush_pending)
                self._flush_timer.daemon = True
                self._flush_timer.start()

        return OperationResult(
            success=True,
            message=f"{self.plugin_name} 已处理事件：{event_type}",
            data={
                "event_type": event_type,
                "title": title,
                "content": content,
                "configured": self._is_configured(),
            },
        ).model_dump(mode="json")

    def _flush_pending(self) -> None:
        with self._lock:
            if not self._pending:
                return
            items = list(self._pending)
            self._pending = []
            self._flush_timer = None

        try:
            title, body = self._format_merged(items)
            self._send_to_dingtalk(title, body)
        except Exception:
            pass

    @staticmethod
    def _format_merged(items: list[dict[str, Any]]) -> tuple[str, str]:
        if len(items) == 1:
            item = items[0]
            emoji = CATEGORY_EMOJI.get(item["category"], "🔔")
            return f"{emoji} {item['title']}", item["content"]

        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            grouped.setdefault(item["category"], []).append(item)

        summary_parts = []
        for category in ["任务完成", "任务失败", "任务开始", "任务创建", "系统通知"]:
            if category in grouped:
                count = len(grouped[category])
                emoji = CATEGORY_EMOJI.get(category, "🔔")
                summary_parts.append(f"{emoji}{category}×{count}")

        title = f"📢 T3 通知 ({len(items)}条) " + " ".join(summary_parts)

        lines: list[str] = []
        for category, cat_items in grouped.items():
            emoji = CATEGORY_EMOJI.get(category, "🔔")
            lines.append(f"【{emoji} {category}】")
            for idx, item in enumerate(cat_items, 1):
                lines.append(f"  {idx}. [{item['time']}] {item['content']}")
            lines.append("")

        return title, "\n".join(lines).rstrip()

    def _is_configured(self) -> bool:
        return bool(str(self._runtime_config.get("webhook_url") or "").strip())

    def _send_to_dingtalk(self, title: str, content: str) -> None:
        webhook_url = str(self._runtime_config.get("webhook_url") or "").strip()
        secret = str(self._runtime_config.get("secret") or "").strip()
        at_mobiles_raw = str(self._runtime_config.get("at_mobiles") or "").strip()
        at_mobiles = [m.strip() for m in at_mobiles_raw.split(",") if m.strip()] if at_mobiles_raw else []

        if secret:
            timestamp = str(round(time.time() * 1000))
            secret_enc = secret.encode("utf-8")
            string_to_sign = f"{timestamp}\n{secret}"
            hmac_code = hmac.new(
                secret_enc, string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
            ).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
            separator = "&" if "?" in webhook_url else "?"
            webhook_url = f"{webhook_url}{separator}timestamp={timestamp}&sign={sign}"

        payload = {
            "msgtype": "text",
            "text": {"content": f"{title}\n{content}"},
            "at": {"atMobiles": at_mobiles, "isAtAll": False},
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()

    @staticmethod
    def _category_for(event_type: str) -> str:
        return EVENT_CATEGORY.get(event_type, "系统通知")

    @staticmethod
    def _normalize_runtime_config(config: dict[str, Any] | None) -> dict[str, Any]:
        return dict(config or {})

    @staticmethod
    def _build_message(event: dict[str, Any]) -> tuple[str, str]:
        event_type = str(event.get("event_type") or "unknown")
        payload = dict(event.get("payload") or {})
        task_name = str(
            payload.get("task_name")
            or payload.get("title")
            or event.get("task_id")
            or "未命名任务"
        )
        summary = str(payload.get("summary") or "").strip()
        error_message = str(
            payload.get("error_message")
            or payload.get("error")
            or summary
            or "未知错误"
        ).strip()

        if event_type == "task.completed":
            content = f"{task_name} 已执行完成。"
            if summary:
                content = f"{content} {summary}"
            return "[任务完成]", content

        if event_type == "task.failed":
            return "[任务失败]", f"{task_name} 执行失败：{error_message}"

        if event_type == "task.started":
            return "[任务开始]", f"{task_name} 已开始执行。"

        if event_type == "task.created":
            return "[任务创建]", f"已创建新任务：{task_name}"

        if summary:
            return "[系统通知]", f"{task_name}：{summary}"
        return "[系统通知]", f"{task_name} 触发事件：{event_type}"


plugin = DingdingBotAutomationPlugin()
