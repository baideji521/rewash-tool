# -*- coding: utf-8 -*-
"""core.quality_check — 输出质量检测

用户审查要求：重复帧/删帧/音画偏移等手段有观感与同步风险，
批量生产必须以质检兜底，而不是默认越强越好。

检测项：
1. 文件存在与体积异常（过小/相对源异常缩水）
2. 可解码（ffprobe 可读 + 抽帧解码验证）
3. 时长合理（与期望偏差 < 25%）
4. 音视频流完整（源有音频则输出也必须有）
5. 分辨率/FPS 符合标准化目标（开启标准化时）
"""
import os

from .ffmpeg_runner import run_ffmpeg, probe_media


def check_output(output_path: str, ffprobe_path: str, ffmpeg_path: str,
                 expected: dict = None) -> tuple:
    """
    expected: {"duration":秒, "has_audio":bool, "width":w, "height":h,
               "src_size":字节}（均可省略）
    返回 (passed: bool, issues: list[str], info: dict)
    """
    expected = expected or {}
    issues = []
    info = {}

    # 1. 存在性与体积
    if not os.path.exists(output_path):
        return False, ["文件不存在"], info
    size = os.path.getsize(output_path)
    info["size"] = size
    if size < 50 * 1024:
        issues.append(f"文件过小({size}字节)，疑似损坏")
    src_size = expected.get("src_size")
    if src_size and size < src_size * 0.01:
        issues.append(f"体积异常缩水(源{src_size//1024}KB → {size//1024}KB)")

    # 2. 可解码与流信息
    media = probe_media(ffprobe_path, output_path)
    info.update(media)
    if not media.get("has_video"):
        issues.append("无视频流或不可解码")
        return False, issues, info

    # 抽帧解码验证（真实解码 5 帧）
    r = run_ffmpeg([
        ffmpeg_path, "-hide_banner", "-loglevel", "error",
        "-i", output_path, "-frames:v", "5", "-f", "null", "-"
    ], timeout=60)
    if r.returncode != 0:
        issues.append(f"解码验证失败: {(r.stderr or '')[-120:]}")

    # 3. 时长合理
    dur = float(media.get("duration", 0.0) or 0.0)
    exp_dur = float(expected.get("duration", 0.0) or 0.0)
    if dur <= 0.1:
        issues.append("时长为0")
    elif exp_dur > 0.5 and abs(dur - exp_dur) / exp_dur > 0.25:
        issues.append(f"时长偏差过大(期望{exp_dur:.1f}s 实际{dur:.1f}s)")

    # 4. 音频流完整
    if expected.get("has_audio") and not media.get("has_audio"):
        issues.append("源有音频但输出丢失音频流")

    # 5. 分辨率/FPS
    exp_w = int(expected.get("width", 0) or 0)
    exp_h = int(expected.get("height", 0) or 0)
    if exp_w and exp_h:
        w, h = int(media.get("width", 0)), int(media.get("height", 0))
        if (w, h) != (exp_w, exp_h):
            issues.append(f"分辨率不符(期望{exp_w}x{exp_h} 实际{w}x{h})")
    exp_fps = float(expected.get("fps", 0.0) or 0.0)
    if exp_fps > 0:
        fps = float(media.get("fps", 0.0) or 0.0)
        if fps and abs(fps - exp_fps) > 1.5:
            issues.append(f"帧率不符(期望{exp_fps:.0f} 实际{fps:.1f})")

    return len(issues) == 0, issues, info


def check_final_product(output_path: str, ffprobe_path: str, ffmpeg_path: str,
                        segment_count: int = 0) -> tuple:
    """
    成品质量检测（混剪后）：在基础检测上额外检查片段边界。
    segment_count>0 时抽更多帧验证各段可解码。
    """
    passed, issues, info = check_output(output_path, ffprobe_path, ffmpeg_path)
    if not passed:
        return passed, issues, info
    # 多点抽帧（覆盖各片段区域）
    dur = float(info.get("duration", 0.0) or 0.0)
    if dur > 2 and segment_count > 0:
        import random
        points = sorted(random.uniform(0.1, 0.9) * dur for _ in range(min(segment_count, 5)))
        for t in points:
            r = run_ffmpeg([
                ffmpeg_path, "-hide_banner", "-loglevel", "error",
                "-ss", f"{t:.2f}", "-i", output_path,
                "-frames:v", "2", "-f", "null", "-"
            ], timeout=30)
            if r.returncode != 0:
                issues.append(f"片段点{t:.1f}s处解码异常")
                break
    return len(issues) == 0, issues, info
