# PARAMETER_CALIBRATION_REPORT_V5

本轮 = 【V4 报告验收 + 剩余证据闭环】。所有结论来自**真实生产路径**（`process_single_pass` / `process_segmented` / `process_clip` / `_merge_reencode`）+ 真实 FFmpeg 渲染 + 对输出文件的像素/采样/ffprobe 测量，不接受"grep 到命令里有这个参数"作为证据。

- 环境：Windows 10，Python 3.11，仓库自带 `ffmpeg/bin/ffmpeg.exe`（`ffprobe.exe`），无 numpy/PIL/scipy
- 代码基线：detached HEAD `a828758`，未做任何 git 提交/回滚
- 取证入口：
  - `python tests/param_forensics.py fps_matrix sr_matrix determinism`
  - `python tests/param_forensics_v5.py visual encode audio av_offset fallback`
  - `python tests/test_timeline_integrity.py`
- 证据落盘：`tests/evidence/<test_id>/`（`command.txt` / `filtergraph.txt` / `parameter_snapshot.json` / `*_metrics.json` / `comparison.json` / `timeline_metrics.json` / `result.json`）

---

## 1. 参数总表（状态口径与 V4 一致，分母 97 条状态分类行）

- `PASS`：**86**
- `BUG`：**0**（V4 遗留 B2–B7 与本轮新发现 B15–B19 全部修复并复测）
- `INEFFECTIVE`：**6**（`mask_drift`/`channel_mix`/`noise` 三项为「配置默认关」，开启后已实测生效；`audio_noise_db`、`_fps`、`crf<24` 钳制、`frame_drop_on` 死参数见第 15 章）
- `PARTIAL`：**0**
- `REGRESSION`：**0**
- `EVIDENCE_INSUFFICIENT`：**5**（明细与原因见第 15 章）

逐类（PASS / BUG / INEFFECTIVE / EI）：

- A 时间轴 25 行：25 / 0 / 0 / 0
- B 几何 21 行：19 / 0 / 1 / 1
- C 颜色 6 行：4 / 0 / 2 / 0
- D 音频 16 行：14 / 0 / 1 / 1
- E 编码 10 行：8 / 0 / 1 / 1
- F 随机 8 行：8 / 0 / 0 / 0
- G 其他 11 行：8 / 0 / 1 / 2

---

## 2. 全部 Bug 清单（V4 遗留 + V5 新发现）

已修复（本轮或前轮，均有修复前/后实测）：

- **B1【P0】** FPS 域错位：A 层降帧在 trim 之前，帧号/帧数仍按源帧率算 → `branch_fps`
- **B2【P1，V5 修复】** `plan.reverse_loop.seg_len` 按 `normalize.fps` 量化却当支路输入时长用 → 改按 `branch_fps` 量化
- **B3【P2，V5 修复】** 整文件/降级路径 `plan_lens_events` 用输出时间轴（`seg_dur/speed`）→ 改按输入时间轴
- **B4【P1】** `av_offset` ±0.02 死区：`|av|<0.02` 不生成任何滤镜，参数被静默丢弃
- **B5【P2，V5 修复】** 变调采样率未知时静默回退 44100 → 改为「采样率未知就不变调」
- **B6【P1】** `asym_crop` 裁剪表达式漏 f-string → FFmpeg `Invalid argument`，真实渲染失败
- **B7【P1】** 缺 `afade=t=out`，淡出参数从未生效
- **B9【P2，V5 修复】** `sc_threshold` 生成但主路径（`spec_encode_args`）不传
- **B10【P1】** `zoompan=fps=` 用输出帧率压缩了该切片 PTS 跨度
- **B11【P1】** 降帧后 trim 帧数量化口径（`round` vs `ceil`）
- **B12【P2】** 派生 seed 撞车 + 第 0 段未独立随机
- **B13【P1】** 源宽高比 == 目标宽高比时 `asym_crop` 无余量 → Δ=0.000000 完全无效
- **B14【P1】** 淡入淡出按段施加，段边界音量掉到 29%（RMS 831 vs 2866）
- **B15【P0，V5 新】** `build_command` 引用未定义 `seg_total` → 降级路径 `NameError` 直接崩
- **B16【P0，V5 新】** `-t` 在 `-i` 之后 = 输出侧限时 → 吞掉 rl/frame_dup/慢放的时间轴膨胀
- **B17【P1，V5 新】** 降级路径第 0 段丢失 `reverse_loop`（`seg_idx==0` 被当成整文件）
- **B18【P2，V5 新】** 降级路径镜头畸变只落在第 0 段
- **B19【P1，V5 新】** 整文件模式 `seg_dur` 未减 `trim_tail` → 音频比视频长 0.300s
- **B20【P3，V5 新】** 日志硬编码 `asetrate=44100*rate`，与实际使用素材真实采样率不符（纯日志）

