# AUDIT_BASELINE

本文件是 V8 轮「全量参数 × 组合 × 路径 × 边界」审计的起点快照。
采集时间：2026-08-24 07:1x（本地工作目录，唯一真值源；未使用任何远端仓库）。
采集脚本：只读统计（`git`、`tests/evidence/index.json`、`tests/evidence/bug_queue.json`、
`tests/unattended.py`、`tests/material/`）。

## 1. 代码状态

- Git HEAD：`a828758 Add files via upload`
- 工作区：**有未提交改动**（本审计的全部修复都在工作区，未提交）
  - 产品文件已修改（9 个）：`video_rewash/core/{_graph,ffmpeg_runner,processor,quality_check,randomizer,segment}.py`、
    `video_rewash/video/{filters,video_processor}.py`、`video_rewash/audio/audio_processor.py`
  - `config.json` 已修改 —— **注意：该文件在本次审计期间（今天 06:50:14）被外部改动**
    （`last_used_preset` → `aggressive`、`video.zoom_drift.probability` 0.8→0.5、
    `video.zoom_drift.duration.min` 3.0→0.5、新增 `preset_tweaks` 块）。
    产品侧只有 GUI 的 `STORE.save()` 会写这个文件，测试不写 → 判定为**人工/GUI 改动**，
    不是测试污染。但它直接改变了取证基线（见 §7 的 c07 案例），必须记录。
  - 未跟踪：`tests/`（取证脚本与证据库）、`.comate/`（临时输出）、V3~V7 报告
  - 遗留冲突标记：`PARAMETER_CALIBRATION_REPORT_V2.md` 处于 `AA`（未合并）状态，
    早于本次审计，未处理（不属于产品代码）
- 自带工具链：`ffmpeg/bin/ffmpeg.exe`、`ffprobe.exe`（所有取证均真实调用）

## 2. 报告版本（历史全部保留，不覆盖）

- 参数校准：`PARAMETER_CALIBRATION_REPORT.md`、`_V2`…`_V7`（最高 **V7**）
- 缺陷报告：`PARAMETER_BUG_REPORT_V4/V5/V6.md`（最高 **V6**）
- 矩阵/总表：`PARAMETER_AUDIT_MATRIX.md`、`PARAMETER_MASTER_AUDIT.md`（自动生成）
- 证据索引：`tests/evidence/EVIDENCE_INDEX.md`、`tests/evidence/BUG_QUEUE.md`
- **尚不存在**：`PATH_COVERAGE_MATRIX.md`、`COMBINATION_COVERAGE.md`（本轮需建立）

## 3. Bug 队列（`tests/evidence/bug_queue.json`）

- 总数 **27**；状态：**FIXED 21 / OPEN 6**；FIXING 0、VERIFYING 0、
  REGRESSION 0、TEMPORARY_BLOCKED 0、PERMANENT_BLOCKED 0、EVIDENCE_INSUFFICIENT 0
- 严重度分布：P0 2（B37、B39）、P1 18、P2 6、P3 1
- 自动修复尝试：总 21 次，失败 2 次（B24 attempt_01、B39 attempt_01），
  达到 3 次上限的 Bug：**0**
- OPEN 6 条（全部来自最近两轮完整回归的套件级失败，尚未归因）：
  - B50 P2 套件失败 `fps_sr_determinism`（BUG_FOUND）—— 已定位为回归驱动
    误把脚本自身收尾行「完成。判定为 BUG/REGRESSION 的检查项=0」当命中，
    驱动已修（噪声前缀 `完成。`），复跑后该套件 PASS，**待正式封存**
  - B51 P2 `v5_visual`、B52 P2 `v5_encode`、B53 P2 `v5_audio` —— 同上，复跑后 PASS，待封存
  - B54 P1 `v5_fallback`（ERROR，rc=2）—— **真实失败**，两轮均复现
  - B55 P1 `v6_combo`（ERROR，rc=2）—— **真实失败**，两轮均复现

## 4. 测试与素材

- 完整回归套件：**20**（`tests/unattended.py`，`full` 模式）
  - fps_sr_determinism、timeline_unit、v5_visual/encode/audio/av_offset/fallback、
    v6_dist/order/dead/boundary/switches/combo/zoomacc/wincoord、
    v7_paths3/sweep/combo19/material/determ
- 取证脚本：`param_forensics.py`（V3）、`_v5`、`_v6`、`_v7`、`evidence_db.py`、
  `evidence_report.py`、`unattended.py`、`test_timeline_integrity.py`
- 素材：**33** 个（含 FPS×采样率 12 个 `V7-Fxx-Sxx`、无音频、双音轨、
  音频长/短、NTSC 非整数帧率、黑边、1.6s/3s 短素材、脉冲素材）

## 5. 证据库（`tests/evidence/index.json`）

