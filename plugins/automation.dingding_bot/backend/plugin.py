from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
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

DRIVE_LABEL_MAP = {
    "quark": "夸克",
    "drive.quark": "夸克",
    "drive.quark_tv": "夸克TV",
    "115": "115网盘",
    "drive.115": "115网盘",
    "aliyun": "阿里云盘",
    "drive.aliyun": "阿里云盘",
    "baidu": "百度网盘",
    "drive.baidu_open": "百度网盘",
    "xunlei": "迅雷",
    "drive.xunlei": "迅雷",
    "cloud189": "天翼云盘",
    "drive.cloud189": "天翼云盘",
    "139yun": "139云盘",
    "drive.139yun": "139云盘",
    "123pan": "123盘",
    "drive.123pan": "123盘",
    "guangya": "光呀",
    "drive.guangya": "光呀",
    "webdav": "WebDAV",
    "drive.webdav": "WebDAV",
    "local": "本地",
    "drive.local": "本地",
}

TASK_TYPE_LABEL = {
    "task.transfer": "网盘转存",
    "quark_transfer": "夸克转存",
    "task.drive_download": "网盘下载",
    "task.video_download": "视频下载",
    "task.strm": "STRM生成",
    "task.short_video": "短视频下载",
    "task.drive_cache_keep": "网盘缓存保活",
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".ts", ".flv", ".wmv",
    ".m4v", ".mpg", ".mpeg", ".m2ts", ".iso", ".rmvb", ".webm",
}

# 集数提取正则：支持 E01, EP01, 第01集, 01, S01E01 等格式
EPISODE_PATTERNS = [
    re.compile(r"[Ss](\d+)[Ee](\d+)"),           # S01E05
    re.compile(r"[Ee][Pp](\d+)"),                     # EP05
    re.compile(r"[Ee](\d+)"),                          # E05
    re.compile(r"第\s*(\d+)\s*集"),                    # 第05集
    re.compile(r"[_\-\s](\d{1,3})[_\-\s\.]"),        # _05_ 或 -05- 或 .05.
    re.compile(r"^(\d{1,3})[_\-\s\.]"),               # 05_ 开头
    re.compile(r"[_\-\s\.](\d{1,3})$"),               # _05 结尾
]


def _deep_find(obj: Any, key: str, max_depth: int = 10) -> list[Any]:
    """递归搜索对象中所有指定 key 的值。"""
    results = []
    if max_depth <= 0:
        return results
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and v is not None and v != "":
                results.append(v)
            results.extend(_deep_find(v, key, max_depth - 1))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            results.extend(_deep_find(item, key, max_depth - 1))
    return results


def _deep_find_first(obj: Any, key: str, max_depth: int = 10) -> Any:
    """递归搜索第一个匹配的值。"""
    results = _deep_find(obj, key, max_depth)
    return results[0] if results else None


def _extract_episode_number(filename: str) -> int | None:
    """从文件名中提取集数编号。"""
    name = str(filename or "")
    # 先去掉扩展名
    name_no_ext = name
    for ext in VIDEO_EXTENSIONS:
        if name_no_ext.lower().endswith(ext):
            name_no_ext = name_no_ext[: -len(ext)]
            break

    for pattern in EPISODE_PATTERNS:
        match = pattern.search(name_no_ext)
        if match:
            try:
                # S01E05 格式取最后一个分组
                groups = match.groups()
                return int(groups[-1])
            except (ValueError, IndexError):
                continue
    return None


