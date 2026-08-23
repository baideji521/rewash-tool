# PARAMETER_CALIBRATION_REPORT_V3

第三轮：源码级时间轴审计（不修改代码）

- 审计对象：当前工作区源码（**注意：这些修复尚未 commit / push，GitHub main 仍是 `08062c6`，仍带 V2 已证实的全部 Bug**）
- 证据优先级：实际源码 > 实际 FFmpeg/ffprobe > V2 > V1（V1 仅历史参考）
- 日期：2026-08-23
- 全部数字来自真实执行 FFmpeg/ffprobe；不可证明处标注【证据不足】

## 本轮最重要的新发现（V2 未覆盖）

**当「源帧率 > normalize.fps」时，整条帧号/帧数换算体系失效。**

`segment.py:119` 在 **trim 之前**就把流降到了目标帧率：

```python
common = (f"fps={eff_fps:.3f}," if eff_fps + 1e-6 < fps else "") + geom
fc_parts.append(f"[0:v]{common}[gbase]")      # segment.py:119,127
```

但 `segment.py:154` 仍然把**原始源帧率**传给时间轴真值函数：

```python
met = segment_video_metrics(snap, config, plan, i, seg_len,
                            speed, fps, eff_fps, t0=t0, t1=t1)   # fps = media_info.fps
```

于是 `_trim_frames(t0,t1,60)` 与 `frame_drop_plan(..., src_fps=60)` 算的是 60fps 空间的帧号，
而 `[vt{i}]` 上真实的流已经是 30fps。V2 的 `test_frame_drop_window` 只**孤立**测了
`frame_drop_plan(src_fps=60)`，从未让 60fps 素材真正走一遍 `process_single_pass`，所以漏掉了。

真 60fps 素材（21.108s / 1266 帧 / normalize 30fps）实测：

| seg | trim 窗口 | 代码 n_win@60 | 真实 n_win@30 | 计划删帧号 | 实测[vt] | 实测[vb] | 实际生效删帧 | 预测 out | 实测支路 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 0.964→4.206 | **195** | 98 | 68, 82, 106 | 97 | 95 | **2**（计划 3） | 100 | 98 |
| 1 | 4.206→7.448 | **194** | 97 | 144, 158, 181 | 97 | 97 | **0**（计划 3） | 97 | 98 |
| 2 | 7.448→10.691 | **195** | 97 | 128, 150 | 98 | 98 | **0**（计划 2） | 96 | 97 |
| 3 | 10.691→13.933 | **194** | 97 | 24, 40 | 97 | 95 | **2**（计划 2） | 99 | 97 |
| 4 | 13.933→17.175 | **195** | 98 | （无） | 97 | 97 | 0 | 98 | 97 |
| 5 | 17.175→20.417 | **195** | 97 | （无） | 98 | 98 | 0 | 97 | 97 |

- A 层链首实测 = `[0:v]fps=30.000,scale=810:1080:...` → 降帧确实在 trim 之前
- 代码认为每段 195 帧，真实 97 帧 → **偏差约 2 倍**
- **计划删 8 帧，实际只删掉 4 帧**：帧号 106/144/158/181/128/150 都 ≥ 该段真实帧数（97），
  `select='not(eq(n,...))'` 对不存在的帧号静默无效
- Σ预测 587 帧 vs Σ实测支路 584 帧；最终 589 帧 → **concat 补帧 5 帧**（逐段 `win_len` 也算错了，
  `|a−v|` 不再为 0）
- `exp_dur=19.566667` vs 实际 `19.633333` → 误差 **0.3407%**，**±2% 质检通过** → 这是一个**静默错误**

对照：29.97fps 源（`eff_fps=30 > src_fps=29.97`，不插入 `fps=`）同一 seed 下
Σ预测 = Σ实测 = 589 帧，误差 0.0000%，补帧 0 —— 证明问题只在「源帧率 > 目标帧率」时出现。

---

## 一、generate_segment_plan：调用位置与时间轴归属

### 调用位置（源码）

- `segment.py:151-152` → `generate_segment_plan(snap, config, i, seg_len / speed, log_fn=log_fn)`
- `video_processor.py:69` → `generate_segment_plan(snap, config, seg_idx, seg_dur / speed)`
- 定义：`randomizer.py:429-528`，随机源 `seed = snap.seed + seg_idx*104729`（`randomizer.py:436`）

**关键：两处都传 `seg_len / speed`，所以 `plan` 里所有时间量都属于【输出时间轴】。**

### 时间轴归属表（逐个变量 + 源码行号）

| 变量 | 所属时间轴 | 源码位置 | 依据 |
|---|---|---|---|
| `trim_head` / `trim_tail` | 原始输入 | `randomizer.py:100-101` | 直接对源 duration 扣减 |
| `effective_duration` | 原始输入 | `segment.py:51-64` | `dur - th - tt` |
| `cuts[i]` | 原始输入（相对 trim_head） | `segment.py:67-73,286` | `make_equal_cuts(eff_dur, n)` |
| `seg_start` / `seg_end` | 原始输入（绝对秒） | `segment.py:297` | `trim_head + cuts[i]` |
| `t0` / `t1` | 原始输入（绝对秒，四舍五入到 ms） | `segment.py:149-150` | 直接进 `trim=start=t0:end=t1` |
| `seg_len = seg_end - seg_start` | 原始输入 | `segment.py:143` | — |
| `speed` | 无量纲 | `randomizer.py:222` | — |
| `plan.rotate/zoom/frame_drop.start,dur` | **输出时间轴** | `randomizer.py:477,504` + `segment.py:151` | `_window()` 基于 `seg_len/speed` |
| `plan.reverse_loop.pos_rel` | 无量纲（比例） | `randomizer.py:524` | — |
| `plan.reverse_loop.seg_len` | **输出时间轴**（`rl_frames/out_fps`） | `randomizer.py:518-525` | 量化到 `normalize.fps` 栅格 |
| `build_segment_branch(seg_len_in)` | 原始输入 | `segment.py:163` | 传的是 `seg_len` 不是 `seg_len/speed` |
| `build_reverse_loop_complex(seg_dur)` | 原始输入 | `_graph.py:200-202` | `= seg_len_in` |
| `frame_drop_plan` 的 `fwin` | 输出时间轴 → 代码内折回输入 | `_graph.py:71-73` | `w0 = start*speed*src_fps` |
| `frame_dup` 的 `t_pos` | **输出时间轴** | `_graph.py:150,272-278` + `filters.py:452` | `dup_dur = frames_pre_dup/out_fps` |
| `av_offset` | 音频相对偏移（秒，输出侧） | `filters.py:534-538` | 只作用于音频链 |

### seed=1786869947670 真实复现

