# PARAMETER_BUG_REPORT_V4

第二阶段取证产出 —— FPS 域矩阵端到端实测后的 Bug 清单

- 日期：2026-08-23
- 取证脚本：`tests/param_forensics.py`（可复跑：`python tests/param_forensics.py fps`）
- 证据落盘：`tests/evidence/fps_matrix/TEST-{A..F}/`
  （`input_probe.json` / `output_probe.json` / `command.txt` / `filtergraph.txt` /
  `parameter_snapshot.json` / `segment_plan.json` / `frame_metrics.json` /
  `audio_metrics.json` / `result.json`）
- 素材：lavfi 合成，30s，480×640，音频立体声（左 1kHz / 右 440Hz），48000 Hz
- 取证期配置覆盖（已记录进每个 `result.json` 的 `config_override`）：
  `normalize.width=324`、`normalize.height=432`、`normalize.video_codec=h264_libx264`
  —— 只为缩短编码耗时；时间轴结论与画布尺寸无关
- seed：`1786869947670`（全部用例同一 seed）
- **第二阶段（本文件上半部分，即「修复记录」之前的所有内容）未修改任何产品代码**；
  修复发生在第四阶段，见文末「修复记录」。上半部分的行号是**修复前**的源码行号，
  修复后的行号在「修复记录」中单独列出。

## FPS 矩阵总览（真实端到端，6 段 single-pass）

| 素材 | 源 fps | A 层是否插入 `fps=30` | C1 branch_fps | C2 抽帧计划/实删 | C3 帧号越界 | C4 worst\|a−v\| | C5 concat 补帧 | C6 exp_dur 误差 | 总判定 |
|---|---|---|---|---|---|---|---|---|---|
| TEST-A | 25 | 否 | PASS | 5 / 5 | 0 | **0.166666** | 0 | 0.0000% | **BUG** |
| TEST-B | 29.97 | 否 | PASS | 4 / 4 | 0 | 0.033333 | 0 | 0.1174% | PASS |
| TEST-C | 30 | 否 | PASS | 4 / 4 | 0 | 0.033333 | 0 | 0.1172% | PASS |
| TEST-D | 50 | **是** | **BUG**(6/6 段) | **10 / 6** | **4** | **0.066667** | **2** | 0.2338% | **BUG** |
| TEST-E | 60 | **是** | **BUG**(6/6 段) | **14 / 7** | **7** | **0.066667** | **2** | 0.2353% | **BUG** |
| TEST-F | 120 | **是** | **BUG**(6/6 段) | **28 / 11** | **17** | **0.166666** | **4** | 0.2338% | **BUG** |

关键：**C6 六个用例全部通过流水线自身的 ±2% 时长门**（`passes_pipeline_2pct_gate=true`），
即以上全部错误在生产环境中都是**静默**的。

---

## BUG-B1【P0】FPS 域错位：A 层降帧发生在 trim 之前，但帧号/帧数按源帧率计算

- **Severity**：P0（影响正确性且静默）
- **源码位置**
  - `video_rewash/core/segment.py:119` A 层插入降帧
    ```python
    common = (f"fps={eff_fps:.3f}," if eff_fps + 1e-6 < fps else "") + geom
    ```
  - `video_rewash/core/segment.py:153-154` 仍传源帧率
    ```python
    met = segment_video_metrics(snap, config, plan, i, seg_len,
                                speed, fps, eff_fps, t0=t0, t1=t1)
    ```
  - `video_rewash/core/segment.py:162-165` `build_segment_branch(..., src_fps=fps, ...)`
  - `video_rewash/video/video_processor.py:93-94`、`:74-75`、`:127-130`、`:189-190` 同病
- **触发条件**：`normalize.fps < 源视频帧率`（50 / 60 / 120 fps 素材，短视频平台常见）
- **复现步骤**
  ```
  python tests/param_forensics.py fps
  # 查看 tests/evidence/fps_matrix/TEST-E/result.json
  ```