def _compress_episode_ranges(filenames: list[str]) -> list[str]:
    """将文件名列表压缩成范围格式。

    例如: [1.mp4, 2.mp4, 3.mp4, 5.mp4] -> ["1-3.mp4", "5.mp4"]
    """
    if not filenames:
        return []

    parsed: list[tuple[int, str, str]] = []  # (episode, original_name, extension)
    unparsed: list[str] = []

    for fn in filenames:
        if not fn:
            continue
        ep = _extract_episode_number(fn)
        if ep is not None:
            # 提取扩展名
            ext = ""
            for v_ext in VIDEO_EXTENSIONS:
                if fn.lower().endswith(v_ext):
                    ext = v_ext
                    break
            parsed.append((ep, fn, ext))
        else:
            unparsed.append(fn)

    if not parsed:
        return unparsed

    # 按集数排序
    parsed.sort(key=lambda x: x[0])

    ranges: list[str] = []
    range_start: tuple[int, str, str] | None = None
    range_prev: tuple[int, str, str] | None = None

    for item in parsed:
        ep, orig_fn, ext = item
        if range_start is None:
            range_start = item
            range_prev = item
            continue

        prev_ep = range_prev[0]
        prev_ext = range_prev[2]

        # 连续且扩展名相同
        if ep == prev_ep + 1 and ext == prev_ext:
            range_prev = item
        else:
            # 结束当前范围
            start_ep = range_start[0]
            end_ep = range_prev[0]
            start_ext = range_start[2]
            if start_ep == end_ep:
                ranges.append(range_start[1])
            else:
                ranges.append(f"{start_ep}-{end_ep}{start_ext}")
            range_start = item
            range_prev = item

    # 处理最后一个范围
    if range_start is not None and range_prev is not None:
        start_ep = range_start[0]
        end_ep = range_prev[0]
        start_ext = range_start[2]
        if start_ep == end_ep:
            ranges.append(range_start[1])
        else:
            ranges.append(f"{start_ep}-{end_ep}{start_ext}")

    # 添加无法解析的文件名
    ranges.extend(unparsed)
    return ranges


def _format_file_list(filenames: list[str], max_items: int = 30) -> str:
    """格式化文件列表，长列表使用范围压缩。"""
    if not filenames:
        return ""

    # 先尝试范围压缩
    compressed = _compress_episode_ranges(filenames)

    if len(compressed) <= max_items:
        return "、".join(compressed)

    # 太长则截断
    shown = compressed[:max_items]
    remaining = len(compressed) - max_items
    return "、".join(shown) + f"…（还有{remaining}个文件）"


def _extract_filenames_from_items(items: list[Any]) -> list[str]:
    """从 item 列表中提取文件名。"""
    filenames: list[str] = []
    for item in items:
        if isinstance(item, str):
            filenames.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("filename") or item.get("file_name") or item.get("title")
            if isinstance(name, str) and name:
                filenames.append(name)
            path = item.get("path") or item.get("path_hint") or item.get("source_path")
            if isinstance(path, str) and path and not name:
                # 只取文件名部分
                just_name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                if just_name:
                    filenames.append(just_name)
    return filenames


