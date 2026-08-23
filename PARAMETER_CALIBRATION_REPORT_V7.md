# PARAMETER_CALIBRATION_REPORT_V7

Rewash Tool 参数最终闭环审计报告（V7）。**不覆盖历史**：V2~V6 报告与
`PARAMETER_BUG_REPORT_V4/V5/V6.md` 均保留。

- 审计期：2026-08-23 ~ 2026-08-24
- 主 RUN 区间：RUN-000037 ~ RUN-000047（V7 阶段），沿用同一证据数据库
  `tests/evidence/`（`index.json` 单调计数：EV-000388 / RUN-00004x / R-00000x）
- 工具链：本仓自带 `ffmpeg/bin/ffmpeg.exe`、`ffprobe.exe`（所有结论均来自**真实执行**）
- 判定口径：PASS / BUG / FIXED / REGRESSION / INEFFECTIVE / PARTIAL /
  EVIDENCE_INSUFFICIENT / TEMPORARY_BLOCKED
- 取证脚本：`tests/param_forensics.py`（V3）、`_v5`、`_v6`、`_v7`、
  驱动 `tests/unattended.py`

---

## 1. 参数表（44 项，逐参数隔离 + 真实命令 + 真实输出对照）

判据链：源参数 → 快照 → plan → filtergraph → **真实 FFmpeg** → ffprobe/帧数/SHA →
与中性基线对照。命令内未出现 → BUG；出现但输出逐字节相同 → INEFFECTIVE；
覆盖值与基线快照/出厂配置相同 → EVIDENCE_INSUFFICIENT（用例问题，不作产品结论）。

最新一轮完整结果：**RUN-000043 `sweep` 44/44 PASS，崩溃阶段 0**。

- 01 trim_head → `trim=start=1.2` PASS（帧数/时长/音频时长均变化）
- 02 trim_tail → `trim=…:end=` PASS
- 03 speed → `setpts=0.9259*PTS` PASS
- 04 frame_drop → `select='not(…)'` PASS
- 05 frame_dup → `tpad=start=3` PASS
- 06 reverse / 07 reverse_loop → `reverse` / `concat=n=` PASS
- 08 rotate / 09 rotate_drift_speed → `rotate=…sin` PASS
- 10 zoom / 11 zoom_drift_dir → `zoompan=` PASS
- 12 brightness / 13 contrast / 14 saturation / 15 hue → `eq=` / `hue=` PASS
- 16 noise → `noise=alls=` PASS **（需 `video.noise.enable`；出厂 false → 默认配置下 INEFFECTIVE，设计如此）**
- 17 scale_crop / 18 asym_crop / 19 lenscorrection → `crop=`/`scale=`/`lenscorrection=` PASS
- 20 audio_pitch → `asetrate=` PASS；44 aresample → `aresample=48000` PASS
- 21 audio_atempo → `atempo=1.05` PASS
- 22 audio_highpass / 23 audio_lowpass → `highpass=f=150` / `lowpass=f=11000` PASS
- 24 audio_fade → `afade=t=in` PASS
- 25/26 av_offset ± → `adelay=80` / `atrim=start=0.080` PASS
- 27 crf / 28 gop / 29 bframes / 30 sc_threshold → `-crf 30` / `-g 45` / `-bf 0` /
  `-sc_threshold 55` PASS（29 取 0：随机区间是 1~3，取 3 有概率与基线相同 → 证据不足）
- 31 audio_eq → `equalizer=frequency=300:width_type=h:width=200:gain=2.50` PASS
- 32 audio_noise_db → `anoisesrc`+`amix` PASS（仅 aggressive 档生成，标准档恒 None）
- 33 channel_mix → `colorchannelmixer=` PASS **（需 `video.channel_mix.enable`；出厂 false → 默认 INEFFECTIVE）**
- 34 mask_drift → `geq=` PASS（阈值 amp > 0.05；出厂 `video.mask_drift.enable=false` → 默认 amp=0）
- 35 frame_dup_pos → `trim=end=`/`tpad=start=2` PASS
- 36 normalize.fps → `fps=24` PASS（帧数与 avg_frame_rate 同步变化）
- 37 normalize 分辨率 → `scale=360:480`+`pad` PASS（输出 width/height 变化）
- 38 normalize.pix_fmt → `yuv422p` PASS（出厂已是 yuv420p，取 422 才有对照）
- 39 normalize.audio_codec → `-c:a libmp3lame` PASS（AAC 码率按 seed 派生，**不由配置指定**）
- 40 encode.cpu_preset → `-preset veryfast` PASS
- 41 concat / 42 setsar / 43 atrim+apad → 结构性判据（无覆盖，只验证结构真实出现在命令里）PASS

