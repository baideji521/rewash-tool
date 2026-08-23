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
import math
import random
import time

from .config import config_get

# 全片级参数（A 层）：一个视频任务开始时随机一次，分段不重新随机。
# 分段/降级路径的子快照必须从主快照继承这些键（段间色调/构图连续）。
GLOBAL_PARAM_KEYS = (
    "scale", "brightness", "contrast", "saturation", "hue",
    "channel_mix", "noise",
    "asym_crop_l", "asym_crop_r", "asym_crop_t", "asym_crop_b",
    "lens_k1", "lens_k2", "lens_cx", "lens_cy",
)


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


def _zero_pair(a, b) -> bool:
    """统一规则：min = max = 0 → 功能自动关闭。
    必须严格用 == 0 判断（不能用 if not a 之类假值判断），
    否则 0~5 / -5~0 会被误判为关闭。"""
    try:
        return float(a) == 0.0 and float(b) == 0.0
    except (TypeError, ValueError):
        return False


def _zero_range(preset: dict, key: str) -> bool:
    """预设节点 {min, max} 为 0~0 → 该功能自动关闭"""
    node = (preset.get("params", {}) or {}).get(key)
    if isinstance(node, dict):
        return _zero_pair(node.get("min"), node.get("max"))
    return False


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
    # 0~0 → 关闭缩放扰动（恒等值 1.0；0 不是合法缩放，必须显式拦截）
    p["scale"] = 1.0 if _zero_range(preset, "scale") else round(rng.uniform(lo, hi), 4)
    lo, hi = _range_of(preset, "trim", 0.2, 0.6)
    p["trim_head"] = round(rng.uniform(lo, hi), 3)
    p["trim_tail"] = round(rng.uniform(lo, hi), 3)
    # 镜头微运动（推镜渐变）；配置 video.zoom_drift.enable 可关闭（默认开）
    zd = params.get("zoom_drift") or {}
    zd_cfg = (config.get("video") or {}).get("zoom_drift") or {}
    if zd_cfg.get("enable", True):
        # 统一规则：推镜幅度 0~0 → 关闭推镜（代码保留，幅度置 0 后下游自动跳过 zoompan）
        if _zero_pair(zd.get("amp_min"), zd.get("amp_max")):
            z_amp = 0.0
        else:
            try:
                z_amp = rng.uniform(float(zd.get("amp_min", 0.01)), float(zd.get("amp_max", 0.03)))
            except (TypeError, ValueError):
                z_amp = rng.uniform(0.01, 0.03)
        p["zoom_drift_amp"] = round(z_amp, 4)
        p["zoom_drift_period"] = float(zd.get("period", 4.0) or 4.0)
        p["zoom_drift_dir"] = rng.choice(["in", "out"])
    else:
        p["zoom_drift_amp"] = 0.0
        p["zoom_drift_period"] = 4.0
        p["zoom_drift_dir"] = "in"
    # 动态微旋（正弦渐变 + 单向恒速漂移）
    # 统一规则：幅度 0~0 → 关闭正弦摆动；周期 0~0 → 周期随机关闭（用固定默认）；
    # 速度 0~0 → 关闭恒速漂移。幅度与速度均为 0 时下游自动跳过 rotate 滤镜
    # （微旋代码完整保留，仅取值置零）
    rd = params.get("rotate_drift") or {}
    if _zero_pair(rd.get("amp_min"), rd.get("amp_max")):
        r_amp = 0.0
    else:
        try:
            r_amp = rng.uniform(float(rd.get("amp_min", 0.3)), float(rd.get("amp_max", 0.8)))
        except (TypeError, ValueError):
            r_amp = rng.uniform(0.3, 0.8)
    p["rotate_drift_amp"] = round(r_amp, 3)
    if _zero_pair(rd.get("period_min"), rd.get("period_max")):
        r_per = 8.0
    else:
        try:
            r_per = rng.uniform(float(rd.get("period_min", 3.0)), float(rd.get("period_max", 6.0)))
        except (TypeError, ValueError):
            r_per = rng.uniform(3.0, 6.0)
    p["rotate_drift_period"] = round(r_per, 2)
    # 微旋速度：单向恒速漂移（°/s），符号随机 → 左旋或右旋；0~0 → 关闭漂移
    if _zero_pair(rd.get("speed_min"), rd.get("speed_max")):
        r_spd = 0.0
    else:
        try:
            r_spd = rng.uniform(float(rd.get("speed_min", 0.02)), float(rd.get("speed_max", 0.08)))
        except (TypeError, ValueError):
            r_spd = rng.uniform(0.02, 0.08)
        r_spd = r_spd if rng.random() < 0.5 else -r_spd
    p["rotate_drift_speed"] = round(r_spd, 4)
    # 随机初始相位（0~2π）→ 每段正弦波起点不同，避免分段同角度起步
    p["rotate_drift_phase"] = round(rng.uniform(0, 2 * math.pi), 4)

    # ── 非对称构图扰动（轻度裁剪，左右/上下独立随机）──
    ac = (config.get("video") or {}).get("asymmetric_crop") or {}
    if ac.get("enable", False):
        try:
            ac_lo = float(ac.get("min", 0.02))
            ac_hi = float(ac.get("max", 0.04))
        except (TypeError, ValueError):
            ac_lo, ac_hi = 0.02, 0.04
        p["asym_crop_l"] = round(rng.uniform(ac_lo, ac_hi), 4)
        p["asym_crop_r"] = round(rng.uniform(ac_lo, ac_hi), 4)
        p["asym_crop_t"] = round(rng.uniform(ac_lo, ac_hi), 4)
        p["asym_crop_b"] = round(rng.uniform(ac_lo, ac_hi), 4)
    else:
        p["asym_crop_l"] = p["asym_crop_r"] = 0.0
        p["asym_crop_t"] = p["asym_crop_b"] = 0.0

    # ── 极轻空间畸变（lenscorrection 镜头畸变）──
    ld = (config.get("video") or {}).get("lens_distortion") or {}
    if ld.get("enable", False):
        try:
            k1_max = float(ld.get("k1_range", 0.015))
            k2_max = float(ld.get("k2_range", 0.005))
        except (TypeError, ValueError):
            k1_max, k2_max = 0.015, 0.005
        p["lens_k1"] = round(rng.uniform(-k1_max, k1_max), 5)
        p["lens_k2"] = round(rng.uniform(-k2_max, k2_max), 5)
        p["lens_cx"] = round(rng.uniform(0.45, 0.55), 3)
        p["lens_cy"] = round(rng.uniform(0.45, 0.55), 3)
    else:
        p["lens_k1"] = p["lens_k2"] = 0.0
        p["lens_cx"], p["lens_cy"] = 0.5, 0.5

    # ── 局部动态扰动（geq 高斯位移漂移）──
    md = (config.get("video") or {}).get("mask_drift") or {}
    if md.get("enable", False):
        try:
            md_str = int(md.get("strength", 2))
        except (TypeError, ValueError):
            md_str = 2
        # 漂移振幅 1~3 像素（按 strength 缩放），周期 8~20s
        md_amp = round(rng.uniform(1, 2 + md_str) , 2)
        p["mask_drift_amp"] = md_amp
        p["mask_drift_period"] = round(rng.uniform(8.0, 20.0), 2)
        p["mask_drift_cx"] = round(rng.uniform(0.25, 0.75), 3)  # 归一化中心 X
        p["mask_drift_cy"] = round(rng.uniform(0.25, 0.75), 3)  # 归一化中心 Y
        p["mask_drift_radius"] = round(rng.uniform(0.15, 0.35), 3)  # 归一化半径
        p["mask_drift_phase_x"] = round(rng.uniform(0, 2 * math.pi), 4)
        p["mask_drift_phase_y"] = round(rng.uniform(0, 2 * math.pi), 4)
    else:
        p["mask_drift_amp"] = 0.0

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
    # 0~0 → 关闭变速（恒等值 1.0；0 是非法速度，必须显式拦截）
    p["speed"] = 1.0 if _zero_range(preset, "speed") else round(rng.uniform(lo, hi), 4)
    lo, hi = _range_of(preset, "frame_dup", 0, 2)
    p["frame_dup"] = int(rng.randint(int(lo), int(hi)))
    p["frame_dup_pos"] = round(rng.uniform(0.25, 0.75), 3)  # 插入位置（相对时长比例）

    # 极短片段倒放/循环（配置 video.reverse_loop）：按概率触发，
    # 每次快照重新掷骰，重试时自然拿到不同的时序扰动参数。
    # 实际时间点由 build 阶段结合时长确定；分段模式下不启用。
    rl_cfg = (config.get("video") or {}).get("reverse_loop") or {}
    p["rl_mode"] = None
    if rl_cfg.get("enable", False):
        try:
            prob = float(rl_cfg.get("probability", 0.4))
        except (TypeError, ValueError):
            prob = 0.4
        if rng.random() < prob:
            p["rl_mode"] = rng.choice(["reverse", "loop"])
            p["rl_pos_rel"] = round(rng.uniform(0.15, 0.85), 4)   # 片段位置（相对时长）
            p["rl_seg_len"] = round(rng.uniform(0.1, 0.2), 3)     # 片段时长(秒)
            p["rl_repeats"] = rng.choice([2, 3])                  # loop 时 B 出现次数（A+B×n+C）

    # 周期性微量抽帧（配置 video.frame_drop）：按配置概率掷启用骰，
    # 具体删帧位置与作用窗口由 build 阶段结合总帧数/事件规划生成。
    fd_cfg = (config.get("video") or {}).get("frame_drop") or {}
    try:
        fd_prob = float(fd_cfg.get("probability", 0.7))
    except (TypeError, ValueError):
        fd_prob = 0.7
    p["frame_drop_on"] = bool(fd_cfg.get("enable", False)) and rng.random() < fd_prob

    # ── 音频 ──
    # 音频速度 = 视频速度 × 微偏因子 → 保证音画不漂移；0~0 → 关闭音频微变速（因子 1.0）
    lo, hi = _range_of(preset, "audio_speed", 0.998, 1.002)
    _as_factor = 1.0 if _zero_range(preset, "audio_speed") else rng.uniform(lo, hi)
    p["audio_atempo"] = round(p["speed"] * _as_factor, 5)
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
    # 0~0 → 关闭随机：CRF 用基线 23（下游仍钳制下限 24），GOP 用默认 40
    lo, hi = _range_of(preset, "crf", 20, 26)
    p["crf"] = 23 if _zero_range(preset, "crf") else int(rng.randint(int(lo), int(hi)))
    lo, hi = _range_of(preset, "gop", 25, 55)
    p["gop"] = 40 if _zero_range(preset, "gop") else int(rng.randint(int(lo), int(hi)))
    p["bframes"] = rng.randint(1, 3)
    p["sc_threshold"] = rng.randint(20, 50)

    return snap


