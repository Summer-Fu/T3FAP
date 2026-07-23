/**
 * 夸克网盘插件 - 前端扩展入口
 *
 * 此文件为可选的前端扩展，用于在平台UI中增强插件的交互体验。
 * 提供集数过滤转存增强功能，包括：
 * - 转存预览面板（显示集数解析结果和过滤建议）
 * - 集数选择器组件（可视化选择要转存的集数）
 * - 扫码登录面板（提供二维码显示和状态轮询）
 */

// ---- 登录认证相关类型 ----

export type AuthType = "cookie" | "qrcode" | "desktop";

export interface QrCodeStartResult {
  success: boolean;
  token?: string;
  qrcode_url?: string;
  qrcode_content?: string;
  request_id?: string;
  error?: string;
}

export type QrCodeStatus = "waiting" | "scanned" | "confirmed" | "expired" | "cancelled" | "error";

export interface QrCodeCheckResult {
  status: QrCodeStatus;
  ticket?: string;
  cookie?: string;
  nickname?: string;
  user_id?: string;
  error?: string;
  note?: string;
  data?: any;
}

export interface DesktopGetResult {
  success: boolean;
  cookie?: string;
  nickname?: string;
  user_id?: string;
  source?: string;
  note?: string;
  error?: string;
}

// ---- 配置相关类型 ----

export interface QuarkFilterConfig {
  cookie: string;
  default_filter_mode: "all" | "latest_n" | "start_from" | "exclude" | "include_only";
  latest_n: number;
  start_episode: number;
  excluded_episodes: string;
  included_episodes: string;
  save_others_to_subfolder: boolean;
  subfolder_name: string;
  episode_regex: string;
  video_extensions: string;
}

export interface ShareItemWithEpisode {
  fid: string;
  file_name: string;
  file_type: string;
  share_fid_token: string;
  episode_number: number | null;
  is_video: boolean;
  size: number;
}

export interface FilterResult {
  selected_group: ShareItemWithEpisode[];
  other_group: ShareItemWithEpisode[];
  unknown_group: ShareItemWithEpisode[];
  max_episode: number | null;
}

// 默认配置
export const DEFAULT_CONFIG: QuarkFilterConfig = {
  cookie: "",
  default_filter_mode: "all",
  latest_n: 5,
  start_episode: 1,
  excluded_episodes: "",
  included_episodes: "",
  save_others_to_subfolder: true,
  subfolder_name: "其他集数",
  episode_regex: "",
  video_extensions: ".mp4,.mkv,.avi,.rmvb,.ts,.mov,.wmv,.flv,.m4v,.iso",
};

/**
 * 计算最新N集的过滤范围
 */
export function computeLatestNEpisodes(
  items: ShareItemWithEpisode[],
  n: number
): FilterResult {
  const knownEpisodes = items.filter(
    (item) => item.is_video && item.episode_number !== null
  );
  const unknownItems = items.filter(
    (item) => item.is_video && item.episode_number === null
  );
  const nonVideoItems = items.filter((item) => !item.is_video);

  if (knownEpisodes.length === 0) {
    return {
      selected_group: items,
      other_group: [],
      unknown_group: unknownItems,
      max_episode: null,
    };
  }

  const maxEp = Math.max(...knownEpisodes.map((item) => item.episode_number!));
  const minTarget = maxEp - n + 1;

  const selectedGroup = knownEpisodes.filter(
    (item) => item.episode_number! >= minTarget
  );
  const otherGroup = knownEpisodes.filter(
    (item) => item.episode_number! < minTarget
  );

  // 未识别集数的视频和非视频文件归入选中组
  selectedGroup.push(...unknownItems, ...nonVideoItems);

  return {
    selected_group: selectedGroup,
    other_group: otherGroup,
    unknown_group: unknownItems,
    max_episode: maxEp,
  };
}

// ---- 扫码登录辅助函数 ----

/**
 * 扫码登录状态对应的中文提示
 */
export const QRCODE_STATUS_TEXT: Record<QrCodeStatus, string> = {
  waiting: "请使用夸克网盘APP扫描二维码",
  scanned: "扫码成功，请在手机上确认登录",
  confirmed: "登录成功",
  expired: "二维码已过期，请刷新",
  cancelled: "已取消登录",
  error: "登录失败，请重试",
};

/**
 * 轮询扫码状态（可用于React Hooks）
 * @param checkFn 检查状态的回调函数
 * @param onSuccess 登录成功回调
 * @param interval 轮询间隔（毫秒），默认2秒
 * @param maxRetries 最大重试次数，默认90次（约3分钟）
 */
export function pollQrCodeStatus(
  checkFn: () => Promise<QrCodeCheckResult>,
  onSuccess: (result: QrCodeCheckResult) => void,
  interval: number = 2000,
  maxRetries: number = 90
): () => void {
  let retries = 0;
  let stopped = false;

  const tick = async () => {
    if (stopped) return;
    if (retries >= maxRetries) return;

    retries++;
    try {
      const result = await checkFn();
      if (result.status === "confirmed") {
        onSuccess(result);
        return;
      }
      if (result.status === "expired" || result.status === "error") {
        return;
      }
    } catch (e) {
      // 忽略单次轮询错误
    }

    if (!stopped) {
      setTimeout(tick, interval);
    }
  };

  setTimeout(tick, interval);

  return () => {
    stopped = true;
  };
}

/**
 * 从桌面客户端获取Cookie的提示信息
 */
export const DESKTOP_HELP_TEXT = `使用说明：
1. 确保已安装并打开夸克网盘桌面客户端
2. 在桌面客户端中登录您的夸克账号
3. 点击"获取"按钮，系统将自动从本地客户端读取登录凭证

注意：此功能仅在本地运行T3FAP时可用，Docker部署可能无法访问本地端口。`;
