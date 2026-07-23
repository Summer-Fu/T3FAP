"""夸克网盘集数过滤插件 - 独立烟测脚本"""
from __future__ import annotations
import sys
import re

# 从 plugin.py 中复用核心解析逻辑
BUILTIN_EPISODE_PATTERNS = [
    r"第\s*(\d+)\s*[集话話期]",
    r"第([一二三四五六七八九十百零\d]+)\s*[集话話期]",
    r"[Ee][Pp]?\.?\s*0*(\d{1,4})(?!\d)",
    r"[Ee]pisode\s*0*(\d{1,4})(?!\d)",
    r"0*(\d{1,4})\s*[话話期集]",
    r"[\[【]\s*0*(\d{1,4})\s*[\]】]",
    r"[-_]\s*0*(\d{1,3})\s*[-_]",
    r"0*(\d{1,3})\s*(?:\.[A-Za-z0-9]+)*$",
]

CN_NUM_MAP = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "百": 100,
}

DEFAULT_VIDEO_EXTENSIONS = [
    ".mp4", ".mkv", ".avi", ".rmvb", ".ts", ".mov",
    ".wmv", ".flv", ".m4v", ".iso",
]


def parse_episode(filename: str) -> int | None:
    if not filename:
        return None
    name_base = filename
    for ext in DEFAULT_VIDEO_EXTENSIONS:
        if name_base.lower().endswith(ext):
            name_base = name_base[:-len(ext)]
            break
    compiled = [re.compile(p) for p in BUILTIN_EPISODE_PATTERNS]
    for pattern in compiled:
        match = pattern.search(name_base)
        if not match:
            continue
        raw = match.group(1)
        if raw.isdigit():
            num = int(raw)
            if num > 0:
                return num
        # 中文数字
        result = 0
        valid = True
        for c in raw:
            if c in CN_NUM_MAP:
                result += CN_NUM_MAP[c]
            else:
                valid = False
                break
        if valid and result > 0:
            return result
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


def is_video(filename: str) -> bool:
    return any(filename.lower().endswith(ext) for ext in DEFAULT_VIDEO_EXTENSIONS)


def test_episode_parser() -> bool:
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
        ("[08]我的剧.mp4", 8),
        ("剧-15-名.mp4", 15),
        ("第一季第一集.mp4", 1),
        ("test.txt", None),
    ]
    all_pass = True
    print("=== Episode Parser Tests ===")
    for filename, expected in test_cases:
        if not is_video(filename):
            result = None
        else:
            result = parse_episode(filename)
        status = "PASS" if result == expected else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  {status}: {filename} -> {result} (expected {expected})")
    return all_pass


def test_episode_list_parser() -> bool:
    print("\n=== Episode List Parser Tests ===")
    list_tests = [
        ("1-5", [1, 2, 3, 4, 5]),
        ("1,3,5", [1, 3, 5]),
        ("1-3,5,8-10", [1, 2, 3, 5, 8, 9, 10]),
        ("", []),
        ("10-8", [8, 9, 10]),  # 反向范围
    ]
    all_pass = True
    for text, expected in list_tests:
        result = parse_episode_list(text)
        status = "PASS" if result == expected else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  {status}: \"{text}\" -> {result} (expected {expected})")
    return all_pass


def test_latest_n_logic() -> bool:
    print("\n=== Latest N Episodes Logic Test ===")
    all_pass = True

    # 24集剧集，latest_n=5 → 选中 20-24
    all_episodes = list(range(1, 25))
    latest_n = 5
    max_ep = max(all_episodes)
    min_target = max_ep - latest_n + 1
    selected = [e for e in all_episodes if e >= min_target]
    expected = [20, 21, 22, 23, 24]
    status = "PASS" if selected == expected else "FAIL"
    if status == "FAIL":
        all_pass = False
    print(f"  {status}: 24 episodes, latest_n={latest_n} -> {selected} (expected {expected})")

    # 10集剧集，latest_n=3 → 选中 8-10
    all_episodes = list(range(1, 11))
    latest_n = 3
    max_ep = max(all_episodes)
    min_target = max_ep - latest_n + 1
    selected = [e for e in all_episodes if e >= min_target]
    expected = [8, 9, 10]
    status = "PASS" if selected == expected else "FAIL"
    if status == "FAIL":
        all_pass = False
    print(f"  {status}: 10 episodes, latest_n={latest_n} -> {selected} (expected {expected})")

    # 3集剧集，latest_n=5（N>总数）→ 选中全部
    all_episodes = list(range(1, 4))
    latest_n = 5
    max_ep = max(all_episodes)
    min_target = max_ep - latest_n + 1
    selected = [e for e in all_episodes if e >= min_target]
    expected = [1, 2, 3]
    status = "PASS" if selected == expected else "FAIL"
    if status == "FAIL":
        all_pass = False
    print(f"  {status}: 3 episodes, latest_n={latest_n} (N>total) -> {selected} (expected {expected})")

    return all_pass


