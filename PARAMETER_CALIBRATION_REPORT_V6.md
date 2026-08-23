# 参数标定报告 V6（无人值守持续取证 + 自动修复 + Bug 队列闭环）

> 历史版本：`PARAMETER_CALIBRATION_REPORT.md`（V1）、`_V2`、`_V3`、`_V4`、`_V5`
> 本文件**不覆盖**任何历史结论，只增量记录 V6 阶段（无人值守轮）的新增证据、
> 新发现 Bug、修复与回归。Bug 明细见 `PARAMETER_BUG_REPORT_V5.md`。
> 参数级权威计数见 `PARAMETER_AUDIT_MATRIX.md` 的最新"状态刷新"节。
> 机器可读证据：`tests/evidence/`（`index.json` / `bug_queue.json` /
> `runs/` / `bugs/` / `regression/`），可检索索引 `tests/evidence/EVIDENCE_INDEX.md`。

## 1 目标与停止条件

目标：所有去重参数有证据、所有生产路径有证据、所有 Bug 修复且经**真实输出**
验证、完整回归通过、证据链永久可追溯。

停止条件（本阶段已满足）：

- Bug Queue OPEN/FIXING/TEMPORARY_BLOCKED/VERIFYING/REGRESSION = **0**
- 连续两轮完整回归（`R-000003` / RUN-000019 与 `R-000004` / RUN-000028）
  各 15 套件全 PASS，新增 BUG = 0，REGRESSION = 0

## 2 本阶段新建的基础设施

- `tests/evidence_db.py`：证据数据库（EV/RUN/R 单调计数器、每条证据固定编号
  文件 01…20、源码/配置/输入/输出/command/filtergraph 全部 SHA256、
  8 个产品文件的 worktree 指纹）。
- `tests/param_forensics_v6.py`：`combo` / `boundary` / `dead` / `order` /
  `dist` / `switches` / **`zoomacc`（新）** / **`wincoord`（新）**。
- `tests/unattended.py`：一轮 = 15 套件顺序执行 → RUN 账本 + 回归记录 →
  刷新 `EVIDENCE_INDEX.md` / `BUG_QUEUE.md` / `PARAMETER_MASTER_AUDIT.md`。
- `tests/evidence_report.py`：索引与总档生成。

## 3 本阶段发现并修复的产品 Bug

- **B24（P1，2 次尝试）** 推镜窗口使段帧数偏离时间轴真值。
  根因两层：`zoompan` 的 `fps=` 栅格用 `branch_fps`，而窗口位于变速之后、
  流真实帧率是 `branch_fps×speed`（+4 帧/段）；非整数栅格下 `trim` 切点不
  帧对齐、`concat` 按末帧时长累加再多 1 帧。
  修复：把推镜窗口移到**变速之前**（栅格 = 源帧率、切点帧对齐），窗口坐标
  由输出空间折回输入空间改为 `×speed`。链路顺序：
  `pre_chain → 抽帧 → 推镜 → 变速 → 微旋 → 标准化尾 → 重复帧`。
- **B22 / B23（P1）** `combo/c07_zoom_rotate_speed@TEST-C/@TEST-E`，与 B24 同根因。
- **B25（P2，1 次尝试）** 微旋窗口坐标在变速后被二次 `/speed`
  （实测 speed=1.05 时窗口提前 0.44s 结束）。修复：两处 rotate `enable`
  直接使用 plan 坐标。

## 4 测试基础设施 Bug（§19：先修测试，不据此改产品）

- **B21（P1）** `dist` 阶段导入路径错误 → 改测试导入，产品未动。
- **B34（P1）** `tests/unattended.py` 的 token 扫描命中取证脚本自身打印的
  `Bug 队列: {…"REGRESSION": 0…}` 摘要行 → 第一轮（RUN-000010）8 个 v6 套件
  全被误判 `BUG_FOUND` 并凭空开出 8 条 `suite/*` 记录。
  修复：`_is_noise()` 过滤驱动自身输出行；8 条误报移出队列并归档到
  `tests/evidence/bugs/B34/discovery/false_positives.json`（证据保留，不计入统计）。

## 5 关键测量方法（V6 新增）

- `zoomacc`（z1–z6）：推镜 on/off × speed 1.00/1.03/0.92 × 幅度，
  逐段与 `segment_video_metrics` 台账对照 —— 用于区分"切段/concat 的账"
  与"fps 栅格的账"。
- `wincoord`：**plan 数值 vs filtergraph 数值**静态一致性
  - W1 推镜切段必须在变速之前
  - W2 推镜窗口坐标 = plan × speed
  - W3 微旋窗口坐标 = plan 原值