def snapshot_summary(snap: dict) -> str:
    """日志首行摘要（方案 3.6：处理开始记录预设与参数）"""
    p = snap.get("params", {})
    return (
        f"预设={snap.get('preset_label')} seed={snap.get('seed')} "
        f"scale={p.get('scale')} rot_drift={p.get('rotate_drift_amp')}°"
        f" rot_spd={p.get('rotate_drift_speed')}°/s"
        f" speed={p.get('speed')} bright={p.get('brightness')}% "
        f"crf={p.get('crf')} gop={p.get('gop')}"
    )


# ────────────────────────────────────────────────────────────
#  时间事件规划（C 层）：全片/分段的时间窗口生成，仅依赖快照 seed，
#  确定性可复现；所有新字段都有默认值，旧配置缺字段不崩溃。
# ────────────────────────────────────────────────────────────

def _num(node: dict, key: str, default):
    try:
        v = node.get(key, default) if isinstance(node, dict) else default
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def plan_lens_events(snap: dict, config: dict, timeline_len: float,
                     seed_offset: int = 53) -> list:
    """镜头疄变事件窗口（全片级：k1/k2/cx/cy 只随机一次，事件按窗口出现）。
    timeline_len: lens 滤镜所在时间轴的总长（秒）。
    返回 [[start, end], ...]（绝对秒）；未触发/关闭返回 []。
    关闭规则：enable=false / probability<=0 / duration 0~0 / count<=0。"""
    p = snap.get("params", {})
    lk1 = float(p.get("lens_k1", 0.0))
    lk2 = float(p.get("lens_k2", 0.0))
    if abs(lk1) <= 0.0001 and abs(lk2) <= 0.0001:
        return []  # 参数本身关闭（enable=false 或 GUI k1/k2 置 0）
    ld = config_get(config, "video.lens_distortion", {}) or {}
    if not ld.get("enable", False):
        return []
    try:
        timeline_len = float(timeline_len)
    except (TypeError, ValueError):
        return []
    if timeline_len < 2.0:
        return []
    prob = _num(ld, "probability", 0.6)
    if prob <= 0.0:
        return []
    rng = random.Random(int(snap.get("seed", 0)) + seed_offset)
    if rng.random() >= prob:
        return []
    dur_node = ld.get("duration", {}) or {}
    d_lo, d_hi = _num(dur_node, "min", 1.5), _num(dur_node, "max", 4.0)
    if d_lo > d_hi:
        d_lo, d_hi = d_hi, d_lo
    if _zero_pair(d_lo, d_hi) or d_lo <= 0.0:
        return []  # 0~0 → 关闭
    cnt_node = ld.get("count", {}) or {}
    c_lo, c_hi = int(_num(cnt_node, "min", 1)), int(_num(cnt_node, "max", 2))
    if c_lo > c_hi:
        c_lo, c_hi = c_hi, c_lo
    if c_hi <= 0:
        return []
    count = rng.randint(max(1, c_lo), c_hi)
    # 均分 cell 内各放一个窗口 → 不重叠且分布在全片
    events, cell = [], timeline_len / count
    for k in range(count):
        dur = rng.uniform(d_lo, min(d_hi, max(d_lo, cell * 0.8)))
        lo = cell * k + 0.2
        hi = cell * (k + 1) - dur - 0.2
        if hi > lo:
            start = rng.uniform(lo, hi)
        else:
            start = cell * k + max(0.0, (cell - dur) / 2.0)
        end = min(start + dur, timeline_len - 0.1)
        if end - start >= 0.3:
            events.append([round(start, 3), round(end, 3)])
    return events