### 出厂配置下无效（INEFFECTIVE，非缺陷，设计/编码器固有）
- `noise`、`channel_mix`、`mask_drift_*`：`config.json` 中 `enable=false`
  （`presets/builtin.json` 明确：v7.2 实测收益/耗时比低，已从三档删除并全局关闭）
- `sc_threshold`：仅 libx264/libx265 分支发出（实测
  NVENC=`['-c:v','h264_nvenc','-preset','p3','-rc','constqp','-qp','24','-g','29','-bf','1']`
  无该项；CPU 回退=`[... '-sc_threshold','43' ...]`）→ **PARTIAL：编码器固有**
- `asym_crop_*`：仅在 `norm_spec` 存在（标准化开启）时生效，关闭标准化时被忽略
  → **PARTIAL：路径相关**
- `rl_pos_rel/rl_seg_len/rl_repeats/rl_mode`（快照级）：分段路径由 plan 级 rl 取代，
  仅整文件路径读取 → **PARTIAL：路径相关**

## 2. 缺陷表（B34~B49，共 16 条；全部 FIXED）

详见 `PARAMETER_BUG_REPORT_V6.md`。按严重度：

- P0：B37（整文件 plan rl → |a-v| +0.333s）、B39/B49（三路径帧数不一致）
- P1：B34、B35、B38、B40、B41、B42、B43、B44、B45、B46、B47
- P2：B36（rotate_drift_phase 死参数）
- P3：B48
- 其中 TEST_INFRA_BUG（只改测试、不改产品结论）：B34、B40、B41、B42、B45、B46、B47、B48

## 3. 根因归类

1. **口径不统一**（同一物理量两处各算一套）：B38（质检期望 base speed vs 段 speed）、
   B39/B49（输入窗口取帧：`-ss/-t` vs `trim`；metrics 传/不传 t0/t1）、
   B43（`effective_duration` 2s 下限 vs 素材末尾）、B44（容器时长 vs 视频流时长）
2. **生成但未送达 FFmpeg**：B36（相位被强制 0）
3. **分支遗漏**：B35（显式 `*_nvenc` 键无 CPU 对应项）、B37（plan 级 rl 在简单链被丢弃却已计入期望）
4. **测试自证自洽 / 判据错误**：B34（扫描自身输出）、B40（比较含文件名的命令串）、
   B41/B46/B47（配置门/配置键写错）、B42（数据形状与产品不一致）、B48（期望值抄错规则）、
   B45（返回值形状用错）

## 4. 每次修复尝试（含失败尝试，不删除）

- B24（V6 期）：attempt_01 只改 `zoompan fps=` 栅格 → **FAIL**（残差 +1 帧/段）；
  attempt_02 独立 ffmpeg 变体实验后改链路顺序（推镜移到变速之前）→ PASS
- B39/B49：attempt_01 给 `process_clip` 的 metrics 传 `t0/t1` → **FAIL**
  （RUN-000045 帧数完全未变：支路 [111,109,110] / 段 [111,110,111] / 合并 332）；
  该改动仍保留（修正了预测口径），attempt_02 在公共链插入
  `trim=end_frame=n_win` 并给 `-t` 留 2 帧余量 → **PASS**（RUN-000046）
- 其余 B35/B36/B37/B38/B40~B48 均为 attempt_01 一次通过（见 bug_queue.json 的
  `attempts[]` 与 `tests/evidence/bugs/Bxx/round_01_attempt_01/result.json`）
- 被实测否决的假设（保留为证据）：`settb=1/90000` 修 zoom 帧差（292 帧，与不加相同）；
  B39 的"`-ss/-t` 与 trim 口径不同"在**帧边界对齐**的窗口上无差异
  （`.comate/b39_repro.py`：0.5/4.2/7.9 三窗口全 111 帧，Δ=0）→ 复现条件必须用
  非帧对齐切点

## 5. before/after 实测数据

- B37：before v=14.000 / a=14.333（|a-v| +0.333s）→ after |a-v| 0.000
- B38：before 期望 11.538 vs 实际 12.400（误差 6.9%）→ after 期望 12.371 vs 12.400（0.23%）
- B43：before v=1.067 / a=1.933（+0.866s，32 帧）→ after |a-v| −0.000333（31 帧）
- B44：before v=7.200 / a=12.600（+5.400s，216 帧）→ after |a-v| −0.033（208 帧）
- B39：before 单进程 330 / 分段 332~333（段 [111,110,111]）→ after 段 [111,109,110]
  与单进程支路逐段一致，合并 331（Δ=1，容差 ±2 帧）
