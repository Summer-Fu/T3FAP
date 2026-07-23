from __future__ import annotations

import re
from typing import Any

from core.sdk import (
    BasePlugin,
    OperationResult,
    TaskExecutionResult,
    TaskTemplate,
    TaskTypeProvider,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_VIDEO_EXTENSIONS = [
    ".mp4", ".mkv", ".avi", ".rmvb", ".ts",
    ".mov", ".wmv", ".flv", ".m4v", ".iso",
]

# 内置集数识别正则，按优先级从高到低尝试匹配
# 每条正则的第一个捕获组即为集数
BUILTIN_EPISODE_PATTERNS: list[str] = [
    # 第01集 / 第1集 / 第一集（中文数字转阿拉伯）
    r"第\s*([0-9]+)\s*[集话話期]",
    # EP01 / E01 / ep.01 / e.01 (不区分大小写)
    r"[Ee][Pp]?\.?\s*0*([0-9]{1,4})(?!\d)",
    # Episode 01
    r"[Ee]pisode\s*0*([0-9]{1,4})(?!\d)",
    # 01话 / 01話 / 01期
    r"0*([0-9]{1,4})\s*[话話期]",
    # [01] 或 【01】
    r"[\[【]\s*0*([0-9]{1,4})\s*[\]】]",
    # - 01 - 或 _01_ 格式
    r"[-_]\s*0*([0-9]{1,4})\s*[-_]",
    # 纯数字结尾：xxx.01.mp4 / xxx 01.mp4 / xxx01.mp4
    # 只在文件名末尾的数字，避免匹配年份等
    r"0*([0-9]{1,3})\s*(?:\.[A-Za-z0-9]+)*$",
]

# 中文数字映射
CHINESE_NUM_MAP = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# 过滤模式枚举
FILTER_MODES = {
    "all": "全部下载",
    "start_from": "从指定集数开始下载",
    "latest_n": "只下载最新N集",
    "exclude": "屏蔽指定集数",
    "include_only": "仅下载指定集数",
    "exclude_items": "屏蔽指定文件/文件夹",
}


# ---------------------------------------------------------------------------
# 集数解析工具
# ---------------------------------------------------------------------------

class EpisodeParser:
    """从文件名中解析集数。"""

    def __init__(self, custom_regex: str = "") -> None:
        self._compiled_patterns: list[re.Pattern[str]] = []
        if custom_regex.strip():
            try:
                self._compiled_patterns.append(re.compile(custom_regex))
            except re.error:
                pass  # 无效正则静默跳过，后续用内置
        for pattern in BUILTIN_EPISODE_PATTERNS:
            try:
                self._compiled_patterns.append(re.compile(pattern))
            except re.error:
                continue

    @staticmethod
    def _chinese_to_int(text: str) -> int | None:
        """尝试将中文数字转为整数，失败返回 None。"""
        if not text:
            return None
        # 纯阿拉伯数字直接转
        if text.isdigit():
            return int(text)
        # 简单中文数字转换（支持一到九十九）
        if len(text) == 1 and text in CHINESE_NUM_MAP:
            return CHINESE_NUM_MAP[text]
        if "十" in text:
            parts = text.split("十")
            tens = CHINESE_NUM_MAP.get(parts[0], 1) if parts[0] else 1
            ones = CHINESE_NUM_MAP.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
            return tens * 10 + ones
        return None

    def parse(self, filename: str) -> int | None:
        """从文件名解析集数，返回整数集数或 None。"""
        if not filename:
            return None

        # 去掉扩展名再匹配，避免扩展名中的数字干扰
        name_base = filename
        for ext in DEFAULT_VIDEO_EXTENSIONS:
            if name_base.lower().endswith(ext):
                name_base = name_base[: -len(ext)]
                break

        for pattern in self._compiled_patterns:
            match = pattern.search(name_base)
            if not match:
                continue
            raw = match.group(1)
            num = self._chinese_to_int(raw)
            if num is not None and num > 0:
                return num

        return None


# ---------------------------------------------------------------------------
# 文件项工具
# ---------------------------------------------------------------------------

def is_video_file(name: str, extensions: list[str]) -> bool:
    """判断文件名是否为视频文件。"""
    lower = name.lower()
    return any(lower.endswith(ext) for ext in extensions)


def parse_episode_list(text: str) -> list[int]:
    """解析逗号分隔的集数列表，支持范围如 1-5,8,10-12。"""
    result: list[int] = []
    if not text or not text.strip():
        return result
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            range_parts = part.split("-")
            if len(range_parts) == 2:
                try:
                    start = int(range_parts[0].strip())
                    end = int(range_parts[1].strip())
                    if start <= end:
                        result.extend(range(start, end + 1))
                    else:
                        result.extend(range(end, start + 1))
                except ValueError:
                    continue
        else:
            try:
                result.append(int(part))
            except ValueError:
                continue
    return result


# ---------------------------------------------------------------------------
# 过滤结果数据结构
# ---------------------------------------------------------------------------

class FilterResult:
    """集数过滤结果。"""

    def __init__(self) -> None:
        self.selected: list[dict[str, Any]] = []      # 选中的下载项
        self.skipped: list[dict[str, Any]] = []        # 被过滤掉的项
        self.no_episode: list[dict[str, Any]] = []     # 无法识别集数的视频文件
        self.non_video: list[dict[str, Any]] = []      # 非视频文件
        self.folders: list[dict[str, Any]] = []        # 文件夹
        self.total_files: int = 0
        self.total_episodes: int = 0
        self.max_episode: int | None = None
        self.min_episode: int | None = None
        self.logs: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": self.selected,
            "skipped": self.skipped,
            "no_episode": self.no_episode,
            "non_video": self.non_video,
            "folders": self.folders,
            "total_files": self.total_files,
            "total_episodes": self.total_episodes,
            "max_episode": self.max_episode,
            "min_episode": self.min_episode,
            "selected_count": len(self.selected),
            "skipped_count": len(self.skipped),
            "logs": self.logs,
        }