未修复（设计取舍，需产品决策，见第 15 章）：

- **B8【P2】** `crf = max(24, …)` 钳制：crf=19 与 crf=24 输出字节数完全相同（545460 / 545460）

---

## 3. 修复前 → 修复后（关键数字）

- B15：降级第 1 段即 `NameError` → 3 段 + 1 合并共 4 条命令全部成功
- B16：段 1 预测 3.900s/117 帧 → 实测被截为 3.767s/113 帧；修复后 3.900s/117 帧，逐段与台账相等
- B17：降级第 0 段 108 帧（无 rl 结构）vs 单进程同段 117 帧 → 修复后两路一致
- B18：cmd0 有 `lenscorrection`，cmd1/2 无 → 修复后三段都有畸变事件
- B19：视频 11.400s / 容器 11.700s（|a−v| = 0.300s = trim_tail）→ 11.400s / 11.400s，Δ=0.000s
- B9：`sc_threshold=0` 关键帧 2 个 → `=60` 关键帧 7 个（位置正对白闪 [0,30,90,150,210,270,330]）
- B2：旧口径（按 `normalize.fps=30` 量化）在 25fps 支路上，扫 40 个 seed 有 **35 个**落在非整帧（如 0.13333s × 25 = 3.33 帧）；修复后 25 / 29.97 / 30 / 50 / 60 / 120 六种支路帧率的 `seg_len × branch_fps` **全部为整数**（4 / 4 / 4 / 7 / 9 / 18 帧）
- B3：`speed=1.25`、30s 输入时，修复前畸变窗口最多只能排到 24.0s（= 30/1.25，后 20% 永远没有畸变）；修复后窗口 [0,29.9]、[15,29.9]、[0.115,18.995]，末端 29.9s ≤ 输入窗口 30.0s
- B5：`sample_rate=48000` → `asetrate=60476,aresample=48000,atempo=0.793701`；`sample_rate=0/None` → **不生成变调链**（旧代码会按 44100 变调，对 48k 素材 = 额外 ×(48000/44100) 变速 + 输出被重采样到 44100，实测 +9.0345%）
- 两条路径总量（同一批段快照）：single-pass 349 帧 / 11.6333s，fallback 349 帧 / 11.6333s，台账预测 349 帧 / 11.6333s

---

## 4. 测试矩阵

- 视觉：17 组（BASELINE + 16 参数），素材 V-BASE（480×640 / 30fps / 48kHz / testsrc2 / L=1kHz R=440Hz）
- 编码：7 组渲染 + `sc_threshold` 0/60 两组 → 14 条判据 E1–E14
- 音频：8 组 → 11 条判据 A1–A10
- av_offset：9 个取值（−0.105 … +0.105）
- 降级路径 / 合并审计：13 条判据 F1–F5、M1–M8
- FPS 矩阵：25 / 29.97 / 30 / 50 / 60 / 120 → 30fps，各 7 条判据 C1–C7
- 采样率矩阵：44100（29.97fps、60→30fps 两个素材）
- 确定性：D1–D3