```
ffprobe 源: duration=21.108000  fps=29.9700  a_sample_rate=48000
normalize 目标 fps=30.0000     A 层是否插入 fps= 降帧: False
快照: speed=0.9805 trim_head=0.964 trim_tail=0.691 av_offset=0.105
      frame_dup=2 frame_dup_pos=0.355

effective_duration = max(2.0, 21.108000 - 0.964 - 0.691) = 19.453000
手算 source - trim_head - trim_tail                    = 19.453000   一致 ✓
make_equal_cuts(19.453, 6)[-1] = 19.453000             == eff ✓
```

| seg | t0→t1（原始输入时间轴） | seg_len（输入） | speed | 传给 plan 的 seg_len/speed（输出时间轴） |
|---|---|---|---|---|
| 0 | 0.964→4.206 | 3.242167 | 0.9805 | 3.306646 |
| 1 | 4.206→7.448 | 3.242167 | 0.9979 | 3.248990 |
| 2 | 7.448→10.691 | 3.242167 | 1.0362 | 3.128900 |
| 3 | 10.691→13.933 | 3.242167 | 0.9921 | 3.267984 |
| 4 | 13.933→17.175 | 3.242167 | 1.0046 | 3.227321 |
| 5 | 17.175→20.417 | 3.242167 | 1.0166 | 3.189226 |

段间缝隙实测 = `+0.000000 ×5`，全部为 0 ✓

### 已确认的时间轴混用（源码级）

1. **`plan.reverse_loop.seg_len`**：`randomizer.py:518-525` 用 `normalize.fps` 量化（输出栅格），
   但 `_graph.py:200-202`/`filters.py:349-351` 把它当作**输入空间**的 `trim` 长度使用。
   代码里没有任何 `×speed` / `÷speed` 转换。
2. **`plan_lens_events` 在整文件路径**：`video_processor.py:95` 传 `seg_dur/speed`（输出时间轴），
   但 lens 滤镜通过 `pre_chain` 接在 `_graph.py:208-211`，位置在 ③抽帧/④变速**之前**（输入空间）。
   （分段路径 `segment.py:123` 传的是 `duration`，且 lens 在 `[0:v]` 上，两者同为输入空间 → 正确。）

---

## 二、reverse_loop

### 源码事实

| 项 | 结论 | 源码 |
|---|---|---|
| 触发随机数来源 | `random.Random(snap.seed + seg_idx*104729)` | `randomizer.py:436-437` |
| probability | `config.video.reverse_loop.probability`（默认 0.4） | `randomizer.py:511-512` |
| mode | `rng.choice(["reverse","loop"])` | `randomizer.py:523` |
| repeats | `rng.choice([2,3])`，下游钳 1~3 | `randomizer.py:526`, `filters.py:356-357` |
| seg_len | `rl_frames/out_fps`，量化到**输出**帧栅格 | `randomizer.py:518-525` |
| loop 起点 | `t1 = pos_rel*seg_dur`，钳 `[0.05, seg_dur-d-0.1]` | `filters.py:349-350` |
| 复制多少时间 | `(repeats-1) × (t2-t1)`，`t2 = min(t1+d, seg_dur-0.05)` | `filters.py:351` |
| 在 speed 之前？ | **是**。rl 是 ①，speed 是 ④ | `_graph.py:197-205` vs `213-220` |
| 在 frame_drop 之前？ | **是**。rl 是 ①，frame_drop 是 ③ | `_graph.py:197` vs `213-217` |
| 影响 audio？ | **是**，用完全相同的 `t1/t2` 切点 | `_graph.py:303-308` |
| PTS 如何处理 | 每片 `setpts=PTS-STARTPTS`，再 `concat` 重排 | `filters.py:361-364,370` |
| reverse 是否改长度 | 不改（三片帧数和恒为 n） | `filters.py:361-364` |

### 强制触发实测（rl_len = 4/30 = 0.133333s，段长 6.0s，源 29.97fps）

| mode | rep | speed | Δv 输入帧 | Δv 输出帧 | Δv 秒 | Δa 秒 | before_speed | after_speed | total_added | \|Δv−Δa\| |
|---|---|---|---|---|---|---|---|---|---|---|
| reverse | 2 | 1.00 | 0 | 0 | +0.00000 | +0.00000 | +0.000000 | +0.000000 | +0.000000 | 0.00000 |
| reverse | 2 | 0.95 | 0 | 0 | +0.00000 | +0.00000 | +0.000000 | +0.000000 | +0.000000 | 0.00000 |
| reverse | 2 | 1.05 | 0 | 0 | +0.00000 | +0.00000 | +0.000000 | +0.000000 | +0.000000 | 0.00000 |
| loop | 2 | 1.00 | 4 | 4 | +0.13333 | +0.13333 | +0.133467 | +0.133467 | +0.133333 | 0.00000 |
| loop | 2 | 0.95 | 4 | 4 | +0.13333 | +0.13333 | +0.133467 | +0.140491 | +0.133333 | 0.00000 |
| loop | 2 | 1.05 | 4 | **3** | +0.10000 | +0.10000 | +0.133467 | +0.127111 | +0.100000 | 0.00000 |
| loop | 3 | 1.00 | 8 | 8 | +0.26667 | +0.26667 | +0.266934 | +0.266934 | +0.266667 | 0.00000 |
| loop | 3 | 0.95 | 8 | 8 | +0.26667 | +0.26667 | +0.266934 | +0.280983 | +0.266667 | 0.00000 |
| loop | 3 | 1.05 | 8 | **7** | +0.23333 | +0.23333 | +0.266934 | +0.254222 | +0.233333 | 0.00000 |

三项指标定义：
- `reverse_loop_delta_before_speed` = `rl_extra_frames / src_fps`（输入空间增量）
- `reverse_loop_delta_after_speed` = 上者 `/ speed`（理论输出增量）
- `reverse_loop_total_added` = 实测输出增量（已落到输出帧栅格）

**结论**：`|Δv − Δa| = 0.00000`（音频靠 `win_len` 中性化对齐，V2 的修复有效）；
但 `total_added ≠ after_speed`，最大残差 0.0271s（loop rep=2 speed=1.05：0.127111 → 0.100000）。

### 时间空间一致性检查（实测）

| speed | rl 输入增量 | 理论输出增量 = 输入/speed | 落到输出帧栅格 | 残差 |
|---|---|---|---|---|
| 1.00 | 0.133333 | 0.133333 | 0.133333 | 0.000000 |
| 0.95 | 0.133333 | 0.140351 | 0.133333 | **0.007018** |
| 1.05 | 0.133333 | 0.126984 | 0.133333 | **0.006349** |

`randomizer.py:518-521` 的注释声称量化目的是让「视频/音频增量相等」——
该目的已由 `win_len` 中性化独立达成；而量化本身放在了**错误的空间**
（输出栅格量化后当输入时长用），所以 speed≠1 时必然留残差。残差 ≤ 1 输出帧，不构成时长失控。

### 另一处结构性精度问题（源码级，无需实测）