- **预期**：`met["n_win"]` == `[vt{i}]` 上实测帧数；计划删帧数 == 实际删帧数
- **实际**（TEST-E，60fps → 30fps，逐段实测）

  | seg | n_win 预测@60 | 实测 `[vt]` | 计划删帧号 | 实际生效删帧 |
  |---|---|---|---|---|
  | 0 | 284 | 142 | 3 个 | 2 |
  | 1 | 283 | 141 | 2 个 | **0** |
  | 2 | 284 | 142 | 4 个 | **0** |
  | 3 | 283 | 142 | 2 个 | 2 |
  | 4 | 284 | 142 | 0 | 0 |
  | 5 | 283 | 141 | 3 个 | 3 |

  合计计划 14 帧，实际只删掉 7 帧；7 个帧号越界（≥ 该段真实帧数）；
  `select='not(eq(n,N))'` 对不存在的帧号静默无效。
  TEST-F（120fps）更严重：计划 28 帧，实删 11 帧，17 个帧号越界。
- **FFmpeg 证据**：`tests/evidence/fps_matrix/TEST-E/filtergraph.txt` 第 0 行
  ```
  [0:v]fps=30.000,scale=324:432:...[gbase]
  ```
  降帧确实在 `[gbase]split` 与 `trim` 之前。
- **根因**：`[vt{i}]` 上的流已经是 `eff_fps`，而 `_trim_frames` 与 `frame_drop_plan`
  按 `media_info["fps"]` 计算，两者相差 `src_fps/eff_fps` 倍（60→30 即 2 倍）。
- **修复方案（G1）**：引入「进入段分支时流的真实帧率」
  ```
  branch_fps = eff_fps if (norm_spec and eff_fps + 1e-6 < fps) else fps
  ```
  判定条件必须与 A 层的 `if` **逐字一致**，否则再次分叉。
  `segment_video_metrics` / `build_segment_branch` / `frame_drop_chain` 全部改传 `branch_fps`。
  **禁止**把 A 层的 `fps=` 挪到 trim 之后来绕过（会让重滤镜按源帧率跑，
  丢掉 `segment.py:115-116` 注释里的性能收益）。
- **修复代码位置**：`segment.py:105-119,153-165`；`video_processor.py:74-75,93-94,127-130,189-190`
- **验证方法**：重跑 `python tests/param_forensics.py fps`，要求 TEST-D/E/F 的
  C1/C2/C3/C4/C5 全部 PASS，且 TEST-A/B/C 无回归

## BUG-B10【P1】zoom 窗口的 `zoompan=fps=` 用输出帧率压缩了该切片的 PTS 跨度（本轮新发现）

- **Severity**：P1
- **源码位置**
  - `video_rewash/video/filters.py:184-188`
    ```python
    parts.append(f"[zs{idx}{sfx}]trim=start={a:.3f}:duration={d:.3f},"
                 f"setpts=PTS-STARTPTS,"
                 f"zoompan=z='{z_expr}':d=1"
                 f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                 f":s={tw}x{th}:fps={fps:.3f}[zB{sfx}]")
    ```
  - 调用方 `video_rewash/core/_graph.py`（⑤ zoom 窗口）传的 `fps` 是 `out_fps`
  - `segment_video_metrics`（`_graph.py:106-160`）**完全不建模 zoom**
- **触发条件**：`plan.zoom.on == True` 且 `branch_fps ≠ normalize.fps`
  （25fps 源 → 30fps 输出是最典型情形）
- **复现步骤**
  ```
  python tests/param_forensics.py fps_matrix   # 生成素材与 filtergraph
  # 逐 label 计帧：见 frame_metrics.json 的 vt/vb/v_measured 三列
  # （当时用的一次性脚本 tests/drill_lastseg.py 已随取证结束删除，
  #   同等信息已固化进 frame_metrics.json）
  ```
