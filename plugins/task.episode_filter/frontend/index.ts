/**
 * task.episode_filter - 网盘下载集数过滤器
 * 前端扩展入口
 *
 * 本文件为可选的前端扩展，主要提供：
 * 1. 任务创建时的集数预览面板注册
 * 2. 自定义文件列表选择器（用于手动标记屏蔽文件/文件夹）
 */

export interface EpisodeFilterConfig {
  filter_mode: string;
  start_episode: number;
  latest_n: number;
  excluded_episodes: string;
  included_episodes: string;
  excluded_item_ids: string;
  episode_regex: string;
  include_non_video: boolean;
  auto_detect_folders: boolean;
  share_url: string;
  drive_type: string;
  share_password: string;
}

export interface DriveFileItem {
  id: string;
  name: string;
  type: "file" | "folder";
  size?: number;
  parent_id?: string;
  episode?: number | null;
}

export interface FilterResult {
  selected: DriveFileItem[];
  skipped: DriveFileItem[];
  no_episode: DriveFileItem[];
  non_video: DriveFileItem[];
  folders: DriveFileItem[];
  total_files: number;
  total_episodes: number;
  max_episode: number | null;
  min_episode: number | null;
  selected_count: number;
  skipped_count: number;
  logs: string[];
}

/** 过滤模式选项 */
export const FILTER_MODE_OPTIONS = [
  { value: "all", label: "全部下载" },
  { value: "start_from", label: "从指定集数开始下载" },
  { value: "latest_n", label: "只下载最新N集" },
  { value: "exclude", label: "屏蔽指定集数" },
  { value: "include_only", label: "仅下载指定集数" },
  { value: "exclude_items", label: "屏蔽指定文件/文件夹" },
];

/** 根据模式获取默认配置 */
export function getDefaultConfig(mode: string): Partial<EpisodeFilterConfig> {
  const base: Partial<EpisodeFilterConfig> = {
    filter_mode: mode,
    episode_regex: "",
    include_non_video: false,
    auto_detect_folders: true,
  };

  switch (mode) {
    case "start_from":
      return { ...base, start_episode: 1 };
    case "latest_n":
      return { ...base, latest_n: 5 };
    case "exclude":
      return { ...base, excluded_episodes: "" };
    case "include_only":
      return { ...base, included_episodes: "" };
    case "exclude_items":
      return { ...base, excluded_item_ids: "" };
    default:
      return base;
  }
}

/** 验证配置 */
export function validateConfig(config: Partial<EpisodeFilterConfig>): string[] {
  const errors: string[] = [];
  const mode = config.filter_mode || "all";

  if (!FILTER_MODE_OPTIONS.find((o) => o.value === mode)) {
    errors.push("过滤模式无效");
  }

  if (mode === "start_from") {
    if (!config.start_episode || config.start_episode < 1) {
      errors.push("起始集数必须 >= 1");
    }
  }

  if (mode === "latest_n") {
    if (!config.latest_n || config.latest_n < 1) {
      errors.push("最新N集的 N 必须 >= 1");
    }
  }

  if (mode === "exclude" && !config.excluded_episodes?.trim()) {
    errors.push("屏蔽模式需要填写屏蔽集数列表");
  }

  if (mode === "include_only" && !config.included_episodes?.trim()) {
    errors.push("仅下载模式需要填写指定集数列表");
  }

  return errors;
}

/** 将过滤结果格式化为可展示的摘要文本 */
export function formatFilterSummary(result: FilterResult): string {
  const parts: string[] = [];
  parts.push(`共 ${result.total_files} 个文件`);
  parts.push(`识别 ${result.total_episodes} 集`);
  if (result.max_episode !== null && result.min_episode !== null) {
    parts.push(`范围 ${result.min_episode}~${result.max_episode}`);
  }
  parts.push(`选中 ${result.selected_count} 个`);
  parts.push(`屏蔽 ${result.skipped_count} 个`);
  if (result.no_episode.length > 0) {
    parts.push(`未识别 ${result.no_episode.length} 个`);
  }
  return parts.join("，");
}

/** 插件前端注册信息 */
export default {
  plugin_id: "task.episode_filter",
  name: "网盘下载集数过滤器",
  version: "1.0.0",
  components: {
    // 任务创建表单的额外面板
    taskFormPanel: "EpisodeFilterFormPanel",
    // 文件选择器（用于手动标记屏蔽项）
    fileSelector: "EpisodeFilterFileSelector",
    // 过滤结果预览
    resultPreview: "EpisodeFilterResultPreview",
  },
};