def _parse_duration_ms(duration_ms: int | None) -> str:
    """格式化耗时为 xh xm xs 格式。"""
    if duration_ms is None or duration_ms <= 0:
        return ""
    total_sec = duration_ms / 1000
    hours = int(total_sec // 3600)
    minutes = int((total_sec % 3600) // 60)
    seconds = int(total_sec % 60)
    parts: list[str] = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")
    return "".join(parts)


def _drive_label_from_plugin_id(plugin_id: str) -> str:
    """根据插件ID获取网盘名称。"""
    if not plugin_id:
        return ""
    label = DRIVE_LABEL_MAP.get(plugin_id)
    if label:
        return label
    # 尝试模糊匹配
    for key, val in DRIVE_LABEL_MAP.items():
        if key in plugin_id or plugin_id in key:
            return val
    return plugin_id


def _parse_datetime_to_local(dt_str: str | None, tz_name: str = "Asia/Shanghai") -> str:
    """将时间字符串解析为本地时间，格式：MM月DD日 HH点MM分。"""
    if not dt_str:
        return ""
    try:
        # 尝试多种常见格式
        formats = [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
        ]
        dt = None
        for fmt in formats:
            try:
                dt = datetime.strptime(dt_str, fmt)
                break
            except ValueError:
                continue

        if dt is None:
            # 尝试 ISO format
            try:
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return dt_str

        # 如果是 UTC 时间，转换为本地
        if dt.tzinfo is not None:
            try:
                from zoneinfo import ZoneInfo
                local_tz = ZoneInfo(tz_name)
                dt = dt.astimezone(local_tz)
            except Exception:
                dt = dt.replace(tzinfo=None) + timedelta(hours=8)

        return dt.strftime("%m月%d日 %H点%M分")
    except Exception:
        return dt_str


def _deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """深度合并两个字典，override 中的非空值会覆盖 base。"""
    result = dict(base)
    for key, val in override.items():
        if val is None or val == "" or val == [] or val == {}:
            continue
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_dicts(result[key], val)
        elif isinstance(val, list) and isinstance(result.get(key), list):
            merged = list(result[key])
            for item in val:
                if item not in merged:
                    merged.append(item)
            result[key] = merged
        else:
            result[key] = val
    return result


class DingdingBotAutomationPlugin(AutomationProvider, BasePlugin):
    plugin_id = "automation.dingding_bot"
    plugin_name = "钉钉 Bot"
    plugin_version = "2.0.1"

    def __init__(self) -> None:
        self._runtime_config: dict[str, Any] = {}
        # task_id -> {event: 合并后的事件, received_at: 收到时间戳, complete: 是否已完整}
        self._event_buffer: dict[str, dict[str, Any]] = {}
        # 已发送的 task_id 集合，防止重复
        self._sent_tasks: set[str] = set()

    # ==================== 配置管理 ====================

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

    # ==================== 测试通知 ====================

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

    # ==================== 事件去重与合并 ====================

    def _get_task_key(self, event: dict[str, Any]) -> str:
        """从事件中获取唯一标识 key。"""
        payload = dict(event.get("payload") or {})
        output_payload = dict(payload.get("output_payload") or {})
        input_payload = dict(payload.get("input_payload") or {})

        all_data = {
            "event": event,
            "payload": payload,
            "output": output_payload,
            "input": input_payload,
        }

        # 优先用 task_id
        task_id = (
            event.get("task_id")
            or payload.get("task_id")
            or output_payload.get("task_id")
            or input_payload.get("task_id")
            or _deep_find_first(all_data, "task_id")
            or event.get("execution_id")
            or payload.get("execution_id")
            or _deep_find_first(all_data, "execution_id")
            or ""
        )
        if task_id:
            return f"task:{task_id}"

        # fallback: 用 event_type + task_name
        task_name_candidates = (
            _deep_find(all_data, "task_name")
            + _deep_find(all_data, "title")
            + _deep_find(all_data, "share_name")
        )
        task_name = ""
        for c in task_name_candidates:
            if isinstance(c, str) and len(c) >= 4:
                task_name = c
                break

        return f"{event.get('event_type', 'unknown')}:{task_name}"

    def _is_event_complete(self, event: dict[str, Any]) -> bool:
        """判断事件数据是否完整（有详细文件列表等）。"""
        payload = dict(event.get("payload") or {})
        output_payload = dict(payload.get("output_payload") or {})

        # 检查是否有详细数据
        for key in [
            "share_results", "artifacts", "saved_files", "skipped_files",
            "failed_files", "saved_items", "items", "results",
        ]:
            val = output_payload.get(key) or payload.get(key)
            if isinstance(val, list) and len(val) > 0:
                return True

        # 检查是否有统计数据
        for key in [
            "saved_count", "skipped_count", "transferred_count",
            "filtered_count", "generated_item_ids_count",
        ]:
            val = output_payload.get(key) or payload.get(key)
            if val is not None and val != 0 and val != "":
                try:
                    if int(val) > 0:
                        return True
                except (ValueError, TypeError):
                    pass

        return False

    def _cleanup_expired_buffer(self, max_age: float = 30.0) -> None:
        """清理过期的 buffer 条目。"""
        now = time.time()
        expired_keys = [
            k for k, v in self._event_buffer.items()
            if now - v.get("received_at", 0) > max_age
        ]
        for k in expired_keys:
            self._event_buffer.pop(k, None)
        # 也清理已发送集合（防止无限增长）
        if len(self._sent_tasks) > 1000:
            self._sent_tasks = set(list(self._sent_tasks)[-500:])

    def _flush_overdue_buffered(self) -> list[tuple[str, str]]:
        """将 buffer 中超过等待时限的事件取出来准备发送。"""
        now = time.time()
        wait_window = 3.0  # 等待第二次事件的窗口（秒）
        results: list[tuple[str, str]] = []
        done_keys: list[str] = []

        for key, buf in self._event_buffer.items():
            if key in self._sent_tasks:
                done_keys.append(key)
                continue
            age = now - buf.get("received_at", 0)
            is_complete = buf.get("complete", False)
            if is_complete or age > wait_window:
                merged_event = buf.get("event", {})
                title, content = self._build_message(merged_event)
                results.append((title, content))
                self._sent_tasks.add(key)
                done_keys.append(key)

        for k in done_keys:
            self._event_buffer.pop(k, None)

        return results

    # ==================== 事件处理 ====================

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        event_type = str(event.get("event_type") or "unknown")
        print(f"[钉钉Bot] 收到事件: {event_type}")

        # 清理过期条目
        self._cleanup_expired_buffer()

        cfg = self._resolve_config()
        configured = bool(str(cfg.get("webhook_url") or "").strip())

        # 立即事件（不需要等待合并的）：失败/取消/开始/创建
        immediate_events = {"task.failed", "task.canceled", "task.started", "task.created"}

        if event_type in immediate_events:
            # 直接发送，不做合并
            title, content = self._build_message(event)
            if configured:
                self._do_send(title, content, event_type)
            return self._make_result(event_type, title, content, configured)

        # 需要合并的事件（主要是 task.completed）
        task_key = self._get_task_key(event)

        # 如果已经发送过，直接跳过（去重）
        if task_key and task_key in self._sent_tasks:
            print(f"[钉钉Bot] 跳过重复事件: task_key={task_key}")
            return self._make_result(event_type, "", "", configured, skipped=True)

        # 判断当前事件是否完整
        is_complete = self._is_event_complete(event)

        # 合并到 buffer
        now = time.time()
        if task_key and task_key in self._event_buffer:
            existing = self._event_buffer[task_key]
            merged = _deep_merge_dicts(existing.get("event", {}), event)
            existing["event"] = merged
            existing["complete"] = existing.get("complete", False) or is_complete
            print(f"[钉钉Bot] 合并事件: task_key={task_key}, complete={existing['complete']}")
        elif task_key:
            self._event_buffer[task_key] = {
                "event": dict(event),
                "received_at": now,
                "complete": is_complete,
            }
            print(f"[钉钉Bot] 缓存事件: task_key={task_key}, complete={is_complete}")

        # 尝试发送：完整的立即发，超时的也发
        messages_to_send = self._flush_overdue_buffered()

        if configured:
            for title, content in messages_to_send:
                self._do_send(title, content, event_type)

        # 返回结果（用最后一条消息）
        if messages_to_send:
            last_title, last_content = messages_to_send[-1]
            return self._make_result(event_type, last_title, last_content, configured)
        return self._make_result(event_type, "", "", configured, buffered=True)

    def _do_send(self, title: str, content: str, event_type: str) -> None:
        """实际执行钉钉发送。"""
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

    def _make_result(
        self, event_type: str, title: str, content: str,
        configured: bool, skipped: bool = False, buffered: bool = False,
    ) -> dict[str, Any]:
        """构造返回结果。"""
        msg = f"{self.plugin_name} 已处理事件：{event_type}"
        if skipped:
            msg = f"{self.plugin_name} 跳过重复事件：{event_type}"
        elif buffered:
            msg = f"{self.plugin_name} 已缓存事件等待合并：{event_type}"
        return OperationResult(
            success=True,
            message=msg,
            data={
                "event_type": event_type,
                "title": title,
                "content": content,
                "configured": configured,
                "skipped": skipped,
                "buffered": buffered,
            },
        ).model_dump(mode="json")

    # ==================== 发送钉钉消息 ====================

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

    # ==================== 工具方法 ====================

    def _category_for(self, event_type: str) -> str:
        return EVENT_CATEGORY.get(event_type, "系统通知")

    @staticmethod
    def _normalize_runtime_config(config: dict[str, Any] | None) -> dict[str, Any]:
        return dict(config or {})

    # ==================== 消息构建核心 ====================

    def _build_message(self, event: dict[str, Any]) -> tuple[str, str]:
        event_type = str(event.get("event_type") or "unknown")
        source = str(event.get("source") or "core")
        plugin_id = str(event.get("plugin_id") or "")
        task_type = str(event.get("task_type") or "")
        status = str(event.get("status") or "unknown")

        payload = dict(event.get("payload") or {})
        output_payload = dict(payload.get("output_payload") or {})
        input_payload = dict(payload.get("input_payload") or {})

        # -------- 深度提取所有可能的字段 --------
        # 合并所有层级以便深度搜索
        all_data = {
            "event": event,
            "payload": payload,
            "output": output_payload,
            "input": input_payload,
        }

        # 任务名称（优先从各个层级查找）
        task_name_candidates = _deep_find(all_data, "task_name") + _deep_find(all_data, "title") + _deep_find(all_data, "share_name")
        # 过滤掉纯集数的文件名
        def _is_good_task_name(name: str) -> bool:
            if not name or not isinstance(name, str):
                return False
            # 太短的通常是集数
            if len(name) < 4:
                return False
            # 看起来像纯集数字符串的排除
            if _extract_episode_number(name) is not None and len(name) < 10:
                return False
            return True

        task_name = "未命名任务"
        for candidate in task_name_candidates:
            if isinstance(candidate, str) and _is_good_task_name(candidate):
                task_name = candidate
                break

        task_id = str(event.get("task_id") or payload.get("task_id") or _deep_find_first(all_data, "task_id") or "")
        execution_id = str(event.get("execution_id") or payload.get("execution_id") or "")

        template_key = str(payload.get("template_key") or input_payload.get("template_key") or _deep_find_first(all_data, "template_key") or "")
        trigger_source = str(payload.get("trigger_source") or _deep_find_first(all_data, "trigger_source") or "")
        triggered_by = str(payload.get("triggered_by") or _deep_find_first(all_data, "triggered_by") or "")

        # 平台/类型信息
        platform_name = str(payload.get("platform_name") or input_payload.get("platform_name") or _deep_find_first(all_data, "platform_name") or "").strip()
        media_category = str(payload.get("media_category") or input_payload.get("media_category") or _deep_find_first(all_data, "media_category") or "").strip()
        sub_kind = str(payload.get("subscription_kind") or input_payload.get("subscription_kind") or _deep_find_first(all_data, "subscription_kind") or "").strip()
        sub_kind_label = RESOURCE_KIND_LABEL.get(sub_kind, sub_kind)
        catalog_label = str(payload.get("catalog_source_label") or input_payload.get("catalog_source_label") or _deep_find_first(all_data, "catalog_source_label") or "").strip()
        owner_plugin_id = str(payload.get("owner_plugin_id") or _deep_find_first(all_data, "owner_plugin_id") or "")

        # 网盘类型识别
        drive_plugin_id = str(
            input_payload.get("drive_plugin_id")
            or input_payload.get("cloud_type")
            or payload.get("drive_plugin_id")
            or _deep_find_first(all_data, "drive_plugin_id")
            or _deep_find_first(all_data, "cloud_type")
            or owner_plugin_id
            or ""
        ).strip()
        drive_label = _drive_label_from_plugin_id(drive_plugin_id) if drive_plugin_id else ""

        # 文本信息
        summary = str(payload.get("summary") or output_payload.get("summary") or _deep_find_first(all_data, "summary") or "").strip()
        detail_message = str(payload.get("detail_message") or payload.get("message") or _deep_find_first(all_data, "message") or "").strip()
        error_text = str(
            payload.get("error_text")
            or payload.get("error_message")
            or payload.get("error")
            or _deep_find_first(all_data, "error_text")
            or _deep_find_first(all_data, "error_message")
            or ""
        ).strip()

        # -------- 统计字段 --------
        def _count(key: str) -> int | None:
            # 按优先级查找
            for source_dict in [output_payload, payload, input_payload]:
                v = source_dict.get(key)
                if v is not None:
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        continue
            # 深度搜索
            deep = _deep_find_first(all_data, key)
            if deep is not None:
                try:
                    return int(deep)
                except (TypeError, ValueError):
                    pass
            return None

        saved_count = _count("saved_count")
        skipped_count = _count("skipped_count")
        transferred_count = _count("transferred_count")
        renamed_count = _count("renamed_count")
        filtered_count = _count("filtered_count")
        cleared_payload_count = _count("cleared_payload_count")
        generated_item_count = _count("generated_item_ids_count") or _count("generated_count")
        new_item_count = _count("new_item_count")
        last_new_data_count = _count("last_new_data_count")
        no_update_days = _count("no_update_days")
        failed_count = _count("failed_count")

        # 耗时
        duration_ms = _count("duration_ms")
        duration_text = _parse_duration_ms(duration_ms)

        # 目标路径
        target_dir = str(
            payload.get("target_dir")
            or input_payload.get("target_dir")
            or _deep_find_first(all_data, "target_dir")
            or _deep_find_first(all_data, "save_path")
            or _deep_find_first(all_data, "output_dir")
            or ""
        ).strip()

        # -------- 详细文件列表 --------
        # 从多种可能的字段中提取
        saved_files: list[str] = []
        skipped_files: list[str] = []
        failed_files: list[str] = []
        all_file_items: list[Any] = []

        # share_results
        share_results = output_payload.get("share_results") or payload.get("share_results") or []
        if isinstance(share_results, list):
            all_file_items.extend(share_results)

        # artifacts
        artifacts = output_payload.get("artifacts") or payload.get("artifacts") or []
        if isinstance(artifacts, list):
            all_file_items.extend(artifacts)

        # 深度搜索 items/results
        for key in ["items", "results", "files", "entries", "saved_items", "processed_items"]:
            found = _deep_find(all_data, key)
            for f in found:
                if isinstance(f, list):
                    all_file_items.extend(f)

        # 提取文件名并分类
        if all_file_items:
            for item in all_file_items:
                fname = ""
                status_val = ""
                if isinstance(item, str):
                    fname = item
                elif isinstance(item, dict):
                    fname = (
                        item.get("name")
                        or item.get("filename")
                        or item.get("file_name")
                        or item.get("title")
                        or ""
                    )
                    status_val = str(
                        item.get("status")
                        or item.get("state")
                        or item.get("result")
                        or ""
                    ).lower()

                if not fname:
                    continue

                if "fail" in status_val or "error" in status_val or "跳过" in status_val:
                    if "跳过" in status_val:
                        skipped_files.append(fname)
                    else:
                        failed_files.append(fname)
                elif "skip" in status_val:
                    skipped_files.append(fname)
                else:
                    saved_files.append(fname)

        # 如果没有从 items 中获取到，用单独的字段
        if not saved_files:
            raw_saved = (
                output_payload.get("saved_files")
                or payload.get("saved_files")
                or output_payload.get("saved_items")
                or []
            )
            if isinstance(raw_saved, list):
                saved_files = _extract_filenames_from_items(raw_saved)

        if not skipped_files:
            raw_skipped = (
                output_payload.get("skipped_files")
                or payload.get("skipped_files")
                or []
            )
            if isinstance(raw_skipped, list):
                skipped_files = _extract_filenames_from_items(raw_skipped)

        if not failed_files:
            raw_failed = (
                output_payload.get("failed_files")
                or payload.get("failed_files")
                or output_payload.get("errors")
                or []
            )
            if isinstance(raw_failed, list):
                failed_files = _extract_filenames_from_items(raw_failed)

        # 合并所有可见文件用于分析最新剧集
        all_visible_files = saved_files + skipped_files + failed_files

        # -------- 最新剧集分析 --------
        latest_episode_file = ""
        latest_episode_number: int | None = None
        latest_episode_update_time = ""

        # 方法1：从文件名中提取最大集数
        for fname in all_visible_files:
            ep = _extract_episode_number(fname)
            if ep is not None:
                if latest_episode_number is None or ep > latest_episode_number:
                    latest_episode_number = ep
                    latest_episode_file = fname

        # 方法2：深度搜索 updated_at / last_update_at 等时间字段
        update_time_candidates = _deep_find(all_data, "updated_at") + _deep_find(all_data, "last_updated_at") + _deep_find(all_data, "data_updated_at") + _deep_find(all_data, "last_data_update_at")
        if update_time_candidates:
            # 取最新的一个
            valid_times = []
            for t in update_time_candidates:
                if isinstance(t, str) and t:
                    valid_times.append(t)
            if valid_times:
                valid_times.sort(reverse=True)
                latest_episode_update_time = _parse_datetime_to_local(valid_times[0])

        # 方法3：从 items 中提取每个文件的更新时间
        if not latest_episode_update_time and all_file_items:
            item_times: list[tuple[str, int | None]] = []
            for item in all_file_items:
                if isinstance(item, dict):
                    t = item.get("updated_at") or item.get("update_time") or item.get("modified_at")
                    ep = _extract_episode_number(str(item.get("name") or ""))
                    if isinstance(t, str) and t:
                        item_times.append((t, ep))
            if item_times:
                item_times.sort(key=lambda x: x[0], reverse=True)
                latest_episode_update_time = _parse_datetime_to_local(item_times[0][0])
                # 如果有集数信息，用最新的
                for t, ep in item_times:
                    if ep is not None:
                        if latest_episode_number is None or ep > latest_episode_number:
                            latest_episode_number = ep
                            # 找对应的文件名
                            for item in all_file_items:
                                if isinstance(item, dict):
                                    name = item.get("name") or ""
                                    item_ep = _extract_episode_number(name)
                                    if item_ep == ep:
                                        latest_episode_file = name
                                        break
                        break

        # -------- 任务类型标签 --------
        task_type_label = TASK_TYPE_LABEL.get(task_type, TASK_TYPE_LABEL.get(template_key, task_type or ""))

        # 构建标签前缀
        tags: list[str] = []
        if drive_label:
            tags.append(drive_label)
        elif platform_name:
            tags.append(platform_name)
        if media_category:
            tags.append(media_category)
        if sub_kind_label:
            tags.append(sub_kind_label)
        if catalog_label:
            tags.append(catalog_label)
        if task_type_label and task_type_label not in tags:
            tags.append(task_type_label)
        tag_prefix = f"({'·'.join(tags)})" if tags else ""

        # -------- 统计行 --------
        stat_parts: list[str] = []
        if saved_count is not None and saved_count > 0:
            stat_parts.append(f"转存成功 {saved_count} 项")
        elif transferred_count is not None and transferred_count > 0:
            stat_parts.append(f"已转存 {transferred_count} 项")
        if generated_item_count is not None and generated_item_count > 0:
            stat_parts.append(f"生成 {generated_item_count} 项")
        if new_item_count is not None and new_item_count > 0:
            stat_parts.append(f"新增 {new_item_count} 项")
        if skipped_count is not None and skipped_count > 0:
            stat_parts.append(f"跳过 {skipped_count} 项")
        if failed_count is not None and failed_count > 0:
            stat_parts.append(f"失败 {failed_count} 项")
        if renamed_count is not None and renamed_count > 0:
            stat_parts.append(f"重命名 {renamed_count} 项")
        if filtered_count is not None and filtered_count > 0:
            stat_parts.append(f"过滤 {filtered_count} 项")
        if cleared_payload_count is not None and cleared_payload_count > 0:
            stat_parts.append(f"清理 {cleared_payload_count} 项")
        if last_new_data_count is not None:
            stat_parts.append(f"上次新增 {last_new_data_count} 项")
        if no_update_days is not None and no_update_days > 0:
            stat_parts.append(f"无更新 {no_update_days} 天")

        stat_line = "｜".join(stat_parts) if stat_parts else ""

        # 判断是否无更新
        is_no_update = False
        if (
            (saved_count is not None and saved_count == 0)
            or (transferred_count is not None and transferred_count == 0)
            or (generated_item_count is not None and generated_item_count == 0)
        ) and (
            (skipped_count is not None and skipped_count > 0)
            or (no_update_days is not None and no_update_days > 0)
        ):
            is_no_update = True

        # ==================== 按事件类型构建消息 ====================
        lines: list[str] = []

        # 标签
        if tag_prefix:
            lines.append(f"🏷️ {tag_prefix}")

        # 耗时
        if duration_text:
            lines.append(f"⏱️ 任务耗时：{duration_text}")

        # 统计
        if stat_line:
            lines.append(f"📊 {stat_line}")

        # 转存/保存明细
        if saved_files:
            saved_display = _format_file_list(saved_files)
            lines.append(f"📋 成功明细：{saved_display}")

        if skipped_files:
            skipped_display = _format_file_list(skipped_files)
            lines.append(f"⏭️  跳过敏细：{skipped_display}")

        if failed_files:
            failed_display = _format_file_list(failed_files)
            lines.append(f"⚠️  失败明细：{failed_display}")

        # 最新剧集
        if latest_episode_file:
            lines.append(f"🆕 最新剧集：{latest_episode_file}")
        elif latest_episode_number is not None:
            lines.append(f"🆕 最新剧集：第 {latest_episode_number} 集")

        if latest_episode_update_time:
            lines.append(f"🕐 最新剧集更新时间：{latest_episode_update_time}")

        # 目标路径
        if target_dir:
            lines.append(f"📁 转存目标：{target_dir}")

        # 触发来源
        if trigger_source:
            lines.append(f"⚡ 触发：{trigger_source}" + (f"（{triggered_by}）" if triggered_by else ""))

        # 总结/描述
        if summary and summary != detail_message:
            lines.append(f"📝 {summary}")

        if detail_message:
            lines.append(f"💬 {detail_message}")

        # 无更新提示
        if is_no_update:
            lines.append("🔄 本次无更新（已存在相同文件）")

        # 错误信息
        if error_text and event_type != "task.failed":
            lines.append(f"❌ {error_text}")

        # 任务ID
        if task_id:
            lines.append(f"🆔 {task_id}")

        # ==================== 标题构建 ====================
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
                # 错误信息放最前面
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
