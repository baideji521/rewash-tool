# -*- coding: utf-8 -*-
"""core.processor — 单素材处理编排

v7.0 主流水线（用户审定架构）：
输入素材 → 媒体信息探测 → 参数快照 → 独立处理（几何/色彩/时序/音频/编码）
→ 输出标准化(折入重编码) → 质量检测 → 指纹质检报告 → 最终输出

核心链路: preset → randomizer → processor → ffmpeg_runner 在此收口。
"""
import os
import shutil
import tempfile
import time

from .ffmpeg_runner import run_ffmpeg, probe_media
from .config import config_get, config_enabled
from .randomizer import generate_snapshot, snapshot_summary
from .segment import process_segmented
from .normalize import get_target_spec
from .quality_check import check_output
from ..video.video_processor import process_clip


def _video_target_kbps(media: dict):
    """体积对齐：从源总码率扣除音频，得到目标视频码率(kbps)。探测不到返回 None"""
    try:
        total = int(media.get("bit_rate", 0) or 0)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    a_bps = int(media.get("a_bit_rate", 0) or 0)
    if a_bps <= 0 and media.get("has_audio"):
        a_bps = 128000  # 探测不到音频码率时按常见值估
    return max(200, (total - a_bps) // 1000)


def _pre_transcode(input_path: str, target_kbps: int, config: dict,
                   use_nvenc: bool, log_fn) -> str:
    """预转码：源码率与配置码率不匹配时，先对齐到目标码率再进冲洗流程。
    成功返回临时文件路径，失败返回 None（调用方直接用原文件）"""
    ffmpeg_path = config_get(config, "runtime.ffmpeg", "ffmpeg")
    tmp = os.path.join(tempfile.gettempdir(),
                       f"rewash_align_{os.getpid()}_{int(time.time())}.mp4")
    t = int(target_kbps)
    if use_nvenc:
        v_args = ["-c:v", "h264_nvenc",
                  "-preset", str(config_get(config, "encode.nvenc_preset", "p3")),
                  "-rc", "cbr", "-b:v", f"{t}k"]
    else:
        v_args = ["-c:v", "libx264",
                  "-preset", str(config_get(config, "encode.cpu_preset", "medium")),
                  "-b:v", f"{t}k", "-maxrate", f"{int(t * 1.5)}k",
                  "-bufsize", f"{t * 3}k", "-pix_fmt", "yuv420p"]
    cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
           "-i", input_path, "-map", "0:v", "-map", "0:a?"] + v_args + \
          ["-c:a", "copy", "-movflags", "+faststart", tmp]
    r = run_ffmpeg(cmd, timeout=1800)
    if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        return tmp
    # NVENC 失败回退 CPU 再试一次
    if use_nvenc and "nvenc" in (r.stderr or "").lower():
        v_args = ["-c:v", "libx264", "-preset", "medium",
                  "-b:v", f"{t}k", "-maxrate", f"{int(t * 1.5)}k",
                  "-bufsize", f"{t * 3}k", "-pix_fmt", "yuv420p"]
        cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
               "-i", input_path, "-map", "0:v", "-map", "0:a?"] + v_args + \
              ["-c:a", "copy", "-movflags", "+faststart", tmp]
        r2 = run_ffmpeg(cmd, timeout=1800)
        if r2.returncode == 0 and os.path.exists(tmp):
            return tmp
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
    log_fn(f"预转码失败，将直接按目标码率编码: {(r.stderr or '')[-120:]}")
    return None


