# PARAMETER_CALIBRATION_REPORT_V4

本轮 = 【全参数自动取证 + Bug 修复】的第三、四阶段成果。
前置文档：`PARAMETER_AUDIT_MATRIX.md`（阶段一，97 项枚举）、
`PARAMETER_BUG_REPORT_V4.md`（阶段二，Bug 台账 + 修复记录）。
`PARAMETER_CALIBRATION_REPORT_V3.md` 为上一轮结论，冲突处**以本轮源码 + 实测为准**并在文末列出 V3 discrepancy。

## 0 环境与仓库状态（只记录，未做任何 git 变更）

- 时间：2026-08-23
- `HEAD`：**detached**，`a828758 Add files via upload`
- `git log -5 --oneline`：`a828758` / `74874ed` / `08062c6` / `946065e` / `23069df`
- `git status --porcelain`（本轮结束时）：

```
AA PARAMETER_CALIBRATION_REPORT_V2.md
D  tests/reconstruct_test.py
A  tests/test_timeline_integrity.py
M  video_rewash/audio/audio_processor.py
MM video_rewash/core/_graph.py
M  video_rewash/core/ffmpeg_runner.py
M  video_rewash/core/processor.py
M  video_rewash/core/quality_check.py
MM video_rewash/core/randomizer.py
MM video_rewash/core/segment.py
M  video_rewash/video/filters.py
MM video_rewash/video/video_processor.py
?? PARAMETER_AUDIT_MATRIX.md  PARAMETER_BUG_REPORT_V4.md
?? PARAMETER_CALIBRATION_REPORT_V3.md  tests/param_forensics.py  tests/evidence/
```

- `AA` / `A` / `D` / 未合并状态**只报告不处理**；未执行 `reset` / `clean` / `checkout` / `restore` / `commit` / `push`。
- 本轮实际改动的产品文件：`core/segment.py`、`core/_graph.py`、`core/randomizer.py`、`video/video_processor.py`（均为 `MM` 中的工作区部分）。

## 1 方法与判据

- 素材（`tests/param_forensics.py` 自动合成，30s，可识别帧号 + 时间码 + 立体声 L=1kHz / R=440Hz）：
  TEST-A 25fps、TEST-B 29.97、TEST-C 30、TEST-D 50、TEST-E 60、TEST-F 120（均 48000Hz）；
  TEST-B44 / TEST-E44 为 44100Hz 对照。
  时长必须 ≥30s：`generate_segment_plan` 要求段长 ≥2.0s 才生成 frame_drop / reverse_loop 事件，12s/6 段取不到证据。
- 路径：真实产品路径 `process_segmented → process_single_pass → FFmpeg`（6 段），`seed=1786869947670`。
- 取证手段：monkeypatch `segment.run_ffmpeg` 捕获真命令 → 落盘 → 逐支路（`[vt] [vb] [zout] [vm] [v]`）用 `-f null` 数帧 → 音频渲染 `pcm_s16le` 后 `ffprobe` 取精确秒 → 执行整条命令并 `ffprobe` 输出。
- 容差：时长 ≤ 0.01s；帧 ≤ 1 帧；段内 `|a−v| ≤ 1/out_fps`。
- 判据编号：C1 branch_fps / C2 frame_drop 计划==实删 / C3 drop 帧号不越界 / C4 段内 A-V 不变量 / C5 concat 补帧 / C6 exp_dur / C7 采样率收敛；D1 同 seed 一致 / D2 异 seed 相异 / D3 派生 seed 无碰撞。
- 证据落盘：`tests/evidence/<task>/<TEST-ID>/` 含 `input_probe.json`、`output_probe.json`、`command.txt`、`filtergraph.txt`、`parameter_snapshot.json`、`segment_plan.json`、`frame_metrics.json`、`audio_metrics.json`、`result.json`。

## 2 修复前 → 修复后（逐参数）

### A 类·时间轴

- `fps` 降帧 / branch 帧率（B1，P0）
  - 修复前：A 层 `fps={eff_fps}` 插在 `trim` 之前，但 `segment_video_metrics` 与 `build_segment_branch` 收到的是**源** fps。60fps 源实测 `n_win` 预测 284、实测 142（2×）；50/60/120 全中。
  - 修复后：`branch_fps` 统一口径；C1/C2/C3 全 PASS，帧号越界 4/7/17 → 0。
  - 证据：`tests/evidence/fps_matrix/TEST-{D,E,F}/frame_metrics.json`
- `frame_drop`（抽帧）
  - 修复前：50/60/120 源计划 10/14/28 帧、实删 6/6/11 帧（select 帧号超出该段帧数直接失效）。
  - 修复后：计划==实删（6/6，全素材），帧号全部 `< [vt]`。
  - 25/29.97/30 源修复前后均一致（3~6 处，计划==实删）。
