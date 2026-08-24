# PARAMETER_BUG_REPORT_V7

本文件只增量记录 **B45 ~ B58**（B34~B44 见 `PARAMETER_BUG_REPORT_V6.md`，
更早见 V3/V4/V5 报告）。历史文件不改写。

判定词表：
- `BUG`：实测越界的产品缺陷
- `TEST_INFRA_BUG`：测试用例/驱动自身的错误（不得记为产品结论）
- `INEFFECTIVE`：执行了但无可观测变化（须区分「设计如此」与「本该生效却没有」）
- `EVIDENCE_INSUFFICIENT`：没测到（无输出/无法度量/基线==覆盖/被配置门挡住）
- `NOT_APPLICABLE`：命题没有被测对象，且不存在可补的证据

---

## 一览

- B45 TEST_INFRA_BUG：`isolate_video_branch` 返回值解包错误 → segacc 阶段崩溃
- B46 TEST_INFRA_BUG：SWEEP 39 行用了不存在的配置键 `normalize.audio_bitrate`
- B47 TEST_INFRA_BUG：SWEEP 40 行用了不存在的配置键 `normalize.preset`
- B48 TEST_INFRA_BUG：v6 dup 期望值抄错（规则是 `dup_dur > 1.0`，1.05s ⇒ dup=3）
- B49 产品（PARTIAL）：降级路径输入窗口帧精确化（`-t` 折算），残留 1 帧在
  `_merge_reencode`
- B50 TEST_INFRA_BUG：驱动把脚本自述汇总行当成 BUG 关键字命中（fps_sr_determinism）
- B51 同 B50（v5_visual）
- B52 同 B50（v5_encode）
- B53 同 B50（v5_audio）
- B54 回归：B39 attempt_02 的窗口截断使降级路径每段少 2 帧（attempt_03 已修）
- **B55 产品：推镜窗口切段重复 1 帧（`trim duration=` 从首个通过帧起算）**
- **B56 产品：黑边裁剪恒定不生效（4 组解包给 5 个名字，被 bare except 吞掉）**
- **B57 TEST_INFRA_BUG：wincoord W2 判据仍匹配 B55 已删除的 `duration=` 写法**
- **B58 TEST_INFRA_BUG：套件级 `v6_wincoord` ERROR（B57 的驱动侧同源记录）**

---

## B50 ~ B53｜驱动的自述行被当成 BUG（TEST_INFRA_BUG，P2）

- 现象：`tests/unattended.py` 的四个套件返回码 0，却被判 `BUG_FOUND`；
  `bug_queue` 里这四条的 `detail.lines` **只有**一行：
  「完成。判定为 BUG/REGRESSION 的检查项=0，崩溃阶段=0」。
- 根因：驱动逐行扫描子脚本 stdout 找 BUG 关键字，命中了脚本自己的汇总行
  （典型的「测试自己骗自己」——把"BUG=0"的陈述当成"发现 BUG"）。
- 修复：`unattended.py` 的噪声过滤扩展
  `NOISE_PREFIX += ("完成。",)`、
  `NOISE_SUBSTR += ("BUG/REGRESSION 检查项", "判定为 BUG/REGRESSION", …)`。
- 独立复验：R-000005(RUN-000048) 与 R-000006(RUN-000062) 两轮全量，
  这四个套件均 PASS，不再复现。

## B54｜降级路径每段少 2 帧（回归，P1）

- 来源：B39 attempt_02 在降级路径链首插 `trim=end_frame=n_win`，与 `-ss/-t`
  的输入窗口叠加。
- 现象：`v5_fallback` 的 `F2_semantics_parity` / `F3_timeline_vs_prediction` /
  `M1_frames_conserved` 同时 BUG（单进程 341 帧 vs 降级 335 帧）。
- 定位方式：故意做 A/B —— 把该行改成 `if False and ...` 再跑同一套件
  （`.comate/fb_with_wintrim.txt` 对 `.comate/fb_without_wintrim.txt`）。
- 修复：回退 attempt_02，改为帧精确 `-t`：
  `t_first = ceil(start*fps)/fps`，`-t = (n_win-0.5)/fps + (t_first-start)`。
- 复验：`v5_fallback` 13/13 PASS；RUN-000080 `segacc` 合并=331 / 单进程=330 /
  台账=330，`paths3` Δ帧=1（残留记 B39 的 PARTIAL）。

