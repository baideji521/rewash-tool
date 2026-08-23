# PARAMETER_CALIBRATION_REPORT_V2

时间轴语义修复 —— 实测证据报告

- 基线版本：`08062c628b025dc847fe971933815da3a6fca277`（main，2026-08-23 18:29:34）
- 修复后版本：当前工作区（未 commit、未 push）
- 报告日期：2026-08-23
- 所有数字均来自**真实执行 FFmpeg / ffprobe 的测量值**。无法实测处标注【证据不足】。

## 0. 实验条件（A/B 对照的唯一变量 = 代码树）

| 项 | 值 |
|---|---|
| 素材 | lavfi 合成 `testsrc2=720x1280:rate=2997/100:duration=21.108` + `sine=440:sample_rate=48000` |
| 素材属性（ffprobe） | duration=21.108s，fps=29.97，a_sample_rate=48000 |
| seed | `1786869947670` |
| preset | `config.json.last_used_preset`（两侧读同一份文件） |
| config | 仓库 `config.json`（本次未修改，两侧完全相同） |
| 分段数 | 6 |
| 归一化目标 | 810×1080 @ 30fps，yuv420p |
| before 代码树 | `git archive 08062c6` 解包到 `%TEMP%\rewash_old_08062c6` |
| after 代码树 | 当前工作区 |

快照参数（两侧完全一致，证明随机器未被改动）：

```
speed=0.9805  frame_dup=2  frame_dup_pos=0.355  av_offset=0.105
trim_head=0.964  trim_tail=0.691  audio_pitch=1.158  audio_atempo=0.98269
```

取证方式：monkeypatch `segment.run_ffmpeg` 使其返回 `FFResult(-15,"","stopped")`，
捕获真实构建出的命令而不执行；随后把捕获到的命令原样交给 FFmpeg 执行并 ffprobe。

---

## A. 修复前的 filtergraph（08062c6，实际捕获）

全局预处理层（`[gbase]`，两版本相同）：

```
[0:v]scale=810:1080:force_original_aspect_ratio=increase:flags=bicubic,
crop=810:1080:(0.0369/(0.0369+0.0334+0.001))*(iw-810):(0.0378/(0.0378+0.0411+0.001))*(ih-1080),
scale=824:1098:flags=bicubic,crop=810:1080,
eq=brightness=-0.1535:contrast=1.0628:saturation=1.0521,hue=h=-4.50,
lenscorrection=cx=0.512:cy=0.523:k1=-0.00286:k2=0.00561:enable='between(t,0.000,21.008)+between(t,10.554,21.008)'[gbase]
[gbase]split=6[g0][g1][g2][g3][g4][g5]
```

第 0 段完整支路（**修复前**）：

```
[g0]trim=start=0.859:end=4.101,setpts=PTS-STARTPTS[vt0]
[vt0]select='not(eq(n,32))',setpts=N/FRAME_RATE/TB,setpts=1.019888*PTS[vb_0]
[vb_0]rotate=(...):fillcolor=none:enable='between(t,0.111,3.076)',setpts=N/30/TB,fps=30,format=yuv420p,setsar=1[vm_0]
[vm_0]split=2[d1_0][d2_0]
[d1_0]trim=end=1.174,setpts=PTS-STARTPTS[fv1_0]
[d2_0]trim=start=1.174,setpts=PTS-STARTPTS,tpad=start=2:start_mode=clone[fv2_0]
[fv1_0][fv2_0]concat=n=2:v=1:a=0[v_0]
[0:a]atrim=start=0.859:end=4.101[at_in0]
[at_in0]asetpts=PTS-STARTPTS[ab_0]
[ab_0]atempo=0.98269,asetrate=47150,aresample=44100,atempo=0.935299,
  equalizer=frequency=180:...,highpass=f=44:poles=2,lowpass=f=17142:poles=2,
  afade=t=in:d=0.50,adelay=105:all=1[af_0]
...
[v_0][af_0][v_1][af_1]...[v_5][af_5]concat=n=6:v=1:a=1[vout]
```

图上可直接看到的三处语义错误：

1. `setpts=N/30/TB` 位于 `setpts=1.019888*PTS` 之后 → 按帧序号重写 PTS，变速结果被整体丢弃。
2. `aresample=44100`，而输入采样率是 **48000** → 残留 48000/44100 = 1.088435 的时长膨胀。
3. 音频链尾没有任何时长中性化，`adelay=105` 直接把该段音频拉长 0.105s。

## B. 修复后的 filtergraph（当前工作区，实际捕获）

全局预处理层与 A 完全相同（未改动）。第 0 段完整支路（**修复后**）：

