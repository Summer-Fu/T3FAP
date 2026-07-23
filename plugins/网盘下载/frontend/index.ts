/**
 * 网盘下载集数过滤 - 前端扩展
 *
 * 该扩展提供以下功能：
 * 1. 在任务创建页面增强网盘内容的集数可视化展示
 * 2. 提供集数范围快速选择（快捷按钮：全部、前10集、后10集等）
 * 3. 支持手动勾选/取消勾选具体文件或文件夹
 * 4. 自动解析文件名中的集数并高亮显示
 */

import type {
  TaskPluginExtension,
  TaskFormContext,
  TaskFormField,
} from "@t3fap/sdk";

const PLUGIN_ID = "task.drive_download_filter";
const TEMPLATE_KEY = "drive_download_filter";

/**
 * 从文件名中提取集数
 */
function extractEpisodeNumber(name: string): number | null {
  if (!name) return null;

  const patterns = [
    /[第\s]*(\d{1,4})[集话話期回]/i,
    /\bE?(\d{1,4})\b/i,
    /-(\d{1,4})\s*\[/.
    /\[(\d{1,4})\]/,
    /\.(\d{1,4})\./,
    /_(\d{1,4})_/,
    /\b(\d{1,4})\s*of\s*\d{1,4}\b/i,
    /EP?\s*(\d{1,4})/i,
  ];

  const candidates: number[] = [];
  for (const pattern of patterns) {
    const match = name.match(pattern);
    if (match) {
      for (let i = 1; i < match.length; i++) {
        if (match[i] && /^\d+$/.test(match[i])) {
          candidates.push(parseInt(match[i], 10));
        }
      }
    }
  }

  if (candidates.length === 0) return null;
  if (candidates.length === 1) return candidates[0];
  return Math.min(...candidates);
}

/**
 * 判断文件是否为视频文件
 */
function isVideoFile(name: string): boolean {
  const videoExts = [
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv",
    ".webm", ".m4v", ".ts", ".mpg", ".mpeg", ".rmvb",
    ".rm", ".3gp", ".vob", ".ogv",
  ];
  const ext = name.includes(".")
    ? "." + name.split(".").pop()?.toLowerCase()
    : "";
  return videoExts.includes(ext);
}

/**
 * 解析屏蔽集数字符串
 * 支持格式：3,5,8-12
 */
function parseBlockedEpisodes(raw: string): Set<number> {
  const result = new Set<number>();
  if (!raw?.trim()) return result;

  for (const part of raw.split(",")) {
    const trimmed = part.trim();
    if (!trimmed) continue;

    if (trimmed.includes("-")) {
      const [startStr, endStr] = trimmed.split("-", 2);
      const start = parseInt(startStr.trim(), 10);
      const end = parseInt(endStr.trim(), 10);
      if (!isNaN(start) && !isNaN(end)) {
        const [min, max] = start <= end ? [start, end] : [end, start];
        for (let i = min; i <= max; i++) {
          result.add(i);
        }
      }
    } else {
      const num = parseInt(trimmed, 10);
      if (!isNaN(num)) {
        result.add(num);
      }
    }
  }

  return result;
}

/**
 * 格式化屏蔽集数集合为字符串
 */
function formatBlockedEpisodes(episodes: Set<number>): string {
  if (episodes.size === 0) return "";

  const sorted = Array.from(episodes).sort((a, b) => a - b);
  const parts: string[] = [];
  let i = 0;

  while (i < sorted.length) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) {
      j++;
    }
    if (i === j) {
      parts.push(String(sorted[i]));
    } else {
      parts.push(`${sorted[i]}-${sorted[j]}`);
    }
    i = j + 1;
  }

  return parts.join(",");
}

const extension: TaskPluginExtension = {
  pluginId: PLUGIN_ID,
  templateKey: TEMPLATE_KEY,

  /**
   * 表单字段增强
   * 在标准表单字段基础上添加自定义渲染逻辑
   */
  enhanceFormFields(
    fields: TaskFormField[],
    context: TaskFormContext
  ): TaskFormField[] {
    return fields.map((field) => {
      if (field.key === "drive_items") {
        return {
          ...field,
          renderer: "drive-item-selector",
        };
      }

      if (field.key === "start_episode" || field.key === "end_episode") {
        return {
          ...field,
          renderer: "episode-range-input",
        };
      }

      if (field.key === "blocked_episodes") {
        return {
          ...field,
          renderer: "blocked-episodes-input",
        };
      }

      if (field.key === "blocked_item_ids") {
        return {
          ...field,
          hidden: true,
        };
      }

      return field;
    });
  },

  /**
   * 自定义组件注册
   */
  customComponents: {
    "drive-item-selector": {
      /**
       * 网盘内容选择器
       * 功能：
       * - 显示网盘内容列表
       * - 自动识别并显示集数
       * - 支持勾选/取消勾选
       * - 支持按集数范围批量选择
       */
    },

    "episode-range-input": {
      /**
       * 集数范围输入
       * 提供快捷选择按钮
       */
    },

    "blocked-episodes-input": {
      /**
       * 屏蔽集数输入
       * 支持可视化选择和批量操作
       */
    },
  },

  /**
   * 任务草稿预处理
   * 在用户提交任务前进行数据处理
   */
  beforeSubmit(context: TaskFormContext): TaskFormContext {
    const config = { ...context.config };
    const driveItems = (config.drive_items as Array<Record<string, unknown>>) || [];
    const blockedItemIds = new Set<string>(
      (config.blocked_item_ids as string[]) || []
    );

    const startEpisode = parseInt(String(config.start_episode || "1"), 10);
    const endEpisode = parseInt(String(config.end_episode || "0"), 10);
    const blockedEpisodes = parseBlockedEpisodes(
      String(config.blocked_episodes || "")
    );
    const onlyVideo = Boolean(config.only_video_files ?? true);
    const includeFolders = Boolean(config.include_folders ?? true);

    const autoBlocked = new Set<string>();

    for (const item of driveItems) {
      const name = String(item.name || "");
      const type = String(item.type || "file");
      const itemId = String(item.id || "");

      if (type === "folder" && !includeFolders) {
        autoBlocked.add(itemId);
        continue;
      }

      if (type === "file" && onlyVideo && !isVideoFile(name)) {
        autoBlocked.add(itemId);
        continue;
      }

      const episode = extractEpisodeNumber(name);
      if (episode !== null) {
        if (blockedEpisodes.has(episode)) {
          autoBlocked.add(itemId);
          continue;
        }
        if (episode < startEpisode) {
          autoBlocked.add(itemId);
          continue;
        }
        if (endEpisode > 0 && episode > endEpisode) {
          autoBlocked.add(itemId);
          continue;
        }
      }
    }

    const finalBlocked = new Set([...blockedItemIds, ...autoBlocked]);
    config.blocked_item_ids = Array.from(finalBlocked);

    return {
      ...context,
      config,
    };
  },

  /**
   * 任务执行结果处理
   */
  afterExecute(result: Record<string, unknown>): Record<string, unknown> {
    const artifacts = (result.artifacts as Array<Record<string, unknown>>) || [];
    const keptItems = artifacts.find((a) => a.type === "drive.items")?.value as Array<
      Record<string, unknown>
    > || [];
    const filteredItems = artifacts.find(
      (a) => a.type === "drive.items.filtered"
    )?.value as Array<Record<string, unknown>> || [];

    return {
      ...result,
      summary: `${result.summary || "过滤完成"}（保留 ${keptItems.length} 项，过滤 ${filteredItems.length} 项）`,
    };
  },
};

export default extension;