---

## 5. 视觉证据（BASELINE off vs TEST on，像素/几何统计）

判据地板 = **同参数双跑噪声**，实测全部为 `0.000000`（`visual/_BASELINE/result.json`）：`edge_fullres`、`y_mean`、`y_std`、`sat_mean`、`hue_mean`、`corner_mean`、`cx`、`cy`、`col/row_profile_rmse`、`hue_hist_l1` 均为 0 → 任何非零差异都可归因于参数本身。

各用例（TEST − BASELINE，节选关键量，全部 PASS）：

- `brightness=10`（luma_up）：y_mean **+17.34**，四角亮度 +15.66
- `contrast=25`（contrast_up）：y_std **+4.67**
- `saturation=30`（sat_up）：sat_mean **+0.0149**
- `hue=15`（hue_shift）：hue_mean **+8.29**，hue 直方图 L1 0.2516
- `scale=1.1`（structure）：列剖面 RMSE **15.05%**，亮度重心 cx −0.0169，尺寸/SAR 不变（324×432 / 1:1）
- `asym_crop_l/r=0.05/0.02`（centroid）：cx **−0.0116**，列剖面 RMSE 16.57%
- `asym_crop_t/b=0.05/0.02`（B6 触发条件）：cy −0.00127，列剖面 RMSE 12.16% → 命令可执行且真的位移
- `asym_crop` 四边同时：cx −0.0126 / cy −0.00134，列剖面 RMSE 19.80%
- `lens_k1=0.08`（structure）：行剖面 RMSE **21.53%**，四角亮度 **−59.27**，有效画面比 −0.0675
- `lens_k2=0.08`：行剖面 RMSE 17.61%，四角亮度 −51.49
- `rotate_drift_amp=2°`：边缘强度（全分辨率）+0.589，列剖面 RMSE 5.09%
- `rotate_drift_speed=1°/s`：边缘 +0.458，列剖面 RMSE 5.97%
- `zoom_drift_amp=0.08`：边缘 +0.612，重心 cx −0.0056
- `noise=3`（配置开启）：**全分辨率**边缘强度 +0.169（96×128 降采样会把高频噪声平均掉 → 必须用原分辨率测，属测量修正）
- `channel_mix=0.08`（配置开启）：hue_mean **+48.94**，hue 直方图 L1 0.9083
- `mask_drift_amp=3`（配置开启）：全分辨率边缘 +0.322，列剖面 RMSE 1.58%

旋转/缩放/裁剪/畸变一律**不只看均值**，而是用列/行剖面 RMSE、亮度重心、四角亮度、有效画面比、`cropdetect` 这些结构/几何量。

---

## 6. 音频频谱证据（wave + Goertzel，无 numpy）

- A1 采样率：输入 48000 → 输出 **48000**（未显式 `-ar`，跟随输入）
- A2 声道独立：L 主频 **1000Hz**、R 主频 **440Hz**（未混合/未交换）
- A3 变调 +4 半音：期望 1259.9Hz，实测 **1261Hz**（0.086%）；−4 半音：期望 793.7Hz，实测 **798Hz**（0.542%）；时长均 8.000s（不变调时长）
- A4 `atempo` 音高不变：1000Hz → **1000Hz**，尾部 RMS 2892.9 → 1515.6（变短后 apad 补静音）
- A5 highpass：1kHz 能量比 **0.0576**，RMS 2874 → 691
- A6 lowpass：1kHz 能量比 **0.2908**
- A7 EQ +9dB：1kHz 能量比 **7.999**（理论 10^(9/20)²≈7.94）
- A8 淡入：首段 RMS 比 **0.3433**；A9 淡出：尾段 RMS 比 **0.3615**
- A10 段边界无淡入淡出（B14 回归）：4.0–4.2s 窗口 RMS 2880 vs 基线 2871，比值 **1.0033**