def generate_segment_plan(snap: dict, config: dict, seg_idx: int,
                          seg_len: float) -> dict:
    """单段动态/事件规划（B/C 层）。时间均为段内局部坐标（0 起）。
    seg_len: 该段在目标时间轴上的长度（变速后空间，与滤镜位置一致）。
    规则：probability<=0 或 duration 0~0 → 关闭；窗口放不下时收缩/放弃。
    返回 {"rotate":..,"zoom":..,"frame_drop":..,"reverse_loop":..}。"""
    seed = int(snap.get("seed", 0)) + seg_idx * 104729
    rng = random.Random(seed)
    plan = {
        "rotate": {"on": False, "start": 0.0, "dur": 0.0},
        "zoom": {"on": False, "start": 0.0, "dur": 0.0},
        "frame_drop": {"on": False, "start": 0.0, "dur": 0.0},
        "reverse_loop": {"mode": None, "pos_rel": 0.5,
                         "seg_len": 0.15, "repeats": 2},
    }
    try:
        seg_len = float(seg_len)
    except (TypeError, ValueError):
        return plan
    if seg_len < 2.0:
        return plan
    vcfg = config_get(config, "video", {}) or {}
    p = snap.get("params", {})

    def _window(node, d_prob, d_lo, d_hi, pad_default=(0.0, 0.0)):
        """掷概率骰 + 取时长窗口；0~0 时长或概率<=0 → None（关闭）"""
        node = node or {}
        prob = _num(node, "probability", d_prob)
        dur_node = node.get("duration", {}) or {}
        lo, hi = _num(dur_node, "min", d_lo), _num(dur_node, "max", d_hi)
        if lo > hi:
            lo, hi = hi, lo
        if prob <= 0.0 or _zero_pair(lo, hi) or lo <= 0.0:
            return None
        if rng.random() >= prob:
            return None
        dur = rng.uniform(lo, hi)
        pad = 0.0
        if pad_default[0] > 0 or pad_default[1] > 0:
            pn = node.get("pause", {}) or {}
            plo, phi = _num(pn, "min", pad_default[0]), _num(pn, "max", pad_default[1])
            pad = rng.uniform(min(plo, phi), max(plo, phi))
        if dur + 2 * pad > seg_len - 0.4:
            dur = seg_len - 0.4 - 2 * pad  # 放不下 → 收缩时长（保事件发生）
        if dur < 0.5:
            return None
        start = rng.uniform(pad, max(pad, seg_len - dur - pad))
        return {"on": True, "start": round(start, 3), "dur": round(dur, 3)}

    # 微旋窗口（幅度/速度已在快照中逐段随机；窗口只在段内部分时间生效）
    r_active = (float(p.get("rotate_drift_amp", 0.0)) > 0.05
                or abs(float(p.get("rotate_drift_speed", 0.0))) > 0.005)
    if r_active:
        w = _window(vcfg.get("rotate_drift"), 0.8, 3.0, 8.0)
        if w:
            plan["rotate"] = w

    # 推镜窗口（含前后留白 pause）
    if float(p.get("zoom_drift_amp", 0.0)) > 0.002:
        w = _window(vcfg.get("zoom_drift"), 0.8, 3.0, 8.0, (1.0, 4.0))
        if w:
            plan["zoom"] = w

    # 抽帧窗口（启用骰已在快照 frame_drop_on；此处只定作用窗口）
    if p.get("frame_drop_on"):
        w = _window(vcfg.get("frame_drop"), 1.0, 2.0, 5.0)
        if w:
            plan["frame_drop"] = w

    # 倒放/循环事件（短窗口，不改整段）
    rl = vcfg.get("reverse_loop") or {}
    if rl.get("enable", False):
        prob = _num(rl, "probability", 0.4)
        if prob > 0.0 and rng.random() < prob:
            el = rl.get("event_length", {}) or {}
            elo, ehi = _num(el, "min", 0.1), _num(el, "max", 0.2)
            plan["reverse_loop"] = {
                "mode": rng.choice(["reverse", "loop"]),
                "pos_rel": round(rng.uniform(0.15, 0.85), 4),
                "seg_len": round(rng.uniform(min(elo, ehi), max(elo, ehi)), 3),
                "repeats": rng.choice([2, 3]),
            }
    return plan