```
[g0]trim=start=0.964:end=4.206,setpts=PTS-STARTPTS[vt0]
[vt0]select='not(eq(n,32))',setpts=N/FRAME_RATE/TB,setpts=1.019888*PTS[vb_0]
[vb_0]rotate=(...):fillcolor=none:enable='between(t,0.111,3.076)',fps=30,format=yuv420p,setsar=1[vm_0]
[vm_0]split=2[d1_0][d2_0]
[d1_0]trim=end=1.171,setpts=PTS-STARTPTS[fv1_0]
[d2_0]trim=start=1.171,setpts=PTS-STARTPTS,tpad=start=2:start_mode=clone[fv2_0]
[fv1_0][fv2_0]concat=n=2:v=1:a=0[v_0]
[0:a]atrim=start=0.964:end=4.206[at_in0]
[at_in0]asetpts=PTS-STARTPTS[ab_0]
[ab_0]atempo=0.98269,asetrate=51320,aresample=48000,atempo=0.935299,
  equalizer=frequency=180:...,highpass=f=44:poles=2,lowpass=f=17142:poles=2,
  afade=t=in:d=0.50,adelay=105:all=1,
  atrim=end=3.366667,apad=whole_dur=3.366667,asetpts=PTS-STARTPTS[af_0]
...
[v_0][af_0][v_1][af_1]...[v_5][af_5]concat=n=6:v=1:a=1[vout]
```

图级差异（正则实测，非人工比对）：

| 特征 | before | after |
|---|---|---|
| 含 `setpts=N/{fps}/TB` | True | **False** |
| 含 `aresample=44100` | True | **False** |
| `asetrate` 值 | 47150（= 44100×1.069） | **51320**（= 48000×1.069） |
| 段尾时长中性化 `apad=whole_dur` | 无 | **每段都有** |
| 标准化尾 | `setpts=N/30/TB,fps=30,format,setsar` | `fps=30,format,setsar` |

变调仍然生效：51320/48000 = 1.069167，理论 2^(1.158/12) = 1.069176，偏差 9e-6。

## C. 每段各层的输入/输出时间（从实际 filtergraph 抽取）

**修复前**（窗口不连续 —— av_offset 平移窗口造成）：

| seg | 视频窗口 | 音频窗口 | 与上一段的缝隙 | setpts 倍率 | 删帧帧号 | tpad 克隆 | 音频偏移 | 中性化 |
|---|---|---|---|---|---|---|---|---|
| 0 | 0.859→4.101 | 0.859→4.101 | — | 1.019888 | 32 | 2 | adelay=105ms | 无 |
| 1 | 4.325→7.567 | 4.325→7.567 | **+0.224 跳过** | 1.002104 | 78 | 2 | atrim=0.119 | 无 |
| 2 | 7.327→10.570 | 7.327→10.570 | **−0.240 重复** | 0.965065 | 60 | 2 | adelay=121ms | 无 |
| 3 | 10.813→14.055 | 10.813→14.055 | **+0.243 跳过** | 1.007963 | 无 | 2 | atrim=0.122 | 无 |
| 4 | 14.082→17.324 | 14.082→17.324 | **+0.027 跳过** | 0.995421 | 无 | 2 | atrim=0.149 | 无 |
| 5 | 17.286→20.528 | 17.286→20.528 | **−0.038 重复** | 0.983671 | 无 | 2 | atrim=0.111 | 无 |

**修复后**（严格连续，首尾与 `effective_duration` 同源）：

| seg | 视频窗口 | 音频窗口 | 与上一段的缝隙 | setpts 倍率 | 删帧帧号 | tpad 克隆 | 音频偏移 | 中性化 `whole_dur` |
|---|---|---|---|---|---|---|---|---|
| 0 | 0.964→4.206 | 0.964→4.206 | — | 1.019888 | 32 | 2 | adelay=105ms | 3.366667 |
| 1 | 4.206→7.448 | 4.206→7.448 | 0.000 | 1.002104 | 78 | 1 | atrim=0.119 | 3.233333 |
| 2 | 7.448→10.691 | 7.448→10.691 | 0.000 | 0.965065 | 70 | 3 | adelay=121ms | 3.200000 |
| 3 | 10.691→13.933 | 10.691→13.933 | 0.000 | 1.007963 | 无 | 2 | atrim=0.122 | 3.333333 |
| 4 | 13.933→17.175 | 13.933→17.175 | 0.000 | 0.995421 | 无 | 1 | atrim=0.149 | 3.266667 |
| 5 | 17.175→20.417 | 17.175→20.417 | 0.000 | 0.983671 | 无 | 1 | atrim=0.111 | 3.233333 |

窗口口径自校验：`21.108 − trim_head 0.964 − trim_tail 0.691 = 19.453`，
而 `20.417 − 0.964 = 19.453`，与 `segment.effective_duration()` 完全一致。

## D. 每段视频 / 音频实测时长

**修复前**：

