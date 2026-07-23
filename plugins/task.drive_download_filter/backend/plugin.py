from __future__ import annotations

import re
from typing import Any

from core.sdk import (
    BasePlugin,
    HealthReport,
    OperationResult,
    TaskExecutionResult,
    TaskTemplate,
    TaskTypeProvider,
)

DEFAULT_EPISODE_PATTERNS = [
    r"[第\s]*(\d{1,4})[集话話期回]",
    r"\bE?(\d{1,4})\b",
    r"-(\d{1,4})\s*\[",
    r"\[(\d{1,4})\]",
    r"\.(\d{1,4})\.",
    r"_(\d{1,4})_",
    r"\b(\d{1,4})\s*of\s*\d{1,4}\b",
    r"EP?\s*(\d{1,4})",
]

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv",
    ".webm", ".m4v", ".ts", ".mpg", ".mpeg", ".rmvb",
    ".rm", ".3gp", ".vob", ".ogv",
}


class DriveDownloadFilterPlugin(BasePlugin, TaskTypeProvider):
    plugin_id = "task.drive_download_filter"
    plugin_name = "网盘下载集数过滤"
    plugin_version = "0.3.0"

    def __init__(self) -> None:
        self._runtime_config: dict[str, Any] = {}

    def set_runtime_config(self, config: dict[str, Any]) -> None:
        self._runtime_config = dict(config or {})

    def health(self, ctx: dict[str, Any]) -> HealthReport:
        return HealthReport(
            status="ok",
            message=f"{self.plugin_name} 运行正常。",
            details={
                "default_start": self._runtime_config.get("default_start_episode", 1),
                "default_end": self._runtime_config.get("default_end_episode", 0),
                "default_latest_count": self._runtime_config.get("default_latest_count", 0),
                "default_skip_downloaded": self._runtime_config.get("default_skip_downloaded", True),
                "rules_count": len(self._parse_all_rules()),
                "configured_patterns": self._get_episode_patterns(),
            },
        )

    def get_template(self) -> TaskTemplate:
        default_start = int(self._runtime_config.get("default_start_episode", 1) or 1)
        default_end = int(self._runtime_config.get("default_end_episode", 0) or 0)
        default_latest = int(self._runtime_config.get("default_latest_count", 0) or 0)
        default_skip = bool(self._runtime_config.get("default_skip_downloaded", True))

        return TaskTemplate(
            type_key="drive_download_filter",
            template_key="drive_download_filter",
            plugin_id=self.plugin_id,
            title="网盘下载集数过滤",
            description="在下载网盘内容前，根据集数范围、最新N集或屏蔽列表过滤需要下载的剧集。支持跳过已下载的集数。",
            allow_manual_creation=True,
            supported_inputs=["manual", "resource"],
            form_schema=[
                {
                    "key": "resource_title",
                    "label": "资源名称",
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "当前处理的资源名称，用于匹配过滤规则。",
                },
                {
                    "key": "drive_items",
                    "label": "网盘内容列表",
                    "type": "array",
                    "required": True,
                    "default": [],
                    "description": "待过滤的网盘文件/文件夹列表，每项应包含 name、type、id 等字段。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "网盘条目 ID"},
                            "name": {"type": "string", "description": "文件或文件夹名称"},
                            "type": {"type": "string", "description": "类型：file 或 folder"},
                            "parent_id": {"type": "string", "description": "父目录 ID"},
                        },
                    },
                },
                {
                    "key": "filter_mode",
                    "label": "过滤模式",
                    "type": "select",
                    "required": True,
                    "default": "range",
                    "description": "选择集数过滤的方式。",
                    "options": [
                        {"value": "range", "label": "按集数范围（起始集~结束集）"},
                        {"value": "latest", "label": "只下载最新N集"},
                        {"value": "range_latest", "label": "范围+最新N集（两者交集）"},
                    ],
                },
                {
                    "key": "start_episode",
                    "label": "起始集数",
                    "type": "integer",
                    "required": False,
                    "default": default_start,
                    "description": "从第几集开始下载，1 表示从第1集开始。在「最新N集」模式下此选项无效。",
                    "min": 1,
                },
                {
                    "key": "end_episode",
                    "label": "结束集数",
                    "type": "integer",
                    "required": False,
                    "default": default_end,
                    "description": "下载到第几集结束，0 表示不设上限（直到最新一集）。在「最新N集」模式下此选项无效。",
                    "min": 0,
                },
                {
                    "key": "latest_count",
                    "label": "下载最新N集",
                    "type": "integer",
                    "required": False,
                    "default": default_latest,
                    "description": "只下载最新的N集（从最大集数倒推）。例如：有1-120集，设为3则下载118、119、120。0表示不启用此功能。",
                    "min": 0,
                },
                {
                    "key": "blocked_episodes",
                    "label": "屏蔽集数列表",
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "手动标记不下载的集数，多个用英文逗号分隔，例如：3,5,8-12。",
                },
                {
                    "key": "skip_downloaded",
                    "label": "跳过已下载集数",
                    "type": "boolean",
                    "required": False,
                    "default": default_skip,
                    "description": "是否跳过已经下载过的集数。需要在下方填写已下载的集数范围。",
                },
                {
                    "key": "downloaded_episodes",
                    "label": "已下载的集数",
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "已经下载过的集数，会被自动跳过。格式同屏蔽集数，例如：1-80 或 1,3,5-10。",
                },
                {
                    "key": "blocked_item_ids",
                    "label": "屏蔽的网盘条目",
                    "type": "array",
                    "required": False,
                    "default": [],
                    "description": "手动选择不下载的具体网盘文件/文件夹 ID 列表。",
                    "items": {"type": "string"},
                },
                {
                    "key": "only_video_files",
                    "label": "仅识别视频文件",
                    "type": "boolean",
                    "required": False,
                    "default": True,
                    "description": "是否仅对视频文件进行集数识别和过滤，推荐开启。",
                },
                {
                    "key": "include_folders",
                    "label": "包含文件夹匹配",
                    "type": "boolean",
                    "required": False,
                    "default": True,
                    "description": "是否也对文件夹名称进行集数识别，适用于按集数建文件夹的资源。",
                },
            ],
            default_config={
                "start_episode": default_start,
                "end_episode": default_end,
                "latest_count": default_latest,
                "blocked_episodes": "",
                "skip_downloaded": default_skip,
                "downloaded_episodes": "",
                "blocked_item_ids": [],
                "only_video_files": True,
                "include_folders": True,
            },
            output_types=["drive.download.filtered"],
        )

    def validate_config(self, config: dict[str, Any]) -> OperationResult:
        errors: list[str] = []

        filter_mode = str(config.get("filter_mode", "range") or "range")

        if filter_mode in ("range", "range_latest"):
            start = int(config.get("start_episode", 1) or 1)
            end = int(config.get("end_episode", 0) or 0)

            if start < 1:
                errors.append("起始集数必须大于等于 1。")

            if end < 0:
                errors.append("结束集数不能为负数。")

            if end > 0 and start > end:
                errors.append("起始集数不能大于结束集数。")

        latest_count = int(config.get("latest_count", 0) or 0)
        if latest_count < 0:
            errors.append("最新N集不能为负数。")

        for field_name in ("blocked_episodes", "downloaded_episodes"):
            raw = str(config.get(field_name, "") or "").strip()
            if raw:
                try:
                    self._parse_episode_list(raw)
                except ValueError as exc:
                    label = "屏蔽集数" if field_name == "blocked_episodes" else "已下载集数"
                    errors.append(f"{label}格式错误：{exc}")

        if errors:
            return OperationResult(
                success=False,
                message="配置校验失败。",
                errors=errors,
            )

        return OperationResult(success=True, message="配置校验通过。")

    def create_from_resource(self, resource: dict[str, Any]) -> dict[str, Any]:
        title = str(resource.get("title") or "未命名资源").strip()
        rule = self._match_rule(title)

        latest_count = int(rule.get("latest_count", self._runtime_config.get("default_latest_count", 0)) or 0)

        filter_mode = "range"
        if latest_count > 0:
            start = int(rule.get("start_episode", 0) or 0)
            end = int(rule.get("end_episode", 0) or 0)
            if start == 0 and end == 0:
                filter_mode = "latest"
            else:
                filter_mode = "range_latest"

        return {
            "title": f"过滤下载：{title}",
            "input_type": "resource",
            "input_payload": {"resource": resource},
            "config": {
                "resource_title": title,
                "filter_mode": filter_mode,
                "start_episode": int(rule.get("start_episode", self._runtime_config.get("default_start_episode", 1)) or 1),
                "end_episode": int(rule.get("end_episode", self._runtime_config.get("default_end_episode", 0)) or 0),
                "latest_count": latest_count,
                "blocked_episodes": str(rule.get("blocked_episodes", self._runtime_config.get("default_blocked_episodes", "")) or ""),
                "skip_downloaded": bool(rule.get("skip_downloaded", self._runtime_config.get("default_skip_downloaded", True))),
                "downloaded_episodes": str(rule.get("downloaded_episodes", "") or ""),
                "blocked_item_ids": [],
                "only_video_files": True,
                "include_folders": True,
            },
        }

    def dry_run(self, config: dict[str, Any]) -> OperationResult:
        items = list(config.get("drive_items", []) or [])
        if not items:
            return OperationResult(
                success=False,
                message="未提供网盘内容列表，无法进行过滤预览。",
            )

        result = self._filter_items(items, config)
        return OperationResult(
            success=True,
            message=f"过滤预览：共 {len(items)} 项，保留 {len(result['kept'])} 项，过滤 {len(result['filtered'])} 项。",
            data=result,
        )

    def execute(self, execution_context: dict[str, Any]) -> TaskExecutionResult:
        config = dict(execution_context.get("config") or {})
        items = list(config.get("drive_items", []) or [])
        logs: list[str] = []

        if not items:
            return TaskExecutionResult(
                success=False,
                status="failed",
                summary="未提供网盘内容列表。",
                errors=["drive_items 参数为空。"],
                logs=logs,
            )

        resource_title = str(config.get("resource_title", "") or "").strip()
        if resource_title:
            rule = self._match_rule(resource_title)
            if rule.get("keyword"):
                logs.append(f"匹配到规则「{rule.get('keyword')}」")
                config["start_episode"] = int(rule.get("start_episode", config.get("start_episode", 1)) or 1)
                config["end_episode"] = int(rule.get("end_episode", config.get("end_episode", 0)) or 0)
                config["latest_count"] = int(rule.get("latest_count", config.get("latest_count", 0)) or 0)
                config["blocked_episodes"] = str(rule.get("blocked_episodes", config.get("blocked_episodes", "")) or "")
                config["skip_downloaded"] = bool(rule.get("skip_downloaded", config.get("skip_downloaded", True)))
                if rule.get("downloaded_episodes"):
                    config["downloaded_episodes"] = str(rule.get("downloaded_episodes", ""))

        filter_mode = str(config.get("filter_mode", "range") or "range")
        logs.append(f"过滤模式：{self._filter_mode_label(filter_mode)}")
        logs.append(f"开始过滤网盘内容，原始条目数：{len(items)}")

        result = self._filter_items(items, config)

        logs.append(f"集数识别完成，识别到 {len(result['recognized'])} 项带集数的内容")
        logs.append(f"过滤规则：起始集 {result['config'].get('start_episode')}，"
                    f"结束集 {result['config'].get('end_episode') or '不限'}，"
                    f"最新N集 {result['config'].get('latest_count') or '不启用'}，"
                    f"屏蔽集数 {result['config'].get('blocked_episodes') or '无'}，"
                    f"跳过已下载 {'启用' if result['config'].get('skip_downloaded') else '关闭'}")
        logs.append(f"保留 {len(result['kept'])} 项，过滤 {len(result['filtered'])} 项")

        for item in result["filtered"]:
            reason = item.get("_filter_reason", "未知原因")
            episode = item.get("_episode", "未识别")
            logs.append(f"  过滤：{item.get('name', '')}（集数: {episode}）- {reason}")

        return TaskExecutionResult(
            success=True,
            status="success",
            summary=f"过滤完成：保留 {len(result['kept'])} 项，过滤 {len(result['filtered'])} 项",
            artifacts=[
                {
                    "type": "drive.items",
                    "value": result["kept"],
                },
                {
                    "type": "drive.items.filtered",
                    "value": result["filtered"],
                },
            ],
            logs=logs,
        )

    def _filter_items(
        self, items: list[dict[str, Any]], config: dict[str, Any]
    ) -> dict[str, Any]:
        filter_mode = str(config.get("filter_mode", "range") or "range")
        start_episode = int(config.get("start_episode", 1) or 1)
        end_episode = int(config.get("end_episode", 0) or 0)
        latest_count = int(config.get("latest_count", 0) or 0)
        blocked_episodes = self._parse_episode_list(
            str(config.get("blocked_episodes", "") or "")
        )
        skip_downloaded = bool(config.get("skip_downloaded", True))
        downloaded_episodes = self._parse_episode_list(
            str(config.get("downloaded_episodes", "") or "")
        ) if skip_downloaded else set()
        blocked_item_ids = set(
            str(i) for i in (config.get("blocked_item_ids", []) or [])
        )
        only_video = bool(config.get("only_video_files", True))
        include_folders = bool(config.get("include_folders", True))
        patterns = self._get_episode_patterns()

        all_recognized_episodes: list[int] = []
        parsed_items: list[dict[str, Any]] = []

        for item in items:
            item = dict(item)
            name = str(item.get("name", "") or "")
            item_type = str(item.get("type", "file") or "file")
            episode = self._extract_episode(name, patterns)
            item["_episode"] = episode
            if episode is not None:
                all_recognized_episodes.append(episode)
            parsed_items.append(item)

        latest_threshold = None
        if filter_mode in ("latest", "range_latest") and latest_count > 0 and all_recognized_episodes:
            sorted_eps = sorted(set(all_recognized_episodes), reverse=True)
            if len(sorted_eps) >= latest_count:
                latest_threshold = sorted_eps[latest_count - 1]
            else:
                latest_threshold = sorted_eps[-1] if sorted_eps else None

        kept: list[dict[str, Any]] = []
        filtered: list[dict[str, Any]] = []
        recognized: list[dict[str, Any]] = []

        for item in parsed_items:
            name = str(item.get("name", "") or "")
            item_type = str(item.get("type", "file") or "file")
            item_id = str(item.get("id", "") or "")
            episode = item.get("_episode")

            if item_type == "folder" and not include_folders:
                item["_filter_reason"] = "文件夹过滤已关闭"
                filtered.append(item)
                continue

            if item_type == "file" and only_video:
                ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
                if ext not in VIDEO_EXTENSIONS:
                    item["_filter_reason"] = "非视频文件"
                    filtered.append(item)
                    continue

            if item_id in blocked_item_ids:
                item["_filter_reason"] = "手动屏蔽该条目"
                filtered.append(item)
                continue

            if episode is not None:
                recognized.append(item)

                if episode in blocked_episodes:
                    item["_filter_reason"] = f"集数 {episode} 在屏蔽列表中"
                    filtered.append(item)
                    continue

                if skip_downloaded and episode in downloaded_episodes:
                    item["_filter_reason"] = f"集数 {episode} 已下载，跳过"
                    filtered.append(item)
                    continue

                if filter_mode in ("range", "range_latest"):
                    if episode < start_episode:
                        item["_filter_reason"] = f"集数 {episode} 早于起始集 {start_episode}"
                        filtered.append(item)
                        continue

                    if end_episode > 0 and episode > end_episode:
                        item["_filter_reason"] = f"集数 {episode} 晚于结束集 {end_episode}"
                        filtered.append(item)
                        continue

                if filter_mode in ("latest", "range_latest") and latest_count > 0 and latest_threshold is not None:
                    if episode < latest_threshold:
                        item["_filter_reason"] = f"集数 {episode} 不在最新 {latest_count} 集范围内（最新阈值: {latest_threshold}）"
                        filtered.append(item)
                        continue

            kept.append(item)

        return {
            "kept": kept,
            "filtered": filtered,
            "recognized": recognized,
            "config": {
                "filter_mode": filter_mode,
                "start_episode": start_episode,
                "end_episode": end_episode,
                "latest_count": latest_count,
                "latest_threshold": latest_threshold,
                "blocked_episodes": str(config.get("blocked_episodes", "") or ""),
                "skip_downloaded": skip_downloaded,
                "downloaded_episodes": str(config.get("downloaded_episodes", "") or ""),
                "blocked_item_ids": list(blocked_item_ids),
            },
        }

    @staticmethod
    def _extract_episode(name: str, patterns: list[re.Pattern[str]]) -> int | None:
        if not name:
            return None

        candidates: list[int] = []
        for pattern in patterns:
            for match in pattern.finditer(name):
                for group in match.groups():
                    if group is not None and group.isdigit():
                        candidates.append(int(group))

        if not candidates:
            return None

        if len(candidates) == 1:
            return candidates[0]

        return min(candidates)

    @staticmethod
    def _parse_episode_list(raw: str) -> set[int]:
        result: set[int] = set()
        raw = raw.strip()
        if not raw:
            return result

        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue

            if "-" in part:
                range_parts = part.split("-", 1)
                try:
                    range_start = int(range_parts[0].strip())
                    range_end = int(range_parts[1].strip())
                    if range_start > range_end:
                        range_start, range_end = range_end, range_start
                    for ep in range(range_start, range_end + 1):
                        result.add(ep)
                except ValueError:
                    raise ValueError(f"无法解析范围：{part}")
            else:
                try:
                    result.add(int(part))
                except ValueError:
                    raise ValueError(f"无法解析集数：{part}")

        return result

    @staticmethod
    def _filter_mode_label(mode: str) -> str:
        labels = {
            "range": "按集数范围",
            "latest": "只下载最新N集",
            "range_latest": "范围+最新N集（交集）",
        }
        return labels.get(mode, mode)

    def _get_episode_patterns(self) -> list[re.Pattern[str]]:
        custom_raw = str(self._runtime_config.get("episode_patterns", "") or "").strip()
        patterns_strs: list[str] = []

        if custom_raw:
            for p in custom_raw.split(","):
                p = p.strip()
                if p:
                    patterns_strs.append(p)
        else:
            patterns_strs = list(DEFAULT_EPISODE_PATTERNS)

        compiled: list[re.Pattern[str]] = []
        for p in patterns_strs:
            try:
                compiled.append(re.compile(p, re.IGNORECASE))
            except re.error:
                continue

        return compiled

    def _parse_all_rules(self) -> list[dict[str, Any]]:
        rules_raw = str(self._runtime_config.get("filter_rules", "") or "").strip()
        rules: list[dict[str, Any]] = []

        if not rules_raw:
            return rules

        for line in rules_raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split("|")
            if len(parts) < 2:
                continue

            keyword = parts[0].strip()
            if not keyword:
                continue

            rule = {
                "keyword": keyword,
                "start_episode": int(parts[1].strip()) if len(parts) > 1 and parts[1].strip() else 0,
                "end_episode": int(parts[2].strip()) if len(parts) > 2 and parts[2].strip() else 0,
                "latest_count": int(parts[3].strip()) if len(parts) > 3 and parts[3].strip() else 0,
                "blocked_episodes": parts[4].strip() if len(parts) > 4 else "",
                "skip_downloaded": True,
                "downloaded_episodes": "",
            }

            if len(parts) > 5:
                val = parts[5].strip().lower()
                if val in ("false", "0", "no", "off"):
                    rule["skip_downloaded"] = False

            if len(parts) > 6:
                rule["downloaded_episodes"] = parts[6].strip()

            rules.append(rule)

        return rules

    def _match_rule(self, task_name: str) -> dict[str, Any]:
        rules = self._parse_all_rules()
        task_name_lower = task_name.lower()

        for rule in rules:
            keyword = str(rule.get("keyword", "") or "").lower()
            if keyword and keyword in task_name_lower:
                return rule

        return {
            "keyword": "",
            "start_episode": self._runtime_config.get("default_start_episode", 1),
            "end_episode": self._runtime_config.get("default_end_episode", 0),
            "latest_count": self._runtime_config.get("default_latest_count", 0),
            "blocked_episodes": self._runtime_config.get("default_blocked_episodes", ""),
            "skip_downloaded": self._runtime_config.get("default_skip_downloaded", True),
            "downloaded_episodes": "",
        }


plugin = DriveDownloadFilterPlugin()
