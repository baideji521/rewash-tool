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
import random

from .randomizer import generate_snapshot
from .ffmpeg_runner import run_ffmpeg, probe_media, STOP_EVENT
from .config import config_get, config_enabled
from .normalize import get_target_spec
from ..video.video_processor import process_clip, build_temporal_pre
from ..video.filters import (spec_encode_args, build_spatial_chain,
                             build_geq_filter, build_audio_filter,
                             build_frame_dup_complex)


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


def process_single_pass(input_path, output_path, base_snap, config,
                        media_info, use_nvenc, planned, log_fn,
                        progress_cb=None, target_kbps=None,
                        norm_spec=None, crop_rect=None) -> tuple:
    """单进程 filter_complex：一次解码 → 每支路（trim+独立滤镜链）→ concat
    → 单次编码，消除旧方案 6 进程 + 中间文件 + 合并重编码遍。
    planned: [(seg_start, seg_end, seg_snap), ...]，各段独立快照参数不变。
    返回 (success, error_msg)；失败由调用方自动降级旧分段路径。
    av_offset 等效：旧方案 -itsoffset 作用于整输入（音视频同步平移，
    相对同步不变），此处两支路 trim 窗口同步平移，语义一致。
    """
    ffmpeg_path = config_get(config, "runtime.ffmpeg", "ffmpeg")
    ffprobe_path = config_get(config, "runtime.ffprobe", "ffprobe")
    width = int(media_info.get("width", 0) or 0)
    height = int(media_info.get("height", 0) or 0)
    fps = float(media_info.get("fps", 25.0) or 25.0)
    has_audio = bool(media_info.get("has_audio", False))
    do_norm = norm_spec is not None
    eff_fps = float(norm_spec.get("fps", fps)) if norm_spec else fps
    n = len(planned)

    v_parts, a_parts, v_lbls, a_lbls = [], [], [], []
    exp_dur = 0.0
    for i, (seg_start, seg_end, snap) in enumerate(planned):
        seg_len = seg_end - seg_start
        p = snap.get("params", {})
        speed = max(0.1, float(p.get("speed", 1.0)))
        exp_dur += seg_len / speed
        av = float(p.get("av_offset", 0.0) or 0.0)
        t0 = max(0.0, seg_start - av)
        t1 = max(t0 + 0.05, seg_end - av)

        # 视频支路：时序前置(降帧/抽帧/变速) + 空间/颜色/纹理 + 标准化 + geq
        pre = build_temporal_pre(snap, config, seg_len, fps, eff_fps, norm_spec)
        vf = build_spatial_chain(snap, width, height, eff_fps,
                                 norm_spec=norm_spec, crop_rect=crop_rect,
                                 config=config)
        vf = (",".join(pre) + "," if pre else "") + vf
        if norm_spec:
            tw = int(norm_spec.get("width", 1280))
            th = int(norm_spec.get("height", 720))
            tw, th = tw - tw % 2, th - th % 2
            # setpts 规整化：zoompan(d=1) 输出时间戳不规则，直接接下游
            # fps 会触发病态缓冲（实测 >120s）；先对齐目标帧率网格，
            # fps 变为零成本直通（实测同图 3.6s）
            nf = norm_spec.get("fps", 30)
            vf += (f",setpts=N/{nf}/TB,fps={nf},"
                   f"format={norm_spec.get('pix_fmt', 'yuv420p')},setsar=1")
            geq = build_geq_filter(snap, tw, th)
            if geq:
                vf += "," + geq
        else:
            # 无标准化：各段 scale 独立 → 拼前统一回源分辨率/重置 SAR（与合并遍同语义）
            w2, h2 = width - width % 2, height - height % 2
            if w2 > 0 and h2 > 0:
                vf += f",scale={w2}:{h2}:flags=bicubic"
            vf += ",setsar=1,format=yuv420p"
        v_parts.append(
            f"[0:v]trim=start={t0:.3f}:end={t1:.3f},"
            f"setpts=PTS-STARTPTS,{vf}[vm{i}]")
        cur_v = f"[vm{i}]"
        # 重复帧插入（段级复杂图，标签加后缀防碰撞）
        if int(p.get("frame_dup", 0)) > 0 and seg_len > 1.0:
            dup_fc, _ = build_frame_dup_complex(snap, seg_len / speed, f"vm{i}")
            if dup_fc:
                for a_, b_ in (("[d1]", f"[d1_{i}]"), ("[d2]", f"[d2_{i}]"),
                               ("[v1]", f"[fv1_{i}]"), ("[v2]", f"[fv2_{i}]"),
                               ("[vout]", f"[v{i}]")):
                    dup_fc = dup_fc.replace(a_, b_)
                v_parts.append(dup_fc)
                cur_v = f"[v{i}]"
        v_lbls.append(cur_v)

        # 音频支路：atrim + 段独立音频滤镜链（变速/变调/EQ/高低通/淡入）
        if has_audio:
            af = build_audio_filter(snap)
            a_chain = (f"[0:a]atrim=start={t0:.3f}:end={t1:.3f},"
                       f"asetpts=PTS-STARTPTS" + (f",{af}" if af else ""))
            noise_db = p.get("audio_noise_db")
            if noise_db:
                # 激进档极低音量粉噪混音（与旧方案 amix 参数一致）
                amp = 10 ** (float(noise_db) / 20)
                a_parts.append(
                    f"{a_chain}[av{i}];"
                    f"anoisesrc=c=pink:a={amp:.7f}[an{i}];"
                    f"[av{i}][an{i}]amix=inputs=2:duration=first"
                    f":dropout_transition=0[a{i}]")
            else:
                a_parts.append(f"{a_chain}[a{i}]")
            a_lbls.append(f"[a{i}]")

    if has_audio:
        cat_in = "".join(f"{v_lbls[i]}{a_lbls[i]}" for i in range(n))
        fc = ";".join(v_parts + a_parts) + \
             f";{cat_in}concat=n={n}:v=1:a=1[vout][aout]"
        maps = ["-map", "[vout]", "-map", "[aout]"]
    else:
        cat_in = "".join(v_lbls)
        fc = ";".join(v_parts) + f";{cat_in}concat=n={n}:v=1:a=0[vout]"
        maps = ["-map", "[vout]"]

    # 音频编码参数（与 build_audio_args 同规则：seed+17 随机码率）
    audio_codec = str(config_get(config, "normalize.audio_codec", "aac"))
    rng = random.Random(int(base_snap.get("seed", 0)) + 17)
    if audio_codec == "mp3":
        a_enc = ["-c:a", "libmp3lame", "-b:a",
                 f"{rng.choice([128, 160, 192])}k"]
    else:
        br = (rng.randint(192, 256)
              if base_snap.get("preset", "standard") == "gentle"
              else rng.randint(128, 256))
        a_enc = ["-c:a", "aac", "-b:a", f"{br}k"]

    # 单次编码：主快照编码参数 + 体积对齐码率；NVENC 失败回退 CPU 重试一次
    err = ""
    ran = False
    for nv in ([True, False] if use_nvenc else [False]):
        v_enc = _merge_encode_args(base_snap, config, nv, do_norm, target_kbps)
        cmd = ([ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
                "-i", input_path, "-filter_complex", fc] + maps + v_enc +
               (a_enc if has_audio else ["-an"]) +
               ["-movflags", "+faststart", output_path])
        try:
            _cv = cmd.index("-c:v")
            log_fn(f"编码器: {cmd[_cv + 1]}")
        except (ValueError, IndexError):
            pass
        r = run_ffmpeg(cmd, timeout=1800, progress_cb=progress_cb,
                       total_duration=exp_dur)
        ran = True
        if r.returncode == 0:
            break
        if r.returncode == -15:
            return False, "stopped"
        err = (r.stderr or "")[-500:]
        if nv and "nvenc" not in err.lower():
            return False, err  # 非 NVENC 问题，回退也大概率同样失败
        if nv:
            log_fn("单进程 NVENC 失败，回退 CPU 重试")
    if not ran:
        return False, "未执行"
    if r.returncode != 0:
        return False, err

    # 结果校验：存在/非空/时长/分辨率/音频流，任一不满足 → 失败降级
    if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
        return False, "输出文件缺失或为空"
    info = probe_media(ffprobe_path, output_path)
    issues = []
    out_dur = float(info.get("duration", 0.0) or 0.0)
    if out_dur < exp_dur * 0.90 or out_dur > exp_dur * 1.10:
        issues.append(f"时长 {out_dur:.1f}s ≠ 预期 {exp_dur:.1f}s")
    if norm_spec:
        tw = int(norm_spec.get("width", 0))
        th = int(norm_spec.get("height", 0))
        tw, th = tw - tw % 2, th - th % 2
        if int(info.get("width", 0)) != tw or int(info.get("height", 0)) != th:
            issues.append(
                f"分辨率 {info.get('width')}x{info.get('height')} ≠ {tw}x{th}")
    if has_audio and not info.get("has_audio"):
        issues.append("音频流缺失")
    if issues:
        try:
            os.remove(output_path)
        except OSError:
            pass
        return False, "; ".join(issues)
    return True, ""


