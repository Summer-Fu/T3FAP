# 夸克网盘集数过滤插件（drive.quark_filter）

## 概述

这是一个 **drive 类型插件**，在夸克网盘转存分享时自动按集数过滤。与之前的 task 版本不同，drive 版直接在转存环节（`save_share()`）执行过滤，**不需要平台额外集成**。

核心机制：利用 `DriveProvider.save_share(selected_items)` 接口，插件在转存时先浏览分享解析集数，然后：
- 选中的集数 → 保存到根目录
- 非选中的集数 → 保存到「其他集数」子文件夹（可配置）

## 功能特性

### 六种过滤模式

| 模式 | 说明 | 关键参数 |
|------|------|----------|
| `all` | 全部转存，不过滤 | — |
| `latest_n` | 只转存最新N集（先扫描所有文件找最大集数） | `latest_n`：手动输入数字 |
| `start_from` | 从指定集数开始转存 | `start_episode` |
| `exclude` | 屏蔽指定集数 | `excluded_episodes`（支持 `1-5,8,10-12` 范围语法） |
| `include_only` | 仅转存指定集数 | `included_episodes` |
| 手动选择 | 用户在UI中手动勾选文件 | `selected_items`（平台传入） |

### 最新N集详解

设 `latest_n=5`，网盘内有24集剧集：
1. 插件浏览分享 → 找到所有视频文件
2. 解析集数 → 识别出 1~24 集
3. 最大集数 = 24 → 范围 = 24 - 5 + 1 = 20
4. 选中组：第20~24集 → 保存到根目录
5. 其他组：第1~19集 → 保存到「其他集数」子文件夹

### 集数识别引擎

内置7种正则 + 中文数字转换 + 自定义正则：
- `E01` / `EP01` / `ep.01` / `Episode 01`
- `第01集` / `第1集` / `第一集` / `01话` / `01期`
- `[01]` / `【01】` / `_01_` / `-01-`
- 文件名末尾纯数字 `剧名.24.mp4`

## 安装和使用

### 1. 安装插件

将 `drive.quark_filter` 文件夹放入 T3FAP 的 `plugins/` 目录，重启平台。

### 2. 获取夸克Cookie

1. 打开浏览器，登录 https://pan.quark.cn
2. 按 F12 打开开发者工具
3. 切换到「网络」标签
4. 刷新页面，找到任意请求
5. 在请求头中找到 `Cookie` 字段，复制完整内容
6. 粘贴到插件设置的「夸克Cookie」字段

### 3. 配置过滤模式

在插件设置页面选择默认过滤模式：
- 如果日常追剧只看最新几集 → 选「只转存最新N集」
- 输入 `latest_n` 的数字（如 3、5）

### 4. 转存分享

当你在平台中遇到夸克分享链接时，点击「转存」操作：
- 平台会使用本插件处理转存
- 插件自动按配置的模式过滤集数
- 选中的集数保存到指定目录
- 非选中的集数保存到子文件夹（可配置关闭）

### 重要说明

如果 T3FAP 平台同时有内置的夸克网盘插件，两者可能会冲突（`share_url_patterns` 相同）。解决方法：
- 在 T3FAP 的插件管理中**禁用内置夸克网盘插件**
- **只启用本插件（drive.quark_filter）**
- 本插件会接管所有 `https://pan.quark.cn/s/` 的分享链接处理

## 夸克网盘 API 参考

本插件调用以下夸克官方 API：

| 操作 | API端点 | 方法 |
|------|---------|------|
| 获取分享凭证 | `/1/clouddrive/share/sharepage/token` | POST |
| 浏览分享文件 | `/1/clouddrive/share/sharepage/detail` | GET |
| 转存文件 | `/1/clouddrive/share/sharepage/save` | POST |
| 创建文件夹 | `/1/clouddrive/file` | POST |
| 列出文件 | `/1/clouddrive/file/sort` | GET |
| 账户信息 | `/account/info` | GET |
| 任务轮询 | `/1/clouddrive/task` | GET |

所有请求使用 Cookie 认证，基础URL为 `https://drive-pc.quark.cn`。

## 与 task 版插件的区别

| | task.episode_filter（旧版） | drive.quark_filter（新版） |
|---|---|---|
| 类型 | task | drive |
| 工作环节 | 下载任务执行时 | 转存（save_share）时 |
| 需要平台配合 | ✅ 需要平台传入文件列表 | ❌ 不需要，插件自己浏览分享 |
| 可实际运行 | ❌ 平台不调用外部task过滤 | ✅ drive接口是平台原生流程 |
| 过滤对象 | 下载清单 | 存目录结构 |
| 结果 | 返回过滤清单 | 根目录保存选中集，子文件夹保存其他集 |

## 目录结构

```
plugins/drive.quark_filter/
  plugin.json          # 插件清单（category=drive）
  backend/
    plugin.py          # 后端核心（QuarkAPI + EpisodeParser + DriveProvider）
  frontend/
    index.ts           # 前端类型定义和辅助函数
  README.md            # 本文档
```
