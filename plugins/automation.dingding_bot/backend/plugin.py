from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

from core.sdk import AutomationProvider, BasePlugin, OperationResult

DEFAULT_EVENTS = [
    "task.completed",
    "task.failed",
    "task.started",
    "task.created",
    "task.canceled",
    "task.transfer.post_execute",
    "task.strm.post_execute",
    "task.download.post_execute",
    "task.drive_download.post_execute",
    "task.video_download.post_execute",
    "task.short_video.post_execute",
    "task.drive_cache_keep.post_execute",
    "task.subscription.post_execute",
    "task.catalog_batch_strm.post_execute",
    "task.live_catalog_batch_strm.post_execute",
    "plugin.installed",
    "plugin.uninstalled",
    "system.startup",
    "system.shutdown",
]

# 默认 T3 平台 API 配置（当用户未在插件设置中填写时使用）
DEFAULT_T3_API_BASE = "https://t3.midsummer.asia:28888/api"
DEFAULT_T3_API_KEY = "t3mt_QzuZ7KKiKA0rEfKYB5z6jk3ktmfLAWL3NpLgxYpJbrs"
DEFAULT_T3_API_HEADER = "X-API-Key"

EVENT_CATEGORY = {
    "task.completed": "任务完成",
    "task.failed": "任务失败",
    "task.started": "任务开始",
    "task.created": "任务创建",
    "task.canceled": "任务取消",
    "task.transfer": "转存任务",
    "task.transfer.post_execute": "转存任务完成",
    "task.drive_download": "网盘下载",
    "task.drive_download.post_execute": "网盘下载完成",
    "task.video_download": "视频下载",
    "task.video_download.post_execute": "视频下载完成",
    "task.short_video.post_execute": "短视频下载完成",
    "task.strm": "STRM生成",
    "task.strm.post_execute": "STRM生成完成",
    "task.drive_cache_keep.post_execute": "网盘缓存保活完成",
    "task.download.post_execute": "下载任务完成",
    "task.subscription.post_execute": "订阅任务完成",
    "task.catalog_batch_strm.post_execute": "批量STRM生成完成",
    "task.live_catalog_batch_strm.post_execute": "直播批量STRM生成完成",
    "plugin.installed": "插件安装",
    "plugin.uninstalled": "插件卸载",
    "system.startup": "系统启动",
    "system.shutdown": "系统关闭",
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
    "strm_generate": "STRM生成",
    "drive_download": "网盘下载",
    "transfer": "订阅转存",
}

