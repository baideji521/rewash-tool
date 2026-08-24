# PARAMETER_CALIBRATION_REPORT_V8

**V8 全量复核审计（参数 × 组合 × 路径 × 边界）**

口径声明（与前几版最重要的区别）：本轮**不把历史报告当结论**。
凡历史记为 PASS 的项，一律回到当前源码状态重新确认、并重新抽样实测；
凡"没测到"的项，宁可记 `EVIDENCE_INSUFFICIENT` / `NOT_APPLICABLE`，
也不写 PASS。本轮的产出不是"绿灯"，而是**证据**。

- 起点基线：`AUDIT_BASELINE.md`（本轮第一步，只读、不改产品代码）
- 增量对象：`PARAMETER_CALIBRATION_REPORT_V7.md`（不改写）
- Bug 增量：`PARAMETER_BUG_REPORT_V7.md`（B45~B58）
- 路径/组合覆盖：`PATH_COVERAGE_MATRIX.md`、`COMBINATION_COVERAGE.md`
- 参数总档：`PARAMETER_MASTER_AUDIT.md`（本轮扩为「静态属性 + 实测统计」）
- 机器可读真值：`tests/evidence/index.json`、`tests/evidence/bug_queue.json`

---

## 1. 本轮结论摘要

1. **又发现两个真实产品缺陷，且都被"看起来通过"的用例掩盖**：
   - **B55**（P1）推镜窗口切段重复 1 帧 —— `trim` 的 `duration=` 从"第一个
     通过的帧"起算，切点非帧对齐时 B 片多含 1 帧、C 片再取一次。
   - **B56**（P1）黑边裁剪**恒定不生效** —— 4 个捕获组解包给 5 个名字，
     `ValueError` 被 bare except 吞掉，`detect_black_crop` 对任何素材返回
     `None`；而出厂配置里该功能是**开**的。
2. 四条 TEST_INFRA_BUG（B50~B53）确认为驱动把脚本自述汇总行当成 BUG 关键字，
   已修并两轮复验不再复现；B54 确认为 B39 attempt_02 引入的回归，attempt_03 已修。
3. 证据库补上了 `fallback` 与 `merge_reencode` 两条**此前零证据**的生产路径标签。
4. 判定词表补 `NOT_APPLICABLE`，把"命题无被测对象"与"没测到"分开，
   避免用 EI 长期挂账、也避免把它偷换成 PASS。
5. 回归自身也暴露了测试债：**B57/B58**（TEST_INFRA_BUG）—— `v6_wincoord` 的
   判据仍在匹配 B55 已删除的 `trim duration=` 写法，匹配失败即静默 `None`，
   恒判 BUG。先复核产品（切点与 `plan×speed` Δ=0.000）确认无缺陷，
   再把判据从 3 项加强到 5 项，并用变异注入自证其区分力。
6. 全部 30 条 Bug 现为 FIXED（0 OPEN / 0 TEMPORARY_BLOCKED / 0 REGRESSION），
   但**仍有 INEFFECTIVE by design 项**（配置门/编码器分支决定），
   以及 `PATH_COVERAGE_MATRIX.md` 中标 `?` 的未覆盖格子 —— 见 §12。

## 2. 参数审计（增量）

参数条目 63 条（证据库口径），逐参数静态属性 + 实测统计见
`PARAMETER_MASTER_AUDIT.md`。本轮**判据升级**的参数：

- `zoom_drift_amp` / `zoom_drift_period` / `zoom_drift_dir`：
  判据由"视觉生效"升级为
  「窗口开启后帧数 == `zoom.off` 基线 == 台账预测」**且**
  「`sum(切段三片帧数) == 输入帧数`」。
  实测 7 组非帧对齐窗口 × speed{1.00,1.03,0.97} = 24 例全过（修复前 9/15 失败）。