| seg | 视频帧 | 视频秒 | 音频秒 | \|a−v\| |
|---|---|---|---|---|
| 0 | 98 | 3.266667 | 3.689909 | **0.42324** |
| 1 | 97 | 3.233333 | 3.401202 | **0.16787** |
| 2 | 99 | 3.300000 | 3.505556 | **0.20556** |
| 3 | 99 | 3.300000 | 3.450204 | **0.15020** |
| 4 | 98 | 3.266667 | 3.394785 | **0.12812** |
| 5 | 98 | 3.266667 | 3.338594 | **0.07193** |

worst \|a−v\| = 0.42324s，阈值 1/30 = 0.03333s → **违反不变量**（每段音频都比视频长）。

**修复后**：

| seg | 视频帧 | 视频秒 | 音频秒 | \|a−v\| |
|---|---|---|---|---|
| 0 | 101 | 3.366667 | 3.366667 | 0.00000 |
| 1 | 97 | 3.233333 | 3.233333 | 0.00000 |
| 2 | 96 | 3.200000 | 3.200000 | 0.00000 |
| 3 | 100 | 3.333333 | 3.333333 | 0.00000 |
| 4 | 98 | 3.266667 | 3.266667 | 0.00000 |
| 5 | 97 | 3.233333 | 3.233333 | 0.00000 |

worst \|a−v\| = **0.00000s** → 满足 `|a_i − v_i| ≤ 1/out_fps`。

## E. frame_drop 实测

本次运行：seg0/1/2 各删 1 帧，seg3/4/5 未触发，共 3 帧。

修复前后删帧帧号不同（before seg2=60，after seg2=70），因为修复了两处换算：

- 帧率：原用 `eff_fps=30`（归一化目标帧率），此处流还没降帧，必须用 `src_fps=29.97`；
- 空间：`generate_segment_plan` 给出的窗口是**输出时间轴**，而 `select` 在变速之前按**输入帧号**
  计数，必须 `input_frame = output_time × speed × src_fps`。

矩阵实测（`test_frame_drop_window`）：

| src_fps | speed | n_in | 输入空间窗口帧号 | 删帧帧号 | 上限 | 落窗 |
|---|---|---|---|---|---|---|
| 25.00 | 0.90 | 150 | [22.5, 90.0] | 38, 62, 81 | 3 | OK |
| 25.00 | 1.00 | 150 | [25.0, 100.0] | 38, 62, 81 | 3 | OK |
| 25.00 | 1.10 | 150 | [27.5, 110.0] | 38, 62, 81 | 3 | OK |
| 29.97 | 0.90 | 180 | [27.0, 107.9] | 38, 62, 81 | 3 | OK |
| 29.97 | 1.00 | 180 | [30.0, 119.9] | 38, 62, 81 | 3 | OK |
| 29.97 | 1.10 | 180 | [33.0, 131.9] | 38, 62, 81 | 3 | OK |
| 60.00 | 0.90 | 360 | [54.0, 216.0] | 62,81,96,112,129,153,165 | 7 | OK |
| 60.00 | 1.00 | 360 | [60.0, 240.0] | 62,81,96,112,129,153,165 | 7 | OK |
| 60.00 | 1.10 | 360 | [66.0, 264.0] | 81,96,112,129,153,165,190 | 7 | OK |

真实执行校验（30fps 素材，6s 窗口 = 180 帧，删 3 帧）：

| speed | 预测保留 | 实测保留 |
|---|---|---|
| 0.90 | 177 | 177 |
| 1.00 | 177 | 177 |
| 1.10 | 177 | 177 |

删帧量与 speed 无关（不被变速/重采样吞掉），预测与实测帧精确一致。

## F. frame_dup 实测

本次运行每段克隆帧数 2 / 1 / 3 / 2 / 1 / 1（由 `segment_video_metrics` 在**真实输出帧栅格**上算出，
落地为 `tpad=start=N:start_mode=clone`）。逐段 `frames_pre_dup + dup == frames_out`：

| seg | frames_pre_dup | dup | frames_out（预测） | frames_out（实测） |
|---|---|---|---|---|
| 0 | 99 | 2 | 101 | 101 |
| 1 | 96 | 1 | 97 | 97 |
| 2 | 93 | 3 | 96 | 96 |
| 3 | 98 | 2 | 100 | 100 |
| 4 | 97 | 1 | 98 | 98 |
| 5 | 96 | 1 | 97 | 97 |

克隆位置实测（`test_frame_dup_position`，`-f framemd5` 定位连续相同帧）：

| pos | speed | [vm] 帧数 | 期望克隆帧号 | 实测克隆帧号 | 重复帧数 | 总帧 |
|---|---|---|---|---|---|---|
| 0.25 | 1.00 | 180 | 45 | 45 | 3 | 183 |
| 0.25 | 0.95 | 189 | 47 | 47 | 3 | 192 |
| 0.25 | 1.05 | 171 | 43 | 43 | 3 | 174 |
| 0.50 | 1.00 | 180 | 90 | 90 | 3 | 183 |
| 0.50 | 0.95 | 189 | 94 | 95 | 3 | 192 |
| 0.50 | 1.05 | 171 | 86 | 86 | 3 | 174 |
| 0.75 | 1.00 | 180 | 135 | 135 | 3 | 183 |
| 0.75 | 0.95 | 189 | 142 | 142 | 3 | 192 |
| 0.75 | 1.05 | 171 | 128 | 128 | 3 | 174 |

