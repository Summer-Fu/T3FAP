# 网盘下载集数过滤器（task.episode_filter）

针对网盘下载任务，支持按起始集数过滤、屏蔽指定集数/文件夹、只下载最新 N 集等灵活的集数筛选策略。所有订阅下载均可经过此插件过滤后再执行。

## 功能概览

| 过滤模式 | 说明 | 关键配置 |
| --- | --- | --- |
| `all` 全部下载 | 不过滤，下载所有视频文件 | — |
| `start_from` 从指定集数开始 | 只下载集数 >= N 的文件 | `start_episode` |
| `latest_n` 只下载最新 N 集 | 先扫描全部视频文件找到最大集数，再选最新 N 集 | `latest_n`（手动输入数字） |
| `exclude` 屏蔽指定集数 | 跳过指定集数的文件 | `excluded_episodes` |
| `include_only` 仅下载指定集数 | 只下载指定集数的文件 | `included_episodes` |
| `exclude_items` 屏蔽文件/文件夹 | 按网盘文件/文件夹 ID 屏蔽 | `excluded_item_ids` |

## 核心特性

### 1. 智能集数识别

内置多模式正则匹配引擎，支持以下常见命名格式：

- `第01集` / `第1集` / `第一集`（中文数字自动转换）
- `EP01` / `E01` / `ep.01` / `e.01`
- `Episode 01`
- `01话` / `01話` / `01期`
- `[01]` / `【01】`
- `- 01 -` / `_01_`
- 文件名末尾纯数字 `xxx.01.mp4`

同时支持通过 `episode_regex` 配置自定义正则表达式，第一个捕获组将作为集数。

### 2. 最新 N 集下载

这是本插件的核心功能之一。执行流程：

1. 扫描网盘目录下的**所有**视频文件
2. 逐一解析集数
3. 找到最大集数 `max_ep`
4. 计算下载范围：`max_ep - N + 1` 到 `max_ep`
5. 只选中该范围内的文件

例如：一个剧集共有 24 集，设置 `latest_n = 5`，则只下载第 20~24 集。

N 值由用户手动输入数字（integer 类型，最小值 1，默认值 5）。

### 3. 手动屏蔽文件/文件夹

通过 `exclude_items` 模式或 `excluded_item_ids` 配置项，可以手动指定要屏蔽的网盘文件或文件夹 ID。使用流程：

1. 浏览网盘内容
2. 选择不需要下载的文件或文件夹
3. 将其 ID 记录到 `excluded_item_ids`（逗号分隔）
4. 执行过滤，被标记的项将被跳过

### 4. 集数范围语法

`excluded_episodes` 和 `included_episodes` 支持灵活的范围语法：

```
1-5        → 第 1, 2, 3, 4, 5 集
1,3,5      → 第 1, 3, 5 集
1-3,5,8-10 → 第 1, 2, 3, 5, 8, 9, 10 集
```

## 目录结构

```text
plugins/
  task.episode_filter/
    plugin.json          # 插件清单
    backend/
      plugin.py          # 后端入口（集数解析 + 过滤逻辑）
      test_filter.py     # 独立烟测脚本（29 项全通过）
    frontend/
      index.ts           # 前端扩展（配置校验 + 结果格式化）
    README.md            # 本文件
```

## 与平台接口的对照（core.sdk）

本插件使用以下 core.sdk 接口，全部符合官方 TaskTypeProvider 协议：

| 接口 | 用途 | 在其他插件中的参照 |
| --- | --- | --- |
| `BasePlugin` | 插件基类，提供生命周期（install/enable/disable/health） | 所有官方插件均继承（catalog.quark, automation.webhook, search.pansou 等） |
| `TaskTypeProvider` | 任务类型协议，定义 5 个必选方法 | 参照 minimal-task-plugin.md 示例 |
| `TaskTemplate` | 任务模板数据类（type_key/template_key/form_schema 等） | 参照 minimal-task-plugin.md 示例 |
| `OperationResult` | 操作结果类（success/message/errors/data） | automation.webhook 的 handle() 也使用此类型 |
| `TaskExecutionResult` | 执行结果类（success/status/summary/artifacts/logs） | 参照 minimal-task-plugin.md 示例 |

### 未使用但相关的重要接口

以下接口在插件体系中存在，但**task 插件无权直接调用**（只能在对应类型的插件内部使用）：

| 接口 | 说明 | 为什么不能直接调用 |
| --- | --- | --- |
| `DriveProvider.list_files()` | 网盘文件列表 | DriveProvider 只在 drive.xxx 插件内实现，task 插件无法跨类型调用 |
| `DriveProvider.browse_share()` | 浏览分享文件 | 同上 |
| `DriveProvider.parse_share()` | 解析分享链接 | 同上 |
| `core.services.resource_http` | HTTP 请求服务 | 用于 catalog/search 插件做外部 API 请求，task 插件无需直接调用 |
| `AutomationProvider.handle()` | 事件处理 | automation 插件是事后通知，不能做前置拦截 |

## 与平台的数据链路（关键）

这是插件能否正常工作的核心问题。T3FAP 插件体系中没有跨插件调用机制，所以网盘文件数据需要由**平台编排层**注入到 task 插件的 execution_context 中。

### 数据入口（4 个层级，按优先级）

| 入口 | 来源 | 说明 |
| --- | --- | --- |
| `execution_context["drive_files"]` | 平台注入（最优先） | 平台在执行前调用 DriveProvider.browse_share() 获取文件列表，注入到此处 |
| `execution_context["config"]["files"]` | 手动提供 | 用户在任务表单中直接粘贴的文件数据，或通过 API 传入 |
| `execution_context["input_payload"]["drive_files"]` | 资源触发时平台注入 | 从资源动作创建任务时，平台可能在 input_payload 中携带文件 |
| `input_payload["resource"]["drive_files"]` | 资源对象自带 | ResourceItem 自身携带的文件列表（较少见） |

