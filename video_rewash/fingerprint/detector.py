# -*- coding: utf-8 -*-
"""fingerprint.detector — 多层视频相似度检测（v9.1 性能优化）

参考 Video Duplicate Finder Next 的多层检测理念，结合本项目
FFmpeg + NVENC 架构，实现三层级联指纹检测。

架构：
  Layer 1 — 灰度直方图诊断（复用 L2 帧，零额外 FFmpeg 调用）
  Layer 2 — 多帧时域多特征融合指纹（单次 FFmpeg 批量提取 N 帧）
  Layer 3 — FFmpeg SSIM 精确验证（仅灰色地带触发）

v9.1 性能优化：
  - 批量帧提取：单次 FFmpeg 调用提取全部采样帧（替代 N 次独立调用）
  - 时长缓存：两个视频各查询一次 duration（替代 4 次重复查询）
  - L1 帧复用：L1 诊断直接使用 L2 已提取的帧（消除 3 次独立帧提取）
  - 优化前：4 次 duration + 13 次帧提取 = 17 次 FFmpeg 进程
  - 优化后：2 次 duration + 2 次批量帧提取 = 4 次 FFmpeg 进程

输出：
  compare_similarity() → float [0,1]
  1.0 = 完全相同，0.0 = 完全不同
  与 processor.py 的阈值比较逻辑完全兼容。
"""
import subprocess as sp
import re
import time

from ..core.ffmpeg_runner import no_window_kwargs


# ═══════════════════════════════════════════════════════════════
#  常量与配置
# ═══════════════════════════════════════════════════════════════

# dHash 网格参数
_HASH_W = 16
_HASH_H = 16
_BITS_PER_ROW = _HASH_W - 1          # 15
_HASH_BITS = _BITS_PER_ROW * _HASH_H  # 240

# 多特征融合权重（Layer 2 内部）
_W_DHASH = 0.40    # 结构相似度
_W_BRIGHT = 0.15   # 亮度差异
_W_HIST = 0.45     # 灰度直方图距离

# 直方图 bin 数
_HIST_BINS = 16

# Layer 1 快速拒绝阈值
_LAYER1_REJECT_DIST = 0.30   # 直方图距离 > 此值 → 明显不同
_LAYER1_SKIP_DIST = 0.05     # 直方图距离 < 此值 → 内容极相似，跳过快速拒绝

# Layer 2 灰色地带（触发 Layer 3）
# 优化：缩窄灰色区间，大部分情况 L2 即可判定，跳过昂贵的 L3 SSIM
_LAYER2_GREY_LOW = 0.50
_LAYER2_GREY_HIGH = 0.70

# Layer 3 SSIM 融合权重
_LAYER3_WEIGHT_DHASH = 0.35
_LAYER3_WEIGHT_SSIM = 0.65

# 快速判定阈值（基于 L3 融合公式推导）：
# final = 0.35 * combined + 0.65 * SSIM
# combined < 0.50 → final 最高 0.50（SSIM=1.0），必 PASS → 跳过 L3
# combined > 0.70 → final 最低 0.70（SSIM=0.0），必 FAIL → 跳过 L3
_QUICK_PASS_THRESH = 0.50
_QUICK_FAIL_THRESH = 0.70

# SSIM 比较参数
_SSIM_WIDTH = 320
_SSIM_HEIGHT = 180
_SSIM_WINDOW_SEC = 5.0


# ═══════════════════════════════════════════════════════════════
#  帧提取与基础特征
# ═══════════════════════════════════════════════════════════════

def _extract_frame(ffmpeg_path: str, video_path: str, ts: float) -> bytes:
    """抽取 ts 时刻帧，返回 16×16 灰度 raw 像素数据，失败返回 None"""
    cmd = [ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error",
           "-ss", f"{ts:.3f}", "-i", video_path, "-frames:v", "1",
           "-vf", f"scale={_HASH_W}:{_HASH_H},format=gray", "-f", "rawvideo", "-"]
    try:
        r = sp.run(cmd, stdout=sp.PIPE, stderr=sp.DEVNULL, timeout=15,
                   **no_window_kwargs())
    except Exception:
        return None
    expected = _HASH_W * _HASH_H
    pixels = r.stdout[:expected]
    if len(pixels) < expected:
        return None
    return pixels


