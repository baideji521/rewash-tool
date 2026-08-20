# -*- coding: utf-8 -*-
"""core.segment — 分段处理

流程开关之一：切 3~5 段，每段独立随机参数（时序多样性）。

Bug#3 修正：废弃 concat demuxer + stream-copy 拼接
（要求各段编码器/参数严格一致，NVENC/CPU 混用时必炸），
统一改为 concat filter 重编码合并，彻底消除合并失败。

观感保护：段间共享色彩参数（避免色调跳变），
几何/时序/编码参数各段独立随机。
"""
import os

from .randomizer import generate_snapshot
from .ffmpeg_runner import run_ffmpeg
from .config import config_get, config_enabled
from .normalize import get_target_spec
from ..video.video_processor import process_clip
from ..video.filters import spec_encode_args


def decide_segment_count(duration: float) -> int:
    if duration < 15:
        return 0  # 太短不分段
    if duration < 30:
        return 3
    if duration < 90:
        return 4
    return 5


def clamp_segment_count(requested: int, duration: float) -> int:
    """安全分段：用户自选段数（1-20）按素材时长收敛，每段至少约 1.5s，<2 段不分段"""
    try:
        n = int(requested)
    except (TypeError, ValueError):
        n = 4
    n = max(1, min(20, n))
    if duration > 0:
        n = min(n, max(1, int(duration // 1.5)))
    return n


def make_equal_cuts(duration: float, n: int) -> list:
    """按时长均分切点：duration/n 每段（例：60s 分 5 段 = 每段 12s）"""
    base_len = duration / n
    cuts = [base_len * i for i in range(n + 1)]
    cuts[0] = 0.0
    cuts[-1] = duration  # 末段对齐结尾，避免累计误差
    return cuts


def _child_snapshot(base_snap: dict, preset: dict, config: dict, seg_idx: int) -> dict:
    """每段独立快照：重新随机，但色彩参数继承主快照（段间色调连续）"""
    seed = base_snap.get("seed", 0) + seg_idx * 7919
    child = generate_snapshot(preset, config, seed=seed)
    cp = child.get("params", {})
    bp = base_snap.get("params", {})
    for key in ("brightness", "contrast", "saturation", "hue", "channel_mix", "noise"):
        if key in bp:
            cp[key] = bp[key]
    return child


def process_segmented(input_path, output_path, base_snap, preset, config,
                      media_info, use_nvenc, log_fn=None,
                      requested_count=None, progress_cb=None,
                      target_kbps=None) -> tuple:
    """
    分段处理主流程。返回 (success, error_msg, snaps_used)。
    requested_count: 用户自选段数（1-20）；None 时按旧策略自动决定。
    progress_cb(frac 0~1)：分段阶段整体进度。
    target_kbps: 体积对齐目标码率（各段与合并共用，中间文件不膨胀）。
    """
    log_fn = log_fn or (lambda m: None)
    ffmpeg_path = config_get(config, "runtime.ffmpeg", "ffmpeg")
    duration = float(media_info.get("duration", 0.0) or 0.0)
    if requested_count is not None:
        n = clamp_segment_count(requested_count, duration)
    else:
        n = decide_segment_count(duration)
    if n < 2:
        return None, "too short", []  # None 表示调用方走整文件流程

    # 按视频时长均分：duration/n（例：60s 分 5 段 = 每段 12s）；
    # 安全分段由 clamp_segment_count 保证每段不至于过短
    cuts = make_equal_cuts(duration, n)

    # 提速：标准化开启时各段直接按目标规格处理（如 60→30fps 少算一半帧），
    # 合并时的 scale/pad/fps 对已达标的段是空操作
    seg_norm_spec = (get_target_spec(config)
                     if config_enabled(config, "switches.normalize", True) else None)

    seg_files, snaps = [], []
    ok = True
    try:
        for i in range(n):
            seg_start, seg_end = cuts[i], cuts[i + 1]
            seg_len = seg_end - seg_start
            if seg_len < 0.8:
                continue
            seg_snap = _child_snapshot(base_snap, preset, config, i)
            seg_path = output_path + f".seg{i}.mp4"
            log_fn(f"  分段 {i + 1}/{n}: {seg_start:.1f}s~{seg_end:.1f}s")
            seg_cb = None
            if progress_cb:
                seg_cb = (lambda idx: lambda f: progress_cb((idx + min(f, 1.0)) / n))(len(seg_files))
            success, err = process_clip(
                input_path, seg_path, seg_snap, config, media_info,
                use_nvenc=use_nvenc, log_fn=log_fn,
                ss=seg_start, in_duration=seg_len, progress_cb=seg_cb,
                target_kbps=target_kbps, norm_spec=seg_norm_spec)
            if not success:
                return False, f"分段{i + 1}处理失败: {err}", snaps
            seg_files.append(seg_path)
            snaps.append(seg_snap)

        if len(seg_files) < 2:
            # 段数不足，直接改名第一段
            if seg_files:
                os.replace(seg_files[0], output_path)
                return True, "", snaps
            return False, "无有效分段", snaps

        # 重编码合并（Bug#3 修正）
        ok, err = _merge_reencode(seg_files, output_path, base_snap, config,
                                  media_info, use_nvenc, log_fn,
                                  target_kbps=target_kbps)
        if not ok:
            return False, f"分段合并失败: {err}", snaps
        return True, "", snaps
    finally:
        for f in seg_files:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except OSError:
                pass


def _merge_encode_args(snap: dict, config: dict, use_nvenc: bool,
                       do_norm: bool, target_kbps=None) -> list:
    """合并编码参数：尊重标准化页所选编码器
    （h265 优先 hevc_nvenc 硬编，无 N 卡回退 libx265 CPU）"""
    spec = ({"video_codec": config_get(config, "normalize.video_codec", "h264"),
             "pix_fmt": config_get(config, "normalize.pix_fmt", "yuv420p")}
            if do_norm else None)
    return spec_encode_args(snap, config, use_nvenc, target_kbps, spec)


def _merge_reencode(seg_files, output_path, snap, config, media_info,
                    use_nvenc, log_fn, target_kbps=None) -> tuple:
    """concat filter 重编码合并：不依赖各段编码参数/尺寸一致。

    提速两项：
    - 标准化开关打开时，合并直接输出目标规格（scale适配+pad+fps），
      processor 不再单独跑一遍标准化（省一遍全量编码）。
    - 编码优先 NVENC（旧版硬编码 CPU，是分段慢的主因之一），失败回退 CPU。
    注：-hwaccel cuda 实测比 CPU 解码慢（滤镜需逐帧回传），不用。
    """
    ffmpeg_path = config_get(config, "runtime.ffmpeg", "ffmpeg")
    has_audio = bool(media_info.get("has_audio", False))
    n = len(seg_files)
    do_norm = config_enabled(config, "switches.normalize", True)

    if do_norm:
        spec = get_target_spec(config)
        tw = spec["width"] - spec["width"] % 2
        th = spec["height"] - spec["height"] % 2
        vpre = (f"scale={tw}:{th}:force_original_aspect_ratio=decrease:flags=bicubic,"
                f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
                f"fps={spec['fps']},")
        pxf = spec["pix_fmt"] or "yuv420p"
    else:
        # 各段 scale/rotate 独立随机 → 尺寸/SAR 不同，合并前统一缩回源分辨率并重置 SAR
        w = int(media_info.get("width", 0) or 0)
        h = int(media_info.get("height", 0) or 0)
        if w > 0 and h > 0:
            w, h = w - w % 2, h - h % 2
        vpre = f"scale={w}:{h}:flags=bicubic,setsar=1," if w > 0 else "setsar=1,"
        pxf = "yuv420p"

    def build_cmd(v_args):
        cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y"]
        for f in seg_files:
            cmd += ["-i", f]
        if has_audio:
            streams = ";".join(
                f"[{i}:v]{vpre}format={pxf}[sv{i}]" for i in range(n))
            concat_in = "".join(f"[sv{i}][{i}:a]" for i in range(n))
            fc = f"{streams};{concat_in}concat=n={n}:v=1:a=1[v][a]"
            maps = ["-map", "[v]", "-map", "[a]"]
            a_args = ["-c:a", "aac", "-b:a", "192k"]
        else:
            streams = ";".join(
                f"[{i}:v]{vpre}format={pxf}[sv{i}]" for i in range(n))
            concat_in = "".join(f"[sv{i}]" for i in range(n))
            fc = f"{streams};{concat_in}concat=n={n}:v=1:a=0[v]"
            maps = ["-map", "[v]"]
            a_args = []
        return cmd + ["-filter_complex", fc] + maps + v_args + a_args + \
               ["-movflags", "+faststart", output_path]

    # 合并编码：用主快照参数（压缩域扰动延续）；NVENC 失败回退 CPU 重试一次
    attempts = [use_nvenc, False] if use_nvenc else [False]
    err = ""
    for nv in attempts:
        r = run_ffmpeg(build_cmd(_merge_encode_args(snap, config, nv, do_norm,
                                                    target_kbps)),
                       timeout=1800)
        if r.returncode == 0:
            return True, ""
        err = (r.stderr or "").lower()[-500:]
        if nv and "nvenc" not in err:
            break  # 非 NVENC 问题，回退也大概率同样失败，不重试
    return False, err