# ---------------------------------------------------------------------------
# 插件主体
# ---------------------------------------------------------------------------

class EpisodeFilterPlugin(BasePlugin, TaskTypeProvider):
    plugin_id = "task.episode_filter"
    plugin_name = "网盘下载集数过滤器"
    plugin_version = "1.0.0"

    def __init__(self) -> None:
        self._runtime_config: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 生命周期 & 配置
    # ------------------------------------------------------------------

    def set_runtime_config(self, config: dict[str, Any]) -> None:
        self._runtime_config = dict(config or {})

    def validate_runtime_config(self, config: dict[str, Any]) -> OperationResult:
        normalized = dict(config or {})
        errors: list[str] = []
        mode = str(normalized.get("default_filter_mode") or "all").strip()
        if mode not in FILTER_MODES:
            errors.append(f"默认过滤模式无效：{mode}，可选值：{', '.join(FILTER_MODES.keys())}")
        if errors:
            return OperationResult(success=False, message="插件配置校验失败。", errors=errors)
        return OperationResult(success=True, message="插件配置校验通过。", data=normalized)

    def health(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "message": "网盘下载集数过滤器运行正常。",
            "details": {
                "default_filter_mode": self._runtime_config.get("default_filter_mode", "all"),
                "enabled": self._runtime_config.get("enabled", True),
            },
        }

    # ------------------------------------------------------------------
    # TaskTypeProvider 协议
    # ------------------------------------------------------------------

    def get_template(self) -> TaskTemplate:
        return TaskTemplate(
            type_key="episode_filter",
            template_key="episode_filter",
            plugin_id=self.plugin_id,
            title="网盘下载集数过滤",
            allow_manual_creation=True,
            supported_inputs=["manual", "resource"],
            form_schema=self._build_form_schema(),
            default_config={
                "filter_mode": self._runtime_config.get("default_filter_mode", "all"),
                "start_episode": 1,
                "latest_n": 5,
                "excluded_episodes": "",
                "included_episodes": "",
                "excluded_item_ids": "",
                "episode_regex": "",
                "include_non_video": False,
                "auto_detect_folders": True,
                "share_url": "",
                "drive_type": "",
                "share_password": "",
            },
            output_types=["episode_filter.result"],
        )

    def validate_config(self, config: dict[str, Any]) -> OperationResult:
        errors: list[str] = []

        mode = str(config.get("filter_mode") or "all").strip()
        if mode not in FILTER_MODES:
            errors.append(f"过滤模式无效：{mode}")

        if mode == "start_from":
            start = config.get("start_episode")
            if start is None or int(start) < 1:
                errors.append("起始集数必须 >= 1")

        if mode == "latest_n":
            n = config.get("latest_n")
            if n is None or int(n) < 1:
                errors.append("最新N集的 N 必须 >= 1")

        if mode == "exclude":
            excluded = str(config.get("excluded_episodes") or "").strip()
            if not excluded:
                errors.append("屏蔽模式需要填写屏蔽集数列表")

        if mode == "include_only":
            included = str(config.get("included_episodes") or "").strip()
            if not included:
                errors.append("仅下载模式需要填写指定集数列表")

        # 校验正则
        custom_regex = str(config.get("episode_regex") or "").strip()
        if custom_regex:
            try:
                re.compile(custom_regex)
            except re.error as e:
                errors.append(f"自定义集数正则无效：{e}")

        if errors:
            return OperationResult(success=False, message="任务配置校验失败。", errors=errors)
        return OperationResult(success=True, message="任务配置校验通过。")

    def create_from_resource(self, resource: dict[str, Any]) -> dict[str, Any]:
        """从资源信息创建任务草稿。

        平台调用此方法将资源（ResourceItem）转为任务配置草稿。
        关键：从 resource 中提取分享链接（links.share）和网盘类型（drive_type），
        这些信息会在 execute() 时被平台用于调用 DriveProvider 获取文件列表。

        根据官方 CatalogProvider/SearchProvider 的 ResourceItem 结构：
        - resource.links.share 包含 ShareLink 列表（url, drive_type, password）
        - resource.media_type 标识资源类型（movie/tv/anime 等）
        - resource.title 是资源标题

        平台会在执行任务前：
        1. 用 share_url + drive_type 找到对应 DriveProvider
        2. 调用 DriveProvider.parse_share() 解析分享
        3. 调用 DriveProvider.browse_share() 获取文件列表
        4. 将文件列表传入 execution_context.drive_files
        """
        title = str(resource.get("title") or "网盘下载任务").strip()
        media_type = str(resource.get("media_type") or "tv").strip()
        default_mode = self._runtime_config.get("default_filter_mode", "all")

        # 电影默认全部下载；电视剧/动漫默认按配置
        if media_type == "movie":
            default_mode = "all"

        # 从资源中提取分享链接信息（这是与平台 DriveProvider 的关键数据桥接）
        share_url = ""
        drive_type = ""
        share_password = ""
        links = resource.get("links") or {}
        if isinstance(links, dict):
            share_links = links.get("share") or []
            if isinstance(share_links, list) and share_links:
                # 取第一个分享链接（通常资源只有一个主要分享）
                first_share = share_links[0]
                if isinstance(first_share, dict):
                    share_url = str(first_share.get("url") or "").strip()
                    drive_type = str(first_share.get("drive_type") or "").strip()
                    share_password = str(first_share.get("password") or "").strip()

        # 提取 account_ref（如果资源携带了账号信息）
        account_ref = resource.get("account_ref") or {}
        if not isinstance(account_ref, dict):
            account_ref = {}

        return {
            "title": f"集数过滤：{title}",
            "input_type": "resource",
            "input_payload": {
                "resource": resource,
                "share_url": share_url,
                "drive_type": drive_type,
                "share_password": share_password,
                "account_ref": account_ref,
            },
            "config": {
                "filter_mode": default_mode,
                "start_episode": 1,
                "latest_n": 5,
                "excluded_episodes": "",
                "included_episodes": "",
                "excluded_item_ids": "",
                "episode_regex": "",
                "include_non_video": False,
                "auto_detect_folders": True,
                "media_type": media_type,
                "resource_title": title,
                "share_url": share_url,
                "drive_type": drive_type,
                "share_password": share_password,
            },
        }

    def dry_run(self, config: dict[str, Any]) -> OperationResult:
        """预览过滤结果，不执行真实下载。

        dry_run 接收的 config 是任务配置（不含平台注入数据），
        所以文件列表只能从 config.files 手动获取。
        如果没有 files，但包含 share_url/drive_type，
        会提示需要平台先解析网盘。
        """
        try:
            # dry_run 传入的是 config dict，需要从 config 中提取文件
            files = config.get("files")
            if isinstance(files, list) and files:
                result = self._filter_files(files, config)
                return OperationResult(
                    success=True,
                    message=(
                        f"预览完成：共 {result.total_files} 个文件，"
                        f"识别到 {result.total_episodes} 集，"
                        f"选中 {len(result.selected)} 个，"
                        f"屏蔽 {len(result.skipped)} 个。"
                    ),
                    data=result.to_dict(),
                )

            share_url = str(config.get("share_url") or "").strip()
            drive_type = str(config.get("drive_type") or "").strip()
            if share_url and drive_type:
                return OperationResult(
                    success=False,
                    message=(
                        f"预览需要网盘文件数据。平台需先调用 "
                        f"DriveProvider({drive_type}) 解析分享链接，"
                        f"再将文件列表传入 config.files 或 execution_context.drive_files。"
                    ),
                )

            return OperationResult(
                success=False,
                message="没有可供过滤的文件列表。请提供 config.files 或 share_url + drive_type。",
            )
        except Exception as e:
            return OperationResult(success=False, message=f"预览失败：{e}")

    def execute(self, execution_context: dict[str, Any]) -> TaskExecutionResult:
        """执行集数过滤，返回过滤后的下载列表。

        平台调用此方法执行任务。execution_context 是 dict[str, Any]，
        包含 config 和平台注入的网盘文件数据。

        关键数据入口（平台在执行前注入）：
        1. execution_context["drive_files"] - DriveProvider.browse_share() 返回的文件列表
           这是平台编排层调用 DriveProvider 后注入到 execution_context 的数据，
           每个文件项包含 {id, name, type, size, parent_id} 等字段。
        2. execution_context["config"]["files"] - 手动创建任务时用户提供的文件列表
        3. execution_context["input_payload"]["resource"]["files"] - 从资源携带的文件列表

        平台编排流程：
        - 当 input_type == "resource" 且 config 包含 share_url/drive_type 时，
          平台会：
          a) 找到 drive_type 对应的 DriveProvider
          b) 调用 DriveProvider.parse_share({share_url, password})
          c) 调用 DriveProvider.browse_share(account_ref, share_ref)
          d) 将返回的文件列表注入 execution_context["drive_files"]
          e) 调用本插件的 execute()
        """
        config = execution_context.get("config") or {}
        logs: list[str] = []
        logs.append(f"[{self.plugin_id}] 开始执行集数过滤任务")

        # 提取文件列表：优先从平台注入的 drive_files，其次从 config 手动提供
        files = self._extract_files_from_context(execution_context)

        try:
            if not files:
                # 没有文件数据时返回等待状态，告知平台需要先解析网盘
                share_url = str(config.get("share_url") or "").strip()
                drive_type = str(config.get("drive_type") or "").strip()
                if share_url and drive_type:
                    logs.append(f"需要平台先解析网盘：share_url={share_url}, drive_type={drive_type}")
                    return TaskExecutionResult(
                        success=False,
                        status="pending",
                        summary=(
                            f"等待网盘文件数据：平台需先调用 "
                            f"DriveProvider({drive_type}) 解析分享链接 {share_url}，"
                            f"再将文件列表注入 execution_context.drive_files"
                        ),
                        artifacts=[
                            {
                                "type": "drive_ref",
                                "value": {
                                    "share_url": share_url,
                                    "drive_type": drive_type,
                                    "share_password": str(config.get("share_password") or ""),
                                    "account_ref": execution_context.get("account_ref") or config.get("account_ref") or {},
                                },
                                "description": "需要平台解析的网盘分享引用",
                            },
                        ],
                        logs=logs,
                    )

                logs.append("警告：没有可供过滤的文件列表，也没有分享链接信息")
                return TaskExecutionResult(
                    success=True,
                    status="completed",
                    summary="没有文件需要过滤（文件列表为空且无分享链接）",
                    artifacts=[],
                    logs=logs,
                )

            logs.append(f"接收到 {len(files)} 个文件/文件夹")

            result = self._filter_files(files, config)
            logs.extend(result.logs)

            mode = str(config.get("filter_mode") or "all")
            mode_label = FILTER_MODES.get(mode, mode)
            logs.append(f"过滤模式：{mode_label}")

            if result.max_episode is not None:
                logs.append(f"集数范围：第 {result.min_episode} 集 ~ 第 {result.max_episode} 集")

            logs.append(f"选中下载：{len(result.selected)} 个文件")
            logs.append(f"屏蔽跳过：{len(result.skipped)} 个文件")
            if result.no_episode:
                logs.append(f"无法识别集数：{len(result.no_episode)} 个视频文件")

            summary = (
                f"集数过滤完成（{mode_label}）："
                f"共 {result.total_files} 个文件，"
                f"识别 {result.total_episodes} 集，"
                f"选中 {len(result.selected)} 个，"
                f"屏蔽 {len(result.skipped)} 个"
            )

            # 构造下载清单 artifact
            download_list = []
            for item in result.selected:
                download_list.append({
                    "id": item.get("id", ""),
                    "name": item.get("name", ""),
                    "episode": item.get("episode"),
                    "size": item.get("size", 0),
                    "parent_id": item.get("parent_id", ""),
                    "type": "file",
                })

            return TaskExecutionResult(
                success=True,
                status="success",
                summary=summary,
                artifacts=[
                    {
                        "type": "download_list",
                        "value": download_list,
                        "description": "过滤后的下载文件清单",
                    },
                    {
                        "type": "filter_report",
                        "value": result.to_dict(),
                        "description": "完整过滤报告（含选中/屏蔽/未识别）",
                    },
                ],
                logs=logs,
            )

        except Exception as e:
            logs.append(f"执行失败：{e}")
            return TaskExecutionResult(
                success=False,
                status="failed",
                summary=f"集数过滤任务执行失败：{e}",
                artifacts=[],
                logs=logs,
            )

    # ------------------------------------------------------------------
    # 核心过滤逻辑
    # ------------------------------------------------------------------

    def _filter_files(
        self,
        files: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> FilterResult:
        """对文件列表执行集数过滤，返回 FilterResult。"""
        result = FilterResult()
        result.total_files = len(files)

        # 获取配置
        mode = str(config.get("filter_mode") or "all")
        start_episode = int(config.get("start_episode") or 1)
        latest_n = int(config.get("latest_n") or 5)
        excluded_episodes = parse_episode_list(
            str(config.get("excluded_episodes") or "")
        )
        included_episodes = parse_episode_list(
            str(config.get("included_episodes") or "")
        )
        excluded_item_ids_raw = str(config.get("excluded_item_ids") or "").strip()
        excluded_item_ids = {
            s.strip() for s in excluded_item_ids_raw.split(",") if s.strip()
        }
        custom_regex = str(config.get("episode_regex") or self._runtime_config.get("custom_episode_regex") or "")
        include_non_video = bool(config.get("include_non_video", False))
        auto_detect_folders = bool(config.get("auto_detect_folders", True))

        # 获取视频扩展名配置
        video_ext_str = str(
            self._runtime_config.get("video_extensions") or ""
        ).strip()
        if video_ext_str:
            video_extensions = [e.strip().lower() for e in video_ext_str.split(",") if e.strip()]
        else:
            video_extensions = list(DEFAULT_VIDEO_EXTENSIONS)

        parser = EpisodeParser(custom_regex)

        # 第一步：分类所有文件
        video_items: list[dict[str, Any]] = []
        for item in files:
            item_id = str(item.get("id") or "")
            name = str(item.get("name") or "")
            item_type = str(item.get("type") or "file").lower()

            # 检查是否在屏蔽列表中
            if item_id and item_id in excluded_item_ids:
                item["_skip_reason"] = "手动屏蔽（文件/文件夹ID）"
                result.skipped.append(item)
                continue

            # 文件夹处理
            if item_type == "folder" or (auto_detect_folders and not name):
                result.folders.append(item)
                continue

            # 非视频文件
            if not is_video_file(name, video_extensions):
                if include_non_video:
                    result.selected.append(item)
                else:
                    result.non_video.append(item)
                continue

            # 视频文件：解析集数
            episode = parser.parse(name)
            if episode is not None:
                item["episode"] = episode
                video_items.append(item)
            else:
                item["episode"] = None
                result.no_episode.append(item)

        # 第二步：统计集数范围
        all_episodes = [item["episode"] for item in video_items if item.get("episode") is not None]
        if all_episodes:
            result.max_episode = max(all_episodes)
            result.min_episode = min(all_episodes)
            result.total_episodes = len(all_episodes)

        # 第三步：按模式过滤
        if mode == "all":
            result.selected.extend(video_items)
            result.logs.append("模式[全部下载]：选中所有视频文件")

        elif mode == "start_from":
            for item in video_items:
                ep = item["episode"]
                if ep >= start_episode:
                    result.selected.append(item)
                else:
                    item["_skip_reason"] = f"集数 {ep} < 起始集数 {start_episode}"
                    result.skipped.append(item)
            result.logs.append(f"模式[从第{start_episode}集开始]：选中 {len(result.selected)} 个，屏蔽 {len(result.skipped)} 个")

        elif mode == "latest_n":
            # 关键逻辑：先检测所有视频文件，找到最大集数，再选最新N集
            if all_episodes:
                max_ep = result.max_episode
                min_target = max_ep - latest_n + 1
                if min_target < 1:
                    min_target = 1
                for item in video_items:
                    ep = item["episode"]
                    if ep >= min_target:
                        result.selected.append(item)
                    else:
                        item["_skip_reason"] = f"集数 {ep} 不在最新 {latest_n} 集范围内（{min_target}~{max_ep}）"
                        result.skipped.append(item)
                result.logs.append(
                    f"模式[最新{latest_n}集]：最大集数={max_ep}，"
                    f"下载范围={min_target}~{max_ep}，"
                    f"选中 {len(result.selected)} 个"
                )
            else:
                result.logs.append("模式[最新N集]：未识别到任何集数，无文件被选中")

        elif mode == "exclude":
            excluded_set = set(excluded_episodes)
            for item in video_items:
                ep = item["episode"]
                if ep in excluded_set:
                    item["_skip_reason"] = f"集数 {ep} 在屏蔽列表中"
                    result.skipped.append(item)
                else:
                    result.selected.append(item)
            result.logs.append(
                f"模式[屏蔽指定集数]：屏蔽 {len(excluded_episodes)} 集，"
                f"选中 {len(result.selected)} 个，屏蔽 {len(result.skipped)} 个"
            )

        elif mode == "include_only":
            included_set = set(included_episodes)
            for item in video_items:
                ep = item["episode"]
                if ep in included_set:
                    result.selected.append(item)
                else:
                    item["_skip_reason"] = f"集数 {ep} 不在指定下载列表中"
                    result.skipped.append(item)
            result.logs.append(
                f"模式[仅下载指定集数]：指定 {len(included_episodes)} 集，"
                f"选中 {len(result.selected)} 个"
            )

        elif mode == "exclude_items":
            # 已经在第一步处理了 excluded_item_ids，这里把剩余视频全部选中
            result.selected.extend(video_items)
            result.logs.append(
                f"模式[屏蔽文件/文件夹]：屏蔽 {len(excluded_item_ids)} 个ID，"
                f"选中 {len(result.selected)} 个视频文件"
            )

        # 对选中的文件按集数排序
        result.selected.sort(key=lambda x: x.get("episode") or 0)

        return result

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _extract_files_from_context(self, execution_context: dict[str, Any]) -> list[dict[str, Any]]:
        """从 execution_context 中提取网盘文件列表。

        按优先级依次尝试以下数据入口：

        1. execution_context["drive_files"]
           - 平台编排层在执行前调用 DriveProvider.browse_share() 获取的文件列表
           - 这是最主要的数据入口，平台负责将网盘文件数据注入此处

        2. execution_context["config"]["files"]
           - 手动创建任务时用户在表单中直接提供的文件列表
           - 或者通过 API 直接传入的文件数据

        3. execution_context["input_payload"]["drive_files"]
           - 从资源动作触发时，平台可能在 input_payload 中携带文件列表

        4. execution_context["input_payload"]["resource"]["files"]
           - 资源对象自身携带的文件列表（较少见）

        每个文件项应包含的字段（对应 DriveProvider.list_files/browse_share 的返回）：
        - id: 文件/文件夹ID
        - name: 文件名（用于集数解析）
        - type: "file" 或 "folder"
        - size: 文件大小（可选）
        - parent_id: 父目录ID（可选）
        """
        # 入口 1: 平台注入的 drive_files（最优先）
        drive_files = execution_context.get("drive_files")
        if isinstance(drive_files, list) and drive_files:
            return drive_files

        config = execution_context.get("config") or {}

        # 入口 2: config.files（手动提供）
        files = config.get("files")
        if isinstance(files, list) and files:
            return files

        # 入口 3: input_payload.drive_files
        input_payload = execution_context.get("input_payload") or config.get("input_payload") or {}
        if isinstance(input_payload, dict):
            drive_files = input_payload.get("drive_files")
            if isinstance(drive_files, list) and drive_files:
                return drive_files

            files = input_payload.get("files")
            if isinstance(files, list) and files:
                return files

            # 入口 4: resource.files
            resource = input_payload.get("resource") or {}
            if isinstance(resource, dict):
                files = resource.get("files") or resource.get("drive_files")
                if isinstance(files, list) and files:
                    return files

        return []

    def _build_form_schema(self) -> list[dict[str, Any]]:
        """构建任务表单 schema。

        包含两部分字段：
        1. 网盘来源设置（share_url / drive_type / share_password / account_ref）
           - 手动创建任务时用户填入分享链接信息
           - 从资源创建时由 create_from_resource() 自动填充
        2. 过滤设置（过滤模式 + 各模式参数）
        3. 高级设置
        """
        return [
            # ---- 网盘来源设置 ----
            {
                "key": "share_url",
                "label": "分享链接",
                "type": "string",
                "required": False,
                "default": "",
                "placeholder": "如：https://pan.quark.cn/s/abc123",
                "description": "网盘分享链接URL。从资源创建任务时自动填充；手动创建时需要填入，平台会用此链接调用对应 DriveProvider 获取文件列表。",
                "group": "网盘来源",
            },
            {
                "key": "drive_type",
                "label": "网盘类型",
                "type": "select",
                "required": False,
                "default": "",
                "options": [
                    {"value": "", "label": "自动识别"},
                    {"value": "quark", "label": "夸克网盘"},
                    {"value": "aliyun", "label": "阿里云盘"},
                    {"value": "baidu", "label": "百度网盘"},
                    {"value": "cloud189", "label": "天翼云盘"},
                    {"value": "115", "label": "115网盘"},
                    {"value": "xunlei", "label": "迅雷云盘"},
                ],
                "description": "网盘类型，用于平台找到对应的 DriveProvider。留空则根据分享链接自动识别。",
                "group": "网盘来源",
            },
            {
                "key": "share_password",
                "label": "分享密码",
                "type": "string",
                "required": False,
                "default": "",
                "description": "网盘分享密码（如有）。平台会将此密码传给 DriveProvider.parse_share()。",
                "secret": True,
                "group": "网盘来源",
            },
            # ---- 过滤设置 ----
            {
                "key": "filter_mode",
                "label": "过滤模式",
                "type": "select",
                "required": True,
                "default": "all",
                "options": [
                    {"value": "all", "label": "全部下载"},
                    {"value": "start_from", "label": "从指定集数开始下载"},
                    {"value": "latest_n", "label": "只下载最新N集"},
                    {"value": "exclude", "label": "屏蔽指定集数"},
                    {"value": "include_only", "label": "仅下载指定集数"},
                    {"value": "exclude_items", "label": "屏蔽指定文件/文件夹"},
                ],
                "description": "选择集数过滤策略。",
                "group": "过滤设置",
            },
            {
                "key": "start_episode",
                "label": "起始集数",
                "type": "integer",
                "required": False,
                "default": 1,
                "min": 1,
                "description": "从第几集开始下载（过滤模式选『从指定集数开始下载』时生效）。",
                "group": "过滤设置",
                "visible_when": {"filter_mode": "start_from"},
            },
            {
                "key": "latest_n",
                "label": "最新N集",
                "type": "integer",
                "required": False,
                "default": 5,
                "min": 1,
                "description": "只下载最新的N集。会先扫描所有视频文件找到最大集数，再选择最大集数往前推N集（过滤模式选『只下载最新N集』时生效）。",
                "group": "过滤设置",
                "visible_when": {"filter_mode": "latest_n"},
            },
            {
                "key": "excluded_episodes",
                "label": "屏蔽集数列表",
                "type": "string",
                "required": False,
                "default": "",
                "placeholder": "如：1-3,5,8-10",
                "description": "要屏蔽的集数，支持范围写法，如 1-3,5,8-10（过滤模式选『屏蔽指定集数』时生效）。",
                "group": "过滤设置",
                "visible_when": {"filter_mode": "exclude"},
            },
            {
                "key": "included_episodes",
                "label": "指定下载集数",
                "type": "string",
                "required": False,
                "default": "",
                "placeholder": "如：1,3,5,7-9",
                "description": "仅下载这些集数，支持范围写法，如 1,3,5,7-9（过滤模式选『仅下载指定集数』时生效）。",
                "group": "过滤设置",
                "visible_when": {"filter_mode": "include_only"},
            },
            {
                "key": "excluded_item_ids",
                "label": "屏蔽文件/文件夹ID",
                "type": "string",
                "required": False,
                "default": "",
                "placeholder": "如：folder_001,file_123",
                "description": "要屏蔽的网盘文件或文件夹ID列表，多个用英文逗号分隔。可手动浏览网盘内容后标记不下载的文件或文件夹。",
                "group": "过滤设置",
                "visible_when": {"filter_mode": "exclude_items"},
            },
            {
                "key": "episode_regex",
                "label": "自定义集数正则",
                "type": "string",
                "required": False,
                "default": "",
                "placeholder": "留空使用内置匹配规则",
                "description": "自定义集数识别正则表达式。第一个捕获组将作为集数。留空则使用内置多模式匹配。",
                "group": "高级设置",
            },
            {
                "key": "include_non_video",
                "label": "包含非视频文件",
                "type": "boolean",
                "required": False,
                "default": False,
                "description": "是否将非视频文件（如字幕、封面图等）也加入下载列表。",
                "group": "高级设置",
            },
            {
                "key": "auto_detect_folders",
                "label": "自动识别文件夹",
                "type": "boolean",
                "required": False,
                "default": True,
                "description": "自动将 type 为 folder 的项目归类，不参与集数过滤。",
                "group": "高级设置",
            },
        ]


plugin = EpisodeFilterPlugin()