def _batch_extract_frames(ffmpeg_path: str, video_path: str,
                          timestamps: list) -> dict:
    """批量提取多帧 16×16 灰度数据（单次 FFmpeg 调用）。

    对短视频（≤120s）：提取全部帧为 raw 数据，按时间戳索引最近帧。
    对长视频（>120s）：回退到逐帧独立提取（避免内存过大）。

    返回 {timestamp: pixels_bytes}，失败的帧不包含在字典中。
    """
    frame_size = _HASH_W * _HASH_H  # 256 bytes per frame
    result = {}

    try:
        dur = _get_duration(ffmpeg_path, video_path)
        if dur <= 0.5:
            return result

        if dur <= 120:
            # ── 短视频：一次性提取全部帧为 raw 数据 ──
            cmd = [ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-i", video_path,
                   "-vf", f"scale={_HASH_W}:{_HASH_H},format=gray",
                   "-f", "rawvideo", "-"]
            r = sp.run(cmd, stdout=sp.PIPE, stderr=sp.DEVNULL, timeout=60,
                       **no_window_kwargs())
            data = r.stdout
            total_frames = len(data) // frame_size
            if total_frames < 1:
                return result
            # 对每个目标时间戳，找到最近的帧索引
            fps_est = total_frames / dur
            for ts in timestamps:
                idx = min(int(ts * fps_est), total_frames - 1)
                offset = idx * frame_size
                result[ts] = data[offset:offset + frame_size]
        else:
            # ── 长视频：逐帧独立提取（避免全量解码内存压力）──
            for ts in timestamps:
                pix = _extract_frame(ffmpeg_path, video_path, ts)
                if pix is not None:
                    result[ts] = pix
    except Exception:
        # 批量提取失败，回退到逐帧提取
        for ts in timestamps:
            if ts not in result:
                pix = _extract_frame(ffmpeg_path, video_path, ts)
                if pix is not None:
                    result[ts] = pix

    return result


def _closest_ts(target: float, available: dict) -> float:
    """在已提取帧的时间戳中找到最接近 target 的。"""
    if not available:
        return target
    return min(available.keys(), key=lambda t: abs(t - target))


def _dhash_from_pixels(pixels: bytes) -> int:
    """从灰度像素数据计算 dHash（240 位）"""
    h = 0
    for row in range(_HASH_H):
        for col in range(_BITS_PER_ROW):
            left = pixels[row * _HASH_W + col]
            right = pixels[row * _HASH_W + col + 1]
            h = (h << 1) | (1 if left > right else 0)
    return h


def _avg_brightness(pixels: bytes) -> float:
    """从灰度像素数据计算平均亮度 [0, 255]"""
    return sum(pixels) / len(pixels)


def _histogram(pixels: bytes) -> list:
    """从灰度像素数据计算归一化灰度直方图"""
    hist = [0] * _HIST_BINS
    for p in pixels:
        hist[min(p * _HIST_BINS // 256, _HIST_BINS - 1)] += 1
    total = sum(hist)
    if total == 0:
        return [1.0 / _HIST_BINS] * _HIST_BINS
    return [h / total for h in hist]


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _bhattacharyya_dist(ha: list, hb: list) -> float:
    """Bhattacharyya 距离 [0,1]，0=完全相同，1=完全不同"""
    bc = sum(min(a, b) for a, b in zip(ha, hb))
    return 1.0 - bc


# ═══════════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════════

def _get_duration(ffmpeg_path: str, video_path: str) -> float:
    """获取视频时长（秒）"""
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
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)",
                      r.stderr.decode("utf-8", errors="ignore"))
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return 0.0


def _get_resolution(ffmpeg_path: str, video_path: str) -> tuple:
    """获取视频分辨率 (width, height)，失败返回 None"""
    try:
        from ..core.ffmpeg_runner import detect_ffprobe, probe_media
        ffprobe = detect_ffprobe(ffmpeg_path)
        if ffprobe:
            info = probe_media(ffprobe, video_path)
            w = int(info.get("width", 0))
            h = int(info.get("height", 0))
            if w > 0 and h > 0:
                return (w, h)
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════
#  Layer 1 — 灰度直方图快速拒绝
# ═══════════════════════════════════════════════════════════════

