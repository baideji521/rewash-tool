# -*- coding: utf-8 -*-
"""core.randomizer — 本次处理参数快照生成

核心链路第二环：preset(范围) → randomizer(具体值快照) → processor。

设计要点：
- 每个参数独立随机，不做整体联动；"全开启"= 全部具备随机化能力，
  允许取到范围低端（温和取值），避免画质损伤累积。
- 对称参数（旋转/亮度等）先采样幅度再随机符号。
- 音频速度 = 视频速度 × 微偏因子，保证音画同步不漂移（观感安全）。
- 快照记录全部具体值，处理日志首行输出，便于追溯与复现（seed）。
"""
import random
import time


def _range_of(preset: dict, key: str, d_lo: float, d_hi: float):
    """从预设读 {min,max}，缺失用默认（永不崩溃）"""
    node = (preset.get("params", {}) or {}).get(key)
    if isinstance(node, dict):
        try:
            lo = float(node.get("min", d_lo))
            hi = float(node.get("max", d_hi))
            if lo > hi:
                lo, hi = hi, lo
            return lo, hi
        except (TypeError, ValueError):
            pass
    return d_lo, d_hi


def _signed(rng: random.Random, lo: float, hi: float) -> float:
    """幅度范围 + 随机符号"""
    mag = rng.uniform(lo, hi)
    return mag if rng.random() < 0.5 else -mag


# 高低通按档位的采样范围（方案 2.2 表）
_HIGHPASS_RANGE = {"gentle": (25, 35), "standard": (30, 50), "aggressive": (40, 60)}
_LOWPASS_RANGE = {"gentle": (18500, 19500), "standard": (17000, 18000), "aggressive": (15000, 17000)}


