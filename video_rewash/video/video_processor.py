# -*- coding: utf-8 -*-
"""video.video_processor — 单段/单文件视频处理执行

核心链路第三环：processor 调用本模块把快照落地为 FFmpeg 命令并执行。
NVENC 失败自动回退 CPU（Bug#11/12 经验：消费级 GPU 会话数有限）。

流水线顺序固定：时序（裁剪/倒放循环/抽帧/变速）→ 空间（黑边裁剪/缩放/旋转）
→ 颜色 → 纹理 → 音频 → 一次最终编码，中间不反复编码。
"""
import math
import random

from ..core.ffmpeg_runner import run_ffmpeg
from ..core.config import config_get
from .filters import (build_spatial_chain, build_geq_filter,
                      build_frame_dup_complex,
                      build_reverse_loop_complex, frame_drop_positions,
                      build_frame_drop_expr, spec_encode_args,
                      build_audio_filter)
from ..audio.audio_processor import build_audio_args


def build_temporal_pre(snap: dict, config: dict, seg_dur: float,
                       fps: float, eff_fps: float, norm_spec: dict) -> list:
    """时序前置滤镜列表（帧率降档 → 抽帧 → 变速），整文件与分段单进程共用。
    抽帧帧号按目标帧率计数，语义与旧版一致。"""
    p = snap.get("params", {})
    pre = []
    # 提速：标准化目标帧率提前生效（如 60→30fps 少算一半帧）
    if norm_spec and eff_fps < fps:
        pre.append(f"fps={eff_fps:.3f}")
    if p.get("frame_drop_on"):
        fd_cfg = (config_get(config, "video.frame_drop", {}) or {})
        fd_iv = fd_cfg.get("interval", {}) or {}
        try:
            fd_lo, fd_hi = int(fd_iv.get("min", 100)), int(fd_iv.get("max", 200))
        except (TypeError, ValueError):
            fd_lo, fd_hi = 100, 200
        rng_fd = random.Random(int(snap.get("seed", 0)) + 29)
        drops = frame_drop_positions(int(seg_dur * eff_fps), fd_lo, fd_hi, rng_fd)
        fd_expr = build_frame_drop_expr(drops)
        if fd_expr:
            pre.append(fd_expr)
    speed = float(p.get("speed", 1.0))
    if abs(speed - 1.0) > 0.0005:
        pre.append(f"setpts={1.0 / speed:.6f}*PTS")
    return pre