- 计数器：`ev_counter=642`、`run_counter=75`、`reg_counter=6`
- 证据记录 **639** 条：PASS 594 / BUG 28 / INEFFECTIVE 12 / EVIDENCE_INSUFFICIENT 5
- 其中带**真实输出 SHA** 576 条、带**真实命令 SHA** 575 条
- 分组：`parameters` 266、`matrix/combinations` 255、`matrix/boundary` 64、
  `matrix/fps` 36、`paths/all` 11、`paths/whole_file` 4、`misc` 3
- 去重用例 **131** 个、去重参数 **62** 个
- 回归轮次：R-000001 ~ R-000006；**最近两轮（R-000005 / R-000006）均为 BUG_FOUND**
  （各 20 套件：18 PASS + 2 ERROR），因此**当前不满足"连续两轮干净"**

## 6. 参数状态（沿用 V5~V7 计数 + V7 新增判定）

- 参数条目 **97**（PASS 86 / BUG 0 / INEFFECTIVE 6 / EVIDENCE_INSUFFICIENT 5）
- V7 新增的 44 项逐参数隔离矩阵：最近一轮 **44/44 PASS**（RUN-000043 / v7_sweep）
- **INEFFECTIVE（设计如此 / 出厂关闭）**：
  `noise`（`video.noise.enable=false`）、`channel_mix`（`video.channel_mix.enable=false`）、
  `mask_drift_*`（`video.mask_drift.enable=false` → amp 恒 0）、
  `audio_noise_db`（仅 aggressive 档生成）、`_fps` 冗余字段、`frame_drop_on` 死参数、
  `crf<24` 钳制（B8，设计取舍）
- **PARTIAL（部分路径/编码器才生效）**：
  `sc_threshold`（仅 libx264/libx265；实测 NVENC 参数串无该项）、
  `asym_crop_*`（仅标准化开启时生效）、
  快照级 `rl_pos_rel/rl_seg_len/rl_repeats/rl_mode`（仅整文件路径读取）、
  B39 残留（分段路径合并遍比单进程多 1 帧，在 ±2 帧容差内）
- **EVIDENCE_INSUFFICIENT（5）**：`effective_duration` 短素材边界（B43 已修，需重新取证）、
  `rl_pos_rel` 分布、`switches.*`/`fingerprint.*` 全组合、`version_count` 多版本、
  AAC 随机码率区间分布

## 7. 路径覆盖现状（关键缺口）

证据里带 `production_path` 标签的分布：`single_pass` 569、`segmented` 12、
`whole_file` 12、`all` 11。

- **`fallback`（process_clip×N）与 `merge_reencode` 没有独立标签** ——
  它们只在 `v5_fallback` 套件里被验证，没有进入参数级矩阵 → 本轮必须建
  `PATH_COVERAGE_MATRIX.md` 并补齐每个关键参数在 5 条路径上的判定。
- 已知路径级问题（本轮待归因）：
  - `v5_fallback` F2/F3/M1：single_pass 341 帧 / 11.367s vs 降级 335 帧 / 11.167s
  - `v6_combo` c07_zoom_rotate_speed@TEST-C：预测 873 帧 / 实测 876 帧（Δ=3 > 容差 2）。
    对比 RUN-000034（PASS，Δ=1）与 RUN-000068（BUG，Δ=3）的证据：
    **预测值完全相同、命令只差推镜窗口的随机长度**（旧 3.448s/6.117s → 新 1.054s/5.056s），
    唯一的配置差异是 `video.zoom_drift.duration.min` 3.0 → 0.5 →
    结论：**短推镜窗口（≈1.05s）暴露了新的帧数漂移**，不是随机噪声。

## 8. 本轮（V8）已确定的起始待办

1. 归因并修复 `v5_fallback` 语义一致性（P1，最多 3 次尝试）
2. 归因并修复短推镜窗口帧数漂移（P1，新 Bug）
3. 正式封存 B50~B53（TEST_INFRA，驱动噪声过滤已修，复跑已 PASS）
4. 建立 `PATH_COVERAGE_MATRIX.md`（5 条路径 × 关键参数）与 `COMBINATION_COVERAGE.md`
5. 把 `fallback` / `merge_reencode` 作为独立 `production_path` 标签纳入取证
6. 重新取证 `EVIDENCE_INSUFFICIENT` 的 5 项
7. 全部修完后跑**连续两轮全新**完整回归（新 RUN ID / 新证据目录），
   要求新增 BUG=0、REGRESSION=0、TEST_INFRA_BUG=0
8. 产出 `PARAMETER_CALIBRATION_REPORT_V8.md` + `PARAMETER_BUG_REPORT_V7.md`（不覆盖历史）

## 9. 停止条件（未达成，逐条记录）

- OPEN=6 ≠ 0 → 不满足
- 连续两轮干净回归 → 不满足（R-000005 / R-000006 均 BUG_FOUND）
- P0/P1 全部有真实证据 → 部分满足（B54/B55 尚未归因）
- EVIDENCE_INSUFFICIENT / INEFFECTIVE / PARTIAL 已列出 → 满足（见 §6）
- 因此**当前不得宣布 AUDIT COMPLETE**。