- **预期**：zoom 窗口切段 + concat 不改变该段的帧数与时间跨度
- **实际**（TEST-A，25fps 源，第 5 段，逐 label 真实计帧）
  ```
  [gb]      750 帧   全长
  [vt5]     118 帧   == n_win 预测 118            ✓
  [vb_5]    117 帧   删 1 帧                       ✓
  [zout_5]  117 帧   zoom 切段拼接后帧数守恒        ✓
  [vm_5]    133 帧   fps=30 后；预测 138           ✗ 少 5 帧
  [v_5]     134 帧   = 133 + tpad 1
  ```
  该段音频按预测 `out_dur` 中性化到 4.833s，视频实际 4.467s →
  **\|a−v\| = 0.16667s（5 帧）**，超出 `1/out_fps = 0.0333s` 容差 5 倍。
- **FFmpeg 证据**：`tests/evidence/fps_matrix/TEST-A/filtergraph.txt`
  ```
  [zs1_5]trim=start=1.559:duration=1.061,setpts=PTS-STARTPTS,
  zoompan=z='1+0.0290*(1-mod(in\,105)/105)':d=1:...:s=324x432:fps=30.000[zB_5]
  ```
- **根因**：`zoompan` 的 `fps` 决定**输出时间戳栅格**。`d=1` 时输出帧数 == 输入帧数
  （实测 117 → 117 守恒），但这些帧被按 30fps 重新打戳，
  于是该切片的 PTS 跨度从 `1.061s`（25fps 下 26 帧）压缩到 `26/30 = 0.867s`，
  缩短 `0.194s ≈ 5.8 帧@30fps` —— 与实测少 5 帧一致。
  后续 `fps=30` 按 PTS 跨度重采样，于是整段少 5 帧。
  同时 `segment_video_metrics` 不建模 zoom，`win_len` 用了未压缩的预测值，
  导致音频被中性化到一个视频达不到的长度。
- **为什么 29.97/30 源看不出来**：压缩因子 `branch_fps/out_fps ≈ 1`，
  残差 ≤1 帧（实测 TEST-B/C worst \|a−v\| 恰为 0.033333 = 1 帧，压在容差边界上）
- **修复方案**
  - 方案 a（推荐，最小改动）：`build_zoom_window_complex` 的 `zoompan` 用
    **branch_fps** 而不是 out_fps，让该切片的 PTS 跨度保持不变；
    CFR 化仍由链尾统一的 `fps={out_fps}` 负责
  - 方案 b：保留 `fps=out_fps`，但在 `segment_video_metrics` 中显式建模
    zoom 切片的时间压缩（`zoom_dur × (1 − branch_fps/out_fps)`）——
    公式复杂且与 B1 的 `branch_fps` 修复耦合，不推荐
- **修复代码位置**：`filters.py:158-196`（签名与 `zoompan=fps=`）+ `_graph.py` 中 ⑤ 的调用点
- **验证方法**：新增 `test_zoom_window_frame_conservation`：
  25 / 29.97 / 30 / 50 / 60 fps × `zoom.on=True`，要求
  ①`[zout]` 帧数 == `[vb]` 帧数；②`[vm]` 帧数 == `segment_video_metrics` 预测；
  ③段内 `|a−v| ≤ 1/out_fps`

## 沿用 V3 的 Bug（本轮未复测，状态不变）

| ID | Severity | 摘要 | 源码位置 |
|---|---|---|---|
| B2 | P1 | `plan.reverse_loop.seg_len` 按输出帧栅格量化却当输入时长用 | `randomizer.py:518-525` vs `_graph.py:200-202` |
| B3 | P2 | 整文件路径 `plan_lens_events` 用输出时间轴 | `video_processor.py:95` |
| B4 | P2 | `av_offset` ±0.02s 死区，参数被静默丢弃 | `filters.py:535,537` |
| B5 | P2 | 变调采样率静默回退 44100 → +9.0345% 膨胀 | `filters.py:489-492` |

## 本轮新增待确证（源码级已定位，尚无实测）