def _layer1_histogram_reject(
    ffmpeg_path: str, path_a: str, path_b: str, n_probe: int = 3
) -> float:
    """快速直方图比较。返回初步相似度估计 [0,1]。

    提取 n_probe 帧的 16×16 灰度直方图，计算平均 Bhattacharyya 距离。
    如果距离很大（> _LAYER1_REJECT_DIST），说明内容明显不同，
    直接映射为低相似度返回，无需进入更耗时的 Layer 2。
    """
    dur_a = _get_duration(ffmpeg_path, path_a)
    dur_b = _get_duration(ffmpeg_path, path_b)
    if dur_a <= 0.5 or dur_b <= 0.5:
        return 0.5  # 无法判断，交给 Layer 2
    dur = min(dur_a, dur_b)

    dists = []
    for i in range(n_probe):
        ts = dur * (i + 0.5) / n_probe
        pix_a = _extract_frame(ffmpeg_path, path_a, ts)
        pix_b = _extract_frame(ffmpeg_path, path_b, ts)
        if pix_a is None or pix_b is None:
            continue
        hist_a = _histogram(pix_a)
        hist_b = _histogram(pix_b)
        dists.append(_bhattacharyya_dist(hist_a, hist_b))

    if not dists:
        return 0.5  # 无法判断

    avg_dist = sum(dists) / len(dists)

    # 映射到相似度：
    # dist=0.00 → sim=1.00（完全相同）
    # dist=0.05 → sim=0.85（极相似）
    # dist=0.15 → sim=0.55（中等差异）
    # dist=0.30 → sim=0.10（明显不同）
    # dist=0.50 → sim=0.00（完全不同）
    sim = max(0.0, 1.0 - avg_dist * 3.0)
    return sim


# ═══════════════════════════════════════════════════════════════
#  Layer 2 — 多帧时域多特征融合指纹
# ═══════════════════════════════════════════════════════════════

def _layer2_temporal_fingerprint(
    ffmpeg_path: str, path_a: str, path_b: str, n_frames: int,
    dur: float = None, frames_a: dict = None, frames_b: dict = None
) -> tuple:
    """多帧时域多特征融合。返回 (similarity, avg_dhash, avg_bright, avg_hist, stats_dict)。

    均匀采样 n_frames 帧，从预提取的 16×16 灰度帧数据同时计算
    dHash / 亮度 / 直方图三项特征，加权融合。
    支持传入预提取帧（frames_a/frames_b）避免重复 FFmpeg 调用。
    stats_dict 包含采样统计信息用于诊断日志。
    """
    if dur is None:
        dur_a = _get_duration(ffmpeg_path, path_a)
        dur_b = _get_duration(ffmpeg_path, path_b)
        if dur_a <= 0.5 or dur_b <= 0.5:
            return (None, None, None, None, {"success": 0, "failed": n_frames})
        dur = min(dur_a, dur_b)

    dhash_sims = []
    bright_diffs = []
    hist_dists = []

    for i in range(max(1, n_frames)):
        ts = dur * (i + 0.5) / n_frames
        # 使用预提取帧（通过最近时间戳匹配）
        if frames_a and frames_b:
            closest_a = _closest_ts(ts, frames_a)
            closest_b = _closest_ts(ts, frames_b)
            pix_a = frames_a.get(closest_a)
            pix_b = frames_b.get(closest_b)
        else:
            pix_a = _extract_frame(ffmpeg_path, path_a, ts)
            pix_b = _extract_frame(ffmpeg_path, path_b, ts)
        if pix_a is None or pix_b is None:
            continue

        # ① dHash 结构相似度
        ha = _dhash_from_pixels(pix_a)
        hb = _dhash_from_pixels(pix_b)
        dhash_sims.append(1.0 - _hamming(ha, hb) / float(_HASH_BITS))

        # ② 亮度差异（归一化到 [0,1]）
        ba = _avg_brightness(pix_a)
        bb = _avg_brightness(pix_b)
        bright_diffs.append(abs(ba - bb) / 255.0)

        # ③ 灰度直方图 Bhattacharyya 距离
        hist_a = _histogram(pix_a)
        hist_b = _histogram(pix_b)
        hist_dists.append(_bhattacharyya_dist(hist_a, hist_b))

    if not dhash_sims:
        return (None, None, None, None, {"success": 0, "failed": n_frames})

    avg_dhash = sum(dhash_sims) / len(dhash_sims)
    avg_bright = sum(bright_diffs) / len(bright_diffs)
    avg_hist = sum(hist_dists) / len(hist_dists)

    # 加权融合：
    # dHash 越高 → 越相似；bright_diff/hist_dist 越高 → 越不同
    combined = (
        _W_DHASH * avg_dhash
        + _W_BRIGHT * (1.0 - min(avg_bright * 5.0, 1.0))
        + _W_HIST * (1.0 - min(avg_hist * 3.0, 1.0))
    )
    stats = {"success": len(dhash_sims), "failed": n_frames - len(dhash_sims)}
    return (combined, avg_dhash, avg_bright, avg_hist, stats)


# ═══════════════════════════════════════════════════════════════
#  Layer 3 — FFmpeg SSIM 精确验证
# ═══════════════════════════════════════════════════════════════

