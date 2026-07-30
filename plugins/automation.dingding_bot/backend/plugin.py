"""钉钉 Bot 通知自动化插件后端入口。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from core.sdk import AutomationProvider, BasePlugin, OperationResult


DEFAULT_EVENTS = [
    "task.completed",
    "task.failed",
    "task.started",
    "task.created",
]

EVENT_CATEGORY: dict[str, str] = {
    "task.completed": "任务完成",
    "task.failed": "任务失败",
    "task.started": "任务开始",
    "task.created": "任务创建",
}

CATEGORY_EMOJI: dict[str, str] = {
    "任务完成": "✅",
    "任务失败": "❌",
    "任务开始": "▶️",
    "任务创建": "📝",
    "系统通知": "🔔",
}

TASK_CATEGORY: dict[str, str] = {
    "transfer": "转存",
    "download": "下载",
    "strm": "STRM 生成",
    "cache_keep": "缓存保持",
    "video_download": "影视下载",
    "transcode": "转码",
    "subtitle": "字幕",
    "search": "搜索",
}

SOURCE_LABEL: dict[str, str] = {
    "quark": "夸克网盘",
    "aliyun": "阿里云盘",
    "pan115": "115 网盘",
    "115": "115 网盘",
    "iqiyi": "爱奇艺",
    "bilibili": "哔哩哔哩",
    "youku": "优酷",
    "tencent": "腾讯视频",
    "mango": "芒果 TV",
    "migu": "咪咕视频",
    "cctv": "央视网",
    "360": "360 影视",
    "pansou": "盘搜",
    "official": "官方视频源",
    "drive": "网盘",
    "video": "影视资源",
    "catalog": "资源目录",
}


def _fmt_time(iso_str: str | None) -> str:
    if not iso_str:
        return ""
    try:
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso_str).astimezone()
        return dt.strftime("%m月%d日 %H点%M分")
    except Exception:
        return iso_str


def _parse_category_and_source(task_type: str | None, plugin_id: str | None) -> tuple[str, str]:
    raw = (task_type or plugin_id or "").strip()
    short = raw.replace("task.", "").replace("download.", "").replace("automation.", "")

    category = "任务"
    for key, label in TASK_CATEGORY.items():
        if key in short:
            category = label
            break

    source = "系统"
    for key, label in SOURCE_LABEL.items():
        if key in short.lower():
            source = label
            break

    if source == "系统" and plugin_id:
        for key, label in SOURCE_LABEL.items():
            if key in plugin_id.lower():
                source = label
                break

    return category, source


def _task_type_label(task_type: str | None, plugin_id: str | None) -> str:
    category, source = _parse_category_and_source(task_type, plugin_id)
    if category == "任务" and source == "系统":
        if task_type:
            return task_type.replace("_", " ").title()
        if plugin_id:
            short = plugin_id.replace("task.", "").replace("download.", "").replace("automation.", "")
            return short.replace("_", " ").title()
        return "任务"
    return f"{source} {category}"


class DingdingBotAutomationPlugin(BasePlugin, AutomationProvider):
    plugin_id = "automation.dingding_bot"
    plugin_name = "钉钉 Bot"
    plugin_version = "1.6.0"

    def __init__(self) -> None:
        self._runtime_config: dict[str, Any] = {}

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

    def test_notification(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = self._normalize_runtime_config(payload)
        webhook_url = str(config.get("webhook_url") or "").strip()
        if not webhook_url:
            return OperationResult(
                success=False,
                message="通知测试失败：缺少 webhook_url 配置。",
            ).model_dump(mode="json")
        try:
            self._send_to_dingtalk_with_config(
                config,
                "🔔 [测试] 钉钉 Bot 通知",
                "这是一条测试消息。如果收到，说明钉钉 Bot 配置正确、推送链路正常。",
            )
            return OperationResult(
                success=True,
                message="通知测试成功，请检查钉钉群是否收到测试消息。",
            ).model_dump(mode="json")
        except Exception as exc:
            return OperationResult(
                success=False,
                message=f"通知测试失败：{exc}",
            ).model_dump(mode="json")

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        event_type = str(event.get("event_type") or "unknown")
        title, content = self._build_message(event)

        if self._is_configured():
            try:
                self._send_to_dingtalk(title, content)
            except Exception:
                pass

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

    def _is_configured(self) -> bool:
        return bool(str(self._runtime_config.get("webhook_url") or "").strip())

    def _send_to_dingtalk(self, title: str, content: str) -> None:
        self._send_to_dingtalk_with_config(self._runtime_config, title, content)

    @staticmethod
    def _send_to_dingtalk_with_config(config: dict[str, Any], title: str, content: str) -> None:
        webhook_url = str(config.get("webhook_url") or "").strip()
        secret = str(config.get("secret") or "").strip()
        at_mobiles_raw = str(config.get("at_mobiles") or "").strip()
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
    def _extract_resource_name(event: dict[str, Any], payload: dict[str, Any]) -> str:
        data = payload.get("data") or {}
        artifacts = payload.get("artifacts") or []
        share_results = (data if isinstance(data, dict) else {}).get("share_results") or []

        if share_results and isinstance(share_results, list):
            name = share_results[0].get("share_name") or share_results[0].get("title")
            if name:
                return str(name)

        if artifacts and isinstance(artifacts, list):
            title = artifacts[0].get("title")
            if title:
                return str(title)

        for key in ("share_name", "resource_name", "name", "title", "task_name"):
            val = payload.get(key) or event.get(key) or data.get(key)
            if val:
                return str(val)

        task_id = str(event.get("task_id") or payload.get("task_id") or "")
        return f"任务 #{task_id}" if task_id else "未命名任务"

    @staticmethod
    def _collect_plugin_status(payload: dict[str, Any]) -> list[tuple[str, bool, str]]:
        data = payload.get("data") or {}
        post_plugins = (data if isinstance(data, dict) else {}).get("post_plugins") or []
        results: list[tuple[str, bool, str]] = []
        for p in post_plugins:
            pid = str(p.get("plugin_id") or "")
            success = bool(p.get("success"))
            message = str(p.get("message") or ("成功" if success else "失败"))
            label = pid.replace("automation.", "").replace("_", " ").title()
            results.append((label, success, message))
        return results

    @staticmethod
    def _normalize_runtime_config(config: dict[str, Any] | None) -> dict[str, Any]:
        return dict(config or {})

    @staticmethod
    def _build_message(event: dict[str, Any]) -> tuple[str, str]:
        event_type = str(event.get("event_type") or "unknown")
        payload = dict(event.get("payload") or {})
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            data = {}

        category = EVENT_CATEGORY.get(event_type, "系统通知")
        emoji = CATEGORY_EMOJI.get(category, "🔔")
        task_type = str(event.get("task_type") or "")
        plugin_id = str(event.get("plugin_id") or "")
        task_category, source = _parse_category_and_source(task_type, plugin_id)

        header = f"{emoji} T3 - {category}"
        resource_name = DingdingBotAutomationPlugin._extract_resource_name(event, payload)
        summary = str(payload.get("summary") or payload.get("message") or "").strip()
        created_at = str(event.get("created_at") or payload.get("created_at") or "")

        lines: list[str] = []
        lines.append(f"{source} {task_category}通知")
        lines.append(f"影视名称：{resource_name}")

        if event_type == "task.completed":
            saved_count = data.get("saved_count") or data.get("transferred_count")
            target_path = data.get("target_path") or data.get("save_path")
            if saved_count and target_path:
                action = task_category if task_category != "任务" else "处理"
                lines.append(f"已{action}：{saved_count}项到{target_path}")
            elif saved_count:
                action = task_category if task_category != "任务" else "处理"
                lines.append(f"已{action}：{saved_count}项")

            artifacts = payload.get("artifacts") or []
            if artifacts and isinstance(artifacts, list):
                latest = artifacts[-1] if artifacts else {}
                artifact_title = latest.get("title") or latest.get("name") or ""
                artifact_path = latest.get("path") or ""
                if artifact_title and artifact_title != resource_name:
                    lines.append(f"最新剧集名称：{artifact_title}")
                elif artifact_path:
                    lines.append(f"输出路径：{artifact_path}")

            if created_at:
                lines.append(f"剧集更新日期：{_fmt_time(created_at)}")

        elif event_type == "task.failed":
            error = str(
                payload.get("error_message")
                or payload.get("error")
                or summary
                or "未知错误"
            ).strip()
            lines.append(f"失败原因：{error}")
            if created_at:
                lines.append(f"失败时间：{_fmt_time(created_at)}")

        elif event_type == "task.started":
            if created_at:
                lines.append(f"开始时间：{_fmt_time(created_at)}")

        elif event_type == "task.created":
            if created_at:
                lines.append(f"创建时间：{_fmt_time(created_at)}")

        if summary and event_type not in ("task.completed",):
            lines.append(f"状态：{summary}")

        plugin_status = DingdingBotAutomationPlugin._collect_plugin_status(payload)
        if plugin_status:
            lines.append("插件状态：")
            for label, success, message in plugin_status:
                status_icon = "成功" if success else "失败"
                lines.append(f"  {label} {status_icon}")

        return header, "\n".join(lines)


plugin = DingdingBotAutomationPlugin()