- B35：before `use_nvenc=False` 仍给 `h264_nvenc` → after `libx264`（回退失效编码器数 0）
- B36：before 图内相位全 0.0000 → after 非 0 相位 6 处（快照 1.9）

## 6. 三条生产路径对照（同 seed / 同快照 / 同参数 / 同素材）

RUN-000046（V7-BASE 12.0s/30fps/48kHz，n_seg=3，speed=1.05，frame_dup=2，
trim 0.4/0.3，av_offset=0.05，frame_drop 概率 1.0）：

- A `segment.process_single_pass`：330 帧 / 11.000s（= 台账预测 330 / 11.000）
- B `process_clip×3 + _merge_reencode`：331 帧 / 11.033s（Δ帧 = **1**，Δ时长 0.033s）
  - 逐段 `process_clip` 帧数 [111, 109, 110] == A 的逐段支路帧数 [111, 109, 110]
  - 残差 1 帧出现在 `_merge_reencode` 的 concat 重编码边界（在 ±2 帧容差内）
- C 整文件 `process_clip(in_duration=None)`：325 帧 / 10.833s
  —— **构成本就不同**（单快照、单段 frame_dup，而 A/B 是 3 段各自 dup），
  因此不与 A/B 做 1:1 帧比较，只校验其自身台账、PTS 单调、分辨率/帧率/采样率一致
- 三路径一致项：324x432、30fps、48000Hz、h264、PTS 单调递增

## 7. FPS 矩阵（RUN-000040 `fpsmat`，12 组合全 PASS）

素材 10.0s，n_seg=2，统一参数集；判据 = 台账预测 vs 真实帧数/时长（Δ帧 0、Δ时长 0.0）：
25 / 29.97(30000/1001) / 30 / 50 / 60 / 120 fps × 44100 / 48000 Hz → 12/12 PASS。
非整数帧率（29.97）单独用 `weird/w6_ntsc_tb` 复核：266 帧、|a-v| = −0.000658s，PASS。

## 8. 采样率矩阵

- 44100 / 48000 两档在上表 12 组合中全部 PASS（输出采样率与源一致，无静默重采样）
- `audio_pitch` 在采样率未知时**不做变调**（B5 历史缺陷的修复）：
  `sweep/20_audio_pitch` 与 `44_aresample` 均验证 `asetrate=`+`aresample=<真实采样率>` 成对出现
- 双音轨素材 `weird/w5_multi_audio`：264 帧、|a-v| = 0.0，PASS（`[0:a]` 取首条音轨）

## 9. 组合矩阵（RUN-000040 `combo19`，19 组合全 PASS）

x01 speed+drop / x02 speed+dup / x03 speed+reverse / x04 speed+rl / x05 speed+zoom /
x06 speed+rotate / x07 speed+lens / x08 speed+zoom+rotate / x09 fps+drop /
x10 fps+dup / x11 fps+zoom / x12 fps+rl / x13 sr+pitch / x14 sr+atempo /
x15 av_offset+trim / x16 av_offset+speed / x17 trim+concat / x18 audio_dur+concat /
x19 fps+全帧类参数 → 19/19 PASS。最大偏差：x05 与 x19 各 Δ帧 1（Δ时长 0.033/0.042s），
其余 Δ帧 0；|a-v| 最大 0.000667s。

## 10. 随机性与确定性（RUN-000039 `determ` / RUN-000044 `dist`）

- 同 seed 两次**真实渲染**输出 SHA256 完全一致：
  `25e7f9f927a8960f07e7de988f11bd9a4f3b89a1e8639c25bb444ee5d8f908c3`（两次相同）
- 异 seed 输出不同：`3f866b9280897fa15fc78e15cb67d52fdd2c6b056f4c4152f264a247874200ea`
- 派生种子链（互不相同）：child `base+(i+1)*7919`、plan `base+(i+1)*104729`、
  lens `base+53`、drop `base+29+i`、码率 `base+17`；第 0 段不复用 base seed
- 分布：`rl_pos_rel` 120 seed → 118 个不同值、范围 [0.176, 0.838]；
  AAC 码率 [128,256] 去重 71，命令值 150k == 由 seed 派生的期望 150k；
  抽帧计划 40 seed → 20 个不同计划，越界 0、重复/乱序 0
  （越界这一条由 `filters.py` 的 clamp 结构性保证，已在证据里标注**不作为判据**）
