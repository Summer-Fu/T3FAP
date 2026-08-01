from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx

from core.sdk import AssistantCommand, AssistantProvider, BasePlugin, OperationResult

DEFAULT_T3_API_BASE = "https://t3.midsummer.asia:28888/api"
DEFAULT_T3_API_KEY = "t3mt_QzuZ7KKiKA0rEfKYB5z6jk3ktmfLAWL3NpLgxYpJbrs"
DEFAULT_T3_API_HEADER = "X-API-Key"

MENTION_PATTERN = re.compile(r"@\S+")


# ==================== 命令模板定义 ====================

def build_command_templates(prefix: str = "/") -> list[dict[str, Any]]:
    """构建所有可用命令的模板定义。

    每个命令包含：名称、关键词、说明、用法示例、参数说明。
    """
    return [
        {
            "name": "帮助",
            "keywords": ["帮助", "help", "?", "？", "菜单", "命令"],
            "description": "查看所有可用命令及使用方法",
            "usage": f"{prefix}帮助",
            "examples": [f"{prefix}帮助", f"{prefix}help"],
            "category": "基础",
        },
        {
            "name": "搜索资源",
            "keywords": ["搜索", "搜", "search", "find", "查"],
            "description": "搜索影视资源（电影、剧集、动漫等）",
            "usage": f"{prefix}搜索 <关键词>",
            "examples": [
                f"{prefix}搜索 妖神记",
                f"{prefix}搜索 斩神",
                f"{prefix}搜索 动物世界",
            ],
            "category": "资源",
        },
        {
            "name": "订阅资源",
            "keywords": ["订阅", "subscribe", "追更"],
            "description": "订阅指定资源，有更新时自动转存/生成STRM",
            "usage": f"{prefix}订阅 <资源ID> [模式]",
            "examples": [
                f"{prefix}订阅 12345",
                f"{prefix}订阅 12345 transfer",
            ],
            "params": {"模式": "可选：transfer(转存) 或 strm(生成STRM)，默认 transfer"},
            "category": "资源",
        },
        {
            "name": "转存分享",
            "keywords": ["转存", "保存", "transfer", "save"],
            "description": "将分享链接转存到网盘",
            "usage": f"{prefix}转存 <分享链接> [目标路径]",
            "examples": [
                f"{prefix}转存 https://pan.quark.cn/s/xxxxx",
                f"{prefix}转存 https://www.aliyundrive.com/s/xxxxx /订阅",
            ],
            "category": "资源",
        },
        {
            "name": "任务状态",
            "keywords": ["任务", "状态", "status", "list", "列表"],
            "description": "查询任务列表或指定任务的执行状态",
            "usage": f"{prefix}任务 [任务ID]",
            "examples": [
                f"{prefix}任务",
                f"{prefix}任务 30",
                f"{prefix}状态",
            ],
            "category": "任务",
        },
        {
            "name": "执行任务",
            "keywords": ["执行", "运行", "run", "start"],
            "description": "手动触发一次指定任务的执行",
            "usage": f"{prefix}执行 <任务ID>",
            "examples": [f"{prefix}执行 30"],
            "category": "任务",
        },
        {
            "name": "系统状态",
            "keywords": ["系统", "监控", "system", "monitor", "健康"],
            "description": "查看平台运行概览和最近执行情况",
            "usage": f"{prefix}系统",
            "examples": [f"{prefix}系统", f"{prefix}监控"],
            "category": "系统",
        },
        {
            "name": "插件列表",
            "keywords": ["插件", "plugin", "plugins"],
            "description": "查看当前已安装的插件列表",
            "usage": f"{prefix}插件",
            "examples": [f"{prefix}插件"],
            "category": "系统",
        },
    ]


def build_help_message(prefix: str = "/") -> str:
    """构建帮助消息（命令模板）。"""
    templates = build_command_templates(prefix)

    # 按分类分组
    categories: dict[str, list[dict[str, Any]]] = {}
    for t in templates:
        cat = t.get("category", "其他")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(t)

    # 分类顺序
    category_order = ["基础", "资源", "任务", "系统"]

    lines: list[str] = []
    lines.append("🤖 **T3 影视助手 · 可用命令**")
    lines.append("─────────────────────")
    lines.append(f"命令前缀：`{prefix}`（或 @机器人 + 关键词）")
    lines.append("")

    for cat in category_order:
        items = categories.get(cat, [])
        if not items:
            continue
        cat_emoji = {
            "基础": "📖",
            "资源": "🎬",
            "任务": "📋",
            "系统": "⚙️",
        }.get(cat, "📌")
        lines.append(f"{cat_emoji} **{cat}命令**")
        for item in items:
            name = item["name"]
            usage = item["usage"]
            desc = item["description"]
            lines.append(f"  `{usage}`  —  {desc}")
        lines.append("")

    lines.append("💡 **使用提示**")
    lines.append("  • 直接 @机器人 并输入关键词即可，无需命令前缀")
    lines.append("  • 例如：@机器人 搜索妖神记")
    lines.append("  • 例如：@机器人 帮助")
    lines.append("")
    lines.append("📌 输入 `{prefix}帮助 <命令名>` 查看命令详细用法".format(prefix=prefix))

    return "\n".join(lines)


