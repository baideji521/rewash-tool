# -*- coding: utf-8 -*-
"""core._graph — 段分支构建（单进程 filtergraph 共享逻辑）

单进程路径（segment.process_single_pass）与降级路径
（video_processor.build_command, seg_idx>0）共用的段内滤镜分支构建。
所有分支都只产生 filter_complex 片段，不产生中间编码。

plan: generate_segment_plan 输出（段内局部坐标，输出时间轴/变速后空间）；
时间窗口在输入空间使用时统一除以 speed 换算。
"""
import random

from .config import config_get
from ..video.filters import (build_reverse_loop_complex, frame_drop_positions,
                             build_frame_drop_expr, build_zoom_window_complex,
                             build_rotate_filter, build_frame_dup_complex,
                             build_audio_filter, build_geq_filter)


def _rl_snap(p: dict, rl: dict) -> dict:
    """段事件 reverse_loop 参数 → 快照形式（复用整文件 rl 构建器）"""
    snap = {"params": dict(p)}
    snap["params"].update({
        "rl_mode": rl.get("mode"),
        "rl_pos_rel": rl.get("pos_rel", 0.5),
        "rl_seg_len": rl.get("seg_len", 0.15),
        "rl_repeats": rl.get("repeats", 2),
    })
    return snap


def rl_extra_seconds(plan: dict, speed: float) -> float:
    """loop 事件增加的输出时长（秒）：(repeats-1) × 片段长 / 变速"""
    rl = (plan or {}).get("reverse_loop") or {}
    if rl.get("mode") == "loop":
        return (int(rl.get("repeats", 2)) - 1) * float(rl.get("seg_len", 0.15)) \
               / max(0.1, speed)
    return 0.0


def frame_drop_chain(snap: dict, config: dict, seg_idx: int,
                     n_frames: int, fwin: dict, eff_fps: float) -> str:
    """抽帧窗口 → select 表达式（含时戳重排）；未触发/窗口无删帧返回空串。
    fwin 窗口为输出时间轴坐标（秒），× eff_fps 换算成帧号；
    select 按帧号计数，变速不改变帧号 → 语义与旧版全片抽帧一致。"""
    if not fwin or not fwin.get("on"):
        return ""
    fd_cfg = config_get(config, "video.frame_drop", {}) or {}
    fd_iv = fd_cfg.get("interval", {}) or {}
    try:
        fd_lo, fd_hi = int(fd_iv.get("min", 100)), int(fd_iv.get("max", 200))
    except (TypeError, ValueError):
        fd_lo, fd_hi = 100, 200
    rng_fd = random.Random(int(snap.get("seed", 0)) + 29 + seg_idx)
    drops = frame_drop_positions(
        n_frames, fd_lo, fd_hi, rng_fd,
        window=(float(fwin.get("start", 0.0)) * eff_fps,
                (float(fwin.get("start", 0.0))
                 + float(fwin.get("dur", 0.0))) * eff_fps))
    return build_frame_drop_expr(drops)


def build_segment_branch(src_v, snap, plan, config, seg_idx,
                         seg_len_in, speed, eff_fps, norm_spec,
                         tw, th, suffix="", pre_chain=""):
    """段内视频分支（B/C 层事件窗口）。
    调用方约定：输入流已处于 eff_fps（fps 降档在公共链头部，
    避免重滤镜按源帧率计算）。pre_chain 非空时先接在 rl 之后
    （整文件/降级路径的 几何+畸变+颜色 公共链）。
    顺序：rl事件 → pre_chain → 抽帧窗口 → 变速 → 推镜窗口(切段)
    → 微旋窗口(timeline) → 标准化尾 → 重复帧。
    返回 (fc_parts, out_label)。
    """
    p = snap.get("params", {})
    sfx = suffix
    parts, cur = [], src_v

    # ① 倒放/循环事件（A+B×n+C 切拼，段内相对位置）
    rl = (plan or {}).get("reverse_loop") or {}
    if rl.get("mode") and seg_len_in >= 2.0:
        fc, v_lbl, _ = build_reverse_loop_complex(
            _rl_snap(p, rl), seg_len_in, False,
            src_v=src_v.strip("[]"), suffix=sfx)
        if fc:
            parts.append(fc)
            cur = v_lbl

    # ② 公共链接入（整文件/降级路径：几何+畸变+颜色；分段模式为空）
    if pre_chain:
        lbl = f"vp{sfx}"
        parts.append(f"{cur}{pre_chain}[{lbl}]")
        cur = f"[{lbl}]"

    chain = []
    # ③ 抽帧窗口（窗口单位：帧，调用方已换算）
    fd_expr = frame_drop_chain(
        snap, config, seg_idx, int(seg_len_in * eff_fps),
        (plan or {}).get("frame_drop") or {}, eff_fps)
    if fd_expr:
        chain.append(fd_expr)
    # ④ 变速
    if abs(speed - 1.0) > 0.0005:
        chain.append(f"setpts={1.0 / speed:.6f}*PTS")
    if chain:
        lbl = f"vb{sfx}"
        parts.append(f"{cur}{','.join(chain)}[{lbl}]")
        cur = f"[{lbl}]"

    # ⑤ 推镜窗口（无 timeline 支持 → 窗口切段，仅窗口段过 zoompan）
    zwin = (plan or {}).get("zoom") or {}
    if zwin.get("on"):
        z_in = {"on": True, "start": zwin["start"] / speed,
                "dur": zwin["dur"] / speed}
        zp_parts, z_out = build_zoom_window_complex(
            cur, z_in, p, eff_fps, tw, th, suffix=sfx)
        if zp_parts:
            parts.extend(zp_parts)
            cur = z_out

    # ⑥ 微旋窗口（rotate timeline：窗口外零开销；段内相位独立）
    rwin = (plan or {}).get("rotate") or {}
    tail = []
    if rwin.get("on"):
        p_rot = dict(p)
        p_rot["rotate_drift_phase"] = 0.0
        rot = build_rotate_filter(
            p_rot,
            f"between(t,{rwin['start'] / speed:.3f},"
            f"{(rwin['start'] + rwin['dur']) / speed:.3f})")
        if rot:
            tail.append(rot)

    # ⑦ 标准化尾（时戳规整 + 目标帧率/像素格式；无标准化时回源分辨率）
    if norm_spec:
        nf = int(norm_spec.get("fps", 30))
        tail += [f"setpts=N/{nf}/TB", f"fps={nf}",
                 f"format={norm_spec.get('pix_fmt', 'yuv420p')}", "setsar=1"]
        geq = build_geq_filter(snap, tw, th)
        if geq:
            tail.append(geq)
    else:
        tail += [f"scale={tw}:{th}:flags=bicubic", "setsar=1",
                 "format=yuv420p"]
    lbl = f"vm{sfx}"
    parts.append(f"{cur}{','.join(tail)}[{lbl}]")
    cur = f"[{lbl}]"

    # ⑧ 重复帧插入（段级复杂图，标签加后缀防碰撞）
    if int(p.get("frame_dup", 0)) > 0 and seg_len_in / speed > 1.0:
        p2 = dict(p)
        p2["_fps"] = eff_fps
        dup_fc, _ = build_frame_dup_complex({"params": p2},
                                            seg_len_in / speed,
                                            cur.strip("[]"))
        if dup_fc:
            for a_, b_ in (("[d1]", f"[d1{sfx}]"), ("[d2]", f"[d2{sfx}]"),
                           ("[v1]", f"[fv1{sfx}]"), ("[v2]", f"[fv2{sfx}]"),
                           ("[vout]", f"[v{sfx}]")):
                dup_fc = dup_fc.replace(a_, b_)
            parts.append(dup_fc)
            cur = f"[v{sfx}]"
    return parts, cur