最大偏差 1 帧（pos=0.50 / speed=0.95），在容差内。

## G. speed 实测时长（矩阵）

`test_speed_changes_duration`：speed ∈ {0.90, 0.97, 1.00, 1.04, 1.10} × frame_drop{ON,OFF}
× frame_dup{ON,OFF}，6s 输入窗口，30fps 素材，真实 `-f null -` 计帧。

| speed | drop | dup | 预测帧 | 实测帧 | 预测秒 | 实测秒 |
|---|---|---|---|---|---|---|
| 0.90 | ON | ON | 199 | 199 | 6.6333 | 6.6333 |
| 0.97 | ON | ON | 184 | 184 | 6.1333 | 6.1333 |
| 1.00 | ON | ON | 179 | 179 | 5.9667 | 5.9667 |
| 1.04 | ON | ON | 172 | 172 | 5.7333 | 5.7333 |
| 1.10 | ON | ON | 163 | 163 | 5.4333 | 5.4333 |
| 0.90 | ON | OFF | 197 | 197 | 6.5667 | 6.5667 |
| 0.97 | ON | OFF | 182 | 182 | 6.0667 | 6.0667 |
| 1.00 | ON | OFF | 177 | 177 | 5.9000 | 5.9000 |
| 1.04 | ON | OFF | 170 | 170 | 5.6667 | 5.6667 |
| 1.10 | ON | OFF | 161 | 161 | 5.3667 | 5.3667 |
| 0.90 | OFF | ON | 202 | 202 | 6.7333 | 6.7333 |
| 0.97 | OFF | ON | 188 | 188 | 6.2667 | 6.2667 |
| 1.00 | OFF | ON | 182 | 182 | 6.0667 | 6.0667 |
| 1.04 | OFF | ON | 175 | 175 | 5.8333 | 5.8333 |
| 1.10 | OFF | ON | 166 | 166 | 5.5333 | 5.5333 |
| 0.90 | OFF | OFF | 200 | 200 | 6.6667 | 6.6667 |
| 0.97 | OFF | OFF | 186 | 186 | 6.2000 | 6.2000 |
| 1.00 | OFF | OFF | 180 | 180 | 6.0000 | 6.0000 |
| 1.04 | OFF | OFF | 173 | 173 | 5.7667 | 5.7667 |
| 1.10 | OFF | OFF | 164 | 164 | 5.4667 | 5.4667 |

20 个组合全部 **预测 == 实测**（帧精确），单调性 4/4 成立（speed 越小输出越长）。

本次 A/B 运行中 speed 生效的直接证据：`[vb_0]` → `[vm_0]` 帧数变化
（seg0 speed=0.9805 → 97 帧变 99 帧；seg2 speed=1.0362 → 96 帧变 93 帧）。
修复前该处帧数不变，因为 `setpts=N/30/TB` 把变速抹掉了。

## H. reverse_loop 实测

本次 A/B 运行 6 段均未触发 reverse_loop（`config.video.reverse_loop.probability=0.4`，
该 seed 下 6 段全部未命中；filtergraph 中无 `asplit=3` / `areverse` 标签可证）。
因此单独构造测试（`test_reverse_loop_av_delta_equal`，rl 长度 = 4/30s，已量化到输出帧栅格）：

| mode | repeats | speed | Δvideo | 预测 Δ | Δaudio | 理论 (rep−1)·rl/speed | \|Δv−Δa\| | 量化残差 |
|---|---|---|---|---|---|---|---|---|
| reverse | 2 | 1.00 | +0.00000 | +0.00000 | +0.00000 | +0.00000 | 0.00000 | 0.00000 |
| reverse | 2 | 0.95 | +0.00000 | +0.00000 | +0.00000 | +0.00000 | 0.00000 | 0.00000 |
| reverse | 2 | 1.05 | +0.00000 | +0.00000 | +0.00000 | +0.00000 | 0.00000 | 0.00000 |
| loop | 2 | 1.00 | +0.13333 | +0.13333 | +0.13333 | +0.13333 | 0.00000 | 0.00000 |
| loop | 2 | 0.95 | +0.16667 | +0.16667 | +0.16667 | +0.14035 | 0.00000 | 0.02632 |
| loop | 2 | 1.05 | +0.13333 | +0.13333 | +0.13333 | +0.12698 | 0.00000 | 0.00635 |
| loop | 3 | 1.00 | +0.26667 | +0.26667 | +0.26667 | +0.26667 | 0.00000 | 0.00000 |
| loop | 3 | 0.95 | +0.30000 | +0.30000 | +0.30000 | +0.28070 | 0.00000 | 0.01930 |
| loop | 3 | 1.05 | +0.26667 | +0.26667 | +0.26667 | +0.25397 | 0.00000 | 0.01270 |

