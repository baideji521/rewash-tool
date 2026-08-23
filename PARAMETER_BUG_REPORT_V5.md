# 参数 Bug 报告 V5（无人值守取证阶段：B21 – B25）

> 本文件是 `PARAMETER_BUG_REPORT_V4.md` 的续篇，**不覆盖**其中 B1–B20 的历史记录。
> 本阶段所有 Bug 均按「发现 → 取证 → 建 Bug → 修复(≤3 次/轮) → 真实输出验证 → 回归」
> 流程处理，永久证据位于 `tests/evidence/`。
> 队列快照：`tests/evidence/bug_queue.json`（本阶段结束时 OPEN=0，5 条全部 FIXED）。

## 汇总

- **B21** P1 TEST_INFRA_BUG — `dist` 阶段导入错误（测试基础设施，未改产品）→ FIXED
- **B22** P1 — combo `c07_zoom_rotate_speed@TEST-C` 时间轴不符 → FIXED（与 B24 同根因）
- **B23** P1 — combo `c07_zoom_rotate_speed@TEST-E` 时间轴不符 → FIXED（与 B24 同根因）
- **B24** P1 — 推镜窗口使段帧数偏离时间轴真值 → FIXED（2 次尝试）
- **B25** P2 — 微旋窗口坐标在变速后被二次 `/speed` → FIXED（1 次尝试）

---

## B21 `dist` 阶段导入错误（TEST_INFRA_BUG）

- 现象：`cannot import name 'frame_drop_plan' from 'video_rewash.video.filters'`
- 分类：**B 测量方法**（测试脚本），依 §19 先修测试、不改产品
- 修复：导入改为 `video_rewash.core._graph`
- 验证：`dist` 3/3 PASS
- 证据：`tests/evidence/bugs/B21/`

---

## B24 推镜窗口使段帧数偏离时间轴真值（P1，2 次尝试）

- 首次发现：`combo/c07_zoom_rotate_speed`（TEST-C Δ帧 +12 / Δ时长 +0.400s；
  TEST-E Δ帧 +11 / Δ时长 +0.367s）。`c06_full_stack` 未暴露的原因是
  `neutral_params()` 把 `zoom_drift_amp` 置 0，而 `zoom_on` 要求 > 0.002。
- 最小复现 `zoomacc` z1–z6（TEST-C 30fps，3 段）：

  - 推镜关 + speed 1.00 → Δ帧 0
  - 推镜开 + speed 1.00 → Δ帧 0（大幅度亦 0）
  - 推镜关 + speed 1.03 → Δ帧 0
  - 推镜开 + speed 1.03 → Δ帧 +12（每段 +4）
  - 推镜开 + speed 0.92 → Δ帧 −7（每段 −2.33）

  → 误差只在「推镜开 AND speed≠1」出现、与幅度无关 → 指向 fps 栅格 / 切段边界。

- 独立 ffmpeg 变体实验（同段 0–10s，speed 1.03，真值 291 帧）：

  - 无推镜 → 291
  - 变速后切段 + `zoompan(fps=30)` → 295（原始 Bug）
  - 变速后切段 + `zoompan(fps=30.9)` → 292（仅修栅格，残 +1）
  - 变速后仅 `split/trim/concat`（不过 zoompan）→ 292（证明 +1 与 zoompan 无关）
  - 变速后 `settb=1/90000` → 292（时基量化假设被否）
  - **变速前**切段 + `zoompan(fps=30)` → 291（Δ=0）

- 根因：推镜窗口原位于 `setpts=1/speed*PTS` **之后**。
  ① `zoompan` 的 `fps=` 是输出时间戳栅格，此处流真实帧率是 `branch_fps×speed`，
  传 `branch_fps` 使窗口切片被拉伸 speed 倍（幅度 = 窗口时长 ×|1−1/speed|）；
  ② 非整数栅格下 `trim` 切点落在帧间、`concat` 按各切片「末帧 PTS + 帧时长」
  累加，每段再多 1 帧。
- 尝试 1（失败）：只把栅格改成 `fps_in*speed` → 残 +1 帧/段（两方向同号）。
- 尝试 2（通过）：把推镜移到变速**之前**（栅格 = 源帧率、切点帧对齐），
  窗口坐标由输出空间折回输入空间改为 **×speed**。链路顺序变为
  `pre_chain → 抽帧 → 推镜窗口 → 变速 → 微旋 → 标准化尾 → 重复帧`。
- 涉及文件：`video_rewash/core/_graph.py`（`build_segment_branch`）、
  `video_rewash/video/video_processor.py`（注释同步）。
- 验证：`zoomacc` 6/6 PASS（z4 残差 1 帧/整片 = 0.033s，属分段取整）；
  `combo` 16/16 PASS。
- 证据：`tests/evidence/bugs/B24/{discovery,attempt_01,attempt_02,final}`

---

## B25 微旋窗口坐标在变速后被二次 `/speed`（P2，1 次尝试）

- 发现方式：修 B24 时确认 plan 坐标空间语义，新增静态一致性阶段
  `wincoord`（plan 数值 vs filtergraph 数值逐段对照）后立即暴露。
- 现象（speed 1.05，TEST-C 3 段）：
  - 段0 图内 `between(t,0.137,8.827)` vs plan `0.144~9.268`
  - 段1 `between(t,0.025,8.714)` vs plan `0.026~9.150`
  - 段2 `between(t,0.298,8.988)` vs plan `0.313~9.437`
  → 图内窗口 = plan / speed，微旋提前约 0.44s 结束。
- 根因：`generate_segment_plan` 的 `seg_len` 传的是 `seg_len/speed`，
  即 plan 坐标定义在**变速后**输出时间轴（docstring 亦写明）。微旋
  `rotate` 的 timeline `enable` 在变速之后求值，坐标应**原样使用**，
  代码却再除一次 speed。
- 修复：`core/_graph.py` 分支链与 `video/video_processor.py` 简单链两处
  去掉 `/speed`。
- 验证：`wincoord` W1/W2/W3 全 PASS（W3 三段与 plan 完全一致）。
- 证据：`tests/evidence/bugs/B25/{discovery,attempt_01,final}`

---

## 本阶段新增的常驻取证用例

- `zoomacc`（`tests/param_forensics_v6.py`）：推镜 × 变速 6 组，逐段对台账。
- `wincoord`（同上）：窗口坐标空间静态一致性
  - W1 推镜切段必须在变速之前
  - W2 推镜窗口坐标 = plan × speed
  - W3 微旋窗口坐标 = plan 原值
- 两者已并入 `tests/unattended.py` 的回归套件清单。

## 源码复扫（§15）结果

- 硬编码 44100 / 采样率回退：已无（仅剩解释性注释），变调在采样率未知时不生效（B5）。
- 重复 `setpts=*PTS`（变速）：每条路径仅 1 处（`_graph.py:257`、
  `video_processor.py:226`），无 `setpts=N/{fps}/TB` 覆盖变速的写法。
- 参数生成未传递 / 传递未使用：`grid_fps`、`seg_total`、`sample_rate`、
  `sc_threshold` 均已在 V5 阶段接通并有取证。