`_graph.py:142-144` 把 `n_in`（**已含 rl 增量**）交给 `frame_drop_plan`，
而窗口帧号 `w0/w1` 由 `output_time × speed × src_fps` 算出，**没有考虑 rl 插入点之后的帧号平移**。
rl 触发时，位于 rl 点之后的删帧帧号会整体偏移 `rl_extra_frames` 帧。
本次 seed 下 6 段 rl 全未触发（filtergraph 中无 `split=3`/`reverse` 标签），故未产生实际影响。

---

## 三、frame_drop

### 源码事实

- 帧率：**`src_fps`**（`_graph.py:70` `fps_in = max(1.0, float(src_fps))`），调用方
  `segment.py:154` 传 `fps = media_info["fps"]`
- `frame_drop_positions(n_frames, lo, hi, rng, window)` 的 `n_frames` = `n_in`
  = `n_win + rl_extra_frames`（`_graph.py:141-144`），属**输入空间帧数**
- `window` = `(w0, w1)`，`_graph.py:71-73`：
  `w0 = start × speed × src_fps` ; `w1 = (start+dur) × speed × src_fps`
- 转换链：`output_time --×speed--> source_time --×src_fps--> source_frame`
- 顺序：`select='not(...)'` → `setpts=N/FRAME_RATE/TB` → `setpts=1/speed*PTS`
  （`filters.py:436` + `_graph.py:213-224`）→ **删帧确实在变速之前，帧号是输入空间** ✓

### 实测：29.97fps 源，6s 窗口（180 帧），窗口 1.0~4.0s（输出时间轴）

| speed | 输出窗口 | → 输入帧号窗口 | 计划删帧 | 实测[vt] | 实测[vb] | n_kept 预测 | 一致 |
|---|---|---|---|---|---|---|---|
| 0.90 | 1.0~4.0 | 27.0~107.9 | 38, 62, 81 | 180 | 177 | 177 | ✓ |
| 1.00 | 1.0~4.0 | 30.0~119.9 | 38, 62, 81 | 180 | 177 | 177 | ✓ |
| 1.10 | 1.0~4.0 | 33.0~131.9 | 38, 62, 81 | 180 | 177 | 177 | ✓ |

删帧帧号全部落在输入帧号窗口内 ✓。反算 `frame/src_fps/speed` = 输出时刻：

- speed 0.90 → 1.409 / 2.299 / 3.003 s
- speed 1.00 → 1.268 / 2.069 / 2.703 s
- speed 1.10 → 1.153 / 1.881 / 2.457 s

均落在 1.0~4.0 的输出窗口内 → **`input_frame = output_time × speed × src_fps` 成立** ✓

### 但：`src_fps` 的取值前提被 A 层破坏

见开头「本轮最重要的新发现」：当 `normalize.fps < 源帧率` 时 `[vt]` 上的流已是
`normalize.fps`，此时 `src_fps` 应为 `eff_fps`。60fps 实测：计划删 8 帧，**实际只删掉 4 帧**。

**计划 N 帧 vs 实际 N 帧汇总**

| 场景 | 计划删帧 | 实际删帧 | 一致 |
|---|---|---|---|
| 29.97fps 源（不降帧） | 3 | 3 | ✓ |
| 60fps 源 → 30fps（降帧） | 8 | **4** | ✗ |

---

## 四、frame_dup

### 源码事实

- `frame_dup_pos` 属**输出时间轴**：`_graph.py:272-278` 传入的 `duration` 是
  `m["dup_dur"] = frames_pre_dup / out_fps`（`_graph.py:150`），
  即 `[vm]` 这条流的真实时长（已扣抽帧、已含 rl、已变速、已被 `fps` 重采样）
- `filters.py:451-452`：`pos = clamp(frame_dup_pos, 0.15, 0.85)` ; `t_pos = duration × pos`
- `filters.py:453-458`：`split=2` → `[d1]trim=end=t_pos` + `[d2]trim=start=t_pos,tpad=start=n:start_mode=clone` → `concat=n=2`
- `tpad=start=n:start_mode=clone` 克隆的是 **`[d2]` 的第一帧**，即时间轴上 `t_pos` 之后的第一帧
- `_graph.py:153`：`dup = dup if dup > 0 and dup_dur > 1.0 else 0`（段短于 1s 不插帧）
- `filters.py:450` 读了 `_fps` 但整个函数体未使用该变量 → 死代码

### 实测（源 29.97fps，段 6.0s，frame_dup=3，out_fps=30）

| pos | speed | [vm]帧 | dup_dur | t_pos(s) | 预测克隆帧号 | 实测克隆帧号 | run | dup 后帧 | Δ帧 |
|---|---|---|---|---|---|---|---|---|---|
| 0.25 | 1.00 | 180 | 6.000000 | 1.500 | 45 | 45 | 3 | 183 | +3 |
| 0.25 | 0.95 | 190 | 6.333333 | 1.583 | 48 | 47 | 3 | 193 | +3 |
| 0.50 | 1.00 | 180 | 6.000000 | 3.000 | 90 | 90 | 3 | 183 | +3 |
| 0.50 | 0.95 | 190 | 6.333333 | 3.167 | 95 | 95 | 3 | 193 | +3 |
| 0.75 | 1.00 | 180 | 6.000000 | 4.500 | 135 | 135 | 3 | 183 | +3 |
| 0.75 | 0.95 | 190 | 6.333333 | 4.750 | 143 | 143 | 3 | 193 | +3 |

- `dup_dur` 与实测 `[vm]` 帧数完全对应（180/30=6.000，190/30=6.333333）✓
- 理论增加 3 帧，实际增加 3 帧，6/6 组一致 ✓
- 克隆位置最大偏差 1 帧（pos=0.25/speed=0.95）✓

---

## 五、speed

### 当前实际链（源码逐行）

```
_graph.py:215-217   fd_expr = build_frame_drop_expr(m["drops"])
                    → select='not(eq(n,..))',setpts=N/FRAME_RATE/TB
_graph.py:219-220   if abs(speed-1.0) > 0.0005: setpts={1/speed:.6f}*PTS
_graph.py:228-235   ⑤ zoom 窗口（切段，仅窗口段过 zoompan）
_graph.py:240-248   ⑥ rotate（timeline enable，不改时长/帧数）
_graph.py:254-260   ⑦ fps={nf}, format={pix}, setsar=1   ← 已无 setpts=N/{nf}/TB
_graph.py:272-285   ⑧ frame_dup（split/trim/tpad/concat）
segment.py:182      concat=n=6:v=1:a=1
```

**是否有后续滤镜覆盖 speed 的时间轴：**

| 候选 | 是否覆盖 | 依据 |
|---|---|---|
| `setpts=N/{nf}/TB` | **已删除** | `_graph.py:250-257` 注释 + 实测 filtergraph 无此串 |
| `fps={nf}` | 不覆盖，按 PTS 重采样 | 见下表实测 |
| `format` / `setsar` | 不涉及时间 | — |
| `rotate`（timeline） | 不涉及时间 | — |
| `zoompan` | 只对窗口段，帧数守恒 | `filters.py:158-201` |
| `concat` | 不覆盖（按各输入自身时长顺序拼接） | 见第七节实测 |