判定口径三条同时成立：预测帧精确、`|Δv−Δa| ≤ 1/out_fps`、与理论值的量化残差 `≤ 1/out_fps`。
量化残差不可消除 —— 输出必须是整数帧；`randomizer` 已把 `rl.seg_len` 量化到输出帧栅格
（`rl_frames/out_fps`），把残差压到 1 帧以内。`reverse` 模式增量恒为 0（不改长度）。

## I. av_offset 实测 A/V 偏移

`test_av_offset_duration_neutral`：素材前 1.0s 静音 + 之后 1kHz，`silencedetect` 定位音频起点。

| av_offset | 段音频长度 | 相对基准 Δ | 1kHz 起点 | 实测相对偏移 | 期望 |
|---|---|---|---|---|---|
| +0.00 | 3.000000 | +0.00000 | 1.0026 | +0.0000 | +0.00 |
| +0.10 | 3.000000 | +0.00000 | 1.1026 | +0.1000 | +0.10 |
| −0.10 | 3.000000 | +0.00000 | 0.9026 | −0.1000 | −0.10 |

偏移真实存在、方向正确（正值音频滞后）、**且段音频总长度完全不变**
（判定口径：长度变化 ≤ 1/30s，偏移误差 < 0.02s）。

修复前对比：`adelay=105` 使该段音频 +0.104989s，`atrim=start=0.119` 使该段音频 −0.119s，
并且窗口本身被平移（C 节 before 表），既改长度又破坏段连续性。

## J. concat 前后帧数

| | Σ 各段支路帧 | 最终输出帧 | concat 补帧 | 折合时长 |
|---|---|---|---|---|
| before | 589 | 621 | **+32** | +1.0667s |
| after | 589 | **589** | **0** | 0.0000s |

补帧机制（修复前）：concat 滤镜按每段 `max(v_dur, a_dur)` 推进下一段起点，
音频比视频长 → 视频侧留下空洞 → CFR 编码器复制帧填充。
D 节 before 表中前 5 段的音视频差
（0.42324 + 0.16787 + 0.20556 + 0.15020 + 0.12812 = 1.06499s ≈ 32/30 = 1.06667s）
与实测补帧数吻合（最后一段的 0.07193 不产生补帧，因为其后没有下一段）。

修复后逐段 `|a−v| = 0` → 补帧 0，且**最终帧数 == Σ 各段帧数**（差 0 帧，容差 1 帧）。

## K. exp_dur 预期时长

| | exp_dur | ffprobe format.duration | 误差 |
|---|---|---|---|
| before | 19.369067 | 20.780000 | **+7.2845%** |
| after | 19.633333 | 19.633333 | **0.0000%** |

修复后公式（`segment_video_metrics` → `exp_dur = Σ out_dur_i`）：

```
n_win_i    = ceil(t1_i·src_fps) − ceil(t0_i·src_fps)        # 帧精确 trim
n_in_i     = n_win_i + rl_extra_frames_i                    # loop 事件在输入空间的增量
n_kept_i   = n_in_i − n_drop_i
vm_dur_i   = n_kept_i / src_fps / speed_i
pre_dup_i  = round(vm_dur_i · out_fps)                      # fps 滤镜按 PTS 重采样
out_dur_i  = (pre_dup_i + dup_i) / out_fps
exp_dur    = Σ out_dur_i
```

多 seed 复核（`test_exp_dur_matches_actual`，14s 素材，6 段，真实执行 + ffprobe）：

| seed | exp_dur | 实际 | 误差 |
|---|---|---|---|
| 1786869947670 | 12.566667 | 12.566667 | 0.0000% |
| 1786869947673 | 12.100000 | 12.100000 | 0.0000% |
| 1786869947680 | 12.366667 | 12.366667 | 0.0000% |
| 1786869947700 | 13.366667 | 13.366667 | 0.0000% |
| 424242 | 12.600000 | 12.600000 | 0.0000% |

5/5 达到 0.0000%，优于要求的 <2%。

`quality_check` 容差已从 ±25% 收紧到 **±5%**，`process_single_pass` 段内校验为 ±2%；
`processor.py` 与 `segment.py` 现在共用同一个 `segment.effective_duration(duration, snap)`
（`test_quality_check_expect_matches_cuts` 验证 3 seed × 3 时长共 9 组
`effective_duration == make_equal_cuts(...)[-1]`，并断言 `processor.py` 真的调用了这个 helper）。

## L. ffprobe 最终输出

**before**：

```
format.duration = 20.780000
video: nb_frames = 621   duration = 20.700000   r_frame_rate = 30/1
audio: duration = 20.780000
```

音频比视频长 0.080000s，容器时长取音频（20.780000），视频只有 20.700000。

**after**：

