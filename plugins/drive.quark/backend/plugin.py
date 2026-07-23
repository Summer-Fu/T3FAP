from __future__ import annotations

import re
import time
from typing import Any

from core.sdk import BasePlugin, HealthReport


# ---------------------------------------------------------------------------
# 集数解析引擎（从 task.episode_filter 插件移植）
# ---------------------------------------------------------------------------

BUILTIN_EPISODE_PATTERNS = [
    # 第1集 / 第01集 / 第12话 / 第03期
    r"第\s*(\d+)\s*[集话話期]",
    # 中文数字：第一集 / 第十二话
    r"第([一二三四五六七八九十百零\d]+)\s*[集话話期]",
    # EP01 / E01 / ep.01 / e.01（不区分大小写）
    r"[Ee][Pp]?\.?\s*0*(\d{1,4})(?!\d)",
    # Episode 01
    r"[Ee]pisode\s*0*(\d{1,4})(?!\d)",
    # 01话 / 01話 / 1期 / 3集
    r"0*(\d{1,4})\s*[话話期集]",
    # [01] / 【01】
    r"[\[【]\s*0*(\d{1,4})\s*[\]】]",
    # _01_ / - 01 -（分隔符包围的数字）
    r"[-_]\s*0*(\d{1,3})\s*[-_]",
    # 文件名末尾纯数字：剧名.24.mp4
    r"0*(\d{1,3})\s*(?:\.[A-Za-z0-9]+)*$",
]

# 中文数字映射
CN_NUM_MAP = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "百": 100,
}

DEFAULT_VIDEO_EXTENSIONS = [
    ".mp4", ".mkv", ".avi", ".rmvb", ".ts", ".mov",
    ".wmv", ".flv", ".m4v", ".iso",
]


class EpisodeParser:
    """集数解析器，从文件名中识别剧集编号。"""

    def __init__(self, custom_regex: str = "", video_extensions: list[str] | None = None):
        self.patterns = [re.compile(p) for p in BUILTIN_EPISODE_PATTERNS]
        if custom_regex:
            try:
                self.patterns.insert(0, re.compile(custom_regex))
            except re.error:
                pass
        self.video_extensions = video_extensions or DEFAULT_VIDEO_EXTENSIONS

    def parse_episode(self, filename: str) -> int | None:
        if not filename:
            return None
        name_base = filename
        for ext in self.video_extensions:
            if name_base.lower().endswith(ext):
                name_base = name_base[: -len(ext)]
                break
        for pattern in self.patterns:
            match = pattern.search(name_base)
            if not match:
                continue
            raw = match.group(1)
            if raw.isdigit():
                num = int(raw)
                if num > 0:
                    return num
            # 中文数字转换
            num = self._cn_to_int(raw)
            if num is not None and num > 0:
                return num
        return None

    def _cn_to_int(self, text: str) -> int | None:
        if not text or not all(c in CN_NUM_MAP for c in text):
            return None
        result = 0
        for c in text:
            result += CN_NUM_MAP[c]
        return result

    def parse_episode_list(self, text: str) -> list[int]:
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

    def is_video(self, filename: str) -> bool:
        return any(filename.lower().endswith(ext) for ext in self.video_extensions)


# ---------------------------------------------------------------------------
# 夸克网盘 API 客户端
# ---------------------------------------------------------------------------

QUARK_BASE_URL = "https://drive-pc.quark.cn"
QUARK_BASE_PARAMS = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
QUARK_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://pan.quark.cn",
    "Referer": "https://pan.quark.cn/",
}

QUARK_UOP_URL = "https://uop.quark.cn"
QUARK_DESKTOP_LOCAL_URL = "http://127.0.0.1:9128"
QUARK_CLIENT_ID = "532"