### 实测：整条 `process_single_pass` 的真实输出 duration（14s 素材，6 段，逐段覆盖 speed）

| speed | exp_dur | 实际 format.duration | 实际 nb_frames | 误差 | 单调性 |
|---|---|---|---|---|---|
| 0.90 | 13.966667 | **13.966667** | 419 | 0.000% | — |
| 1.00 | 12.566667 | **12.566667** | 377 | 0.000% | 变短 ✓ |
| 1.10 | 11.533333 | **11.533333** | 346 | 0.000% | 变短 ✓ |

不是靠「filtergraph 里出现了 `setpts=1/speed*PTS`」判定，而是三个真实输出文件的
ffprobe duration 分别为 13.966667 / 12.566667 / 11.533333，严格单调 → **speed 真实生效**。

另一条独立证据（29.97 源，seed 复现）：`[vb_0]`→`[vm_0]` 帧数
seg0 speed=0.9805 → 97 变 99 帧；seg2 speed=1.0362 → 96 变 93 帧。

---

## 六、trim

### 进入路径（源码）

```
randomizer.py:99-101   trim_head = round(rng.uniform(lo,hi),3)
                       trim_tail = round(rng.uniform(lo,hi),3)
segment.py:284         eff_dur = effective_duration(duration, base_snap)
segment.py:51-64       return max(2.0, dur - th - tt)
segment.py:286         cuts = make_equal_cuts(eff_dur, n)
segment.py:297         seg_start, seg_end = trim_head + cuts[i], trim_head + cuts[i+1]
segment.py:149-150     t0 = round(seg_start,3) ; t1 = round(max(t0+0.05, seg_end),3)
segment.py:160-161     {src}trim=start={t0}:end={t1},setpts=PTS-STARTPTS[vt{i}]
segment.py:172         [0:a]atrim=start={t0}:end={t1}[at_in{i}]
processor.py           exp = effective_duration(duration, snap) / speed（质检期望）
```

### 实测对账（seed=1786869947670）

```
source_duration - trim_head - trim_tail = 21.108000 - 0.964 - 0.691 = 19.453000
effective_duration()                                                = 19.453000  ✓
make_equal_cuts(eff,6)[-1]                                          = 19.453000  ✓
最后一个 segment_end - 第一个 segment_start = 20.417 - 0.964        = 19.453000  ✓
duration - trim_tail = 21.108 - 0.691 = 20.417 = 末段 end            ✓
```

| 检查项 | 结论 |
|---|---|
| trim 被重复应用 | 否。`cuts` 基于已扣 head+tail 的 `eff_dur`，`seg_start` 只加 `trim_head` 一次 |
| trim 完全没应用 | 否。首段 `t0 = 0.964 = trim_head` |
| trim 应用到错误时间轴 | 否。`t0/t1` 直接进 `trim=`，同为原始输入时间轴 |
| trim 与 av_offset 重复扣除 | 否。`segment.py:146-148` 已删除窗口平移，`av_offset` 只走音频链 |

**边界隐患（未构造实测）**：`effective_duration` 的 `max(2.0, ...)`（`segment.py:64`）
在 `dur - th - tt < 2.0` 时会返回 2.0，此时 `cuts[-1] = 2.0 > dur - th - tt`，
末段 `t1` 会超过 `duration - trim_tail`，甚至超过 `duration`。
`_trim_frames` 会按 `t1` 预测帧数，而实际流没有那么多帧 → `exp_dur` 偏大。
触发条件：源时长 < `trim_head + trim_tail + 2.0`（约 < 3.2s）。
但 `clamp_segment_count`（`segment.py:47`）要求 `duration >= 3.0` 才可能 n>=2，
且 `decide_segment_count` 在 <15s 时返回 0 → 实际能否触发取决于 GUI 传入的
`requested_count`。标注为【证据不足】：未实测。

---

## 七、concat

### 两处 concat

1. **段内**：`filters.py:458` `[v1][v2]concat=n=2:v=1:a=0[vout]`（frame_dup 的三段拼接）
   —— 纯视频，帧数守恒（实测：180 帧 + tpad 3 帧 = 183 帧）
2. **最终**：`segment.py:180-182`
   `[v_0][af_0][v_1][af_1]...[v_5][af_5]concat=n=6:v=1:a=1[vout][aout]`

### 推进规则实测（构造 2 段，seg1 固定 a=v=2.0s）

| 情形 | seg0 音频 | seg0 视频 | Σ视频帧 | 最终帧 | 补帧 | 结论 |
|---|---|---|---|---|---|---|
| a == v | 2.00 | 2.00 | 120 | 120 | **+0** | 按 video 推进 |
| **a > v（+0.5s）** | 2.50 | 2.00 | 120 | **135** | **+15** | **按 max(v,a) 推进 → 视频空洞被 CFR 补帧** |
| a < v（−0.5s） | 1.50 | 2.00 | 120 | 120 | **+0** | 按 video 推进 |

`+15 帧 = 0.5s × 30fps` —— 精确等于音频超出视频的时长。
**结论：concat 按 `max(video_duration, audio_duration)` 推进下一段起点。**
音频比视频长时，视频侧留下 PTS 空洞，CFR 编码器复制帧填补，最终视频被动变长。
这正是 V2 中 08062c6 基线 +32 帧 / +1.0667s 的机制。

### 当前代码是否满足不变量

29.97fps 源实测：每段 `|a−v| = 0.00000`，Σ支路 589 帧 == 最终 589 帧，**补帧 0** ✓
60fps 源实测：**补帧 5 帧**（因 `win_len` 用了错的 `out_dur`）✗

---

## 八、audio 逐滤镜隔离实测（48000Hz 输入，pitch=+1.158 半音）

```
rate = 2**(1.158/12) = 1.069176
asetrate 应 = 48000 × 1.069176 = 51320
aresample 应 = 48000
```

| 滤镜链 | 实测输出时长 |
|---|---|
| `anull`（基准，5.0s 素材 + AAC 帧对齐） | 5.013333 |
| `atempo=0.98269` 单独 | 5.099625 |
| `asetrate=51320` 单独 | 4.689010 |
| `asetrate=51320,aresample=48000`（不补偿） | 4.689021 |
| `asetrate=51320,aresample=48000,atempo=0.935299`（完整变调） | **5.009896** |
| **【错误对照】`asetrate=47150,aresample=44100,atempo=0.935299`** | **5.452971** |
| `adelay=105:all=1` | 5.118333（+0.105）|
| `atrim=start=0.105,asetpts=PTS-STARTPTS` | 4.908333（−0.105）|
| `apad=whole_dur=6.0` | 6.000000 |
| `atrim=end=4.0` | 4.000000 |
| `atrim=end=4.0,apad=whole_dur=4.0,asetpts`（中性化） | 4.000000 |