| ID | Severity | 摘要 | 源码位置 | 触发条件 |
|---|---|---|---|---|
| B6 | P2 | 非 f-string 字面量 `"(iw-{tw})/2"`，`{tw}` 不展开 → 非法 crop 表达式 | `filters.py:56-57` | `has_asym` 为真但 `cl+cr ≤ 0.001`（只有上下非对称裁剪） |
| B7 | P2 | 只有 `afade=t=in`，无 `afade=t=out`，与"淡入淡出"设计不符 | `filters.py:528-530` | 恒定 |
| B8 | P2 | `crf = max(24, ...)` 静默上钳，预设 `crf.min=19` 时区间下半段失效 | `filters.py:586,629` | 预设 `crf.min < 24` |
| B9 | P2 | `spec_encode_args` 不传 `sc_threshold`（主路径），`get_encode_args` 才传 | `filters.py:620-703` | 用标准化页编码器（主路径） |

## 【证据不足】

| ID | 项 | 缺什么 |
|---|---|---|
| E1 | `av_offset` 端到端负方向 | `silencedetect` 依赖前导静音，`atrim=start` 把静音连同部分音调一起切掉 → 需换成内嵌脉冲序列标记 |
| E2 | `effective_duration` 的 `max(2.0,…)` 短片边界 | 需构造 `duration < trim_head+trim_tail+2.0` 且 `requested_count≥2` |
| E3 | 降级路径 / `_merge_reencode` | 未端到端实测 |
| E4 | rl 触发时 frame_drop 帧号平移 | 需构造同段同时触发 |
| E5 | 视觉参数（`scale`/`asym_crop`/`rotate`/`zoom`/`lens`/`brightness`/`contrast`/`saturation`/`hue`） | 需 `signalstats`/`entropy` 统计化取证；本轮只测时间轴 |
| E6 | 音频频谱（EQ / highpass / lowpass / fade 包络 / 声道） | 需 `astats`/频谱取证；素材已备好左右异频 |
| E7 | 编码参数（QP / 关键帧间隔 / B 帧 / pix_fmt / SAR） | 未校验输出 |
| E8 | seed 确定性与五套 seed 碰撞 | 未做同 seed 两跑 filtergraph 逐字比对 |
| E9 | 44100 Hz 素材（TEST-B44 / TEST-E44） | 素材定义已就绪，本轮未跑 |

## 与 V3 的差异（V3 discrepancy）

| 项 | V3 结论 | 本轮实测 | 处理 |
|---|---|---|---|
| B1 影响面 | 仅举 60fps 一例，`concat` 补帧 5 帧 | 50/60/120 全中；补帧 2/2/4；帧号越界 4/7/17 | 以本轮为准，B1 升级为完整 FPS 矩阵证据 |
| B1 时的 frame_drop | 「计划 8 实删 4」（21.108s 素材） | 30s 素材：50fps 10/6、60fps 14/7、120fps 28/11 | 两者一致，样本更全 |
| 段内 `\|a−v\|` | 「29.97 源 6 段全 0.00000」 | 30s 素材 29.97 源 worst = 0.033333（1 帧，压在容差边界） | 差异来自 zoom 是否触发（B10）；V3 那次 6 段未触发 zoom |
| zoom 窗口 | 列为 E6【证据不足】「未做帧数实测」 | 已实测：帧数守恒但 PTS 跨度被压缩 → B10 | 新增 B10 |

## 修复记录（修复前 → 修改 → 修复后，逐项实测）

### B1 已修复 ✅

- 修复代码位置
  - `video_rewash/core/segment.py:119-124` 新增 `do_down` / `branch_fps`
  - `segment.py:158-160`（`segment_video_metrics(..., branch_fps, eff_fps, coarse_tb=do_down)`）
  - `segment.py:169-171`（`build_segment_branch(..., src_fps=branch_fps)`）
  - `video_rewash/video/video_processor.py:47-50`（降级路径同口径 `branch_fps`）
  - `video_processor.py:78-80`、`:133`、`:193-195`（`frame_drop_chain` 也改 `branch_fps`）
- 判据：`branch_fps = eff_fps if (A 层插入了 fps=eff_fps) else fps`，条件表达式与公共链的 `if` 完全一致，避免两处漂移。
- 复测：`python tests/param_forensics.py fps_matrix`

### B10 已修复 ✅

