# -*- coding: utf-8 -*-
"""video.filters — 视频滤镜链构建

把参数快照翻译成 FFmpeg filtergraph。纯字符串构建，不执行命令，
便于测试与调试（打印即可审查完整滤镜链）。

观感安全设计（用户审查要求）：
- 动态渐变幅度小、周期长 → 无晕眩感
- 重复帧只插 0~4 帧 → 节奏影响可忽略（且输出过质检）
- v7.1：静态 rotate 已删除（与 rotate_drift 动态微旋功能重叠）
"""
import math

from ..core.config import config_get


def build_video_filter(snap: dict, width: int, height: int,
                       fps: float = 25.0, tag: str = "") -> str:
    """
    构建单个输入的视频滤镜链（trim/setpts 由 -ss 参数处理，不在此列）。
    width/height: 原始视频尺寸；fps: 用于时间表达式；tag: 多输入标签。
    返回逗号连接的滤镜串。
    """
    p = snap.get("params", {})
    filters = []

    # ── 几何：缩放（2 的倍数保证 yuv420p）──
    scale_base = float(p.get("scale", 1.0))
    tw = int(width * scale_base / 2) * 2
    th = int(height * scale_base / 2) * 2
    filters.append(f"scale={tw}:{th}:flags=bicubic")

    # 镜头微运动：周期性推镜渐变
    # 注：此 build 的 crop 无 eval 参数且 init 时无法求值含 t 的表达式，
    # 改用 zoompan（支持 in 帧号变量，probe 已验证可用）
    z_amp = float(p.get("zoom_drift_amp", 0.0))
    z_period = max(1.0, float(p.get("zoom_drift_period", 4.0)))
    if z_amp > 0.002:
        period_frames = max(2, int(z_period * fps))
        # in 为输入帧号；mod 内逗号需转义
        if p.get("zoom_drift_dir") == "in":
            z_expr = f"1+{z_amp:.4f}*mod(in\\,{period_frames})/{period_frames}"
        else:
            z_expr = f"1+{z_amp:.4f}*(1-mod(in\\,{period_frames})/{period_frames})"
        filters.append(
            f"zoompan=z='{z_expr}':d=1"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={tw}x{th}:fps={fps:.3f}"
        )

    # 动态微旋：正弦角度渐变
    r_amp = float(p.get("rotate_drift_amp", 0.0))
    r_period = max(1.0, float(p.get("rotate_drift_period", 3.0)))
    if r_amp > 0.05:
        filters.append(
            f"rotate='{r_amp:.3f}*{math.pi / 180:.6f}*sin(2*PI*t/{r_period:.2f})'"
            f":fillcolor=none"
        )

    # ── 色彩 ──
    eq_parts = []
    b = float(p.get("brightness", 0.0))
    c = float(p.get("contrast", 0.0))
    s = float(p.get("saturation", 0.0))
    if abs(b) > 0.01:
        eq_parts.append(f"brightness={b / 100.0:.4f}")
    if abs(c) > 0.01:
        eq_parts.append(f"contrast={1.0 + c / 100.0:.4f}")
    if abs(s) > 0.01:
        eq_parts.append(f"saturation={1.0 + s / 100.0:.4f}")
    if eq_parts:
        filters.append("eq=" + ":".join(eq_parts))

    hue = float(p.get("hue", 0.0))
    if abs(hue) > 0.01:
        filters.append(f"hue=h={hue:.2f}")

    cm = float(p.get("channel_mix", 0.0))
    if abs(cm) > 0.001:
        a = min(0.10, abs(cm)) * (1 if cm > 0 else -1)
        # 通道权重微偏移（行和保持为 1）
        filters.append(
            f"colorchannelmixer="
            f"rr={1 - a:.4f}:rg={a:.4f}:rb=0:"
            f"gr=0:gg={1 + a:.4f}:gb={-a:.4f}:"
            f"br={a:.4f}:bg=0:bb={1 - a:.4f}"
        )

    # ── 噪点（并入 film_grain，唯一噪声手段）──
    noise = float(p.get("noise", 0.0))
    if noise > 0.1:
        ns = max(1, min(30, int(round(noise * 3))))
        filters.append(f"noise=alls={ns}:allf=t+u")

    return ",".join(filters)


def build_frame_dup_complex(snap: dict, duration: float, src_label: str = "0:v") -> tuple:
    """
    重复帧插入（trim + tpad克隆 + concat 方案）：
    在指定位置克隆 n 帧（≈n/fps 秒冻结）。
    此 build 无 freeze 滤镜，tpad 只支持首尾，因此切三段拼接。
    返回 (filter_complex_str, 视频输出 label)，无重复帧返回 (None, src_label)。
    """
    p = snap.get("params", {})
    n = int(p.get("frame_dup", 0))
    if n <= 0 or duration <= 1.0:
        return None, src_label
    fps = max(1.0, float(p.get("_fps", 25.0) or 25.0))
    pos = min(0.85, max(0.15, float(p.get("frame_dup_pos", 0.5))))
    t_pos = duration * pos
    fc = (
        f"[{src_label}]split=2[d1][d2];"
        f"[d1]trim=end={t_pos:.3f},setpts=PTS-STARTPTS[v1];"
        f"[d2]trim=start={t_pos:.3f},setpts=PTS-STARTPTS,"
        f"tpad=start={n}:start_mode=clone[v2];"
        f"[v1][v2]concat=n=2:v=1:a=0[vout]"
    )
    return fc, "vout"