```
format.duration = 19.633333
video: nb_frames = 589   duration = 19.633333   r_frame_rate = 30/1
audio: duration = 19.633000
```

视频与容器时长完全一致；音视频差 0.000333s（AAC 帧栅格量化），远小于 1/30。

## M. 测试结果

`python tests/test_timeline_integrity.py` → **PASSED 11 / 11**

| # | 测试 | 覆盖 | 结果 |
|---|---|---|---|
| 1 | `test_audio_pitch_duration_invariance` | P0-1 变调不得改时长；44.1k/48k/32k/22.05k | PASS（误差 0.0703%–0.1452%，全部 <1%；`asetrate/sr` 恒为 1.069161±2e-5，变调仍生效） |
| 2 | `test_concat_no_padding` | P0-2 段内 \|a−v\| ≤ 1/out_fps 且最终帧 == Σ 段帧 | PASS（worst 0.00000，补帧 0，377 == 377） |
| 3 | `test_quality_check_expect_matches_cuts` | P0-3 期望时长与切点同源 | PASS（9/9 组一致；processor 确实调用 helper） |
| 4 | `test_exp_dur_matches_actual` | P0-4 exp_dur 误差 <2% | PASS（5 seed 全部 0.0000%） |
| 5 | `test_speed_changes_duration` | P1-1 变速真实生效、不被 drop/dup 抵消 | PASS（20 组合帧精确 + 4/4 单调性） |
| 6 | `test_segment_windows_contiguous` | P1-2 段窗口连续、音视频窗口一致 | PASS（3 seed × 6 段） |
| 7 | `test_av_offset_duration_neutral` | P1-3 偏移真实且长度中性 | PASS（+0.10 / −0.10 / 0 三档） |
| 8 | `test_reverse_loop_av_delta_equal` | P1-4/5 reverse 不改长度、loop 增量一致 | PASS（3 模式 × 3 speed = 9 组） |
| 9 | `test_frame_drop_window` | P1-6 删帧落输入空间窗口、数量正确 | PASS（9 组 + 3 组真实执行） |
| 10 | `test_frame_dup_position` | P1-7 克隆位置在真实输出时间轴 | PASS（9 组，最大偏差 1 帧） |
| 11 | `test_cmd_dump` | 取证开关落盘 + 默认关闭 | PASS |

测试素材全部由 lavfi 现场合成（可控 fps / 采样率 / 可测量标记），不依赖任何私有视频；
临时目录用完即删。

修复过程中发现并修掉的**测试脚手架**缺陷（未削弱任何断言）：

- `render_audio` 里 `"a_%d.wav" % abs(hash(...)) % 10**8` 优先级错误 → `TypeError`，
  导致测试 2 / 8 直接 ERROR；
- 测试 5 / 8 / 9 / 10 直接把 `[0:v]` / `[0:a]` 喂给 `build_segment_branch` / `build_segment_audio`，
  而这两个函数**自身不做 trim**（真实调用方 `segment.py` 先裁好 `[vtN]` / `[at_inN]` 再传入），
  多算了 8s − 6s = 2s 素材（表现为"实测秒 = 预测秒 + 2/speed"）→ 新增 `win_inputs()` 补上窗口裁剪；
- 测试 10 的重复帧检测取"第一段连续相同 md5"，而 speed≠1 时 `fps` 重采样自身会零星复制单帧
  （run=1），会先命中噪声 → 改为取**最长**连续段（frame_dup 产生 run == dup，是全局最长的一段）。

第 8 项判定口径被修正过一次：原先要求"裸音频增量 == 视频增量"，
但 `build_segment_audio` **不施加视频 speed**（音频变速是独立的 `audio_atempo` 参数，
音视频长度靠 `win_len` 中性化对齐），所以 speed≠1 时裸增量本来就不该相等。
现口径改为三条同时成立（预测帧精确 / `|Δv−Δa| ≤ 1/out_fps` / 量化残差 `≤ 1/out_fps`），
这是对代码真实语义的正确刻画，不是为了通过测试而放宽。

---

## 完整证据链：source → 最终输出

以 **after** 第 0 段为例，逐层给出实测数字（素材 21.108s / 29.97fps / 48000Hz）。