- 修复代码位置：`video_rewash/core/_graph.py:239-244` —— `build_zoom_window_complex(cur, z_in, p, fps_in, tw, th, ...)`（原为 `eff_fps`）
- 效果：25fps 源 seg5 的 `|a−v|` 从 **0.166666 → 0.033334**；TEST-A 总判定 BUG → PASS。
- 60/120fps 源上 `fps_in == eff_fps == 30`，滤镜串字节不变 → 无回归。

### B11【P1，本轮新发现并修复】降帧后 trim 帧数量化口径错误

- 现象：B1 修好后 TEST-D/E/F 仍有 3 段 `n_win` 与实测 `[vt]` 差 **±1 帧**，并经 `win_len` 传给音频 → `concat` 补帧 2 帧。
- 取证（`ffmpeg -vf "fps=30,trim=...,showinfo"`，TEST-E 60fps 源）：

| 窗口 | 实测帧 | ceil 模型 | round 模型 |
|---|---|---|---|
| 0.964–5.688 | 142 | 142 | 142 |
| 5.688–10.412 | **141** | 142 ✗ | 141 ✓ |
| 10.412–15.137 | 142 | 142 | 142 |
| 15.137–19.861 | **142** | 141 ✗ | 142 ✓ |
| 19.861–24.585 | 142 | 142 | 142 |
| 24.585–29.309 | **141** | 142 ✗ | 141 ✓ |

- 根因：`fps=30` 之后流时基变为 **1/30**，`trim` 的 start/end 先按时基**四舍五入**成整帧号再取 `[start_i, end_i)`；而源流时基远细于帧间隔时才是 `ceil` 语义。栅格实测为严格 `k/30`（`pts_time` 0, 0.0333333, …），末帧 10.3667=311/30 而非 312/30，正是 `312.36 → 312` 的证据。
- 修复：`_graph.py:106-123` `_trim_frames(..., coarse_tb)`；`segment_video_metrics(..., coarse_tb)`；`rl_extra_frames(..., coarse_tb)` 同步；调用方按「A 层是否插入 fps=」传入。
- 修复后：TEST-D/E/F 的 C1 全 PASS，`exp_dur` 误差 **0.2338% / 0.2353% / 0.2338% → 0.0000%**，补帧 2 → 1（换 seed 派生后 → 0）。

### B12【P2，本轮新发现并修复】派生 seed 撞车 + 第 0 段未独立随机

- 取证：`param_forensics determinism` → `D3 collisions: {base: [child_0, plan_0]}`；即 `seg_idx=0` 时 `_child_snapshot` 的 `base+0*7919` 与 `generate_segment_plan` 的 `base+0*104729` 都等于 base seed，两条随机流与全片快照同源 → 第 0 段 B/C 层参数与全片快照完全相同，事件规划与之相关。
- 修复：`segment.py:80-84` → `base + (seg_idx+1)*7919`；`randomizer.py:436-439` → `base + (seg_idx+1)*104729`。**未改任何随机取值区间**，只改派生偏移。
- 修复后：D1/D2/D3 全 PASS（21 个派生 seed 零碰撞；5 个 seed 产生 5 个互不相同的 filtergraph；同 seed 两次 filtergraph 字节一致）。

### 非 Bug 澄清（原 D1 判 BUG 的原因）

`snapshot` 里的 `ts`（`randomizer.py:89` `time.strftime`）是生成时刻墙上时钟，属溯源元数据不是参数；跨秒运行必然不同。判据已改为「排除 `ts` 后比对」，并把两次 `ts` 一起写进证据。**不是产品缺陷**。

## 修复后完整回归（全部真实 FFmpeg 端到端）

- `tests/param_forensics.py fps_matrix`：TEST-A/B/C/D/E/F **6/6 PASS**（C1–C7 全 PASS）
- `tests/param_forensics.py sr_matrix`：TEST-B44（44100Hz/29.97）、TEST-E44（44100Hz/60→30）**2/2 PASS**，C7 变调链 6 处 `asetrate→aresample` 全部收敛到 44100
- `tests/param_forensics.py determinism`：**PASS**
- `tests/test_timeline_integrity.py`：**11/11 PASS**
- 换 seed 派生口径后（等价于换一整套事件计划）8 个素材仍全 PASS，抽帧计划从 4~5 处变为 3~6 处、补帧全 0 → 修复不是对单一 plan 的过拟合。