- 参数随机性 ≠ 日志/元数据随机性：日志与 `dump_snapshot` 里的时间戳不参与判据，
  确定性只以**输出字节**与**命令（掩去输出文件名）**为准

## 11. 回归结果

（见文末「回归与停止条件」小节，由本轮两次**新执行**的完整回归回填）

## 12. 证据索引

- 根目录：`tests/evidence/`
  - `index.json`：EV/RUN/R 单调计数（本报告成稿时 ev_counter=388、run_counter=47）
  - `runs/RUN-0000xx/EV-0000xx/`：固定编号证据文件
    （01_metadata … 14_comparison / 15_verdict / 16_failure_analysis / 20_before_after）
  - `bugs/Bxx/`：`README.md`（发现 + 根因 + 修复 + 验证）、
    `round_01_attempt_0x/result.json`、`bug.json`
  - `regression/R-00000x/`：每轮回归的套件级结果与完整 stdout
  - `BUG_QUEUE.md` / `EVIDENCE_INDEX.md` / `PARAMETER_MASTER_AUDIT.md`：自动生成
- 每条证据都带 `git_head` 与 8 个产品文件的 `worktree_hash`，可判断证据是否与当前代码同源

## 13. 未解决项 / 明确标注的残留

- **B39 残留 1 帧**（P2 级残留，已在容差内）：`_merge_reencode` 的 concat 重编码使
  分段路径总帧数比单进程多 1 帧（331 vs 330，Δ时长 0.033s）。逐段帧数已完全一致，
  残差只出现在合并遍。判定 PARTIAL —— 不再继续收敛，因为
  ① 分段路径只是单进程失败后的降级路径；② 偏差 1 帧 < ±2 帧容差且不影响音画同步。
- **整文件路径（C）与分段路径不可 1:1 比帧**：整文件只应用单快照，
  frame_dup 只加一次。这是设计语义差异，不是缺陷；报告中按各自台账校验。
- 死代码（未删除，仅登记）：`filters.get_encode_args`、`filters.build_spatial_chain`
  （及其唯一调用者 `build_zoompan_filter`）、`normalize.normalize_output`、
  `quality_check.check_final_product`、`ffmpeg_runner.has_audio/get_duration`、
  `config.rand_range`、`preset.is_overridden/get_param/restore_builtin`、
  `batch/retry.run_with_retry`、`fingerprint/detector._get_resolution/_layer1_histogram_reject`；
  `segment.decide_segment_count` 在生产中不可达（`processor` 总是显式传 `requested_count`）。
- 静态扫描登记项（非缺陷）：`filters.py:497-513` 的 `rate/sr` 属"可能未绑定"模式；
  `segment.py:349` 死存储 `ok=True`；`video_processor.py:148` 未使用的 `frame_dup_n`；
  `_graph.py` 的 `rl_in` 无消费者；`normalize.py:85` 无操作语句；
  `randomizer.py:339` 未使用的 `deg2rad`。
- 质检不阻断：`quality_check` 的 `passed` 只写日志，不阻止产物落地（产品设计选择，已登记）。

## 14. 产品风险

1. **降级路径合并遍多 1 帧**（上文 B39 残留）：连续多次降级会累积（每次 1 帧/次合并），
   长视频批量处理时可能出现累计几十毫秒的时长漂移。建议后续把 `_merge_reencode`
   也纳入帧精确台账。
2. **音画长度依赖视频流时长**：现在 `probe_media` 在视频流更短时以视频流为准。
   若素材的视频流时长元数据缺失（部分流式容器），会退回容器时长 —— 此时
   音频比视频长的素材仍可能出现 |a-v| 偏差。已在代码注释中标注该退化路径。
3. **NVENC 与 CPU 编码参数不等价**：`sc_threshold` 只在 CPU 分支生效，
   同一 seed 在有/无 N 卡的机器上产物不同（确定性只在同一编码器下成立）。
4. **出厂关闭的三个扰动参数**（noise/channel_mix/mask_drift）仍在随机化并写入快照，
   会出现在快照 JSON 里但对产物无影响 —— 排查时容易误判。
5. **短素材（<2s）会自动放弃 trim**：这是 B43 的正确处置，但意味着用户设置的
   首尾裁剪在极短素材上不生效（已在 `effective_duration` docstring 说明）。