def build_audio_filter(snap: dict) -> str:
    """
    构建音频滤镜链（方案 2.2 不换BGM指纹破坏组合）。
    注意：atempo 单滤镜范围 0.5~2.0，这里取值恒在安全区间。
    """
    p = snap.get("params", {})
    filters = []

    atempo = float(p.get("audio_atempo", 1.0))
    atempo = min(2.0, max(0.5, atempo))
    if abs(atempo - 1.0) > 0.0005:
        filters.append(f"atempo={atempo:.5f}")

    pitch = float(p.get("audio_pitch", 0.0))
    if abs(pitch) > 0.01:
        rate = 2.0 ** (pitch / 12.0)
        # asetrate 变调 + aresample 回原采样率 + atempo 补偿时长
        sr = 44100
        filters.append(f"asetrate={int(sr * rate)}")
        filters.append(f"aresample={sr}")
        filters.append(f"atempo={1.0 / rate:.6f}")

    eq_bands = p.get("audio_eq_bands") or []
    eq_parts = []
    for band in eq_bands:
        try:
            f = float(band.get("freq", 1000))
            g = float(band.get("gain", 0.0))
            w = float(band.get("width", 800))
            if abs(g) > 0.01:
                eq_parts.append(f"frequency={f:.0f}:width_type=h:width={w:.0f}:gain={g:.2f}")
        except (TypeError, ValueError):
            continue
    if eq_parts:
        filters.append("equalizer=" + ":".join(eq_parts[:1]))
        for part in eq_parts[1:]:
            filters.append("equalizer=" + part)

    hp = int(p.get("audio_highpass", 0) or 0)
    if hp > 0:
        filters.append(f"highpass=f={hp}:poles=2")
    lp = int(p.get("audio_lowpass", 0) or 0)
    if lp > 0:
        filters.append(f"lowpass=f={lp}:poles=2")

    fade = float(p.get("audio_fade", 0.0) or 0.0)
    if fade > 0.05:
        filters.append(f"afade=t=in:d={fade:.2f}")

    return ",".join(filters)


# 标准化页可选编码器（实测捆绑 ffmpeg 全部可用）：key → (显示名, ffmpeg 编码器)
ENCODER_TABLE = {
    "h264_libx264": ("H.264 (libx264)", "libx264"),
    "h264_nvenc":   ("H.264 (NVIDIA NVENC)", "h264_nvenc"),
    "h264_qsv":     ("H.264 (Intel QSV)", "h264_qsv"),
    "h264_amf":     ("H.264 (AMD AMF)", "h264_amf"),
    "h265_libx265": ("H.265 (libx265)", "libx265"),
    "h265_nvenc":   ("H.265 (NVIDIA NVENC)", "hevc_nvenc"),
    "h265_qsv":     ("H.265 (Intel QSV)", "hevc_qsv"),
    "h265_amf":     ("H.265 (AMD AMF)", "hevc_amf"),
    "vp9_libvpx":   ("VP9 (libvpx-vp9)", "libvpx-vp9"),
    "av1_svtav1":   ("AV1 (libsvtav1)", "libsvtav1"),
    "av1_nvenc":    ("AV1 (NVIDIA NVENC)", "av1_nvenc"),
    "av1_qsv":      ("AV1 (Intel QSV)", "av1_qsv"),
    "av1_amf":      ("AV1 (AMD AMF)", "av1_amf"),
}


def get_encode_args(snap: dict, config: dict, use_nvenc: bool,
                    target_kbps: int = None) -> list:
    """
    编码参数（快照驱动）：GOP/B帧全随机。

    体积对齐（target_kbps 非空，默认路径）：按目标码率编码，
    输出体积 ≈ 源体积（用户要求：29MB 进 ≈ 29MB 出）。
      NVENC 用 cbr（体积最准），CPU 用 -b:v + maxrate/bufsize 约束。
    CRF 回退（探测不到源码率）：CRF 下限钳制 24，防低 QP 体积膨胀
    （实测 qp17 ≈ 11Mbps，272s ≈ 391MB）。
    """
    p = snap.get("params", {})
    crf = max(24, int(p.get("crf", 23)))
    gop = int(p.get("gop", 40))
    bf = int(p.get("bframes", 2))
    sc = int(p.get("sc_threshold", 30))

    if use_nvenc:
        args = [
            "-c:v", "h264_nvenc",
            "-preset", str(config_get(config, "encode.nvenc_preset", "p3")),
        ]
        if target_kbps:
            args += ["-rc", "cbr", "-b:v", f"{int(target_kbps)}k"]
        else:
            args += ["-rc", "constqp", "-qp", str(crf)]
        args += ["-g", str(gop), "-bf", str(bf)]
    else:
        args = [
            "-c:v", "libx264",
            "-preset", str(config_get(config, "encode.cpu_preset", "medium")),
        ]
        if target_kbps:
            t = int(target_kbps)
            args += ["-b:v", f"{t}k",
                     "-maxrate", f"{int(t * 1.5)}k", "-bufsize", f"{t * 3}k"]
        else:
            args += ["-crf", str(crf)]
        args += [
            "-g", str(gop), "-bf", str(bf),
            "-sc_threshold", str(sc),
            "-pix_fmt", "yuv420p",
        ]
    return args