- `trim` 帧数量化（B11，P1，本轮新发现）
  - 修复前：降帧后仍按 `ceil` 口径，3/6 段差 ±1 帧 → 经 `win_len` 传给音频 → `concat` 补帧。
  - 修复后：`coarse_tb` 分支（`round` 口径），预测 = 实测（6/6 段）。
  - 直接证据：`fps=30,trim=5.688~10.412` 实测 141 帧（末帧 `pts_time` 10.3667=311/30），`ceil` 预测 142 ✗ / `round` 预测 141 ✓。
- `zoom_drift`（推镜窗口，B10，P1）
  - 修复前：`zoompan=fps=30.000` 施加在 25fps 流上 → 该切片 PTS 跨度按 25/30 压缩，seg5 实测 133 帧 vs 预测 138，段内 `|a−v| = 0.166666`（5 帧 = 容差 5 倍）。
  - 修复后：`zoompan=fps=` 用 `branch_fps`；`|a−v| = 0.033334`（1 帧，达标）。帧数守恒 117→117 修复前后不变。
- `speed`：0.90/0.95/1.00/1.05/1.10 由 `test_timeline_integrity` 的真实输出时长核对，`setpts=1/speed*PTS` **未**被 `setpts=N/FPS/TB` 覆盖（标准化尾已不含该滤镜）→ PASS，本轮无变化。
- `frame_dup`：`tpad=start=N:start_mode=clone` 位于 `[vm]` 之后，克隆帧号预测==实测（`test_frame_dup_position` 9 组 pos×speed 全中）→ PASS，本轮无变化。
- `reverse_loop`：repeats 0/1/2/3 × speed 0.95/1.0/1.05，`|Δvideo − Δaudio| ≤ 1 帧`，`seg_len` 判定为**输入时长**（`rl_input_seconds` / `rl_extra_frames` 均在输入空间）→ PASS，本轮无变化。
- `trim_head` / `trim_tail`：0 / 0.5 / 1.0 在 single-pass 与降级路径 → PASS（沿用 V3 实测）。
- `concat` 推进规则：每段推进 `max(v_dur, a_dur)`；修复后段内 `|a−v|` 全 ≤1 帧且 `a` 不再系统性大于 `v` → 补帧 2/2/4 → **0**。
- `exp_dur` 账本：TEST-D/E/F 相对误差 **0.2338% / 0.2353% / 0.2338% → 0.0024%**；A/B/C 维持 0.117%（= 1 帧的容器口径差，绝对值 0.033s）。

### D 类·音频

- 采样率（C7，覆盖 V3 的 44100 硬编码质疑）
  - 48000Hz 素材：6 处 `asetrate=R` 后全部 `aresample=48000`；44100Hz 素材：6 处全部 `aresample=44100`。**未发现 44100 硬编码逃逸**。
  - 44100Hz 端到端：TEST-B44 / TEST-E44 全判据 PASS（`exp_dur` 误差 0.1175% / 0.0023%）。
  - 结论：`filters.py` 的 `sr = 44100` 仅在探测不到采样率时兜底；本轮两种真实采样率均未触发。
- `win_len` 音频长度对齐：段内 `|a−v|` 全素材 worst = 0.033334（1 帧）→ PASS。
- `av_offset`：滤镜级已证（onset 0.8976 = −0.105 精确命中）；端到端负向测量因 `atrim` 吃掉前导静音仍为【证据不足】（见 §4 E1）。

### F 类·随机

- `seed` 决定性（D1）：同 seed 两次 → filtergraph **字节一致**（6801 字节）、快照除 `ts` 外一致。`ts` 是墙上时钟元数据，非参数。
- 异 seed（D2）：5 个 seed → 5 个互不相同的 filtergraph。
- 派生 seed（D3，B12，P2）
  - 修复前：`child_0 = base + 0*7919 = base`、`plan_0 = base + 0*104729 = base` → 与全片快照同源，第 0 段 B/C 层参数与全片快照完全相同。
  - 修复后：`(seg_idx+1)*` 偏移；21 个派生 seed 零碰撞。**未改任何随机取值区间**。
  - 副作用即回归证据：事件计划整体变化（抽帧 4~5 处 → 3~6 处）后 8 个素材仍全 PASS。

## 3 分类结论

### PASS（有实测证据）

1. `fps` 降帧 / branch 帧率（C1，6 素材）
2. `frame_drop` 计划==实删（C2，8 素材）
3. `frame_drop` 帧号合法性（C3）
4. 段内 A-V 不变量（C4，worst 0.033334 ≤ 1/30）
5. `concat` 补帧（C5，全 0）
6. `exp_dur` 账本（C6，最坏 0.1175%，绝对 0.033s ≤ 1 帧）
7. 采样率收敛（C7，48000 与 44100 各 6 处）
8. `trim` 帧数量化（B11 修复后 6/6 段命中）
9. `zoom` 窗口帧数守恒 + PTS 跨度（B10 修复后）
10. `speed`（真实输出时长，5 档）
11. `frame_dup` 克隆帧位置（9 组）
12. `reverse_loop` A-V 一致（rl 矩阵）
13. `trim_head` / `trim_tail`
14. `setpts` 变速未被覆盖
15. seed 决定性 D1
16. 异 seed 相异 D2
17. 派生 seed 无碰撞 D3
18. `REWASH_DUMP_CMD` 命令/快照落盘（`test_cmd_dump`）