def build_command(input_path, output_path, snap, config, media_info,
                  use_nvenc=True, ss=0.0, in_duration=None,
                  video_codec_args=None, target_kbps=None,
                  norm_spec=None, crop_rect=None) -> list:
    """
    构建完整 ffmpeg 命令。
    ss: 输入侧起始偏移（秒）；in_duration: 输入侧时长（秒，None=整段）。
    video_codec_args: 覆盖编码参数（预转码对齐时使用）。
    target_kbps: 体积对齐目标视频码率（None=CRF 回退）。
    norm_spec: 标准化目标规格（scale/pad/fps 折入主处理一遍完成，免单独标准化遍）。
    crop_rect: 黑边检测矩形 (w, h, x, y)，分析阶段一次性获得。
    """
    ffmpeg_path = config_get(config, "runtime.ffmpeg", "ffmpeg")
    ffprobe = None
    p = snap.get("params", {})
    width = int(media_info.get("width", 0) or 0)
    height = int(media_info.get("height", 0) or 0)
    fps = float(media_info.get("fps", 25.0) or 25.0)
    has_audio = bool(media_info.get("has_audio", False))
    if width <= 0 or height <= 0:
        width, height = 1280, 720
    # 提速：标准化目标帧率提前生效，滤镜链直接按目标帧率处理（如 60→30fps 少算一半帧）
    eff_fps = float(norm_spec.get("fps", fps)) if norm_spec else fps

    # 首尾裁剪（输入侧 -ss / -t）
    trim_head = float(p.get("trim_head", 0.0)) if ss == 0 else 0.0
    total_dur = float(media_info.get("duration", 0.0) or 0.0)
    cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y"]
    # 注：-hwaccel cuda 实测比 CPU 解码更慢（CPU 滤镜链需逐帧回传），不用
    start = ss + (trim_head if ss == 0 else 0.0)
    if start > 0.05:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", input_path]
    if in_duration is None:
        # 整文件模式：裁掉尾部
        avail = total_dur - start
        if avail > 0.5:
            cmd += ["-t", f"{max(0.5, avail - float(p.get('trim_tail', 0.0))):.3f}"]
    elif in_duration > 0:
        cmd += ["-t", f"{in_duration:.3f}"]

    seg_dur = in_duration if in_duration else max(1.0, total_dur - start)

    # ══ 视频滤镜：流水线顺序 时序 → 空间 → 颜色 → 纹理 ══
    # ① 时序：倒放/循环（trim 之后、所有画面滤镜之前；仅整文件模式，
    #    分段模式各段独立时序，不在段内叠加）
    rl_fc, v_in = None, ""
    if (p.get("rl_mode") and ss == 0 and in_duration is None
            and seg_dur >= 2.0):
        rl_fc, v_lbl, _a_unused = build_reverse_loop_complex(
            snap, seg_dur, has_audio)
        if rl_fc:
            v_in = v_lbl  # 后续滤镜接在 [vt] 上

    # 空间/颜色/纹理（性能优化：先缩到目标画布再跑重滤镜）
    vf = build_spatial_chain(snap, width, height, eff_fps,
                             norm_spec=norm_spec, crop_rect=crop_rect,
                             config=config)

    # ② 时序：抽帧（周期性每 interval 帧删 1 帧）→ 变速（拼在空间链之前）
    pre = build_temporal_pre(snap, config, seg_dur, fps, eff_fps, norm_spec)
    # 时序残余(抽帧/变速) 拼在空间/颜色/纹理之前；
    # rl 模式下 concat 输出帧号从 0 重计，select 语义仍正确
    vf = (",".join(pre) + "," if pre else "") + vf

    # ③ 标准化折入主处理：画布缩放已前移到空间链头部，此处只补 fps/格式/geq
    if norm_spec:
        fp = norm_spec.get("fps", 30)
        pix = str(norm_spec.get("pix_fmt", "yuv420p"))
        tw = int(norm_spec.get("width", 1280))
        th = int(norm_spec.get("height", 720))
        vf = ((vf + ",") if vf else "") + f"fps={fp},format={pix}"
        geq = build_geq_filter(snap, tw - tw % 2, th - th % 2)
        if geq:
            vf += "," + geq

    # 音频参数
    audio_codec = str(config_get(config, "normalize.audio_codec", "aac"))
    in_pre, a_args, audio_mixed = build_audio_args(snap, has_audio, audio_codec)
    if in_pre:
        # -itsoffset 必须在 -i 之前：重排命令
        i_idx = cmd.index("-i")
        cmd = cmd[:i_idx] + in_pre + cmd[i_idx:]

    frame_dup_n = int(p.get("frame_dup", 0))
    speed = float(p.get("speed", 1.0))
    use_complex = bool(rl_fc) or (frame_dup_n > 0 and seg_dur > 1.0) or audio_mixed

    if use_complex:
        parts = []
        if rl_fc:
            parts.append(rl_fc)
            src_lbl = v_in  # [vt]
        else:
            src_lbl = "[0:v]"
        if frame_dup_n > 0 and seg_dur > 1.0:
            parts.append(f"{src_lbl}{vf}[vm]")
            dup_fc, _ = build_frame_dup_complex(snap, seg_dur / max(speed, 0.1), "vm")
            if dup_fc:
                parts.append(dup_fc)
                v_out = "[vout]"
            else:
                v_out = "[vm]"
        else:
            parts.append(f"{src_lbl}{vf}[vout]")
            v_out = "[vout]"

        # 音频：rl 时音频同样三段拼接（[at]），与视频切点一致不跑同步；
        # 其后叠加既有音频滤镜链（音量/变调/EQ/高低通）与混噪。
        if has_audio and rl_fc:
            a_src = "[at]"
            if audio_mixed:
                # 从 a_args 中提取 filter_complex 段，把输入标签换成 [at]
                for i, a in enumerate(a_args):
                    if a == "-filter_complex":
                        parts.append(a_args[i + 1].replace("[0:a]", a_src, 1))
                        a_args = a_args[:i] + a_args[i + 2:]
                        break
                a_map = "[amix]"
            else:
                af = build_audio_filter(snap)
                if af:
                    parts.append(f"{a_src}{af}[amix]")
                    a_map = "[amix]"
                    # 去掉 a_args 里的 -af 段，保留编码参数
                    for i, a in enumerate(a_args):
                        if a == "-af":
                            a_args = a_args[:i] + a_args[i + 2:]
                            break
                else:
                    a_map = a_src
        elif audio_mixed and has_audio:
            # 从 a_args 中提取 filter_complex 段
            for i, a in enumerate(a_args):
                if a == "-filter_complex":
                    parts.append(a_args[i + 1])
                    a_args = a_args[:i] + a_args[i + 2:]
                    break
            a_map = "[amix]"
        else:
            a_map = "0:a"

        cmd += ["-filter_complex", ";".join(parts), "-map", v_out]
        if has_audio:
            cmd += ["-map", a_map]
    else:
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-map", "0:v"]
        if has_audio:
            cmd += ["-map", "0:a"]

    cmd += a_args if has_audio else ["-an"]
    # 抽帧后视频略短于音频：按视频长度对齐收尾，防止尾部音画错位
    if any(x.startswith("select=") for x in pre):
        cmd += ["-shortest"]

    # 视频编码（尊重标准化页编码器选择：h265 → hevc_nvenc/libx265）
    cmd += video_codec_args if video_codec_args else spec_encode_args(
        snap, config, use_nvenc, target_kbps, norm_spec)
    cmd += ["-movflags", "+faststart", output_path]
    return cmd