def _layer3_ssim_verify(
    ffmpeg_path: str, path_a: str, path_b: str,
    dur_a: float = None, dur_b: float = None
) -> float:
    """使用 FFmpeg ssim 滤镜计算结构相似度。返回 SSIM All [0,1]，失败返回 None。

    将两个视频缩放到相同分辨率（320×180），使用 setpts 对齐时间基，
    取较短视频的时长进行比较。
    支持传入缓存的时长避免重复 ffprobe 调用。
    参考: https://ffmpeg.org/ffmpeg-filters.html#ssim
    """
    if dur_a is None:
        dur_a = _get_duration(ffmpeg_path, path_a)
    if dur_b is None:
        dur_b = _get_duration(ffmpeg_path, path_b)
    if dur_a <= 0.5 or dur_b <= 0.5:
        return None
    compare_dur = min(dur_a, dur_b, _SSIM_WINDOW_SEC)

    w = _SSIM_WIDTH
    h = _SSIM_HEIGHT

    # 构建 SSIM 比较命令
    # 两路输入分别缩放到相同分辨率，对齐时间基
    filter_complex = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
        f"settb=AVTB,setpts=N/FRAME_RATE/TB[v0];"
        f"[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
        f"settb=AVTB,setpts=N/FRAME_RATE/TB[v1];"
        f"[v0][v1]ssim"
    )

    cmd = [ffmpeg_path, "-nostdin", "-hide_banner",
           "-ss", "0", "-t", f"{compare_dur:.2f}", "-i", path_a,
           "-ss", "0", "-t", f"{compare_dur:.2f}", "-i", path_b,
           "-filter_complex", filter_complex,
           "-f", "null", "-"]

    try:
        r = sp.run(cmd, stdout=sp.PIPE, stderr=sp.PIPE, timeout=60,
                   **no_window_kwargs())
        stderr_text = r.stderr.decode("utf-8", errors="ignore")

        # 解析 SSIM 结果：
        # 格式: [Parsed_ssim_0 @ ...] SSIM Y:0.95 (13.01) U:0.98 (16.94) V:0.97 (15.82) All:0.96 (14.27)
        m = re.search(r"SSIM\s+Y:([\d.-]+).*All:([\d.-]+)", stderr_text)
        if m:
            return float(m.group(2))

        # 某些 FFmpeg 版本输出格式不同，尝试从日志末尾提取
        for line in reversed(stderr_text.splitlines()):
            m2 = re.search(r"All:([\d.-]+)", line)
            if m2:
                return float(m2.group(1))

    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════
#  主入口 — 多层级联比较（优化版）
# ═══════════════════════════════════════════════════════════════