def process_one(input_path: str, output_path: str, preset: dict, config: dict,
                use_nvenc: bool = True, log_fn=None, progress_cb=None) -> dict:
    """
    单素材完整处理。
    返回 {"success","output","issues","snap","elapsed","fingerprint_sim"}
    """
    log_fn = log_fn or (lambda m: None)
    progress_cb = progress_cb or (lambda stage, frac: None)
    t0 = time.time()
    result = {"success": False, "output": output_path, "issues": [],
              "snap": None, "elapsed": 0.0, "fingerprint_sim": None}

    ffmpeg_path = config_get(config, "runtime.ffmpeg", "ffmpeg")
    ffprobe_path = config_get(config, "runtime.ffprobe", "ffprobe")

    # 用户要求：冲洗成功前不进输出目录 —— 全程在临时目录处理，
    # 全部检查通过后才移入 output_path
    final_output = output_path

    # 1. 媒体信息探测
    progress_cb("probe", 0.02)
    media = probe_media(ffprobe_path, input_path)
    if not media.get("has_video"):
        result["issues"].append("无法探测到视频流")
        log_fn("✗ 无法探测媒体信息")
        return result
    duration = media.get("duration", 0.0)

    # 2. 体积对齐：目标码率 = 用户配置码率(kbps) 优先，否则自动对齐源码率
    #    （29MB 进 ≈ 29MB 出）；源码率与配置值差异>15% 时先跑预转码对齐
    cfg_kbps = int(config_get(config, "normalize.bitrate_kbps", 0) or 0)
    src_v_kbps = _video_target_kbps(media)
    target_kbps = cfg_kbps if cfg_kbps > 0 else src_v_kbps
    work_input, pre_tmp = input_path, None
    if cfg_kbps > 0 and src_v_kbps and abs(src_v_kbps - cfg_kbps) > cfg_kbps * 0.15:
        log_fn(f"源码率 {src_v_kbps}kbps ≠ 配置 {cfg_kbps}kbps，先跑预转码对齐...")
        pre_tmp = _pre_transcode(input_path, cfg_kbps, config, use_nvenc, log_fn)
        if pre_tmp:
            work_input = pre_tmp
            log_fn(f"预转码完成 → {cfg_kbps}kbps")
    if target_kbps:
        if cfg_kbps > 0:
            log_fn(f"体积对齐：目标视频码率 {target_kbps}kbps（配置指定）")
        else:
            log_fn(f"体积对齐：目标视频码率 {target_kbps}kbps（输出体积≈源体积）")

    # 3. 生成本次处理参数快照
    snap = generate_snapshot(preset, config)
    result["snap"] = snap
    log_fn(f"使用预设：{snapshot_summary(snap)}")

    work_dir = tempfile.mkdtemp(prefix="rewash_work_")
    output_path = os.path.join(work_dir, os.path.basename(final_output))
    processed_ok = False
    try:
        # 4. 独立处理（分段 / 整文件）
        # v7.1 提速：标准化(scale/pad/fps)折入主处理一遍完成，不再单独一遍
        progress_cb("process", 0.05)
        seg_count = int(config_get(config, "segment_count", 4) or 4)
        normalize_on = config_enabled(config, "switches.normalize", True)
        norm_spec = get_target_spec(config) if normalize_on else None
        process_cb = lambda f: progress_cb("process", 0.05 + 0.63 * min(1.0, f))
        worked_path = output_path
        segmented_used = False
        if seg_count > 1:
            ok, err, _snaps = process_segmented(
                work_input, output_path, snap, preset, config, media,
                use_nvenc=use_nvenc, log_fn=log_fn,
                requested_count=seg_count, progress_cb=process_cb,
                target_kbps=target_kbps)
            if ok is None:  # 太短，走整文件
                ok, err = process_clip(work_input, output_path, snap, config, media,
                                       use_nvenc=use_nvenc, log_fn=log_fn,
                                       progress_cb=process_cb,
                                       target_kbps=target_kbps,
                                       norm_spec=norm_spec)
            else:
                segmented_used = bool(ok)
        else:
            ok, err = process_clip(work_input, output_path, snap, config, media,
                                   use_nvenc=use_nvenc, log_fn=log_fn,
                                   progress_cb=process_cb,
                                   target_kbps=target_kbps,
                                   norm_spec=norm_spec)
        if not ok:
            result["issues"].append(f"处理失败: {err}")
            log_fn(f"✗ 处理失败: {err[-200:]}")
            return result
        progress_cb("process", 0.70)

        # 5. 输出标准化：已折入主处理（norm_spec）/分段合并，无独立遍
        if normalize_on:
            log_fn("输出标准化完成（已折入主处理，无额外遍）")
        processed_ok = True
    finally:
        # 清理预转码临时文件
        if pre_tmp:
            try:
                if os.path.exists(pre_tmp):
                    os.remove(pre_tmp)
            except OSError:
                pass
        # 处理失败：临时工作目录直接清掉，输出目录不留任何文件
        if not processed_ok:
            shutil.rmtree(work_dir, ignore_errors=True)

    # 6. 质量检测（重复帧/删帧/偏移等手段的兜底）
    try:
        if config_enabled(config, "switches.quality_check", True):
            progress_cb("quality_check", 0.90)
            spec = get_target_spec(config)
            normalized = config_enabled(config, "switches.normalize", True)
            # 期望时长：分段模式下 -ss 只平移窗口不缩短（每段保全长），
            # 整文件模式才扣首尾裁剪
            if segmented_used:
                exp_dur = duration
            else:
                exp_dur = max(0.5, duration
                              - float(snap["params"].get("trim_head", 0))
                              - float(snap["params"].get("trim_tail", 0)))
            exp = {
                "duration": exp_dur / float(snap["params"].get("speed", 1.0)) * 0.98,
                "has_audio": media.get("has_audio", False),
                "src_size": os.path.getsize(input_path) if os.path.exists(input_path) else 0,
            }
            if normalized:
                exp.update({"width": spec["width"] - spec["width"] % 2,
                            "height": spec["height"] - spec["height"] % 2,
                            "fps": spec["fps"]})
            passed, issues, _info = check_output(output_path, ffprobe_path,
                                                 ffmpeg_path, expected=exp)
            if issues:
                result["issues"].extend(issues)
                log_fn("⚠ 质检问题: " + "; ".join(issues))
            else:
                log_fn("✓ 质量检测通过")
            if not passed and not os.path.exists(output_path):
                return result

        # 8. 指纹相似度（质检工具，仅报告不阻断）
        if config_enabled(config, "fingerprint", True):
            try:
                from ..fingerprint.detector import compare_similarity
                sim = compare_similarity(input_path, output_path, ffmpeg_path,
                                         n_frames=int(config_get(config, "fingerprint.sample_frames", 8)))
                result["fingerprint_sim"] = sim
                thresh = float(config_get(config, "fingerprint.max_similarity", 0.70))
                if sim is not None:
                    mark = "✓" if sim < thresh else "⚠"
                    log_fn(f"{mark} 指纹相似度: {sim:.3f} (阈值 {thresh:.2f})")
            except Exception as e:
                log_fn(f"指纹检测跳过: {e}")

        progress_cb("done", 1.0)

        # 9. 全部通过 → 从临时目录移入输出目录（失败则输出目录不留任何文件）
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            try:
                dst_dir = os.path.dirname(final_output)
                if dst_dir:
                    os.makedirs(dst_dir, exist_ok=True)
                if os.path.exists(final_output):
                    os.remove(final_output)
                shutil.move(output_path, final_output)
            except OSError as e:
                result["issues"].append(f"写入输出目录失败: {e}")
                log_fn(f"✗ 写入输出目录失败: {e}")
    finally:
        # 无论成败/意外异常，临时工作目录都不残留
        shutil.rmtree(work_dir, ignore_errors=True)

    result["success"] = os.path.exists(final_output)
    result["elapsed"] = round(time.time() - t0, 1)
    return result