def process_clip(input_path, output_path, snap, config, media_info,
                 use_nvenc=True, log_fn=None, ss=0.0, in_duration=None,
                 video_codec_args=None, progress_cb=None,
                 target_kbps=None, norm_spec=None, crop_rect=None) -> tuple:
    """
    执行处理，NVENC 失败自动回退 CPU。
    progress_cb(frac 0~1)：本段编码实时进度。
    crop_rect: 黑边检测矩形（分析阶段一次性获得，逐段复用）。
    返回 (success: bool, error_msg: str)。
    """
    log_fn = log_fn or (lambda m: None)
    timeout = max(900, int(float(media_info.get("duration", 0) or 600) * 30))

    # 预期输出时长（供进度换算）：输入段时长 / 变速
    total_dur = float(media_info.get("duration", 0) or 0)
    start = ss + (float(snap.get("params", {}).get("trim_head", 0.0)) if ss == 0 else 0.0)
    seg_dur = in_duration if in_duration else max(0.5, total_dur - start
                                                  - float(snap.get("params", {}).get("trim_tail", 0.0)))
    speed = max(0.1, float(snap.get("params", {}).get("speed", 1.0)))
    expect_dur = seg_dur / speed

    cmd = build_command(input_path, output_path, snap, config, media_info,
                        use_nvenc=use_nvenc, ss=ss, in_duration=in_duration,
                        video_codec_args=video_codec_args,
                        target_kbps=target_kbps, norm_spec=norm_spec,
                        crop_rect=crop_rect)
    # 记录实际使用的编码器
    try:
        _cv = cmd.index("-c:v")
        log_fn(f"编码器: {cmd[_cv + 1]}")
    except (ValueError, IndexError):
        log_fn(f"编码器: {'h264_nvenc' if use_nvenc else 'libx264'}")
    r = run_ffmpeg(cmd, timeout=timeout, progress_cb=progress_cb,
                   total_duration=expect_dur)
    if r.returncode == 0:
        if progress_cb:
            try:
                progress_cb(1.0)
            except Exception:
                pass
        return True, ""

    err = (r.stderr or "")[-800:]
    # NVENC 失败（会话超限/驱动问题）→ 回退 CPU 重试一次
    _e = err.lower()
    if use_nvenc and "nvenc" in _e:
        log_fn(f"NVENC 编码失败，回退 CPU 重试: {err[-150:]}")
        cpu_args = spec_encode_args(snap, config, False, target_kbps, norm_spec)
        cmd2 = build_command(input_path, output_path, snap, config, media_info,
                             use_nvenc=False, ss=ss, in_duration=in_duration,
                             video_codec_args=cpu_args,
                             norm_spec=norm_spec, crop_rect=crop_rect)
        r2 = run_ffmpeg(cmd2, timeout=timeout)
        if r2.returncode == 0:
            return True, ""
        return False, (r2.stderr or "")[-500:]

    return False, err
