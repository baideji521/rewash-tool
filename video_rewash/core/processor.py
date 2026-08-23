# -*- coding: utf-8 -*-
"""core.processor — 单素材处理编排

v8.0 主流水线（最终改造方案）：
输入 → 探测 → 黑边检测 → [retry循环: 重生成随机参数 → 时序 → 空间 →
颜色 → 纹理 → 音频 → 编码 → 质检 → 指纹] → 输出。
随机参数在每次 retry 时重新生成，保证重试有效性。
FFmpeg 先写 .processing 临时文件，全部检查通过后才 rename。

核心链路: preset → randomizer → processor → ffmpeg_runner 在此收口。
"""
import os
import shutil
import tempfile
import time

from .ffmpeg_runner import run_ffmpeg, probe_media, STOP_EVENT, detect_black_crop
from .config import config_get, config_enabled
from .randomizer import generate_snapshot, snapshot_summary, log_parameter_calibration
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
    单素材完整处理（固定流水线）：
      探测 → 黑边检测 → [重试循环: 随机快照 → 时序→空间→颜色→纹理→音频→编码
                          → 质检 → 指纹] → 最终输出

    指纹重试在函数内部完成（读取 fingerprint.retry_max），每次重试
    重新生成随机参数。达到上限保留最后一次结果。

    返回 {"success","output","issues","snap","elapsed","fingerprint_sim","fp_pass"}
    """
    log_fn = log_fn or (lambda m: None)
    progress_cb = progress_cb or (lambda stage, frac: None)
    t0 = time.time()
    result = {"success": False, "output": output_path, "issues": [],
              "snap": None, "elapsed": 0.0, "fingerprint_sim": None,
              "fp_pass": True, "fp_attempts": 0}

    ffmpeg_path = config_get(config, "runtime.ffmpeg", "ffmpeg")
    ffprobe_path = config_get(config, "runtime.ffprobe", "ffprobe")

    final_output = output_path

    # ── 1. 媒体信息探测（缓存由 probe_media 内部保证，同文件不重复 ffprobe）──
    progress_cb("probe", 0.02)
    media = probe_media(ffprobe_path, input_path)
    if not media.get("has_video"):
        result["issues"].append("无法探测到视频流")
        log_fn("✗ 无法探测媒体信息")
        return result
    duration = media.get("duration", 0.0)

    # ── 2. 黑边检测（分析阶段只跑一次，内部带缓存；失败不阻断）──
    crop_rect = None
    if config_get(config, "video.black_crop.enable", False):
        if config_get(config, "video.black_crop.detect", True):
            crop_rect = detect_black_crop(ffmpeg_path, ffprobe_path, input_path)
            if crop_rect:
                cw, ch, cx, cy = crop_rect
                log_fn(f"黑边检测: crop={cw}:{ch}:{cx}:{cy}")
            else:
                log_fn("黑边检测：未发现黑边或检测失败，按原始尺寸继续")

    # ── 3. 体积对齐：预转码（一次性，不在重试循环内重复执行）──
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

    # ── 4. 指纹重试循环 ──
    # 每次迭代重新生成随机参数快照 → 处理 → 质检 → 指纹。
    # 指纹 FAIL 且未达上限：删当前输出，重新随机，重新处理。
    # 达到上限：保留最后一次结果。
    # 优化：重试时保留编码参数（crf/gop/bframes/sc_threshold），
    #       只重新随机视觉变换参数（scale/brightness/contrast/speed 等），
    #       因为编码参数不影响视觉指纹，避免无意义重复计算。
    fp_retry_max = max(0, int(config_get(config, "fingerprint.retry_max", 3) or 0))
    fp_thresh = float(config_get(config, "fingerprint.max_similarity", 0.70))
    fp_enabled = config_enabled(config, "fingerprint", True)
    fp_attempts = 0
    _encode_time = 0.0   # 累计编码耗时
    _fp_time = 0.0       # 累计指纹检测耗时
    _saved_encode_params = None  # 首次编码参数，重试时保留

    try:
        for attempt_idx in range(fp_retry_max + 1):
            fp_attempts += 1
            if STOP_EVENT.is_set():
                break
            is_last_attempt = (attempt_idx >= fp_retry_max)

            # 每次重试开始时，清除上一轮指纹重试累积的 issues
            if attempt_idx > 0:
                result["issues"] = [
                    iss for iss in result["issues"]
                    if "指纹相似度" not in iss and "超过阈值" not in iss
                    and "最大重试次数" not in iss
                ]
                result["fp_pass"] = True  # 重置，本轮重新判定

            # ── 4a. 生成本次随机参数快照（每次重试都重新生成）──
            snap = generate_snapshot(preset, config)
            # 首次编码后保存编码参数，供重试时恢复
            if attempt_idx == 0:
                _saved_encode_params = {
                    k: snap["params"].get(k)
                    for k in ("crf", "gop", "bframes", "sc_threshold")
                }
            elif _saved_encode_params:
                # 重试时保留编码参数，只重新随机视觉变换参数
                snap["params"].update(_saved_encode_params)
            result["snap"] = snap
            log_fn(f"使用预设：{snapshot_summary(snap)}"
                   + (f"  [重试 {attempt_idx + 1}/{fp_retry_max + 1}]"
                      if attempt_idx > 0 else ""))
            log_parameter_calibration(snap, log_fn)

            work_dir = tempfile.mkdtemp(prefix="rewash_work_")
            # FFmpeg 先写 .processing 临时文件，质检+指纹通过后再 rename
            # 命名: xxx.processing.mp4（不是 xxx.mp4.processing，FFmpeg 需要正确扩展名）
            _base = os.path.basename(final_output)
            _stem, _ext = os.path.splitext(_base)
            processing_path = os.path.join(
                work_dir, f"{_stem}.processing{_ext}")
            processed_ok = False

            try:
                # ── 4b. 独立处理（分段 / 整文件）──
                _enc_t0 = time.time()
                progress_cb("process", 0.05)
                seg_count = int(config_get(config, "segment_count", 4) or 4)
                normalize_on = config_enabled(config, "switches.normalize", True)
                norm_spec = get_target_spec(config) if normalize_on else None
                process_cb = lambda f: progress_cb("process", 0.05 + 0.63 * min(1.0, f))

                if seg_count > 1:
                    ok, err, _snaps = process_segmented(
                        work_input, processing_path, snap, preset, config, media,
                        use_nvenc=use_nvenc, log_fn=log_fn,
                        requested_count=seg_count, progress_cb=process_cb,
                        target_kbps=target_kbps, crop_rect=crop_rect)
                    if ok is None:  # 太短，走整文件
                        ok, err = process_clip(
                            work_input, processing_path, snap, config, media,
                            use_nvenc=use_nvenc, log_fn=log_fn,
                            progress_cb=process_cb,
                            target_kbps=target_kbps,
                            norm_spec=norm_spec,
                            crop_rect=crop_rect)
                else:
                    ok, err = process_clip(
                        work_input, processing_path, snap, config, media,
                        use_nvenc=use_nvenc, log_fn=log_fn,
                        progress_cb=process_cb,
                        target_kbps=target_kbps,
                        norm_spec=norm_spec,
                        crop_rect=crop_rect)
                if not ok:
                    result["issues"].append(f"处理失败: {err}")
                    log_fn(f"✗ 处理失败: {err[-200:]}")
                    return result  # 处理失败直接返回，不重试
                _encode_time += time.time() - _enc_t0
                progress_cb("process", 0.70)

                if normalize_on:
                    log_fn("输出标准化完成（已折入主处理，无额外遍）")
                processed_ok = True
            finally:
                if not processed_ok:
                    shutil.rmtree(work_dir, ignore_errors=True)

            if not processed_ok:
                return result

            # ── 4c. 输出质量检查 ──
            try:
                if config_enabled(config, "switches.quality_check", True):
                    progress_cb("quality_check", 0.90)
                    spec = get_target_spec(config)
                    normalized = config_enabled(
                        config, "switches.normalize", True)
                    if seg_count > 1:
                        exp_dur = duration
                    else:
                        exp_dur = max(
                            0.5, duration
                            - float(snap["params"].get("trim_head", 0))
                            - float(snap["params"].get("trim_tail", 0)))
                    exp = {
                        "duration": (exp_dur
                                     / float(snap["params"].get("speed", 1.0))
                                     * 0.98),
                        "has_audio": media.get("has_audio", False),
                        "src_size": (os.path.getsize(input_path)
                                     if os.path.exists(input_path) else 0),
                    }
                    if normalized:
                        exp.update({
                            "width": spec["width"] - spec["width"] % 2,
                            "height": spec["height"] - spec["height"] % 2,
                            "fps": spec["fps"],
                        })
                    passed, issues, _info = check_output(
                        processing_path, ffprobe_path, ffmpeg_path,
                        expected=exp)
                    if issues:
                        result["issues"].extend(issues)
                        log_fn("⚠ 质检问题: " + "; ".join(issues))
                    else:
                        log_fn("✓ 质量检测通过")
                    if not passed and not os.path.exists(processing_path):
                        return result  # 质检严重失败，文件不存在

                # ── 4d. 指纹相似度检查（针对最终编码完成的视频）──
                if fp_enabled:
                    try:
                        from ..fingerprint.detector import compare_similarity
                        _fp_t0 = time.time()
                        sim = compare_similarity(
                            input_path, processing_path, ffmpeg_path,
                            n_frames=int(config_get(
                                config, "fingerprint.sample_frames", 6)),
                            log_fn=log_fn)
                        _fp_time += time.time() - _fp_t0
                        result["fingerprint_sim"] = sim
                        if sim is not None:
                            if sim <= fp_thresh:
                                log_fn(
                                    f"✓ 指纹相似度: {sim:.3f}"
                                    f" (阈值 {fp_thresh:.2f}) PASS")
                                # 指纹通过，清除之前重试累积的指纹 issues
                                result["issues"] = [
                                    iss for iss in result["issues"]
                                    if "指纹相似度" not in iss
                                    and "超过阈值" not in iss
                                    and "最大重试次数" not in iss
                                ]
                                result["fp_pass"] = True
                            elif is_last_attempt:
                                result["fp_pass"] = False
                                log_fn(
                                    f"⚠ 指纹相似度: {sim:.3f}"
                                    f" > 阈值 {fp_thresh:.2f}，"
                                    f"已达到最大重试次数，保留最后结果")
                                result["issues"].append(
                                    f"已达最大重试次数，保留最后结果"
                                    f"（指纹 {sim:.3f}"
                                    f" > 阈值 {fp_thresh:.2f}）")
                            else:
                                result["fp_pass"] = False
                                result["issues"].append(
                                    f"指纹相似度 {sim:.3f}"
                                    f" 超过阈值 {fp_thresh:.2f}，"
                                    f"将重新生成随机参数重试")
                                log_fn(
                                    f"✗ 指纹相似度: {sim:.3f}"
                                    f" > 阈值 {fp_thresh:.2f} FAIL，"
                                    f"将重新随机重试")
                                progress_cb("fingerprint", 0.97)
                                # 删当前失败输出，重新生成随机参数重试
                                try:
                                    if os.path.exists(processing_path):
                                        os.remove(processing_path)
                                except OSError:
                                    pass
                                shutil.rmtree(work_dir, ignore_errors=True)
                                continue  # 下一次迭代：重新随机
                    except Exception as e:
                        log_fn(f"指纹检测跳过: {e}")

                progress_cb("done", 1.0)

                # ── 4e. 全部通过（或最后一次）→ rename 为最终文件 ──
                try:
                    dst_dir = os.path.dirname(final_output)
                    if dst_dir:
                        os.makedirs(dst_dir, exist_ok=True)
                    if os.path.exists(final_output):
                        os.remove(final_output)
                    shutil.move(processing_path, final_output)
                except OSError as e:
                    result["issues"].append(f"写入输出目录失败: {e}")
                    log_fn(f"✗ 写入输出目录失败: {e}")
            finally:
                # 无论成败，临时工作目录不残留
                shutil.rmtree(work_dir, ignore_errors=True)

            # 到达这里说明处理+质检+指纹（PASS 或最后一次）都完成了
            break
    finally:
        # 清理预转码临时文件
        if pre_tmp:
            try:
                if os.path.exists(pre_tmp):
                    os.remove(pre_tmp)
            except OSError:
                pass

    result["fp_attempts"] = fp_attempts
    result["success"] = os.path.exists(final_output)
    result["elapsed"] = round(time.time() - t0, 1)
    result["encode_time"] = round(_encode_time, 1)
    result["fp_time"] = round(_fp_time, 1)
    return result