def spec_encode_args(snap: dict, config: dict, use_nvenc: bool,
                     target_kbps: int = None, norm_spec: dict = None) -> list:
    """
    按标准化页显式选择的编码器生成编码参数（13 种：
    H.264/H.265/AV1 × libx264·libx265·libsvtav1·libvpx-vp9/NVENC/QSV/AMF）。
    兼容旧配置 h264/h265：按 use_nvenc 自动选 NVENC 或 CPU。
    体积对齐（target_kbps）按目标码率；否则 CRF/CQ 质量模式（下限 24）。
    """
    p = snap.get("params", {})
    crf = max(24, int(p.get("crf", 24)))
    gop = int(p.get("gop", 40))
    bf = int(p.get("bframes", 2))
    pix = str((norm_spec or {}).get("pix_fmt") or "yuv420p")
    nv_preset = str(config_get(config, "encode.nvenc_preset", "p3"))
    cpu_preset = str(config_get(config, "encode.cpu_preset", "medium"))

    key = str((norm_spec or {}).get("video_codec") or "h264")
    if key == "h264":   # 旧配置兼容
        key = "h264_nvenc" if use_nvenc else "h264_libx264"
    elif key == "h265":
        key = "h265_nvenc" if use_nvenc else "h265_libx265"
    if key not in ENCODER_TABLE:
        key = "h264_nvenc" if use_nvenc else "h264_libx264"
    enc = ENCODER_TABLE[key][1]

    if enc.endswith("_nvenc"):
        args = ["-c:v", enc, "-preset", nv_preset]
        if target_kbps:
            args += ["-rc", "cbr", "-b:v", f"{int(target_kbps)}k"]
        else:
            args += ["-rc", "constqp", "-qp", str(crf)]
        args += ["-g", str(gop)]
        if enc != "av1_nvenc":  # AV1 无 B 帧概念
            args += ["-bf", str(bf)]
        return args
    if enc.endswith("_qsv"):
        args = ["-c:v", enc, "-preset", "medium"]
        if target_kbps:
            t = int(target_kbps)
            args += ["-b:v", f"{t}k", "-maxrate", f"{int(t * 1.5)}k",
                     "-bufsize", f"{t * 3}k"]
        else:
            args += ["-global_quality", str(crf)]
        return args + ["-g", str(gop)]
    if enc.endswith("_amf"):
        args = ["-c:v", enc]
        if target_kbps:
            args += ["-rc", "cbr", "-b:v", f"{int(target_kbps)}k"]
        else:
            args += ["-quality", "balanced"]  # AMF 质量模式
        return args + ["-g", str(gop)]
    if enc == "libx264":
        args = ["-c:v", "libx264", "-preset", cpu_preset]
        if target_kbps:
            t = int(target_kbps)
            args += ["-b:v", f"{t}k",
                     "-maxrate", f"{int(t * 1.5)}k", "-bufsize", f"{t * 3}k"]
        else:
            args += ["-crf", str(crf)]
        return args + ["-g", str(gop), "-bf", str(bf), "-pix_fmt", pix]
    if enc == "libx265":
        args = ["-c:v", "libx265", "-preset", cpu_preset, "-pix_fmt", pix]
        if target_kbps:
            t = int(target_kbps)
            args += ["-b:v", f"{t}k",
                     "-maxrate", f"{int(t * 1.5)}k", "-bufsize", f"{t * 3}k"]
        else:
            args += ["-crf", str(crf)]
        return args + ["-g", str(gop)]
    if enc == "libvpx-vp9":
        args = ["-c:v", "libvpx-vp9", "-cpu-used", "4", "-pix_fmt", "yuv420p"]
        if target_kbps:
            args += ["-b:v", f"{int(target_kbps)}k"]
        else:
            args += ["-b:v", "0", "-crf", str(crf)]
        return args
    # libsvtav1
    args = ["-c:v", "libsvtav1", "-preset", "8", "-pix_fmt", "yuv420p"]
    if target_kbps:
        args += ["-b:v", f"{int(target_kbps)}k"]
    else:
        args += ["-crf", str(crf)]
    return args