---

## 7. 编码证据（代码 → 命令 → 编码器实际输出 → ffprobe）

- E1 codec：h264（`normalize.video_codec=h264_libx264`）
- E2/E10 pix_fmt：yuv420p；切 yuv422p 时输出实测 **yuv422p**
- E3 SAR：1:1（`setsar=1`），尺寸 324×432
- E4 帧率：`r_frame_rate=30/1`
- E5 GOP：`-g 20` → max_gap **20**；`-g 60` → max_gap **60**
- E6 B 帧：`-bf 2` → `has_b_frames=2`，B 帧计数 >0；`-bf 0` → 0 / 0
- E7 CRF 生效：crf24 545460 B → crf34 **306101 B**（0.561×）
- E8 CRF 钳制：crf19 与 crf24 **字节数完全相同** → `INEFFECTIVE`（设计取舍，见第 15 章）
- E9 目标码率 600kbps：实测总码率 773kbps（含音频，容差内）
- E11 音频编码切换：aac → **mp3**
- E12/E14 `sc_threshold`：命令带该参数，且 0 → 2 个关键帧、60 → **7 个**关键帧（位置对齐白闪）
- E13 preset：来自 `encode.cpu_preset`

---

## 8. 时间轴证据（§10 台账）

`tests/evidence/fallback/{SINGLE_PASS,DEGRADED}/timeline_metrics.json` 记录每段的
`source → segment_window → trim → reverse_loop → frame_drop → speed → fps_resample → frame_dup → audio → concat → encode` 全链，含预测/实测/Δ/相对误差。

3 段（V-PULSE 12s，切点 0.4/4.167/7.933/11.7，speed=1.06、frame_dup=2、frame_drop 与 reverse_loop 概率拉满）：

- 段 0：窗口 113 帧 → rl +10 → drop −1 → /1.06 → fps 重采样 115 → dup +2 = **117 帧 / 3.900s**，降级实测 **117 / 3.900**，Δ=0
- 段 1：同上 **117 / 3.900**，实测 117 / 3.900，Δ=0
- 段 2：rl +8 → **115 / 3.833**，实测 115 / 3.833，Δ=0
- 合计预测 349 帧 / 11.6333s；single-pass 实测 349 / 11.6333；fallback 实测 349 / 11.6333

三段都是 **`reverse_loop` 与 `frame_drop` 同段共同触发**（rl +10/+10/+8 帧，drop 各 1 帧）且预测与实测逐段 Δ=0 → 覆盖 V4 遗留的「rl 触发时 frame_drop 帧号平移」证据缺口。

---

## 9. FPS 矩阵

6 个素材（25 / 29.97 / 30 / 50 / 60 / 120 → 30fps）全 PASS，C1–C7 全 PASS：

- C1 `branch_fps`：`n_win` 预测与实测 `[vt]` 帧数**逐段一致**（不一致段列表为空）
- C2 抽帧计划/实删：6/6
- C3 帧号越界：0
- C4 段内最坏 `|a−v|`：0.033333~0.033334（≤ 1 帧）
- C5 concat 补帧：**0**
- C6 `exp_dur` 相对误差：25fps 0.1171%、29.97 0.1175%、30 0.1174%、50/60/120 **0.0024%**
- C7 变调链采样率：6 处 `asetrate→aresample` 全部收敛到输入采样率，不符 0 处

## 10. 采样率矩阵

- TEST-B44（44100Hz / 29.97fps）：PASS，C6 0.1175%
- TEST-E44（44100Hz / 60→30fps）：PASS，C6 0.0023%
- 两者 C7 均为「6 处链路全部收敛到 44100」→ 无硬编码 44100 影响真实渲染

## 11. Fallback 证据（§7）