- 独立 ffmpeg 变体实验（6 变体，见 `bugs/B24/discovery/minimal_repro.py`）：
  在最小图上把"栅格错" / "切段固有" / "时基量化" / "顺序错"逐一隔离，
  是本阶段定位 B24 的决定性证据。

## 6 生产路径覆盖

- `single_pass`（首选）：162 条证据
- `segmented`（降级 `process_clip`×N + `_merge_reencode`）：4 条
- `whole_file`（`in_duration=None`）：4 条
- 三条路径在同一份逐段快照下与台账 Δ=0 帧（V5 §7/§8/§10 已闭环，V6 回归复验）

## 7 源码复扫（§15）

- 硬编码 44100 / 采样率静默回退：已无（仅剩解释性注释）；采样率未知时不变调（B5）。
- 重复 `setpts=*PTS`（变速）：每条路径仅 1 处
  （`core/_graph.py:257`、`video/video_processor.py:226`），
  无 `setpts=N/{fps}/TB` 覆盖变速的写法（`order` 阶段 I2 常驻断言）。
- 参数生成未传递 / 传递未使用：`grid_fps`、`seg_total`、`sample_rate`、
  `sc_threshold` 均已接通且有证据。
- 日志与实际不一致：V5 已修 `asetrate` 日志（B20），本轮复扫无新增。

## 8 遗留（非 Bug，需产品决策或上层改造）

- `version_count`：需 GUI/批处理层参与，单进程取证无法覆盖（EVIDENCE_INSUFFICIENT）。
- `switches.*` / `fingerprint.*` 全组合矩阵：已覆盖 16 组开关证据，
  完整笛卡尔积仍未穷举。
- `frame_drop_on`：4 条证据均为 INEFFECTIVE（开关不改变输出字节），
  按设计取舍待产品确认。
- CRF 下限钳制（B8，≥24）：INEFFECTIVE by design，需产品决策。

---

## §22 最终统计块

**参数覆盖（含 V5 累计基线）**

- 已取证参数条目（V5 结论沿用）：97
  - PASS 86 / BUG 0（V5 阶段全部修复）/ INEFFECTIVE 6 / EVIDENCE_INSUFFICIENT 5
- V6 阶段带证据的参数维度（`index.json` 的 `parameter` 去重）：15
  - `zoom_drift_amp` 36、`combination` 80、`switches` 16、`speed` 8、
    `window_coords` 5、`filter_order` 5、`rl_pos_rel` 5、`audio_bitrate` 5、
    `_fps` 4、`audio_noise_db` 4、`effective_duration` 4、`frame_drop` 4、
    `frame_dup` 4、`trim` 4、`frame_drop_on` 4
- 未取证 / 无法取证：上述 §8 四项

**证据统计**

- Evidence 总数：189（EV-000001 … EV-000189）
- 用例判定：PASS 177 / BUG 8（均已修复复验）/ INEFFECTIVE 4
- 分组：`matrix/combinations` 142、`parameters` 26、`matrix/boundary` 20、`misc` 1
- 生产路径：`single_pass` 162、`segmented` 4、`whole_file` 4、未标注 19
- 去重测试用例：40
- RUN 账本：36（RUN-000001 … RUN-000036）

**Bug 统计**

- 总数 6：P1 5 / P2 1
- 状态：FIXED 6，OPEN 0、FIXING 0、TEMPORARY_BLOCKED 0、VERIFYING 0、
  REGRESSION 0、EVIDENCE_INSUFFICIENT 0、PERMANENT_BLOCKED 0
- 其中产品 Bug 4（B22/B23/B24/B25），测试基础设施 Bug 2（B21/B34）
- 误报（不计入）：8 条 `suite/*`，归档于 `bugs/B34/discovery/false_positives.json`

**自动修复统计**

- 修复尝试总数：5（B24 两次、B25 一次、B21 一次、B34 一次）
- 成功 4 / 失败 1（B24 attempt 01 只改 fps 栅格，残 +1 帧/段）
- 成功率 80%；无 Bug 达到 3 次失败上限，TEMPORARY_BLOCKED = 0、
  PERMANENT_BLOCKED = 0、AUTOFIX_BLOCKED = 0

**回归统计**

- 回归记录 4 条：
  - `R-000001` 定向回归（zoomacc 6 + combo 16 + wincoord 3）PASS
  - `R-000002` 完整回归 15 套件 BUG_FOUND（全部为 B34 误报）
  - `R-000003` 完整回归 15 套件 PASS ← 第 1 轮干净
  - `R-000004` 完整回归 15 套件 PASS ← 第 2 轮干净
- 连续干净轮次：**2**（新增 BUG 0、REGRESSION 0）

**结论**：Bug 队列清零 + 连续两轮完整回归干净，V6 阶段停止条件达成。
