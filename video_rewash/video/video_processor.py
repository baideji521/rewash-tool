# -*- coding: utf-8 -*-
"""video.video_processor — 单段/单文件视频处理执行

核心链路第三环：processor 调用本模块把快照落地为 FFmpeg 命令并执行。
NVENC 失败自动回退 CPU（Bug#11/12 经验：消费级 GPU 会话数有限）。

流水线顺序固定：时序（裁剪/倒放循环/抽帧/变速）→ 空间（黑边裁剪/缩放/旋转）
→ 颜色 → 纹理 → 音频 → 一次最终编码，中间不反复编码。
"""
from ..core.ffmpeg_runner import run_ffmpeg
from ..core.config import config_get
from ..core.randomizer import plan_lens_events, generate_segment_plan
from ..core._graph import (build_segment_branch, build_segment_audio,
                           rl_extra_seconds, frame_drop_chain)
from .filters import (build_geom_chain, build_color_chain,
                      build_lens_filter, build_lens_enable_expr,
                      build_rotate_filter, build_geq_filter,
                      build_reverse_loop_complex, spec_encode_args,
                      build_audio_filter)
from ..audio.audio_processor import build_audio_args


def build_command(input_path, output_path, snap, config, media_info,
                  use_nvenc=True, ss=0.0, in_duration=None,
                  video_codec_args=None, target_kbps=None,
                  norm_spec=None, crop_rect=None, seg_idx=0) -> list:
    """
    构建完整 ffmpeg 命令（v8.1 事件窗口版）。
    ss: 输入侧起始偏移（秒）；in_duration: 输入侧时长（秒，None=整段）。
    seg_idx: 段序号（0=整文件；>0=降级分段，接分段级事件窗口，
             与单进程路径同 seed 公式，窗口位置可复现）。
    链序：rl(整文件) → fps降档 → 几何 → 畸变事件窗口 → 颜色 → 抽帧窗口
    → 变速 → 推镜窗口(切段) → 微旋窗口(enable) → 标准化尾 → 重复帧。
    推镜窗口触发时强制 filter_complex（zoompan 无 timeline，切段直通+拼接）。
    """
    ffmpeg_path = config_get(config, "runtime.ffmpeg", "ffmpeg")
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
    speed = max(0.1, float(p.get("speed", 1.0)))

    # ══ B/C 层事件规划（段内局部坐标，变速后空间）══
    plan = generate_segment_plan(snap, config, seg_idx, seg_dur / speed)
    if seg_idx == 0 and p.get("rl_mode"):
        plan["reverse_loop"] = {"mode": None}  # 整文件用快照级 rl，避免双触发
    zwin = plan.get("zoom") or {}
    zoom_on = bool(zwin.get("on")) and float(p.get("zoom_drift_amp", 0.0)) > 0.002
    rl_plan_on = bool((plan.get("reverse_loop") or {}).get("mode")) and seg_dur >= 2.0

    # ① 时序：倒放/循环（快照级，仅整文件模式；分段模式用事件级 rl）
    rl_fc, v_in = None, ""
    if (p.get("rl_mode") and ss == 0 and in_duration is None
            and seg_dur >= 2.0):
        rl_fc, v_lbl, _a_unused = build_reverse_loop_complex(
            snap, seg_dur, has_audio)
        if rl_fc:
            v_in = v_lbl  # 后续滤镜接在 [vt] 上

    # ② A 层公共链：降帧 → 几何 → 畸变事件窗口（仅整文件）→ 颜色
    geom, tw, th = build_geom_chain(snap, width, height,
                                    norm_spec=norm_spec, crop_rect=crop_rect)
    common = (f"fps={eff_fps:.3f},"
              if norm_spec and eff_fps + 1e-6 < fps else "") + geom
    lens_events = (plan_lens_events(snap, config, seg_dur / speed)
                   if seg_idx == 0 else [])
    lens = build_lens_filter(p, build_lens_enable_expr(lens_events)) \
        if lens_events else ""
    if lens:
        common += "," + lens
    color = build_color_chain(snap, config)
    if color:
        common += "," + color

    # 音频参数（-itsoffset / -af / 混噪 / 编码参数）
    audio_codec = str(config_get(config, "normalize.audio_codec", "aac"))
    in_pre, a_args, audio_mixed = build_audio_args(snap, has_audio, audio_codec)
    if in_pre:
        # -itsoffset 必须在 -i 之前：重排命令
        i_idx = cmd.index("-i")
        cmd = cmd[:i_idx] + in_pre + cmd[i_idx:]

    frame_dup_n = int(p.get("frame_dup", 0))
    fdup_active = frame_dup_n > 0 and seg_dur / speed > 1.0
    use_complex = (bool(rl_fc) or zoom_on or fdup_active or audio_mixed
                   or (seg_idx > 0 and rl_plan_on))
    fd_applied = False

    if use_complex:
        parts = []
        cur = v_in if rl_fc else "[0:v]"
        if rl_fc:
            parts.append(rl_fc)
        # 分支：公共链 → 抽帧窗口 → 变速 → 推镜窗口 → 微旋窗口 → 标准化尾 → 重复帧
        b_parts, v_out = build_segment_branch(
            cur, snap, plan, config, seg_idx, seg_dur, speed,
            eff_fps, norm_spec, tw, th, suffix="", pre_chain=common)
        parts.extend(b_parts)
        fd_applied = any("select=" in x for x in b_parts)

        # 音频：分段降级路径且段内 rl 事件触发 → 音频同切点切拼；
        # 整文件 rl 时音频接 [at]；其余沿用既有 -af / 混噪参数。
        if has_audio:
            if seg_idx > 0 and rl_plan_on:
                a_parts, a_cur = build_segment_audio(
                    "[0:a]", snap, plan, seg_dur, speed, suffix="")
                parts.extend(a_parts)
                # af/混噪已在分支内完成，剥离 a_args 里的对应段保留编码参数
                for key in ("-af", "-filter_complex"):
                    for i, a in enumerate(a_args):
                        if a == key:
                            a_args = a_args[:i] + a_args[i + 2:]
                            break
                a_map = a_cur
            elif rl_fc:
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
            elif audio_mixed:
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
        # 简单链（无切段类事件）：全部折入 -vf
        vf = common
        fd_expr = frame_drop_chain(snap, config, seg_idx,
                                   int(seg_dur * eff_fps),
                                   plan.get("frame_drop") or {}, eff_fps)
        if fd_expr:
            vf += "," + fd_expr
            fd_applied = True
        if abs(speed - 1.0) > 0.0005:
            vf += f",setpts={1.0 / speed:.6f}*PTS"
        rwin = plan.get("rotate") or {}
        if rwin.get("on"):
            p_rot = dict(p)
            p_rot["rotate_drift_phase"] = 0.0
            rot = build_rotate_filter(
                p_rot,
                f"between(t,{rwin['start'] / speed:.3f},"
                f"{(rwin['start'] + rwin['dur']) / speed:.3f})")
            if rot:
                vf += "," + rot
        if norm_spec:
            fp = norm_spec.get("fps", 30)
            pix = str(norm_spec.get("pix_fmt", "yuv420p"))
            vf += f",fps={fp},format={pix}"
            geq = build_geq_filter(snap, tw, th)
            if geq:
                vf += "," + geq
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-map", "0:v"]
        if has_audio:
            cmd += ["-map", "0:a"]

    cmd += a_args if has_audio else ["-an"]
    # 抽帧后视频略短于音频：按视频长度对齐收尾，防止尾部音画错位
    if fd_applied and has_audio:
        cmd += ["-shortest"]

    # 视频编码（尊重标准化页编码器选择：h265 → hevc_nvenc/libx265）
    cmd += video_codec_args if video_codec_args else spec_encode_args(
        snap, config, use_nvenc, target_kbps, norm_spec)
    cmd += ["-movflags", "+faststart", output_path]
    return cmd