## B55｜推镜窗口切段重复 1 帧（产品，P1）

- 现象：`combo/c07_zoom_rotate_speed@TEST-C` 长期 BUG：
  `Δ帧=3 Δ时长=0.1 |a-v|=-0.034`（3 段 × +1 帧）。
- 触发条件（实测否定了"短窗口"的直觉）：与窗口长短无关，取决于
  `(start, start+dur) × speed` 是否落在帧栅格上。
  固定 start=5.056 扫 dur：0.5/0.8/1.054/1.5/3.0/3.448/4.0 全部 +1，dur=2.0 为 0；
  固定 dur=1.054 扫 start：2.017/5.056 为 +1，0.0/0.5/1.0/7.5 为 0。
- 定位：把产品生成的**原样**分片串单独计帧，`sum(A,B,C) = 301 > n(输入)=300`
  → 同一帧被两片各取一次；zoompan 前后计数一致，排除 zoompan 与 concat。
- 根因：FFmpeg `trim` 的 `duration=` 从**第一个通过的帧**起算
  （`trim.c`：`frame->pts - first_pts >= duration_tb`），不是从 `start` 起算。
  切点非帧对齐时 B 片实际末端 `ceil(a*fps)/fps + d > a+d`，多含 1 帧；
  C 片 `start=a+d` 又取同一帧。纯 FFmpeg 对照（a=5.208 d=1.086 b=6.294）：
  `duration=` 版 B=33 帧、`end=` 版 B=32 帧，C=111 → 144 vs 143。
  次生隐患：`b = a+d`（取整前相加）与 B 片打印值之和可能差 1ms。
- 修复：`build_zoom_window_complex` 三片改用绝对半开区间，
  且相邻片共用同一格式化字符串：
  `trim=end={a_s}` / `trim=start={a_s}:end={b_s}` / `trim=start={b_s}`。
- 复验（新执行）：RUN-000079 v6 combo **16/16 PASS**（c07@TEST-C Δ帧 3→0，
  c07@TEST-E −1→0）；RUN-000080 paths3+segacc 4/4 PASS；v5 fallback 13/13 PASS；
  单元级 15/15 窗口帧数 == 台账、10/10 组分片守恒。
- 永久回归锁：`tests/test_timeline_integrity.py::test_zoom_window_frame_conservation`
  —— 7 组**非帧对齐**窗口 × speed 1.00/1.03/0.97 共 24 例，
  要求 `实测帧数 == 台账预测 == zoom.off 基线`（修复前 9/15 会失败）。
- 与历史结论的关系：B24 把切段移到变速之前是对的但不完整，
  它只对齐了 zoompan 的 fps 栅格，没有消除 `duration=` 的语义偏差。

## B56｜黑边裁剪恒定不生效（产品，P1）

- 现象：`switches/s03_black_crop_on` 判 EVIDENCE_INSUFFICIENT，
  打印「产品未检出黑边」；素材 `V-BLACKBAR.mp4`（480x640，上下各 80px 黑边）。
- 复现：同一素材上原始 `cropdetect` 稳定输出 `crop=480:480:0:80` 共 238 行
  （四种参数组合一致），而 `detect_black_crop` 返回 `None`。
- 根因：`ffmpeg_runner.py` 把 4 个捕获组解包给 5 个名字
  （`_, w, h, x, y = re.match(...).groups()`）→ `ValueError` 被函数末尾的
  `except Exception: return None` 静默吞掉 → **对任何素材恒返回 None**。
  出厂配置 `video.black_crop.enable = true`、`detect = true`，
  功能本应默认开启，因此不能记 "INEFFECTIVE by design"。
- 为什么以前没抓到：V6 时期该用例用**无黑边**的 TEST-C，
  baseline == override，PASS 是自证自洽；且判定里根本没有检查裁剪是否发生。
- 修复：
  1. 产品：`w, h, x, y = re.match(...).groups()`；
  2. 用例：black_crop 分支补两条真判据 —— 产品检出的 rect 必须逐字出现在
     **真实执行的命令**里，且与 `black_crop.enable=false` 的对照渲染输出
     SHA 必须不同。
- 复验：`detect_black_crop` → `(480, 480, 0, 80)`；RUN-000081 switches 4/4 PASS；
  RUN-000082（补强判据后）4/4 PASS，`EV-000703` 记录
  `crop_in_command=true`、`differs_from_no_crop_control=true`。