def test_filter_modes() -> bool:
    print("\n=== Filter Modes Tests ===")
    all_pass = True

    # 模拟文件列表（24集）
    files = [
        {"fid": f"f{i:02d}", "file_name": f"剧名.E{i:02d}.mp4", "share_fid_token": f"t{i:02d}",
         "episode_number": i, "is_video": True}
        for i in range(1, 25)
    ]
    # 添加非视频文件
    files.append({"fid": "f_sub", "file_name": "字幕.srt", "share_fid_token": "t_sub",
                   "episode_number": None, "is_video": False})

    # test: latest_n=5 → 选中 E20-E24 + 字幕
    latest_n = 5
    max_ep = max(f["episode_number"] for f in files if f["episode_number"] is not None)
    min_target = max_ep - latest_n + 1
    selected = [f for f in files if f["episode_number"] is not None and f["episode_number"] >= min_target]
    selected += [f for f in files if f["episode_number"] is None and f["is_video"]]
    selected += [f for f in files if not f["is_video"]]
    other = [f for f in files if f["episode_number"] is not None and f["episode_number"] < min_target]
    expected_selected_count = 5 + 1  # 5个选中集 + 1个字幕
    expected_other_count = 19
    status = "PASS" if len(selected) == expected_selected_count and len(other) == expected_other_count else "FAIL"
    if status == "FAIL":
        all_pass = False
    print(f"  {status}: latest_n={latest_n} -> selected={len(selected)}, other={len(other)} "
          f"(expected {expected_selected_count}, {expected_other_count})")

    # test: start_from=10 → 选中 E10-E24 + 字幕
    start_ep = 10
    selected = [f for f in files if f["episode_number"] is not None and f["episode_number"] >= start_ep]
    selected += [f for f in files if f["episode_number"] is None and f["is_video"]]
    selected += [f for f in files if not f["is_video"]]
    other = [f for f in files if f["episode_number"] is not None and f["episode_number"] < start_ep]
    status = "PASS" if len(selected) == 15 + 1 and len(other) == 9 else "FAIL"
    if status == "FAIL":
        all_pass = False
    print(f"  {status}: start_from={start_ep} -> selected={len(selected)}, other={len(other)}")

    # test: exclude 1-5 → 选中 E6-E24 + 字幕
    excluded_set = set(parse_episode_list("1-5"))
    selected = [f for f in files if f["episode_number"] is not None and f["episode_number"] not in excluded_set]
    selected += [f for f in files if f["episode_number"] is None and f["is_video"]]
    selected += [f for f in files if not f["is_video"]]
    other = [f for f in files if f["episode_number"] is not None and f["episode_number"] in excluded_set]
    status = "PASS" if len(selected) == 19 + 1 and len(other) == 5 else "FAIL"
    if status == "FAIL":
        all_pass = False
    print(f"  {status}: exclude 1-5 -> selected={len(selected)}, other={len(other)}")

    return all_pass


def test_quark_api_parsing() -> bool:
    print("\n=== Quark URL Parsing Tests ===")
    all_pass = True

    def extract_pwd_id(url: str) -> str:
        match = re.search(r"/s/([A-Za-z0-9_-]+)", url)
        return match.group(1) if match else ""

    tests = [
        ("https://pan.quark.cn/s/abc123def", "abc123def"),
        ("https://pan.quark.cn/s/zzzzz", "zzzzz"),
        ("https://pan.quark.cn/", ""),
        ("random text", ""),
    ]
    for url, expected in tests:
        result = extract_pwd_id(url)
        status = "PASS" if result == expected else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  {status}: \"{url}\" -> \"{result}\" (expected \"{expected}\"")

    return all_pass


def main() -> int:
    results = [
        test_episode_parser(),
        test_episode_list_parser(),
        test_latest_n_logic(),
        test_filter_modes(),
        test_quark_api_parsing(),
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