### BUG（已修复并复测）

- B1【P0】FPS 域错位 → 已修复 ✅
- B10【P1】`zoompan=fps=` 压缩 PTS 跨度 → 已修复 ✅
- B11【P1】降帧后 trim 帧数量化口径 → 已修复 ✅
- B12【P2】派生 seed 撞车 / 第 0 段未独立随机 → 已修复 ✅

### BUG（沿用 V3，本轮未复测，状态不变）

- B2–B5：见 `PARAMETER_BUG_REPORT_V4.md`「沿用 V3 的 Bug」。
- B6–B9：源码级已定位、尚无实测，不得当作已确证。

### INEFFECTIVE

- `mask_drift`、`channel_mix`、`noise`、`audio_noise_db`、`_fps`（详见 `PARAMETER_AUDIT_MATRIX.md`）。本轮未改动。

### PARTIAL

- `av_offset`：滤镜级方向与幅度已证，端到端负向未证。
- `lens`（畸变事件）：窗口表达式已证，画面统计未证。
- 降级路径（`video_processor` 复杂图）：`branch_fps` / `coarse_tb` 已同口径修复，但**未跑端到端取证**（本轮所有测量都在 single-pass 路径）。
- `_merge_reencode`：未取证。

### REGRESSION

- 无。修复前 PASS 的判据在修复后全部仍 PASS（含换一整套事件计划后的复跑）。

### EVIDENCE_INSUFFICIENT

- E1 `av_offset` 端到端负向（测量工具受 `atrim` 影响）
- E2 视觉参数的图像统计（亮度均值/方差、HSV 饱和度、色相直方图、有效画面区域、边界/角度、时空方差）
- E3 路径矩阵：segmented / fallback / `_merge_reencode`
- E4 编码参数：QP、关键帧间隔、B 帧、pix_fmt、SAR
- E5 音频频谱：EQ / highpass / lowpass / fade 包络 / 立体声 L-R 分离度
- E6 `run_ffmpeg()` 的 start_time / end_time（当前记录 command / return_code / elapsed_s / stderr_tail）
- E7 `timeline_metrics.json` 未单独产出（量已分散在 `frame_metrics.json` + `audio_metrics.json`）

## 4 复现命令

```powershell
python tests/param_forensics.py fps_matrix     # 25/29.97/30/50/60/120 → 30fps
python tests/param_forensics.py sr_matrix      # 44100Hz 对照
python tests/param_forensics.py determinism    # seed 决定性 / 碰撞
python tests/test_timeline_integrity.py        # 11 项时间轴回归
```

单点复现 B11（不依赖框架）：

```powershell
ffmpeg\bin\ffmpeg.exe -i tests\material\TEST-E.mp4 -vf "fps=30,trim=start=5.688:end=10.412,showinfo" -an -f null -
# → 141 帧，末帧 pts_time=10.3667（=311/30）；ceil 口径会预测 142
```

## 5 V3 discrepancy

| 项 | V3 结论 | 本轮实测 | 处理 |
|---|---|---|---|
| B1 影响面 | 仅 60fps 一例，补帧 5 | 50/60/120 全中，补帧 2/2/4，帧号越界 4/7/17 | 以本轮为准 |
| 段内 `\|a−v\|` | 「29.97 源 6 段全 0.00000」 | 30s 素材 worst 0.033333（1 帧） | 差异源于 zoom 是否触发（B10）；V3 那次未触发 |
| zoom 窗口 | 列为【证据不足】 | 已实测 → B10 已修 | 升级为 PASS |
| 音频 44100 硬编码 | 质疑「可能逃逸」 | 48000/44100 两种素材各 6 处全部按真实采样率收敛 | 判 PASS，兜底分支未触发 |
| 派生 seed | 未验证碰撞 | `child_0 == plan_0 == base` 实测碰撞 → B12 已修 | 新增 B12 |
| `trim` 帧数公式 | 只有 `ceil` 一种口径 | 降帧后必须 `round`（时基 1/fps） | 新增 B11 |

## 6 禁止项自检

- 未为通过测试而修改测试预期：唯一改动的判据是 D1 排除 `ts`（附实证：`ts` 为 `time.strftime`，非参数）与取证脚本的**参照模型**跟随产品改为 `branch_fps` / `coarse_tb`（参照模型必须与被测对象同口径，否则比较无意义）。
- 未改随机参数取值区间；仅改派生 seed 偏移。
- 未改 GUI；未删除任何测试；未删除任何历史报告。
- 未 `commit` / `push` / `reset` / `clean` / `checkout` / `restore`；未处理 `AA` / `A` / `D` / 未合并状态。
- 所有结论均附源码位置 + FFmpeg 证据 + 实测数字；无实测者已标【证据不足】。