def process_clip(input_path, output_path, snap, config, media_info,
                 use_nvenc=True, log_fn=None, ss=0.0, in_duration=None,
                 video_codec_args=None, progress_cb=None,
                 target_kbps=None, norm_spec=None, crop_rect=None,
                 seg_idx=0) -> tuple:
    """
    执行处理，NVENC 失败自动回退 CPU。
    progress_cb(frac 0~1)：本段编码实时进度。
    crop_rect: 黑边检测矩形（分析阶段一次性获得，逐段复用）。
    seg_idx: 段序号（0=整文件；>0=降级分段，接分段级事件窗口）。
    返回 (success: bool, error_msg: str)。
    """
    log_fn = log_fn or (lambda m: None)
    timeout = max(900, int(float(media_info.get("duration", 0) or 600) * 30))

    # 预期输出时长（供进度换算）：输入段时长 / 变速 + loop 事件增量
    total_dur = float(media_info.get("duration", 0) or 0)
    start = ss + (float(snap.get("params", {}).get("trim_head", 0.0)) if ss == 0 else 0.0)
    seg_dur = in_duration if in_duration else max(0.5, total_dur - start
                                                  - float(snap.get("params", {}).get("trim_tail", 0.0)))
    speed = max(0.1, float(snap.get("params", {}).get("speed", 1.0)))
    expect_dur = seg_dur / speed
    plan = generate_segment_plan(snap, config, seg_idx, expect_dur,
                                 log_fn=log_fn)
    if seg_idx == 0 and snap.get("params", {}).get("rl_mode"):
        plan["reverse_loop"] = {"mode": None}
    expect_dur += rl_extra_seconds(plan, speed)

    cmd = build_command(input_path, output_path, snap, config, media_info,
                        use_nvenc=use_nvenc, ss=ss, in_duration=in_duration,
                        video_codec_args=video_codec_args,
                        target_kbps=target_kbps, norm_spec=norm_spec,
                        crop_rect=crop_rect, seg_idx=seg_idx)
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
                             norm_spec=norm_spec, crop_rect=crop_rect,
                             seg_idx=seg_idx)
        r2 = run_ffmpeg(cmd2, timeout=timeout)
        if r2.returncode == 0:
            return True, ""
        return False, (r2.stderr or "")[-500:]

    return False, err