def build_segment_audio(src_a, snap, plan, seg_len_in, speed, suffix=""):
    """段内音频分支：atrim → rl 音频(与视频同切点) → af → 混噪。
    返回 (fc_parts, out_label)。rl 切点公式与 build_reverse_loop_complex
    完全一致（同参数同结果），只内联音频支路避免视频标签碰撞。"""
    sfx = suffix
    parts = [f"{src_a}asetpts=PTS-STARTPTS[ab{sfx}]"]
    cur = f"[ab{sfx}]"
    rl = (plan or {}).get("reverse_loop") or {}
    p = snap.get("params", {})
    if rl.get("mode") and seg_len_in >= 2.0:
        mode = rl.get("mode")
        d = float(rl.get("seg_len", 0.15))
        t1 = float(rl.get("pos_rel", 0.5)) * seg_len_in
        t1 = max(0.05, min(t1, seg_len_in - d - 0.1))
        t2 = min(t1 + d, seg_len_in - 0.05)
        if t2 - t1 >= 0.05:
            n_rep = int(rl.get("repeats", 2)) if mode == "loop" else 1
            n_rep = max(1, min(3, n_rep))
            parts += [
                f"[ab{sfx}]asplit=3[s1{sfx}][s2{sfx}][s3{sfx}]",
                f"[s1{sfx}]atrim=end={t1:.3f},asetpts=PTS-STARTPTS[aA{sfx}]",
                f"[s2{sfx}]atrim=start={t1:.3f}:end={t2:.3f},"
                f"asetpts=PTS-STARTPTS"
                f"{',areverse' if mode == 'reverse' else ''}[aB{sfx}]",
                f"[s3{sfx}]atrim=start={t2:.3f},asetpts=PTS-STARTPTS[aC{sfx}]",
            ]
            if n_rep > 1:
                parts.append(f"[aB{sfx}]asplit={n_rep}" +
                             "".join(f"[ab{i}{sfx}]" for i in range(n_rep)))
                ain = (f"[aA{sfx}]" +
                       "".join(f"[ab{i}{sfx}]" for i in range(n_rep)) +
                       f"[aC{sfx}]")
                parts.append(f"{ain}concat=n={n_rep + 2}:v=0:a=1[at{sfx}]")
            else:
                parts.append(f"[aA{sfx}][aB{sfx}][aC{sfx}]"
                             f"concat=n=3:v=0:a=1[at{sfx}]")
            cur = f"[at{sfx}]"
    af = build_audio_filter(snap)
    if af:
        parts.append(f"{cur}{af}[af{sfx}]")
        cur = f"[af{sfx}]"
    noise_db = p.get("audio_noise_db")
    if noise_db:
        amp = 10 ** (float(noise_db) / 20)
        parts.append(f"anoisesrc=c=pink:a={amp:.7f}[an{sfx}]")
        parts.append(f"{cur}[an{sfx}]amix=inputs=2:duration=first"
                     f":dropout_transition=0[a{sfx}]")
        cur = f"[a{sfx}]"
    return parts, cur