关键数字（修复前 → 修复后，`exp_dur` 相对误差 / 段内最坏 `|a−v|` / concat 补帧）：

- TEST-A 25fps：0.0000% / **0.166666** / 0 → 0.1171% / **0.033334** / 0
- TEST-B 29.97fps：0.1174% / 0.033333 / 0 → 0.1175% / 0.033333 / 0
- TEST-C 30fps：0.1172% / 0.033333 / 0 → 0.1174% / 0.033334 / 0
- TEST-D 50fps：**0.2338%** / **0.066667** / **2** → **0.0024%** / 0.033334 / **0**
- TEST-E 60fps：**0.2353%** / **0.066667** / **2** → **0.0024%** / 0.033334 / **0**
- TEST-F 120fps：**0.2338%** / **0.166666** / **4** → **0.0024%** / 0.033334 / **0**

（TEST-A 修复后 `exp_dur` 误差由 0 变 0.1171% 属正常：修复前 A 的 0.0000% 是「预测与输出同时错」的巧合抵消，修复后 0.1171% 与 B/C 同量级，来自 mp4 容器 `format.duration` 比视频流多算最后一帧的固有偏差，绝对值 0.033s = 1 帧，在 ≤max(0.01s, 1/30) 判据内。）

## 下一步（按优先级）

1. 视觉参数统计化取证（E5：`signalstats`/`entropy`/输出尺寸/SAR）
2. 音频频谱取证（E6：EQ / highpass / lowpass / fade 包络 / 立体声 L-R，素材已含 L=1kHz R=440Hz）
3. 编码取证（E7：QP / 关键帧间隔 / B 帧 / pix_fmt）
4. 路径矩阵（segmented / fallback / `_merge_reencode`，E3）
5. B2–B9 定向确证与修复
6. `run_ffmpeg()` 记录 start_time/end_time（第 18 条尚未落地，现记录 command/return_code/elapsed/stderr）
7. `timeline_metrics.json` 目前未单独产出（帧/时长量已在 `frame_metrics.json` + `audio_metrics.json`）

> 上述 1–7 已在 V5 阶段全部完成或转为明确结论，详见 `PARAMETER_CALIBRATION_REPORT_V5.md`。以下为 V5 阶段追加内容，**历史内容不改写**。

## V5 阶段追加：降级路径与编码参数新发现（2026-08-23）

取证脚本：`tests/param_forensics_v5.py fallback|encode`（真实 `process_segmented` / `process_clip` / `_merge_reencode`）。

### B15【P0，V5 新发现并修复】降级路径直接崩溃：`build_command` 引用未定义的 `seg_total`

- 位置：`video_rewash/video/video_processor.py:114`
- 现象：`NameError: name 'seg_total' is not defined` —— B14（淡入淡出只加首尾段）给 `process_clip` 加了 `seg_total` 形参，但没传进 `build_command`。
- 影响：**只要单进程路径失败触发降级，处理立即异常退出**（`process_segmented` 第一段就抛）。
- 修复：`build_command(..., seg_total=1)` 形参 + 两处调用点透传。
- 修复后：3 段降级渲染全部成功（`fallback/DEGRADED/commands.txt` 共 4 条命令 = 3 段 + 1 合并）。

### B16【P0，V5 新发现并修复】`-t` 放在 `-i` 之后 → 输出侧限时，吞掉时间轴膨胀

- 位置：`video_rewash/video/video_processor.py:69-76`（修复前 `-t` 在 `-i` 之后）
- 判据与实测：
  - 段 1 台账预测 3.900s / 117 帧，实际输出 **3.767s / 113 帧**（= `-t` 值，正好被截断）
  - 段 2 预测 3.833s / 115 帧 → 实际 **3.767s / 113 帧**
  - 整文件模式同样受影响（frame_dup=3 的 +3 帧膨胀被吞）
