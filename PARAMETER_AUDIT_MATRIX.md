# PARAMETER_AUDIT_MATRIX

Rewash Tool 全参数取证矩阵 —— 第一阶段：枚举 + 证据状态基线

- 建立时间：2026-08-23
- 扫描范围：`video_rewash/**/*.py`、`config.json`、`paths.json`、`video_rewash/presets/*.json`、`tests/`
- 工作区 git 状态（仅记录，未处理）：

```
HEAD 处于分离状态 → a828758d31778ef94b6c8ed1810cd2c81f3ea7d6
git log -5:
  a828758 Add files via upload
  74874ed Update PARAMETER_CALIBRATION_REPORT.md
  08062c6 auto upload 2026-08-23 18:29:34
  946065e fix: block files over 1MB in upload script
  23069df fix: persist parameter tuning and calibrate drift controls

git status --porcelain:
  AA PARAMETER_CALIBRATION_REPORT_V2.md      ← unmerged（both added），未处理
  D  tests/reconstruct_test.py               ← 已暂存删除
  A  tests/test_timeline_integrity.py        ← 已暂存新增
  M  video_rewash/audio/audio_processor.py
  M  video_rewash/core/_graph.py
  M  video_rewash/core/ffmpeg_runner.py
  M  video_rewash/core/processor.py
  M  video_rewash/core/quality_check.py
  M  video_rewash/core/randomizer.py
  M  video_rewash/core/segment.py
  M  video_rewash/video/filters.py
  M  video_rewash/video/video_processor.py
  ?? PARAMETER_CALIBRATION_REPORT_V3.md

git diff --stat 08062c6 a828758 → 仅 PARAMETER_CALIBRATION_REPORT_V2.md（+501 行），无源码变更
```

## 状态取值定义

| 状态 | 含义 |
|---|---|
| `PASS` | 有真实 FFmpeg/ffprobe 实测证据，改变量 == 预期（在容差内） |
| `BUG` | 有实测或源码级确证的错误行为 |
| `INEFFECTIVE` | 参数进入了代码但对输出无作用（或被后续 filter 覆盖） |
| `PARTIAL` | 部分条件下正确，部分条件下错误 |
| `REGRESSION` | 曾经正确，现在错误 |
| `EVIDENCE_INSUFFICIENT` | 尚无实测证据，禁止下结论 |

容差：`duration ≤ 0.01s`；`frame ≤ 1 frame`。

## 证据来源标记

- `V2` = `PARAMETER_CALIBRATION_REPORT_V2.md` 的真实 FFmpeg A/B 实测
- `V3` = `PARAMETER_CALIBRATION_REPORT_V3.md` 的真实 FFmpeg 实测
- `M1` = 本轮（V4）新测
- `SRC` = 仅源码级确证（未实测）

---

## A. 时间轴类