- 风险提示：修复后默认配置下**会真的开始裁黑边**，对有黑边的素材输出画面
  与修复前不同（这是恢复设计行为，但属可观测的产出变化）。

## B57｜wincoord 判据仍匹配 B55 已删除的写法（TEST_INFRA_BUG，P2）

- 现象：V8 全量回归第 1 轮（RUN-000084）中 `[v6_wincoord] ERROR rc=2 命中=1`，
  其余 14 个套件全 PASS。用例内 `W2_zoom_coords_times_speed` 判 BUG，
  三段 `graph_zoom_trim` 全为 **null**（不是数值不符，是一个数都没解析出来）。
- 根因：`phase_wincoord` 的判据正则是
  `trim=start=([\d.]+):duration=([\d.]+)`，而 B55 的修复已把推镜 B 片改成绝对
  `trim=start=a:end=b`。匹配失败即静默 `None`，`_close(None, expect)` 恒 False
  → W2 恒判 BUG；更糟的是这种退化让判据**无法区分**「滤镜写法变了」与
  「坐标换算错了」——两种情况都退化成 null。
- 产品侧独立复核（`.comate/b57_probe.py`，抓本次真实执行的 `-filter_complex`）：
  三段 `start/end` 与 `[plan.start*speed, (plan.start+plan.dur)*speed]` 逐位一致
  （3.083/6.233、3.593/6.743、3.990/6.019，Δ=0.000），且 B 片 `end=` 与 C 片
  `start=` 字符串逐字符相同 → **产品无缺陷**，`_graph.py:246` 的 ×speed 折回
  未被 B55 影响。
- 修复（只动测试，判据**增强**而非放宽）：
  1. 按 `start=/end=` 解析，并显式记录写法 `graph_zoom_form`，认不出写法要单独
     报错而不是静默 null；
  2. W2 口径改为绝对区间 `[start*speed, (start+dur)*speed]`；
  3. 新增 `W4_zoom_trim_absolute_form`（B 片必须绝对 `end=` 写法）与
     `W5_zoom_cut_strings_shared`（C.start == B.end、A.end == B.start 字符串相同，
     把 B55 的不变量固化进 wincoord）。阈值 `tol=0.003` 未动。
- 判据可信度先验证再回归（变异注入，证据库重定向到临时目录不污染真实证据）：
  M0 基线 W1–W5 全 PASS；M1「坐标不折回」→ 仅 W2 BUG；M2「写法退回 duration=」
  → W4 BUG（并连带 W2/W5）；M3「C 片 start 偏移 0.001」→ 仅 W5 BUG。
  即每种注入都被对应项抓到，基线不误报。
- 复验：`RUN-000098` rc=0，`1 用例 {'PASS': 1}`，W1–W5 全 PASS。
- 分类说明：这是「修了产品却没同步修判据」型测试债，属 B55 的收尾遗漏，
  不得记成产品结论。

## B58｜套件级 `v6_wincoord` ERROR（TEST_INFRA_BUG 派生，P1）

- 由 `unattended.py` 在套件 rc≠0 时自动登记，`detail.lines` 只有 B57 的那一行。
- 根因与 B57 完全同源，不做独立修复；修 B57 后 `RUN-000098` rc=0。
- 证据：`tests/evidence/bugs/B58/final`，原始日志
  `tests/evidence/runs/RUN-000084/suite_v6_wincoord.log`。

---

## 本轮对「非 PASS 状态」的清账

- `dead/snapshot_fps_field`：EVIDENCE_INSUFFICIENT → **NOT_APPLICABLE**
  （快照里不存在任何 `*fps` 字段 → 命题无被测对象，也没有可补的证据；
  帧率活口径由 `sweep/36_normalize_fps` PASS 覆盖）。
- `dead/frame_drop_on`：保持 **INEFFECTIVE**（快照生成该字段但渲染链引用数 = 0，
  真正的门是 `video.frame_drop.enable` 与段级 plan）——属"生成但不可达"，
  已按规则标记，不当作 PASS。
- `sweep/16_noise`、`sweep/33_channel_mix`、`34_mask_drift`、`32_audio_noise_db`
  （非 aggressive 预设）、`30_sc_threshold`（NVENC 分支）：
  **INEFFECTIVE by design**，配置门/编码器分支决定，已在
  `PARAMETER_MASTER_AUDIT.md` 的「配置门」字段留档。