class QuarkAPI:
    """夸克网盘 HTTP API 客户端，负责与夸克服务器交互。"""

    def __init__(self, cookie: str = ""):
        self.cookie = cookie
        self.headers = {**QUARK_DEFAULT_HEADERS, "Cookie": cookie} if cookie else {**QUARK_DEFAULT_HEADERS}

    # ---- 扫码登录 API ----

    @staticmethod
    def _gen_request_id() -> str:
        """生成随机请求ID。"""
        import uuid
        return str(uuid.uuid4())

    @staticmethod
    def start_qr_login() -> dict[str, Any]:
        """
        发起扫码登录，获取二维码。

        返回：
        - token: 扫码登录令牌（用于后续轮询）
        - qrcode_url: 二维码图片URL
        - qrcode_content: 二维码内容（用于前端渲染）
        """
        request_id = QuarkAPI._gen_request_id()
        url = f"{QUARK_UOP_URL}/cas/ajax/getTokenForQrcodeLogin"
        params = {
            "client_id": QUARK_CLIENT_ID,
            "v": "1.2",
            "request_id": request_id,
        }
        result = QuarkAPI._static_get(url, params)

        if result.get("success") or result.get("code") == 0 or result.get("status") == 200:
            data = result.get("data", result)
            token = data.get("token", "")
            qrcode_url = data.get("qrcodeUrl", "") or data.get("qrCodeUrl", "")
            return {
                "success": True,
                "token": token,
                "qrcode_url": qrcode_url,
                "qrcode_content": token,
                "request_id": request_id,
            }
        return {
            "success": False,
            "error": result.get("message", "获取二维码失败"),
            "raw": result,
        }

    @staticmethod
    def check_qrcode_status(token: str, request_id: str = "") -> dict[str, Any]:
        """
        轮询扫码登录状态。

        返回状态：
        - waiting: 等待扫码
        - scanned: 已扫码，等待确认
        - confirmed: 已确认登录，返回ticket
        - expired: 二维码已过期
        - cancelled: 已取消
        """
        req_id = request_id or QuarkAPI._gen_request_id()
        url = f"{QUARK_UOP_URL}/cas/ajax/getServiceTicketByQrcodeToken"
        params = {
            "client_id": QUARK_CLIENT_ID,
            "v": "1.2",
            "request_id": req_id,
            "token": token,
        }
        result = QuarkAPI._static_get(url, params)

        code = result.get("code")
        data = result.get("data", result)

        if code == 0 or result.get("success"):
            ticket = data.get("ticket", "")
            if ticket:
                return {"status": "confirmed", "ticket": ticket, "data": data}
            else:
                status_code = data.get("code", data.get("status", 0))
                if status_code == 10001:
                    return {"status": "waiting", "data": data}
                elif status_code == 10002:
                    return {"status": "scanned", "data": data}
                elif status_code == 10003:
                    return {"status": "expired", "data": data}
                else:
                    return {"status": "waiting", "data": data}
        else:
            return {"status": "error", "error": result.get("message", "查询状态失败"), "raw": result}

    @staticmethod
    def ticket_to_cookie(ticket: str) -> dict[str, Any]:
        """
        使用登录ticket换取Cookie。

        注意：这一步通常需要调用夸克的登录接口。由于夸克网页版在拿到ticket后，
        会通过一系列重定向来设置Cookie。我们这里直接尝试使用ticket换取用户信息，
        并尝试构造可用的Cookie。
        """
        url = f"{QUARK_UOP_URL}/cas/login"
        params = {
            "client_id": QUARK_CLIENT_ID,
            "v": "1.2",
            "ticket": ticket,
            "redirect_url": "https://pan.quark.cn/",
        }
        try:
            import requests as _requests
            session = _requests.Session()
            resp = session.get(url, params=params, headers=QUARK_DEFAULT_HEADERS, timeout=30, allow_redirects=True)
            cookies_dict = session.cookies.get_dict()
            cookie_str = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])
            if cookie_str:
                api = QuarkAPI(cookie_str)
                info = api.get_account_info()
                if info.get("success"):
                    return {
                        "success": True,
                        "cookie": cookie_str,
                        "nickname": info.get("nickname", ""),
                        "user_id": info.get("user_id", ""),
                    }
                return {"success": True, "cookie": cookie_str}
            return {"success": False, "error": "未能获取到Cookie"}
        except Exception as e:
            return {"success": False, "error": f"换取Cookie失败：{str(e)}"}

    # ---- 本地桌面客户端接口（从系统读取登录状态）----

    @staticmethod
    def check_desktop_client() -> dict[str, Any]:
        """
        检查本地夸克桌面客户端是否运行。

        这就是"从系统读取Cookie"的方式：
        夸克桌面客户端会在本地启动一个HTTP服务（端口9128），
        网页可以通过这个服务获取登录凭证。
        """
        try:
            import requests as _requests
            resp = _requests.get(f"{QUARK_DESKTOP_LOCAL_URL}/desktop_info", timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                return {"success": True, "info": data}
            return {"success": False, "error": "桌面客户端未响应"}
        except Exception:
            return {"success": False, "error": "桌面客户端未运行或端口不可达"}

    @staticmethod
    def get_cookie_from_desktop() -> dict[str, Any]:
        """
        从本地夸克桌面客户端获取登录Cookie。

        这是最方便的方式：只要用户在电脑上打开了夸克桌面客户端并登录了，
        就可以直接获取到Cookie，无需手动复制。
        """
        try:
            import requests as _requests
            info_resp = _requests.get(f"{QUARK_DESKTOP_LOCAL_URL}/desktop_info", timeout=3)
            if info_resp.status_code != 200:
                return {"success": False, "error": "桌面客户端未运行"}

            token_resp = _requests.get(f"{QUARK_DESKTOP_LOCAL_URL}/desktop_webtokenid", timeout=3)
            if token_resp.status_code != 200:
                return {"success": False, "error": "获取tokenId失败"}

            token_data = token_resp.json()
            token_id = token_data.get("tokenId") or token_data.get("token_id") or ""
            if not token_id:
                return {"success": False, "error": "tokenId为空"}

            cookie_resp = _requests.get(
                f"{QUARK_DESKTOP_LOCAL_URL}/desktop_webtoken",
                params={"tokenId": token_id, "platform": "browser"},
                timeout=5,
            )
            if cookie_resp.status_code != 200:
                return {"success": False, "error": "获取Cookie失败"}

            cookie_data = cookie_resp.json()
            token = cookie_data.get("token") or cookie_data.get("webToken") or ""
            if not token:
                return {"success": False, "error": "返回的token为空"}

            cookie_str = f"__pushtokenxxx={token}"
            api = QuarkAPI(cookie_str)
            info = api.get_account_info()
            if info.get("success"):
                return {
                    "success": True,
                    "cookie": cookie_str,
                    "nickname": info.get("nickname", ""),
                    "user_id": info.get("user_id", ""),
                    "source": "desktop",
                }

            return {
                "success": True,
                "cookie": cookie_str,
                "source": "desktop",
                "note": "Cookie已获取，有效性待验证",
            }

        except Exception as e:
            return {"success": False, "error": f"从桌面客户端获取Cookie失败：{str(e)}"}

    @staticmethod
    def _static_get(url: str, params: dict[str, Any]) -> dict[str, Any]:
        """静态GET请求（不需要Cookie）。"""
        try:
            from core.services.resource_http import fetch_json
            return fetch_json(url, params=params, headers=QUARK_DEFAULT_HEADERS)
        except ImportError:
            try:
                import requests
                resp = requests.get(url, params=params, headers=QUARK_DEFAULT_HEADERS, timeout=30)
                return resp.json()
            except Exception:
                return {"code": -1, "message": "请求失败"}

    # ---- 分享相关 API ----

    def get_stoken(self, pwd_id: str, passcode: str = "") -> dict[str, Any]:
        """获取分享访问凭证（stoken）。"""
        url = f"{QUARK_BASE_URL}/1/clouddrive/share/sharepage/token"
        payload = {"pwd_id": pwd_id, "passcode": passcode}
        result = self._post(url, payload)
        if result.get("status") == 200 or result.get("code") == 0:
            data = result.get("data", result)
            return {"stoken": data.get("stoken", ""), "share_id": pwd_id}
        return {"error": result.get("message", "获取stoken失败"), "raw": result}

    def browse_share(
        self,
        pwd_id: str,
        stoken: str,
        pdir_fid: str = "0",
        page: int = 1,
        page_size: int = 200,
    ) -> dict[str, Any]:
        """列出分享目录下的文件。"""
        url = f"{QUARK_BASE_URL}/1/clouddrive/share/sharepage/detail"
        params = {
            **QUARK_BASE_PARAMS,
            "pwd_id": pwd_id,
            "stoken": stoken,
            "pdir_fid": pdir_fid,
            "_page": str(page),
            "_size": str(page_size),
            "_fetch_total": "1",
            "_fetch_share": "1",
            "fetch_share_full_path": "1",
        }
        result = self._get(url, params)
        if result.get("code") == 0:
            data = result.get("data", {})
            items = data.get("list", [])
            total = data.get("metadata", {}).get("_total", len(items))
            return {"items": items, "total": total, "stoken": stoken}
        return {"error": result.get("message", "获取分享文件列表失败"), "items": [], "total": 0}

    def save_share_files(
        self,
        pwd_id: str,
        stoken: str,
        fid_list: list[str],
        fid_token_list: list[str],
        to_pdir_fid: str,
        pdir_fid: str = "0",
        scene: str = "link",
    ) -> dict[str, Any]:
        """将分享中的指定文件保存到个人网盘指定目录。"""
        url = f"{QUARK_BASE_URL}/1/clouddrive/share/sharepage/save"
        payload = {
            "fid_list": fid_list,
            "fid_token_list": fid_token_list,
            "to_pdir_fid": to_pdir_fid,
            "pwd_id": pwd_id,
            "stoken": stoken,
            "pdir_fid": pdir_fid,
            "scene": scene,
        }
        result = self._post(url, payload)
        if result.get("code") == 0:
            task_id = result.get("data", {}).get("task_id", "")
            # 等待转存任务完成
            if task_id:
                task_result = self._poll_task(task_id)
                if task_result:
                    return {"success": True, "task_id": task_id, "saved_count": len(fid_list)}
            return {"success": True, "task_id": task_id, "saved_count": len(fid_list)}
        return {"success": False, "error": result.get("message", "转存失败"), "raw": result}

    # ---- 文件系统 API ----

    def create_folder(self, parent_fid: str, folder_name: str) -> dict[str, Any]:
        """在个人网盘中创建文件夹。"""
        url = f"{QUARK_BASE_URL}/1/clouddrive/file"
        payload = {
            "pdir_fid": parent_fid,
            "file_name": folder_name,
            "dir_init_lock": False,
        }
        result = self._post(url, payload)
        if result.get("code") == 0:
            data = result.get("data", {})
            return {"success": True, "fid": data.get("fid", ""), "file_name": folder_name}
        # 处理文件夹已存在的情况（code=41012）
        if result.get("code") == 41012 or "already exist" in str(result.get("message", "")):
            # 文件夹已存在，需要查找其fid
            existing = self._find_folder_fid(parent_fid, folder_name)
            if existing:
                return {"success": True, "fid": existing, "file_name": folder_name, "already_exists": True}
            return {"success": True, "fid": "", "file_name": folder_name, "already_exists": True}
        return {"success": False, "error": result.get("message", "创建文件夹失败"), "raw": result}

    def list_files(self, parent_fid: str = "0", page: int = 1, page_size: int = 50) -> dict[str, Any]:
        """列出个人网盘目录下的文件。"""
        url = f"{QUARK_BASE_URL}/1/clouddrive/file/sort"
        params = {
            **QUARK_BASE_PARAMS,
            "pdir_fid": parent_fid,
            "_page": str(page),
            "_size": str(page_size),
            "_fetch_total": "1",
            "_fetch_sub_dirs": "0",
            "_sort": "file_type:asc,updated_at:desc",
        }
        result = self._get(url, params)
        if result.get("code") == 0:
            data = result.get("data", {})
            items = data.get("list", [])
            total = data.get("metadata", {}).get("_total", len(items))
            return {"items": items, "total": total}
        return {"items": [], "total": 0}

    def get_account_info(self) -> dict[str, Any]:
        """获取账户信息（验证Cookie有效性）。"""
        url = "https://pan.quark.cn/account/info"
        params = {"fr": "pc", "platform": "pc"}
        result = self._get(url, params, use_base_url=False)
        if result.get("code") == 0 or result.get("status") == 200:
            data = result.get("data", result)
            return {
                "success": True,
                "nickname": data.get("nickname", ""),
                "user_id": data.get("user_id", ""),
            }
        return {"success": False, "error": result.get("message", "Cookie无效或已过期")}

    # ---- 辅助方法 ----

    def _find_folder_fid(self, parent_fid: str, folder_name: str) -> str | None:
        """在指定目录中查找同名文件夹的fid。"""
        result = self.list_files(parent_fid)
        for item in result.get("items", []):
            if item.get("file_name") == folder_name and item.get("file_type") == "folder":
                return item.get("fid", "")
        return None

    def _poll_task(self, task_id: str, max_retries: int = 30, interval: float = 1.0) -> dict[str, Any] | None:
        """轮询任务状态直到完成。"""
        url = f"{QUARK_BASE_URL}/1/clouddrive/task"
        for i in range(max_retries):
            params = {**QUARK_BASE_PARAMS, "task_id": task_id, "retry_index": i}
            result = self._get(url, params)
            if result.get("code") == 0:
                data = result.get("data", {})
                status = data.get("status")
                if status == 2:
                    return data
                elif status == 3:
                    return None
            time.sleep(interval)
        return None

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """发送POST请求（使用 core.services.resource_http）。"""
        try:
            from core.services.resource_http import fetch_json
            return fetch_json(url, method="POST", json_body=payload, headers=self.headers)
        except ImportError:
            # 备用方案：使用 requests（独立测试时）
            import json as _json
            try:
                import requests
                resp = requests.post(url, json=payload, headers=self.headers, params=QUARK_BASE_PARAMS, timeout=30)
                return resp.json()
            except Exception:
                return {"code": -1, "message": "请求失败：缺少 requests 库和 core.services.resource_http"}

    def _get(self, url: str, params: dict[str, Any], use_base_url: bool = True) -> dict[str, Any]:
        """发送GET请求。"""
        try:
            from core.services.resource_http import fetch_json
            return fetch_json(url, params=params, headers=self.headers)
        except ImportError:
            import json as _json
            try:
                import requests
                resp = requests.get(url, params=params, headers=self.headers, timeout=30)
                return resp.json()
            except Exception:
                return {"code": -1, "message": "请求失败：缺少 requests 库和 core.services.resource_http"}

    @staticmethod
    def extract_pwd_id(share_url: str) -> str:
        """从分享链接中提取 pwd_id。"""
        match = re.search(r"/s/([A-Za-z0-9_-]+)", share_url)
        return match.group(1) if match else ""

    @staticmethod
    def extract_passcode(share_url: str) -> str:
        """从分享链接中提取密码（如果有）。"""
        # 密码通常不在URL中，而是单独提供
        return ""


# ---------------------------------------------------------------------------
# 夸克网盘集数过滤 DriveProvider 插件
# ---------------------------------------------------------------------------

class QuarkFilterDrivePlugin(BasePlugin):
    plugin_id = "drive.quark"
    plugin_name = "夸克网盘"
    plugin_version = "1.0.0"

    def __init__(self) -> None:
        self._runtime_config: dict[str, Any] = {}
        self._scan_sessions: dict[str, dict[str, Any]] = {}

    def set_runtime_config(self, config: dict[str, Any]) -> None:
        self._runtime_config = dict(config or {})

    def health(self, ctx: dict[str, Any]) -> HealthReport:
        cookie = str(self._runtime_config.get("cookie") or "").strip()
        if cookie:
            api = QuarkAPI(cookie)
            info = api.get_account_info()
            if info.get("success"):
                return HealthReport(status="ok", message=f"夸克账号已连接：{info.get('nickname', '未知用户')}")
            return HealthReport(status="degraded", message="Cookie无效或已过期，请重新获取。")
        return HealthReport(status="degraded", message="未配置夸克Cookie。请在插件设置中填写Cookie。")

    # ---- DriveProvider 协议方法 ----

    def get_contract(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "cloud_type": "quark",
            "display_name": "夸克网盘",
            "account_mode": "user",
            "capabilities": ["drive.account", "drive.fs", "drive.share"],
            "account_form_schema": [
                {
                    "key": "cookie",
                    "label": "夸克Cookie",
                    "type": "string",
                    "required": False,
                    "default": "",
                    "description": "登录夸克网盘网页版后从浏览器获取的完整Cookie。如使用扫码登录可不填。",
                    "secret": True,
                }
            ],
            "supported_auth_types": ["cookie", "qrcode", "desktop"],
            "supported_actions": {
                "account": ["test", "scan_start", "scan_status", "scan_cancel", "desktop_get"],
                "fs": ["list", "mkdir"],
                "share": ["parse", "browse", "save"],
                "file": [],
            },
            "share_url_patterns": ["https://pan.quark.cn/s/"],
        }

    def get_account_form_schema(self) -> list[dict[str, Any]]:
        return self.get_contract()["account_form_schema"]

    def test_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        cookie = str(payload.get("cookie") or "").strip()
        if not cookie:
            return {"success": False, "message": "Cookie不能为空"}
        api = QuarkAPI(cookie)
        info = api.get_account_info()
        if info.get("success"):
            return {"success": True, "message": f"账号验证成功：{info.get('nickname', '')}"}
        return {"success": False, "message": info.get("error", "Cookie无效")}

    # ---- 扫码登录相关方法 ----

    def qrcode_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        """发起扫码登录，获取二维码。"""
        result = QuarkAPI.start_qr_login()
        return result

    def qrcode_check(self, payload: dict[str, Any]) -> dict[str, Any]:
        """轮询扫码状态。"""
        token = str(payload.get("token") or "").strip()
        request_id = str(payload.get("request_id") or "").strip()
        if not token:
            return {"status": "error", "error": "缺少token参数"}

        result = QuarkAPI.check_qrcode_status(token, request_id)

        if result.get("status") == "confirmed":
            ticket = result.get("ticket", "")
            if ticket:
                cookie_result = QuarkAPI.ticket_to_cookie(ticket)
                if cookie_result.get("success"):
                    return {
                        "status": "confirmed",
                        "cookie": cookie_result.get("cookie", ""),
                        "nickname": cookie_result.get("nickname", ""),
                        "user_id": cookie_result.get("user_id", ""),
                    }
                return {
                    "status": "confirmed",
                    "ticket": ticket,
                    "note": "扫码成功，请使用ticket换取Cookie",
                }
        return result

    def desktop_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        """从本地夸克桌面客户端获取Cookie。"""
        result = QuarkAPI.get_cookie_from_desktop()
        return result

    def create_account_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"cookie": str(payload.get("cookie") or "").strip()}

    def get_account_info(self, account_ref: dict[str, Any]) -> dict[str, Any]:
        cookie = str(account_ref.get("cookie") or "").strip()
        api = QuarkAPI(cookie)
        info = api.get_account_info()
        return {
            "account_id": info.get("user_id", "quark-filter-user"),
            "plugin_id": self.plugin_id,
            "cloud_type": "quark",
            "display_name": "夸克网盘",
            "status": "ok" if info.get("success") else "error",
            "nickname": info.get("nickname", ""),
        }

    def refresh_account(self, account_ref: dict[str, Any]) -> dict[str, Any]:
        return self.get_account_info(account_ref)

    # ---- 分享相关 ----

    def parse_share(self, account_ref: dict[str, Any], share_ref: dict[str, Any]) -> dict[str, Any]:
        """解析夸克分享链接。"""
        share_url = str(share_ref.get("share_url") or "").strip()
        passcode = str(share_ref.get("password") or "").strip()
        pwd_id = QuarkAPI.extract_pwd_id(share_url)

        if not pwd_id:
            return {"error": "无法从URL中提取分享ID", "share_url": share_url}

        cookie = str(account_ref.get("cookie") or "").strip()
        api = QuarkAPI(cookie)
        stoken_result = api.get_stoken(pwd_id, passcode)

        if "error" in stoken_result:
            return {
                "error": stoken_result.get("error"),
                "share_id": pwd_id,
                "share_url": share_url,
            }

        return {
            "share_id": pwd_id,
            "share_name": "",
            "share_url": share_url,
            "normalized_url": f"https://pan.quark.cn/s/{pwd_id}",
            "can_save": True,
            "root_id": "0",
            "stoken": stoken_result.get("stoken", ""),
            "passcode": passcode,
        }

    def browse_share(
        self,
        account_ref: dict[str, Any],
        share_ref: dict[str, Any],
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """浏览分享目录，返回带有集数元数据的文件列表。"""
        cookie = str(account_ref.get("cookie") or "").strip()
        share_url = str(share_ref.get("share_url") or "").strip()
        passcode = str(share_ref.get("password") or share_ref.get("passcode") or "").strip()
        stoken = str(share_ref.get("stoken") or "").strip()
        pwd_id = QuarkAPI.extract_pwd_id(share_url) or str(share_ref.get("share_id") or "")

        api = QuarkAPI(cookie)

        # 如果没有stoken，先获取
        if not stoken:
            stoken_result = api.get_stoken(pwd_id, passcode)
            if "error" in stoken_result:
                return {"items": [], "total": 0, "error": stoken_result.get("error")}
            stoken = stoken_result.get("stoken", "")

        pdir_fid = parent_id or share_ref.get("root_id") or "0"
        browse_result = api.browse_share(pwd_id, stoken, pdir_fid=pdir_fid)

        if "error" in browse_result:
            return {"items": [], "total": 0, "error": browse_result.get("error")}

        # 添加集数元数据到每个文件
        parser = self._get_parser()
        items = browse_result.get("items", [])
        for item in items:
            name = item.get("file_name", "")
            item["episode_number"] = parser.parse_episode(name)
            item["is_video"] = parser.is_video(name)

        return {
            "items": items,
            "total": browse_result.get("total", len(items)),
            "parent_id": pdir_fid,
            "path_nodes": [],
        }

    def save_share(
        self,
        account_ref: dict[str, Any],
        share_ref: dict[str, Any],
        target_parent_id: str,
        selected_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        转存分享文件到个人网盘，自动按集数过滤。

        核心流程：
        1. 浏览分享获取所有文件
        2. 解析集数，按过滤模式分组（选中组 / 其他组）
        3. 选中组保存到 target_parent_id（根目录）
        4. 其他组保存到子文件夹（如果配置开启）
        """
        cookie = str(account_ref.get("cookie") or "").strip()
        api = QuarkAPI(cookie)
        parser = self._get_parser()

        # 获取分享信息
        share_url = str(share_ref.get("share_url") or "").strip()
        passcode = str(share_ref.get("password") or share_ref.get("passcode") or "").strip()
        pwd_id = QuarkAPI.extract_pwd_id(share_url) or str(share_ref.get("share_id") or "")
        stoken = str(share_ref.get("stoken") or "").strip()

        if not stoken:
            stoken_result = api.get_stoken(pwd_id, passcode)
            if "error" in stoken_result:
                return {"success": False, "message": stoken_result.get("error")}
            stoken = stoken_result.get("stoken", "")

        # 浏览分享获取所有文件
        browse_result = self.browse_share(account_ref, share_ref)
        all_items = browse_result.get("items", [])
        if not all_items:
            return {"success": False, "message": "分享中没有文件"}

        # 解析集数并分类
        filter_mode = str(self._runtime_config.get("default_filter_mode") or "all").strip()

        # 如果用户手动选择了文件（selected_items不为空），检查是否要覆盖
        if selected_items is not None and len(selected_items) > 0:
            # 用户手动选择了文件，尊重用户选择，不做过滤
            # 但仍然会为选中文件添加集数信息
            selected_fids = [str(item.get("fid") or item.get("id") or "") for item in selected_items]
            selected_tokens = [str(item.get("share_fid_token") or item.get("fid_token") or "") for item in selected_items]

            if not selected_fids:
                # 使用 selected_items 中的完整信息来构造
                selected_fids = []
                selected_tokens = []
                for sel in selected_items:
                    fid = str(sel.get("fid") or sel.get("id") or "")
                    token = str(sel.get("share_fid_token") or sel.get("fid_token") or "")
                    if fid:
                        selected_fids.append(fid)
                        if token:
                            selected_tokens.append(token)

            if not selected_fids:
                return {"success": False, "message": "选中的文件没有有效的文件ID"}

            save_result = api.save_share_files(
                pwd_id=pwd_id,
                stoken=stoken,
                fid_list=selected_fids,
                fid_token_list=selected_tokens,
                to_pdir_fid=target_parent_id,
            )
            return {
                "success": save_result.get("success", False),
                "message": "已转存用户选中的文件",
                "saved_count": len(selected_fids),
            }

        # 没有手动选择 → 应用集数过滤
        video_items = []
        non_video_items = []
        for item in all_items:
            if item.get("is_video", parser.is_video(item.get("file_name", ""))):
                video_items.append(item)
            elif item.get("file_type") == "folder":
                # 文件夹类型 - 保留但不解析集数
                non_video_items.append(item)
            else:
                non_video_items.append(item)

        # 按集数分组
        selected_group: list[dict[str, Any]] = []
        other_group: list[dict[str, Any]] = []

        if filter_mode == "all":
            selected_group = video_items + non_video_items
        elif filter_mode == "latest_n":
            latest_n = int(self._runtime_config.get("latest_n") or 5)
            episodes = [(item, item.get("episode_number") or parser.parse_episode(item.get("file_name", ""))) for item in video_items]
            known = [(item, ep) for item, ep in episodes if ep is not None]
            unknown = [item for item, ep in episodes if ep is None]
            if known:
                max_ep = max(ep for _, ep in known)
                min_target = max_ep - latest_n + 1
                selected_group = [item for item, ep in known if ep >= min_target]
                other_group = [item for item, ep in known if ep < min_target]
            else:
                selected_group = video_items
            selected_group.extend(unknown)  # 未识别集数的默认保存
            selected_group.extend(non_video_items)  # 非视频文件也保存
        elif filter_mode == "start_from":
            start_ep = int(self._runtime_config.get("start_episode") or 1)
            for item in video_items:
                ep = item.get("episode_number") or parser.parse_episode(item.get("file_name", ""))
                if ep is not None and ep >= start_ep:
                    selected_group.append(item)
                elif ep is None:
                    selected_group.append(item)  # 未识别默认保存
                else:
                    other_group.append(item)
            selected_group.extend(non_video_items)
        elif filter_mode == "exclude":
            excluded_set = set(parser.parse_episode_list(str(self._runtime_config.get("excluded_episodes") or "")))
            for item in video_items:
                ep = item.get("episode_number") or parser.parse_episode(item.get("file_name", ""))
                if ep is not None and ep in excluded_set:
                    other_group.append(item)
                else:
                    selected_group.append(item)
            selected_group.extend(non_video_items)
        elif filter_mode == "include_only":
            included_set = set(parser.parse_episode_list(str(self._runtime_config.get("included_episodes") or "")))
            for item in video_items:
                ep = item.get("episode_number") or parser.parse_episode(item.get("file_name", ""))
                if ep is not None and ep in included_set:
                    selected_group.append(item)
                elif ep is None:
                    other_group.append(item)  # 未识别不保存
                else:
                    other_group.append(item)
            selected_group.extend(non_video_items)  # 非视频文件保存到选中组
        else:
            selected_group = video_items + non_video_items

        if not selected_group and not other_group:
            return {"success": False, "message": "没有可转存的文件"}

        # ---- 执行转存 ----
        logs: list[str] = []

        # 步骤1：保存选中组到 target_parent_id
        selected_fids = []
        selected_tokens = []
        for item in selected_group:
            fid = str(item.get("fid") or "")
            token = str(item.get("share_fid_token") or "")
            if fid:
                selected_fids.append(fid)
                if token:
                    selected_tokens.append(token)

        total_saved = 0

        if selected_fids:
            save_result = api.save_share_files(
                pwd_id=pwd_id,
                stoken=stoken,
                fid_list=selected_fids,
                fid_token_list=selected_tokens,
                to_pdir_fid=target_parent_id,
            )
            if save_result.get("success"):
                total_saved += len(selected_fids)
                logs.append(f"选中组转存成功：{len(selected_fids)} 个文件保存到根目录")
            else:
                logs.append(f"选中组转存失败：{save_result.get('error', '未知错误')}")

        # 步骤2：保存其他组到子文件夹（如果配置开启）
        save_others = bool(self._runtime_config.get("save_others_to_subfolder", True))
        other_saved = 0

        if save_others and other_group:
            subfolder_name = str(self._runtime_config.get("subfolder_name") or "其他集数").strip()
            # 创建子文件夹
            mkdir_result = api.create_folder(target_parent_id, subfolder_name)
            if mkdir_result.get("success"):
                subfolder_fid = mkdir_result.get("fid", "")
                if not subfolder_fid:
                    # 查找已存在的文件夹
                    subfolder_fid = api._find_folder_fid(target_parent_id, subfolder_name) or ""
                if subfolder_fid:
                    other_fids = []
                    other_tokens = []
                    for item in other_group:
                        fid = str(item.get("fid") or "")
                        token = str(item.get("share_fid_token") or "")
                        if fid:
                            other_fids.append(fid)
                            if token:
                                other_tokens.append(token)
                    if other_fids:
                        other_save_result = api.save_share_files(
                            pwd_id=pwd_id,
                            stoken=stoken,
                            fid_list=other_fids,
                            fid_token_list=other_tokens,
                            to_pdir_fid=subfolder_fid,
                        )
                        if other_save_result.get("success"):
                            other_saved = len(other_fids)
                            logs.append(f"其他组转存成功：{len(other_fids)} 个文件保存到 '{subfolder_name}' 子文件夹")
                        else:
                            logs.append(f"其他组转存失败：{other_save_result.get('error', '未知错误')}")
                else:
                    logs.append("无法获取子文件夹ID，其他组文件未保存")
            else:
                logs.append(f"创建子文件夹失败：{mkdir_result.get('error', '未知错误')}，其他组文件未保存")

        summary = f"转存完成：根目录 {total_saved} 个文件"
        if other_saved:
            summary += f"，子文件夹 {other_saved} 个文件"
        if other_group and not other_saved and save_others:
            summary += f"，{len(other_group)} 个其他集数文件未成功保存到子文件夹"

        return {
            "success": total_saved > 0 or other_saved > 0,
            "message": summary,
            "saved_count": total_saved + other_saved,
            "root_saved_count": total_saved,
            "subfolder_saved_count": other_saved,
            "filter_mode": filter_mode,
            "selected_episode_count": len(selected_group),
            "other_episode_count": len(other_group),
            "logs": logs,
        }

    # ---- 文件系统 ----

    def list_files(self, account_ref: dict[str, Any], parent_id: str, page: int, page_size: int) -> dict[str, Any]:
        cookie = str(account_ref.get("cookie") or "").strip()
        api = QuarkAPI(cookie)
        result = api.list_files(parent_id, page, page_size)
        items = []
        for raw_item in result.get("items", []):
            items.append({
                "id": raw_item.get("fid", ""),
                "name": raw_item.get("file_name", ""),
                "type": "folder" if raw_item.get("file_type") == "folder" else "file",
                "parent_id": parent_id,
                "size": raw_item.get("size", 0),
            })
        return {
            "items": items,
            "total": result.get("total", len(items)),
            "parent_id": parent_id,
            "path_nodes": [],
        }

    def get_item(self, account_ref: dict[str, Any], item_id: str) -> dict[str, Any]:
        return {"id": item_id, "name": "（请使用list_files浏览）", "type": "unknown", "parent_id": "0"}

    def mkdir(self, account_ref: dict[str, Any], parent_id: str, name: str) -> dict[str, Any]:
        cookie = str(account_ref.get("cookie") or "").strip()
        api = QuarkAPI(cookie)
        result = api.create_folder(parent_id, name)
        return {"success": result.get("success", False), "item_id": result.get("fid", ""), "name": name}

    def rename(self, account_ref: dict[str, Any], item_id: str, new_name: str) -> dict[str, Any]:
        return {"success": False, "message": "rename暂未实现，请使用夸克官方客户端操作"}

    def delete(self, account_ref: dict[str, Any], item_ids: list[str]) -> dict[str, Any]:
        return {"success": False, "message": "delete暂未实现，请使用夸克官方客户端操作"}

    def upload(self, account_ref: dict[str, Any], parent_id: str, file_name: str, file_path: str = "") -> dict[str, Any]:
        return {"success": False, "message": "upload暂未实现，请使用夸克官方客户端操作"}

    def move(self, account_ref: dict[str, Any], item_ids: list[str], target_parent_id: str) -> dict[str, Any]:
        return {"success": False, "message": "move暂未实现，请使用夸克官方客户端操作"}

    def copy(self, account_ref: dict[str, Any], item_ids: list[str], target_parent_id: str) -> dict[str, Any]:
        return {"success": False, "message": "copy暂未实现，请使用夸克官方客户端操作"}

    # ---- 扫码登录（标准协议方法） ----

    def start_scan_login(self) -> dict[str, Any]:
        """发起扫码登录，获取二维码（标准协议方法）。"""
        result = QuarkAPI.start_qr_login()
        if not result.get("success"):
            return result

        token = result.get("token", "")
        request_id = result.get("request_id", "")
        # 生成scan_id并存储会话
        scan_id = f"qr_{token}_{int(time.time())}"
        self._scan_sessions[scan_id] = {
            "token": token,
            "request_id": request_id,
            "status": "pending",
            "created_at": time.time(),
        }

        return {
            "success": True,
            "scan_id": scan_id,
            "qrcode_url": result.get("qrcode_url", ""),
            "qrcode_content": result.get("qrcode_content", ""),
            "message": "请使用夸克APP扫描二维码登录",
        }

    def get_scan_status(self, scan_id: str) -> dict[str, Any]:
        """轮询扫码状态（标准协议方法）。"""
        session = self._scan_sessions.get(scan_id)
        if not session:
            return {"success": False, "status": "expired", "message": "扫码会话不存在或已过期"}

        token = session.get("token", "")
        request_id = session.get("request_id", "")

        result = QuarkAPI.check_qrcode_status(token, request_id)
        status = result.get("status", "")

        if status == "confirmed":
            ticket = result.get("ticket", "")
            if ticket:
                cookie_result = QuarkAPI.ticket_to_cookie(ticket)
                if cookie_result.get("success"):
                    # 清理会话
                    self._scan_sessions.pop(scan_id, None)
                    return {
                        "success": True,
                        "status": "confirmed",
                        "account_payload": {
                            "cookie": cookie_result.get("cookie", ""),
                        },
                        "account_info": {
                            "nickname": cookie_result.get("nickname", ""),
                            "user_id": cookie_result.get("user_id", ""),
                        },
                        "message": "登录成功",
                    }
            # 有ticket但换取cookie失败的情况
            self._scan_sessions.pop(scan_id, None)
            return {
                "success": False,
                "status": "error",
                "message": "扫码成功但换取凭证失败",
            }
        elif status == "expired":
            self._scan_sessions.pop(scan_id, None)
            return {"success": False, "status": "expired", "message": "二维码已过期，请重新扫描"}

        return {
            "success": True,
            "status": status,  # waiting / confirmed / expired
            "message": result.get("message", "等待扫码..."),
        }

    def cancel_scan_login(self, scan_id: str) -> dict[str, Any]:
        """取消扫码登录（标准协议方法）。"""
        self._scan_sessions.pop(scan_id, None)
        return {"success": True, "message": "已取消扫码登录"}

    def create_share(self, account_ref: dict[str, Any], item_ids: list[str], options: dict[str, Any]) -> dict[str, Any]:
        return {"success": False, "message": "创建分享暂未实现"}

    def get_download_link(self, account_ref: dict[str, Any], item_id: str) -> dict[str, Any]:
        return {"success": False, "message": "下载链接请使用夸克官方客户端获取"}

    def get_supported_actions(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "account": ["test", "scan_start", "scan_status", "scan_cancel", "desktop_get"],
            "fs": ["list", "get", "mkdir", "rename", "move", "copy", "delete", "upload"],
            "share": ["parse", "browse", "save", "create"],
            "file": ["download"],
        }

    # ---- 内部辅助 ----

    def _get_parser(self) -> EpisodeParser:
        custom_regex = str(self._runtime_config.get("episode_regex") or "").strip()
        video_ext_str = str(self._runtime_config.get("video_extensions") or "").strip()
        video_extensions = [ext.strip() for ext in video_ext_str.split(",") if ext.strip()] if video_ext_str else None
        return EpisodeParser(custom_regex=custom_regex, video_extensions=video_extensions)


plugin = QuarkFilterDrivePlugin()