def build_command_detail(command_name: str, prefix: str = "/") -> str:
    """构建单个命令的详细帮助。"""
    templates = build_command_templates(prefix)
    matched: dict[str, Any] | None = None
    for t in templates:
        if command_name in t["name"] or any(command_name in kw for kw in t["keywords"]):
            matched = t
            break

    if not matched:
        return f"❌ 未找到命令「{command_name}」\n\n输入 `{prefix}帮助` 查看所有可用命令。"

    lines: list[str] = []
    lines.append(f"📖 **命令详情：{matched['name']}**")
    lines.append("─────────────────────")
    lines.append(f"**说明**：{matched['description']}")
    lines.append(f"**用法**：`{matched['usage']}`")
    lines.append("")
    lines.append("**关键词**：" + "、".join(f"`{k}`" for k in matched["keywords"]))
    lines.append("")
    lines.append("**示例**：")
    for ex in matched.get("examples", []):
        lines.append(f"  • `{ex}`")

    params = matched.get("params")
    if params:
        lines.append("")
        lines.append("**参数说明**：")
        for pk, pv in params.items():
            lines.append(f"  • `{pk}`：{pv}")

    return "\n".join(lines)


class DingdingInteractionPlugin(BasePlugin, AssistantProvider):
    plugin_id = "interaction.dingding"
    plugin_name = "钉钉交互机器人"
    plugin_version = "1.1.4"

    def __init__(self) -> None:
        self._runtime_config: dict[str, Any] = {}
        self._api_base: str | None = None
        self._stream_client = None
        self._stream_connected = False
        self._stream_stop_event = None
        self._first_welcome_sent = False

    # ==================== 配置管理 ====================

    def _resolve_config(self, override: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = dict(self._runtime_config)
        if override:
            merged.update(dict(override or {}))
        return merged

    def set_runtime_config(self, config: dict[str, Any]) -> None:
        normalized = self._normalize_runtime_config(config)
        self._runtime_config = normalized
        self._api_base = None
        keys = list(normalized.keys())
        has_credentials = bool(str(normalized.get("app_key") or "").strip()) and bool(
            str(normalized.get("app_secret") or "").strip()
        )
        print(f"[钉钉交互] set_runtime_config: keys={keys}, 凭据已配置={has_credentials}")
        # 打印所有非敏感配置值供调试
        for k, v in normalized.items():
            if k in ("app_key", "app_secret", "t3_api_key"):
                print(f"[钉钉交互][配置]   {k}: {'***已配置***' if v else '(空)'}")
            else:
                print(f"[钉钉交互][配置]   {k}: {repr(v)}")
        # 配置变更后尝试重连 Stream
        self._reconnect_stream()
        # 检测是否需要发送测试消息
        self._trigger_test_message_if_needed()

    def validate_runtime_config(self, config: dict[str, Any]) -> OperationResult:
        normalized = self._normalize_runtime_config(config)
        errors: list[str] = []
        warnings: list[str] = []

        for key in ("app_key", "app_secret"):
            if not str(normalized.get(key) or "").strip():
                errors.append(f"缺少必填配置：{key}")

        # 测试 T3 平台 API 联通性
        t3_base = str(normalized.get("t3_api_base") or "").strip()
        t3_key = str(normalized.get("t3_api_key") or "").strip()
        t3_header = str(normalized.get("t3_api_header") or "").strip() or DEFAULT_T3_API_HEADER

        if t3_base and t3_key:
            print(f"[钉钉交互][配置校验] 测试 T3 API 联通性: {t3_base}")
            test_url = t3_base if t3_base.endswith("/api") or "/api/" in t3_base else t3_base.rstrip("/") + "/api"
            try:
                test_headers = {t3_header: t3_key}
                with httpx.Client(timeout=3, headers=test_headers) as client:
                    resp = client.get(f"{test_url}/tasks?limit=1")
                    if resp.status_code == 200:
                        print(f"[钉钉交互][配置校验] T3 API 联通性测试通过")
                    elif resp.status_code == 401:
                        msg = f"T3 API 认证失败（HTTP 401）：请检查 API Key 是否正确。"
                        warnings.append(msg)
                    else:
                        msg = f"T3 API 联通性异常（HTTP {resp.status_code}）"
                        warnings.append(msg)
            except Exception as e:
                msg = f"T3 API 联通性测试失败：{e}"
                warnings.append(msg)

        data = dict(normalized)
        if warnings:
            data["warnings"] = warnings
        if errors:
            return OperationResult(success=False, message="插件配置校验失败。", errors=errors, data=data)
        msg = "插件配置校验通过。"
        if warnings:
            msg = "插件配置校验通过（有警告）：" + "；".join(warnings)
        return OperationResult(success=True, message=msg, data=data)

    @staticmethod
    def _normalize_runtime_config(config: dict[str, Any] | None) -> dict[str, Any]:
        return dict(config or {})

    # ==================== 平台 API 相关 ====================

    def _get_api_credentials(self) -> tuple[str | None, str | None, str]:
        config = self._resolve_config()
        api_base = str(config.get("t3_api_base") or "").strip()
        api_key = str(config.get("t3_api_key") or "").strip()
        api_header = str(config.get("t3_api_header") or "").strip()

        if not api_base:
            api_base = (
                os.environ.get("T3MT_API_BASE")
                or os.environ.get("T3_API_BASE")
                or os.environ.get("T3MT_HOST")
                or DEFAULT_T3_API_BASE
            )
            if api_base and "/api" not in api_base:
                api_base = api_base.rstrip("/") + "/api"

        if not api_key:
            api_key = (
                os.environ.get("T3MT_API_KEY")
                or os.environ.get("T3_API_KEY")
                or DEFAULT_T3_API_KEY
                or ""
            )

        if not api_header:
            api_header = (
                os.environ.get("T3MT_API_HEADER")
                or os.environ.get("T3_API_HEADER")
                or DEFAULT_T3_API_HEADER
            )

        return api_base if api_base else None, api_key if api_key else None, api_header

    def _get_api_base(self) -> str | None:
        if self._api_base:
            return self._api_base

        api_base, api_key, api_header = self._get_api_credentials()
        if not api_base:
            return None

        probe_headers: dict[str, str] = {}
        if api_key:
            probe_headers[api_header] = api_key
            probe_headers["Authorization"] = f"Bearer {api_key}"

        # 尝试本地回环（容器内运行时）
        import re as _re
        port_match = _re.search(r":(\d+)", api_base)
        if port_match:
            port = port_match.group(1)
            scheme = "https" if api_base.startswith("https") else "http"
            for local in [f"{scheme}://127.0.0.1:{port}", f"{scheme}://localhost:{port}"]:
                test_url = local if "/api" in local else f"{local}/api"
                try:
                    with httpx.Client(timeout=2, headers=probe_headers) as client:
                        resp = client.get(f"{test_url}/tasks?limit=1")
                        if resp.status_code == 200:
                            self._api_base = local
                            print(f"[钉钉交互][平台API] 本地回环探测成功: {local}")
                            return local
                except Exception:
                    pass

        self._api_base = api_base
        return api_base

    def _fetch_api(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> tuple[bool, Any]:
        base = self._get_api_base()
        if not base:
            return False, "未探测到平台API地址"

        api_base_url = base
        if "/api" not in base:
            api_base_url = f"{base}/api"

        clean_path = path
        if clean_path.startswith("/api/"):
            clean_path = clean_path[4:]
        elif clean_path.startswith("api/"):
            clean_path = clean_path[3:]
        if not clean_path.startswith("/"):
            clean_path = "/" + clean_path

        url = f"{api_base_url}{clean_path}"

        _, api_key, api_header_name = self._get_api_credentials()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers[api_header_name] = api_key
            headers["Authorization"] = f"Bearer {api_key}"

        print(f"[钉钉交互][API] {method} {url}")
        try:
            with httpx.Client(timeout=15, headers=headers) as client:
                if method == "GET":
                    resp = client.get(url)
                elif method == "POST":
                    resp = client.post(url, json=payload or {})
                elif method == "PUT":
                    resp = client.put(url, json=payload or {})
                else:
                    return False, f"不支持的方法: {method}"

                print(f"[钉钉交互][API] 结果: HTTP {resp.status_code}")
                if resp.status_code in (200, 201):
                    try:
                        return True, resp.json()
                    except Exception:
                        return True, resp.text
                return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
        except Exception as e:
            print(f"[钉钉交互][API] 异常: {e}")
            return False, str(e)

    # ==================== Stream 模式连接 ====================

    def _ensure_dingtalk_sdk(self) -> bool:
        """确保 dingtalk-stream SDK 已安装，未安装则尝试自动安装。"""
        try:
            import dingtalk_stream  # noqa: F401
            return True
        except ImportError:
            pass

        print(f"[钉钉交互] dingtalk-stream SDK 未找到，尝试自动安装...")
        try:
            import subprocess
            import sys
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "dingtalk-stream", "-q"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                print(f"[钉钉交互] dingtalk-stream SDK 自动安装成功")
                return True
            else:
                print(f"[钉钉交互] dingtalk-stream SDK 自动安装失败: {result.stderr}")
        except Exception as exc:
            print(f"[钉钉交互] 自动安装 dingtalk-stream 异常: {exc}")

        print(f"[钉钉交互] 请手动安装: pip install dingtalk-stream")
        return False

    def _reconnect_stream(self) -> None:
        """配置变更后异步重连 Stream（不阻塞主线程）。"""
        self._stream_connected = False
        self._stream_stop_event = None
        if self._stream_client is not None:
            try:
                self._stream_client.stop()
            except Exception:
                pass
            self._stream_client = None
        print(f"[钉钉交互] Stream 连接已重置，将在后台异步重新连接...")
        # 异步启动，不阻塞配置保存流程
        import threading
        t = threading.Thread(target=self._start_stream_if_needed, daemon=True, name="dingtalk-stream-starter")
        t.start()

    def _start_stream_if_needed(self) -> bool:
        """启动钉钉 Stream 连接（如果已配置凭据）。"""
        import asyncio
        import threading

        config = self._resolve_config()
        app_key = str(config.get("app_key") or "").strip()
        app_secret = str(config.get("app_secret") or "").strip()

        if not app_key or not app_secret:
            print(f"[钉钉交互] Stream 未启动：缺少 AppKey/AppSecret 配置")
            return False

        if self._stream_connected and self._stream_client is not None:
            return True

        # 确保 SDK 可用
        if not self._ensure_dingtalk_sdk():
            return False

        try:
            import dingtalk_stream

            # 插件自身引用，供 handler 内部使用
            plugin_ref = self

            class T3ChatbotHandler(dingtalk_stream.ChatbotHandler):
                """T3 影视助手的消息处理器。"""

                async def process(self, callback: dingtalk_stream.CallbackMessage):
                    try:
                        incoming = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
                        text_content = incoming.text.content if incoming.text else ""
                        sender_nick = incoming.sender_nick or ""
                        chat_type = incoming.chat_type or "unknown"
                        conversation_id = incoming.conversation_id or ""

                        print(f"[钉钉交互] 收到消息: sender={sender_nick}, type={chat_type}, content={text_content[:100]}")

                        # 首次收到消息时，发送欢迎/验证消息
                        is_first_message = not plugin_ref._first_welcome_sent
                        if is_first_message:
                            plugin_ref._first_welcome_sent = True
                            welcome_msg = (
                                "🎉 **T3 影视助手已成功连接！**\n"
                                "─────────────────────\n"
                                f"发送人：{sender_nick}\n"
                                f"会话类型：{'群聊' if chat_type == 'group' else '单聊'}\n"
                                "─────────────────────\n"
                                "✅ 配置正确，通讯正常！\n\n"
                                "输入「帮助」查看所有可用命令。"
                            )
                            try:
                                self.reply_text(welcome_msg, incoming)
                            except Exception as reply_exc:
                                print(f"[钉钉交互] 发送欢迎消息失败: {reply_exc}")
                            print("")
                            print("╔══════════════════════════════════════════════════════════════╗")
                            print("║  ✅  钉钉交互机器人首次通讯成功！已发送欢迎/验证消息          ║")
                            print("╚══════════════════════════════════════════════════════════════╝")
                            print("")
                        else:
                            # 解析并执行命令
                            reply_text = plugin_ref._process_user_command(text_content, sender_nick)
                            try:
                                self.reply_text(reply_text, incoming)
                            except Exception as reply_exc:
                                print(f"[钉钉交互] 回复消息失败: {reply_exc}")

                    except Exception as exc:
                        print(f"[钉钉交互] 处理消息异常: {exc}")
                        import traceback
                        traceback.print_exc()

                    return dingtalk_stream.AckMessage.STATUS_OK, "OK"

            # 创建凭据和客户端
            credential = dingtalk_stream.Credential(app_key, app_secret)
            client = dingtalk_stream.DingTalkStreamClient(credential)

            # 注册机器人消息处理器（topic 固定值）
            client.register_callback_handler(
                dingtalk_stream.chatbot.ChatbotMessage.TOPIC,
                T3ChatbotHandler(),
            )

            self._stream_client = client

            # 在独立线程中启动 event loop，避免阻塞插件主线程
            def _run_stream():
                loop = None
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    print(f"[钉钉交互] Stream 连接线程已启动 (AppKey={app_key[:6]}...)")
                    loop.run_until_complete(client.start())
                except Exception as exc:
                    print(f"[钉钉交互] Stream 连接异常（将自动重连）: {exc}")
                finally:
                    if loop:
                        try:
                            loop.close()
                        except Exception:
                            pass
                    plugin_ref._stream_connected = False
                    print(f"[钉钉交互] Stream 连接已断开")

            stream_thread = threading.Thread(target=_run_stream, daemon=True, name="dingtalk-stream")
            stream_thread.start()
            self._stream_connected = True
            print("")
            print("╔══════════════════════════════════════════════════════════════╗")
            print("║  ✅  钉钉交互机器人 Stream 模式连接成功！                     ║")
            print("╠══════════════════════════════════════════════════════════════╣")
            print(f"║  AppKey:  {app_key[:8]}...{app_key[-4:]}")
            print(f"║  Topic:   {dingtalk_stream.chatbot.ChatbotMessage.TOPIC}")
            print("║                                                              ║")
            print("║  请在钉钉群中 @机器人 输入「帮助」测试通讯是否正常。         ║")
            print("║  如无回复，请查看平台日志中 [钉钉交互] 开头的日志。          ║")
            print("╚══════════════════════════════════════════════════════════════╝")
            print("")
            return True

        except ImportError:
            print(f"[钉钉交互] dingtalk-stream SDK 未安装，Stream 模式不可用。请安装: pip install dingtalk-stream")
            return False
        except Exception as exc:
            print(f"[钉钉交互] Stream 启动失败: {exc}")
            import traceback
            traceback.print_exc()
            return False

    # ==================== 钉钉 API 主动发消息 ====================

    def _get_dingtalk_access_token(self) -> str | None:
        """获取钉钉企业内部应用 access_token。"""
        config = self._resolve_config()
        app_key = str(config.get("app_key") or "").strip()
        app_secret = str(config.get("app_secret") or "").strip()
        if not app_key or not app_secret:
            print(f"[钉钉交互] 获取 access_token 失败：缺少 AppKey/AppSecret")
            return None
        try:
            url = f"https://oapi.dingtalk.com/gettoken?appkey={app_key}&appsecret={app_secret}"
            with httpx.Client(timeout=5) as client:
                resp = client.get(url)
                data = resp.json()
                if data.get("errcode") == 0:
                    token = data.get("access_token")
                    print(f"[钉钉交互] access_token 获取成功")
                    return token
                else:
                    print(f"[钉钉交互] access_token 获取失败: {data}")
                    return None
        except Exception as exc:
            print(f"[钉钉交互] 获取 access_token 异常: {exc}")
            return None

    def _get_userid_by_mobile(self, access_token: str, mobile: str) -> str | None:
        """通过手机号获取钉钉用户 userId。"""
        try:
            url = "https://oapi.dingtalk.com/topapi/v2/user/getbymobile"
            with httpx.Client(timeout=5) as client:
                resp = client.post(url, params={"access_token": access_token}, json={"mobile": mobile})
                data = resp.json()
                if data.get("errcode") == 0:
                    userid = data.get("result", {}).get("userid")
                    print(f"[钉钉交互] 手机号 {mobile[:3]}****{mobile[-4:]} 对应 userId: {userid}")
                    return userid
                else:
                    print(f"[钉钉交互] 手机号查 userId 失败: {data}")
                    return None
        except Exception as exc:
            print(f"[钉钉交互] 手机号查 userId 异常: {exc}")
            return None

    def _send_oto_test_message(self, userid: str) -> bool:
        """向指定 userId 发送单聊测试消息。"""
        config = self._resolve_config()
        access_token = self._get_dingtalk_access_token()
        if not access_token:
            return False
        robot_code = str(config.get("robot_code") or "").strip() or str(config.get("app_key") or "").strip()
        try:
            test_text = (
                "🎉 **T3 影视助手 - 连通性测试**\n"
                "─────────────────────\n"
                "✅ 机器人配置正确\n"
                "✅ API 通讯正常\n"
                "✅ 单聊消息推送成功\n"
                "─────────────────────\n"
                f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                "现在可以回到群里 @机器人 输入「帮助」开始使用！"
            )
            msg_param = json.dumps({"content": test_text}, ensure_ascii=False)
            url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
            headers = {
                "x-acs-dingtalk-access-token": access_token,
                "Content-Type": "application/json",
            }
            payload = {
                "robotCode": robot_code,
                "userIds": [userid],
                "msgKey": "sampleText",
                "msgParam": msg_param,
            }
            with httpx.Client(timeout=8) as client:
                resp = client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    print(f"[钉钉交互] 单聊测试消息发送成功！")
                    return True
                else:
                    print(f"[钉钉交互] 单聊测试消息发送失败: HTTP {resp.status_code} - {resp.text}")
                    return False
        except Exception as exc:
            print(f"[钉钉交互] 单聊测试消息发送异常: {exc}")
            import traceback
            traceback.print_exc()
            return False

    def _trigger_test_message_if_needed(self) -> None:
        """检测配置中的 send_test_message 开关，如开启则发送测试消息并自动关闭。"""
        config = self._resolve_config()
        raw_switch = config.get("send_test_message")
        should_send = False
        if isinstance(raw_switch, bool):
            should_send = raw_switch
        elif isinstance(raw_switch, str):
            should_send = raw_switch.lower() in ("true", "1", "yes", "on")
        mobile = str(config.get("test_receiver_mobile") or "").strip()
        app_key = str(config.get("app_key") or "").strip()
        app_secret = str(config.get("app_secret") or "").strip()

        print(f"[钉钉交互][调试] _trigger_test_message_if_needed called:")
        print(f"[钉钉交互][调试]   send_test_message = {repr(raw_switch)} (type={type(raw_switch).__name__})")
        print(f"[钉钉交互][调试]   should_send (after parse) = {should_send}")
        print(f"[钉钉交互][调试]   test_receiver_mobile = {mobile[:3]}****{mobile[-4:] if len(mobile) > 7 else '***'}")
        print(f"[钉钉交互][调试]   app_key configured = {bool(app_key)}")
        print(f"[钉钉交互][调试]   app_secret configured = {bool(app_secret)}")

        if not should_send:
            print(f"[钉钉交互] 发送测试消息开关未开启，跳过。")
            return

        print(f"[钉钉交互] 检测到「发送测试消息」开关已开启，准备发送测试消息...")

        if not mobile:
            print(f"[钉钉交互] 发送测试消息失败：请先填写「测试消息接收人手机号」")
            return
        if not app_key or not app_secret:
            print(f"[钉钉交互] 发送测试消息失败：请先填写 AppKey 和 AppSecret")
            return

        # 异步执行，不阻塞保存流程
        def _do_send():
            # 先重置开关，避免重复发送
            self._runtime_config["send_test_message"] = False
            access_token = self._get_dingtalk_access_token()
            if not access_token:
                print(f"[钉钉交互] 发送测试消息失败：无法获取 access_token")
                return
            userid = self._get_userid_by_mobile(access_token, mobile)
            if not userid:
                print(f"[钉钉交互] 发送测试消息失败：未找到手机号 {mobile[:3]}****{mobile[-4:]} 对应用户")
                return
            ok = self._send_oto_test_message(userid)
            if ok:
                print("")
                print("╔══════════════════════════════════════════════════════════════╗")
                print("║  ✅  测试消息已通过钉钉机器人成功发送！                        ║")
                print("║  请检查钉钉单聊消息，确认收到后即可正常使用。                  ║")
                print("╚══════════════════════════════════════════════════════════════╝")
                print("")
            else:
                print(f"[钉钉交互] 发送测试消息失败：调用发送消息 API 返回失败")

        import threading
        t = threading.Thread(target=_do_send, daemon=True, name="dingtalk-test-msg")
        t.start()

    # ==================== 命令处理 ====================

    def _process_user_command(self, raw_text: str, sender: str = "") -> str:
        """处理用户输入的命令，返回回复文本。"""
        text = (raw_text or "").strip()
        if not text:
            return "请输入命令，或输入「帮助」查看所有可用命令。"

        # 移除 @提及
        text = MENTION_PATTERN.sub("", text).strip()

        config = self._resolve_config()
        prefix = str(config.get("command_prefix") or "/").strip() or "/"

        # 去除命令前缀
        if text.startswith(prefix):
            text = text[len(prefix):].strip()

        if not text:
            return build_help_message(prefix)

        # 解析命令和参数
        parts = text.split(maxsplit=1)
        command_word = parts[0].strip()
        args = parts[1].strip() if len(parts) > 1 else ""

        # 命令匹配
        templates = build_command_templates(prefix)
        matched_template: dict[str, Any] | None = None
        for t in templates:
            if command_word in t["keywords"] or command_word == t["name"]:
                matched_template = t
                break

        # 帮助命令特殊处理
        if matched_template and matched_template["name"] == "帮助":
            if args:
                return build_command_detail(args, prefix)
            return build_help_message(prefix)

        if matched_template:
            handler = self._get_command_handler(matched_template["name"])
            if handler:
                try:
                    return handler(args, prefix)
                except Exception as exc:
                    print(f"[钉钉交互] 命令执行异常: {exc}")
                    import traceback
                    traceback.print_exc()
                    return f"⚠️ 命令执行出错：{exc}"

        # 未匹配到命令，尝试模糊搜索
        return self._handle_unknown_command(text, prefix)

    def _get_command_handler(self, command_name: str):
        """获取命令对应的处理函数。"""
        handlers = {
            "搜索资源": self._cmd_search,
            "订阅资源": self._cmd_subscribe,
            "转存分享": self._cmd_transfer,
            "任务状态": self._cmd_task_status,
            "执行任务": self._cmd_run_task,
            "系统状态": self._cmd_system_status,
            "插件列表": self._cmd_plugins,
        }
        return handlers.get(command_name)

    def _handle_unknown_command(self, text: str, prefix: str) -> str:
        """处理未识别的命令。"""
        # 尝试作为模糊搜索处理
        if len(text) >= 2:
            return (
                f"❓ 未识别的命令：`{text}`\n"
                f"您可能想输入：`{prefix}搜索 {text}`\n\n"
                f"输入 `{prefix}帮助` 查看所有可用命令。"
            )
        return f"❓ 未识别的命令。输入 `{prefix}帮助` 查看所有可用命令。"

    # ==================== 具体命令实现 ====================

    def _cmd_search(self, args: str, prefix: str) -> str:
        """搜索资源。"""
        if not args:
            return (
                "❌ 请输入搜索关键词\n"
                f"用法：`{prefix}搜索 <关键词>`\n"
                f"示例：`{prefix}搜索 妖神记`"
            )

        keyword = args.strip()
        print(f"[钉钉交互] 搜索资源: {keyword}")

        # 调用搜索 API
        request_payload = {
            "keyword": keyword,
            "media_type": "all",
            "target_type": "all",
            "page": 1,
            "page_size": 10,
        }

        ok, result = self._fetch_api("/resources/search/query", method="POST", payload=request_payload)
        if not ok:
            return f"⚠️ 搜索失败：{result}\n请检查 T3 平台 API 配置。"

        items = []
        if isinstance(result, dict):
            if isinstance(result.get("items"), list):
                items = result["items"]
            elif isinstance(result.get("data"), dict) and isinstance(result["data"].get("items"), list):
                items = result["data"]["items"]

        if not items:
            return f"🔍 搜索「{keyword}」未找到相关资源。"

        lines: list[str] = []
        lines.append(f"🔍 搜索结果：{keyword}（找到 {len(items)} 项）")
        lines.append("─────────────────────")

        for idx, item in enumerate(items[:10], 1):
            title = str(item.get("title") or "未知标题")
            subtitle = str(item.get("subtitle") or "")
            media_type = str(item.get("media_type") or "unknown")
            rid = str(item.get("id") or "")
            year = item.get("year") or ""

            type_label = {
                "movie": "电影",
                "tv": "剧集",
                "variety": "综艺",
                "anime": "动漫",
                "documentary": "纪录片",
                "live": "直播",
            }.get(media_type, media_type)

            info_parts = [type_label]
            if year:
                info_parts.append(str(year))
            info = " · ".join(info_parts)

            lines.append(f"{idx}. **{title}**")
            lines.append(f"   📌 {info}")
            if subtitle:
                lines.append(f"   💬 {subtitle[:60]}")
            if rid:
                lines.append(f"   🔗 资源ID：`{rid}`")
            lines.append(f"   💡 输入 `{prefix}订阅 {rid}` 进行订阅")
            lines.append("")

        if len(items) > 10:
            lines.append(f"…还有 {len(items) - 10} 项未展示")

        return "\n".join(lines)

    def _cmd_subscribe(self, args: str, prefix: str) -> str:
        """订阅资源。"""
        if not args:
            return (
                "❌ 请输入资源ID\n"
                f"用法：`{prefix}订阅 <资源ID> [模式]`\n"
                f"示例：`{prefix}订阅 12345 transfer`\n"
                f"模式可选：transfer（转存，默认）、strm（生成STRM）"
            )

        parts = args.split()
        resource_id = parts[0].strip()
        mode = parts[1].strip() if len(parts) > 1 else "transfer"

        if mode not in ("transfer", "strm"):
            return f"❌ 无效的模式：{mode}\n可选模式：transfer（转存）、strm（生成STRM）"

        print(f"[钉钉交互] 订阅资源: resource_id={resource_id}, mode={mode}")

        # 调用订阅 API
        request_payload = {
            "resource_id": resource_id,
            "mode": mode,
        }

        ok, result = self._fetch_api("/tasks/subscriptions", method="POST", payload=request_payload)
        if not ok:
            return f"⚠️ 订阅失败：{result}\n请检查资源ID是否正确，或 T3 平台 API 配置。"

        task_info = ""
        if isinstance(result, dict):
            item = result.get("item") or result.get("data") or {}
            if isinstance(item, dict):
                task_id = item.get("id") or item.get("task_id") or ""
                title = item.get("title") or item.get("name") or ""
                if task_id:
                    task_info = f"\n📋 任务ID：{task_id}"
                if title:
                    task_info += f"\n📌 任务名称：{title}"

        mode_label = "转存" if mode == "transfer" else "STRM生成"
        return f"✅ 订阅成功（{mode_label}模式）！\n资源ID：`{resource_id}`{task_info}\n\n后续有更新时将自动执行。"

    def _cmd_transfer(self, args: str, prefix: str) -> str:
        """转存分享链接。"""
        if not args:
            return (
                "❌ 请输入分享链接\n"
                f"用法：`{prefix}转存 <分享链接> [目标路径]`\n"
                f"示例：`{prefix}转存 https://pan.quark.cn/s/xxxxx /订阅`"
            )

        parts = args.split(maxsplit=1)
        share_url = parts[0].strip()
        target_path = parts[1].strip() if len(parts) > 1 else "/订阅"

        print(f"[钉钉交互] 转存分享: url={share_url[:50]}, path={target_path}")

        # 验证链接格式
        if not share_url.startswith(("http://", "https://")):
            return f"❌ 无效的分享链接：{share_url}"

        # 通过平台创建转存任务（需要具体的任务模板插件）
        ok, result = self._fetch_api("/tasks/templates")
        templates = []
        if ok and isinstance(result, dict):
            templates = result.get("items") or result.get("data") or []

        # 查找转存相关的任务模板
        transfer_plugin = None
        for tpl in templates:
            if isinstance(tpl, dict):
                pid = str(tpl.get("plugin_id") or tpl.get("id") or "")
                if "transfer" in pid or "转存" in str(tpl.get("name", "")):
                    transfer_plugin = pid
                    break

        if not transfer_plugin:
            return (
                f"⚠️ 未找到转存任务模板\n"
                f"链接：{share_url}\n"
                f"目标路径：{target_path}\n"
                f"请先在平台安装转存类插件。"
            )

        # 执行转存任务
        run_payload = {
            "config": {"target_path": target_path},
            "input_data": {"share_url": share_url},
            "triggered_by": "dingding_bot",
        }
        ok, result = self._fetch_api(f"/tasks/run/{transfer_plugin}", method="POST", payload=run_payload)
        if not ok:
            return f"⚠️ 转存任务启动失败：{result}"

        return f"✅ 转存任务已启动！\n链接：{share_url}\n目标路径：{target_path}\n\n请稍后查看任务状态。"

    def _cmd_task_status(self, args: str, prefix: str) -> str:
        """查询任务状态。"""
        if args:
            # 查询指定任务
            task_id = args.strip()
            print(f"[钉钉交互] 查询任务状态: task_id={task_id}")
            ok, result = self._fetch_api(f"/tasks/{task_id}")
            if not ok:
                return f"⚠️ 查询失败：{result}"

            task = result if isinstance(result, dict) else {}
            if isinstance(result, dict) and result.get("item"):
                task = result["item"]

            if not task:
                return f"❌ 未找到任务ID：{task_id}"

            title = str(task.get("title") or task.get("name") or "未知")
            status = str(task.get("status") or "unknown")
            enabled = task.get("enabled", True)
            plugin_id = str(task.get("plugin_id") or "")
            last_run = str(task.get("last_run_at") or task.get("updated_at") or "")

            status_label = {
                "success": "✅ 成功",
                "failed": "❌ 失败",
                "running": "▶️ 运行中",
                "pending": "⏳ 等待中",
                "disabled": "⏸️ 已停用",
            }.get(status, status)

            lines = [
                f"📋 任务详情：{task_id}",
                "─────────────────────",
                f"📌 名称：{title}",
                f"📊 状态：{status_label}",
                f"🔌 插件：{plugin_id}",
                f"⚡ 启用：{'是' if enabled else '否'}",
            ]
            if last_run:
                lines.append(f"🕐 最近运行：{last_run}")

            lines.append("")
            lines.append(f"💡 输入 `{prefix}执行 {task_id}` 立即执行")

            # 查询执行记录
            ok_exec, exec_result = self._fetch_api("/tasks/executions?limit=5")
            if ok_exec and isinstance(exec_result, dict):
                exec_items = exec_result.get("items") or []
                task_execs = [e for e in exec_items if isinstance(e, dict) and str(e.get("task_id")) == task_id]
                if task_execs:
                    lines.append("")
                    lines.append("📜 最近执行：")
                    for exec_item in task_execs[:3]:
                        eid = exec_item.get("id") or ""
                        estat = exec_item.get("status") or ""
                        ecreated = exec_item.get("created_at") or ""
                        lines.append(f"  • [{estat}] {eid[:12]}… @ {ecreated}")

            return "\n".join(lines)

        # 查询任务列表
        print(f"[钉钉交互] 查询任务列表")
        ok, result = self._fetch_api("/tasks")
        if not ok:
            return f"⚠️ 查询失败：{result}"

        items = []
        if isinstance(result, dict):
            items = result.get("items") or result.get("data") or []

        if not items:
            return "📋 当前没有任务。"

        lines = [f"📋 任务列表（共 {len(items)} 个）", "─────────────────────"]
        for idx, item in enumerate(items[:15], 1):
            tid = str(item.get("id") or "")
            title = str(item.get("title") or item.get("name") or "未知")
            status = str(item.get("status") or "unknown")
            enabled = item.get("enabled", True)

            status_icon = {
                "success": "✅",
                "failed": "❌",
                "running": "▶️",
                "pending": "⏳",
            }.get(status, "📌")

            if not enabled:
                status_icon = "⏸️"

            lines.append(f"{idx}. {status_icon} [{tid}] {title[:40]}")

        if len(items) > 15:
            lines.append(f"…还有 {len(items) - 15} 个任务未展示")

        lines.append("")
        lines.append(f"💡 输入 `{prefix}任务 <ID>` 查看详情")
        return "\n".join(lines)

    def _cmd_run_task(self, args: str, prefix: str) -> str:
        """执行任务。"""
        if not args:
            return (
                "❌ 请输入任务ID\n"
                f"用法：`{prefix}执行 <任务ID>`\n"
                f"先输入 `{prefix}任务` 查看任务列表"
            )

        task_id = args.strip()
        print(f"[钉钉交互] 执行任务: task_id={task_id}")

        ok, result = self._fetch_api(f"/tasks/{task_id}/run", method="POST", payload={})
        if not ok:
            return f"⚠️ 任务启动失败：{result}"

        return f"▶️ 任务已启动：{task_id}\n\n请稍后输入 `{prefix}任务 {task_id}` 查看执行状态。"

    def _cmd_system_status(self, args: str, prefix: str) -> str:
        """系统状态。"""
        print(f"[钉钉交互] 查询系统状态")

        lines: list[str] = ["⚙️ 平台运行概览", "─────────────────────"]

        # 监控概览
        ok, overview = self._fetch_api("/monitor/overview")
        if ok and isinstance(overview, dict):
            data = overview.get("data") or overview
            total_tasks = data.get("total_tasks") or data.get("task_count") or 0
            running_count = data.get("running_executions") or data.get("running") or 0
            success_count = data.get("success_count") or 0
            failed_count = data.get("failed_count") or 0

            lines.append(f"📊 任务总数：{total_tasks}")
            lines.append(f"▶️ 运行中：{running_count}")
            if success_count:
                lines.append(f"✅ 成功数：{success_count}")
            if failed_count:
                lines.append(f"❌ 失败数：{failed_count}")

        # 最近执行
        ok_exec, exec_result = self._fetch_api("/monitor/executions?limit=5")
        if ok_exec and isinstance(exec_result, dict):
            exec_items = exec_result.get("items") or []
            if exec_items:
                lines.append("")
                lines.append("📜 最近执行：")
                for item in exec_items[:5]:
                    tid = str(item.get("task_id") or "")
                    status = str(item.get("status") or "")
                    tname = str(item.get("task_name") or item.get("title") or "")
                    created = str(item.get("created_at") or "")
                    status_icon = {"success": "✅", "failed": "❌", "running": "▶️", "pending": "⏳"}.get(status, "📌")
                    lines.append(f"  {status_icon} [{tid}] {tname[:30]} @ {created[:16]}")

        lines.append("")
        lines.append(f"💡 输入 `{prefix}任务` 查看所有任务")
        return "\n".join(lines)

    def _cmd_plugins(self, args: str, prefix: str) -> str:
        """插件列表。"""
        print(f"[钉钉交互] 查询插件列表")

        ok, result = self._fetch_api("/plugins")
        if not ok:
            return f"⚠️ 查询失败：{result}"

        items = []
        if isinstance(result, dict):
            items = result.get("items") or result.get("data") or []

        if not items:
            return "🔌 当前没有安装任何插件。"

        lines = [f"🔌 插件列表（共 {len(items)} 个）", "─────────────────────"]
        for idx, item in enumerate(items[:20], 1):
            pid = str(item.get("id") or item.get("plugin_id") or "")
            name = str(item.get("name") or "未知")
            version = str(item.get("version") or "")
            enabled = item.get("enabled", True)
            status = "✅" if enabled else "⏸️"
            lines.append(f"{idx}. {status} **{name}** v{version}  (`{pid}`)")

        if len(items) > 20:
            lines.append(f"…还有 {len(items) - 20} 个插件未展示")

        return "\n".join(lines)

    # ==================== AssistantProvider 接口 ====================

    def commands(self) -> list[AssistantCommand] | list[dict[str, Any]]:
        """返回命令列表（用于平台展示）。"""
        config = self._resolve_config()
        prefix = str(config.get("command_prefix") or "/").strip() or "/"
        templates = build_command_templates(prefix)

        result: list[AssistantCommand] = []
        for t in templates:
            examples_str = " / ".join(t.get("examples", []))
            description = t["description"]
            if examples_str:
                description += f"（示例：{examples_str}）"
            result.append(
                AssistantCommand(
                    command=t["usage"],
                    title=t["name"],
                    description=description,
                )
            )
        return result

    def handle(self, command_request: dict[str, Any]) -> dict[str, Any]:
        """处理来自平台的命令请求。"""
        command = str(command_request.get("command") or "").strip()
        sender = str(command_request.get("sender") or command_request.get("user") or "")

        print(f"[钉钉交互] 收到平台命令: {command}")

        reply = self._process_user_command(command, sender)

        return OperationResult(
            success=True,
            message="命令已处理。",
            data={
                "command": command,
                "reply": reply,
                "configured": self._is_configured(),
            },
        ).model_dump(mode="json")

    # ==================== 生命周期 ====================

    def enable(self, ctx: dict[str, Any] | None = None) -> None:
        """插件启用时异步启动 Stream 连接（不阻塞）。"""
        print(f"[钉钉交互] 插件已启用，将在后台异步启动 Stream 连接...")
        import threading
        t = threading.Thread(target=self._start_stream_if_needed, daemon=True, name="dingtalk-stream-enable")
        t.start()

    def disable(self, ctx: dict[str, Any] | None = None) -> None:
        """插件禁用时关闭 Stream 连接。"""
        print(f"[钉钉交互] 插件已禁用")
        if self._stream_client is not None:
            try:
                self._stream_client.stop()
            except Exception:
                pass
            self._stream_client = None
        self._stream_connected = False

    def health(self, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        """健康检查。"""
        api_base_used = self._api_base or "未探测"
        stream_status = "已连接" if self._stream_connected else "未连接"
        api_status = "unknown"
        api_msg = ""

        if self._api_base:
            try:
                _, api_key, api_header = self._get_api_credentials()
                test_headers = {api_header: api_key} if api_key and api_header else {}
                test_url = self._api_base if "/api" in self._api_base else f"{self._api_base}/api"
                with httpx.Client(timeout=3, headers=test_headers) as client:
                    resp = client.get(f"{test_url}/tasks?limit=1")
                    if resp.status_code == 200:
                        api_status = "ok"
                        api_msg = "平台API连接正常"
                    elif resp.status_code == 401:
                        api_status = "error"
                        api_msg = "平台API认证失败"
                    else:
                        api_status = "degraded"
                        api_msg = f"平台API响应异常（HTTP {resp.status_code}）"
            except Exception as e:
                api_status = "error"
                api_msg = f"平台API连接失败：{e}"

        return {
            "status": "ok",
            "message": "钉钉交互机器人插件运行正常。",
            "details": {
                "configured": self._is_configured(),
                "stream_status": stream_status,
                "command_count": len(self.commands()),
                "api_base": api_base_used,
                "api_status": api_status,
                "api_message": api_msg,
            },
        }

    def _is_configured(self) -> bool:
        required = ("app_key", "app_secret")
        return all(bool(str(self._runtime_config.get(key) or "").strip()) for key in required)


plugin = DingdingInteractionPlugin()
