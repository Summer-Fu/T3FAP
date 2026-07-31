from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any

import httpx

from core.sdk import AutomationProvider, BasePlugin, OperationResult

DEFAULT_EVENTS = ["task.completed", "task.failed"]

EVENT_CATEGORY = {
    "task.completed": "任务完成",
    "task.failed": "任务失败",
    "task.started": "任务开始",
    "task.created": "任务创建",
    "task.canceled": "任务取消",
    "task.transfer": "转存任务",
    "task.drive_download": "网盘下载",
    "task.video_download": "视频下载",
    "task.strm": "STRM生成",
}

CATEGORY_EMOJI = {
    "任务完成": "✅",
    "任务失败": "❌",
    "任务开始": "▶️",
    "任务取消": "⏹️",
    "任务创建": "📝",
    "转存任务": "📦",
    "网盘下载": "⬇️",
    "视频下载": "🎬",
    "STRM生成": "📺",
    "系统通知": "🔔",
}

RESOURCE_KIND_LABEL = {
    "official_strm": "官方STRM",
    "official_download": "官方下载",
    "share_transfer": "分享转存",
    "share_download": "分享下载",
}


class DingdingBotAutomationPlugin(AutomationProvider, BasePlugin):
    plugin_id = "automation.dingding_bot"
    plugin_name = "钉钉 Bot"
    plugin_version = "1.9.4"

    def __init__(self) -> None:
        self._runtime_config: dict[str, Any] = {}

    def _resolve_config(self, override: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = dict(self._runtime_config)
        if override:
            merged.update(dict(override or {}))
        return merged

    def set_runtime_config(self, config: dict[str, Any]) -> None:
        normalized = self._normalize_runtime_config(config)
        self._runtime_config = normalized
        keys = list(normalized.keys())
        has_webhook = bool(str(normalized.get("webhook_url") or "").strip())
        print(f"[钉钉Bot] set_runtime_config: keys={keys}, webhook已配置={has_webhook}")

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

    def subscribed_events(self, config: dict[str, Any] | None = None) -> list[str]:
        cfg = self._resolve_config(config)
        raw = str(cfg.get("enabled_events") or ",".join(DEFAULT_EVENTS))
        values = [item.strip() for item in raw.split(",") if item.strip()]
        return values or list(DEFAULT_EVENTS)

    def test_notification(self, payload: dict[str, Any]) -> dict[str, Any]:
        cfg = self._resolve_config(payload)
        if not self._is_configured(cfg):
            return OperationResult(
                success=False,
                message="通知测试失败：缺少 webhook_url 配置。",
            ).model_dump(mode="json")
        try:
            self._send_to_dingtalk(
                "🔔 [测试] 钉钉 Bot 通知",
                "这是一条测试消息。如果收到，说明钉钉 Bot 配置正确、推送链路正常。",
                config=cfg,
            )
            return OperationResult(
                success=True,
                message="通知测试成功，请检查钉钉群是否收到测试消息。",
            ).model_dump(mode="json")
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return OperationResult(
                success=False,
                message=f"通知测试失败：{exc}",
            ).model_dump(mode="json")

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        event_type = str(event.get("event_type") or "unknown")
        print(f"[钉钉Bot] 收到事件: {event_type}")

        title, content = self._build_message(event)

        cfg = self._resolve_config()
        configured = bool(str(cfg.get("webhook_url") or "").strip())
        print(f"[钉钉Bot] 配置状态: {'已配置' if configured else '未配置'}, keys={list(cfg.keys())}")

        if configured:
            try:
                category = self._category_for(event_type)
                emoji = CATEGORY_EMOJI.get(category, "🔔")
                full_title = f"{emoji} {title}"
                print(f"[钉钉Bot] 准备发送: {full_title}")
                self._send_to_dingtalk(full_title, content)
                print(f"[钉钉Bot] 发送成功")
            except Exception as exc:
                import traceback
                print(f"[钉钉Bot] 发送失败: {exc}")
                traceback.print_exc()

        return OperationResult(
            success=True,
            message=f"{self.plugin_name} 已处理事件：{event_type}",
            data={
                "event_type": event_type,
                "title": title,
                "content": content,
                "configured": configured,
            },
        ).model_dump(mode="json")

    def _is_configured(self, config: dict[str, Any] | None = None) -> bool:
        cfg = self._resolve_config(config)
        return bool(str(cfg.get("webhook_url") or "").strip())

    def _send_to_dingtalk(self, title: str, content: str, config: dict[str, Any] | None = None) -> None:
        cfg = self._resolve_config(config)
        webhook_url = str(cfg.get("webhook_url") or "").strip()
        secret = str(cfg.get("secret") or "").strip()
        at_mobiles_raw = str(cfg.get("at_mobiles") or "").strip()
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

        with httpx.Client(timeout=10) as client:
            resp = client.post(webhook_url, json=payload)
            resp.raise_for_status()
            result = resp.json()
            if result.get("errcode") != 0:
                raise RuntimeError(f"钉钉返回错误: {result}")

    def _category_for(self, event_type: str) -> str:
        return EVENT_CATEGORY.get(event_type, "系统通知")

    @staticmethod
    def _normalize_runtime_config(config: dict[str, Any] | None) -> dict[str, Any]:
        return dict(config or {})

    def _build_message(self, event: dict[str, Any]) -> tuple[str, str]:
        event_type = str(event.get("event_type") or "unknown")
        source = str(event.get("source") or "core")
        plugin_id = str(event.get("plugin_id") or "")
        task_type = str(event.get("task_type") or "")
        status = str(event.get("status") or "unknown")

        payload = dict(event.get("payload") or {})
        output_payload = dict(payload.get("output_payload") or {})
        input_payload = dict(payload.get("input_payload") or {})

        task_name = str(
            payload.get("task_name")
            or payload.get("title")
            or input_payload.get("task_name")
            or event.get("task_id")
            or "未命名任务"
        )
        task_id = str(event.get("task_id") or payload.get("task_id") or "")
        execution_id = str(event.get("execution_id") or payload.get("execution_id") or "")

        template_key = str(payload.get("template_key") or input_payload.get("template_key") or "")
        trigger_source = str(payload.get("trigger_source") or "")
        triggered_by = str(payload.get("triggered_by") or "")

        platform_name = str(payload.get("platform_name") or input_payload.get("platform_name") or "").strip()
        media_category = str(payload.get("media_category") or input_payload.get("media_category") or "").strip()
        sub_kind = str(payload.get("subscription_kind") or input_payload.get("subscription_kind") or "").strip()
        sub_kind_label = RESOURCE_KIND_LABEL.get(sub_kind, sub_kind)
        catalog_label = str(payload.get("catalog_source_label") or input_payload.get("catalog_source_label") or "").strip()
        owner_plugin_id = str(payload.get("owner_plugin_id") or "")

        summary = str(payload.get("summary") or output_payload.get("summary") or "").strip()
        detail_message = str(payload.get("detail_message") or payload.get("message") or "").strip()
        error_text = str(
            payload.get("error_text")
            or payload.get("error_message")
            or payload.get("error")
            or ""
        ).strip()

        def _count(key: str) -> int | None:
            v = output_payload.get(key)
            if v is None:
                v = payload.get(key)
            try:
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        saved_count = _count("saved_count")
        skipped_count = _count("skipped_count")
        transferred_count = _count("transferred_count")
        renamed_count = _count("renamed_count")
        filtered_count = _count("filtered_count")
        cleared_payload_count = _count("cleared_payload_count")
        generated_item_ids_count = _count("generated_item_ids_count")
        new_item_count = _count("new_item_count")
        last_new_data_count = _count("last_new_data_count")
        no_update_days = _count("no_update_days")

        duration_ms = _count("duration_ms")
        duration_text = ""
        if duration_ms is not None and duration_ms > 0:
            sec = duration_ms / 1000
            if sec >= 3600:
                duration_text = f"{sec / 3600:.1f}h"
            elif sec >= 60:
                duration_text = f"{sec / 60:.1f}m"
            else:
                duration_text = f"{sec:.1f}s"

        target_dir = str(
            payload.get("target_dir")
            or input_payload.get("target_dir")
            or ""
        ).strip()

        tags: list[str] = []
        if media_category:
            tags.append(media_category)
        if platform_name:
            tags.append(platform_name)
        if sub_kind_label:
            tags.append(sub_kind_label)
        if catalog_label:
            tags.append(catalog_label)
        if task_type and task_type not in tags:
            tags.append(task_type)
        tag_prefix = f"({'·'.join(tags)})" if tags else ""

        stat_parts: list[str] = []
        if saved_count is not None:
            stat_parts.append(f"转存成功 {saved_count} 项")
        if transferred_count is not None:
            stat_parts.append(f"已转存 {transferred_count} 项")
        if skipped_count is not None:
            stat_parts.append(f"跳过 {skipped_count} 项")
        if renamed_count is not None and renamed_count > 0:
            stat_parts.append(f"重命名 {renamed_count} 项")
        if filtered_count is not None and filtered_count > 0:
            stat_parts.append(f"过滤 {filtered_count} 项")
        if generated_item_ids_count is not None:
            stat_parts.append(f"生成 {generated_item_ids_count} 项")
        if new_item_count is not None:
            stat_parts.append(f"新增 {new_item_count} 项")
        if cleared_payload_count is not None and cleared_payload_count > 0:
            stat_parts.append(f"清理 {cleared_payload_count} 项")
        if last_new_data_count is not None:
            stat_parts.append(f"上次新增 {last_new_data_count} 项")
        if no_update_days is not None and no_update_days > 0:
            stat_parts.append(f"无更新 {no_update_days} 天")

        stat_line = "｜".join(stat_parts) if stat_parts else ""

        is_no_update = False
        if (
            (saved_count is not None and saved_count == 0)
            or (transferred_count is not None and transferred_count == 0)
        ) and (
            (skipped_count is not None and skipped_count > 0)
            or (no_update_days is not None and no_update_days > 0)
        ):
            is_no_update = True

        lines: list[str] = []
        if tag_prefix:
            lines.append(f"🏷️ {tag_prefix}")
        if duration_text:
            lines.append(f"⏱️ 耗时：{duration_text}")
        if stat_line:
            lines.append(f"📊 {stat_line}")
        if target_dir:
            lines.append(f"📁 目标：{target_dir}")
        if trigger_source:
            lines.append(f"⚡ 触发：{trigger_source}" + (f"（{triggered_by}）" if triggered_by else ""))
        if summary and summary != detail_message:
            lines.append(f"📝 {summary}")
        if detail_message:
            lines.append(f"💬 {detail_message}")
        if is_no_update:
            lines.append("🔄 本次无更新（已存在相同文件）")
        if task_id:
            lines.append(f"🆔 {task_id}")

        category = self._category_for(event_type)

        if event_type == "task.completed":
            if is_no_update:
                title = f"{task_name} · 无更新"
            else:
                title = f"{task_name} · 完成"
            return title, "\n".join(lines) if lines else f"{task_name} 已完成。"

        if event_type == "task.failed":
            title = f"{task_name} · 失败"
            if error_text:
                lines.insert(0, f"❌ {error_text}")
            return title, "\n".join(lines) if lines else f"{task_name} 执行失败：{error_text or '未知错误'}"

        if event_type == "task.canceled":
            title = f"{task_name} · 已取消"
            return title, "\n".join(lines) if lines else f"{task_name} 已取消。"

        if event_type == "task.started":
            title = f"{task_name} · 开始"
            return title, "\n".join(lines) if lines else f"{task_name} 已开始执行。"

        if event_type == "task.created":
            title = f"{task_name} · 已创建"
            return title, "\n".join(lines) if lines else f"已创建新任务：{task_name}"

        title = f"{task_name}"
        if summary and not lines:
            lines.append(summary)
        return title, "\n".join(lines) if lines else f"{task_name}：{event_type}"


plugin = DingdingBotAutomationPlugin()
