# -*- coding: utf-8 -*-
"""core.normalize — 输出标准化

统一输出规格（方案 3.3）：比例/分辨率/fps/像素格式/编码均可配置。
scale 适配 + pad 居中填充，不拉伸变形。
"""
from ..core.ffmpeg_runner import run_ffmpeg
from ..core.config import config_get
from ..video.filters import spec_encode_args


def _parse_aspect(ar) -> tuple:
    """解析比例字符串 "3:4" → (3.0, 4.0)；无效/原始比例返回 None"""
    if not ar or not isinstance(ar, str) or ar.strip() in ("", "原始比例"):
        return None
    parts = ar.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        rw, rh = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if rw <= 0 or rh <= 0:
        return None
    return rw, rh


def get_target_spec(config: dict) -> dict:
    """读取标准化目标规格（全部有默认值）。
    画面比例全量对接：所选比例真实约束输出——若宽高与所选比例不符，
    以宽为基准修正高度（偶数对齐）；选「原始比例」则不约束。"""
    w = int(config_get(config, "normalize.width", 1080))
    h = int(config_get(config, "normalize.height", 1440))
    ar = _parse_aspect(config_get(config, "normalize.aspect_ratio", None))
    if ar:
        rw, rh = ar
        target = rw / rh
        if w > 0 and h > 0 and abs(w / h - target) / target > 0.01:
            h = max(2, int(round(w * rh / rw)) // 2 * 2)
    return {
        "width": w,
        "height": h,
        "fps": int(config_get(config, "normalize.fps", 30)),
        "pix_fmt": str(config_get(config, "normalize.pix_fmt", "yuv420p")),
        "video_codec": str(config_get(config, "normalize.video_codec", "h264")),
        "audio_codec": str(config_get(config, "normalize.audio_codec", "aac")),
    }


def normalize_output(input_path, output_path, snap, config, media_info,
                     use_nvenc=True, log_fn=None, target_kbps=None) -> tuple:
    """
    标准化输出。返回 (success, error_msg)。
    target_kbps: 体积对齐目标码率（None=CRF 回退）。
    """
    log_fn = log_fn or (lambda m: None)
    ffmpeg_path = config_get(config, "runtime.ffmpeg", "ffmpeg")
    spec = get_target_spec(config)
    w, h = spec["width"], spec["height"]
    # 保证偶数（yuv420p 要求）
    w, h = w - w % 2, h - h % 2

    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=bicubic,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
          f"fps={spec['fps']},format={spec['pix_fmt']}")

    v_args = spec_encode_args(snap, config, use_nvenc, target_kbps, spec)

    a_codec = ["-c:a", "aac", "-b:a", "192k"] if spec["audio_codec"] == "aac" \
        else ["-c:a", "libmp3lame", "-b:a", "192k"]

    cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
           "-i", input_path,
           "-vf", vf, "-map", "0:v", "-map", "0:a?",
           *v_args, *a_codec,
           "-movflags", "+faststart", output_path]
    r = run_ffmpeg(cmd, timeout=1200)
    if r.returncode == 0:
        return True, ""
    # NVENC 失败回退
    if use_nvenc and "nvenc" in (r.stderr or "").lower():
        log_fn("标准化 NVENC 失败，回退 CPU")
        fallback = {"video_codec": "h264_libx264", "pix_fmt": spec["pix_fmt"]}
        v_args = spec_encode_args(snap, config, False, target_kbps, fallback)
        cmd[cmd.index("-map")] = "-map"  # 结构不变，仅换编码参数
        cmd = cmd[:cmd.index("-vf")] + ["-vf", vf, "-map", "0:v", "-map", "0:a?"] + \
            v_args + a_codec + ["-movflags", "+faststart", output_path]
        r2 = run_ffmpeg(cmd, timeout=1200)
        if r2.returncode == 0:
            return True, ""
        return False, (r2.stderr or "")[-500:]
    return False, (r.stderr or "")[-500:]
