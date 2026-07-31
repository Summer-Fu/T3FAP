from __future__ import annotations

import os
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


class TransferRenameTaskPlugin(BasePlugin, TaskTypeProvider):
    """转存前文件重命名任务插件

    在网盘转存前对文件进行重命名处理，支持：
    - 序号补零（如 1 → 01）
    - 添加前缀/后缀
    - 字符替换（清理特殊字符）
    - 命名模板自定义
    - 从原文件名提取序号
    """

    plugin_id = "task.transfer_rename"
    plugin_name = "转存前文件重命名任务插件"
    plugin_version = "0.2.0"

    def get_template(self) -> TaskTemplate:
        return TaskTemplate(
            type_key="transfer_rename",
            template_key="transfer_rename",
            plugin_id=self.plugin_id,
            title="转存前文件重命名",
            allow_manual_creation=True,
            supported_inputs=["manual", "resource", "share"],
            form_schema=[
                {
                    "key": "share_url",
                    "label": "网盘分享链接",
                    "type": "string",
                    "required": True,
                    "default": "",
                    "description": "需要转存的网盘分享链接",
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
                    "description": "用于转存的目标网盘账号ID",
                },
                {
                    "key": "target_path",
                    "label": "转存目标路径",
                    "type": "string",
                    "required": True,
                    "default": "/订阅",
                    "description": "文件转存到的目标目录路径",
                },
                {
                    "key": "show_name",
                    "label": "剧集/节目名称",
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "用于重命名的剧集名称（留空则从资源标题提取）",
                },
                {
                    "key": "naming_template",
                    "label": "命名模板",
                    "type": "string",
                    "required": True,
                    "default": "{show_name} - {episode:02d}{ext}",
                    "description": "支持变量：{show_name} {episode} {ext} {original}",
                },
                {
                    "key": "episode_padding",
                    "label": "序号补零位数",
                    "type": "number",
                    "required": False,
                    "default": 2,
                    "description": "集数序号补零位数（如2位：1→01，0表示不补零）",
                },
                {
                    "key": "prefix",
                    "label": "文件名前缀",
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "添加到文件名前的前缀",
                },
                {
                    "key": "suffix",
                    "label": "文件名后缀",
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "添加到扩展名前的后缀",
                },
                {
                    "key": "replace_chars",
                    "label": "需替换的特殊字符",
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "需要替换为空格的特殊字符，用逗号分隔（如：[],（）,【】）",
                },
                {
                    "key": "clean_spaces",
                    "label": "清理多余空格",
                    "type": "boolean",
                    "required": False,
                    "default": True,
                    "description": "是否清理文件名中多余的空格",
                },
                {
                    "key": "rename_subfolders",
                    "label": "递归处理子目录",
                    "type": "boolean",
                    "required": False,
                    "default": False,
                    "description": "是否递归处理子目录中的文件",
                },
                {
                    "key": "episode_offset",
                    "label": "集数偏移量",
                    "type": "number",
                    "required": False,
                    "default": 0,
                    "description": "集数序号偏移量（如：原第1集偏移+5后变为第6集）",
                },
                {
                    "key": "extension_replacements",
                    "label": "扩展名替换",
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "扩展名替换规则，用逗号分隔，如：.zip→.mkv,.rar→.mp4（匹配原扩展名替换为新扩展名）",
                },
            ],
            default_config={
                "target_path": "/订阅",
                "naming_template": "{show_name} - {episode:02d}{ext}",
                "episode_padding": 2,
                "clean_spaces": True,
                "rename_subfolders": False,
                "episode_offset": 0,
            },
            output_types=["transfer_rename.result"],
        )

    def validate_config(self, config: dict[str, Any]) -> OperationResult:
        """校验插件全局配置（config_schema 中的项）。

        注意：任务级配置（如 share_url、target_path）在任务创建时校验，
        不在此全局配置校验中处理。
        """
        errors: list[str] = []

        try:
            padding = int(config.get("default_padding") or 2)
            if padding < 0 or padding > 10:
                errors.append("默认序号补零位数必须在0-10之间")
        except (ValueError, TypeError):
            errors.append("默认序号补零位数必须是有效数字")

        if errors:
            return OperationResult(
                success=False,
                message="插件配置校验失败。",
                errors=errors,
            )
        return OperationResult(success=True, message="配置校验通过。")

    def create_from_resource(self, resource: dict[str, Any]) -> dict[str, Any]:
        title = str(resource.get("title") or "转存任务").strip()
        share_links = resource.get("links", {}).get("share", []) if isinstance(resource.get("links"), dict) else []

        share_url = ""
        share_password = ""
        if share_links and isinstance(share_links, list) and len(share_links) > 0:
            first_link = share_links[0] if isinstance(share_links[0], dict) else {}
            share_url = str(first_link.get("url") or first_link.get("share_url") or "").strip()
            share_password = str(first_link.get("password") or first_link.get("share_password") or "").strip()

        return {
            "title": f"转存并重命名：{title}",
            "input_type": "resource",
            "input_payload": {"resource": resource},
            "config": {
                "share_url": share_url,
                "share_password": share_password,
                "show_name": title,
                "target_path": f"/订阅/{title}",
                "naming_template": "{show_name} - {episode:02d}{ext}",
                "episode_padding": 2,
            },
        }

    def dry_run(self, config: dict[str, Any]) -> OperationResult:
        try:
            result = self._prepare_rename(config, dry_run=True)
            return OperationResult(
                success=True,
                message="dry run 执行成功",
                data={
                    "config": config,
                    "rename_preview": result.get("rename_preview", []),
                    "total_count": result.get("total_count", 0),
                    "rename_count": result.get("rename_count", 0),
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

        logs.append(f"[{self._format_time()}] 转存前重命名任务开始执行")
        logs.append(f"[{self._format_time()}] 任务ID: {task_id}")

        try:
            result = self._prepare_rename(config, dry_run=False)

            rename_preview = result.get("rename_preview", [])
            total_count = result.get("total_count", 0)
            rename_count = result.get("rename_count", 0)
            skip_count = result.get("skip_count", 0)
            failed_items = result.get("failed_items", [])

            logs.append(f"[{self._format_time()}] 发现文件总数: {total_count}")
            logs.append(f"[{self._format_time()}] 将重命名: {rename_count}")
            logs.append(f"[{self._format_time()}] 跳过: {skip_count}")

            for item in rename_preview[:10]:
                logs.append(
                    f"[{self._format_time()}] 重命名: "
                    f"{item.get('original', '')} → {item.get('renamed', '')}"
                )
            if len(rename_preview) > 10:
                logs.append(f"[{self._format_time()}] ... 还有 {len(rename_preview) - 10} 个文件")

            duration = int(time.time() - start_time)
            duration_str = self._format_duration(duration)

            original_list = [item.get("original", "") for item in rename_preview]
            renamed_list = [item.get("renamed", "") for item in rename_preview]

            original_compressed = self._compress_file_list(original_list)
            latest_episode = self._find_latest_episode(original_list)

            summary = (
                f"重命名完成：成功 {rename_count} 项，"
                f"跳过 {skip_count} 项，"
                f"失败 {len(failed_items)} 项，"
                f"耗时 {duration_str}"
            )

            logs.append(f"[{self._format_time()}] 任务完成: {summary}")

            success = len(failed_items) == 0 or rename_count > 0
            status = "success" if success else "partial_success"

            artifacts = [
                {
                    "type": "transfer_rename.summary",
                    "rename_count": rename_count,
                    "skip_count": skip_count,
                    "failed_count": len(failed_items),
                    "total_count": total_count,
                    "duration": duration_str,
                    "duration_seconds": duration,
                },
                {
                    "type": "transfer_rename.file_list",
                    "original_files": original_list,
                    "renamed_files": renamed_list,
                    "rename_preview": rename_preview,
                    "file_list_compressed": original_compressed,
                },
                {
                    "type": "transfer_rename.latest",
                    "episode": latest_episode,
                },
                {
                    "type": "transfer_rename.target",
                    "path": str(config.get("target_path") or "/订阅"),
                },
                {
                    "type": "transfer_rename.failed",
                    "items": failed_items,
                },
                {
                    "type": "transfer_rename.config",
                    "show_name": str(config.get("show_name") or ""),
                    "naming_template": str(config.get("naming_template") or ""),
                    "prefix": str(config.get("prefix") or ""),
                    "suffix": str(config.get("suffix") or ""),
                    "episode_padding": int(config.get("episode_padding") or 0),
                    "episode_offset": int(config.get("episode_offset") or 0),
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
                summary=f"重命名任务失败: {error_msg}",
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

    def _prepare_rename(self, config: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        """准备重命名：获取文件列表、执行重命名、返回结果。"""
        share_url = str(config.get("share_url") or "").strip()

        # 1. 获取分享文件列表
        all_files = self._fetch_share_files(config)
        total_count = len(all_files)

        # 2. 对每个文件执行重命名
        rename_preview: list[dict[str, str]] = []
        failed_items: list[dict[str, str]] = []
        skip_count = 0

        for f in all_files:
            original_name = f.get("name", "")
            item_id = f.get("id", "")

            if not original_name:
                skip_count += 1
                continue

            try:
                renamed_name = self._apply_rename_rules(original_name, config)
                if renamed_name == original_name:
                    skip_count += 1
                    continue

                rename_preview.append({
                    "id": item_id,
                    "original": original_name,
                    "renamed": renamed_name,
                })
            except Exception as exc:
                failed_items.append({
                    "name": original_name,
                    "reason": str(exc),
                })

        rename_count = len(rename_preview)

        return {
            "rename_preview": rename_preview,
            "total_count": total_count,
            "rename_count": rename_count,
            "skip_count": skip_count,
            "failed_items": failed_items,
        }

    def _apply_rename_rules(self, original_name: str, config: dict[str, Any]) -> str:
        """对单个文件名应用重命名规则。"""
        show_name = str(config.get("show_name") or "").strip()
        naming_template = str(config.get("naming_template") or "{original}").strip()
        prefix = str(config.get("prefix") or "").strip()
        suffix = str(config.get("suffix") or "").strip()
        padding = int(config.get("episode_padding") or 0)
        offset = int(config.get("episode_offset") or 0)
        replace_chars = str(config.get("replace_chars") or "").strip()
        clean_spaces = bool(config.get("clean_spaces", True))
        extension_replacements_raw = str(config.get("extension_replacements") or "").strip()

        # 分离文件名和扩展名
        name_part, ext = os.path.splitext(original_name)

        # 0. 扩展名替换（如 .zip → .mkv）
        if extension_replacements_raw:
            ext_replace_map = self._parse_extension_replacements(extension_replacements_raw)
            if ext.lower() in ext_replace_map:
                ext = ext_replace_map[ext.lower()]

        # 提取集数序号
        episode = self._extract_episode_number(name_part)
        if episode is not None:
            episode = episode + offset

        # 1. 字符替换
        if replace_chars:
            for char in replace_chars.split(","):
                char = char.strip()
                if char:
                    name_part = name_part.replace(char, " ")

        # 2. 清理多余空格
        if clean_spaces:
            name_part = re.sub(r"\s+", " ", name_part).strip()

        # 3. 构建模板变量
        template_vars: dict[str, Any] = {
            "show_name": show_name or name_part,
            "original": original_name,
            "ext": ext,
        }

        if episode is not None:
            template_vars["episode"] = episode

        # 4. 应用命名模板
        try:
            new_name = naming_template.format(**template_vars)
        except (KeyError, ValueError, IndexError):
            # 模板出错时退化为原文件名
            new_name = original_name

        # 5. 应用前缀后缀
        name_without_ext, new_ext = os.path.splitext(new_name)
        if prefix:
            name_without_ext = prefix + name_without_ext
        if suffix:
            name_without_ext = name_without_ext + suffix

        final_name = name_without_ext + (new_ext or ext)

        # 6. 应用序号补零（如果模板中没有使用格式化）
        if padding > 0 and episode is not None and "{episode" not in naming_template:
            ep_str = str(episode).zfill(padding)
            # 尝试替换文件名中的数字
            final_name = re.sub(r"(?<!\d)\d+(?!\d)", ep_str, final_name, count=1)

        return final_name

    def _fetch_share_files(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        """从分享链接获取文件列表。

        真实实现需要调用网盘插件的接口。
        这里返回占位结构，实际部署时替换为真实调用。
        """
        # TODO: 真实实现时，调用对应网盘插件的接口：
        # 1. parse_share 解析分享链接
        # 2. browse_share 浏览分享目录获取文件列表
        # 3. 递归遍历子目录（如果启用）

        # 占位返回：实际部署时替换
        return []

    @staticmethod
    def _extract_episode_number(filename: str) -> int | None:
        """从文件名中提取集数序号。"""
        if not filename:
            return None

        # S01E01 格式
        m = re.search(r"[Ee][Pp]?(\d{1,4})", filename)
        if m:
            return int(m.group(1))

        # 第X集 格式
        m = re.search(r"第\s*(\d{1,4})\s*[集话話]", filename)
        if m:
            return int(m.group(1))

        # 纯数字（1-4位）
        m = re.search(r"(?<!\d)(\d{1,4})(?!\d)", filename)
        if m:
            return int(m.group(1))

        return None

    @staticmethod
    def _compress_file_list(files: list[str]) -> str:
        """将文件名列表压缩为易读格式。"""
        if not files:
            return ""

        numbered: list[tuple[int, str, str]] = []
        unnumbered: list[str] = []

        for f in files:
            num = TransferRenameTaskPlugin._extract_episode_number(f)
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
    def _find_latest_episode(files: list[str]) -> str:
        """找到最新剧集（最大序号的文件名）。"""
        if not files:
            return ""

        max_num = -1
        latest = ""
        for name in files:
            num = TransferRenameTaskPlugin._extract_episode_number(name)
            if num is not None and num > max_num:
                max_num = num
                latest = name

        return latest

    @staticmethod
    def _parse_extension_replacements(raw: str) -> dict[str, str]:
        """解析扩展名替换规则字符串。

        支持格式：
        - 使用 → 分隔：.zip→.mkv,.rar→.mp4
        - 使用 -> 分隔：.zip->.mkv,.rar->.mp4
        - 使用 : 分隔：.zip:.mkv,.rar:.mp4

        返回：{原扩展名(小写): 新扩展名} 映射
        """
        result: dict[str, str] = {}
        if not raw:
            return result

        for rule in raw.split(","):
            rule = rule.strip()
            if not rule:
                continue

            # 尝试多种分隔符
            parts = None
            for sep in ("→", "->", ":", "="):
                if sep in rule:
                    parts = rule.split(sep, 1)
                    break

            if parts and len(parts) == 2:
                old_ext = parts[0].strip().lower()
                new_ext = parts[1].strip()
                if old_ext and new_ext:
                    # 确保扩展名以 . 开头
                    if not old_ext.startswith("."):
                        old_ext = "." + old_ext
                    if not new_ext.startswith("."):
                        new_ext = "." + new_ext
                    result[old_ext] = new_ext

        return result

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


plugin = TransferRenameTaskPlugin()
