#!/usr/bin/env python3
"""集数过滤器独立烟测脚本 - 不依赖 core.sdk"""
from __future__ import annotations

import re
import sys

# ---------------------------------------------------------------------------
# 复制核心逻辑进行独立测试
# ---------------------------------------------------------------------------

DEFAULT_VIDEO_EXTENSIONS = [
    ".mp4", ".mkv", ".avi", ".rmvb", ".ts",
    ".mov", ".wmv", ".flv", ".m4v", ".iso",
]

BUILTIN_EPISODE_PATTERNS = [
    r"第\s*([0-9]+)\s*[集话話期]",
    r"[Ee][Pp]?\.?\s*0*([0-9]{1,4})(?!\d)",
    r"[Ee]pisode\s*0*([0-9]{1,4})(?!\d)",
    r"0*([0-9]{1,4})\s*[话話期]",
    r"[\[【]\s*0*([0-9]{1,4})\s*[\]】]",
    r"[-_]\s*0*([0-9]{1,4})\s*[-_]",
    r"0*([0-9]{1,3})\s*(?:\.[A-Za-z0-9]+)*$",
]

COMPILED_PATTERNS = [re.compile(p) for p in BUILTIN_EPISODE_PATTERNS]


def parse_episode(filename: str) -> int | None:
    if not filename:
        return None
    name_base = filename
    for ext in DEFAULT_VIDEO_EXTENSIONS:
        if name_base.lower().endswith(ext):
            name_base = name_base[: -len(ext)]
            break
    for pattern in COMPILED_PATTERNS:
        match = pattern.search(name_base)
        if not match:
            continue
        raw = match.group(1)
        if raw.isdigit():
            num = int(raw)
            if num > 0:
                return num
    return None


def parse_episode_list(text: str) -> list[int]:
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


def is_video_file(name: str) -> bool:
    lower = name.lower()
    return any(lower.endswith(ext) for ext in DEFAULT_VIDEO_EXTENSIONS)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

def test_episode_parser():
    print("=== 集数解析测试 ===")
    test_cases = [
        ("我的剧集.E01.1080p.WEB-DL.mp4", 1),
        ("我的剧集_EP05_4K.mkv", 5),
        ("某剧第12集.rmvb", 12),
        ("Show.Name.S01E03.x264.mp4", 3),
        ("Anime[07].mkv", 7),
        ("剧集.24.mp4", 24),
        ("第03话.ts", 3),
        ("EP10.mkv", 10),
        ("episode5.mp4", 5),
        ("The.Show.E12.FINAL.720p.mp4", 12),
        ("某剧第5期.mkv", 5),
        ("test_08_4k.ts", 8),
        ("【03】某番.mkv", 3),
    ]
    all_pass = True
    for filename, expected in test_cases:
        result = parse_episode(filename)
        status = "PASS" if result == expected else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  {status}: {filename} -> {result} (expected {expected})")
    return all_pass


def test_episode_list_parser():
    print()
    print("=== 集数列表解析测试 ===")
    test_cases = [
        ("1-5", [1, 2, 3, 4, 5]),
        ("1,3,5", [1, 3, 5]),
        ("1-3,5,8-10", [1, 2, 3, 5, 8, 9, 10]),
        ("", []),
        ("10-8", [8, 9, 10]),
        ("1, 2, 3", [1, 2, 3]),
    ]
    all_pass = True
    for text, expected in test_cases:
        result = parse_episode_list(text)
        status = "PASS" if result == expected else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  {status}: '{text}' -> {result} (expected {expected})")
    return all_pass


def test_latest_n_logic():
    print()
    print("=== 最新N集逻辑测试 ===")
    all_pass = True

    # Case 1: 24集，取最新5集 -> 20~24
    all_episodes = list(range(1, 25))
    latest_n = 5
    max_ep = max(all_episodes)
    min_target = max_ep - latest_n + 1
    selected = [e for e in all_episodes if e >= min_target]
    expected = [20, 21, 22, 23, 24]
    status = "PASS" if selected == expected else "FAIL"
    if status == "FAIL":
        all_pass = False
    print(f"  {status}: 24集, latest_n={latest_n} -> {selected} (expected {expected})")

    # Case 2: 10集，取最新3集 -> 8~10
    all_episodes = list(range(1, 11))
    latest_n = 3
    max_ep = max(all_episodes)
    min_target = max_ep - latest_n + 1
    selected = [e for e in all_episodes if e >= min_target]
    expected = [8, 9, 10]
    status = "PASS" if selected == expected else "FAIL"
    if status == "FAIL":
        all_pass = False
    print(f"  {status}: 10集, latest_n={latest_n} -> {selected} (expected {expected})")

    # Case 3: 5集，取最新10集 -> 1~5 (不超过总数)
    all_episodes = list(range(1, 6))
    latest_n = 10
    max_ep = max(all_episodes)
    min_target = max(max_ep - latest_n + 1, 1)
    selected = [e for e in all_episodes if e >= min_target]
    expected = [1, 2, 3, 4, 5]
    status = "PASS" if selected == expected else "FAIL"
    if status == "FAIL":
        all_pass = False
    print(f"  {status}: 5集, latest_n={latest_n} -> {selected} (expected {expected})")

    return all_pass