- 根因：FFmpeg 中 `-t` 在最后一个 `-i` 之后即为**输出**时长上限；`reverse_loop` 循环增量、`frame_dup`、慢放都会让输出比输入窗口长，于是被硬截。
- 修复：`-t` 移到 `-i` 之前（输入侧限时），语义与单进程路径的 `trim=start:end` 一致。
- 修复后：段 1/2/3 输出帧数与台账预测**逐段相等**，两条路径总帧数 349/349、时长 11.6333s/11.6333s。

### B17【P1，V5 新发现并修复】降级路径第 0 段丢失 `reverse_loop` 事件

- 位置：`video_processor.py:81`（`if seg_idx == 0 and p.get("rl_mode")` 把段级 rl 计划清空）+ `use_complex` / 音频分支里的 `seg_idx > 0` 判断
- 现象：`seg_idx == 0` 被当作「整文件模式」，于是降级路径的第 0 段既拿不到快照级 rl（要求 `in_duration is None`），也拿不到段级 rl → 该段完全没有倒放循环。单进程路径的 i=0 是有的。
- 实测：`fallback/DEGRADED/commands.txt` cmd0 无 `split=3 … concat=n=5` 结构，108 帧；单进程同段 117 帧。
- 修复：改用 `seg_mode = in_duration is not None` 区分整文件/分段，三处判断同步。
- 修复后：三段 rl 结构一致，帧数与台账一致。

### B18【P2，V5 新发现并修复】降级路径镜头畸变事件只落在第 0 段

- 位置：`video_processor.py`（修复前 `lens_events = … if seg_idx == 0 else []`）
- 实测：cmd0 有 `lenscorrection=…`，cmd1/cmd2 完全没有 → 同一条视频里只有前 1/3 有畸变。单进程路径在 A 层对全片生效。
- 修复：去掉 `seg_idx == 0` 限制，每段按自身段快照规划畸变事件（与 rotate/zoom/抽帧的段级随机口径一致）。
- 残留说明：两条路径的**窗口位置**仍不同（段快照 seed 不同，属设计），但「是否有畸变」不再随段号丢失。

### B19【P1，V5 新发现并修复】整文件模式 `seg_dur` 未减 `trim_tail` → 音频比视频长 trim_tail

- 位置：`video_processor.py`（修复前 `seg_dur = in_duration if in_duration else max(1.0, total_dur - start)`）
- 现象：`-t` 已按 `avail - trim_tail` 限时，但时间轴真值 `seg_dur` 仍按 `total-start` 算 → `met["out_dur"]` 多 `trim_tail`，音频 `apad=whole_dur` 把音轨补到 11.7s，而视频只有 11.4s。
- 实测（trim_tail=0.3s，frame_dup=3）：视频 342 帧 = 11.400s，容器时长 **11.700s**，`|a−v| = 0.300s = trim_tail`。
- 说明：B16 修复前这条被掩盖（输出侧 `-t` 把音视频一起截了），B16 修复后暴露。
- 修复：`seg_dur = 实际读入窗口`（= `-t` 值），与 `process_clip` 的 `expect_dur` 口径统一。
- 修复后：预测 11.400s / 342 帧，实测 11.400s / 342 帧，Δ=0.000s。

### B9【P2，V5 修复并确证】`sc_threshold` 生成但主路径不传

- 位置：`video_rewash/video/filters.py` `spec_encode_args` libx264 分支（旧代码只有 `build_encode_args` 传 `-sc_threshold`）
- 修复：libx264 分支补 `-sc_threshold`。
- 确证（素材 V-PULSE 含硬切白闪，`-g 250` 让周期关键帧几乎不出现）：
  - `sc_threshold=0` → 关键帧 **2** 个，位置 [0, 250]
  - `sc_threshold=60` → 关键帧 **7** 个，位置 [0, 30, 90, 150, 210, 270, 330]（正对白闪时刻）
- 结论：由 INEFFECTIVE 转 **PASS**（判据取自编码器实际输出，不是命令行）。

### B20【P3，V5 新发现并修复】日志硬编码 44100 与实际不符