要点：
- `asetrate` 的时长效应 = `sr_in / asetrate` → `5.013333 × 48000/51320 = 4.689` ✓ 与实测一致
- `aresample` **不恢复时长**（4.689010 → 4.689021），只把采样率转回去
- 时长必须靠 `atempo=1/rate` 补偿 → 5.009896 ≈ 基准 5.013333（差 0.0033s = AAC 帧栅格）
- 写死 44100 的错误链 → 5.452971，比正确链长 **+8.84%**（= 48000/44100 − 1 = 8.84%）✓

### 真实 `build_audio_filter` 输出对比

| `sample_rate` 参数 | 生成的链 | 实测时长 | 理论 5/0.98269 | 误差 |
|---|---|---|---|---|
| **48000** | `atempo=0.98269,asetrate=51320,aresample=48000,atempo=0.935299` | 5.096979 | 5.088075 | **0.1750%** |
| `None`（回退 44100） | `atempo=0.98269,asetrate=47150,aresample=44100,atempo=0.935299` | 5.547755 | 5.088075 | **9.0345%** |

`sample_rate` 传真实 48000 时误差 0.175%，pitch 仍生效（51320/48000 = 1.069167 vs 理论 1.069176）✓
调用链已全部透传真实采样率：`segment.py:141,175` / `video_processor.py:76,108,140,160` /
`audio_processor.build_audio_args` / `_graph.py:331`。

**残留风险**：`filters.py:490-492` 的 `sr = ... else 44100` 回退分支仍然存在。
若 `probe_media` 未探到 `a_sample_rate`（返回 0/None），会静默回退到 44100 并重新引入 +9% 膨胀。

---

## 九、av_offset

### 语义判定（源码，非日志）

```python
# filters.py:531-538
av = float(p.get("av_offset", 0.0) or 0.0)
if av >= 0.02:
    filters.append(f"adelay={int(round(av * 1000))}:all=1")
elif av <= -0.02:
    filters += [f"atrim=start={-av:.3f}", "asetpts=PTS-STARTPTS"]
```

- **只作用于音频流**，视频链完全不含偏移滤镜
- `segment.py:160` 与 `segment.py:172` 使用**完全相同**的 `t0/t1`
- 没有 `-itsoffset`、没有 `-ss` 差异（`segment.py:206-209` 的命令里只有一个 `-i`）

**答案：A（音频相对视频延迟）。** 不是 B、不是 C、不是 D。

关于「video trim window 和 audio trim window 相同能否产生相对偏移」：
能。相对偏移不是靠错开截取窗口实现的，而是靠 `adelay` 在音频**内容前面插入真实静音样本**
（av>0）或 `atrim` **切掉音频开头**（av<0）。窗口相同是 V2 修复的结果（窗口平移会破坏段连续性）。

### 滤镜级实测（标记素材：前 1.0s 静音 + 之后 1kHz，`win_len=4.0`）

| av_offset | 生成的偏移滤镜 | 段音频长度 | 1kHz 起点 | 相对基准偏移 | 长度 Δ |
|---|---|---|---|---|---|
| +0.000 | （无） | 4.000000 | 1.0026 | +0.0000 | +0.000000 |
| +0.105 | `adelay=105:all=1` | 4.000000 | 1.1076 | **+0.1050** | +0.000000 |
| −0.105 | `atrim=start=0.105` | 4.000000 | 0.8976 | **−0.1050** | +0.000000 |
| +0.010 | （无 —— 死区） | 4.000000 | 1.0026 | +0.0000 | +0.000000 |

偏移量精确、方向正确、**段音频长度完全不变** ✓
`|av| < 0.02` 是**死区**：参数被静默丢弃（`filters.py:535,537`）。

### 端到端实测（整条 `process_single_pass`，标记素材，逐段覆盖 av_offset）

| av_offset | 视频 black_end | 音频 silence_end | A−V 偏移 | 相对基准 |
|---|---|---|---|---|
| +0.000 | 0.0333 | 0.0387 | +0.0053 | +0.0000 |
| +0.105 | 0.0333 | 0.1437 | +0.1103 | **+0.1050** |
| −0.105 | 0.0333 | 2.0667 | +2.0333 | +2.0280 ✗ |

- `av=+0.105` 端到端偏移 **+0.1050**，与设定值精确一致 ✓
- `av=−0.105` 的 +2.0280 是**测量方法失效**，不是代码 Bug：段窗口 0.964→3.021，
  标记点 t=1.0s 落在段内 0.036s 处，`atrim=start=0.105` 把这 0.036s 静音**连同 0.069s 的
  1kHz 一起切掉**，音频从第一个样本起就是 1kHz → `silencedetect` 找不到开头静音，
  返回的是后面某段的静音结束点。负方向已由上面的滤镜级实测（0.8976，精确 −0.105）证明。
  端到端负方向 →【证据不足】（需换用不依赖前导静音的标记方案）。

### 容器级 start_pts

| av_offset | video start_time | audio start_time | video_dur | audio_dur | format_dur |
|---|---|---|---|---|---|
| +0.105 | 0.000000 | 0.000000 | 12.566667 | 12.566000 | 12.566667 |
| −0.105 | 0.000000 | 0.000000 | 12.566667 | 12.566000 | 12.566667 |
| +0.000 | 0.000000 | 0.000000 | 12.566667 | 12.566000 | 12.566667 |

三者完全相同 —— 这是**预期行为**：偏移在每段音频**内容内部**（前置静音/切头），
不是容器级 `start_time` 偏移。`start_time` 无法用来验证 av_offset。

---

## 十、时长账本（29.97fps 源，seed=1786869947670，逐箭头）