# 触发类型中文映射
TRIGGER_SOURCE_CN = {
    "cron-schedule": "定时触发",
    "cron": "定时触发",
    "schedule": "定时触发",
    "manual-ui": "手动触发",
    "manual": "手动触发",
    "manual_api": "手动触发",
    "system": "系统触发",
    "api": "API触发",
    "webhook": "Webhook触发",
    "followup": "联动触发",
    "post_plugin": "插件触发",
    "dispatcher": "调度触发",
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".ts", ".flv", ".wmv",
    ".m4v", ".mpg", ".mpeg", ".m2ts", ".iso", ".rmvb", ".webm",
    ".strm",
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
    plugin_version = "2.7.2"

    def __init__(self) -> None:
        self._runtime_config: dict[str, Any] = {}
        # task_id -> {event: 合并后的事件, received_at: 收到时间戳, complete: 是否已完整}
        self._event_buffer: dict[str, dict[str, Any]] = {}
        # 已发送的 task_id 集合，防止重复
        self._sent_tasks: set[str] = set()
        # 已发送事件的时间戳（去重用）: dedup_key -> 发送时间
        self._sent_task_times: dict[str, float] = {}
        # 平台本地 API 基础地址（尝试自动探测）
        self._api_base: str | None = None
        # ---- 日统计相关 ----
        self._daily_stats: dict[str, Any] = {}  # 内存中的统计数据
        self._daily_stats_dirty: bool = False    # 统计数据是否有变更
        self._daily_stats_test_done: bool = False  # 是否已完成配置保存后的测试推送（做过就不再做）
        self._daily_stats_test_pending: bool = False  # 配置保存后是否需要立即推送统计（用于测试）
        self._daily_stats_last_push_date: str | None = None  # 上次日统计推送的日期（YYYY-MM-DD），防止同一天重复推送
        self._daily_stats_pushed_times: set[str] = set()  # 今日已推送过的时间点（HH:MM 集合），支持多个推送时间
        self._last_config_hash: str | None = None  # 上次配置的哈希，用于检测配置是否真的变更
        # ---- 异步发送线程池 ----
        # 所有钉钉 HTTP 推送都在独立线程池中执行，避免阻塞事件回调线程
        self._sender_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dingding-sender")
        # ---- 日统计独立定时轮询线程 ----
        # 不依赖任务事件触发，每分钟检查一次是否到达推送时间
        self._stats_timer_stop = threading.Event()
        self._stats_timer_thread = threading.Thread(target=self._stats_timer_loop, name="dingding-stats-timer", daemon=True)
        self._stats_timer_thread.start()

    # ==================== 平台本地 API 调用 ====================

    def _get_api_credentials(self) -> tuple[str | None, str | None, str]:
        """从配置中获取 API 凭据。"""
        import os
        config = self._resolve_config()
        api_base = str(config.get("t3_api_base") or "").strip()
        api_key = str(config.get("t3_api_key") or "").strip()
        api_header = str(config.get("t3_api_header") or "").strip()
        # 兼容环境变量（T3MT_* 优先，然后 T3_*）
        base_from_config = bool(api_base)
        key_from_config = bool(api_key)
        if not api_base:
            api_base = (
                os.environ.get("T3MT_API_BASE")
                or os.environ.get("T3_API_BASE")
                or os.environ.get("T3MT_HOST")
                or ""
            )
            if api_base and "/api" not in api_base:
                api_base = api_base.rstrip("/") + "/api"
        if not api_base:
            api_base = DEFAULT_T3_API_BASE
        if not api_key:
            api_key = (
                os.environ.get("T3MT_API_KEY")
                or os.environ.get("T3_API_KEY")
                or DEFAULT_T3_API_KEY
                or ""
            )
        if not api_header:
            env_header = (
                os.environ.get("T3MT_API_HEADER")
                or os.environ.get("T3_API_HEADER")
                or ""
            )
            api_header = env_header if env_header else DEFAULT_T3_API_HEADER
        # 调试打印
        base_mask = api_base[:30] + "..." if len(str(api_base)) > 30 else str(api_base)
        key_mask = api_key[:8] + "***" + api_key[-4:] if len(api_key) > 12 else ("已设置" if api_key else "未设置")
        src_base = "插件设置" if base_from_config else ("环境变量" if os.environ.get("T3MT_API_BASE") or os.environ.get("T3_API_BASE") else "默认值")
        src_key = "插件设置" if key_from_config else ("环境变量" if os.environ.get("T3MT_API_KEY") or os.environ.get("T3_API_KEY") else "默认值")
        print(f"[钉钉Bot][API凭据] base={base_mask} (来源:{src_base}), key={key_mask} (来源:{src_key}), header={api_header}")
        return api_base if api_base else None, api_key if api_key else None, api_header

    def _get_api_base(self) -> str | None:
        """获取平台 API 地址。

        优先级：
        1. 已缓存的地址
        2. 从用户配置中提取端口，探测本地回环（插件在Docker内，192.168.x.x可能不可达）
        3. 用户配置的地址（验证通过才用）
        4. 环境变量配置的地址
        5. 自动探测容器内本地端口
        6. 兜底默认地址
        """
        if self._api_base:
            return self._api_base

        import os

        # 获取用户配置的凭据
        config = self._resolve_config()
        configured_base = str(config.get("t3_api_base") or "").strip()
        _, probe_key, probe_header = self._get_api_credentials()
        # 探测时同时发送多种认证header（和_fetch_api保持一致）
        probe_headers: dict[str, str] = {}
        if probe_key:
            probe_headers[probe_header] = probe_key
            probe_headers["Authorization"] = f"Bearer {probe_key}"
            if probe_header.lower() != "x-api-key":
                probe_headers["x-api-key"] = probe_key

        # ===== 从用户配置中提取端口，优先探测本地回环 =====
        if configured_base:
            import re
            # 提取端口号
            port_match = re.search(r":(\d+)", configured_base)
            configured_port = port_match.group(1) if port_match else None
            # 提取协议
            scheme = "https" if configured_base.startswith("https") else "http"

            # 用配置的端口探测本地回环
            if configured_port:
                local_candidates = [
                    f"{scheme}://127.0.0.1:{configured_port}",
                    f"{scheme}://localhost:{configured_port}",
                ]
                for local_base in local_candidates:
                    test_url = local_base if "/api" in local_base else f"{local_base}/api"
                    try:
                        with httpx.Client(timeout=3, headers=probe_headers) as client:
                            resp = client.get(f"{test_url}/tasks?limit=1")
                            if resp.status_code == 200:
                                self._api_base = local_base
                                print(f"[钉钉Bot][平台API] 本地回环探测成功（认证通过）: {local_base}")
                                return local_base
                            elif resp.status_code == 401:
                                print(f"[钉钉Bot][平台API] 本地回环可达但认证失败: {local_base} (HTTP 401)")
                            else:
                                print(f"[钉钉Bot][平台API] 本地回环状态: {local_base} (HTTP {resp.status_code})")
                    except Exception as e:
                        print(f"[钉钉Bot][平台API] 本地回环探测失败: {local_base} ({e})")

            # 本地回环不通，再用用户配置的地址试试
            normalized = configured_base.rstrip("/")
            print(f"[钉钉Bot][平台API] 尝试用户配置的 API 地址: {normalized}")
            test_url = normalized if "/api" in normalized else f"{normalized}/api"
            try:
                with httpx.Client(timeout=5, headers=probe_headers) as client:
                    resp = client.get(f"{test_url}/tasks?limit=1")
                    if resp.status_code == 200:
                        self._api_base = normalized
                        print(f"[钉钉Bot][平台API] 用户配置地址验证通过: {normalized}")
                        return normalized
                    else:
                        print(f"[钉钉Bot][平台API] 用户配置地址认证状态: HTTP {resp.status_code}")
            except Exception as e:
                print(f"[钉钉Bot][平台API] 用户配置地址测试异常: {e}")
            # 即使认证失败也用用户配置的地址（可能key不对，但地址是对的）
            self._api_base = normalized
            return normalized

        # 检查环境变量
        env_base = (
            os.environ.get("T3MT_API_BASE")
            or os.environ.get("T3_API_BASE")
            or os.environ.get("T3MT_HOST")
            or ""
        )
        if env_base:
            if "/api" not in env_base:
                env_base = env_base.rstrip("/") + "/api"
            normalized = env_base.rstrip("/")
            print(f"[钉钉Bot][平台API] 环境变量配置 API 地址: {normalized}")
            self._api_base = normalized
            return normalized

        # 收集候选地址，按优先级排序
        candidates: list[str] = []

        # 第1组：容器内本地探测（T3 平台就在同一个容器里运行）
        local_hosts = ["127.0.0.1", "localhost", "0.0.0.0"]
        env_port = (
            os.environ.get("T3_API_PORT")
            or os.environ.get("PORT")
            or os.environ.get("T3_PORT")
            or ""
        )
        common_ports = ["7860", "8000", "3000", "5173", "80", "7861", "8080"]
        ports_to_try: list[str] = []
        if env_port:
            ports_to_try.append(str(env_port))
        ports_to_try.extend(common_ports)

        for host in local_hosts:
            for port in ports_to_try:
                candidate = f"http://{host}:{port}"
                if candidate not in candidates:
                    candidates.append(candidate)

        # 第2组：兜底的远程 T3MT 平台
        remote_default = DEFAULT_T3_API_BASE.rstrip("/")
        if remote_default not in candidates:
            if remote_default.endswith("/api"):
                remote_base_no_api = remote_default[:-4].rstrip("/")
                if remote_base_no_api not in candidates:
                    candidates.append(remote_base_no_api)
            else:
                candidates.append(remote_default)

        # 逐个测试，优先返回认证通过（200）的地址
        print(f"[钉钉Bot][平台API] 开始探测 {len(candidates)} 个候选地址...")
        # 第一轮：找认证通过的（200）
        for base in candidates:
            test_url = base if "/api" in base else f"{base}/api"
            try:
                with httpx.Client(timeout=1.5, headers=probe_headers) as client:
                    resp = client.get(f"{test_url}/tasks?limit=1")
                    if resp.status_code == 200:
                        self._api_base = base
                        print(f"[钉钉Bot][平台API] 探测成功（认证通过）: {base}")
                        return base
            except Exception:
                continue
        # 第二轮：找地址可达但可能没认证的（<500），最后兜底
        for base in candidates:
            test_url = base if "/api" in base else f"{base}/api"
            try:
                with httpx.Client(timeout=1.5) as client:
                    resp = client.get(f"{test_url}/tasks?limit=1")
                    if resp.status_code < 500:
                        self._api_base = base
                        print(f"[钉钉Bot][平台API] 探测成功（地址可达，需认证）: {base} (HTTP {resp.status_code})")
                        return base
            except Exception:
                continue

        print(f"[钉钉Bot][平台API] 所有 {len(candidates)} 个候选地址均不可达")
        return None

    def _fetch_api(self, path: str) -> tuple[bool, Any]:
        """调用平台 API，返回 (是否成功, 数据)。同时发送 x-api-key 和 Authorization Bearer 两种认证。"""
        base = self._get_api_base()
        if not base:
            return False, "未探测到平台API地址"
        # 构造 URL：base 可能是 https://xxx 或 https://xxx/api
        # path 可能是 /api/tasks/xxx 或 /tasks/xxx
        api_base_url = base
        if "/api" not in base:
            api_base_url = f"{base}/api"
        # 去掉 path 开头的 /api（如果有）
        clean_path = path
        if clean_path.startswith("/api/"):
            clean_path = clean_path[4:]
        elif clean_path.startswith("api/"):
            clean_path = clean_path[3:]
        if not clean_path.startswith("/"):
            clean_path = "/" + clean_path
        url = f"{api_base_url}{clean_path}"
        # 获取认证凭据
        _, api_key, api_header_name = self._get_api_credentials()
        # 同时发送两种认证 header（x-api-key 和 Authorization Bearer）
        # 平台源码 deps.py 的 _extract_api_key 函数同时支持这两种方式
        headers: dict[str, str] = {}
        if api_key:
            # 方式1: 用户配置的 header 名（默认 X-API-Key）
            headers[api_header_name] = api_key
            # 方式2: 标准 Bearer Token
            headers["Authorization"] = f"Bearer {api_key}"
            # 方式3: 全小写 x-api-key（保险，HTTP header 不区分大小写，但某些代理可能有问题）
            if api_header_name.lower() != "x-api-key":
                headers["x-api-key"] = api_key
        key_mask = api_key[:8] + "***" + api_key[-4:] if api_key and len(api_key) > 12 else ("已设置" if api_key else "未设置")
        header_names = list(headers.keys())
        print(f"[钉钉Bot][API调用] GET {url}  headers={header_names} key={key_mask}")
        try:
            with httpx.Client(timeout=10, headers=headers) as client:
                resp = client.get(url)
                print(f"[钉钉Bot][API调用] 结果: HTTP {resp.status_code}")
                if resp.status_code == 200:
                    return True, resp.json()
                return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
        except Exception as e:
            print(f"[钉钉Bot][API调用] 异常: {e}")
            return False, str(e)

    def _read_local_task_logs(self, execution_id: str, log_file_path: str | None = None) -> str:
        """从本地文件系统读取任务执行日志。

        Args:
            execution_id: 执行 ID
            log_file_path: 可选，从 output_payload 中获取的精确日志路径（如 storage/runtime/task-logs/2026-07-31/exec_xxx.jsonl）
        """
        import os
        import datetime

        candidates: list[str] = []

        # 第1优先级：精确路径（来自 output_payload.log_file_path）
        if log_file_path:
            candidates.append(log_file_path)
            # 也尝试不带路径前缀的
            candidates.append(os.path.basename(log_file_path))

        # 生成日期候选（今天和昨天）
        today = datetime.date.today()
        date_candidates = [
            today.strftime("%Y-%m-%d"),
            (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
        ]
        for d in date_candidates:
            candidates.extend([
                f"storage/runtime/task-logs/{d}/{execution_id}.jsonl",
                f"storage/runtime/task-logs/{d}/{execution_id}.log",
                f"storage/runtime/task-logs/{d}/{execution_id}",
            ])

        # 不带日期的路径
        candidates.extend([
            f"storage/runtime/task-logs/{execution_id}.log",
            f"storage/runtime/task-logs/{execution_id}.jsonl",
            f"storage/runtime/task-logs/{execution_id}",
            f"runtime/task-logs/{execution_id}.log",
            f"runtime/task-logs/{execution_id}.jsonl",
            "data/task-logs/{execution_id}.log",
            "logs/{execution_id}.log",
        ])

        # 从环境变量获取项目根目录
        project_root = os.environ.get("PROJECT_ROOT") or os.environ.get("T3_ROOT") or "."
        for rel in candidates:
            full_path = os.path.join(project_root, rel) if project_root != "." else rel
            try:
                if os.path.isfile(full_path):
                    content = ""
                    # JSONL 文件需要逐行解析提取 message
                    if full_path.endswith(".jsonl"):
                        lines: list[str] = []
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            for line in f:
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    entry = __import__("json").loads(line)
                                    if isinstance(entry, dict):
                                        msg = entry.get("message") or entry.get("msg") or entry.get("text")
                                        ts = entry.get("created_at") or entry.get("timestamp") or entry.get("time")
                                        level = entry.get("level") or ""
                                        if msg:
                                            if ts:
                                                lines.append(f"[{ts}] [{level}] {msg}" if level else f"[{ts}] {msg}")
                                            else:
                                                lines.append(str(msg))
                                        else:
                                            # 如果没有 message 字段，就取整行
                                            lines.append(line)
                                    else:
                                        lines.append(str(entry))
                                except Exception:
                                    lines.append(line)
                        content = "\n".join(lines)
                    else:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                    print(f"[钉钉Bot][本地日志] 读取成功: {full_path} ({len(content)}字)")
                    return content
            except Exception as e:
                print(f"[钉钉Bot][本地日志] 读取失败 {full_path}: {e}")
        print(f"[钉钉Bot][本地日志] 未找到 execution_id={execution_id} 的日志文件（尝试了{len(candidates)}个路径）")
        return ""

    # ==================== 配置管理 ====================

    def _resolve_config(self, override: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = dict(self._runtime_config)
        if override:
            merged.update(dict(override or {}))
        return merged

    def set_runtime_config(self, config: dict[str, Any]) -> None:
        normalized = self._normalize_runtime_config(config)
        # 计算配置哈希，判断是否真的变更（框架可能每次事件都调用 set_runtime_config）
        import hashlib
        import json
        try:
            cur_hash = hashlib.md5(json.dumps(normalized, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        except Exception:
            cur_hash = str(normalized)
        config_changed = (cur_hash != self._last_config_hash)
        self._last_config_hash = cur_hash
        self._runtime_config = normalized
        keys = list(normalized.keys())
        has_webhook = bool(str(normalized.get("webhook_url") or "").strip())
        # 打印 API 相关配置（注意不打印完整 key，只打掩码）
        t3_base = str(normalized.get("t3_api_base") or "")
        t3_key = str(normalized.get("t3_api_key") or "")
        t3_header = str(normalized.get("t3_api_header") or "")
        t3_key_mask = t3_key[:8] + "***" + t3_key[-4:] if len(t3_key) > 12 else ("已设置" if t3_key else "未设置")
        print(f"[钉钉Bot] set_runtime_config: keys={keys}, webhook已配置={has_webhook}")
        print(f"[钉钉Bot][配置] t3_api_base={t3_base or '未设置'}, t3_api_key={t3_key_mask}, t3_api_header={t3_header or '未设置'}")
        # 清除缓存的 API 地址，强制重新探测（因为配置可能变了）
        self._api_base = None
        # 只有配置真的变更了，才标记待测试推送（做过一次后会被 _maybe_push_daily_stats 置 done）
        # （框架可能每次事件都调用 set_runtime_config，不能每次都置 True）
        if config_changed:
            self._daily_stats_test_done = False  # 配置变了，重置标志，允许再做一次测试推送
            self._daily_stats_test_pending = True
            self._daily_stats_pushed_times.clear()  # 清空已推送时间点记录，让新配置的时间点可以重新触发
            print(f"[钉钉Bot][日统计] 配置已变更，已重置推送状态，待首条任务完成后推送测试统计，定时推送时间={normalized.get('daily_stats_push_time', '23:55')}")

    def validate_runtime_config(self, config: dict[str, Any]) -> OperationResult:
        normalized = self._normalize_runtime_config(config)
        errors: list[str] = []
        warnings: list[str] = []
        if not str(normalized.get("webhook_url") or "").strip():
            errors.append("缺少必填配置：webhook_url")
        # 如果配置了 API 地址，测试联通性
        t3_base = str(normalized.get("t3_api_base") or "").strip()
        t3_key = str(normalized.get("t3_api_key") or "").strip()
        t3_header = str(normalized.get("t3_api_header") or "").strip() or DEFAULT_T3_API_HEADER
        if t3_base and t3_key:
            print(f"[钉钉Bot][配置校验] 测试 API 联通性: {t3_base}")
            test_url = t3_base if t3_base.endswith("/api") or "/api/" in t3_base else t3_base.rstrip("/") + "/api"
            try:
                test_headers = {t3_header: t3_key}
                with httpx.Client(timeout=5, headers=test_headers) as client:
                    resp = client.get(f"{test_url}/tasks?limit=1")
                    if resp.status_code == 200:
                        print(f"[钉钉Bot][配置校验] API 联通性测试通过（认证成功）")
                    elif resp.status_code == 401:
                        msg = f"API 认证失败（HTTP 401）：请检查 API Key 是否正确。Key 预览: {t3_key[:8]}***{t3_key[-4:] if len(t3_key) > 12 else ''}"
                        warnings.append(msg)
                        print(f"[钉钉Bot][配置校验] {msg}")
                    else:
                        msg = f"API 联通性异常（HTTP {resp.status_code}）：{resp.text[:200]}"
                        warnings.append(msg)
                        print(f"[钉钉Bot][配置校验] {msg}")
            except Exception as e:
                msg = f"API 联通性测试失败（无法连接）：{e}"
                warnings.append(msg)
                print(f"[钉钉Bot][配置校验] {msg}")
        elif not t3_base and not t3_key:
            print(f"[钉钉Bot][配置校验] 未配置 T3 平台 API，将使用本地日志数据源")
        elif not t3_key:
            warnings.append("已配置 API 地址但未配置 API Key，平台接口将无法获取数据。")
        data = dict(normalized)
        if warnings:
            data["warnings"] = warnings
        if errors:
            return OperationResult(success=False, message="插件配置校验失败。", errors=errors, data=data)
        msg = "插件配置校验通过。"
        if warnings:
            msg = "插件配置校验通过（有警告）：" + "；".join(warnings)
        return OperationResult(success=True, message=msg, data=data)

    def health(self, ctx: dict[str, Any]) -> dict[str, Any]:
        api_base_used = self._api_base or "未探测"
        api_status = "unknown"
        api_msg = ""
        # 简单测试 API 联通性
        if self._api_base:
            try:
                _, api_key, api_header = self._get_api_credentials()
                test_headers = {api_header: api_key} if api_key and api_header else {}
                test_url = self._api_base if "/api" in self._api_base else f"{self._api_base}/api"
                with httpx.Client(timeout=3, headers=test_headers) as client:
                    resp = client.get(f"{test_url}/tasks?limit=1")
                    if resp.status_code == 200:
                        api_status = "ok"
                        api_msg = "平台API连接正常（认证通过）"
                    elif resp.status_code == 401:
                        api_status = "error"
                        api_msg = "平台API认证失败（401），请检查API Key"
                    else:
                        api_status = "degraded"
                        api_msg = f"平台API响应异常（HTTP {resp.status_code}）"
            except Exception as e:
                api_status = "error"
                api_msg = f"平台API连接失败：{e}"
        return {
            "status": "ok",
            "message": "钉钉 Bot 通知插件运行正常。",
            "details": {
                "configured": self._is_configured(),
                "subscribed_events": self.subscribed_events(),
                "api_base": api_base_used,
                "api_status": api_status,
                "api_message": api_msg,
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

    def test_api_connectivity(self, payload: dict[str, Any]) -> dict[str, Any]:
        """测试平台API联通性。在插件设置页面的「测试」按钮调用。"""
        cfg = self._resolve_config(payload)
        t3_base = str(cfg.get("t3_api_base") or "").strip()
        t3_key = str(cfg.get("t3_api_key") or "").strip()
        t3_header = str(cfg.get("t3_api_header") or "").strip() or DEFAULT_T3_API_HEADER

        results: list[str] = []

        if not t3_base:
            return OperationResult(
                success=False,
                message="API 测试失败：未配置 T3 平台 API 地址。",
                data={"results": ["未配置 t3_api_base"]},
            ).model_dump(mode="json")
        if not t3_key:
            return OperationResult(
                success=False,
                message="API 测试失败：未配置 T3 平台 API Key。",
                data={"results": ["未配置 t3_api_key"]},
            ).model_dump(mode="json")

        # 规范化 URL
        test_base = t3_base.rstrip("/")
        if "/api" not in test_base:
            test_base = test_base + "/api"

        # 测试的接口列表
        test_endpoints = [
            ("GET /api/tasks", f"{test_base}/tasks?limit=1", True),
            ("GET /api/monitor/overview", f"{test_base}/monitor/overview", True),
            ("GET /api/monitor/executions", f"{test_base}/monitor/executions?limit=3", True),
            ("GET /api/health", f"{test_base}/health", False),
            ("GET /api/plugins", f"{test_base}/plugins", True),
            ("GET /openapi.json", t3_base.replace("/api", "") + "/openapi.json", False),
        ]

        headers = {t3_header: t3_key}
        print(f"[钉钉Bot][API测试] 开始测试 {len(test_endpoints)} 个接口，地址: {test_base}")

        all_ok = True
        for name, url, need_auth in test_endpoints:
            try:
                req_headers = headers if need_auth else {}
                with httpx.Client(timeout=5, headers=req_headers) as client:
                    resp = client.get(url)
                    if resp.status_code == 200:
                        results.append(f"✅ {name} → 200 OK")
                    elif resp.status_code == 401:
                        results.append(f"❌ {name} → 401 未授权（API Key 无效）")
                        all_ok = False
                    else:
                        results.append(f"⚠️ {name} → HTTP {resp.status_code}")
                        all_ok = False
            except Exception as e:
                results.append(f"❌ {name} → 连接失败: {e}")
                all_ok = False

        print(f"[钉钉Bot][API测试] 完成，结果: {'全部通过' if all_ok else '存在问题'}")
        for r in results:
            print(f"  {r}")

        msg = "API 联通性测试通过！" if all_ok else "API 联通性测试存在问题，请检查配置。"
        return OperationResult(
            success=all_ok,
            message=msg + "\n" + "\n".join(results),
            data={"results": results, "api_base": test_base},
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

    def _safe_build_message(self, event: dict[str, Any]) -> tuple[str, str, bool, dict[str, Any]]:
        """安全地构建消息，任何异常都返回兜底消息，防止静默失败。
        返回: (title, content, is_no_update, stats_info)
        """
        try:
            return self._build_message(event)
        except Exception as exc:
            import traceback
            print(f"[钉钉Bot][严重] _build_message 异常: {exc}")
            traceback.print_exc()
            # 兜底消息：至少保证能收到通知
            event_type = str(event.get("event_type") or "unknown")
            task_id = str(event.get("task_id") or "")
            payload = dict(event.get("payload") or {})
            task_name = str(
                payload.get("task_title")
                or payload.get("title")
                or event.get("task_type")
                or event_type
            )
            task_id_prefix = f"[{task_id}] " if task_id else ""
            title = f"{task_id_prefix}{task_name} · 通知"
            status = str(event.get("status") or "")
            summary = str(payload.get("summary") or payload.get("message") or "")
            content = (
                f"任务状态：{status}\n"
                f"{summary}\n\n"
                f"⚠️ 消息构建异常（详细错误已打印到日志）：\n{exc}"
            )
            return title, content, False, {}

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
                try:
                    title, content, _no_update, _stats = self._safe_build_message(merged_event)
                    results.append((title, content))
                    self._sent_tasks.add(key)
                    done_keys.append(key)
                except Exception as exc:
                    print(f"[钉钉Bot][严重] 处理缓存事件失败: {exc}")
                    # 出错也标记为已处理，防止死循环
                    self._sent_tasks.add(key)
                    done_keys.append(key)

        for k in done_keys:
            self._event_buffer.pop(k, None)

        return results

    # ==================== 事件处理 ====================

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._handle_safe(event)
        except Exception as exc:
            import traceback
            print(f"[钉钉Bot][严重] handle 顶层异常: {exc}")
            traceback.print_exc()
            # 最兜底：即使什么都失败了，也要返回成功状态
            return OperationResult(
                success=True,
                message=f"{self.plugin_name} 处理事件时出现异常（已打印到日志）：{exc}",
                data={"event_type": str(event.get("event_type", "unknown"))},
            ).model_dump(mode="json")

    def _handle_safe(self, event: dict[str, Any]) -> dict[str, Any]:
        event_type = str(event.get("event_type") or "unknown")
        task_id = str(event.get("task_id") or "")
        execution_id = str(event.get("execution_id") or "")

        # ===== 去重：同一 execution_id 60秒内只发一次（防止同一任务两条通知） =====
        dedup_key = f"{execution_id or task_id}:{event_type}"
        now = time.time()
        if dedup_key in self._sent_tasks:
            last_sent = self._sent_task_times.get(dedup_key, 0)
            if now - last_sent < 60:
                print(f"[钉钉Bot][去重] 跳过重复事件: dedup_key={dedup_key}（{int(now - last_sent)}秒前已发送）")
                return self._make_result(event_type, "", "", bool(str(self._resolve_config().get("webhook_url") or "").strip()), skipped=True)
        # 清理过期的时间记录
        expired_times = [k for k, v in self._sent_task_times.items() if now - v > 300]
        for k in expired_times:
            self._sent_task_times.pop(k, None)

        # ===== 从事件 payload 中提取 play_domain（平台内部地址，最准确） =====
        payload = dict(event.get("payload") or {})
        data = dict(payload.get("data") or {})
        play_domain = str(data.get("play_domain") or "").strip()
        if play_domain and not self._api_base:
            # play_domain 是平台内部监听的地址，格式如 http://127.0.0.1:8521
            normalized_domain = play_domain.rstrip("/")
            print(f"[钉钉Bot][平台API] 从payload提取到play_domain: {normalized_domain}，将其作为API地址")
            self._api_base = f"{normalized_domain}/api"

        # ===== 全量事件调试打印（无论是否订阅都打印完整结构） =====
        try:
            event_debug = json.dumps(event, ensure_ascii=False, default=str, indent=2)
        except Exception:
            event_debug = str(event)
        print(f"[钉钉Bot][全量事件] 收到事件类型: {event_type}")
        print(f"[钉钉Bot][全量事件] 完整结构:\n{event_debug}")
        # 也打印顶层所有字段的类型和值概览
        for k, v in event.items():
            if isinstance(v, (dict, list)):
                try:
                    preview = json.dumps(v, ensure_ascii=False, default=str)[:200]
                except Exception:
                    preview = str(v)[:200]
                print(f"[钉钉Bot][字段] {k}: {type(v).__name__} = {preview}...")
            else:
                print(f"[钉钉Bot][字段] {k}: {type(v).__name__} = {v}")

        # 清理过期条目
        self._cleanup_expired_buffer()

        cfg = self._resolve_config()
        configured = bool(str(cfg.get("webhook_url") or "").strip())
        subscribed = self.subscribed_events(cfg)
        print(f"[钉钉Bot] 已订阅事件: {subscribed}")

        # 如果事件不在订阅列表中，只打印不发送
        if event_type not in subscribed:
            print(f"[钉钉Bot] 事件 {event_type} 不在订阅列表中，仅打印不推送")
            return self._make_result(event_type, "", "", configured, skipped=True)

        # ===== 所有订阅事件都立即发送（取消合并缓冲，防止STRM等事件被吞掉） =====
        try:
            title, content, is_no_update, stats_info = self._safe_build_message(event)
        except Exception as exc:
            title, content, is_no_update, stats_info = f"[{event.get('task_id', '')}] 通知", f"构建消息失败: {exc}", False, {}

        # ===== 无更新不推送（总开关）—— 直接使用 _build_message 返回的数据标志，不靠关键词猜 =====
        skip_no_update = bool(cfg.get("skip_no_update_notify"))
        is_task_event = event_type.startswith("task.")
        if skip_no_update and is_task_event and is_no_update:
            print(f"[钉钉Bot] 任务 {execution_id or task_id} 结果为「无更新」，skip_no_update_notify=true，跳过推送")
            return self._make_result(event_type, title, content, configured, skipped=True)

        # ===== 更新日统计（不管是否推送单条消息，都累计统计） =====
        if is_task_event and stats_info:
            try:
                self._record_task_to_daily_stats(
                    task_type_label=stats_info.get("task_type_label", ""),
                    task_id=stats_info.get("task_id", ""),
                    task_name=stats_info.get("task_name", ""),
                    is_no_update=stats_info.get("is_no_update", False),
                    is_failed=stats_info.get("is_failed", False),
                    is_started=stats_info.get("is_started", False),
                    update_content=stats_info.get("update_content", ""),
                    latest_update_time=stats_info.get("latest_update_time", ""),
                    latest_episode=stats_info.get("latest_episode", ""),
                    first_run_of_task=stats_info.get("first_run_of_task", False),
                    error_text=stats_info.get("error_text", ""),
                )
            except Exception as exc:
                print(f"[钉钉Bot][日统计] 记录任务失败: {exc}")

        # ===== 发送单条任务消息 =====
        if configured:
            # 发送前记录去重信息
            dedup_key = f"{execution_id or task_id}:{event_type}"
            self._sent_tasks.add(dedup_key)
            self._sent_task_times[dedup_key] = time.time()
            self._do_send(title, content, event_type)

        # ===== 检查是否需要推送日统计（配置保存后的测试推送等） =====
        try:
            self._maybe_push_daily_stats(cfg)
        except Exception as exc:
            print(f"[钉钉Bot][日统计] 推送失败: {exc}")

        return self._make_result(event_type, title, content, configured)

    def _do_send(self, title: str, content: str, event_type: str) -> None:
        """异步提交钉钉发送任务到线程池，不阻塞事件回调线程。"""
        category = self._category_for(event_type)
        emoji = CATEGORY_EMOJI.get(category, "🔔")
        full_title = f"{emoji} {title}"
        # 捕获当前配置快照，防止异步发送时配置已变更
        config_snapshot = dict(self._resolve_config())
        print(f"[钉钉Bot] 异步提交发送: {full_title}")
        self._sender_executor.submit(self._send_worker, full_title, content, config_snapshot)

    # ==================== 日统计功能 ====================

    def _get_daily_stats_file(self) -> str:
        """获取日统计数据文件路径。"""
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "daily_stats.json")

    def _today_str(self) -> str:
        """获取今日日期字符串（YYYY-MM-DD）。"""
        import datetime
        return datetime.datetime.now().strftime("%Y-%m-%d")

    def _load_daily_stats(self) -> dict[str, Any]:
        """加载日统计数据，跨天自动清零。"""
        import os, json
        today = self._today_str()
        # 内存中有就先用内存的
        if self._daily_stats and self._daily_stats.get("date") == today:
            return self._daily_stats
        fpath = self._get_daily_stats_file()
        data: dict[str, Any] = {"date": today, "by_type": {}}
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if loaded.get("date") == today:
                    data = loaded
                else:
                    print(f"[钉钉Bot][日统计] 日期变更 {loaded.get('date')} -> {today}，重置统计")
            except Exception as exc:
                print(f"[钉钉Bot][日统计] 加载文件失败: {exc}，使用新统计")
        self._daily_stats = data
        self._daily_stats_dirty = False
        return data

    def _save_daily_stats(self, data: dict[str, Any]) -> None:
        """保存日统计数据到文件。"""
        import json
        if not self._daily_stats_dirty:
            return
        fpath = self._get_daily_stats_file()
        try:
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._daily_stats_dirty = False
        except Exception as exc:
            print(f"[钉钉Bot][日统计] 保存文件失败: {exc}")

    def _record_task_to_daily_stats(
        self,
        task_type_label: str,
        task_id: str,
        task_name: str,
        is_no_update: bool,
        is_failed: bool,
        is_started: bool,
        update_content: str,
        latest_update_time: str,
        latest_episode: str,
        first_run_of_task: bool,
        error_text: str,
    ) -> None:
        """记录单条任务到日统计。"""
        if is_started:
            return
        if not task_type_label:
            task_type_label = "未知任务"

        stats = self._load_daily_stats()
        today = self._today_str()
        if stats.get("date") != today:
            stats = {"date": today, "by_type": {}}

        type_data = stats["by_type"].get(task_type_label)
        if not type_data:
            type_data = {
                "total": 0,       # 累计执行
                "updated": 0,     # 成功更新（有新内容）
                "failed": 0,      # 失败
                "no_update": 0,   # 无更新
                "updates": [],    # 更新明细（所有有更新的任务，包括首次转入）
                "failures": [],   # 失败明细
            }
            stats["by_type"][task_type_label] = type_data

        type_data["total"] += 1
        if is_failed:
            type_data["failed"] += 1
            type_data["failures"].append({
                "task_id": task_id,
                "task_name": task_name,
                "error_text": error_text or "未知错误",
            })
        elif is_no_update:
            type_data["no_update"] += 1
        else:
            type_data["updated"] += 1
            # 只要有更新内容就记录（包括首次转入），但首次转入会标注
            if update_content or latest_episode:
                type_data["updates"].append({
                    "task_id": task_id,
                    "task_name": task_name,
                    "update_content": update_content,
                    "latest_update_time": latest_update_time,
                    "latest_episode": latest_episode,
                    "first_run": first_run_of_task,
                })

        self._daily_stats = stats
        self._daily_stats_dirty = True
        self._save_daily_stats(stats)

    def _build_daily_stats_message(self, cfg: dict[str, Any]) -> str | None:
        """构建日统计推送消息。返回 None 表示不需要推送。"""
        show_daily_stats = bool(cfg.get("daily_stats_enable"))
        show_daily_updates = bool(cfg.get("daily_updates_enable"))
        if not show_daily_stats and not show_daily_updates:
            return None

        stats = self._load_daily_stats()
        today = stats.get("date", self._today_str())
        by_type = stats.get("by_type", {})
        if not by_type:
            return None

        lines: list[str] = []
        lines.append(f"📅 统计日期：{today}")
        lines.append("─" * 20)

        # 任务日统计情况
        if show_daily_stats:
            lines.append("📋 任务执行情况：")
            for type_label, type_data in sorted(by_type.items()):
                total = type_data.get("total", 0)
                updated = type_data.get("updated", 0)
                failed = type_data.get("failed", 0)
                no_update = type_data.get("no_update", 0)
                lines.append(
                    f"  🏷️ {type_label}：累计执行 {total} 个｜"
                    f"成功更新 {updated} 个｜失败 {failed} 个｜无更新 {no_update} 个"
                )
            lines.append("")

        # 当日更新情况（含首次转入）
        if show_daily_updates:
            lines.append("🆕 当日更新情况：")
            has_any_update = False
            for type_label, type_data in sorted(by_type.items()):
                updates = type_data.get("updates", [])
                if not updates:
                    continue
                has_any_update = True
                lines.append(f"  🏷️ {type_label}（{len(updates)} 个任务有更新）")
                for idx, u in enumerate(updates, 1):
                    tid = u.get("task_id", "")
                    tname = u.get("task_name", "")
                    ucontent = u.get("update_content", "")
                    utime = u.get("latest_update_time", "")
                    uepisode = u.get("latest_episode", "")
                    first_run = bool(u.get("first_run", False))
                    prefix_parts = []
                    if tid:
                        prefix_parts.append(f"[{tid}]")
                    prefix_parts.append(tname or "未知任务")
                    if first_run:
                        prefix_parts.append("（首次转入）")
                    prefix = " ".join(prefix_parts)
                    lines.append(f"    {idx}. {prefix}")
                    if ucontent:
                        lines.append(f"       📝 更新内容：{ucontent}")
                    if uepisode:
                        lines.append(f"       🎬 最新剧集：{uepisode}")
                    if utime:
                        lines.append(f"       🕐 更新时间：{utime}")
            if not has_any_update:
                lines.append("  （今日暂无非首次的更新记录）")
            lines.append("")

        # 失败任务情况
        any_failures = False
        for type_label, type_data in sorted(by_type.items()):
            failures = type_data.get("failures", [])
            if failures:
                any_failures = True
                break
        if any_failures:
            lines.append("❌ 失败任务：")
            for type_label, type_data in sorted(by_type.items()):
                failures = type_data.get("failures", [])
                if not failures:
                    continue
                lines.append(f"  🏷️ {type_label}（{len(failures)} 个任务失败）")
                for idx, f in enumerate(failures, 1):
                    tid = f.get("task_id", "")
                    tname = f.get("task_name", "")
                    err = f.get("error_text", "未知错误")
                    prefix = f"[{tid}] {tname}" if tid else tname
                    lines.append(f"    {idx}. {prefix}")
                    lines.append(f"       💥 失败原因：{err}")

        return "\n".join(lines)

    def _maybe_push_daily_stats(self, cfg: dict[str, Any]) -> None:
        """检查是否需要推送日统计，需要的话就推送。

        触发条件（任一满足即推送）：
          1. 配置保存后的测试推送（_daily_stats_test_pending）—— 只推一次
          2. 到达任一每日定时推送时间（支持多个，如 08:30,20:00）—— 每天每个时间点只推一次
        """
        should_push = False
        reason = ""
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        # 跨天清零：今日已推送时间点集合
        if self._daily_stats_last_push_date != today_str:
            self._daily_stats_pushed_times.clear()
            self._daily_stats_last_push_date = today_str

        # 1. 配置保存后的测试推送（只推一次）
        if self._daily_stats_test_pending:
            should_push = True
            reason = "配置保存测试"
            self._daily_stats_test_pending = False
            self._daily_stats_test_done = True

        # 2. 每日定时推送（支持多个时间点，逗号/顿号/分号分隔）
        # 只要日统计或当日更新任一开关开启，就检查定时时间
        if not should_push and (bool(cfg.get("daily_stats_enable")) or bool(cfg.get("daily_updates_enable"))):
            push_time_raw = str(cfg.get("daily_stats_push_time") or "23:55").strip()
            # 兼容多种分隔符：, 、 ; 以及空格，兼容 08-30 写法
            import re as _re
            time_candidates = [
                t.strip().replace("-", ":")
                for t in _re.split(r"[,、;；\s]+", push_time_raw)
                if t.strip()
            ]
            for t in time_candidates:
                try:
                    hour_str, minute_str = t.split(":")
                    push_hour = int(hour_str)
                    push_minute = int(minute_str)
                    already_past = (
                        now.hour > push_hour
                        or (now.hour == push_hour and now.minute >= push_minute)
                    )
                    # 该时间点今天还没推送过
                    not_pushed = t not in self._daily_stats_pushed_times
                    if already_past and not_pushed:
                        should_push = True
                        reason = f"每日定时（{t}）"
                        self._daily_stats_pushed_times.add(t)
                        break
                except (ValueError, AttributeError):
                    # 时间格式解析失败就跳过
                    continue

        if not should_push:
            return

        if not bool(str(cfg.get("webhook_url") or "").strip()):
            print(f"[钉钉Bot][日统计] webhook 未配置，跳过{reason}推送")
            return

        msg = self._build_daily_stats_message(cfg)
        config_snapshot = dict(self._resolve_config())
        if msg is None:
            # 两个开关都关闭或者还没有数据，但测试模式下还是推个提示
            if "测试" in reason:
                test_msg = (
                    f"📊 任务日统计（测试推送）\n"
                    f"─" * 20 + "\n"
                    f"当前日统计相关开关：\n"
                    f"  - 任务日统计情况推送：{'✅ 开启' if bool(cfg.get('daily_stats_enable')) else '❌ 关闭'}\n"
                    f"  - 当日更新情况推送：{'✅ 开启' if bool(cfg.get('daily_updates_enable')) else '❌ 关闭'}\n"
                    f"  - 日统计推送时间：{cfg.get('daily_stats_push_time', '23:55')}\n"
                    f"\n配置已生效，如有任务完成会自动累计统计。"
                )
                self._sender_executor.submit(self._send_worker, "📊 钉钉 Bot 日统计测试", test_msg, config_snapshot)
                print(f"[钉钉Bot][日统计] {reason}推送已异步提交（开关状态提示）")
            else:
                print(f"[钉钉Bot][日统计] {reason}推送条件已满足但暂无可推送数据，跳过本次推送")
            return

        self._sender_executor.submit(self._send_worker, "📊 任务日统计", msg, config_snapshot)
        print(f"[钉钉Bot][日统计] {reason}推送已异步提交")

    def _stats_timer_loop(self) -> None:
        """独立定时轮询线程：每分钟检查一次是否到达日统计推送时间。
        不依赖任务事件触发，确保即使没有任务发生也能按时推送。
        """
        import time as _time
        print(f"[钉钉Bot][日统计] 独立定时轮询线程已启动（每30秒检查一次推送时间）")
        while not self._stats_timer_stop.is_set():
            try:
                cfg = self._resolve_config()
                if bool(cfg.get("daily_stats_enable")) or bool(cfg.get("daily_updates_enable")):
                    self._maybe_push_daily_stats(cfg)
            except Exception as exc:
                print(f"[钉钉Bot][日统计] 定时轮询异常: {exc}")
            # 每 30 秒检查一次
            self._stats_timer_stop.wait(30)

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

    def _send_worker(self, title: str, content: str, config_snapshot: dict[str, Any]) -> None:
        """线程池中的实际发送逻辑，带完整异常捕获，不影响主流程。"""
        try:
            print(f"[钉钉Bot][异步] 开始发送: {title[:50]}")
            self._send_to_dingtalk(title, content, config_snapshot)
            print(f"[钉钉Bot][异步] 发送成功: {title[:50]}")
        except Exception as exc:
            import traceback
            print(f"[钉钉Bot][异步] 发送失败: {title[:50]} - {exc}")
            traceback.print_exc()

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

    def _build_message(self, event: dict[str, Any]) -> tuple[str, str, bool, dict[str, Any]]:
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

        task_id = str(event.get("task_id") or payload.get("task_id") or _deep_find_first(all_data, "task_id") or "")
        execution_id = str(event.get("execution_id") or payload.get("execution_id") or "")

        # -------- 调用平台本地 API 获取完整数据 --------
        # 接口1: GET /api/tasks/{task_id} - 任务详情
        task_detail: dict[str, Any] = {}
        task_detail_status = "此任务不涉及"
        if task_id:
            ok, data = self._fetch_api(f"/api/tasks/{task_id}")
            if ok:
                task_detail = data if isinstance(data, dict) else {}
                # 如果返回的是 {'item': ...} 或 {'data': ...} 格式
                for wrapper_key in ["item", "data", "task"]:
                    if wrapper_key in task_detail and isinstance(task_detail[wrapper_key], dict):
                        task_detail = task_detail[wrapper_key]
                        break
                task_detail_status = "已获取"
                all_data["task_detail"] = task_detail
                # 从任务详情中提取并合并 output_payload / share_results / items
                for merge_key in ["output_payload", "input_payload", "share_results", "items", "artifacts", "results"]:
                    if merge_key in task_detail and task_detail[merge_key] is not None:
                        all_data[f"task_detail_{merge_key}"] = task_detail[merge_key]
                        if merge_key == "output_payload" and isinstance(task_detail[merge_key], dict):
                            output_payload = _deep_merge_dicts(output_payload, task_detail[merge_key])
                # 把详情中的 latest_execution 也合进来
                if "latest_execution" in task_detail and isinstance(task_detail["latest_execution"], dict):
                    all_data["latest_execution"] = task_detail["latest_execution"]
                    if not execution_id:
                        execution_id = str(task_detail["latest_execution"].get("execution_id") or "")
                    # 从 latest_execution 中提取 output_payload
                    le = task_detail["latest_execution"]
                    for merge_key in ["output_payload", "share_results", "items", "artifacts", "results", "logs"]:
                        if merge_key in le and le[merge_key] is not None:
                            all_data[f"latest_exec_{merge_key}"] = le[merge_key]
                            if merge_key == "output_payload" and isinstance(le[merge_key], dict):
                                output_payload = _deep_merge_dicts(output_payload, le[merge_key])
            else:
                task_detail_status = f"获取失败: {data}"
        print(f"[钉钉Bot][平台API] 任务详情: {task_detail_status}")

        # 接口2: GET /api/tasks/executions/{execution_id} - 执行详情
        execution_detail: dict[str, Any] = {}
        execution_detail_status = "此任务不涉及"
        if execution_id:
            ok, data = self._fetch_api(f"/api/tasks/executions/{execution_id}")
            if ok:
                execution_detail = data if isinstance(data, dict) else {}
                for wrapper_key in ["item", "data", "execution"]:
                    if wrapper_key in execution_detail and isinstance(execution_detail[wrapper_key], dict):
                        execution_detail = execution_detail[wrapper_key]
                        break
                execution_detail_status = "已获取"
                all_data["execution_detail"] = execution_detail
                # 从执行详情中提取并合并所有关键数据
                for merge_key in ["output_payload", "input_payload", "share_results", "items", "artifacts", "results", "logs", "log_entries"]:
                    if merge_key in execution_detail and execution_detail[merge_key] is not None:
                        all_data[f"exec_{merge_key}"] = execution_detail[merge_key]
                        if merge_key == "output_payload" and isinstance(execution_detail[merge_key], dict):
                            output_payload = _deep_merge_dicts(output_payload, execution_detail[merge_key])
            else:
                execution_detail_status = f"获取失败: {data}"
        print(f"[钉钉Bot][平台API] 执行详情: {execution_detail_status}")

        # 接口3: 从执行详情中提取日志（平台没有独立的执行日志API接口，已通过OpenAPI验证）
        exec_logs_api_status = "平台无独立执行日志API接口（已验证）"
        # 提前初始化日志变量（防止后续代码路径都会用到）
        local_logs = ""
        local_logs_status = "此任务不涉及"
        # 从 execution_detail 中提取日志（如果有的话）
        if execution_detail:
            exec_api_logs = execution_detail.get("logs") or execution_detail.get("log_entries") or []
            if isinstance(exec_api_logs, list) and exec_api_logs:
                log_text = "\n".join(
                    str(e.get("message") or e) if isinstance(e, dict) else str(e)
                    for e in exec_api_logs
                )
                if log_text:
                    all_data["exec_api_logs"] = exec_api_logs
                    if not local_logs:
                        local_logs = log_text
                        local_logs_status = f"从执行详情中提取日志 ({len(local_logs)}字)"
                    exec_logs_api_status = f"从执行详情提取 ({len(exec_api_logs)}条)"
        print(f"[钉钉Bot][平台API] 执行日志: {exec_logs_api_status}")

        # 接口4: GET /api/monitor/overview - 监控总览
        monitor_overview: dict[str, Any] = {}
        monitor_overview_status = "此任务不涉及"
        try:
            ok, data = self._fetch_api("/api/monitor/overview")
            if ok:
                monitor_overview = data if isinstance(data, dict) else {}
                monitor_overview_status = "已获取"
                all_data["monitor_overview"] = monitor_overview
            else:
                monitor_overview_status = f"获取失败: {data}"
        except Exception as e:
            monitor_overview_status = f"异常: {e}"
        print(f"[钉钉Bot][平台API] 监控总览: {monitor_overview_status}")

        # 接口5: GET /api/monitor/executions - 最近执行列表
        monitor_executions: list[Any] = []
        monitor_executions_status = "此任务不涉及"
        try:
            ok, data = self._fetch_api("/api/monitor/executions?limit=5")
            if ok:
                if isinstance(data, dict):
                    monitor_executions = data.get("items") or data.get("executions") or []
                elif isinstance(data, list):
                    monitor_executions = data
                monitor_executions_status = f"已获取 ({len(monitor_executions)}条)"
                all_data["monitor_executions"] = monitor_executions
            else:
                monitor_executions_status = f"获取失败: {data}"
        except Exception as e:
            monitor_executions_status = f"异常: {e}"
        print(f"[钉钉Bot][平台API] 最近执行: {monitor_executions_status}")

        # 接口6: GET /api/tasks - 任务列表（兜底用）
        tasks_list_status = "此任务不涉及"

        # 接口7: GET /api/plugins - 插件列表
        plugins_list_status = "此任务不涉及"

        # 接口8: 从本地文件系统读取任务执行日志
        if execution_id:
            # 从 output_payload 中获取精确的日志文件路径
            log_file_path = output_payload.get("log_file_path") if isinstance(output_payload, dict) else None
            if not log_file_path:
                # 也从 all_data 的各个 output_payload 中查找
                for key in ["exec_output_payload", "latest_exec_output_payload"]:
                    op = all_data.get(key)
                    if isinstance(op, dict) and op.get("log_file_path"):
                        log_file_path = op["log_file_path"]
                        break
            local_logs = self._read_local_task_logs(execution_id, log_file_path)
            if local_logs:
                local_logs_status = f"已获取 ({len(local_logs)}字)"
            else:
                local_logs_status = "未找到日志文件"
        print(f"[钉钉Bot][本地日志] {local_logs_status}")

        # 把 execution_detail 里的 logs 也合入本地日志
        if not local_logs and execution_detail:
            exec_logs = execution_detail.get("logs") or execution_detail.get("log_entries") or []
            if isinstance(exec_logs, list) and exec_logs:
                local_logs = "\n".join(
                    str(entry.get("message") or entry) if isinstance(entry, dict) else str(entry)
                    for entry in exec_logs
                )
                local_logs_status = f"从执行详情获取 ({len(local_logs)}字)"

        # 从 task_detail 中也尝试拿日志
        if not local_logs and task_detail:
            latest_exec = task_detail.get("latest_execution") or {}
            if isinstance(latest_exec, dict):
                exec_logs = latest_exec.get("logs") or latest_exec.get("log_entries") or []
                if isinstance(exec_logs, list) and exec_logs:
                    local_logs = "\n".join(
                        str(entry.get("message") or entry) if isinstance(entry, dict) else str(entry)
                        for entry in exec_logs
                    )
                    local_logs_status = f"从任务详情获取 ({len(local_logs)}字)"

        # -------- 任务名称（优先从各个层级查找） --------
        # payload.task_title 是最完整的任务名（如 "九门 (2026) 4K... - 订阅转存"）
        task_title_val = (
            task_detail.get("task_title")
            or task_detail.get("title")
            or payload.get("task_title")
            or output_payload.get("task_title")
            or _deep_find_first(all_data, "task_title")
        )
        task_name_candidates = []
        if isinstance(task_title_val, str) and task_title_val:
            task_name_candidates.append(task_title_val)
        task_name_candidates.extend(_deep_find(all_data, "task_name") + _deep_find(all_data, "title") + _deep_find(all_data, "share_name"))
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
        skipped_existing_local = _count("skipped_existing_local")
        recreated_missing = _count("recreated_missing")
        skipped_by_keywords = _count("skipped_by_keywords")

        # 从列表类型字段推导数量（STRM 任务等）
        def _list_count(key: str) -> int | None:
            for source_dict in [output_payload, payload, input_payload]:
                v = source_dict.get(key)
                if isinstance(v, list):
                    return len(v)
            deep = _deep_find_first(all_data, key)
            if isinstance(deep, list):
                return len(deep)
            return None

        # generated_entry_ids / processed_entry_ids (STRM 任务)
        generated_entry_count = _list_count("generated_entry_ids")
        processed_entry_count = _list_count("processed_entry_ids")
        artifacts_count = _list_count("artifacts")

        if generated_item_count is None and generated_entry_count is not None:
            generated_item_count = generated_entry_count

        # 如果顶层 saved_count 为 0 但有 artifacts，用 artifacts 数量
        if (saved_count is None or saved_count == 0) and artifacts_count is not None and artifacts_count > 0:
            if event_type == "task.completed" and "strm" in (plugin_id or ""):
                saved_count = artifacts_count

        # === 从 share_results 累加统计（每个分享有自己的统计） ===
        share_results_raw = (
            payload.get("share_results")
            or output_payload.get("share_results")
            or _deep_find_first(all_data, "share_results")
            or []
        )
        if isinstance(share_results_raw, list) and share_results_raw:
            total_saved = 0
            total_skipped = 0
            total_filtered = 0
            total_renamed = 0
            share_names: list[str] = []
            for sr in share_results_raw:
                if isinstance(sr, dict):
                    total_saved += int(sr.get("saved_count") or 0)
                    total_skipped += int(sr.get("skipped_count") or 0)
                    total_filtered += int(sr.get("filtered_count") or 0)
                    total_renamed += int(sr.get("renamed_count") or 0)
                    sn = sr.get("share_name")
                    if isinstance(sn, str) and sn:
                        share_names.append(sn)
            # 如果顶层没有统计，用累加的值
            if saved_count is None or saved_count == 0:
                saved_count = total_saved if total_saved > 0 else saved_count
            if skipped_count is None or skipped_count == 0:
                skipped_count = total_skipped if total_skipped > 0 else skipped_count
            if filtered_count is None or filtered_count == 0:
                filtered_count = total_filtered if total_filtered > 0 else filtered_count
            if renamed_count is None or renamed_count == 0:
                renamed_count = total_renamed if total_renamed > 0 else renamed_count
            print(f"[钉钉Bot] share_results统计: saved={total_saved}, skipped={total_skipped}, filtered={total_filtered}, shares={share_names}")

        # 耗时
        duration_ms = _count("duration_ms")
        duration_text = _parse_duration_ms(duration_ms)

        # 目标路径（payload.target_path 才是正确字段）
        target_dir = str(
            payload.get("target_path")
            or payload.get("target_dir")
            or input_payload.get("target_dir")
            or _deep_find_first(all_data, "target_path")
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

        # === 策略1：直接查找已知字段 ===
        direct_keys = [
            "share_results", "artifacts", "items", "results", "files",
            "entries", "saved_items", "processed_items", "transferred_items",
            "saved_files", "skipped_files", "failed_files", "transfer_results",
        ]
        for key in direct_keys:
            for src in [output_payload, payload, input_payload]:
                val = src.get(key)
                if isinstance(val, list) and val:
                    print(f"[钉钉Bot][数据侦探] 发现列表字段: {key} (长度={len(val)})")
                    all_file_items.extend(val)

        # === 策略2：全量扫描 output_payload/payload 中所有列表类型的字段 ===
        def _scan_all_lists(obj: Any, path: str = "root", max_depth: int = 6) -> list[tuple[str, list[Any]]]:
            """递归扫描对象中所有的列表字段，返回 (路径, 列表)。"""
            results: list[tuple[str, list[Any]]] = []
            if max_depth <= 0:
                return results
            if isinstance(obj, dict):
                for k, v in obj.items():
                    cur_path = f"{path}.{k}"
                    if isinstance(v, list) and len(v) > 0:
                        results.append((cur_path, v))
                    elif isinstance(v, (dict, list)):
                        results.extend(_scan_all_lists(v, cur_path, max_depth - 1))
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:5]):  # 只扫前5个防止爆
                    if isinstance(item, (dict, list)):
                        results.extend(_scan_all_lists(item, f"{path}[{i}]", max_depth - 1))
            return results

        all_lists = _scan_all_lists(output_payload, "output_payload") + _scan_all_lists(payload, "payload")

        # 过滤出可能包含文件的列表（列表元素是 dict 且有 name/filename 等字段，或是字符串）
        file_list_candidates: list[str] = []
        for path, lst in all_lists:
            if not lst:
                continue
            # 判断是否可能是文件列表
            first = lst[0]
            is_file_list = False
            if isinstance(first, str):
                is_file_list = True
            elif isinstance(first, dict):
                file_keys = {"name", "filename", "file_name", "title", "path", "source_path"}
                if set(first.keys()) & file_keys:
                    is_file_list = True
            if is_file_list:
                file_list_candidates.append(path)
                print(f"[钉钉Bot][数据侦探] 疑似文件列表: {path} (长度={len(lst)}, 首元素类型={type(first).__name__})")
                for item in lst:
                    if item not in all_file_items:
                        all_file_items.append(item)

        print(f"[钉钉Bot][数据侦探] output_payload keys={list(output_payload.keys()) if isinstance(output_payload, dict) else 'N/A'}")
        print(f"[钉钉Bot][数据侦探] payload keys={list(payload.keys()) if isinstance(payload, dict) else 'N/A'}")
        print(f"[钉钉Bot][数据侦探] 找到文件列表字段: {file_list_candidates}, 总 items 数={len(all_file_items)}")

        # 深度搜索兜底
        for key in ["items", "results", "files", "entries", "saved_items", "processed_items", "share_results", "artifacts"]:
            found = _deep_find(all_data, key)
            for f in found:
                if isinstance(f, list):
                    for item in f:
                        if item not in all_file_items:
                            all_file_items.append(item)

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

        # === 策略3：从任务日志中解析文件明细 ===
        # 例如: "skipped existing target files: 06.mkv, 01.mkv, 03.mkv"
        task_logs: list[str] = []
        # 从多个来源查找日志
        raw_logs = (
            output_payload.get("logs")
            or payload.get("logs")
            or output_payload.get("log_entries")
            or payload.get("log_entries")
            or event.get("logs")
            or event.get("log_entries")
            or []
        )
        if isinstance(raw_logs, list):
            for entry in raw_logs:
                if isinstance(entry, str):
                    task_logs.append(entry)
                elif isinstance(entry, dict):
                    msg = entry.get("message") or entry.get("msg") or ""
                    if msg:
                        task_logs.append(str(msg))
        # 深度搜索兜底
        if not task_logs:
            deep_logs = _deep_find(all_data, "logs") + _deep_find(all_data, "log_entries")
            for dl in deep_logs:
                if isinstance(dl, list):
                    for entry in dl:
                        if isinstance(entry, str):
                            task_logs.append(entry)
                        elif isinstance(entry, dict):
                            msg = entry.get("message") or entry.get("msg") or ""
                            if msg:
                                task_logs.append(str(msg))
            print(f"[钉钉Bot][日志解析] 深度搜索找到 {len(task_logs)} 条日志")
        else:
            print(f"[钉钉Bot][日志解析] 直接找到 {len(task_logs)} 条日志")

        # 从本地日志文件内容中提取（local_logs 可能是多行字符串）
        if local_logs:
            for line in local_logs.split("\n"):
                line = line.strip()
                if line:
                    task_logs.append(line)
            print(f"[钉钉Bot][日志解析] 加入本地日志行，共 {len(task_logs)} 条")

        # 从执行详情的 logs 里也提取
        if execution_detail:
            exec_logs_list = execution_detail.get("logs") or []
            if isinstance(exec_logs_list, list):
                for entry in exec_logs_list:
                    if isinstance(entry, str):
                        task_logs.append(entry)
                    elif isinstance(entry, dict):
                        msg = entry.get("message") or entry.get("msg") or ""
                        if msg:
                            task_logs.append(str(msg))

        # 从日志中提取 skipped 文件
        skipped_from_logs: list[str] = []
        saved_from_logs: list[str] = []
        for log_line in task_logs:
            # 匹配: skipped existing target files: xxx, yyy, zzz
            m = re.search(r"skipped\s+(?:existing\s+)?(?:target\s+)?files?:\s*(.+)", log_line, re.IGNORECASE)
            if m:
                files_str = m.group(1).strip()
                # 分割文件名（逗号或顿号分隔）
                parts = re.split(r"[、,，]", files_str)
                for p in parts:
                    fn = p.strip().rstrip(".")
                    if fn:
                        skipped_from_logs.append(fn)
            # 匹配: 筛选结果：保留 N 个文件，跳过 M 个
            m2 = re.search(r"筛选结果[：:]\s*保留\s*(\d+)\s*个文件[，,]\s*跳过\s*(\d+)\s*个", log_line)
            if m2:
                print(f"[钉钉Bot][日志解析] 筛选结果: 保留={m2.group(1)}, 跳过={m2.group(2)}")
            # 匹配: 转存数量：N 或 已转存 N 项
            m3 = re.search(r"(?:转存数量|已转存)[：:]\s*(\d+)", log_line)
            if m3:
                print(f"[钉钉Bot][日志解析] 转存数量: {m3.group(1)}")

        # 合并日志解析出的文件（去重）
        if skipped_from_logs:
            for fn in skipped_from_logs:
                if fn not in skipped_files:
                    skipped_files.append(fn)
            print(f"[钉钉Bot][日志解析] 从日志提取跳过文件: {len(skipped_from_logs)} 个")

        if saved_from_logs:
            for fn in saved_from_logs:
                if fn not in saved_files:
                    saved_files.append(fn)
            print(f"[钉钉Bot][日志解析] 从日志提取成功文件: {len(saved_from_logs)} 个")

        print(f"[钉钉Bot][最终统计] saved_files={len(saved_files)}, skipped_files={len(skipped_files)}, failed_files={len(failed_files)}")

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
            # STRM 任务用"生成"更准确
            if "strm" in (plugin_id or ""):
                stat_parts.append(f"生成 {saved_count} 项")
            else:
                stat_parts.append(f"转存成功 {saved_count} 项")
        elif transferred_count is not None and transferred_count > 0:
            stat_parts.append(f"已转存 {transferred_count} 项")
        if generated_item_count is not None and generated_item_count > 0:
            # 避免和上面的 saved_count 重复
            if saved_count is None or saved_count == 0 or "strm" not in (plugin_id or ""):
                stat_parts.append(f"生成 {generated_item_count} 项")
        if processed_entry_count is not None and processed_entry_count > 0 and processed_entry_count != generated_item_count:
            stat_parts.append(f"处理 {processed_entry_count} 项")
        if new_item_count is not None and new_item_count > 0:
            stat_parts.append(f"新增 {new_item_count} 项")
        if skipped_existing_local is not None and skipped_existing_local > 0:
            stat_parts.append(f"本地跳过 {skipped_existing_local} 项")
        if recreated_missing is not None and recreated_missing > 0:
            stat_parts.append(f"补全缺失 {recreated_missing} 项")
        if skipped_by_keywords is not None and skipped_by_keywords > 0:
            stat_parts.append(f"关键词排除 {skipped_by_keywords} 项")
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

        # 判断是否无更新（失败任务永远不算无更新！）
        is_no_update = False
        if not is_failed:
            # 1) 数据标志判断：计数为 0 且有跳过/无更新天数
            if (
                (saved_count is not None and saved_count == 0)
                or (transferred_count is not None and transferred_count == 0)
                or (generated_item_count is not None and generated_item_count == 0)
            ) and (
                (skipped_count is not None and skipped_count > 0)
                or (no_update_days is not None and no_update_days > 0)
            ):
                is_no_update = True
            # 2) summary/detail_message 关键词兜底：平台已明确说明"没有新的/无需生成/无更新"
            if not is_no_update:
                no_update_keywords = (
                    "没有新的官网条目",
                    "没有需要下载的文件",
                    "没有需要转存的文件",
                    "没有新的内容需要",
                    "无需生成新",
                    "无更新（已存在相同文件）",
                    "没有缺失的本地",
                    "没有新的缺失",
                )
                keyword_text = " ".join(x for x in (summary, detail_message) if x)
                if keyword_text and any(kw in keyword_text for kw in no_update_keywords):
                    is_no_update = True

        # ==================== 按移动端友好简洁模板构建消息 ====================

        # 触发类型转中文
        trigger_cn = TRIGGER_SOURCE_CN.get(trigger_source, trigger_source or "未知")

        # 任务类型标签（更友好的显示）
        type_display = task_type_label or "未知任务"

        # 判断状态
        is_failed = event_type == "task.failed" or status == "failed" or (error_text and not saved_count and not generated_item_count)
        is_started = event_type == "task.started"
        is_no_update = bool(is_no_update)

        # 组装日统计需要的信息
        # 判断是否首次转入（之前没转过，skipped_count 为 0 且有实际新增内容）
        has_new_content = (
            (saved_count is not None and saved_count > 0)
            or (generated_item_count is not None and generated_item_count > 0)
        )
        first_run_of_task = bool(has_new_content and (skipped_count is None or skipped_count == 0))
        # 组装更新内容简述
        update_parts = []
        if saved_count is not None and saved_count > 0:
            saved_files_preview = _format_file_list(saved_files, max_items=5) if saved_files else ""
            update_parts.append(f"转存成功 {saved_count} 项{saved_files_preview}")
        if generated_item_count is not None and generated_item_count > 0:
            gen_preview = _format_file_list(saved_files, max_items=5) if saved_files else ""
            update_parts.append(f"生成 {generated_item_count} 项{gen_preview}")
        update_content = "｜".join(update_parts)
        # 最新剧集名
        latest_episode_name = latest_episode_file or (f"第 {latest_episode_number} 集" if latest_episode_number else "")
        stats_info: dict[str, Any] = {
            "task_id": task_id,
            "task_name": task_name,
            "task_type_label": task_type_label,
            "is_failed": is_failed,
            "is_started": is_started,
            "is_no_update": is_no_update,
            "update_content": update_content,
            "latest_update_time": latest_episode_update_time or "",
            "latest_episode": latest_episode_name,
            "first_run_of_task": first_run_of_task,
            "error_text": error_text or "",
        }

        # 状态显示
        if is_failed:
            status_display = "❌ 失败"
        elif is_started:
            status_display = "▶️ 开始"
        elif is_no_update:
            status_display = "无更新"
        elif status == "skipped":
            status_display = "已跳过"
        else:
            status_display = "完成"

        # 获取通知内容显示配置（字段级开关 + 调试区开关）
        _cfg = self._resolve_config()
        show_api = bool(_cfg.get("show_api_status"))
        show_logs = bool(_cfg.get("show_task_logs"))
        show_debug = bool(_cfg.get("show_debug_info"))
        show_task_id = bool(_cfg.get("show_task_id", True))
        show_duration = bool(_cfg.get("show_duration", True))
        show_task_type = bool(_cfg.get("show_task_type", True))
        show_trigger = bool(_cfg.get("show_trigger", True))
        show_task_name = bool(_cfg.get("show_task_name", True))
        show_status = bool(_cfg.get("show_status", True))
        show_target_path = bool(_cfg.get("show_target_path", True))
        show_exec_summary = bool(_cfg.get("show_exec_summary", True))
        show_skip_detail = bool(_cfg.get("show_skip_detail", True))
        show_save_detail = bool(_cfg.get("show_save_detail", True))
        show_latest_episode = bool(_cfg.get("show_latest_episode", True))
        show_update_time = bool(_cfg.get("show_update_time", True))
        show_other_notes = bool(_cfg.get("show_other_notes", True))
        show_divider = bool(_cfg.get("show_divider", True))

        # 分隔线辅助函数
        def _div() -> None:
            if show_divider:
                lines.append("──────────────")

        lines: list[str] = []

        # ===== 判断事件大类，选择不同模板 =====
        is_system_event = event_type.startswith("system.")
        is_plugin_event = event_type.startswith("plugin.")

        if is_system_event or is_plugin_event:
            # ===== 系统/插件事件模板 =====
            event_label = EVENT_CATEGORY.get(event_type, event_type)
            now_str = datetime.datetime.now().strftime("%m月%d日 %H:%M")

            # 从payload提取信息
            p_msg = str(payload.get("message") or payload.get("detail") or "") if isinstance(payload, dict) else ""
            p_name = str(payload.get("plugin_name") or payload.get("name") or payload.get("plugin_id") or "") if isinstance(payload, dict) else ""
            p_version = str(payload.get("version") or "") if isinstance(payload, dict) else ""

            if show_task_name:
                if is_system_event:
                    lines.append(f"📌 系统事件：{event_label}")
                else:
                    lines.append(f"📌 插件事件：{event_label}")
            if show_task_type:
                lines.append(f"🏷️ 类型：{event_label}")
            if show_update_time:
                lines.append(f"🕐 时间：{now_str}")

            _div()

            if is_plugin_event:
                if p_name:
                    lines.append(f"🔌 插件名称：{p_name}")
                if p_version:
                    lines.append(f"📝 插件版本：{p_version}")
                if p_msg:
                    lines.append(f"📄 详细信息：{p_msg}")
            else:
                # 系统事件
                if event_type == "system.startup":
                    lines.append("💬 平台服务已启动，可开始使用")
                elif event_type == "system.shutdown":
                    lines.append("💬 平台服务正在关闭/重启")
                if p_msg:
                    lines.append(f"📄 详细信息：{p_msg}")

            _div()

            if show_other_notes:
                if is_system_event:
                    if event_type == "system.startup":
                        lines.append("💬 其他说明：系统已就绪，所有定时任务将按计划执行")
                    else:
                        lines.append("💬 其他说明：系统服务已停止，如有疑问请查看服务器日志")
                else:
                    if event_type == "plugin.installed":
                        lines.append(f"💬 其他说明：插件{p_name}已安装，可在插件管理中查看配置")
                    else:
                        lines.append(f"💬 其他说明：插件{p_name}已卸载，相关功能已停止")

        elif is_started:
            # ===== 任务开始模板 =====
            if show_task_id and task_id:
                lines.append(f"🆔 任务ID：{task_id}")
            if show_duration and duration_text:
                lines.append(f"⏱️ 耗时：{duration_text}")
            if show_task_type:
                lines.append(f"🏷️ 任务类型：{type_display}")
            if show_trigger:
                lines.append(f"⚡ 触发方式：{trigger_cn}")
            if show_task_name:
                lines.append(f"📌 任务名称：{task_name}")
            if show_status:
                lines.append("📊 任务状态：开始执行")
            if show_target_path and target_dir:
                lines.append(f"📂 目标路径：{target_dir}")
        else:
            # ===== 任务完成/失败/取消/创建模板（方案5：字段单独一行 + 短横线 + 图标） =====
            # 基础信息区
            if show_task_id and task_id:
                lines.append(f"🆔 任务ID：[{task_id}]")
            if show_duration and duration_text:
                lines.append(f"⏱️ 耗时：{duration_text}")
            if show_task_type:
                lines.append(f"🏷️ 任务类型：{type_display}")
            if show_trigger:
                lines.append(f"⚡ 触发方式：{trigger_cn}")
            if show_task_name:
                lines.append(f"📌 任务名称：{task_name}")
            if show_status:
                lines.append(f"📊 任务状态：{status_display}")
            if show_target_path and target_dir:
                lines.append(f"📂 目标路径：{target_dir}")

            _div()

            # 执行情况统计
            if show_exec_summary:
                exec_parts = []
                if saved_count is not None:
                    verb = "生成" if "strm" in (plugin_id or "") else "转存成功"
                    exec_parts.append(f"{verb} {saved_count} 项")
                elif transferred_count is not None and transferred_count > 0:
                    exec_parts.append(f"转存 {transferred_count} 项")
                if generated_item_count is not None and generated_item_count > 0 and "strm" not in (plugin_id or ""):
                    exec_parts.append(f"生成 {generated_item_count} 项")
                if skipped_count is not None and skipped_count > 0:
                    exec_parts.append(f"跳过 {skipped_count} 项")
                if filtered_count is not None and filtered_count > 0:
                    exec_parts.append(f"过滤 {filtered_count} 项")
                if failed_count is not None and failed_count > 0:
                    exec_parts.append(f"失败 {failed_count} 项")
                if renamed_count is not None and renamed_count > 0:
                    exec_parts.append(f"重命名 {renamed_count} 项")
                if processed_entry_count is not None and processed_entry_count > 0:
                    exec_parts.append(f"处理 {processed_entry_count} 项")
                if exec_parts:
                    lines.append(f"📈 执行情况：{'｜'.join(exec_parts)}")

            # 跳过明细
            if show_skip_detail:
                if skipped_files:
                    skipped_display = _format_file_list(skipped_files)
                    lines.append(f"⏭️  跳过明细：{skipped_display}")
                elif skipped_count is not None and skipped_count > 0 and not skipped_files:
                    skip_note = "（首次转入无跳过）" if saved_count is not None and saved_count > 0 else "（本地已存在）"
                    lines.append(f"⏭️  跳过明细：{skipped_count} 项{skip_note}")

            # 转存/生成情况
            if show_save_detail:
                if saved_files and (saved_count is None or saved_count > 0):
                    saved_display = _format_file_list(saved_files)
                    verb = "生成" if "strm" in (plugin_id or "") else "转存"
                    lines.append(f"✅ {verb}情况：{len(saved_files)} 项，{saved_display}")
                elif saved_count is not None and saved_count > 0:
                    verb = "生成" if "strm" in (plugin_id or "") else "转存"
                    lines.append(f"✅ {verb}情况：{saved_count} 项")
                elif saved_count is not None and saved_count == 0:
                    verb = "生成" if "strm" in (plugin_id or "") else "转存"
                    lines.append(f"✅ {verb}情况：0 项（首次转入无{verb} / 全部已存在）")

            # 最新剧集
            if show_latest_episode:
                episode_display_parts = []
                if latest_episode_file:
                    episode_display_parts.append(latest_episode_file)
                elif latest_episode_number is not None:
                    episode_display_parts.append(f"第 {latest_episode_number} 集")
                if not episode_display_parts:
                    for f in (skipped_files + saved_files)[:2]:
                        if f not in episode_display_parts:
                            episode_display_parts.append(f)
                if episode_display_parts:
                    shown = episode_display_parts[:2]
                    lines.append(f"🆕 最新剧集：{'、'.join(shown)}")

            # 更新日期
            if show_update_time and latest_episode_update_time:
                lines.append(f"🕐 更新日期：{latest_episode_update_time}")

            _div()

            # 其他说明
            if show_other_notes:
                other_parts = []
                if is_no_update:
                    other_parts.append("本次无更新（已存在相同文件）")
                if is_failed and error_text:
                    other_parts.append(f"失败原因：{error_text}")
                elif error_text and event_type != "task.failed" and not is_failed:
                    other_parts.append(error_text)
                if failed_files:
                    failed_display = _format_file_list(failed_files)
                    other_parts.append(f"失败文件：{failed_display}")
                if not other_parts and detail_message and not is_failed:
                    other_parts.append(detail_message)

                if other_parts:
                    lines.append(f"💬 其他说明：{'；'.join(other_parts)}")
                else:
                    lines.append("💬 其他说明：无")

        # ==================== 平台接口获取状态区（可配置，默认关闭） ====================
        if show_api:
            lines.append("")
            lines.append("─" * 10 + " 平台接口 " + "─" * 10)
            # 显示当前实际使用的API配置（方便排查401问题）
            __api_base_used = self._api_base or "未探测"
            __api_cfg = self._resolve_config()
            __cfg_base = str(__api_cfg.get("t3_api_base") or "")
            __cfg_key = str(__api_cfg.get("t3_api_key") or "")
            __cfg_header = str(__api_cfg.get("t3_api_header") or "")
            __all_cfg_keys = sorted(list(__api_cfg.keys()))
            __key_mask = __cfg_key[:8] + "***" + __cfg_key[-4:] if len(__cfg_key) > 12 else ("已设置" if __cfg_key else "未设置")
            lines.append(f"🔧 API: {__cfg_base or '未设置'}")
            lines.append(f"📍 实际: {__api_base_used}")
            lines.append(f"📋 任务: {task_detail_status}")
            lines.append(f"⚙️  执行: {execution_detail_status}")
            lines.append(f"📜 日志: {exec_logs_api_status}")
            lines.append(f"📊 监控: {monitor_overview_status}")
            lines.append(f"📈 列表: {monitor_executions_status}")

        # ==================== 任务执行滚动日志（可配置，默认关闭） ====================
        if show_logs and local_logs:
            lines.append("")
            lines.append("─" * 10 + " 任务日志 " + "─" * 10)
            log_lines = local_logs.strip().split("\n")
            if len(log_lines) > 30:
                shown = log_lines[-30:]
                lines.extend(shown)
                lines.append(f"…（共{len(log_lines)}行）")
            else:
                lines.extend(log_lines)

        # ==================== 调试数据区（可配置，默认关闭） ====================
        if show_debug:
            lines.append("")
            lines.append("─" * 10 + " 调试数据 " + "─" * 10)

            def _safe_json(obj: Any, max_len: int = 3000) -> str:
                """安全地 JSON 序列化，截断过长内容。"""
                try:
                    text = json.dumps(obj, ensure_ascii=False, default=str)
                    if len(text) > max_len:
                        return text[:max_len] + f"…（共{len(text)}字）"
                    return text
                except Exception as e:
                    return f"<序列化失败: {e}>"

            # 列出所有顶层 keys
            lines.append(f"event: {list(event.keys())}")
            lines.append(f"payload: {list(payload.keys()) if isinstance(payload, dict) else 'N/A'}")
            if task_detail:
                lines.append(f"task_detail: {list(task_detail.keys()) if isinstance(task_detail, dict) else 'N/A'}")
            if execution_detail:
                lines.append(f"exec_detail: {list(execution_detail.keys()) if isinstance(execution_detail, dict) else 'N/A'}")

            # payload 全量
            if payload:
                lines.append("")
                lines.append("📦 payload:")
                lines.append(_safe_json(payload, 2000))

        # ==================== 标题构建 ====================
        category = self._category_for(event_type)
        task_id_prefix = f"[{task_id}] " if task_id else ""

        # 系统/插件事件标题
        if is_system_event:
            event_label = EVENT_CATEGORY.get(event_type, event_type)
            title = f"🔔 {event_label}"
            return title, "\n".join(lines) if lines else f"系统事件：{event_type}", False, {}

        if is_plugin_event:
            event_label = EVENT_CATEGORY.get(event_type, event_type)
            p_name = str(payload.get("plugin_name") or payload.get("name") or payload.get("plugin_id") or "") if isinstance(payload, dict) else ""
            title = f"🔌 {event_label}：{p_name}" if p_name else f"🔌 {event_label}"
            return title, "\n".join(lines) if lines else f"插件事件：{event_type}", False, {}

        # 任务事件标题
        if event_type == "task.completed":
            if is_no_update:
                title = f"{task_id_prefix}{task_name} · 无更新"
            else:
                title = f"{task_id_prefix}{task_name} · 完成"
            return title, "\n".join(lines) if lines else f"{task_name} 已完成。", is_no_update, stats_info

        if event_type == "task.failed":
            title = f"{task_id_prefix}{task_name} · 失败"
            if error_text:
                lines.insert(0, f"❌ {error_text}")
            return title, "\n".join(lines) if lines else f"{task_name} 执行失败：{error_text or '未知错误'}", False, stats_info

        if event_type == "task.canceled":
            title = f"{task_id_prefix}{task_name} · 已取消"
            return title, "\n".join(lines) if lines else f"{task_name} 已取消。", False, stats_info

        if event_type == "task.started":
            title = f"{task_id_prefix}{task_name} · 开始"
            return title, "\n".join(lines) if lines else f"{task_name} 已开始执行。", False, stats_info

        if event_type == "task.created":
            title = f"{task_id_prefix}{task_name} · 已创建"
            return title, "\n".join(lines) if lines else f"已创建新任务：{task_name}", False, stats_info

        # post_execute 系列事件（STRM、下载、转存等）—— 也统一带状态后缀
        if is_no_update:
            title = f"{task_id_prefix}{task_name} · 无更新"
        elif is_failed:
            title = f"{task_id_prefix}{task_name} · 失败"
        else:
            title = f"{task_id_prefix}{task_name} · 完成"
        if summary and not lines:
            lines.append(summary)
        return title, "\n".join(lines) if lines else f"{task_name}：{event_type}", is_no_update, stats_info


plugin = DingdingBotAutomationPlugin()
