from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from core.sdk import (
    BasePlugin,
    OperationResult,
    TaskExecutionResult,
    TaskTemplate,
    TaskTypeProvider,
)

DEFAULT_VIDEO_EXTENSIONS = [
    ".mp4", ".mkv", ".avi", ".rmvb", ".ts",
    ".mov", ".wmv", ".flv", ".m4v", ".iso",
]

BUILTIN_EPISODE_PATTERNS: list[str] = [
    r"第\s*([0-9]+)\s*[集话話期]",
    r"[Ee][Pp]?\.?\s*0*([0-9]{1,4})(?!\d)",
    r"[Ee]pisode\s*0*([0-9]{1,4})(?!\d)",
    r"0*([0-9]{1,4})\s*[话話期]",
    r"[\[【]\s*0*([0-9]{1,4})\s*[\]】]",
    r"[-_]\s*0*([0-9]{1,4})\s*[-_]",
    r"0*([0-9]{1,3})\s*(?:\.[A-Za-z0-9]+)*$",
]

CHINESE_NUM_MAP = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


class T3FAPIClient:
    """T3FAP REST API 客户端，用于调用平台网盘接口。"""

    def __init__(self, host: str, api_key: str) -> None:
        self._host = host.rstrip("/")
        self._api_key = api_key
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        url = self._host + path
        headers = {"Accept": "application/json", "X-API-Key": self._api_key}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60, context=self._ssl_ctx) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    return resp.status, json.loads(raw)
                except json.JSONDecodeError:
                    return resp.status, raw
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, raw
        except Exception as e:
            return 0, {"error": str(e)}

    def list_accounts(self) -> tuple[int, list[dict[str, Any]]]:
        status, data = self._request("GET", "/api/drive/accounts")
        items = data if isinstance(data, list) else data.get("items", data.get("data", []))
        return status, items

    def parse_share(self, account_id: str, share_ref: dict[str, Any]) -> tuple[int, Any]:
        return self._request(
            "POST",
            f"/api/drive/accounts/{account_id}/share/parse",
            {"share_ref": share_ref},
        )

    def browse_share(self, account_id: str, share_ref: dict[str, Any], parent_id: str = "") -> tuple[int, Any]:
        body: dict[str, Any] = {"share_ref": share_ref}
        if parent_id:
            body["parent_id"] = parent_id
        return self._request(
            "POST",
            f"/api/drive/accounts/{account_id}/share/browse",
            body,
        )

    def save_share(self, account_id: str, share_ref: dict[str, Any],
                   target_parent_id: str = "0",
                   selected_items: list[dict[str, Any]] | None = None) -> tuple[int, Any]:
        body: dict[str, Any] = {
            "share_ref": share_ref,
            "target_parent_id": target_parent_id,
        }
        if selected_items is not None:
            body["selected_items"] = selected_items
        return self._request(
            "POST",
            f"/api/drive/accounts/{account_id}/share/save",
            body,
        )

    def create_folder(self, account_id: str, name: str, parent_id: str = "0") -> tuple[int, Any]:
        return self._request(
            "POST",
            f"/api/drive/accounts/{account_id}/folders",
            {"parent_id": parent_id, "name": name},
        )

    def list_files(self, account_id: str, parent_id: str = "0") -> tuple[int, list[dict[str, Any]]]:
        status, data = self._request(
            "GET",
            f"/api/drive/accounts/{account_id}/files?parent_id={urllib.parse.quote(parent_id)}",
        )
        items = data if isinstance(data, list) else data.get("items", data.get("data", []))
        return status, items


