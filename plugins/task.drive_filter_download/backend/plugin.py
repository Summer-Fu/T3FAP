from __future__ import annotations

import re
import time
from typing import Any

from core.sdk import (
    BasePlugin,
    OperationResult,
    TaskExecutionResult,
    TaskTemplate,
    TaskTypeProvider,
)


class DriveFilterDownloadTaskPlugin(BasePlugin, TaskTypeProvider):
    """云盘过滤下载任务插件

    支持从网盘分享链接或目录中筛选文件并下载：
    - 全部下载
    - 集数范围下载（如 1-15, 16, 18-20）
    - 最新N集下载
    """

    plugin_id = "task.drive_filter_download"
    plugin_name = "云盘过滤下载任务插件"
    plugin_version = "0.1.0"

    # 视频文件扩展名
    VIDEO_EXTENSIONS = {
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv",
        ".webm", ".m4v", ".ts", ".mpg", ".mpeg", ".rmvb",
    }

    # 序号提取正则（匹配文件名开头或结尾的数字）
    EPISODE_PATTERNS = [
        re.compile(r"(\d{1,4})"),  # 通用数字匹配
    ]

    def get_template(self) -> TaskTemplate:
        return TaskTemplate(
            type_key="drive_filter_download",
            template_key="drive_filter_download",
            plugin_id=self.plugin_id,
            title="云盘过滤下载",
            allow_manual_creation=True,
            supported_inputs=["manual", "resource", "share"],
            form_schema=[
                {
                    "key": "share_url",
                    "label": "网盘分享链接",
                    "type": "string",
                    "required": True,
                    "default": "",
                    "description": "需要下载的网盘分享链接",
                },
                {
                    "key": "share_password",
                    "label": "分享密码",
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "分享链接的提取码（如有）",
                },
                {
                    "key": "drive_account_id",
                    "label": "网盘账号",
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "用于解析和下载的网盘账号ID",
                },
                {
                    "key": "range_mode",
                    "label": "下载筛选模式",
                    "type": "select",
                    "required": True,
                    "default": "latest_n",
                    "options": [
                        {"value": "all", "label": "全部下载"},
                        {"value": "range", "label": "集数范围"},
                        {"value": "latest_n", "label": "最新N集"},
                    ],
                    "description": "选择文件筛选方式",
                },
                {
                    "key": "episode_range",
                    "label": "集数范围",
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "集数范围，如 1-15,16,18-20（仅range模式）",
                },
                {
                    "key": "latest_count",
                    "label": "最新集数",
                    "type": "number",
                    "required": False,
                    "default": 5,
                    "description": "最新N集的数量（仅latest_n模式）",
                },
                {
                    "key": "download_path",
                    "label": "下载路径",
                    "type": "string",
                    "required": False,
                    "default": "/downloads",
                    "description": "下载文件保存路径",
                },
                {
                    "key": "video_only",
                    "label": "仅视频文件",
                    "type": "boolean",
                    "required": False,
                    "default": True,
                    "description": "是否只下载视频文件",
                },
            ],
            default_config={
                "range_mode": "latest_n",
                "latest_count": 5,
                "download_path": "/downloads",
                "video_only": True,
            },
            output_types=["download.result"],
        )

    def validate_config(self, config: dict[str, Any]) -> OperationResult:
        """校验插件全局配置（config_schema 中的项）。

        注意：任务级配置（如 share_url、range_mode）在任务创建时校验，
        不在此全局配置校验中处理。
        """
        errors: list[str] = []

        default_range_mode = str(config.get("default_range_mode") or "latest_n").strip()
        if default_range_mode not in ("all", "range", "latest_n"):
            errors.append(f"无效的默认筛选模式: {default_range_mode}")

        if default_range_mode == "latest_n":
            try:
                default_latest_count = int(config.get("default_latest_count") or 5)
                if default_latest_count <= 0:
                    errors.append("默认最新集数必须大于0")
            except (ValueError, TypeError):
                errors.append("默认最新集数必须是有效数字")

        if errors:
            return OperationResult(
                success=False,
                message="插件配置校验失败。",
                errors=errors,
            )
        return OperationResult(success=True, message="配置校验通过。")

    def create_from_resource(self, resource: dict[str, Any]) -> dict[str, Any]:
        title = str(resource.get("title") or "云盘下载任务").strip()
        share_links = resource.get("links", {}).get("share", []) if isinstance(resource.get("links"), dict) else []

        share_url = ""
        share_password = ""
        if share_links and isinstance(share_links, list) and len(share_links) > 0:
            first_link = share_links[0] if isinstance(share_links[0], dict) else {}
            share_url = str(first_link.get("url") or first_link.get("share_url") or "").strip()
            share_password = str(first_link.get("password") or first_link.get("share_password") or "").strip()

        return {
            "title": f"下载：{title}",
            "input_type": "resource",
            "input_payload": {"resource": resource},
            "config": {
                "share_url": share_url,
                "share_password": share_password,
                "range_mode": "latest_n",
                "latest_count": 5,
            },
        }

    def dry_run(self, config: dict[str, Any]) -> OperationResult:
        try:
            result = self._prepare_download(config, dry_run=True)
            return OperationResult(
                success=True,
                message="dry run 执行成功",
                data={
                    "config": config,
                    "matched_files": result.get("matched_files", []),
                    "total_count": result.get("total_count", 0),
                    "download_count": result.get("download_count", 0),
                    "skip_count": result.get("skip_count", 0),
                },
            )
        except Exception as exc:
            return OperationResult(
                success=False,
                message=f"dry run 执行失败: {exc}",
                errors=[str(exc)],
            )

    def execute(self, execution_context: dict[str, Any]) -> TaskExecutionResult:
        config = dict(execution_context.get("config") or {})
        task_id = str(execution_context.get("task_id") or "").strip()
        start_time = time.time()
        logs: list[str] = []

        logs.append(f"[{self._format_time()}] 云盘过滤下载任务开始执行")
        logs.append(f"[{self._format_time()}] 任务ID: {task_id}")

        try:
            result = self._prepare_download(config, dry_run=False)

            matched_files = result.get("matched_files", [])
            total_count = result.get("total_count", 0)
            download_count = result.get("download_count", 0)
            skip_count = result.get("skip_count", 0)
            failed_files = result.get("failed_files", [])

            logs.append(f"[{self._format_time()}] 发现文件总数: {total_count}")
            logs.append(f"[{self._format_time()}] 筛选后待下载: {download_count}")
            logs.append(f"[{self._format_time()}] 跳过: {skip_count}")

            duration = int(time.time() - start_time)
            duration_str = self._format_duration(duration)

            file_list_compressed = self._compress_file_list(
                [f.get("name", "") for f in matched_files]
            )
            latest_episode = self._find_latest_episode(matched_files)
            latest_episode_time = self._find_latest_episode_time(matched_files)

            summary = (
                f"下载完成：成功 {download_count} 项，"
                f"跳过 {skip_count} 项，"
                f"失败 {len(failed_files)} 项，"
                f"耗时 {duration_str}"
            )

            logs.append(f"[{self._format_time()}] 任务完成: {summary}")

            success = len(failed_files) == 0 or download_count > 0
            status = "success" if success else "partial_success"

            artifacts = [
                {
                    "type": "download.summary",
                    "success_count": download_count,
                    "skip_count": skip_count,
                    "failed_count": len(failed_files),
                    "total_count": total_count,
                    "duration": duration_str,
                    "duration_seconds": duration,
                },
                {
                    "type": "download.file_list",
                    "files": [f.get("name", "") for f in matched_files],
                    "file_list_compressed": file_list_compressed,
                },
                {
                    "type": "download.latest",
                    "episode": latest_episode,
                    "update_time": latest_episode_time,
                },
                {
                    "type": "download.target",
                    "path": str(config.get("download_path") or "/downloads"),
                },
                {
                    "type": "download.failed",
                    "items": failed_files,
                },
                {
                    "type": "task.duration",
                    "value": duration_str,
                    "seconds": duration,
                },
            ]

            return TaskExecutionResult(
                success=success,
                status=status,
                summary=summary,
                artifacts=artifacts,
                logs=logs,
            )

        except Exception as exc:
            duration = int(time.time() - start_time)
            duration_str = self._format_duration(duration)
            error_msg = str(exc)

            logs.append(f"[{self._format_time()}] 任务执行失败: {error_msg}")

            return TaskExecutionResult(
                success=False,
                status="failed",
                summary=f"下载任务失败: {error_msg}",
                error_message=error_msg,
                artifacts=[
                    {
                        "type": "task.duration",
                        "value": duration_str,
                        "seconds": duration,
                    }
                ],
                logs=logs,
            )

    # ============== 内部辅助方法 ==============

    def _prepare_download(self, config: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        """准备下载：解析分享、筛选文件、返回下载计划。

        注意：真实的下载执行需要调用对应网盘插件的下载接口。
        本插件负责筛选逻辑和编排，实际下载委托给网盘插件。
        """
        share_url = str(config.get("share_url") or "").strip()
        range_mode = str(config.get("range_mode") or "all").strip()
        video_only = bool(config.get("video_only", True))
        download_path = str(config.get("download_path") or "/downloads").strip()

        # 1. 解析分享链接获取文件列表
        # 真实场景下需要调用 drive 插件的 parse_share + browse_share
        all_files = self._fetch_share_files(config)

        # 2. 过滤视频文件
        if video_only:
            all_files = [f for f in all_files if self._is_video_file(f.get("name", ""))]

        total_count = len(all_files)

        # 3. 按集数排序并提取序号
        numbered_files = []
        for f in all_files:
            num = self._extract_episode_number(f.get("name", ""))
            numbered_files.append((num, f))
        numbered_files.sort(key=lambda x: (x[0] is None, x[0] if x[0] is not None else 0))

        # 4. 根据模式筛选
        matched_files: list[dict[str, Any]] = []

        if range_mode == "all":
            matched_files = [f for _, f in numbered_files]

        elif range_mode == "range":
            episode_range = str(config.get("episode_range") or "").strip()
            range_set = self._parse_episode_range(episode_range)
            for num, f in numbered_files:
                if num is not None and num in range_set:
                    matched_files.append(f)

        elif range_mode == "latest_n":
            latest_count = int(config.get("latest_count") or 5)
            # 只取有编号且最大的N个
            numbered_only = [(num, f) for num, f in numbered_files if num is not None]
            # 按编号从大到小排序，取前N个
            numbered_only.sort(key=lambda x: x[0], reverse=True)
            latest_items = numbered_only[:latest_count]
            # 恢复编号从小到大排序
            latest_items.sort(key=lambda x: x[0])
            matched_files = [f for _, f in latest_items]

        download_count = len(matched_files)
        skip_count = total_count - download_count

        return {
            "matched_files": matched_files,
            "total_count": total_count,
            "download_count": download_count,
            "skip_count": skip_count,
            "failed_files": [],
            "download_path": download_path,
        }

    def _fetch_share_files(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        """从分享链接获取文件列表。

        真实实现需要调用网盘插件的接口。
        这里返回占位结构，实际部署时替换为真实调用。
        """
        # TODO: 真实实现时，调用对应网盘插件的接口：
        # 1. parse_share 解析分享链接
        # 2. browse_share 浏览分享目录获取文件列表
        # 3. 递归遍历子目录

        # 占位返回：实际部署时替换
        return []

    @staticmethod
    def _is_video_file(filename: str) -> bool:
        """判断是否为视频文件。"""
        filename_lower = filename.lower()
        return any(filename_lower.endswith(ext) for ext in DriveFilterDownloadTaskPlugin.VIDEO_EXTENSIONS)

    @staticmethod
    def _extract_episode_number(filename: str) -> int | None:
        """从文件名中提取集数序号。"""
        if not filename:
            return None
        # 先去掉扩展名
        import os
        name_without_ext = os.path.splitext(filename)[0]

        # 尝试多种模式匹配
        # 1. S01E01 格式
        m = re.search(r"[Ee][Pp]?(\d{1,4})", name_without_ext)
        if m:
            return int(m.group(1))

        # 2. 第X集 格式
        m = re.search(r"第\s*(\d{1,4})\s*[集话話]", name_without_ext)
        if m:
            return int(m.group(1))

        # 3. 纯数字（1-4位）
        m = re.search(r"(?<!\d)(\d{1,4})(?!\d)", name_without_ext)
        if m:
            return int(m.group(1))

        return None

    @staticmethod
    def _validate_episode_range(range_str: str) -> bool:
        """验证集数范围字符串格式。"""
        if not range_str:
            return False
        parts = range_str.split(",")
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # 单集
            if re.match(r"^\d{1,4}$", part):
                continue
            # 范围
            if re.match(r"^\d{1,4}-\d{1,4}$", part):
                start, end = part.split("-")
                if int(start) > int(end):
                    return False
                continue
            return False
        return True

    @staticmethod
    def _parse_episode_range(range_str: str) -> set[int]:
        """解析集数范围字符串为集合。"""
        result: set[int] = set()
        if not range_str:
            return result

        parts = range_str.split(",")
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start_str, end_str = part.split("-", 1)
                start = int(start_str.strip())
                end = int(end_str.strip())
                for i in range(start, end + 1):
                    result.add(i)
            else:
                result.add(int(part))
        return result

    @staticmethod
    def _compress_file_list(files: list[str]) -> str:
        """将文件名列表压缩为易读格式（如 1-15.mp4、16.mp4、18-20.mp4）。"""
        if not files:
            return ""

        numbered: list[tuple[int, str, str]] = []
        unnumbered: list[str] = []

        import os
        for f in files:
            num = DriveFilterDownloadTaskPlugin._extract_episode_number(f)
            if num is not None:
                _, ext = os.path.splitext(f)
                numbered.append((num, ext, f))
            else:
                unnumbered.append(f)

        numbered.sort(key=lambda x: x[0])

        ranges: list[tuple[int, int, str]] = []
        for num, ext, _ in numbered:
            if (
                ranges
                and ranges[-1][1] + 1 == num
                and ranges[-1][2] == ext
            ):
                ranges[-1] = (ranges[-1][0], num, ext)
            else:
                ranges.append((num, num, ext))

        parts: list[str] = []
        for start, end, ext in ranges:
            if start == end:
                parts.append(f"{start}{ext}")
            else:
                parts.append(f"{start}-{end}{ext}")

        if unnumbered:
            parts.extend(unnumbered[:3])
            if len(unnumbered) > 3:
                parts.append(f"...(共{len(unnumbered)}个)")

        return "、".join(parts)

    @staticmethod
    def _find_latest_episode(files: list[dict[str, Any]]) -> str:
        """找到最新剧集（最大序号的文件名）。"""
        if not files:
            return ""

        max_num = -1
        latest = ""
        for f in files:
            name = f.get("name", "")
            num = DriveFilterDownloadTaskPlugin._extract_episode_number(name)
            if num is not None and num > max_num:
                max_num = num
                latest = name

        return latest

    @staticmethod
    def _find_latest_episode_time(files: list[dict[str, Any]]) -> str:
        """找到最新剧集的更新时间。"""
        if not files:
            return ""

        max_num = -1
        latest_time = ""
        for f in files:
            name = f.get("name", "")
            num = DriveFilterDownloadTaskPlugin._extract_episode_number(name)
            if num is not None and num > max_num:
                max_num = num
                mod_time = f.get("modified_at") or f.get("update_time") or ""
                if mod_time:
                    # 格式化时间
                    try:
                        import datetime
                        if isinstance(mod_time, (int, float)):
                            dt = datetime.datetime.fromtimestamp(mod_time)
                        else:
                            dt = datetime.datetime.fromisoformat(str(mod_time).replace("Z", "+00:00"))
                        latest_time = dt.strftime("%Y-%m-%d %H:%M")
                    except (ValueError, OSError):
                        latest_time = str(mod_time)
                else:
                    latest_time = ""

        return latest_time

    @staticmethod
    def _format_time() -> str:
        """格式化当前时间用于日志。"""
        import datetime
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _format_duration(seconds: int) -> str:
        """将秒数格式化为易读时长。"""
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        secs = seconds % 60
        if minutes < 60:
            return f"{minutes}m{secs}s" if secs > 0 else f"{minutes}m"
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h{mins}m"


plugin = DriveFilterDownloadTaskPlugin()