| 层 | 操作 | 输入 | 输出 | 依据 |
|---|---|---|---|---|
| source | — | 21.108s / 29.97fps / 48000Hz | 同左 | ffprobe |
| 全局预处理 | scale/crop/scale/crop/eq/hue/lenscorrection | 21.108s | 21.108s（不改时长） | `[gbase]`，无 trim/setpts |
| trim | `trim=start=0.964:end=4.206` | 21.108s | `ceil(4.206×29.97) − ceil(0.964×29.97)` = 127 − 29 = **98 帧** | `[vt0]`，帧精确公式 |
| reverse_loop | 本段未触发 | 98 帧 | 98 帧 | graph 无 `asplit=3` / `areverse` |
| frame_drop | `select='not(eq(n,32))'` | 98 帧 | **97 帧** | 输入空间帧号 32（= 输出时刻 × speed × src_fps） |
| speed | `setpts=1.019888*PTS`（speed=0.9805） | 97 帧 / 3.2366s | 3.3009s（PTS 拉伸，帧数不变） | 1/0.9805 = 1.019888 |
| zoom | 本段未触发 | — | — | graph 无 `zoompan` |
| rotate | `rotate=...:enable='between(t,0.111,3.076)'`（timeline） | 3.3009s | 3.3009s（不改时长/帧数） | 窗口外零开销 |
| normalize 尾 | `fps=30,format=yuv420p,setsar=1` | 3.3009s @ 29.97 | **99 帧** = round(3.3009×30) | 已删除 `setpts=N/30/TB` |
| frame_dup | `split` + `trim` + `tpad=start=2:start_mode=clone` | 99 帧 | **101 帧** | 克隆点 1.171s = 99×0.355/30 |
| audio | `atrim(0.964→4.206)` → `atempo=0.98269` → `asetrate=51320,aresample=48000`（pitch +1.158 半音）→ `atempo=0.935299`（补偿）→ EQ/HP/LP → `afade` → `adelay=105` | 3.242s @ 48000 | 中性化前 3.4715s | 用 48000 而非 44100 |
| audio 中性化 | `atrim=end=3.366667,apad=whole_dur=3.366667,asetpts=PTS-STARTPTS` | 3.4715s | **3.366667s** = 101/30 | `win_len = 段视频输出时长` |
| 段不变量 | — | v = 101 帧 = 3.366667s，a = 3.366667s | **\|a−v\| = 0.00000** | 实测 |
| concat | `concat=n=6:v=1:a=1` | Σ v = **589 帧**，每段 \|a−v\| = 0 | **589 帧**，补帧 **0** | 实测 |
| encode/mux | libx264 CFR 30fps + AAC | 589 帧 | format 19.633333s / video 19.633333s / audio 19.633000s | ffprobe |
| exp_dur 校验 | Σ out_dur_i | 19.633333 | 实际 19.633333 → **0.0000%** | 实测 |

时长台账（before → after，同一素材同一 seed）：

```
before  最终 20.780000s   exp_dur 19.369067s   误差 +7.2845%   补帧 32
        成因：音频 48000/44100 膨胀 → 每段音频超长 → concat 空洞 1.06667s
              + 窗口不连续（跳过/重复共 5 处）
              + setpts=N/30/TB 抹掉变速
after   最终 19.633333s   exp_dur 19.633333s   误差  0.0000%   补帧  0
```

## 逐项修复清单与落点

| 编号 | 问题 | 文件 / 函数 | 修复 | 验证 |
|---|---|---|---|---|
| P0-1 | 变调硬编码 44100，输入 48000 时时长膨胀 1.088435 | `video/filters.py::build_audio_filter` | 新增 `sample_rate` 参数，`asetrate=int(sr*rate)` / `aresample=sr`；调用链 `audio_processor.build_audio_args`、`_graph.build_segment_audio`、`core/segment.py`、`video/video_processor.py` 全部透传 `media_info["a_sample_rate"]` | 测试 1（4 种采样率）+ B 节 |
| P0-2 | concat 按 `max(v,a)` 推进 → 补帧 | `video/filters.py::build_audio_filter`（`win_len` 尾）、`_graph.build_segment_audio` | 段音频链尾 `atrim=end=win_len,apad=whole_dur=win_len,asetpts=PTS-STARTPTS` | 测试 2 + J 节补帧 0 |
| P0-3 | 期望时长口径分散（processor 少算 trim） | `core/segment.py::effective_duration`（新增）、`core/processor.py`、`core/quality_check.py` | 抽出唯一 helper；processor 改用它；容差 25% → 5% | 测试 3 |
| P0-4 | exp_dur 公式缺 drop/dup/帧栅格量化 | `_graph.py::segment_video_metrics`（新增）、`core/segment.py::process_single_pass` | 帧精确 `_trim_frames` + 完整链式公式；段内容差 ±2% | 测试 4 + K 节 |
| P1-1 | `setpts=N/{nf}/TB` 抹掉变速 | `_graph.py::build_segment_branch` 标准化尾 | 删除该 `setpts`，CFR 化交给 `fps={nf}` 按 PTS 重采样 | 测试 5 + B 节 |
| P1-2 | av_offset 平移段窗口 → 段不连续 | `core/segment.py::process_single_pass` / `process_segmented` | 删除窗口平移；`t0/t1` 只由切点决定；切点用 `effective_duration` 并加回 `trim_head` | 测试 6 + C 节 |
| P1-3 | av_offset 改变段音频长度 | `video/filters.py::build_audio_filter` | `adelay` / `atrim` 之后统一走 `win_len` 中性化 | 测试 7 |
| P1-4/5 | reverse_loop 时间空间混用、rl 增量音视频不等 | `core/randomizer.py::generate_segment_plan`、`_graph.py::rl_extra_frames` / `rl_input_seconds` / `rl_extra_seconds` | `rl.seg_len` 量化到输出帧栅格；新增帧精确 `rl_extra_frames`（镜像 `build_reverse_loop_complex` 的 `:.3f` 切点） | 测试 8 |
| P1-6 | frame_drop 用 `eff_fps=30` 且把输出空间窗口当输入帧号 | `_graph.py::frame_drop_plan` / `frame_drop_chain` | 增加 `src_fps` 与 `speed` 参数：`input_frame = output_time × speed × src_fps` | 测试 9 |
| P1-7 | frame_dup 的 `t_pos` 不在真实输出时间轴上 | `_graph.py::segment_video_metrics` / `build_segment_branch` | 用 `frames_pre_dup / out_fps` 作为真实输出时长再取 `pos` | 测试 10 |
| 取证 | `run_ffmpeg` 从不记录命令，事故无法复盘 | `core/ffmpeg_runner.py`、`core/processor.py` | 新增 `dump_enabled()` / `dump_command()` / `dump_snapshot()`，`REWASH_DUMP_CMD=1` 时落 `logs/cmd_dump/*.json`（默认关闭） | 测试 11 |
| 清理 | `tests/reconstruct_test.py` 用错误模型反推时长 | 已删除 | 该脚本把 fps 固定为 30（源实为 29.97）、把 `av_offset` 当时长增量、`dup_extra = frame_dup/fps` 忽略帧栅格量化、段号从 1 计、完全忽略 frame_drop 与 concat 补帧，且目标是去凑一条历史日志里的 "22.4s"。其职责已被 `test_exp_dur_matches_actual` + `segment_video_metrics` 完整覆盖 | — |

