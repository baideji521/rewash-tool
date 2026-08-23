# PARAMETER_BUG_REPORT_V6（V7 闭环审计期发现的缺陷清单）

> 版本策略：本文件只记录 **B34～B44** 段（V6 收尾 + V7 最终闭环审计期）的缺陷。
> 历史缺陷见 `PARAMETER_BUG_REPORT_V4.md` / `PARAMETER_BUG_REPORT_V5.md`（不覆盖）。
> 状态口径：OPEN / FIXING / TEMPORARY_BLOCKED / VERIFYING / FIXED / REGRESSION /
> EVIDENCE_INSUFFICIENT / PERMANENT_BLOCKED；另有非缺陷结论 INEFFECTIVE / PARTIAL。
> 证据根目录：`tests/evidence/`（bugs/Bxx/… + runs/RUN-0000xx/EV-0000xx/…，只增不删）。

## 汇总

- B34 P1 FIXED — 回归驱动把取证脚本自身的「Bug 队列」摘要行误判为套件失败（TEST_INFRA）
- B35 P1 FIXED — NVENC→CPU 回退无效（两次命令编码器相同，日志谎报回退）
- B36 P2 FIXED — `rotate_drift_phase` 生成后被强制置 0（参数从未进入 FFmpeg）
- B37 P0 FIXED — 整文件路径 plan 级 `reverse_loop` 导致音视频时长不一致（+0.333s）
- B38 P1 FIXED — 质检期望时长口径与实际不一致（base speed vs 段 speed，误差 6.9%）
- B39 P0 —— 三路径一致性（single_pass vs segmented vs whole_file 帧数差），见下文
- B40 P1 FIXED — 确定性判据错误（比较含输出文件名的整条命令串，TEST_INFRA）
- B41 P1 FIXED — `sweep/16_noise` 判据缺配置门（TEST_INFRA）
- B42 P1 FIXED — `sweep` 阶段崩溃：list 型 EQ band（TEST_INFRA + 产品健壮性收口）
- B43 P1 FIXED — 短素材（1.6s）切点越过素材末尾 → 输出 |a-v| = +0.866s
- B44 P1 FIXED — 音频比视频长（8s/14s）→ 输出 |a-v| = +5.400s

---

## B35 NVENC→CPU 回退无效
- 用例 `encoder/nvenc_cpu_fallback`（EV-000191）
- 现象：`use_nvenc=True/False` 两次 `spec_encode_args` 对 `h264_nvenc` 返回**完全相同**
  的参数（`-c:v h264_nvenc -preset p4 -rc constqp -qp 24 -g 29 -bf 1`），
  而 `segment.process_single_pass` 的 `for nv in [True, False]` 依赖第二次换成 CPU 编码器；
  日志仍打印「单进程 NVENC 失败，回退 CPU 重试」→ 日志与实际不符。
- 根因：`ENCODER_TABLE` 里显式的 `*_nvenc/_qsv/_amf` 键没有 CPU 对应项，
  `use_nvenc=False` 只影响 `h264`/`h265` 这两个"旧配置兼容"键。
- 修复：`video/filters.py` 新增 `HW_TO_CPU`（h264/h265/av1 × nvenc/qsv/amf → 同族 CPU
  编码器），`spec_encode_args` 在 `use_nvenc=False` 时先做键替换。
- 验证：RUN-000039 PASS，回退失效的编码器列表 = `[]`。

## B36 rotate_drift_phase 死参数
- 用例 `rotphase/phase_reaches_ffmpeg`（EV-000192）
- 现象：快照 `rotate_drift_phase = 1.9`，filtergraph 内 6 处相位全是 `0.0000`。
- 根因：`_graph.py`、`video_processor.py` 的微旋窗口分支都把 `p_rot["rotate_drift_phase"]`
  强制置 0。
- 修复：两处改为使用快照相位值。
- 验证：RUN-000039 PASS，图内非 0 相位数 = 6。