def compare_similarity(path_a: str, path_b: str, ffmpeg_path: str,
                       n_frames: int = 8, log_fn=None) -> float:
    """
    多层视频相似度检测 [0,1]，1=完全相同。

    级联流程（v9.1 性能优化版）：
      1. 获取时长（每个视频仅 1 次 ffprobe）
      2. 批量提取帧（每个视频仅 1 次 FFmpeg 调用提取全部 n_frames 帧）
      3. Layer 1: 灰度直方图诊断 — 复用 L2 已提取帧，零额外 FFmpeg 调用
      4. Layer 2: 多帧时域多特征融合 — 使用预提取帧计算
      5. Layer 3: FFmpeg SSIM — 仅当 L2 处于灰色地带 [0.50, 0.70] 时触发

    快速判定：
      L2 combined < 0.50 → 直接 PASS（跳过 L3）
      L2 combined > 0.70 → 直接 FAIL（跳过 L3）

    采样失败时返回 0.5（不确定），绝不返回 None 或伪装 0.0。
    """
    _log = log_fn or (lambda m: None)
    import os as _os
    t_total = time.time()
    _log(f"指纹检测：source={_os.path.basename(path_a)} output={_os.path.basename(path_b)}")

    # ── 获取时长（每个视频仅 1 次 ffprobe，缓存给 L1/L2/L3 复用）──
    dur_a = _get_duration(ffmpeg_path, path_a)
    dur_b = _get_duration(ffmpeg_path, path_b)
    _log(f"  duration_source={dur_a:.1f}s duration_output={dur_b:.1f}s")
    if dur_a <= 0.5 or dur_b <= 0.5:
        _log("  ⚠ 视频时长过短，无法进行指纹检测")
        return 0.5

    dur = min(dur_a, dur_b)

    # ── 计算 L2 采样时间戳 ──
    l2_timestamps = [dur * (i + 0.5) / n_frames for i in range(n_frames)]

    # ── 批量提取帧（每个视频仅 1 次 FFmpeg 调用）──
    t_ext = time.time()
    frames_a = _batch_extract_frames(ffmpeg_path, path_a, l2_timestamps)
    frames_b = _batch_extract_frames(ffmpeg_path, path_b, l2_timestamps)
    ext_time = time.time() - t_ext
    _log(f"  批量帧提取: A={len(frames_a)}/{n_frames}帧 B={len(frames_b)}/{n_frames}帧 ({ext_time:.2f}s)")

    if not frames_a or not frames_b:
        _log(f"  ⚠ 指纹检测失败：批量提取帧为空，返回不确定值")
        return 0.5

    # ── Layer 1: 灰度直方图诊断（复用 L2 已提取帧，零额外 FFmpeg 调用）──
    t0 = time.time()
    l1_dists = []
    l1_ok, l1_fail = 0, 0
    for i in range(3):
        ts = dur * (i + 0.5) / 3
        # 从 L2 预提取帧中找最近的
        close_a = _closest_ts(ts, frames_a)
        close_b = _closest_ts(ts, frames_b)
        pix_a = frames_a.get(close_a)
        pix_b = frames_b.get(close_b)
        if pix_a is None or pix_b is None:
            l1_fail += 1
            continue
        l1_ok += 1
        l1_dists.append(_bhattacharyya_dist(_histogram(pix_a), _histogram(pix_b)))
    l1_sim = max(0.0, 1.0 - sum(l1_dists) / len(l1_dists) * 3.0) if l1_dists else 0.5
    _log(f"  L1直方图(诊断): sim={l1_sim:.3f} 采样={l1_ok}/3 失败={l1_fail} ({time.time()-t0:.2f}s) [复用L2帧]")

    # ── Layer 2: 多帧时域多特征融合（使用预提取帧，零额外 FFmpeg 调用）──
    t1 = time.time()
    l2_sim, l2_dhash, l2_bright, l2_hist, l2_stats = _layer2_temporal_fingerprint(
        ffmpeg_path, path_a, path_b, n_frames,
        dur=dur, frames_a=frames_a, frames_b=frames_b
    )
    l2_time = time.time() - t1

    if l2_sim is None:
        _log(f"  ⚠ 指纹检测失败：成功采样 0/{n_frames}，返回不确定值")
        return 0.5

    _log(f"  采样: plan={n_frames} success={l2_stats['success']} failed={l2_stats['failed']}")
    _log(f"  dhash={l2_dhash:.3f} brightness={l2_bright:.3f} histogram={l2_hist:.3f} "
         f"combined={l2_sim:.3f} ({l2_time:.2f}s)")

    # ── 快速判定：L2 结果明确时跳过昂贵的 L3 SSIM ──
    if l2_sim < _QUICK_PASS_THRESH:
        _log(f"  快速检测：combined={l2_sim:.3f} < {_QUICK_PASS_THRESH} → 明确 PASS，跳过 L3 SSIM")
        _log(f"指纹检测：耗时 {time.time()-t_total:.2f}s 结果={l2_sim:.3f} (快速PASS)")
        return l2_sim
    if l2_sim > _QUICK_FAIL_THRESH:
        _log(f"  快速检测：combined={l2_sim:.3f} > {_QUICK_FAIL_THRESH} → 明确 FAIL，跳过 L3 SSIM")
        _log(f"指纹检测：耗时 {time.time()-t_total:.2f}s 结果={l2_sim:.3f} (快速FAIL)")
        return l2_sim

    # ── Layer 3: SSIM 精确验证（仅灰色地带 0.50~0.70，传入缓存时长）──
    t3 = time.time()
    _log(f"  灰色地带({l2_sim:.3f})，进入 L3 SSIM 精确验证")
    ssim_score = _layer3_ssim_verify(ffmpeg_path, path_a, path_b,
                                     dur_a=dur_a, dur_b=dur_b)

    if ssim_score is not None:
        final = (_LAYER3_WEIGHT_DHASH * l2_sim
                 + _LAYER3_WEIGHT_SSIM * ssim_score)
        _log(f"  L3 SSIM={ssim_score:.3f} → 最终={final:.3f} ({time.time()-t3:.2f}s)")
        _log(f"指纹检测：耗时 {time.time()-t_total:.2f}s 结果={final:.3f} (L3融合)")
        return final

    # SSIM 失败，回退到 Layer 2 结果
    _log(f"  L3 SSIM 失败，回退 Layer2={l2_sim:.3f}")
    _log(f"指纹检测：耗时 {time.time()-t_total:.2f}s 结果={l2_sim:.3f} (L2回退)")
    return l2_sim
