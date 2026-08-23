# -*- coding: utf-8 -*-
"""video.filters — 视频滤镜链构建

把参数快照翻译成 FFmpeg filtergraph。纯字符串构建，不执行命令，
便于测试与调试（打印即可审查完整滤镜链）。

v8.1 全片预处理 + 分段动态扰动（三层结构）：
  A 全片级（整条视频只随机一次）：
      黑边裁剪 → 非对称裁剪/画布缩放 → scale 推近/拉远 → 颜色（eq/hue）
  B 分段级（每段独立随机）：
      推镜/微旋/抽帧/倒放循环 的段内参数
  C 时间事件（时间轴窗口出现）：
      镜头畸变（全片窗口，timeline enable）/ 推镜窗口（切段直通+拼接，
      zoompan 无 timeline 支持）/ 微旋窗口（timeline enable，窗口外零开销）
主路径：一次解码 → 一个 filter_complex → 一次 NVENC，不产生中间编码。
"""
import math

from ..core.config import config_get


# ──────────────────────────────────────────────────────────────
#  A 层：全片级几何与颜色
# ──────────────────────────────────────────────────────────────

def build_geom_chain(snap: dict, width: int, height: int,
                     norm_spec: dict = None, crop_rect: tuple = None) -> tuple:
    """A 层几何链（全片级）：黑边裁剪 → 画布缩放到目标分辨率（含非对称裁剪）
    → scale 参数居中推近/拉远。返回 (滤镜串, tw, th)，tw/th 为画布尺寸
    （无 norm_spec 时为缩放后源分辨率尺寸，偶数对齐）。"""
    p = snap.get("params", {})
    filters = []
    if crop_rect:
        cw, ch, cx, cy = crop_rect
        if cw > 0 and ch > 0:
            filters.append(f"crop={cw}:{ch}:{cx}:{cy}")
            width, height = cw, ch

    scale_base = float(p.get("scale", 1.0))
    tw = th = 0
    if norm_spec:
        tw = int(norm_spec.get("width", 1280))
        th = int(norm_spec.get("height", 720))
        tw -= tw % 2
        th -= th % 2

        # 非对称构图裁剪：scale 覆盖目标 → crop 裁到目标尺寸（位置按左右/上下比例偏移）
        cl = float(p.get("asym_crop_l", 0.0))
        cr = float(p.get("asym_crop_r", 0.0))
        ct = float(p.get("asym_crop_t", 0.0))
        cb = float(p.get("asym_crop_b", 0.0))
        has_asym = (cl + cr + ct + cb) > 0.001
        if has_asym:
            # scale=increase 保证缩放后画面 ≥ 目标尺寸（覆盖模式）
            # crop 位置：cl/(cl+cr) 决定水平 excess 在左侧保留多少
            crop_x_expr = f"({cl:.4f}/({cl:.4f}+{cr:.4f}+0.001))*(iw-{tw})" if (cl + cr) > 0.001 else "(iw-{tw})/2"
            crop_y_expr = f"({ct:.4f}/({ct:.4f}+{cb:.4f}+0.001))*(ih-{th})" if (ct + cb) > 0.001 else "(ih-{th})/2"
            filters.append(
                f"scale={tw}:{th}:force_original_aspect_ratio=increase:flags=bicubic,"
                f"crop={tw}:{th}:{crop_x_expr}:{crop_y_expr}")
        else:
            filters.append(
                f"scale={tw}:{th}:force_original_aspect_ratio=decrease:flags=bicubic,"
                f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=black")

        # scale 参数等效：目标分辨率上推近→居中裁剪，拉远→黑边 pad
        # （显式整数尺寸：swscale 对表达式四舍五入不可控，可能越界）
        if scale_base > 1.003:
            sw = max(tw, int(round(tw * scale_base)) // 2 * 2)
            sh = max(th, int(round(th * scale_base)) // 2 * 2)
            filters.append(f"scale={sw}:{sh}:flags=bicubic,crop={tw}:{th}")
        elif scale_base < 0.997:
            sw = min(tw, max(2, int(round(tw * scale_base)) // 2 * 2))
            sh = min(th, max(2, int(round(th * scale_base)) // 2 * 2))
            filters.append(
                f"scale={sw}:{sh}:flags=bicubic,"
                f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=black")
    else:
        # 标准化关闭：源分辨率缩放（2 的倍数保证 yuv420p）
        tw = int(width * scale_base / 2) * 2
        th = int(height * scale_base / 2) * 2
        filters.append(f"scale={tw}:{th}:flags=bicubic")
    return ",".join(filters), tw, th


def build_color_chain(snap: dict, config: dict = None) -> str:
    """A 层颜色/纹理链（全片级）：亮度/对比度/饱和度合并单个 eq + hue
    （+可选减法项 通道混合/噪点，默认关，不进普通 GUI）。
    返回逗号连接滤镜串（可空）。"""
    p = snap.get("params", {})
    filters = []
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
    # 性能减法开关：配置 video.channel_mix.enable 可开启（默认关，不进普通 GUI）；
    # 实测 10s 片段省 1.20s，指纹贡献仅 0.011，收益/耗时比最差之一
    cm_cfg = ((config or {}).get("video") or {}).get("channel_mix") or {}
    if abs(cm) > 0.001 and cm_cfg.get("enable", False):
        a = min(0.10, abs(cm)) * (1 if cm > 0 else -1)
        # 通道权重微偏移（行和保持为 1）
        filters.append(
            f"colorchannelmixer="
            f"rr={1 - a:.4f}:rg={a:.4f}:rb=0:"
            f"gr=0:gg={1 + a:.4f}:gb={-a:.4f}:"
            f"br={a:.4f}:bg=0:bb={1 - a:.4f}"
        )

    noise = float(p.get("noise", 0.0))
    noise_cfg = ((config or {}).get("video") or {}).get("noise") or {}
    if noise > 0.1 and noise_cfg.get("enable", False):
        ns = max(1, min(30, int(round(noise * 3))))
        filters.append(f"noise=alls={ns}:allf=t+u")

    return ",".join(filters)


# ──────────────────────────────────────────────────────────────
#  B/C 层：推镜（窗口化）
# ──────────────────────────────────────────────────────────────

def build_zoompan_expr(p: dict, fps: float) -> str:
    """推镜 zoompan z 表达式（周期性推近/拉远，in 为片段内帧号）"""
    z_amp = float(p.get("zoom_drift_amp", 0.0))
    z_period = max(1.0, float(p.get("zoom_drift_period", 4.0)))
    period_frames = max(2, int(z_period * fps))
    # mod 内逗号需转义
    if p.get("zoom_drift_dir") == "in":
        return f"1+{z_amp:.4f}*mod(in\\,{period_frames})/{period_frames}"
    return f"1+{z_amp:.4f}*(1-mod(in\\,{period_frames})/{period_frames})"


def build_zoompan_filter(p: dict, fps: float, tw: int, th: int) -> str:
    """全长推镜（降级/兼容路径；新主路径用 build_zoom_window_complex 窗口化）"""
    z_amp = float(p.get("zoom_drift_amp", 0.0))
    if z_amp <= 0.002:
        return ""
    z_expr = build_zoompan_expr(p, fps)
    return (f"zoompan=z='{z_expr}':d=1"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={tw}x{th}:fps={fps:.3f}")


def build_zoom_window_complex(src_label: str, zwin: dict, p: dict,
                              fps: float, tw: int, th: int,
                              suffix: str = "") -> tuple:
    """推镜时间窗口（C 层）：按窗口切段，仅窗口段过 zoompan，其余直通，
    concat 拼回 → 推镜只在窗口内发生（zoompan 无 timeline 支持，故用切段）。
    返回 (fc_parts 列表, 输出 label)；未触发返回 ([], src_label)。"""
    if not zwin or not zwin.get("on"):
        return [], src_label
    a = float(zwin.get("start", 0.0))
    d = float(zwin.get("dur", 0.0))
    z_amp = float(p.get("zoom_drift_amp", 0.0))
    if z_amp <= 0.002 or d < 0.3:
        return [], src_label
    b = a + d
    z_expr = build_zoompan_expr(p, fps)
    sfx = suffix
    parts, ins = [], []
    n_split = 2 if a <= 0.05 else 3
    parts.append(f"{src_label}split={n_split}" +
                 "".join(f"[zs{k}{sfx}]" for k in range(n_split)))
    idx = 0
    if a > 0.05:
        parts.append(f"[zs{idx}{sfx}]trim=duration={a:.3f},"
                     f"setpts=PTS-STARTPTS[zA{sfx}]")
        ins.append(f"[zA{sfx}]")
        idx += 1
    parts.append(f"[zs{idx}{sfx}]trim=start={a:.3f}:duration={d:.3f},"
                 f"setpts=PTS-STARTPTS,"
                 f"zoompan=z='{z_expr}':d=1"
                 f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                 f":s={tw}x{th}:fps={fps:.3f}[zB{sfx}]")
    ins.append(f"[zB{sfx}]")
    idx += 1
    parts.append(f"[zs{idx}{sfx}]trim=start={b:.3f},"
                 f"setpts=PTS-STARTPTS[zC{sfx}]")
    ins.append(f"[zC{sfx}]")
    out = f"[zout{sfx}]"
    parts.append("".join(ins) + f"concat=n={len(ins)}:v=1:a=0{out}")
    return parts, out


# ──────────────────────────────────────────────────────────────
#  B/C 层：微旋（窗口化）
# ──────────────────────────────────────────────────────────────

def build_rotate_filter(p: dict, enable_expr: str = None) -> str:
    """动态微旋（正弦角度渐变 + 单向恒速漂移）。
    单位：amp=最大摆角（°），period=正弦周期（s），speed=单向漂移（°/s，
    累计角度=speed×t，取值必须极小），phase=初相位（rad）。
    FFmpeg rotate 角度为弧度，故各项均乘 π/180 换算；公式不再做任何
    单位变换。enable_expr 非空时仅窗口内生效（rotate 支持 timeline，
    窗口外零开销）。窗口内时间基准由调用方保证（先 setpts 归零再进窗口）。
    未启用返回空串。"""
    r_amp = float(p.get("rotate_drift_amp", 0.0))
    r_period = max(1.0, float(p.get("rotate_drift_period", 10.0)))
    r_speed = float(p.get("rotate_drift_speed", 0.0))
    r_phase = float(p.get("rotate_drift_phase", 0.0))
    # 启用阈值只区分 0~0 关闭与启用，不拦截新区间内的合法小值（amp≥0.03°）
    if not (r_amp > 0.001 or abs(r_speed) > 0.0005):
        return ""
    deg2rad = math.pi / 180
    sin_part = (f"{r_amp:.4f}*{deg2rad:.6f}*sin(2*PI*t/{r_period:.2f}+{r_phase:.4f})"
                if r_amp > 0.001 else "0")
    # {:+.4f} 自动携带符号，负速度生成 "-0.0140" 而非 "+-0.0140"
    spd_part = f"{r_speed:+.4f}*{deg2rad:.6f}*t" if abs(r_speed) > 0.0005 else ""
    angle_expr = f"({sin_part}{spd_part})"
    f = f"rotate={angle_expr}:fillcolor=none"
    if enable_expr:
        f += f":enable='{enable_expr}'"
    return f


# ──────────────────────────────────────────────────────────────
#  C 层：镜头畸变（全片参数一次随机，事件窗口出现）
# ──────────────────────────────────────────────────────────────

def build_lens_enable_expr(events: list, t0: float = 0.0,
                           t1: float = None) -> str:
    """绝对时间事件窗口 → timeline enable 表达式（裁剪到 [t0,t1] 并转相对时间）。
    无有效窗口返回 None。"""
    if not events:
        return None
    parts = []
    for ev in events:
        try:
            a, b = float(ev[0]), float(ev[1])
        except (TypeError, ValueError, IndexError):
            continue
        a2 = max(a, t0)
        b2 = min(b, t1) if t1 is not None else b
        if b2 - a2 < 0.05:
            continue
        parts.append(f"between(t,{a2 - t0:.3f},{b2 - t0:.3f})")
    return "+".join(parts) if parts else None


def build_lens_filter(p: dict, enable_expr: str = None) -> str:
    """镜头畸变（lenscorrection，支持 timeline）。enable_expr 非空时仅事件窗口
    内执行，窗口外完全跳过（lenscorrection 重，窗口化是主要性能手段）。"""
    lk1 = float(p.get("lens_k1", 0.0))
    lk2 = float(p.get("lens_k2", 0.0))
    if abs(lk1) <= 0.0001 and abs(lk2) <= 0.0001:
        return ""
    lcx = float(p.get("lens_cx", 0.5))
    lcy = float(p.get("lens_cy", 0.5))
    f = (f"lenscorrection=cx={lcx:.3f}:cy={lcy:.3f}"
         f":k1={lk1:.5f}:k2={lk2:.5f}")
    if enable_expr:
        f += f":enable='{enable_expr}'"
    return f


def build_spatial_chain(snap: dict, width: int, height: int,
                        fps: float = 25.0, norm_spec: dict = None,
                        crop_rect: tuple = None, config: dict = None) -> str:
    """降级/兼容路径：A 层几何 + 全长推镜/微旋/畸变 + 颜色（整段作用，无窗口）。
    新主路径（单进程 filtergraph）用上述拆分 builder 组装窗口化链路。"""
    p = snap.get("params", {})
    geom, tw, th = build_geom_chain(snap, width, height, norm_spec, crop_rect)
    parts = [geom]
    zp = build_zoompan_filter(p, fps, tw, th)
    if zp:
        parts.append(zp)
    rot = build_rotate_filter(p)
    if rot:
        parts.append(rot)
    lens = build_lens_filter(p)
    if lens:
        parts.append(lens)
    color = build_color_chain(snap, config)
    if color:
        parts.append(color)
    return ",".join(x for x in parts if x)


def build_geq_filter(snap: dict, tw: int, th: int) -> str:
    """局部动态扰动（geq 高斯位移漂移）。
    性能优化（实测）：geq 逐像素变位移求值是全链最重开销（10.2s 片段
    全分辨率 41s，speed 0.25；且与表达式复杂度无关、加线程反而更慢），
    改为半分辨率执行：缩到 1/2 → geq → 缩回，中心/半径随分辨率缩放，
    位移幅度同步减半（放大后视觉位移量不变），耗时降至 ~1/3.5。
    返回滤镜串（不含前导逗号），未启用返回空串。"""
    p = snap.get("params", {})
    md_amp = float(p.get("mask_drift_amp", 0.0))
    if md_amp <= 0.05 or tw <= 8 or th <= 8:
        return ""
    md_per = max(1.0, float(p.get("mask_drift_period", 12.0)))
    md_px = float(p.get("mask_drift_phase_x", 0.0))
    md_py = float(p.get("mask_drift_phase_y", 0.0))
    # 半分辨率几何（偶数对齐，yuv420p 要求）
    hw = tw // 2 - (tw // 2) % 2
    hh = th // 2 - (th // 2) % 2
    md_cx = float(p.get("mask_drift_cx", 0.5)) * hw
    md_cy = float(p.get("mask_drift_cy", 0.5)) * hh
    md_r = float(p.get("mask_drift_radius", 0.25)) * min(hw, hh)
    r2 = md_r * md_r
    amp = md_amp / 2.0  # 半分辨率下幅度减半，缩回后视觉位移不变
    dx = f"{amp:.2f}*sin(2*PI*T/{md_per:.2f}+{md_px:.4f})"
    dy = f"{amp:.2f}*sin(2*PI*T/{md_per:.2f}+{md_py:.4f})"
    falloff = f"exp(-((X-{md_cx:.1f})*(X-{md_cx:.1f})+(Y-{md_cy:.1f})*(Y-{md_cy:.1f}))/{r2:.0f})"
    geq = f"geq=lum='p(X+{dx}*{falloff}\\,Y+{dy}*{falloff})'"
    geq += f":cb='p(X+{dx}*{falloff}\\,Y+{dy}*{falloff})'"
    geq += f":cr='p(X+{dx}*{falloff}\\,Y+{dy}*{falloff})'"
    return (f"scale={hw}:{hh}:flags=bicubic,{geq},"
            f"scale={tw}:{th}:flags=bicubic")


# ──────────────────────────────────────────────────────────────
#  时序扰动：倒放/循环（单遍滤镜实现，不额外编码）
# ──────────────────────────────────────────────────────────────

def build_reverse_loop_complex(snap: dict, seg_dur: float, has_audio: bool,
                               src_v: str = "0:v", src_a: str = "0:a",
                               suffix: str = "") -> tuple:
    """
    真片段倒放/循环（修复旧版只拼一次的问题）：
      reverse → A + 反转(B) + C（音视频同步倒放）
      loop    → A + B + B(+B) + C（B 真正被重复，次数由 rl_repeats 定）
    音视频用完全相同的切点三段拼接，不产生不同步；
    全部在最终滤镜图内完成，不产生中间编码。
    suffix: 标签后缀（分段模式多段并存时防标签碰撞）。
    返回 (fc_str, v_out_label, a_out_label)；未触发返回 (None, src_v, src_a)。
    """
    p = snap.get("params", {})
    mode = p.get("rl_mode")
    if not mode or seg_dur < 2.0:
        return None, src_v, src_a
    d = float(p.get("rl_seg_len", 0.15))
    t1 = float(p.get("rl_pos_rel", 0.5)) * seg_dur
    t1 = max(0.05, min(t1, seg_dur - d - 0.1))
    t2 = min(t1 + d, seg_dur - 0.05)
    if t2 - t1 < 0.05:
        return None, src_v, src_a
    sfx = suffix

    n_rep = int(p.get("rl_repeats", 2)) if mode == "loop" else 1
    n_rep = max(1, min(3, n_rep))

    parts = [
        f"[{src_v}]split=3[r1{sfx}][r2{sfx}][r3{sfx}]",
        f"[r1{sfx}]trim=end={t1:.3f},setpts=PTS-STARTPTS[vA{sfx}]",
        f"[r2{sfx}]trim=start={t1:.3f}:end={t2:.3f},setpts=PTS-STARTPTS"
        f"{',reverse' if mode == 'reverse' else ''}[vB{sfx}]",
        f"[r3{sfx}]trim=start={t2:.3f},setpts=PTS-STARTPTS[vC{sfx}]",
    ]
    if n_rep > 1:
        parts.append(f"[vB{sfx}]split={n_rep}" +
                     "".join(f"[vb{i}{sfx}]" for i in range(n_rep)))
        vin = f"[vA{sfx}]" + "".join(f"[vb{i}{sfx}]" for i in range(n_rep)) + f"[vC{sfx}]"
        parts.append(f"{vin}concat=n={n_rep + 2}:v=1:a=0[vt{sfx}]")
    else:
        parts.append(f"[vA{sfx}][vB{sfx}][vC{sfx}]concat=n=3:v=1:a=0[vt{sfx}]")

    a_out = None
    if has_audio:
        parts += [
            f"[{src_a}]asplit=3[s1{sfx}][s2{sfx}][s3{sfx}]",
            f"[s1{sfx}]atrim=end={t1:.3f},asetpts=PTS-STARTPTS[aA{sfx}]",
            f"[s2{sfx}]atrim=start={t1:.3f}:end={t2:.3f},asetpts=PTS-STARTPTS"
            f"{',areverse' if mode == 'reverse' else ''}[aB{sfx}]",
            f"[s3{sfx}]atrim=start={t2:.3f},asetpts=PTS-STARTPTS[aC{sfx}]",
        ]
        if n_rep > 1:
            parts.append(f"[aB{sfx}]asplit={n_rep}" +
                         "".join(f"[ab{i}{sfx}]" for i in range(n_rep)))
            ain = f"[aA{sfx}]" + "".join(f"[ab{i}{sfx}]" for i in range(n_rep)) + f"[aC{sfx}]"
            parts.append(f"{ain}concat=n={n_rep + 2}:v=0:a=1[at{sfx}]")
        else:
            parts.append(f"[aA{sfx}][aB{sfx}][aC{sfx}]concat=n=3:v=0:a=1[at{sfx}]")
        a_out = f"[at{sfx}]"
    return ";".join(parts), f"[vt{sfx}]", a_out


# ──────────────────────────────────────────────────────────────
#  时序扰动：周期性微量抽帧（修复旧版只删 1 帧的问题）
# ──────────────────────────────────────────────────────────────

def frame_drop_positions(n_frames: int, lo: int, hi: int, rng,
                         window: tuple = None) -> list:
    """
    每 interval(min~max) 帧随机删 1 帧，然后继续计算下一次间隔：
      第 100~200 帧附近删一帧 → 再加 100~200 帧删下一帧 → …
    window=(start_frame, end_frame) 非空时只在窗口内删帧（时间事件化）。
    视频太短（不够一个间隔）不删；总量封顶 ≤2% 帧，不影响播放。
    """
    try:
        lo_i, hi_i = int(min(lo, hi)), int(max(lo, hi))
    except (TypeError, ValueError):
        return []
    if n_frames <= 0 or lo_i < 2 or n_frames < lo_i + 5:
        return []
    w0 = 0
    w1 = n_frames - 3
    if window:
        try:
            w0 = max(0, int(window[0]))
            w1 = min(n_frames - 3, int(window[1]))
        except (TypeError, ValueError, IndexError):
            pass
    if w1 - w0 < lo_i:
        return []  # 窗口不够一个间隔
    cap = max(1, n_frames // 50)
    drops, nxt = [], rng.randint(lo_i, hi_i)
    while nxt < min(w1, n_frames - 3) and len(drops) < cap:
        if nxt >= w0:
            drops.append(nxt)
        nxt += rng.randint(lo_i, hi_i)
    return drops


def build_frame_drop_expr(drops: list) -> str:
    """删帧位置列表 → select 表达式（含时戳重排，输出仍为恒定帧率）"""
    if not drops:
        return ""
    cond = "+".join(f"eq(n,{i})" for i in drops)
    return f"select='not({cond})',setpts=N/FRAME_RATE/TB"


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
            args += ["-b:v", f"{target_kbps}k"]
        else:
            args += ["-b:v", "0", "-crf", str(crf)]
        return args
    # libsvtav1
    args = ["-c:v", "libsvtav1", "-preset", "8", "-pix_fmt", "yuv420p"]
    if target_kbps:
        args += ["-b:v", f"{target_kbps}k"]
    else:
        args += ["-crf", str(crf)]
    return args