```
SOURCE            21.108000 s   (632 帧 @ 29.9700 fps, a_sample_rate=48000)
                  ↓ 源码 segment.py:110  ffprobe 实测
trim              -0.964(head) -0.691(tail) → effective = 19.453000 s
                  ↓ segment.py:51-64,284   公式 max(2.0, dur-th-tt)
segment split     6 段，窗口 0.964→20.417，每段 3.242 s（输入时间轴）
                  ↓ segment.py:286,297     make_equal_cuts + trim_head 偏移
trim 帧精确        Σn_win = 583 帧 @29.97fps
                  ↓ _graph.py:106-113,138  ceil(t1*fps)-ceil(t0*fps)
reverse_loop      +0 帧（本 seed 6 段全未触发；filtergraph 无 split=3/reverse）
                  ↓ _graph.py:87-103,197-205
frame_drop        -3 帧（输入空间帧号 32 / 78 / 70）
                  ↓ _graph.py:48-75,142-144 ; filters.py:398-436
(保留)            Σn_kept = 580 帧
speed             逐段 setpts=1/speed*PTS → Σvm_dur = 19.269993 s
                  ↓ _graph.py:147,219-220
fps=30 重采样      Σframes_pre_dup = 579 帧 = 19.300000 s
                  ↓ _graph.py:149,254-257   round(vm_dur × out_fps)
frame_dup         +10 帧 → Σframes_out = 589 帧 = 19.633333 s
                  ↓ _graph.py:150-154,272-285 ; filters.py:439-460
audio 处理         每段 atempo/asetrate(51320)/aresample(48000)/EQ/HP/LP/afade
                  ↓ filters.py:480-530 ; 实测单滤镜时长见第八节
av_offset         adelay=105ms 或 atrim（只动音频，长度不变）
                  ↓ filters.py:531-538     实测 Δ长度 = 0.000000
audio 中性化       atrim=end + apad=whole_dur = 段视频输出时长 → |a-v| = 0.00000
                  ↓ filters.py:543-551     逐段实测 6/6 为 0
segment concat    段内 frame_dup 的 concat=n=2 不改总帧数
                  ↓ filters.py:458         实测 180+3 = 183
final concat      concat=n=6:v=1:a=1 → 补帧 0 帧
                  ↓ segment.py:180-182     实测 Σ589 == 最终 589
CFR 归一化         libx264 CFR 30fps
                  ↓ segment.py:205-209
FINAL VIDEO       19.633333 s  (589 帧)   audio=19.633000   format=19.633333
exp_dur = 19.633333   误差 = 0.0000%
```

### 逐段明细（预测，且每一列都与实测支路帧数一致）

| seg | t0→t1（输入） | speed | n_win | rl | drop | kept | vm_dur | pre_dup | dup | out | out_dur |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 0.964→4.206 | 0.9805 | 98 | 0 | 1 | 97 | 3.300938 | 99 | 2 | 101 | 3.366667 |
| 1 | 4.206→7.448 | 0.9979 | 97 | 0 | 1 | 96 | 3.209944 | 96 | 1 | 97 | 3.233333 |
| 2 | 7.448→10.691 | 1.0362 | 97 | 0 | 1 | 96 | 3.091298 | 93 | 3 | 96 | 3.200000 |
| 3 | 10.691→13.933 | 0.9921 | 97 | 0 | 0 | 97 | 3.262342 | 98 | 2 | 100 | 3.333333 |
| 4 | 13.933→17.175 | 1.0046 | 97 | 0 | 0 | 97 | 3.221750 | 97 | 1 | 98 | 3.266667 |
| 5 | 17.175→20.417 | 1.0166 | 97 | 0 | 0 | 97 | 3.183720 | 96 | 1 | 97 | 3.233333 |

---

# 十二、分类结论

## A. 已确认正确（有实测证据）

| # | 项 | 证据 |
|---|---|---|
| A1 | trim 口径唯一、无重复扣除、无遗漏 | 四式对账全部 = 19.453000（第六节） |
| A2 | 段窗口严格连续，音视频窗口一致 | 段间缝隙 `+0.000000 ×5`（第一节） |
| A3 | speed 真实生效且单调 | 13.966667 / 12.566667 / 11.533333，误差均 0.000%（第五节） |
| A4 | 标准化尾已无 `setpts=N/{nf}/TB` | 实测 filtergraph 无此串（第五节） |
| A5 | frame_drop 换算公式 `input_frame = output_time × speed × src_fps` 成立 | 反算输出时刻全部落窗（第三节） |
| A6 | frame_drop 顺序正确（select 在变速前） | `_graph.py:213-224` + 实测 `[vb]` 帧数 = n_kept（第三节） |
| A7 | frame_dup 的 `t_pos` 基于真实输出流时长 | `dup_dur` == 实测 `[vm]`/30，6/6 组克隆位置 ≤1 帧（第四节） |
| A8 | frame_dup 增帧量准确 | 理论 +3 == 实测 +3，6/6（第四节） |
| A9 | reverse 模式不改长度 | Δv = Δa = 0，3/3 speed（第二节） |
| A10 | loop 的视频/音频增量一致 | `|Δv−Δa| = 0.00000`，9/9 组（第二节） |
| A11 | 变调用真实采样率，时长不膨胀 | 48000 → 误差 0.1750%；写死 44100 → 9.0345%（第八节） |
| A12 | `aresample` 不恢复时长，必须靠 `atempo` 补偿 | 4.689010 → 4.689021 → 5.009896（第八节） |
| A13 | av_offset 语义 = A（音频相对视频延迟），只动音频 | 源码 `filters.py:531-538` + onset ±0.1050（第九节） |
| A14 | av_offset 不改变段音频长度 | 4 档实测 Δ长度全为 `+0.000000`（第九节） |
| A15 | concat 按 `max(v,a)` 推进（机制确认） | a>v → +15 帧 = 0.5s×30（第七节） |
| A16 | 音频中性化使 `|a−v| = 0`，concat 补帧 0 | 6 段全 0，Σ589 == 最终 589（第七、十节） |
| A17 | exp_dur 公式在「源帧率 ≤ 目标帧率」时精确 | 误差 0.0000%（第十节） |

## B. 已确认 Bug（本轮新发现，当前代码仍存在）

### B1【P0】源帧率 > normalize.fps 时，帧号/帧数换算全线错位

- **位置**：`segment.py:119`（A 层插入 `fps={eff_fps}`）与 `segment.py:154`
  （传 `src_fps = media_info["fps"]`）矛盾；`video_processor.py:93-94` 与 `:74-75` 同病
- **实际错误**：60fps 源 → 30fps，代码算 n_win=195，真实 97；
  计划删 8 帧实际只删 4 帧；Σ预测 587 vs Σ实测 584；concat 补帧 5 帧
- **为什么**：`select`/`_trim_frames` 作用在**已降帧**的流上，帧号必须按 `eff_fps` 换算
- **影响面**：所有 60fps / 50fps / 120fps 素材（短视频平台常见）
- **是否被质检拦住**：不会。误差 0.3407% < ±2% → **静默错误**

### B2【P1】`plan.reverse_loop.seg_len` 在错误的时间空间量化

- **位置**：`randomizer.py:518-525`（按 `normalize.fps` 量化，输出栅格）
  vs `_graph.py:200-202` / `filters.py:349-351`（当作输入时长使用）
- **实际错误**：speed=0.95 残差 0.007018s；speed=1.05 残差 0.006349s；
  loop rep=2 speed=1.05 时输出增量 0.100000 而理论应为 0.127111（差 0.0271s ≈ 0.8 帧）
- **为什么**：量化目标（让音视频增量相等）已由 `win_len` 中性化独立达成，
  当前量化只是把误差从音视频差挪到了「理论值 vs 实际值」上
- **影响面**：所有 loop 事件 + speed≠1 的段；残差 ≤1 输出帧，不导致时长失控

### B3【P2】`plan_lens_events` 在整文件路径用了输出时间轴

