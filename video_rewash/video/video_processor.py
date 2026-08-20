# -*- coding: utf-8 -*-
"""video.video_processor — 单段/单文件视频处理执行

核心链路第三环：processor 调用本模块把快照落地为 FFmpeg 命令并执行。
NVENC 失败自动回退 CPU（Bug#11/12 经验：消费级 GPU 会话数有限）。
"""
from ..core.ffmpeg_runner import run_ffmpeg
from ..core.config import config_get
from .filters import build_video_filter, build_frame_dup_complex, spec_encode_args
from ..audio.audio_processor import build_audio_args


def build_command(input_path, output_path, snap, config, media_info,
                  use_nvenc=True, ss=0.0, in_duration=None,
                  video_codec_args=None, target_kbps=None,
                  norm_spec=None) -> list:
    """
    构建完整 ffmpeg 命令。
    ss: 输入侧起始偏移（秒）；in_duration: 输入侧时长（秒，None=整段）。
    video_codec_args: 覆盖编码参数（二次编码时使用）。
    target_kbps: 体积对齐目标视频码率（None=CRF 回退）。
    norm_spec: 标准化目标规格（scale/pad/fps 折入主处理一遍完成，免单独标准化遍）。
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

    # 视频滤镜链（标准化折入时按目标帧率构建）
    vf = build_video_filter(snap, width, height, eff_fps)
    # 提速关键：目标帧率先于 zoompan 生效，zoompan 只算目标帧率的帧
    # （fps 放在 zoompan 后无效，zoompan 仍逐帧处理源帧率）
    if norm_spec and eff_fps < fps:
        vf = f"fps={eff_fps:.3f}," + vf
    speed = float(p.get("speed", 1.0))
    if abs(speed - 1.0) > 0.0005:
        vf = (vf + "," if vf else "") + f"setpts={1.0 / speed:.6f}*PTS"
    # 标准化折入主处理：scale适配+pad+fps 接在去重滤镜后，一遍完成
    if norm_spec:
        tw = int(norm_spec.get("width", 1280))
        th = int(norm_spec.get("height", 720))
        tw -= tw % 2
        th -= th % 2
        fp = norm_spec.get("fps", 30)
        pix = str(norm_spec.get("pixel_format", "yuv420p"))
        vf = ((vf + ",") if vf else "") + (
            f"scale={tw}:{th}:force_original_aspect_ratio=decrease:flags=bicubic,"
            f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={fp},format={pix}")

    # 音频参数
    audio_codec = str(config_get(config, "normalize.audio_codec", "aac"))
    in_pre, a_args, audio_mixed = build_audio_args(snap, has_audio, audio_codec)
    if in_pre:
        # -itsoffset 必须在 -i 之前：重排命令
        i_idx = cmd.index("-i")
        cmd = cmd[:i_idx] + in_pre + cmd[i_idx:]

    frame_dup_n = int(p.get("frame_dup", 0))
    use_complex = (frame_dup_n > 0 and seg_dur > 1.0) or audio_mixed

    if use_complex:
        parts = []
        if frame_dup_n > 0 and seg_dur > 1.0:
            parts.append(f"[0:v]{vf}[vm]")
            dup_fc, _ = build_frame_dup_complex(snap, seg_dur / max(speed, 0.1), "vm")
            if dup_fc:
                parts.append(dup_fc)
                v_out = "[vout]"
            else:
                v_out = "[vm]"
        else:
            parts.append(f"[0:v]{vf}[vout]")
            v_out = "[vout]"
        if audio_mixed and has_audio:
            # 从 a_args 中提取 filter_complex 段
            for i, a in enumerate(a_args):
                if a == "-filter_complex":
                    parts.append(a_args[i + 1])
                    a_args = a_args[:i] + a_args[i + 2:]
                    break
        cmd += ["-filter_complex", ";".join(parts), "-map", v_out]
        if has_audio:
            cmd += ["-map", "[amix]" if audio_mixed else "0:a"]
    else:
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-map", "0:v"]
        if has_audio:
            cmd += ["-map", "0:a"]

    cmd += a_args if has_audio else ["-an"]

    # 视频编码（尊重标准化页编码器选择：h265 → hevc_nvenc/libx265）
    cmd += video_codec_args if video_codec_args else spec_encode_args(
        snap, config, use_nvenc, target_kbps, norm_spec)
    cmd += ["-movflags", "+faststart", output_path]
    return cmd


def process_clip(input_path, output_path, snap, config, media_info,
                 use_nvenc=True, log_fn=None, ss=0.0, in_duration=None,
                 video_codec_args=None, progress_cb=None,
                 target_kbps=None, norm_spec=None) -> tuple:
    """
    执行处理，NVENC 失败自动回退 CPU。
    progress_cb(frac 0~1)：本段编码实时进度。
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
                        target_kbps=target_kbps, norm_spec=norm_spec)
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
                             norm_spec=norm_spec)
        r2 = run_ffmpeg(cmd2, timeout=timeout)
        if r2.returncode == 0:
            return True, ""
        return False, (r2.stderr or "")[-500:]

    return False, err