def test_video_detection():
    print()
    print("=== 视频文件识别测试 ===")
    test_cases = [
        ("movie.mp4", True),
        ("video.mkv", True),
        ("show.rmvb", True),
        ("cover.jpg", False),
        ("subtitle.srt", False),
        ("readme.txt", False),
        ("data.iso", True),
    ]
    all_pass = True
    for filename, expected in test_cases:
        result = is_video_file(filename)
        status = "PASS" if result == expected else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  {status}: {filename} -> {result} (expected {expected})")
    return all_pass


def test_full_filter_simulation():
    """模拟完整过滤流程"""
    print()
    print("=== 完整过滤流程模拟 ===")

    # 模拟网盘文件列表
    files = [
        {"id": "f01", "name": "某剧.E01.1080p.mp4", "type": "file"},
        {"id": "f02", "name": "某剧.E02.1080p.mp4", "type": "file"},
        {"id": "f03", "name": "某剧.E03.1080p.mp4", "type": "file"},
        {"id": "f04", "name": "某剧.E04.1080p.mp4", "type": "file"},
        {"id": "f05", "name": "某剧.E05.1080p.mp4", "type": "file"},
        {"id": "f06", "name": "某剧.E06.1080p.mp4", "type": "file"},
        {"id": "f07", "name": "某剧.E07.1080p.mp4", "type": "file"},
        {"id": "f08", "name": "某剧.E08.1080p.mp4", "type": "file"},
        {"id": "f09", "name": "某剧.E09.1080p.mp4", "type": "file"},
        {"id": "f10", "name": "某剧.E10.1080p.mp4", "type": "file"},
        {"id": "d01", "name": "花絮", "type": "folder"},
        {"id": "f11", "name": "封面.jpg", "type": "file"},
    ]

    # 解析集数
    video_items = []
    for item in files:
        if item["type"] == "folder":
            continue
        if not is_video_file(item["name"]):
            continue
        ep = parse_episode(item["name"])
        if ep is not None:
            item["episode"] = ep
            video_items.append(item)

    all_episodes = [item["episode"] for item in video_items]
    max_ep = max(all_episodes)
    print(f"  扫描到 {len(video_items)} 个视频文件，集数范围 1~{max_ep}")

    # Test latest_n=3
    latest_n = 3
    min_target = max_ep - latest_n + 1
    selected = [item for item in video_items if item["episode"] >= min_target]
    selected_names = [item["name"] for item in selected]
    expected_names = ["某剧.E08.1080p.mp4", "某剧.E09.1080p.mp4", "某剧.E10.1080p.mp4"]
    status = "PASS" if selected_names == expected_names else "FAIL"
    print(f"  {status}: latest_n={latest_n} -> 选中 {selected_names}")

    # Test start_from=5
    start_from = 5
    selected2 = [item for item in video_items if item["episode"] >= start_from]
    count = len(selected2)
    expected_count = 6  # episodes 5-10
    status = "PASS" if count == expected_count else "FAIL"
    print(f"  {status}: start_from={start_from} -> 选中 {count} 个 (expected {expected_count})")

    # Test exclude episodes 3,7
    excluded = {3, 7}
    selected3 = [item for item in video_items if item["episode"] not in excluded]
    count3 = len(selected3)
    expected_count3 = 8  # 10 - 2
    status = "PASS" if count3 == expected_count3 else "FAIL"
    print(f"  {status}: exclude={{3,7}} -> 选中 {count3} 个 (expected {expected_count3})")

    return status == "PASS"


