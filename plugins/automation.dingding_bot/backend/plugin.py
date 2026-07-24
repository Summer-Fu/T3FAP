from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Any

from core.sdk import AutomationProvider, BasePlugin, HealthReport, OperationResult

DEFAULT_EVENTS = ["task.completed", "task.failed", "task.started"]


class DingdingBotPlugin(BasePlugin, AutomationProvider):
    plugin_id = "automation.dingding_bot"
    plugin_name = "钉钉 Bot"
    plugin_version = "1.0.0"

    def __init__(self) -> None:
        self._runtime_config: dict[str, Any] = {}

    def set_runtime_config(self, config: dict[str, Any]) -> None:
        self._runtime_config = dict(config or {})

    def validate_runtime_config(self, config: dict[str, Any]) -> OperationResult:
        errors: list[str] = []
        webhook = str(config.get("webhook_url") or "").strip()
        if not webhook:
            errors.append("缺少必填配置：机器人 Webhook 地址")
        elif not webhook.startswith("http"):
            errors.append("Webhook 地址格式不正确，应以 http 开头")
        secret = str(config.get("secret") or "").strip()
        if secret and not secret.startswith("SEC"):
            errors.append("加签密钥格式不正确，应以 SEC 开头")
        timeout = int(config.get("timeout", 10) or 10)
        if timeout < 1 or timeout > 60:
            errors.append("请求超时应在 1-60 秒之间")
        if errors:
            return OperationResult(
                success=False,
                message="插件配置校验失败。",
                errors=errors,
            )
        return OperationResult(success=True, message="插件配置校验通过。")

    def health(self, ctx: dict[str, Any]) -> HealthReport:
        configured = self._is_configured()
        details: dict[str, Any] = {
            "configured": configured,
            "subscribed_events": self.subscribed_events(),
            "message_type": self._runtime_config.get("message_type", "markdown"),
            "has_secret": bool(str(self._runtime_config.get("secret") or "").strip()),
            "at_all": bool(self._runtime_config.get("at_all", False)),
            "at_count": len(self._get_at_mobiles()),
        }
        if configured:
            try:
                parsed = urllib.parse.urlparse(
                    str(self._runtime_config.get("webhook_url") or "")
                )
                details["webhook_host"] = parsed.hostname or "unknown"
            except Exception:
                details["webhook_host"] = "unknown"
        return HealthReport(
            status="ok" if configured else "degraded",
            message=f"{self.plugin_name} {'运行正常' if configured else '尚未配置 Webhook 地址'}。",
            details=details,
        )

    def subscribed_events(self) -> list[str]:
        raw = str(self._runtime_config.get("enabled_events") or "").strip()
        if not raw:
            return list(DEFAULT_EVENTS)
        values = [item.strip() for item in raw.split(",") if item.strip()]
        return values or list(DEFAULT_EVENTS)

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self._is_enabled():
            return OperationResult(
                success=True,
                message="钉钉 Bot 未启用，已跳过。",
            ).model_dump(mode="json")

        if not self._is_configured():
            return OperationResult(
                success=False,
                message="钉钉 Bot 尚未配置 Webhook 地址，无法发送消息。",
            ).model_dump(mode="json")

        event_type = str(event.get("event_type") or "unknown")
        title, text, markdown = self._build_message(event)
        message_type = str(
            self._runtime_config.get("message_type", "markdown") or "markdown"
        )

        if message_type == "markdown":
            content, msg_type = markdown, "markdown"
        else:
            content, msg_type = text, "text"

        try:
            status_code, response_body = self._send_dingtalk_message(
                msg_type=msg_type,
                title=title,
                content=content,
                at_mobiles=self._get_at_mobiles(),
                at_all=bool(self._runtime_config.get("at_all", False)),
            )
        except Exception as exc:
            return OperationResult(
                success=False,
                message=f"发送钉钉消息失败：{exc}",
                errors=[str(exc)],
                data={"event_type": event_type, "title": title},
            ).model_dump(mode="json")

        try:
            resp = json.loads(response_body) if response_body else {}
        except json.JSONDecodeError:
            resp = {"raw": response_body}

        errcode = int(resp.get("errcode", -1) or -1)
        errmsg = str(resp.get("errmsg", "") or "")

        if errcode == 0:
            return OperationResult(
                success=True,
                message=f"钉钉消息发送成功：{title}",
                data={"event_type": event_type, "title": title, "errcode": 0},
            ).model_dump(mode="json")

        error_hint = self._get_error_hint(errcode, errmsg)
        return OperationResult(
            success=False,
            message=f"钉钉消息发送失败（errcode={errcode}）：{errmsg}。{error_hint}",
            errors=[f"errcode={errcode}, errmsg={errmsg}", error_hint],
            data={
                "event_type": event_type,
                "title": title,
                "errcode": errcode,
                "errmsg": errmsg,
            },
        ).model_dump(mode="json")

    def _is_enabled(self) -> bool:
        return bool(self._runtime_config.get("enabled", True))

    def _is_configured(self) -> bool:
        return bool(str(self._runtime_config.get("webhook_url") or "").strip())

    def _get_at_mobiles(self) -> list[str]:
        raw = str(self._runtime_config.get("at_mobiles") or "").strip()
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    def _send_dingtalk_message(
        self,
        msg_type: str,
        title: str,
        content: str,
        at_mobiles: list[str] | None = None,
        at_all: bool = False,
    ) -> tuple[int, str]:
        webhook_url = str(self._runtime_config.get("webhook_url") or "").strip()
        secret = str(self._runtime_config.get("secret") or "").strip()
        timeout = int(self._runtime_config.get("timeout", 10) or 10)

        if secret:
            timestamp = str(round(time.time() * 1000))
            sign = self._compute_sign(timestamp, secret)
            separator = "&" if "?" in webhook_url else "?"
            webhook_url = f"{webhook_url}{separator}timestamp={timestamp}&sign={sign}"

        payload: dict[str, Any] = {
            "msgtype": msg_type,
            "at": {
                "atMobiles": at_mobiles or [],
                "isAtAll": at_all,
            },
        }

        if msg_type == "markdown":
            payload["markdown"] = {"title": title, "text": content}
        else:
            payload["text"] = {"content": content}

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return resp.status, body
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            return e.code, body

    @staticmethod
    def _compute_sign(timestamp: str, secret: str) -> str:
        secret_enc = secret.encode("utf-8")
        string_to_sign = f"{timestamp}\n{secret}"
        string_to_sign_enc = string_to_sign.encode("utf-8")
        hmac_code = hmac.new(
            secret_enc, string_to_sign_enc, digestmod=hashlib.sha256
        ).digest()
        return urllib.parse.quote_plus(base64.b64encode(hmac_code))

    @staticmethod
    def _build_message(event: dict[str, Any]) -> tuple[str, str, str]:
        event_type = str(event.get("event_type") or "unknown")
        payload = dict(event.get("payload") or {})
        task_name = str(
            payload.get("task_name")
            or payload.get("title")
            or event.get("task_id")
            or "未命名任务"
        ).strip()
        summary = str(payload.get("summary") or "").strip()
        error_message = str(
            payload.get("error_message")
            or payload.get("error")
            or summary
            or "未知错误"
        ).strip()
        task_id = str(event.get("task_id") or payload.get("task_id") or "").strip()
        tid_line = f"**任务 ID**：{task_id}\n\n" if task_id else ""

        if event_type == "task.completed":
            title = "✅ 任务完成"
            detail = summary if summary else "任务已成功执行完成。"
            return (
                title,
                f"[任务完成] {task_name} - {detail}",
                f"## ✅ 任务完成\n\n**任务名称**：{task_name}\n\n**执行结果**：{detail}\n\n{tid_line}---\n*来自 T3FAP 钉钉 Bot*",
            )

        if event_type == "task.failed":
            title = "❌ 任务失败"
            return (
                title,
                f"[任务失败] {task_name} - {error_message}",
                f"## ❌ 任务失败\n\n**任务名称**：{task_name}\n\n**错误信息**：{error_message}\n\n{tid_line}---\n*来自 T3FAP 钉钉 Bot*",
            )

        if event_type == "task.started":
            title = "▶️ 任务开始"
            return (
                title,
                f"[任务开始] {task_name} 已开始执行。",
                f"## ▶️ 任务开始\n\n**任务名称**：{task_name}\n\n**状态**：已开始执行\n\n{tid_line}---\n*来自 T3FAP 钉钉 Bot*",
            )

        if event_type == "task.updated":
            title = "🔄 任务更新"
            detail = summary if summary else "任务状态已更新。"
            return (
                title,
                f"[任务更新] {task_name} - {detail}",
                f"## 🔄 任务更新\n\n**任务名称**：{task_name}\n\n**更新内容**：{detail}\n\n{tid_line}---\n*来自 T3FAP 钉钉 Bot*",
            )

        title = "📢 系统通知"
        detail = summary if summary else f"触发事件：{event_type}"
        return (
            title,
            f"[系统通知] {task_name} - {detail}",
            f"## 📢 系统通知\n\n**来源**：{task_name}\n\n**事件类型**：{event_type}\n\n**内容**：{detail}\n\n---\n*来自 T3FAP 钉钉 Bot*",
        )

    @staticmethod
    def _get_error_hint(errcode: int, errmsg: str) -> str:
        hints: dict[int, str] = {
            310000: "请检查 Webhook 地址中的 access_token 是否正确。",
            310001: "机器人已被移除或停用，请检查群内机器人状态。",
            300001: "请求签名不匹配，请检查加签密钥(Secret)是否正确。",
            300002: "IP 地址不在白名单内，请检查机器人的 IP 白名单设置。",
            310004: "机器人不在群内或群组已解散。",
            310005: "发送频率超限，请稍后重试。",
            310006: "消息内容过长，请精简后重试。",
            310007: "消息中缺少关键词。如果机器人设置了自定义关键词安全策略，消息内容必须包含至少一个关键词。",
            310012: "消息内容包含违规信息，请检查。",
        }
        if errcode in hints:
            return hints[errcode]
        em = errmsg.lower()
        if "sign not match" in em:
            return "签名不匹配，请检查加签密钥(Secret)是否正确，或改为关键词模式。"
        if "keywords not in content" in em:
            return "消息不包含关键词。请检查机器人的自定义关键词设置。"
        if "ip" in em and "not" in em:
            return "IP 地址不在白名单内。请检查机器人的 IP 白名单设置。"
        if "invalid" in em and "token" in em:
            return "Webhook 地址无效，请检查 access_token 是否正确。"
        return "请检查机器人设置（关键词/加签/IP白名单）和 Webhook 地址是否正确。"


plugin = DingdingBotPlugin()