- 位置：`video_rewash/core/randomizer.py:349`，日志打印 `asetrate={int(44100*rate)}`，而实际滤镜用素材真实采样率（`filters.py:500`，48k 素材即 48000×rate）。
- 修复：日志改为 `asetrate=round(sr_in*rate)` 并注明 `sr_in` 为素材真实采样率。产品行为无变化（纯日志一致性）。

### B2【P1，V5 修复】rl 事件长度按 `normalize.fps` 量化，却当支路输入时长用

- 位置：`randomizer.py:518-528`（量化）vs `_graph.py` rl 切拼（消费）
- 修复：`generate_segment_plan(..., grid_fps=branch_fps)`，按**进入支路的真实帧率**量化；三处调用点（`segment.process_single_pass`、`video_processor.build_command`、`video_processor.process_clip`）与取证参照模型同步传入。
- 反证（旧口径的实际危害）：固定 `grid=normalize.fps=30`、支路 25fps 时扫 40 个 seed，**35 个** `seg_len × 25` 不是整数（如 0.133333s × 25 = 3.33 帧）→ 视频侧必须落整帧、音频侧不受限，两侧增量不等。
- 修复后：25 / 29.97 / 30 / 50 / 60 / 120 六种支路帧率的 `seg_len × branch_fps` 全为整数（4 / 4 / 4 / 7 / 9 / 18 帧）。证据：`tests/evidence/source_scan/B2_B3/result.json`
- 回归：fps 矩阵 8 素材全 PASS，C4 最坏 `|a−v|` 与 C5 补帧数与修复前同（此前该误差已被 `win_len` 对齐吸收，所以数值上看不出改善，本次修的是根因）。

### B3【P2，V5 修复】整文件/降级路径畸变事件按输出时间轴规划

- 位置：`video_processor.py`（修复前 `plan_lens_events(snap, config, seg_dur / speed)`）
- 原理：`lenscorrection` 在公共链里，位于 `setpts=1/speed*PTS` **之前** → `enable='between(t,…)'` 的 `t` 是输入时间轴。用 `seg_dur/speed` 规划时，`speed>1` 会把窗口全压到前段，`speed<1` 会越界。
- 实测（30s 素材 / speed=1.25）：修复前窗口上限只能到 24.0s（后 20% 永远没有畸变）；修复后窗口 [0,29.9]、[15,29.9]、[0.115,18.995]，末端 29.9s ≤ 输入窗口 30.0s。
- 修复：`plan_lens_events(snap, config, seg_dur)`（单进程路径本来就用源时长，口径统一）。

### B5【P2，V5 修复】变调采样率未知时静默回退 44100

- 位置：`filters.py:495-513`
- 修复前：`sr = … else 44100` → 48k 素材上 `asetrate=44100*rate` + `aresample=44100`，等于额外 ×(48000/44100) 变速且输出被重采样到 44100（V4 实测 +9.0345% 时长膨胀，音高也是错的），而且**完全静默**。
- 修复：采样率未知（0/None/异常）时**不生成变调链**，宁可少一个扰动参数也不悄悄改速度/音高。
- 实测：`sample_rate=48000` → `asetrate=60476,aresample=48000,atempo=0.793701`；`sample_rate=0`/`None` → 空链。证据：`tests/evidence/source_scan/B5/result.json`
- 回归：`param_forensics_v5.py audio` 11/11 PASS（A3 ±4 半音仍为 1261Hz / 798Hz，已知采样率路径逐字未变）。

### 仍为 INEFFECTIVE（设计取舍，需产品决策，未改代码）

- **B8 CRF 下限钳制 24**：`spec_encode_args` 里 `crf = max(24, …)`。实测 crf=19 与 crf=24 输出**字节数完全相同**（545460 / 545460）→ 预设区间里 <24 的取值全部无效。该钳制是为体积对齐刻意加的（qp17 实测 ≈11Mbps），要不要放开属产品决策，本轮不擅自改。
- **`frame_drop_on`**：`randomizer` 生成但渲染链从未引用（抽帧由 `video.frame_drop.enable/probability` + 段计划驱动）。源码级确认为死参数，涉及 GUI 展示，未删。

