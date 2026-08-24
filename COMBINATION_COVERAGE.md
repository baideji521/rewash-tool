# COMBINATION_COVERAGE.md — 参数组合覆盖矩阵（V8）

> 生成口径：本文件由 `tests/param_forensics_v6.py phase_combo`（8 组 × 2 素材）
> 与 `tests/param_forensics_v7.py phase_combo19`（19 组）的**实测**用例清单汇总。
> 每一格的判定与数值以证据库为准（`tests/evidence/index.json` 的
> `group=matrix/combinations`）；本文件只做覆盖与状态索引，不重复数值。
> 判定含义：PASS=实测且守恒；BUG=实测且越界；EVIDENCE_INSUFFICIENT=未真实渲染/
> 无法度量/基线与覆盖值相同；INEFFECTIVE=执行了但无可观测变化。

## 1. 被组合的"因子"

- A 时间轴类：`speed`、`frame_dup`、`frame_drop`、`reverse_loop`、
  `trim_head/trim_tail`、`normalize.fps`
- B 几何/事件类：`zoom_drift`（窗口切段）、`rotate_drift`（timeline enable）、
  `lens_k1`、`asym_crop_*`、`scale`
- C 音频类：`audio_pitch`、`audio_atempo`、`audio_fade`、`av_offset`、
  `audio_highpass/lowpass`、`audio_eq`
- D 编码类：`normalize.video_codec`（NVENC / CPU）、`cpu_preset`、码率

## 2. v6 组合（8 组 × TEST-C 30fps / TEST-E 60fps = 16 用例）

判定源：`RUN-000079`（B55 修复后重跑）— 16/16 PASS，Δ帧 全 0。

- `c01_speed_drop_rl_dup` = speed × frame_drop × reverse_loop × frame_dup
  （A×A×A×A，四个帧数改写者叠加）— PASS / PASS
- `c02_slow_speed_rl` = speed<1 × reverse_loop — PASS / PASS
- `c03_drop_only` = frame_drop 单因子基准 — PASS / PASS（|a−v|=−0.000333）
- `c04_dup_only` = frame_dup 单因子基准 — PASS / PASS
- `c05_trim_speed_av` = trim_head/tail × speed × av_offset — PASS / PASS
- `c06_full_stack` = speed × drop × rl × dup × trim × 颜色 × 几何 × 音频
  （全栈）— PASS / PASS
- `c07_zoom_rotate_speed` = zoom_drift × rotate_drift × speed
  — **修复前 BUG（Δ帧=3，B55）→ 修复后 PASS / PASS**
- `c08_audio_stack` = pitch × atempo × highpass × lowpass × fade × av_offset
  — PASS / PASS

## 3. v7 19 组合（`phase_combo19`，A / B / A+B / A+C / B+C / A+B+C 结构）

- 单因子基线：`x02`(dup) `x07`(lens) `x13`(pitch) `x14`(atempo) `x17`(trim)
  `x18`(audio_dur)
- A+A：`x01` speed×drop、`x03` speed×reverse、`x04` speed×rl
- A+B：`x05` speed×zoom、`x06` speed×rotate、`x08` speed×zoom×rotate
- A+A(fps)：`x09` fps×drop、`x10` fps×dup、`x12` fps×rl
- A+B(fps)：`x11` fps×zoom
- A+C：`x15` av_offset×trim、`x16` av_offset×speed
- A+B+C 全叠加：`x19` fps×speed×dup×drop×zoom

## 4. 关键对（A、B、A+B、A+C、B+C、A+B+C）与覆盖来源

- speed + frame_drop → v6 c01、v7 x01
- speed + frame_dup → v6 c01/c04、v7 x02/x19
- speed + reverse_loop → v6 c01/c02、v7 x03/x04
- speed + zoom_drift → **v6 c07、v7 x05/x08/x19**（B55 的暴露组合）
- speed + rotate_drift → v6 c07、v7 x06/x08
- speed + lens → v7 x07
- normalize.fps + 帧事件 → v7 x09/x10/x11/x12/x19
- trim + speed + av_offset → v6 c05、v7 x15/x16/x17
- 音频链内部叠加（pitch+atempo+滤波+fade）→ v6 c08、v7 x13/x14/x18
- 全栈 → v6 c06、v7 x19
- zoom_drift + rotate_drift + speed 的**坐标空间**（不是帧数）→
  `v6 wincoord/plan_vs_filtergraph`：W1 推镜在变速之前、W2 推镜坐标 ×speed、
  W3 微旋坐标原样、W4 B 片绝对 `end=` 写法、W5 相邻切点字符串共享
  （判据 B57 后由 3 项增至 5 项；`RUN-000098` 5/5 PASS）

## 5. 覆盖缺口（诚实记录，未测 ≠ 通过）

- **编码类 × 组合**：NVENC 与 CPU 两种编码器只在 `phase_encoder` 单独取证，
  没有与 A/B/C 因子做组合渲染 → 组合维度记 EVIDENCE_INSUFFICIENT。
- **fingerprint.* / switches.\* 组合**：只有静态/单参取证，无组合渲染。
- **mask_drift × 其它**：出厂配置门关闭（`INEFFECTIVE by design`），
  未在组合中打开测量。
- **降级路径 × 组合**：v6/v7 的组合用例全部走 `single_pass`；
  组合在 `fallback` / `merge_reencode` 上的表现仅由 `paths3` 的固定参数集覆盖
  （见 `PATH_COVERAGE_MATRIX.md`）。