def process_segmented(input_path, output_path, base_snap, preset, config,
                      media_info, use_nvenc, log_fn=None,
                      requested_count=None, progress_cb=None,
                      target_kbps=None, crop_rect=None) -> tuple:
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

    # ── 优先：单进程 filter_complex（一次解码/一次编码，无合并遍无中间文件）──
    # 各段独立快照先生成（与降级路径同 seed 公式，参数完全一致）
    planned = []
    for i in range(n):
        seg_start, seg_end = cuts[i], cuts[i + 1]
        if seg_end - seg_start < 0.8:
            continue
        planned.append((seg_start, seg_end,
                        _child_snapshot(base_snap, preset, config, i)))
    if len(planned) >= 2:
        log_fn(f"分段策略：单进程 filter_complex × {len(planned)} 支路")
        sp_ok, sp_err = False, ""
        try:
            sp_ok, sp_err = process_single_pass(
                input_path, output_path, base_snap, config, media_info,
                use_nvenc, planned, log_fn, progress_cb=progress_cb,
                target_kbps=target_kbps, norm_spec=seg_norm_spec,
                crop_rect=crop_rect)
        except Exception as e:
            sp_err = f"构建异常: {e}"
        if sp_ok:
            return True, "", [s for _, _, s in planned]
        if STOP_EVENT.is_set() or sp_err == "stopped":
            return False, sp_err or "stopped", [s for _, _, s in planned]
        log_fn(f"⚠ 单进程路径失败，自动降级分段独立编码: {sp_err[-160:]}")
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            pass
        # ── 降级：旧方案 6 段独立编码 + 重编码合并（下方原路径）──

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
                target_kbps=target_kbps, norm_spec=seg_norm_spec,
                crop_rect=crop_rect)
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
        cmd = build_cmd(_merge_encode_args(snap, config, nv, do_norm,
                                            target_kbps))
        # 记录合并编码器
        try:
            _cv = cmd.index("-c:v")
            log_fn(f"合并编码器: {cmd[_cv + 1]}")
        except (ValueError, IndexError):
            pass
        r = run_ffmpeg(cmd, timeout=1800)
        if r.returncode == 0:
            return True, ""
        err = (r.stderr or "").lower()[-500:]
        if nv and "nvenc" not in err:
            break  # 非 NVENC 问题，回退也大概率同样失败，不重试
    return False, err