## B37 整文件路径 plan 级 reverse_loop
- 用例 `wholerl/plan_rl_whole_file`（EV-000193）
- 现象：plan 级 rl（mode=loop, repeats=3）在 `-vf` 简单链里被丢弃，
  但 `segment_video_metrics`/`expect_dur` 已计入 rl 帧 → 音频侧 `apad` 补长。
  实测输出 v=14.000s / a=14.333s → **|a-v| = +0.333s**。
- 根因：`video_processor.process_clip` 的条件 `not seg_mode and p.get("rl_mode")`
  只中和了快照级 rl，plan 级 rl 既没进视频链也没被中和。
- 修复：`seg_mode=False` 时显式 `plan["reverse_loop"] = {"mode": None}`。
- 验证：RUN-000039 PASS，`plan_rl=loop v_concat=0 a_cut=0 |a-v|=0.0`。

## B38 质检期望时长口径
- 用例 `qc/expect_duration_scope`（EV-000194）
- 现象：`effective_duration=12.0s`，主快照 speed=1.04，三段 child speed 均 0.97。
  期望（base 口径）= 11.538s，实际 = 12.400s → 误差 **6.9% > 5% 容差**，稳定误报。
- 根因：`processor.py` 质检期望只用主快照 speed，而分段路径每段 speed 重新随机。
- 修复：新增唯一口径 `quality_check.expected_duration(eff, snap, seg_snaps)`，
  分段路径按各段 child 的 speed 折算；`processor.py` 传入 `seg_snaps`。
- 验证：RUN-000039 PASS，期望（段口径）= 12.371s vs 实际 12.400s（误差 0.23%）。

## B40 确定性判据错误（TEST_INFRA）
- 用例 `determ/real_render`（EV-000196）
- 现象：同 seed 两次真实渲染输出 SHA256 **完全一致**、异 seed 不同，
  但 `same_seed_identical_command=False` → 被判 BUG。
- 根因：判据比较了含输出文件名的整条命令串（`..._det_a1.mp4` vs `..._det_a2.mp4`）。
- 修复：比较前把输出文件名归一化为 `OUT.mp4`。
- 验证：RUN-000039 PASS（同 seed 一致=True，异 seed 不同=True）。

## B41 / B42 取证用例缺陷（TEST_INFRA）
见 `tests/evidence/bugs/B41/README.md`（两条合并记录）。要点：
- B41：`noise` 有出厂关闭的配置门 `video.noise.enable=false`，用例未打开该门；
  同时确认**出厂配置下 `noise` 恒定无效（INEFFECTIVE，设计如此）**。
- B42：用例用了 list 型 EQ band，产品 `build_audio_filter` 的 `except (TypeError,
  ValueError)` 收不住 `AttributeError` → 整阶段崩溃、31~44 号参数未执行。
  用例改 dict 型；产品侧对非 dict band 直接跳过（边界输入健壮性）。

## B43 短素材切点越过素材末尾
见 `tests/evidence/bugs/B43/README.md`。
- 实测（EV-000266）：1.6s 素材 + trim 0.5/0.4 → 输出 v=1.067s / a=1.933s，|a-v|=0.866s。
- 根因：`segment.effective_duration` 的 `max(2.0, dur-th-tt)` 把 eff 抬到 2.0，
  而切点是 `trim_head + cuts[i]` → 末切点 2.5s > 素材 1.6s。
- 修复：eff < 2s 时先放弃 trim_tail、再放弃 trim_head，保证 `trim_head + eff ≤ duration`。
- 验证：RUN-000041 PASS，|a-v| = -0.000333s。

## B44 音频比视频长
见 `tests/evidence/bugs/B44/README.md`。
- 实测（EV-000268）：视频 8s / 音频 14s 素材 → 输出 v=7.200s / a=12.600s，|a-v|=5.400s。
- 根因：`probe_media` 只读容器 `format.duration`（= max(视频, 音频)），
  时间轴真值应为视频流时长。
- 修复：`probe_media` 增加逐流时长（`v_duration`/`a_duration`/`container_duration`），
  视频流更短时 `duration` 取视频流时长。
- 验证：RUN-000041 PASS，|a-v| = -0.033s。