- `crop_rect` / `video.black_crop`：判据由"渲染成功"升级为
  「产品 rect 逐字出现在真实命令里」**且**「与关闭该开关的对照渲染 SHA 不同」。
- `_fps`：改判 `NOT_APPLICABLE`（快照无该字段）。
- `frame_drop_on`：保持 `INEFFECTIVE`（生成但渲染链零引用）。
- `window_coords`（推镜/微旋窗口坐标空间）：判据由 3 项增至 5 项 ——
  除「推镜在变速之前」「推镜坐标 ×speed」「微旋坐标原样」外，新增
  「B 片必须写绝对 `end=`」与「相邻切片切点字符串逐字符共享」，
  并要求解析不出写法时**显式报错**而非静默 null（B57）。

## 3. 时间轴真值与 B55 的量化

单段 10s / TEST-C 30fps / speed=1.03，台账 `segment_video_metrics` 预测 291 帧：

- 修复前：`start=5.056` 固定、`dur ∈ {0.5,0.8,1.054,1.5,3.0,3.448,4.0}` → 全部 292（+1）；
  `dur=2.0` → 291
- 修复前：`dur=1.054` 固定、`start ∈ {2.017,5.056}` → 292（+1）；
  `start ∈ {0.0,0.5,1.0,7.5}` → 291
- 分片守恒（真值 `n(输入)=300`）：`a=5.056 d=1.054` → A=157 **B=33** C=111
  = **301**；`a=2.017 d=1.054` → 63/**33**/205 = **301**
- 修复后：15/15 窗口 == 291；10/10 组分片和 == 300

纯 FFmpeg 语义对照（无产品代码，a=5.208 d=1.086 b=6.294）：

- `trim=start=5.208:duration=1.086` → **33** 帧（末帧 pts 6.300）
- `trim=start=5.208:end=6.294` → **32** 帧
- `trim=start=6.294` → 111 帧
- 结论：`duration=` 版 33+111=144 vs `end=` 版 32+111=143，差的那 1 帧
  同时属于 B、C 两片。

## 4. 生产路径

五条路径的定义、参数 × 路径覆盖、路径专属能力见 `PATH_COVERAGE_MATRIX.md`。
本轮新增的路径级证据（`RUN-000080`）：

- `paths3/single_vs_segmented_vs_whole` **PASS**：A 单进程 330 帧 / B 分段 331 /
  C 整文件 325，Δ帧=1、Δ时长=0.033333（容差 2 帧 / 3 帧时长）
- `paths3/fallback_clips` **PASS**（新标签 `fallback`）：中间段帧数
  `[111, 109, 110]`，和=330 == 台账 330
- `paths3/merge_reencode` **PASS**（新标签 `merge_reencode`）：合并 331 vs 段和 330
  → +1 帧，PTS 单调；该 +1 就是 B39 记录的 PARTIAL 残留
- `segacc/per_segment_frames` **PASS**：支路 `[111,109,110]` / 段 `[111,110,110]` /
  合并 331 / 单进程 330 / 台账 330

## 5. 组合

8 组（× 2 素材）+ 19 组的清单、因子分类与覆盖缺口见 `COMBINATION_COVERAGE.md`。
关键结果（`RUN-000079`，B55 修复后新执行）：**16/16 PASS，Δ帧 全 0**，
其中 `c07_zoom_rotate_speed@TEST-C` 由 `BUG Δ帧=3` → `PASS Δ帧=0`，
`@TEST-E` 由 `Δ帧=-1` → `0`。

## 6. 边界与开关

- `switches`（`RUN-000082`）4/4 PASS，`s03_black_crop_on` 由
  `EVIDENCE_INSUFFICIENT` → `PASS`，证据含
  `crop_in_command=true` / `differs_from_no_crop_control=true`
- `dead`（`RUN-000083`）：`frame_drop_on` INEFFECTIVE、`audio_noise_db` PASS、
  `snapshot_fps_field` **NOT_APPLICABLE**