def generate_snapshot(preset: dict, config: dict = None, seed: int = None) -> dict:
    """
    输入预设（范围字典），输出本次处理的完整参数快照（具体值）。
    preset: {"name","label","builtin","params":{...}}
    """
    config = config or {}
    if seed is None:
        seed = int(time.time() * 1000) ^ random.getrandbits(31)
    rng = random.Random(seed)
    params = preset.get("params", {}) or {}
    pname = preset.get("name", "standard")

    snap = {
        "preset": pname,
        "preset_label": preset.get("label", pname),
        "seed": seed,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "params": {},
    }
    p = snap["params"]

    # ── 几何 ──
    # v7.1：静态 rotate 已删除（与 rotate_drift 动态微旋功能重叠），仅保留动态微旋
    lo, hi = _range_of(preset, "scale", 1.01, 1.04)
    p["scale"] = round(rng.uniform(lo, hi), 4)
    lo, hi = _range_of(preset, "trim", 0.2, 0.6)
    p["trim_head"] = round(rng.uniform(lo, hi), 3)
    p["trim_tail"] = round(rng.uniform(lo, hi), 3)
    # 镜头微运动（推镜渐变）
    zd = params.get("zoom_drift") or {}
    try:
        z_amp = rng.uniform(float(zd.get("amp_min", 0.01)), float(zd.get("amp_max", 0.03)))
    except (TypeError, ValueError):
        z_amp = rng.uniform(0.01, 0.03)
    p["zoom_drift_amp"] = round(z_amp, 4)
    p["zoom_drift_period"] = float(zd.get("period", 4.0) or 4.0)
    p["zoom_drift_dir"] = rng.choice(["in", "out"])
    # 动态微旋（正弦渐变）
    rd = params.get("rotate_drift") or {}
    try:
        r_amp = rng.uniform(float(rd.get("amp_min", 0.3)), float(rd.get("amp_max", 0.8)))
    except (TypeError, ValueError):
        r_amp = rng.uniform(0.3, 0.8)
    p["rotate_drift_amp"] = round(r_amp, 3)
    p["rotate_drift_period"] = float(rd.get("period", 3.0) or 3.0)

    # ── 色彩 ──
    for key, dlo, dhi in (("brightness", 1.5, 4.0), ("contrast", 1.5, 4.0),
                          ("saturation", 1.5, 4.0)):
        lo, hi = _range_of(preset, key, dlo, dhi)
        p[key] = round(_signed(rng, lo, hi), 2)
    lo, hi = _range_of(preset, "hue", 2.0, 5.0)
    p["hue"] = round(_signed(rng, lo, hi), 2)
    lo, hi = _range_of(preset, "channel_mix", 0.01, 0.03)
    p["channel_mix"] = round(_signed(rng, lo, hi), 4)
    lo, hi = _range_of(preset, "noise", 1.5, 3.0)
    p["noise"] = round(rng.uniform(lo, hi), 2)

    # ── 时序 ──
    lo, hi = _range_of(preset, "speed", 0.98, 1.02)
    p["speed"] = round(rng.uniform(lo, hi), 4)
    lo, hi = _range_of(preset, "frame_dup", 0, 2)
    p["frame_dup"] = int(rng.randint(int(lo), int(hi)))
    p["frame_dup_pos"] = round(rng.uniform(0.25, 0.75), 3)  # 插入位置（相对时长比例）
    lo, hi = _range_of(preset, "scene_jitter", 0, 2)
    p["scene_jitter"] = int(rng.randint(int(lo), int(hi)))

    # ── 音频 ──
    # 音频速度 = 视频速度 × 微偏因子 → 保证音画不漂移
    lo, hi = _range_of(preset, "audio_speed", 0.998, 1.002)
    p["audio_atempo"] = round(p["speed"] * rng.uniform(lo, hi), 5)
    lo, hi = _range_of(preset, "audio_pitch", 0.3, 0.8)
    p["audio_pitch"] = round(_signed(rng, lo, hi), 3)  # 半音
    lo, hi = _range_of(preset, "audio_eq", 1.0, 2.0)
    p["audio_eq_bands"] = [
        {"freq": f, "gain": round(_signed(rng, lo, hi), 2), "width": w}
        for f, w in ((180, 200), (1000, 900), (6000, 4000))
    ]
    hp_lo, hp_hi = _HIGHPASS_RANGE.get(pname, (30, 50))
    lp_lo, lp_hi = _LOWPASS_RANGE.get(pname, (17000, 18000))
    p["audio_highpass"] = int(rng.uniform(hp_lo, hp_hi))
    p["audio_lowpass"] = int(rng.uniform(lp_lo, lp_hi))
    av_node = params.get("av_offset") or {}
    if av_node.get("enable", False):
        lo, hi = _range_of(preset, "av_offset", 0.05, 0.1)
        p["av_offset"] = round(_signed(rng, lo, hi), 3)
    else:
        p["av_offset"] = 0.0
    p["audio_noise_db"] = -rng.uniform(45, 50) if pname == "aggressive" else None
    # 首尾淡入淡出与裁剪联动
    p["audio_fade"] = min(0.5, max(0.1, p["trim_head"]))

    # ── 编码（压缩域扰动，优先级低于画面变换）──
    lo, hi = _range_of(preset, "crf", 20, 26)
    p["crf"] = int(rng.randint(int(lo), int(hi)))
    lo, hi = _range_of(preset, "gop", 25, 55)
    p["gop"] = int(rng.randint(int(lo), int(hi)))
    p["bframes"] = rng.randint(1, 3)
    p["sc_threshold"] = rng.randint(20, 50)

    # ── 可选增强（默认关；开启后参数随机）──
    extra = (config.get("extra") or {})
    snap["extra"] = {}
    if extra.get("minterpolate", {}).get("enable", False):
        snap["extra"]["minterpolate"] = {
            "target_fps": int(extra.get("minterpolate", {}).get("target_fps", 60))
        }
    if extra.get("wave_displace", {}).get("enable", False):
        wd = extra.get("wave_displace", {})
        amp_lo, amp_hi = 1, 3
        per_lo, per_hi = 2, 5
        try:
            amp_lo, amp_hi = float(wd.get("amp_px", {}).get("min", 1)), float(wd.get("amp_px", {}).get("max", 3))
            per_lo, per_hi = float(wd.get("period_sec", {}).get("min", 2)), float(wd.get("period_sec", {}).get("max", 5))
        except (TypeError, ValueError):
            pass
        snap["extra"]["wave_displace"] = {
            "amp_px": round(rng.uniform(amp_lo, amp_hi), 2),
            "period_sec": round(rng.uniform(per_lo, per_hi), 2),
            "horizontal": rng.random() < 0.5,
        }
    if extra.get("region_split", {}).get("enable", False):
        rs = extra.get("region_split", {})
        snap["extra"]["region_split"] = {
            "regions": rng.randint(2, 4),
            "var_pct": round(rng.uniform(2, 5), 2),
        }

    return snap


def snapshot_summary(snap: dict) -> str:
    """日志首行摘要（方案 3.6：处理开始记录预设与参数）"""
    p = snap.get("params", {})
    return (
        f"预设={snap.get('preset_label')} seed={snap.get('seed')} "
        f"scale={p.get('scale')} rot_drift={p.get('rotate_drift_amp')}° "
        f"speed={p.get('speed')} bright={p.get('brightness')}% "
        f"crf={p.get('crf')} gop={p.get('gop')}"
    )
