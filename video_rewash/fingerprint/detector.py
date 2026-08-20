# -*- coding: utf-8 -*-
"""fingerprint.detector — 视频指纹相似度检测（质检工具）

定位（Bug#2 修正）：作为质检/验证工具报告去重效果，不阻断处理。

实现（经验结论：此 FFmpeg build 无 phash 滤镜）：
- 均匀采样 n 帧 → scale=9:8 灰度 rawvideo → dHash(64位) → 汉明距离归一化
- 两视频对应帧比较，取平均相似度
"""
import subprocess as sp

from ..core.ffmpeg_runner import no_window_kwargs


def _dhash_frame(ffmpeg_path: str, video_path: str, ts: float) -> int:
    """抽取 ts 时刻帧并计算 64 位 dHash，失败返回 None"""
    cmd = [ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error",
           "-ss", f"{ts:.3f}", "-i", video_path, "-frames:v", "1",
           "-vf", "scale=9:8,format=gray", "-f", "rawvideo", "-"]
    try:
        r = sp.run(cmd, stdout=sp.PIPE, stderr=sp.DEVNULL, timeout=15,
                   **no_window_kwargs())
    except Exception:
        return None
    pixels = r.stdout[:72]
    if len(pixels) < 72:
        return None
    h = 0
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            h = (h << 1) | (1 if left > right else 0)
    return h


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _get_duration(ffmpeg_path: str, video_path: str) -> float:
    try:
        from ..core.ffmpeg_runner import detect_ffprobe, probe_media
        ffprobe = detect_ffprobe(ffmpeg_path)
        if ffprobe:
            return float(probe_media(ffprobe, video_path).get("duration", 0.0) or 0.0)
    except Exception:
        pass
    # 回退：ffmpeg 解析 duration
    try:
        r = sp.run([ffmpeg_path, "-nostdin", "-i", video_path],
                   stdout=sp.PIPE, stderr=sp.PIPE, timeout=15,
                   **no_window_kwargs())
        import re
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)",
                      r.stderr.decode("utf-8", errors="ignore"))
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return 0.0


def compare_similarity(path_a: str, path_b: str, ffmpeg_path: str,
                       n_frames: int = 8) -> float:
    """
    计算两视频指纹相似度 [0,1]，1=完全相同。
    采样失败时返回 None。
    """
    dur_a = _get_duration(ffmpeg_path, path_a)
    dur_b = _get_duration(ffmpeg_path, path_b)
    if dur_a <= 0.5 or dur_b <= 0.5:
        return None
    dur = min(dur_a, dur_b)

    sims = []
    for i in range(max(1, n_frames)):
        ts = dur * (i + 0.5) / n_frames
        ha = _dhash_frame(ffmpeg_path, path_a, ts)
        hb = _dhash_frame(ffmpeg_path, path_b, ts)
        if ha is None or hb is None:
            continue
        sims.append(1.0 - _hamming(ha, hb) / 64.0)
    if not sims:
        return None
    return sum(sims) / len(sims)