### 平台编排流程（从资源触发时）

```
1. 用户从资源详情页点击「下载」
   → 触发 task.from_resource 动作
2. 平台调用 create_from_resource(resource)
   → 插件从 resource.links.share 提取 share_url + drive_type
   → 返回任务草稿 {config: {share_url, drive_type, filter_mode, ...}}
3. 平台编排层根据 share_url + drive_type 找到对应 DriveProvider
   → 调用 DriveProvider.parse_share({share_url, password})
   → 调用 DriveProvider.browse_share(account_ref, share_ref)
   → 获取文件列表
4. 平台将文件列表注入 execution_context.drive_files
5. 平台调用 execute(execution_context)
   → 插件从 execution_context.drive_files 提取文件
   → 执行集数过滤
   → 返回 download_list + filter_report
6. 平台根据 download_list 决定下载哪些文件
```

### 手动创建任务时

```
1. 用户在任务表单中填写 share_url + drive_type + 过滤参数
2. 平台编排层解析分享链接获取文件列表
3. 将文件列表注入 execution_context.drive_files
4. 调用 execute(execution_context) 执行过滤
```

### 如果平台未注入文件数据

execute() 会检测到文件列表为空，如果存在 share_url + drive_type 信息，会返回 `status="pending"` 并附带 `drive_ref` artifact，告知平台需要先解析网盘：

```json
{
  "success": false,
  "status": "pending",
  "summary": "等待网盘文件数据：平台需先调用 DriveProvider(quark) 解析分享链接...",
  "artifacts": [
    {
      "type": "drive_ref",
      "value": {
        "share_url": "https://pan.quark.cn/s/abc123",
        "drive_type": "quark",
        "share_password": "xyz",
        "account_ref": {}
      }
    }
  ]
}
```

## 插件配置

### 全局配置（config_schema）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enabled` | boolean | `true` | 是否启用插件 |
| `default_filter_mode` | string | `all` | 新建任务时的默认过滤模式 |
| `video_extensions` | string | `.mp4,.mkv,...` | 视频文件扩展名列表 |
| `custom_episode_regex` | string | `""` | 全局自定义集数正则 |

### 任务配置（form_schema）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `share_url` | string | `""` | 网盘分享链接（从资源创建时自动填充） |
| `drive_type` | select | `""` | 网盘类型（quark/aliyun/baidu/cloud189/115/xunlei，留空自动识别） |
| `share_password` | string | `""` | 分享密码（如有） |
| `filter_mode` | select | `all` | 过滤模式选择 |
| `start_episode` | integer | `1` | 起始集数（start_from 模式时生效） |
| `latest_n` | integer | `5` | 最新 N 集的 N（手动输入数字，latest_n 模式时生效） |
| `excluded_episodes` | string | `""` | 屏蔽集数列表（exclude 模式时生效） |
| `included_episodes` | string | `""` | 指定下载集数（include_only 模式时生效） |
| `excluded_item_ids` | string | `""` | 屏蔽文件/文件夹 ID（exclude_items 模式时生效） |
| `episode_regex` | string | `""` | 自定义集数正则 |
| `include_non_video` | boolean | `false` | 是否包含非视频文件 |
| `auto_detect_folders` | boolean | `true` | 自动识别文件夹 |

## 输出格式

`execute()` 返回 `TaskExecutionResult`，包含两个 artifact：

### download_list（下载清单）

```json
[
  { "id": "f002", "name": "剧集名.E02.1080p.mp4", "episode": 2, "size": 1073741824, "parent_id": "root", "type": "file" },
  { "id": "f003", "name": "剧集名第03集.mkv", "episode": 3, "size": 536870912, "parent_id": "root", "type": "file" }
]
```

### filter_report（完整过滤报告）

包含 `selected`（选中）、`skipped`（屏蔽）、`no_episode`（未识别）、`non_video`（非视频）、`folders`（文件夹）等完整信息，以及执行日志。

## 集数识别示例

| 文件名 | 解析结果 |
| --- | --- |
| `我的剧集.E01.1080p.WEB-DL.mp4` | 1 |
| `我的剧集_EP05_4K.mkv` | 5 |
| `某剧第12集.rmvb` | 12 |
| `Show.Name.S01E03.x264.mp4` | 3 |
| `Anime[07].mkv` | 7 |
| `剧集.24.mp4` | 24 |
| `第一集.mp4` | 1 |
| `第十集.mp4` | 10 |

## 开发说明

- 后端基于 `BasePlugin + TaskTypeProvider` 协议（5 个必选方法全部实现）
- 集数解析引擎在 `EpisodeParser` 类中，可独立测试
- 过滤逻辑在 `_filter_files()` 方法中，按模式分支处理
- 数据入口在 `_extract_files_from_context()` 中，按 4 个层级优先提取
- 所有方法均有错误兜底，不会静默失败
- `dry_run()` 可用于预览过滤结果而不执行下载
- `create_from_resource()` 从 ResourceItem.links.share 提取分享链接信息
- `execute()` 无文件数据但有分享链接时，返回 pending 状态 + drive_ref artifact

## 版本

- **1.0.0** — 初始版本
  - 六种过滤模式
  - 多模式集数识别引擎
  - 最新 N 集智能检测（手动输入数字）
  - 手动屏蔽文件/文件夹
  - dry_run 预览支持
  - 分享链接数据桥接（share_url / drive_type / share_password）
  - 4 层级数据入口（drive_files 优先）
  - pending 状态返回（平台未注入文件时）