- F1 真降级：日志出现「⚠ 单进程路径失败，自动降级分段独立编码」，产生 3 个中间段文件 + 1 条 `concat=n=3` 合并命令；single-pass 1 条命令 vs fallback 4 条
- F2 语义一致（13 项逐项比对）：时长 11.6333 vs 11.6333、帧数 349 vs 349、`avg_frame_rate` 30/1、尺寸 324×432、SAR、pix_fmt、视频/音频编码、采样率、声道、音频时长、AV 同步 —— 全部 PASS
- F3 与台账预测一致：两条路径都落在 ±3 帧内（实际 Δ=0）
- F4 `av_offset` 在降级路径的绝对值：期望 +0.050，single-pass **+0.049896**，fallback **+0.049583**（≤1 帧）
- F5 整文件路径（`in_duration=None`）不被截断：预测 11.400s/342 帧，实测 11.400s/342 帧

## 12. `_merge_reencode` 审计（§8，MERGE_REENCODE_AUDIT）

- M1 帧数守恒：各段 117+117+115=349，合并后 **349**（Δ=0，第二次编码不增删帧）
- M2 时长守恒：段和 11.6333s，合并后 11.6333s（Δ 在 3 帧内）
- M3 合并后帧率：`avg_frame_rate=30/1`，与目标一致
- M4 音视频时长：|a−v| ≤ 3 帧
- M5 编码规格：合并输出 h264 / yuv420p / SAR 1:1 / 324×432 / `has_b_frames=1` / `max_keyframe_gap=29` / aac 192k（各段规格同时记录在 `segment_probes.json`）
- M6 PTS：单调递增，步长无异常（`n_irregular_steps=0`），各段 PTS 同时留证
- M7 AV 同步不被二次编码改变：合并前各段中位偏移均值 0.0972s vs 合并后 0.1294s，Δ=0.0322s ≤ 1 帧（0.0333s，判据是相对量；绝对量由 F4 单独测）
- M8 合并命令：含 `concat=n=3:v=1:a=1`，命令原文落盘

## 13. Determinism

- D1 同 seed 两跑：filtergraph **逐字相同**，快照排除 `ts` 后相同（`ts` 是生成时刻墙上时钟，非参数）
- D2 5 个不同 seed：5 条 filtergraph 互不相同
- D3 21 个派生 seed（child / plan / lens / drop / bitrate）：**0 碰撞**

## 14. Regression（§12，每次修复后全量回归）

第一轮（修 B15–B19、B9、B20 之后）：

- `param_forensics.py fps_matrix`：TEST-A/B/C/D/E/F **6/6 PASS**
- `param_forensics.py sr_matrix`：**2/2 PASS**
- `param_forensics.py determinism`：**PASS**（D1/D2/D3）
- `test_timeline_integrity.py`：**11/11 PASS**（含 `run_ffmpeg` 起止时间落盘用例）
- `param_forensics_v5.py visual`：17/17 PASS
- `param_forensics_v5.py encode`：13 PASS + 1 INEFFECTIVE(B8)
- `param_forensics_v5.py audio`：11/11 PASS
- `param_forensics_v5.py av_offset`：9/9 PASS（−0.105 → +0.105，最大误差 0.00033s）
- `param_forensics_v5.py fallback`：13/13 PASS

第二轮（再修 B2/B3/B5 之后，这三项改动了事件规划与音频链，必须重跑）：

- `fps_matrix` 8 个素材（含 sr_matrix 两个）：**全 PASS**，C4 最坏 `|a−v|` 0.033333~0.033334、C5 补帧 0、C6 误差 0.0023%~0.1175%（与第一轮同量级，无劣化）
- `determinism`：D1/D2/D3 **PASS**
- `test_timeline_integrity.py`：**11/11 PASS**
- `param_forensics_v5.py fallback`：**13/13 PASS**
- `param_forensics_v5.py audio`：**11/11 PASS**（B5 只改「采样率未知」分支，已知采样率行为逐字不变）
- 无 `REGRESSION`：V4 与本轮前一次已 PASS 的项目全部复测通过