def test_extract_files_from_context():
    """测试 _extract_files_from_context 方法在各种数据入口下的表现。"""
    print()
    print("=== 数据入口提取测试 ===")
    all_pass = True

    # 实例化插件（不用 core.sdk 的 BasePlugin，直接测核心方法）
    # 但 _extract_files_from_context 是实例方法，需要 mock
    # 用简单对象模拟
    class MockPlugin:
        pass

    p = MockPlugin()
    # 直接复制核心逻辑做独立测试
    def extract_files_from_context(execution_context):
        drive_files = execution_context.get("drive_files")
        if isinstance(drive_files, list) and drive_files:
            return drive_files
        config = execution_context.get("config") or {}
        files = config.get("files")
        if isinstance(files, list) and files:
            return files
        input_payload = execution_context.get("input_payload") or config.get("input_payload") or {}
        if isinstance(input_payload, dict):
            drive_files = input_payload.get("drive_files")
            if isinstance(drive_files, list) and drive_files:
                return drive_files
            files = input_payload.get("files")
            if isinstance(files, list) and files:
                return files
            resource = input_payload.get("resource") or {}
            if isinstance(resource, dict):
                files = resource.get("files") or resource.get("drive_files")
                if isinstance(files, list) and files:
                    return files
        return []

    sample_files = [{"id": "f1", "name": "test.mp4", "type": "file"}]

    # Case 1: drive_files at top level (platform injected)
    ctx1 = {"drive_files": sample_files, "config": {}}
    result1 = extract_files_from_context(ctx1)
    s1 = "PASS" if result1 == sample_files else "FAIL"
    if s1 == "FAIL": all_pass = False
    print(f"  {s1}: execution_context.drive_files -> 找到 {len(result1)} 个文件")

    # Case 2: config.files (manual input)
    ctx2 = {"config": {"files": sample_files}}
    result2 = extract_files_from_context(ctx2)
    s2 = "PASS" if result2 == sample_files else "FAIL"
    if s2 == "FAIL": all_pass = False
    print(f"  {s2}: config.files -> 找到 {len(result2)} 个文件")

    # Case 3: input_payload.drive_files
    ctx3 = {"config": {}, "input_payload": {"drive_files": sample_files}}
    result3 = extract_files_from_context(ctx3)
    s3 = "PASS" if result3 == sample_files else "FAIL"
    if s3 == "FAIL": all_pass = False
    print(f"  {s3}: input_payload.drive_files -> 找到 {len(result3)} 个文件")

    # Case 4: input_payload.resource.drive_files
    ctx4 = {"config": {}, "input_payload": {"resource": {"drive_files": sample_files}}}
    result4 = extract_files_from_context(ctx4)
    s4 = "PASS" if result4 == sample_files else "FAIL"
    if s4 == "FAIL": all_pass = False
    print(f"  {s4}: resource.drive_files -> 找到 {len(result4)} 个文件")

    # Case 5: drive_files 优先级高于 config.files
    better_files = [{"id": "f2", "name": "better.mp4"}]
    ctx5 = {"drive_files": better_files, "config": {"files": sample_files}}
    result5 = extract_files_from_context(ctx5)
    s5 = "PASS" if result5 == better_files else "FAIL"
    if s5 == "FAIL": all_pass = False
    print(f"  {s5}: drive_files 优先级高于 config.files -> 选中 {len(result5)} 个")

    # Case 6: 空 context
    ctx6 = {"config": {}}
    result6 = extract_files_from_context(ctx6)
    s6 = "PASS" if result6 == [] else "FAIL"
    if s6 == "FAIL": all_pass = False
    print(f"  {s6}: 空 context -> 返回空列表")

    return all_pass


def test_create_from_resource():
    """测试 create_from_resource 对资源分享链接的提取。"""
    print()
    print("=== create_from_resource 分享链接提取测试 ===")
    all_pass = True

    # 模拟资源对象（含 share link，与 search.pansou 的 ResourceItem 结构一致）
    resource_with_share = {
        "title": "某剧 第一季",
        "media_type": "tv",
        "links": {
            "share": [
                {
                    "drive_type": "quark",
                    "url": "https://pan.quark.cn/s/abc123",
                    "password": "xyz",
                }
            ],
        },
    }

    # 手动模拟 create_from_resource 的核心逻辑
    links = resource_with_share.get("links") or {}
    share_links = (links.get("share") or []) if isinstance(links, dict) else []
    first_share = share_links[0] if share_links else {}
    share_url = str(first_share.get("url") or "").strip()
    drive_type = str(first_share.get("drive_type") or "").strip()
    share_password = str(first_share.get("password") or "").strip()

    s1 = "PASS" if share_url == "https://pan.quark.cn/s/abc123" else "FAIL"
    if s1 == "FAIL": all_pass = False
    print(f"  {s1}: 提取 share_url = {share_url}")

    s2 = "PASS" if drive_type == "quark" else "FAIL"
    if s2 == "FAIL": all_pass = False
    print(f"  {s2}: 提取 drive_type = {drive_type}")

    s3 = "PASS" if share_password == "xyz" else "FAIL"
    if s3 == "FAIL": all_pass = False
    print(f"  {s3}: 提取 share_password = {share_password}")

    # 测试无分享链接的资源
    resource_no_share = {"title": "某电影", "media_type": "movie", "links": {}}
    links2 = resource_no_share.get("links") or {}
    share_links2 = (links2.get("share") or []) if isinstance(links2, dict) else []
    s4 = "PASS" if not share_links2 else "FAIL"
    if s4 == "FAIL": all_pass = False
    print(f"  {s4}: 无分享链接时 share_links 为空")

    # 电影类型默认全部下载
    media_type = str(resource_no_share.get("media_type") or "tv")
    default_mode = "all" if media_type == "movie" else "latest_n"
    s5 = "PASS" if default_mode == "all" else "FAIL"
    if s5 == "FAIL": all_pass = False
    print(f"  {s5}: 电影类型默认模式 = {default_mode}")

    return all_pass


def main():
    results = [
        test_episode_parser(),
        test_episode_list_parser(),
        test_latest_n_logic(),
        test_video_detection(),
        test_full_filter_simulation(),
        test_extract_files_from_context(),
        test_create_from_resource(),
    ]
    print()
    if all(results):
        print("========== ALL TESTS PASSED ==========")
        return 0
    else:
        print("========== SOME TESTS FAILED ==========")
        return 1


if __name__ == "__main__":
    sys.exit(main())