- **位置**：`video_processor.py:95` 传 `seg_dur / speed`，但 lens 经 `pre_chain`
  接在 `_graph.py:208-211`（③抽帧/④变速之前 = 输入空间）
- **实际错误**：speed≠1 时畸变事件窗口整体缩放 `1/speed`
- **注**：分段路径 `segment.py:123` 传 `duration` 且 lens 在 `[0:v]` 上 → 正确，不受影响

### B4【P2】`av_offset` 存在 ±0.02s 死区

- **位置**：`filters.py:535,537`（`if av >= 0.02` / `elif av <= -0.02`）
- **实际错误**：`av_offset = +0.010` 实测偏移 `+0.0000`，参数被静默丢弃
- **影响面**：预设把 `av_offset.min` 设到 0.02 以下时该档位完全无效

### B5【P2】变调采样率仍保留 44100 静默回退

- **位置**：`filters.py:490-492` `sr = int(sample_rate) if ... else 44100`
- **实际错误**：`a_sample_rate` 探测失败（0/None）时回退 44100 →
  实测重新引入 **+9.0345%** 时长膨胀，且不报错
- **影响面**：`probe_media` 无法解析音频流信息的素材

## C. 已修复但尚未验证

| # | 项 | 状态 |
|---|---|---|
| C1 | 全部 V2 修复（P0-1/P0-2/P0-3/P0-4/P1-1…P1-7 + 取证 + 清理） | 代码已改、V2 与本轮均实测通过，但**未 commit / 未 push**。GitHub main 仍是 `08062c6`，线上代码仍带全部原始 Bug |
| C2 | 降级路径（`video_processor.process_clip`，`process_single_pass` 失败时启用） | 已同步透传 `src_fps` / `a_sr` / `win_len` / `metrics`（`video_processor.py:74-76,108-109,127-130,140,160-161,189-190`），但**本轮未对降级路径做端到端 FFmpeg 实测** |
| C3 | `_merge_reencode` 合并路径（`segment.py:384-455`） | 未审计、未实测 |

## D. 当前仍存在的 Bug

即 B1 ~ B5，全部在当前工作区代码中仍然存在。按严重度：

1. **B1（P0）** —— 影响正确性且静默，必须先修
2. **B2（P1）** —— 精度问题，≤1 帧
3. **B3 / B4 / B5（P2）** —— 边界/回退路径

另附两处非时间轴的代码卫生问题（不影响输出）：

- `segment.py:98-99` 的 docstring 仍写「此处两支路 trim 窗口同步平移」，
  与 `segment.py:146-148` 的实际实现（已删除平移）矛盾
- `filters.py:450` `fps = max(1.0, float(p.get("_fps", 25.0) or 25.0))` 读取后从未使用（死代码）；
  `_graph.py:274-275` 仍在为它赋值

## E. 证据不足

| # | 项 | 缺什么 |
|---|---|---|
| E1 | av_offset 端到端**负方向** | `silencedetect` 依赖前导静音，而 `atrim=start` 把静音连同部分 1kHz 一起切掉 → 测量方法失效。需换用「音频内嵌周期性脉冲」等不依赖前导静音的标记 |
| E2 | `effective_duration` 的 `max(2.0, ...)` 边界 | 需构造 `duration < trim_head+trim_tail+2.0`（约 <3.2s）且 `requested_count>=2` 的用例，验证末段 `t1` 是否越界、`exp_dur` 是否偏大 |
| E3 | 降级路径（6 段独立编码 + `_merge_reencode`） | 未做端到端实测；`-ss/-t` 输入裁剪与 `segment_video_metrics`（无 `t0/t1`，走 `round(seg_len×fps)`）的一致性未验证 |
| E4 | rl 触发时 frame_drop 帧号平移 | 结构性问题已在源码定位（`_graph.py:142-144` 未考虑 rl 插入点后的帧号偏移），但本 seed 6 段 rl 全未触发，未构造「rl + frame_drop 同段触发」的实测 |
| E5 | 原始事故运行（18:13:31，输出 22.4s） | 源码树是未提交中间态、输出已删、命令未记录（V2 已记录同一结论） |
| E6 | `zoompan` 窗口切段是否严格帧数守恒 | 本轮所有实测均在 `zoom.on=False` 下进行；`filters.py:158-201` 的切段拼接未做帧数实测 |

## F. 必须修改的源码位置

| Bug | 文件:行 | 当前代码 |
|---|---|---|
| B1 | `segment.py:105,108,119` | `fps = media_info["fps"]` ; `eff_fps = norm_spec["fps"]` ; `common = (f"fps={eff_fps:.3f}," if eff_fps+1e-6 < fps else "") + geom` |
| B1 | `segment.py:153-154` | `segment_video_metrics(..., speed, fps, eff_fps, t0=t0, t1=t1)` |
| B1 | `segment.py:162-165` | `build_segment_branch(..., src_fps=fps, metrics=met)` |
| B1 | `video_processor.py:74-75` | `segment_video_metrics(..., speed, fps, eff_fps)` |
| B1 | `video_processor.py:93-94` | `common = (f"fps={eff_fps:.3f}," if norm_spec and eff_fps+1e-6 < fps else "") + geom` |
| B1 | `video_processor.py:127-130` | `build_segment_branch(..., src_fps=fps, metrics=met)` |
| B1 | `video_processor.py:189-190` | `frame_drop_chain(..., met["n_in"], ..., fps, speed)` |
| B2 | `randomizer.py:515-525` | `out_fps = config_get(config,"normalize.fps",30)` ; `rl_frames = round(raw_len*out_fps)` ; `"seg_len": rl_frames/out_fps` |
| B3 | `video_processor.py:95` | `plan_lens_events(snap, config, seg_dur / speed)` |
| B4 | `filters.py:534-538` | `if av >= 0.02: ... elif av <= -0.02: ...` |
| B5 | `filters.py:489-492` | `sr = int(sample_rate) if sample_rate and int(sample_rate) > 0 else 44100` |
| 卫生 | `segment.py:98-99` | 过期 docstring |
| 卫生 | `filters.py:450` / `_graph.py:274-275` | `_fps` 死代码 |

## G. 修改方案

### G1（B1）引入「进入段分支时的真实帧率」概念

核心：**区分 `media_info.fps`（源帧率）与 `branch_fps`（进入 `[vt{i}]` 时流的真实帧率）。**
A 层是否插入 `fps=` 决定了后者：

```
branch_fps = eff_fps  if (norm_spec and eff_fps + 1e-6 < fps) else fps
```

- `segment.py`：算出 `branch_fps`，把 `segment_video_metrics(..., src_fps=branch_fps, ...)`
  与 `build_segment_branch(..., src_fps=branch_fps, ...)` 全部改为传 `branch_fps`
- `video_processor.py`：同样按 `norm_spec and eff_fps+1e-6 < fps` 计算 `branch_fps`，
  用于 `segment_video_metrics` / `build_segment_branch` / `frame_drop_chain`
