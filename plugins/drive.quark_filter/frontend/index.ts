/**
 * 夸克网盘集数过滤插件 - 前端扩展入口
 *
 * 此文件为可选的前端扩展，用于在平台UI中增强插件的交互体验。
 * 当前版本提供基础类型定义，后续可扩展为：
 * - 转存预览面板（显示集数解析结果和过滤建议）
 * - 集数选择器组件（可视化选择要转存的集数）
 */

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
