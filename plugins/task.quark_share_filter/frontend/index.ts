/**
 * 夸克分享过滤转存任务插件类型定义
 */

export interface QuarkShareFilterConfig {
  share_url: string;
  share_password?: string;
  target_parent_id?: string;
  latest_n: number;
  others_folder_name?: string;
  drive_account_id?: string;
}

export interface QuarkShareFilterResult {
  share_url: string;
  latest_n: number;
  latest_count: number;
  others_count: number;
  others_folder: string;
  video_count: number;
}