- 建议同时把 `_graph.py:182-183` 的 docstring 从「源帧率」改为「进入本分支时流的真实帧率」，
  并在 `segment_video_metrics` 里加一句注释说明该参数不是 `media_info.fps`

判定条件必须与 A 层的 `if` **逐字一致**（同一个 `eff_fps + 1e-6 < fps`），否则又会分叉。

### G2（B2）rl 片段长量化到「输入」帧栅格，或直接不量化

两个方案，取其一：

- **方案 a（推荐）**：`randomizer.py` 不再量化，`seg_len` 直接取 `rng.uniform(elo, ehi)`；
  帧精确性交给已有的 `rl_extra_frames`（`_graph.py:87-103`，本来就用 `_trim_frames` 算真实帧数）。
  量化的原始动机（音视频增量相等）已由 `win_len` 中性化解决，量化已无必要。
- **方案 b**：把量化改到输入栅格 —— 但 `randomizer` 拿不到 `src_fps`/`branch_fps`，
  需要改签名传入，成本高于收益。

### G3（B3）lens 事件窗口改用输入时间轴

`video_processor.py:95`：`plan_lens_events(snap, config, seg_dur)`（去掉 `/ speed`），
与 `segment.py:123` 的口径统一（都用未除 speed 的输入时长）。

### G4（B4）死区改为「有值就生效」

`filters.py:534-538`：阈值从 `0.02` 降到一个纯粹的浮点噪声门限（如 `1e-4`），
并对 `adelay` 的毫秒取整做保护（`av*1000 < 1` 时跳过，因为 `adelay=0` 无意义）。

### G5（B5）采样率探测失败改为显式失败或用容器值

`filters.py:489-492`：把静默回退 44100 改为
（1）优先用 `sample_rate`；（2）缺失时**不加变调滤镜**（放弃 pitch 而不是引入 9% 膨胀），
并由调用方 log 一条告警。绝不能保留「静默按 44100 处理」。

### G6 卫生

- 更新 `segment.py:98-99` docstring，改为描述当前实现（窗口不平移，偏移由音频侧实现）
- 删除 `filters.py:450` 的 `fps` 局部变量与 `_graph.py:274-275` 的 `_fps` 赋值

## H. 修改后的验证方案

全部加进 `tests/test_timeline_integrity.py`（现有 11 项全过，需扩到 16 项）：

| # | 新增测试 | 判定口径 |
|---|---|---|
| H1 | `test_branch_fps_matches_stream`（B1） | 素材 fps ∈ {25, 29.97, 30, 50, 60, 120} × normalize.fps=30，走完整 `process_single_pass`：逐段 `met["n_win"] == 实测[vt]帧数`（帧精确）；`met["n_kept"] == 实测[vb]帧数`；`Σframes_out == 最终 nb_frames`（≤1 帧）；`concat 补帧 == 0` |
| H2 | `test_frame_drop_actually_drops`（B1） | 同上帧率矩阵：`计划删帧数 == 实测(vt − vb)`，且每个帧号 `< 该段实测[vt]帧数` |
| H3 | `test_exp_dur_all_fps`（B1） | 上述帧率矩阵下 `|out − exp_dur| / exp_dur < 0.5%`（比现有 2% 更严，因为修好后应当帧精确） |
| H4 | `test_rl_output_delta_matches_theory`（B2） | loop rep∈{2,3} × speed∈{0.90,0.95,1.00,1.05,1.10}：`|实测输出增量 − rl_input/speed| ≤ 1/out_fps`，且 `|Δv−Δa| ≤ 1/out_fps` |
| H5 | `test_lens_event_window_input_space`（B3） | speed≠1 时，`lenscorrection` 的 `enable=between(t,a,b)` 中 a/b 必须等于 `plan_lens_events(…, seg_dur)` 的原值（不含 `1/speed` 缩放） |
| H6 | `test_av_offset_small_values`（B4） | av ∈ {+0.005, +0.01, +0.03, −0.01, −0.03}：实测 onset 偏移与设定值误差 < 5ms，段音频长度不变 |
| H7 | `test_pitch_without_sample_rate`（B5） | `sample_rate=None/0` 时：链中**不得**出现 `aresample=44100`；输出时长与 `1/atempo` 理论值误差 < 1% |
| H8 | `test_degraded_path_end_to_end`（E3） | 强制 `process_single_pass` 失败 → 走降级 6 段独立编码 + `_merge_reencode`，校验最终时长与 `exp_dur` 误差 < 2%、音视频流齐全 |
| H9 | `test_rl_with_frame_drop_same_segment`（E4） | 强制同段同时触发 rl(loop) 与 frame_drop：删帧帧号必须落在「考虑 rl 平移后」的输入窗口内；`n_kept` 预测 == 实测 |
| H10 | `test_zoom_window_frame_conservation`（E6） | `zoom.on=True`：`[vm]` 帧数与 `zoom.on=False` 相同（切段拼接不得增删帧） |
| H11 | `test_effective_duration_short_clip`（E2） | `duration ∈ {2.5, 3.0, 3.2}` 且 `requested_count=2`：末段 `t1 ≤ duration`；`exp_dur` 与实际误差 < 2% |
| H12 | `test_av_offset_negative_end_to_end`（E1） | 改用「音频内嵌 3 个等间隔 1kHz 脉冲 + 视频同步黑白翻转」素材，用互相关或逐脉冲定位测 A−V 偏移，覆盖 av<0 |

补充要求：

- H1/H2/H3 必须真正跑 FFmpeg 并解码计帧，不能只对 filtergraph 做正则匹配
- 修 B1 时**禁止**用「把 A 层的 `fps=` 挪到 trim 之后」来绕过 —— 那会让重滤镜按源帧率计算，
  丢掉 `segment.py:115-116` 注释里的性能收益；必须走 `branch_fps` 口径统一
- 修 B2 若采用方案 a（去掉量化），必须同时确认 H4 与现有
  `test_reverse_loop_av_delta_equal` 都通过；不得为了通过而放宽 `1/out_fps` 判定
- 全部改完后重跑现有 11 项，确认无回归

---

## 附：与前两版报告的关系

| 报告 | 状态 |
|---|---|
| V1 `PARAMETER_CALIBRATION_REPORT.md` | 结论「一切正确」已被 V2 的 A/B 实测推翻（误差 +7.2845%、concat 补帧 32）。仅作历史参考 |
| V2 `PARAMETER_CALIBRATION_REPORT_V2.md` | 本轮全部复核项均与 V2 一致（A1~A17）。V2 的盲区是 `test_frame_drop_window` 只孤立测 `frame_drop_plan(src_fps=60)`，未让 60fps 素材真正走 `process_single_pass` → 漏掉 B1 |
| V3（本报告） | 新增 B1~B5 五项当前仍存在的 Bug，并给出 H1~H12 验证方案 |

**未修改任何代码；未 commit；未 push。**