- 短素材 / trim 吃满 / 极短段 dup 抑制 / speed 上下界 / 音频长于视频等边界
  沿用 V7 的 17 组素材，本轮回归中全部复跑（见 §11）

## 7. 修复过程（逐次尝试，含失败与被否假设）

- **B55**：1/3 次尝试成功。
  - 被否的直觉假设 ①「短窗口才有问题」→ 扫窗口长度 0.5~4.0s 与起点 0~7.5s，
    证明与长短无关、只与帧对齐有关；
  - 被否的假设 ②「zoompan 造帧」→ 去掉 zoompan 只留 split/trim/concat，
    分片和仍为 301；
  - 被否的假设 ③「concat 的时长累加误差」→ zoompan 前后计数一致、
    `sum(分片) > n(输入)` 说明是**重复帧**而非时长漂移；
  - 成功的 attempt_01：三片改绝对半开区间 `end=`，相邻片共用切点字符串。
- **B56**：1/3 次尝试成功。产品修解包元数；同批把 s03 用例从"空判"改成
  两条可观测判据（否则修完也看不出差别）。
- **B50~B53**：驱动噪声过滤修正后，两轮全量不再复现。
- **B54**：先做 A/B（`if False and ...`）确认是 attempt_02 引入，再回退并换
  帧精确 `-t`。
- **B57 / B58**（TEST_INFRA_BUG，1/3 次尝试）：全量回归第 1 轮暴露
  `[v6_wincoord] ERROR rc=2`。先做**产品侧独立复核**（抓真实
  `-filter_complex`：三段切点与 `plan×speed` Δ=0.000）确认产品无缺陷，
  再判定为判据过期（正则仍匹配 B55 已删除的 `duration=` 写法，匹配失败静默
  `None` → 恒 BUG 且无法区分"写法变了"与"坐标错了"）。
  修复方向是**加强**判据：新增 W4（必须绝对 `end=` 写法）、
  W5（相邻切点字符串共享，B55 不变量），阈值未动。
  修完先用变异注入证明判据能各自抓错（M1 坐标错→W2、M2 写法退回→W4、
  M3 切点错位→W5，基线全 PASS），之后才跑产品回归（RUN-000098 PASS）。

## 8. 反自证自洽（测试基础设施审计）

本轮新增/修正的"防自骗"机制：

- 驱动的 BUG 关键字扫描不得命中脚本自己的汇总行（B50~B53）
- `switches/s03` 不得在"标准化开启→输出恒为目标规格"的情况下靠尺寸判 PASS，
  必须有命令内 rect + 对照渲染 SHA 差异（B56）
- `paths3` 不再只留一条 `path="all"` 的合并证据，`fallback` 与
  `merge_reencode` 各自出证据、各自判定（未测则记 `EVIDENCE_INSUFFICIENT`）
- 期望值不得抄产品公式：`effective_duration` 一律调用产品函数；
  推镜守恒用例的真值取产品台账 `segment_video_metrics(...)["frames_out"]`
  **并**与 `zoom.off` 基线互相印证（两个独立来源同时成立才算过）
- 新增永久回归锁 `test_zoom_window_frame_conservation`（24 例，
  修复前必然失败 —— 这是"用例真的能抓到该 Bug"的自证）
- `wincoord` 判据不得因产品滤镜写法变化而**静默退化**：写法认不出要单独报错
  （B57 的教训 —— 三段解析结果全 null 时旧判据只会喊"坐标错"）；
  并用**变异注入**（故意把坐标折反 / 写法退回 `duration=` / 切点偏移 0.001）
  证明每条判据各自有区分力、基线不误报，再去跑产品回归

## 9. 确定性

沿用 V7 的种子派生审计：子快照 `base+(i+1)*7919`、plan `base+(i+1)*104729`、
lens `base+53`、drop `base+29+i`、bitrate `base+17`；plan 的 RNG 抽取顺序固定。
本轮回归中 `v7_determ` / `fps_sr_determinism` 套件复跑（见 §11），
同 seed 两次真实渲染输出 SHA 相同、不同 seed 不同。