## 禁止项自查

| 禁止项 | 状态 |
|---|---|
| 不得修改 randomizer 取值范围 | 未改。`randomizer.py` 唯一改动是把 `rl.seg_len` 量化到输出帧栅格（`rl_frames/out_fps`），取值区间 `event_length` 仍读 config |
| 不得降低 pitch 范围 | 未改。本次运行 `audio_pitch=1.158` 原样生效，`asetrate/sr = 1.069167` 实测 |
| 不得关闭 normalize | 未关。`fps=30,format=yuv420p,setsar=1` 仍在，只删了会破坏时间轴的 `setpts=N/30/TB` |
| 不得关闭 frame_drop / frame_dup / reverse_loop | 未关。E / F / H 节均有实测触发证据 |
| 不得在 concat 后再裁剪 | 未做。concat 后只有编码器参数 |
| 不得改 quality_check 阈值来让测试通过 | 阈值是**收紧**的（25% → 5%），方向与"放宽以通过"相反 |
| 不得用固定 30fps 代替真实源帧率 | `src_fps` 已贯穿 `frame_drop_plan` / `segment_video_metrics` / `build_segment_branch`；本次实测源帧率 29.97 被正确使用（E 节矩阵含 25 / 29.97 / 60） |

## 【证据不足】

1. 原始事故运行（2026-08-23 18:13:31，输出 22.4s）的源码树是未提交的中间状态，输出文件已被删除，
   当时的 `run_ffmpeg` 也不记录命令。本报告的 before 基线是**已提交的 `08062c6`**，
   不等于事故当时那棵树。第一轮审计中曾通过复现 rotate 窗口逐字匹配日志、并实测复现出
   "video 22.400000s / 672 帧" 来间接锁定那棵树，但该结论无法再被独立复核。
2. 本报告 A/B 使用 lavfi 合成素材（21.108s / 29.97fps / 48000Hz / 720×1280），不是原始私有素材。
   选择合成素材是为了让证据可被任何人重跑；代价是分辨率与编码特性不同，
   **未**验证原素材在修复后代码上的具体输出时长。
3. before 侧 `format.duration` 与 `video.duration` 相差 0.08s，机制已定位（容器时长取音频），
   但未逐字段拆解 mp4 `mvhd` / `mdhd` 时间基，属未验证细节。

## 复现方式

```powershell
# 全部测试（真实 FFmpeg）
python tests/test_timeline_integrity.py
# 单项（按名字关键字过滤）
python tests/test_timeline_integrity.py speed

# 取证：落盘每条 FFmpeg 命令与参数快照
$env:REWASH_DUMP_CMD = "1"     # 输出到 logs/cmd_dump/*.json，默认关闭
```

## 状态

- 代码修复：完成（P0-1、P0-2、P0-3、P0-4、P1-1、P1-2、P1-3、P1-4/5、P1-6、P1-7 + 取证 + 清理）
- 实测验证：完成（11/11 测试 PASS；A/B 对照误差 7.2845% → 0.0000%；concat 补帧 32 → 0）
- **未 commit、未 push**（按要求）