class EpisodeParser:
    """从文件名中解析集数。"""

    def __init__(self, custom_regex: str = "") -> None:
        self._compiled_patterns: list[re.Pattern[str]] = []
        if custom_regex.strip():
            try:
                self._compiled_patterns.append(re.compile(custom_regex))
            except re.error:
                pass
        for pattern in BUILTIN_EPISODE_PATTERNS:
            try:
                self._compiled_patterns.append(re.compile(pattern))
            except re.error:
                continue

    @staticmethod
    def _chinese_to_int(text: str) -> int | None:
        if not text:
            return None
        if text.isdigit():
            return int(text)
        if len(text) == 1 and text in CHINESE_NUM_MAP:
            return CHINESE_NUM_MAP[text]
        if "十" in text:
            parts = text.split("十")
            tens = CHINESE_NUM_MAP.get(parts[0], 1) if parts[0] else 1
            ones = CHINESE_NUM_MAP.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
            return tens * 10 + ones
        return None

    def parse(self, filename: str) -> int | None:
        if not filename:
            return None
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


def is_video_file(name: str, extensions: list[str]) -> bool:
    lower = name.lower()
    return any(lower.endswith(ext) for ext in extensions)


class QuarkShareFilterPlugin(BasePlugin, TaskTypeProvider):
    plugin_id = "task.quark_share_filter"
    plugin_name = "夸克分享过滤转存"
    plugin_version = "1.0.0"

    def __init__(self) -> None:
        self._runtime_config: dict[str, Any] = {}

    def set_runtime_config(self, config: dict[str, Any]) -> None:
        self._runtime_config = dict(config or {})

    def validate_runtime_config(self, config: dict[str, Any]) -> OperationResult:
        return OperationResult(success=True, message="插件配置校验通过。", data=dict(config or {}))

    def health(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "message": "夸克分享过滤转存插件运行正常。",
            "details": {
                "enabled": self._runtime_config.get("enabled", True),
                "default_latest_n": self._runtime_config.get("default_latest_n", 5),
            },
        }

    # ------------------------------------------------------------------
    # TaskTypeProvider 协议
    # ------------------------------------------------------------------

    def get_template(self) -> TaskTemplate:
        return TaskTemplate(
            type_key="quark_share_filter",
            template_key="quark_share_filter",
            plugin_id=self.plugin_id,
            title="夸克分享过滤转存",
            allow_manual_creation=True,
            supported_inputs=["manual", "resource"],
            form_schema=self._build_form_schema(),
            default_config={
                "share_url": "",
                "share_password": "",
                "target_parent_id": "0",
                "latest_n": int(self._runtime_config.get("default_latest_n", 5)),
                "others_folder_name": str(self._runtime_config.get("others_folder_name", "其他剧集")),
                "drive_account_id": "",
            },
            output_types=["quark_share_filter.result"],
        )

    def validate_config(self, config: dict[str, Any]) -> OperationResult:
        errors: list[str] = []
        share_url = str(config.get("share_url") or "").strip()
        if not share_url:
            errors.append("请填写分享链接")
        if not share_url.startswith("http"):
            errors.append("分享链接格式不正确")
        n = config.get("latest_n") or 5
        if int(n) < 1:
            errors.append("最新 N 集必须 >= 1")
        if errors:
            return OperationResult(success=False, message="任务配置校验失败。", errors=errors)
        return OperationResult(success=True, message="任务配置校验通过。")

    def create_from_resource(self, resource: dict[str, Any]) -> dict[str, Any]:
        title = str(resource.get("title") or "夸克分享任务").strip()
        share_url = ""
        share_password = ""
        links = resource.get("links") or {}
        if isinstance(links, dict):
            share_links = links.get("share") or []
            if isinstance(share_links, list) and share_links:
                first_share = share_links[0]
                if isinstance(first_share, dict) and "quark" in str(first_share.get("drive_type", "")).lower():
                    share_url = str(first_share.get("url") or "").strip()
                    share_password = str(first_share.get("password") or "").strip()
        return {
            "title": f"夸克过滤转存：{title}",
            "input_type": "resource",
            "input_payload": {"resource": resource},
            "config": {
                "share_url": share_url,
                "share_password": share_password,
                "target_parent_id": "0",
                "latest_n": int(self._runtime_config.get("default_latest_n", 5)),
                "others_folder_name": str(self._runtime_config.get("others_folder_name", "其他剧集")),
                "drive_account_id": "",
            },
        }

    def dry_run(self, config: dict[str, Any]) -> OperationResult:
        return OperationResult(
            success=True,
            message="预览功能暂不支持，将直接执行转存。",
        )

    def execute(self, execution_context: dict[str, Any]) -> TaskExecutionResult:
        config = execution_context.get("config") or {}
        logs: list[str] = []
        logs.append(f"[{self.plugin_id}] 开始执行夸克分享过滤转存任务")

        try:
            host = str(
                self._runtime_config.get("t3fap_host")
                or os.environ.get("T3MT_HOST")
                or "http://127.0.0.1:8521"
            ).strip()
            api_key = str(
                self._runtime_config.get("t3fap_api_key")
                or os.environ.get("T3MT_API_KEY")
                or ""
            ).strip()

            if not api_key:
                return TaskExecutionResult(
                    success=False,
                    status="failed",
                    summary="未配置 T3FAP API 密钥，请在插件设置中配置。",
                    logs=logs + ["错误：缺少 API 密钥"],
                )

            client = T3FAPIClient(host, api_key)

            share_url = str(config.get("share_url") or "").strip()
            share_password = str(config.get("share_password") or "").strip()
            target_parent_id = str(config.get("target_parent_id") or "0").strip()
            latest_n = int(config.get("latest_n") or self._runtime_config.get("default_latest_n", 5))
            others_folder_name = str(config.get("others_folder_name") or self._runtime_config.get("others_folder_name", "其他剧集")).strip()
            preferred_account_id = str(config.get("drive_account_id") or "").strip()

            video_ext_str = str(self._runtime_config.get("video_extensions") or "").strip()
            video_extensions = (
                [e.strip().lower() for e in video_ext_str.split(",") if e.strip()]
                if video_ext_str
                else list(DEFAULT_VIDEO_EXTENSIONS)
            )

            logs.append(f"目标 API: {host}")
            logs.append(f"分享链接: {share_url}")
            logs.append(f"最新 N 集: {latest_n}")
            logs.append(f"其他剧集文件夹: {others_folder_name}")

            # 1. 查找可用的夸克网盘账号
            status, accounts = client.list_accounts()
            if status >= 400 or not accounts:
                return TaskExecutionResult(
                    success=False, status="failed",
                    summary=f"获取网盘账号失败：HTTP {status}",
                    logs=logs + [f"错误: {accounts}"],
                )
            quark_accounts = [a for a in accounts if str(a.get("plugin_id", "")) == "drive.quark"]
            if not quark_accounts:
                return TaskExecutionResult(
                    success=False, status="failed",
                    summary="没有找到可用的夸克网盘账号。",
                    logs=logs + ["错误: 没有 drive.quark 账号"],
                )

            # 优先使用用户指定的账号，否则选主账号
            account = None
            if preferred_account_id:
                account = next((a for a in quark_accounts if str(a.get("id")) == preferred_account_id), None)
            if account is None:
                main_accounts = [a for a in quark_accounts if a.get("is_main")]
                account = main_accounts[0] if main_accounts else quark_accounts[0]
            account_id = str(account.get("id"))
            logs.append(f"使用账号: {account.get('display_name', account_id)} (id={account_id})")

            # 2. 构造 share_ref
            share_ref = {"url": share_url}
            if share_password:
                share_ref["password"] = share_password

            # 3. 解析分享
            logs.append("正在解析分享链接...")
            status, parsed = client.parse_share(account_id, share_ref)
            if status >= 400:
                return TaskExecutionResult(
                    success=False, status="failed",
                    summary=f"解析分享失败：HTTP {status}",
                    logs=logs + [f"错误: {parsed}"],
                )
            logs.append("分享解析成功")

            # 4. 浏览分享内容
            logs.append("正在浏览分享内容...")
            status, browse_result = client.browse_share(account_id, share_ref)
            if status >= 400:
                return TaskExecutionResult(
                    success=False, status="failed",
                    summary=f"浏览分享失败：HTTP {status}",
                    logs=logs + [f"错误: {browse_result}"],
                )

            items = browse_result if isinstance(browse_result, list) else browse_result.get("items", browse_result.get("data", []))
            logs.append(f"分享中包含 {len(items)} 个文件/文件夹")

            if not items:
                return TaskExecutionResult(
                    success=True, status="completed",
                    summary="分享内容为空，没有需要转存的文件。",
                    logs=logs + ["提示: 分享目录为空"],
                )

            # 5. 解析集数并分类
            parser = EpisodeParser()
            video_items: list[dict[str, Any]] = []
            other_items: list[dict[str, Any]] = []
            folder_items: list[dict[str, Any]] = []

            for item in items:
                name = str(item.get("name") or "")
                item_type = str(item.get("type") or "file").lower()
                if item_type == "folder":
                    folder_items.append(item)
                    continue
                if is_video_file(name, video_extensions):
                    ep = parser.parse(name)
                    item_copy = dict(item)
                    item_copy["episode"] = ep
                    if ep is not None:
                        video_items.append(item_copy)
                    else:
                        other_items.append(item_copy)
                else:
                    other_items.append(item)

            logs.append(f"识别到 {len(video_items)} 个可识别集数的视频，{len(other_items)} 个其他文件，{len(folder_items)} 个文件夹")

            # 6. 过滤最新 N 集
            video_items.sort(key=lambda x: x.get("episode") or 0)
            all_episodes = [v["episode"] for v in video_items if v.get("episode")]
            max_ep = max(all_episodes) if all_episodes else 0
            min_target = max(1, max_ep - latest_n + 1) if max_ep > 0 else 1

            latest_items: list[dict[str, Any]] = []
            older_items: list[dict[str, Any]] = []

            for v in video_items:
                ep = v.get("episode") or 0
                if ep >= min_target and ep <= max_ep:
                    latest_items.append(v)
                else:
                    older_items.append(v)

            logs.append(f"集数范围: 第{min(all_episodes) if all_episodes else 0}集 ~ 第{max_ep}集")
            logs.append(f"最新 {latest_n} 集: 第{min_target}集 ~ 第{max_ep}集, 共 {len(latest_items)} 个文件")
            logs.append(f"其余集数: 共 {len(older_items)} 个文件")

            # 7. 转存最新 N 集到根目录
            if latest_items or other_items:
                logs.append("正在转存最新 N 集和其他文件到根目录...")
                root_items = latest_items + other_items
                # 分批转存，每次最多 50 个
                batch_size = 50
                for i in range(0, len(root_items), batch_size):
                    batch = root_items[i:i + batch_size]
                    selected = [{"id": it.get("id"), "type": it.get("type", "file"), "name": it.get("name")} for it in batch]
                    status, result = client.save_share(account_id, share_ref, target_parent_id, selected)
                    if status >= 400:
                        logs.append(f"警告: 第 {i // batch_size + 1} 批转存失败: {result}")
                    else:
                        logs.append(f"第 {i // batch_size + 1} 批转存成功 ({len(batch)} 个文件)")
                    time.sleep(1)

            # 8. 转存其余集数到"其他剧集"子文件夹
            if older_items or folder_items:
                logs.append(f"正在创建 '{others_folder_name}' 文件夹...")
                status, folder_result = client.create_folder(account_id, others_folder_name, target_parent_id)
                if status >= 400:
                    logs.append(f"警告: 创建文件夹失败，尝试直接使用目标ID转存: {folder_result}")
                    others_folder_id = target_parent_id
                else:
                    others_folder_id = str(
                        folder_result.get("id")
                        or folder_result.get("data", {}).get("id")
                        or target_parent_id
                    )
                    logs.append(f"文件夹创建成功，ID: {others_folder_id}")

                time.sleep(1)

                sub_items = older_items + folder_items
                logs.append(f"正在转存 {len(sub_items)} 个文件/文件夹到 '{others_folder_name}'...")
                batch_size = 50
                for i in range(0, len(sub_items), batch_size):
                    batch = sub_items[i:i + batch_size]
                    selected = [{"id": it.get("id"), "type": it.get("type", "file"), "name": it.get("name")} for it in batch]
                    status, result = client.save_share(account_id, share_ref, others_folder_id, selected)
                    if status >= 400:
                        logs.append(f"警告: 子文件夹第 {i // batch_size + 1} 批转存失败: {result}")
                    else:
                        logs.append(f"子文件夹第 {i // batch_size + 1} 批转存成功 ({len(batch)} 个文件)")
                    time.sleep(1)

            summary = (
                f"转存完成：最新 {latest_n} 集({len(latest_items)}个文件)保存到根目录，"
                f"其余 {len(older_items)} 个文件保存到 '{others_folder_name}' 子文件夹。"
            )
            logs.append(summary)

            return TaskExecutionResult(
                success=True,
                status="success",
                summary=summary,
                artifacts=[
                    {
                        "type": "quark_share_filter.result",
                        "value": {
                            "share_url": share_url,
                            "latest_n": latest_n,
                            "latest_count": len(latest_items),
                            "others_count": len(older_items),
                            "others_folder": others_folder_name,
                            "video_count": len(video_items),
                        },
                        "description": "转存结果统计",
                    },
                ],
                logs=logs,
            )

        except Exception as e:
            logs.append(f"执行失败：{e}")
            import traceback
            logs.append(traceback.format_exc())
            return TaskExecutionResult(
                success=False,
                status="failed",
                summary=f"夸克分享过滤转存任务执行失败：{e}",
                artifacts=[],
                logs=logs,
            )

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _build_form_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "share_url",
                "label": "夸克分享链接",
                "type": "string",
                "required": True,
                "default": "",
                "placeholder": "如：https://pan.quark.cn/s/abc123",
                "description": "夸克网盘的分享链接URL。",
                "group": "分享设置",
            },
            {
                "key": "share_password",
                "label": "分享密码",
                "type": "string",
                "required": False,
                "default": "",
                "description": "夸克网盘分享的访问密码（如有）。",
                "secret": True,
                "group": "分享设置",
            },
            {
                "key": "target_parent_id",
                "label": "目标目录ID",
                "type": "string",
                "required": False,
                "default": "0",
                "placeholder": "0 表示根目录",
                "description": "转存到个人网盘的哪个目录下，填目录ID，0 表示根目录。",
                "group": "转存设置",
            },
            {
                "key": "latest_n",
                "label": "保存最新 N 集",
                "type": "integer",
                "required": True,
                "default": int(self._runtime_config.get("default_latest_n", 5)),
                "min": 1,
                "description": "最新的 N 集会保存到目标目录根目录下。",
                "group": "过滤设置",
            },
            {
                "key": "others_folder_name",
                "label": "其他剧集文件夹名",
                "type": "string",
                "required": False,
                "default": str(self._runtime_config.get("others_folder_name", "其他剧集")),
                "description": "除了最新 N 集外的内容都会保存到这个子文件夹中。",
                "group": "过滤设置",
            },
            {
                "key": "drive_account_id",
                "label": "指定网盘账号ID",
                "type": "string",
                "required": False,
                "default": "",
                "placeholder": "留空则使用主账号",
                "description": "指定要使用的夸克网盘账号ID，留空则自动使用主账号。",
                "group": "高级设置",
            },
        ]


plugin = QuarkShareFilterPlugin()