## 10. 证据

- 证据总数与分组、逐条索引见 `tests/evidence/EVIDENCE_INDEX.md`
- Bug 队列可读版见 `tests/evidence/BUG_QUEUE.md`
- 本轮 Bug 证据目录：
  `tests/evidence/bugs/B55/{discovery,attempt_01,final}`、
  `tests/evidence/bugs/B56/{discovery,attempt_01,final}`、
  `tests/evidence/bugs/B57/{discovery,attempt_01,final}`（含原始失败日志与
  错误判据原文、产品侧独立复核、变异注入验证）、
  `tests/evidence/bugs/B58/final`
  （含改动前源码状态、假设、素材、命令、stdout/stderr、度量、before/after、
  成功标志）

## 11. 回归结果

（本节在两轮**新执行**的完整回归结束后填写：RUN ID、逐套件判定、
新增 BUG / REGRESSION / TEST_INFRA_BUG 计数。）

## 12. 未解决 / 非 PASS 项（诚实清单）

- **PARTIAL**：`_merge_reencode` 相对单进程稳定 +1 帧（Δ时长 0.033s @30fps），
  B39 已用满 3 次尝试并封存为 PARTIAL；两条路径各自与自己的台账一致，
  容差 2 帧内。
- **INEFFECTIVE by design**（配置门 / 编码器分支决定，非缺陷）：
  `noise`（`video.noise.enable=false`）、`channel_mix`、`mask_drift_*`、
  `audio_noise_db`（仅 aggressive 预设）、`sc_threshold`（仅 CPU 编码器）、
  `asym_crop_*`（仅标准化开启）、快照级 `rl_*`（仅整文件路径）。
- **INEFFECTIVE by defect-free-but-dead**：`frame_drop_on`（快照生成、渲染链
  零引用）——不是缺陷但属冗余字段，建议清理。
- **NOT_APPLICABLE**：`_fps` 冗余字段（不存在被测对象）。
- **未覆盖（记 EVIDENCE_INSUFFICIENT，不得当作通过）**：
  `PATH_COVERAGE_MATRIX.md` 中标 `?` 的格子 —— 主要是
  `frame_drop` / `rotate_drift` / `lens` / 音频滤波 / 采样率 / `normalize.fps`
  在 `fallback` 与 `whole_file` 路径上的单独度量；
  以及编码器 × 组合、`fingerprint.*` 组合、`mask_drift` 组合。

## 13. 产品风险与建议

1. **B56 修复会改变默认产出**：出厂 `black_crop.enable=true`，修复后有黑边的
   素材会真的被裁剪（恢复设计行为），下游若依赖旧的"不裁"行为需要知会。
2. `detect_black_crop` 的 `except Exception: return None` 会把任何编程错误
   变成"没检测到"。建议至少把解析异常与 FFmpeg 失败分开记日志
   —— 本次缺陷之所以活了这么久，正是因为它静默。
3. `build_zoom_window_complex` 这类"按时间切段再拼回"的实现，只要用
   `duration=` 就会踩帧对齐；建议在项目内约定**一律用绝对 `end=`**，
   并让相邻片共用同一格式化字符串。
4. `quality_check` 的 `passed` 仍不阻断输出（历史记录），属产品决策，未改。

## 14. 复跑入口

```
python tests/unattended.py                 # 20 套件完整回归
python tests/param_forensics_v6.py combo switches dead
python tests/param_forensics_v7.py paths3 segacc sweep
python tests/test_timeline_integrity.py zoom_window
python tests/evidence_report.py            # 重生成索引/总档/Bug 队列
```

## 15. 停止条件核对

（本节在 §11 完成后填写：逐条列出停止条件与对应证据。）