## 15. 剩余【证据不足】与未修复项（含原因与所需条件）

`EVIDENCE_INSUFFICIENT`（5）：

1. `effective_duration` 的 `max(2.0)` 短素材边界：需要 < 2s 的素材，现有素材最短 8s；属边界分支，未构造
2. `rl_pos_rel`（倒放窗口相对位置分布）：单次渲染只出一个位置，要统计分布需批量 seed 扫描
3. `switches.*` / `fingerprint.*` 全组合：组合爆炸，本轮只覆盖默认组合 + 单开关切换
4. `version_count`（多版本产出）：需 GUI/批处理层端到端，本轮只测单版本渲染路径
5. AAC 随机码率（`seed+17`）的区间统计：单次渲染只能验证「命令里的值 == 派生值」，区间分布需批量

`INEFFECTIVE`（6）：

- `mask_drift` / `channel_mix` / `noise`：`config` 默认关闭 → 默认配置下无效；**开启后已实测生效**（第 5 章）。属产品默认值选择，不是缺陷
- `audio_noise_db`：默认关闭（`anoisesrc/amix` 未接入默认链）
- `_fps`（快照里冗余的帧率字段）：渲染链使用 `normalize.fps`，快照字段不参与
- `crf<24`（B8）：`max(24, …)` 钳制为体积对齐刻意设计（qp17 实测 ≈11Mbps / 272s ≈391MB）。放开与否是产品决策，本轮不擅自改判据也不擅自改代码
- `frame_drop_on`：`randomizer` 生成、渲染链从未引用的死参数（抽帧由 `video.frame_drop.*` + 段计划驱动）。涉及 GUI 展示，未删

## 16. 最终结论

- 本轮新发现并修复 6 个产品 Bug（B15 P0、B16 P0、B17 P1、B18 P2、B19 P1、B20 P3），并把 V4 遗留的 4 项一起收口：B2（rl 量化栅格）、B3（畸变窗口时间轴）、B5（变调采样率静默回退）、B9（`sc_threshold` 未传，INEFFECTIVE → PASS）。其中 **B15 会让整条降级路径直接崩溃、B16 会静默吃掉时间轴膨胀**，两者都只在真正触发降级/整文件路径时才暴露 —— 这正是 V4 阶段「降级路径证据不足」掩盖掉的风险。
- 三条渲染路径（single-pass / 降级分段+合并 / 整文件）现在在**同一批参数**下时长、帧数、编码规格、音频规格、AV 同步全部一致，并与时间轴台账的逐层预测吻合（Δ=0 帧）。
- 判据没有为了通过而放宽：本轮有 4 处**测量修正**（全部记录在案）—— 台账窗口必须用主快照的 trim（不是段快照）、AV 同步测量必须关掉 EQ/滤波/淡入淡出与 `audio_atempo`、事件配对数不等时判证据不足而不是硬凑、B5 判据里的 `asetrate` 期望值算错过一次（60473 vs 正确 60476，改的是测试期望不是产品）。产品判据本身（1 帧容差、±2% 时长、结构地板 3× 噪声）未改。
- 剩余 5 项证据不足全部是「需要额外素材/批量统计/GUI 层」的客观缺口，已逐条写明所需条件；6 项 INEFFECTIVE 中 4 项是产品默认值/设计取舍，1 项是死参数，1 项（B8 CRF 下限钳制）需要产品决策。
- 本轮改动的产品文件：`video_rewash/video/video_processor.py`（B15/B16/B17/B18/B19/B3 + grid_fps 透传）、`video_rewash/video/filters.py`（B9/B5）、`video_rewash/core/randomizer.py`（B2/B20）、`video_rewash/core/segment.py`（grid_fps 透传）。GUI、随机参数区间、已 PASS 行为、历史报告均未改动，未做任何 git 提交。