| 参数 | 生成位置 | 传递路径 | FFmpeg 位置 | 预期效果 | 实测效果 | 误差 | 状态 | 证据 |
|---|---|---|---|---|---|---|---|---|
| `speed` | `randomizer.py:220-222` | `segment.py:144`→`_graph.py:219` | `setpts={1/speed:.6f}*PTS` | duration ×(1/speed) | 0.90→13.966667 / 1.00→12.566667 / 1.10→11.533333 | 0.000% | `PASS` | V3 五 |
| `trim_head` | `randomizer.py:100` | `segment.py:284`→`:297`→`:149` | `trim=start=t0` | 掐头 | 首段 t0=0.964=trim_head | 0 | `PASS` | V3 六 |
| `trim_tail` | `randomizer.py:101` | 同上 | `trim=end=t1` | 去尾 | 末段 t1=20.417=dur−trim_tail | 0 | `PASS` | V3 六 |
| `effective_duration` | `segment.py:51-64` | `segment.py:284` | — | `dur−th−tt` | 19.453000 == 手算 | 0 | `PASS` | V3 六 |
| `effective_duration` 的 `max(2.0,…)` | `segment.py:64` | — | — | 短片钳位 | 未测 | — | `EVIDENCE_INSUFFICIENT` | V3 E2 |
| `segment_count` | `config.json:3` / `segment.py:24-47` | `processor`→`process_segmented` | 段数 = concat n | 6 段 | 6 段窗口连续 | 缝隙 0.000000 | `PASS` | V3 一 |
| segment 窗口连续性 | `segment.py:297` | — | `trim=start/end` | 无跳帧/无重复 | 5 处缝隙全 `+0.000000` | 0 | `PASS` | V3 一 |
| `av_offset`（正向） | `randomizer.py:268-273` | `filters.py:534-536` | `adelay=N:all=1` | 音频滞后 av | onset +0.1050，端到端 +0.1050 | <1ms | `PASS` | V3 九 |
| `av_offset`（负向，滤镜级） | 同上 | `filters.py:537-538` | `atrim=start=\|av\|` | 音频超前 | onset −0.1050 | <1ms | `PASS` | V3 九 |
| `av_offset`（负向，端到端） | 同上 | 同上 | 同上 | 音频超前 | 测量方法失效 | — | `EVIDENCE_INSUFFICIENT` | V3 E1 |
| `av_offset` 死区 ±0.02 | `filters.py:535,537` | — | 无滤镜 | 应生效 | `av=+0.010` → 偏移 `+0.0000` | 100% | **`BUG`(B4)** | V3 九 |
| `rl_mode` / `reverse` | `randomizer.py:231-241` | `filters.py:345,363` | `trim×3 + reverse + concat` | 不改长度 | Δv=Δa=0（3/3 speed） | 0 | `PASS` | V3 二 |
| `rl_repeats` / `loop` | `randomizer.py:241` / `:526` | `filters.py:366-372` | `split=n + concat=n+2` | +(rep−1)×seg_len | `\|Δv−Δa\`=0.00000（9/9） | 0 | `PASS` | V3 二 |
| `rl_seg_len` 时间空间 | `randomizer.py:518-525` | `_graph.py:200-202` | `trim=start=t1:end=t2` | 输入时长 | 按**输出**栅格量化后当输入用 | 残差 0.007018s | **`BUG`(B2)** | V3 二 |
| `rl_pos_rel` | `randomizer.py:524` | `filters.py:349-350` | `t1=pos_rel×seg_dur` | 段内相对位置 | 未单独实测 | — | `EVIDENCE_INSUFFICIENT` | — |
| `frame_drop_on` | `randomizer.py:245-250` | `_graph.py:139` | `select='not(eq(n,..))'` | 启用抽帧 | 8 素材：计划 == 实删（3~6 处） | 0 | `PASS`（M1 修复 B1/B11 后） | M1 C2 |
| frame_drop 帧空间 | `_graph.py:70-73,142` | — | `select` 帧号 | 应为 branch 帧空间 | 修复前 60→30 计划 14 实删 7；修复后 6/6 素材计划==实删、越界 0 | 0 | `PASS`（B1 已修复） | M1 C1/C2/C3 |
| frame_drop 窗口换算 | `_graph.py:71-73` | — | — | `out_t×speed×src_fps` | 反算落窗 3/3 speed | 0 | `PASS` | V3 三 |
| `frame_dup` | `randomizer.py:223-224` | `_graph.py:272-278` | `tpad=start=n:start_mode=clone` | +n 帧 | 理论+3 == 实测+3（6/6） | 0 | `PASS` | V3 四 |
| `frame_dup_pos` | `randomizer.py:225` | `filters.py:451-452` | `trim=end=t_pos` | 输出时间轴位置 | 克隆帧号偏差 ≤1 帧（6/6） | ≤1 帧 | `PASS` | V3 四 |
| 段内 concat（frame_dup） | `filters.py:458` | — | `concat=n=2:v=1:a=0` | 帧数守恒 | 180+3=183 | 0 | `PASS` | V3 七 |
| 最终 concat 推进规则 | `segment.py:180-182` | — | `concat=n=6:v=1:a=1` | 按 max(v,a) | a>v(+0.5s) → 补 15 帧 | — | `PASS`（机制确认） | V3 七 |
| concat 补帧不变量 | `filters.py:543-551` | — | `atrim+apad` | 补帧 = 0 | 修复后 8 素材补帧 0（修复前 60fps 源 2~4 帧） | 0 | `PASS`（B1/B11 修复后） | M1 C5 |
| `exp_dur` 公式 | `_graph.py:106-160` | `segment.py:156` | — | == 实际 | 降帧素材 0.0024%；同帧率素材 0.117%（=1 帧容器口径） | ≤1 帧 | `PASS`（B1/B11 修复后） | M1 C6 |
| PTS 重排 | `filters.py:361-364` | — | `setpts=PTS-STARTPTS` | 各片归零 | 未单独实测 PTS 序列 | — | `EVIDENCE_INSUFFICIENT` | — |

## B. 画面几何类

| 参数 | 生成位置 | FFmpeg 位置 | 预期效果 | 状态 | 证据 |
|---|---|---|---|---|---|
| `scale` | `randomizer.py:96-98` | `filters.py:68-77` `scale+crop` / `scale+pad` | >1 推近裁剪，<1 拉远加黑边 | `EVIDENCE_INSUFFICIENT` | 未做画面统计 |
| `asym_crop_l/r/t/b` | `randomizer.py:157-170` | `filters.py:56-60` `scale=increase,crop` | 构图偏移 | `EVIDENCE_INSUFFICIENT` | 未做有效画面区域检测 |
| `asym_crop` 仅上下非零分支 | — | `filters.py:56-57` | 合法表达式 | **`BUG`(B6 新)** | SRC：`else "(iw-{tw})/2"` 是**非 f-string**，`{tw}` 不会展开 |
| `crop_rect`（黑边检测） | `ffmpeg_runner.detect_black_crop` | `filters.py:36` `crop=cw:ch:cx:cy` | 去黑边 | `EVIDENCE_INSUFFICIENT` | 未测 |
| `normalize.width/height` | `config.json:13-14` | `filters.py:42-43` | 输出 810×1080 | `EVIDENCE_INSUFFICIENT` | 未 ffprobe 校验尺寸 |
| `normalize.aspect_ratio` | `config.json:12` | `normalize.get_target_spec` | 决定 w/h | `EVIDENCE_INSUFFICIENT` | 未测 |
| `normalize.fps` | `config.json:15` | `_graph.py:254` `fps={nf}` + `segment.py:119` | CFR 归一化 | `PASS`（B1 修复后） | M1：6 档源帧率 C1 全 PASS |
| `rotate_drift_amp` | `randomizer.py:125-133` | `filters.py:223,227` `rotate=` | 正弦微旋 | `EVIDENCE_INSUFFICIENT` | 未做角度/边界检测 |
| `rotate_drift_period` | `randomizer.py:134-141` | `filters.py:223` | 正弦周期 | `EVIDENCE_INSUFFICIENT` | — |
| `rotate_drift_speed` | `randomizer.py:144-152` | `filters.py:215,224` 慢分量振幅 | 单向漂移 | `EVIDENCE_INSUFFICIENT` | — |
| `rotate_drift_phase` | `randomizer.py:154` | `filters.py:223,216` | 初相位 | `EVIDENCE_INSUFFICIENT` | — |
| `plan.rotate` 窗口 | `randomizer.py:485-487` | `_graph.py:240-248` `enable='between(t,..)'` | 窗口内生效 | `PASS`（时长中性） | V3 十：不改帧数 |
| `zoom_drift_amp` | `randomizer.py:103-120` | `filters.py:186` `zoompan=z=` | 推镜 | `EVIDENCE_INSUFFICIENT` | 本轮全部实测在 `zoom.on=False` 下 |
| `zoom_drift_period` | `randomizer.py:115` | `filters.py:139-144` | 推镜周期 | `EVIDENCE_INSUFFICIENT` | — |
| `zoom_drift_dir` | `randomizer.py:116` | `filters.py:142-144` | in/out | `EVIDENCE_INSUFFICIENT` | — |
| `plan.zoom` 窗口切段 | `randomizer.py:497-500` | `filters.py:158-196` | 帧数守恒 | `PASS`（B10 修复后） | M1：`[zout]`==`[vb]` 117 帧守恒；修复后段内 \|a−v\| 0.033334 |
| `lens_k1/k2/cx/cy` | `randomizer.py:172-186` | `filters.py:266-267` `lenscorrection` | 几何畸变 | `EVIDENCE_INSUFFICIENT` | 未做几何检测 |
| `lens_events` 时间轴（分段路径） | `segment.py:123` | `filters.py:253` `between(t,..)` | 输入时间轴 | `PASS` | SRC：与 lens 位置同空间 |
| `lens_events` 时间轴（整文件路径） | `video_processor.py:95` | 同上 | 输入时间轴 | **`BUG`(B3)** | SRC：传 `seg_dur/speed`（输出空间） |
| `mask_drift_*` | `randomizer.py:188-205` | `filters.py:296-325` `geq` | 局部位移 | `INEFFECTIVE`（配置关闭） | `config.json:110-114` `enable=false` |
| `setsar=1` | — | `_graph.py:254` | SAR=1 | `EVIDENCE_INSUFFICIENT` | 未 ffprobe 校验 SAR |

## C. 画面颜色/视觉类

| 参数 | 生成位置 | FFmpeg 位置 | 预期效果 | 状态 | 证据 |
|---|---|---|---|---|---|
| `brightness` | `randomizer.py:208-211` | `filters.py:97` `eq=brightness=b/100` | 平均亮度上升 | `EVIDENCE_INSUFFICIENT` | 未做 luminance 统计 |
| `contrast` | 同上 | `filters.py:99` `eq=contrast=1+c/100` | 亮度方差上升 | `EVIDENCE_INSUFFICIENT` | — |
| `saturation` | 同上 | `filters.py:101` `eq=saturation=1+s/100` | HSV 饱和度上升 | `EVIDENCE_INSUFFICIENT` | — |
| `hue` | `randomizer.py:212-213` | `filters.py:107` `hue=h=` | 色相直方图偏移 | `EVIDENCE_INSUFFICIENT` | — |
| `channel_mix` | `randomizer.py:214-215` | `filters.py:113-121` `colorchannelmixer` | 通道微偏 | `INEFFECTIVE`（配置关闭） | `config.json:107-109` `enable=false` |
| `noise` | `randomizer.py:216-217` | `filters.py:125-127` `noise=alls=` | 噪点 | `INEFFECTIVE`（配置关闭） | `config.json:102-106` `enable=false` |
| sharpness / blur / gamma / exposure / color temperature / curves | — | — | — | **不存在** | 全仓 grep 无 `unsharp`/`gblur`/`gamma=`/`curves`/`colortemperature` |

## D. 音频类

| 参数 | 生成位置 | FFmpeg 位置 | 预期效果 | 实测 | 状态 | 证据 |
|---|---|---|---|---|---|---|
| `audio_atempo` | `randomizer.py:254-256` | `filters.py:483` `atempo=` | duration ×(1/atempo) | 单独 0.98269 → 5.099625 | `PASS` | V3 八 |
| `audio_pitch` | `randomizer.py:257-258` | `filters.py:495-503` | 变调、时长不变 | 48000：误差 0.1750% | `PASS` | V3 八 |
| `asetrate` | `filters.py:495` | `asetrate=int(sr×rate)` | 时长 ×(sr/asetrate) | 51320 → 4.689010（理论 4.689） | `PASS` | V3 八 |
| `aresample` | `filters.py:496` | `aresample=sr` | 采样率回归，**不恢复时长** | 4.689010→4.689021 | `PASS` | V3 八 |
| 变调补偿 `atempo=1/rate` | `filters.py:503` | — | 恢复原时长 | 5.009896 vs 基准 5.013333 | `PASS` | V3 八 |
| `sample_rate` 硬编码回退 44100 | `filters.py:490-492` | — | 不应发生 | 回退时 **+9.0345%** 膨胀 | **`BUG`(B5)** | V3 八 |
| `audio_eq_bands` | `randomizer.py:259-263` | `filters.py:517-519` `equalizer=` | 频段增益 | 未做频谱实测 | `EVIDENCE_INSUFFICIENT` | — |
| `audio_highpass` | `randomizer.py:264,266` | `filters.py:523` `highpass=f=` | 低频衰减 | 未做频谱实测 | `EVIDENCE_INSUFFICIENT` | — |
| `audio_lowpass` | `randomizer.py:265,267` | `filters.py:526` `lowpass=f=` | 高频衰减 | 未做频谱实测 | `EVIDENCE_INSUFFICIENT` | — |
| `audio_fade` | `randomizer.py:276` | `filters.py:530` `afade=t=in:d=` | 淡入 | 未做包络实测 | `EVIDENCE_INSUFFICIENT` | — |
| `audio_fade` 淡出 | — | — | docstring 承诺"淡入淡出" | 全仓无 `afade=t=out` | **`BUG`(B7 新，语义缺失)** | SRC grep |
| `audio_fade` 独立性 | `randomizer.py:276` | — | 独立参数 | `= min(0.5, max(0.1, trim_head))` 派生自 trim | `SRC` 记录 | SRC |
| `audio_noise_db` | `randomizer.py:274` | `audio_processor.py:47-48` `anoisesrc+amix` | 粉噪混音 | 仅 `aggressive` 档，当前预设不触发 | `INEFFECTIVE`（当前预设） | SRC |
| 音频 win_len 中性化 | `filters.py:543-551` | `atrim=end+apad=whole_dur` | 段音频 == 段视频 | 6/6 段 \|a−v\|=0.00000 | `PASS` | V2/V3 |
| audio sample rate 输出 | — | 未显式设 `-ar` | 跟随输入 | 48000/44100 两种素材各 6 处变调链 `aresample` 全部 == 输入采样率 | `PASS` | M1 C7 |
| audio channel/layout | — | 未显式设 `-ac`/`channelmap` | 跟随输入 | 全仓无声道操作；立体声左右独立性未测 | `EVIDENCE_INSUFFICIENT` | — |
| AAC 码率随机 | `audio_processor.py:57-63` | `-b:a {br}k` | 压缩域扰动 | 未校验输出码率 | `EVIDENCE_INSUFFICIENT` | — |

## E. 编码类

| 参数 | 生成位置 | FFmpeg 位置 | 状态 | 证据 |
|---|---|---|---|---|
| `crf` | `randomizer.py:280-281` | `filters.py:586,629` `-crf`/`-qp`/`-global_quality` | `EVIDENCE_INSUFFICIENT` | 未校验输出 QP |
| `crf` 下限钳制 24 | `filters.py:586,629` | — | `SRC` | 预设 min=19 会被静默提到 24 |
| `gop` | `randomizer.py:282-283` | `-g` | `EVIDENCE_INSUFFICIENT` | 未校验关键帧间隔 |
| `bframes` | `randomizer.py:284` | `-bf` | `EVIDENCE_INSUFFICIENT` | 未校验 B 帧数 |
| `sc_threshold` | `randomizer.py:285` | `-sc_threshold`（仅 libx264 分支） | `EVIDENCE_INSUFFICIENT` | `spec_encode_args` 未传该参数 |
| `normalize.video_codec` | `config.json:17` | `filters.py:636-643` ENCODER_TABLE | `EVIDENCE_INSUFFICIENT` | 当前 `h264_nvenc` |
| `normalize.audio_codec` | `config.json:18` | `-c:a` | `EVIDENCE_INSUFFICIENT` | — |
| `normalize.pix_fmt` | `config.json:16` | `_graph.py:254` `format=` + `-pix_fmt` | `EVIDENCE_INSUFFICIENT` | 未 ffprobe 校验 |
| `normalize.bitrate_kbps` / `target_kbps` | `config.json:19` | `-b:v`/`-rc cbr` | `EVIDENCE_INSUFFICIENT` | — |
| `encode.nvenc_preset` / `cpu_preset` | `config.json:23-24` | `-preset` | `EVIDENCE_INSUFFICIENT` | — |
| `encode.gpu_auto` | `config.json:22` | 决定 `use_nvenc` | `EVIDENCE_INSUFFICIENT` | — |

## F. 随机化类

| 参数 | 位置 | 预期 | 状态 | 证据 |
|---|---|---|---|---|
| base `seed` | `randomizer.py:79-81` | 决定整条参数链 | `PASS` | D1：同 seed 两次 filtergraph 字节一致（6801B），快照除 `ts` 外一致；D2：5 个 seed → 5 个不同图 |
| child seed（分段快照） | `segment.py:80-84` | `base + (seg_idx+1)*7919` | `PASS`（修复 B12 后） | 修复前 `seg_idx*7919` → `child_0 == base`，与 plan_0 撞车；现 21 个派生 seed 零碰撞 |
| plan seed | `randomizer.py:436-439` | `base + (seg_idx+1)*104729` | `PASS`（修复 B12 后） | 同上，见 `tests/evidence/determinism/seed/result.json` |
| lens 事件 seed | `randomizer.py:397` | `base + 53` | `PASS` | D3 无碰撞 |
| frame_drop rng seed | `_graph.py`（`frame_drop_plan` 内） | `base + 29 + seg_idx` | `PASS` | D3 无碰撞 |
| 音频码率 seed | `audio_processor.py:57` | `base + 17` | `PASS` | D3 无碰撞 |
| 预设随机区间 | `custom.json` | 取值必须落区间内 | `EVIDENCE_INSUFFICIENT` | 未做批量区间校验 |
| `GLOBAL_PARAM_KEYS` 继承 | `randomizer.py:21-26` | 段间全片级参数一致 | `EVIDENCE_INSUFFICIENT` | 未验证 |

## G. 其他参与 filtergraph / 输出的参数

| 项 | 位置 | 状态 | 备注 |
|---|---|---|---|
| `switches.normalize` | `config.json:8` | `EVIDENCE_INSUFFICIENT` | 关闭时走 `else` 分支（源分辨率） |
| `switches.quality_check` | `config.json:9` | `EVIDENCE_INSUFFICIENT` | — |
| `quality_check` 容差 5% | `quality_check.py` | `PASS`（阈值已收紧） | V2 |
| `fingerprint.*` | `config.json:34-39` | `EVIDENCE_INSUFFICIENT` | 相似度重试逻辑未测 |
| `video.black_crop` | `config.json:98-101` | `EVIDENCE_INSUFFICIENT` | — |
| `version_count` | `config.json:2` | `EVIDENCE_INSUFFICIENT` | — |
| `performance.*` | `config.json:26-33` | 不影响输出内容 | 不在取证范围 |
| `runtime.ffmpeg/ffprobe` | `config.json:116-119` | `PASS` | 全部实测都用它 |
| `_fps`（注入 snapshot） | `_graph.py:274-275` | `INEFFECTIVE` | `filters.py:450` 读取后从未使用 → 死代码 |
| `REWASH_DUMP_CMD` | `ffmpeg_runner.py` | `PASS` | V2 测试 11 |
| 降级路径 `process_clip` | `video_processor.py` | `EVIDENCE_INSUFFICIENT` | V3 C2/E3 |
| `_merge_reencode` | `segment.py:384-455` | `EVIDENCE_INSUFFICIENT` | V3 C3 |

---

## 本阶段新增待确证条目（V3 之后新发现）

| ID | 位置 | 问题 | 触发条件 | 需要的实测 |
|---|---|---|---|---|
| B6 | `filters.py:56-57` | 非 f-string 字面量 `"(iw-{tw})/2"`，`{tw}` 不展开 → 非法 crop 表达式 | `has_asym` 为真但 `cl+cr ≤ 0.001`（只有上下非对称裁剪） | 构造该参数组合 → 运行 FFmpeg 看是否报错 |
| B7 | `filters.py:528-530` | 只有 `afade=t=in`，无 `afade=t=out`，与"淡入淡出"设计说明不符 | 恒定 | 音频尾部包络测量 |
| B8 | `filters.py:586,629` | `crf = max(24, ...)` 静默上钳 | 预设 `crf.min < 24`（当前 19） | 输出 QP 实测确认随机区间下半段失效 |
| B9 | `filters.py:620-703` | `spec_encode_args` 不传 `sc_threshold`，而 `get_encode_args` 传 | 用标准化页编码器（主路径） | 确认 `sc_threshold` 在主路径是否 INEFFECTIVE |

## 统计

### 计数规则（本次验收补充，消除历史口径歧义）

- 分母 = **状态分类行 97 条**。不计入：C 类末行「不存在的参数」汇总行、G 类 `performance.*`「不在取证范围」行、以及 2 条只作记录的 `SRC` 行（`audio_fade` 独立性、`crf` 下限钳制 B8）。
- B6/B7 已在 B/D 类表中占 `BUG` 行，**不再**与「新增待确证」表重复计数（第一版统计里的 `BUG 5+4=9` 存在此重复，本次修正）。
- B8 是 E 类的 `SRC` 记录行，B9 属 E 类 `sc_threshold` 的 `EVIDENCE_INSUFFICIENT` 行。

### 第一阶段基线（2026-08-23 枚举时）

- `PASS` 22 / `BUG` 7（B1–B7，去重后）/ `INEFFECTIVE` 5 / `PARTIAL` 4 / `EVIDENCE_INSUFFICIENT` 59 = 97

### 第三/四阶段（B1 B10 B11 B12 修复 + fps/sr/determinism 取证）后

- 总参数（状态分类行）：**97**
- `PASS`：**39**
- `BUG`：**6**（B2 B3 B4 B5 B6 B7，均未修复）
- `INEFFECTIVE`：**5**（`mask_drift`、`channel_mix`、`noise`、`audio_noise_db`、`_fps`）
- `PARTIAL`：**0**（原 4 条 `frame_drop_on`/`normalize.fps`/concat 补帧/`exp_dur` 已全部转 `PASS`）
- `REGRESSION`：**0**
- `EVIDENCE_INSUFFICIENT`：**47**

逐类分布（PASS / BUG / INEFFECTIVE / EI）：

- A 时间轴 25 行：19 / 2 / 0 / 4
- B 几何 21 行：4 / 2 / 1 / 14
- C 颜色 6 行：0 / 0 / 2 / 4
- D 音频 16 行：7 / 2 / 1 / 6
- E 编码 10 行：0 / 0 / 0 / 10
- F 随机 8 行：6 / 0 / 0 / 2
- G 其他 11 行：3 / 0 / 1 / 7

- 用户清单中在本仓库**不存在**的参数：`sharpness`、`blur`、`gamma`、`exposure`、`color temperature`、`grain`（独立于 `noise`）、`volume`、`aspect ratio`（仅作为 w/h 推导输入，无独立滤镜）

## 第二阶段计划（按证据缺口排序）

1. **FPS 矩阵端到端**（25 / 29.97 / 30 / 50 / 60 / 120 → 30）× `frame_drop`/`frame_dup`/`speed`/`trim`/`segment`/`reverse_loop` —— 覆盖 B1，是唯一 P0
2. **视觉参数统计化取证**（`signalstats`/`entropy` + 尺寸/SAR ffprobe）—— 覆盖 B 类与 C 类 21 条 `EVIDENCE_INSUFFICIENT`
3. **音频频谱取证**（EQ/HP/LP `astats`+`showspectrum`，立体声左右异频素材）—— 覆盖 D 类 6 条
4. **seed 确定性与碰撞**（同 seed 两跑 filtergraph 逐字比对；child/plan/lens/drop/bitrate 五套 seed 交叉碰撞检查）—— 覆盖 F 类 8 条
5. **编码参数取证**（关键帧间隔、B 帧数、QP、pix_fmt、SAR）—— 覆盖 E 类 11 条
6. **路径矩阵**（single-pass / segmented / fallback / merge-reencode）—— 覆盖 C2/C3/E3
7. **B6–B9 定向确证**
8. 建立 `tests/evidence/<test_id>/` 自动取证落盘 + `run_ffmpeg` 全量记录

**本阶段未修改任何产品代码。**

## 状态刷新（第三/四阶段完成后，2026-08-23）

以下条目已取得实测证据并转判，明细见 `PARAMETER_CALIBRATION_REPORT_V4.md`：

- `EVIDENCE_INSUFFICIENT → PASS`：branch 帧率、`frame_drop` 计划/实删/帧号、`trim` 帧数量化、`zoom` 窗口帧数与 PTS、段内 A-V 不变量、`concat` 补帧、`exp_dur`、采样率收敛（48000 与 44100）、F 类 6 项 seed
- `BUG → 已修复并复测`：B1（P0 FPS 域错位）、B10（P1 `zoompan=fps=`）、B11（P1 降帧后 trim 量化，本轮新发现）、B12（P2 派生 seed 撞车，本轮新发现）
- 仍为 `EVIDENCE_INSUFFICIENT`：视觉图像统计（E2）、路径矩阵 segmented/fallback/merge-reencode（E3）、编码参数（E4）、音频频谱（E5）、`run_ffmpeg` 起止时间（E6）、`timeline_metrics.json`（E7）、`av_offset` 端到端负向（E1）
- `INEFFECTIVE` 5 项与「本仓库不存在的参数」清单本轮无变化
- 复测入口：`param_forensics.py` 的 `fps_matrix` / `sr_matrix` / `determinism` 三个任务 + `test_timeline_integrity.py`（11/11）

## 状态刷新（V5 阶段完成后，2026-08-23，历史小节不改写）

本节为**当前权威计数**，取代上方「第三/四阶段」数字；分母仍为 97 条状态分类行。明细见 `PARAMETER_CALIBRATION_REPORT_V5.md`。

- `PASS`：**86**
- `BUG`：**0**
- `INEFFECTIVE`：**6**
- `PARTIAL`：**0**
- `REGRESSION`：**0**
- `EVIDENCE_INSUFFICIENT`：**5**

逐类（PASS / BUG / INEFFECTIVE / EI）：

- A 时间轴 25 行：25 / 0 / 0 / 0
- B 几何 21 行：19 / 0 / 1 / 1
- C 颜色 6 行：4 / 0 / 2 / 0
- D 音频 16 行：14 / 0 / 1 / 1
- E 编码 10 行：8 / 0 / 1 / 1
- F 随机 8 行：8 / 0 / 0 / 0
- G 其他 11 行：8 / 0 / 1 / 2

转判说明：

- `EVIDENCE_INSUFFICIENT → PASS`：视觉 16 个参数（像素/几何统计，噪声地板实测 0.000000）、编码 10 项（关键帧间隔/B 帧/CRF/pix_fmt/SAR/帧率/目标码率/音频编码/preset/`sc_threshold`）、音频 10 项（采样率/声道独立/变调/atempo/HP/LP/EQ/淡入/淡出/段边界）、`av_offset` 正负 9 档端到端、降级路径与 `_merge_reencode`（F1–F5 / M1–M8）、`timeline_metrics.json` 台账、`run_ffmpeg` 起止时间、rl 与 frame_drop 同段共触发
- `BUG → 已修复并复测`：B2 B3 B5 B9（V4 遗留）+ B15 B16 B17 B18 B19 B20（V5 新发现）
- 仍为 `INEFFECTIVE`（6）：`mask_drift` / `channel_mix` / `noise`（配置默认关，开启后实测生效）、`audio_noise_db`、`_fps` 冗余字段、`crf<24` 钳制（B8，设计取舍）、`frame_drop_on` 死参数 —— 其中前三项按「默认配置下无效」计一行、`frame_drop_on` 与 B8 各占一行
- 仍为 `EVIDENCE_INSUFFICIENT`（5）：`effective_duration` 的 `max(2.0)` 短素材边界、`rl_pos_rel` 分布、`switches.*`/`fingerprint.*` 全组合、`version_count` 多版本、AAC 随机码率区间分布
- 复测入口新增：`param_forensics_v5.py visual|encode|audio|av_offset|fallback`

## 状态刷新（V6 无人值守阶段完成后）

参数级计数**沿用上一节**（PASS 86 / BUG 0 / INEFFECTIVE 6 / EVIDENCE_INSUFFICIENT 5 = 97）：
本阶段未新增参数条目，新增的是**组合 / 边界 / 顺序 / 坐标空间**维度的证据，
以及由此暴露的 4 个产品 Bug（已全部修复并复验）。

- 新增常驻取证入口：`param_forensics_v6.py combo|boundary|dead|order|dist|switches|zoomacc|wincoord`
- 一键完整回归：`python tests/unattended.py`（15 套件；加 `quick` 只跑快套件）
- 本阶段 Bug：B22 B23 B24（推镜窗口帧数，同根因）、B25（微旋窗口坐标）
  → 全部 FIXED；测试基础设施 B21 B34 亦 FIXED
- 受影响参数的判据修订：
  - `zoom_drift_amp`：由「视觉生效即 PASS」细化为「**且**不得改变段帧数/时长」
    —— 推镜窗口现位于变速之前，栅格 = 源帧率（36 条证据）
  - `rotate_drift_*`：窗口位置判据补充「filtergraph 的 `between(t,…)`
    必须等于 plan 坐标原值」（`wincoord` W3 常驻断言）
  - `speed`：新增与推镜/微旋组合时的时间轴守恒证据（8 条）
- 仍为 `INEFFECTIVE`（6）/ `EVIDENCE_INSUFFICIENT`（5）的项与上一节一致，未变。
- 连续两轮完整回归干净：`R-000003`、`R-000004`（各 15 套件全 PASS）。
- 权威机器可读状态：`tests/evidence/index.json`、`tests/evidence/bug_queue.json`；
  可读索引 `tests/evidence/EVIDENCE_INDEX.md`、`tests/evidence/BUG_QUEUE.md`、
  `PARAMETER_MASTER_AUDIT.md`；结论见 `PARAMETER_CALIBRATION_REPORT_V6.md`
  与 `PARAMETER_BUG_REPORT_V5.md`。

