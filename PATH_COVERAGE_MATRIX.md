# PATH_COVERAGE_MATRIX.md — 生产路径覆盖矩阵（V8）

> 五条路径的定义以源码为准（读当前源码，不引用历史报告结论）：
>
> - `single_pass` —— `segment.process_single_pass`：一次解码 + 一个
>   `filter_complex`（含 `build_segment_branch` × n + concat）+ 一次编码
> - `fallback` —— `process_single_pass` 返回失败后 `process_segmented` 走
>   `video_processor.process_clip` × n（每段独立解码/编码为中间文件）
> - `merge_reencode` —— `segment._merge_reencode`：把 `fallback` 的 n 个中间段
>   重编码合并为最终文件
> - `whole_file` —— `process_clip(in_duration=None)`：不分段、整文件一次处理
> - `segmented` —— 上层入口 `process_segmented`（先试 `single_pass`，
>   失败才 `fallback` + `merge_reencode`）；作为**标签**时指"分段后的最终产物"
>
> 判定口径：未真实渲染 / 无法度量 / 基线==覆盖值 ⇒ EVIDENCE_INSUFFICIENT，
> 不写 PASS。

## 1. 路径级证据（V8 新增）

V8 基线时证据库里 `production_path` 只有 `single_pass` / `segmented` /
`whole_file` / `all` 四种标签，`fallback` 与 `merge_reencode` **一条都没有**
（`AUDIT_BASELINE.md` §6）。V8 在 `tests/param_forensics_v7.py::phase_paths3`
增加了两条独立标签的证据：

- `paths3/fallback_clips`（`path="fallback"`，group `paths/fallback`）
  —— 在 `_merge_reencode` 之前抓取每个中间段的 probe，度量
  `sum(中间段帧数)` 对 `台账预测帧数`（容差 2 帧）
- `paths3/merge_reencode`（`path="merge_reencode"`，group `paths/merge_reencode`）
  —— 度量 `合并输出帧数 − sum(中间段帧数)`、PTS 单调、合并命令留证

实测（`RUN-000080`，B55 修复后新执行）：

- `paths3/single_vs_segmented_vs_whole  PASS  A帧=330 B帧=331 C帧=325
  Δ帧=1 Δ时长=0.033333`
- `paths3/fallback_clips  PASS  段帧=[111,109,110] 台账=330`
- `paths3/merge_reencode  PASS  合并=331 段和=330`（+1 帧 = B39 记录的 PARTIAL）

## 2. 参数 × 路径

约定：`✓实测` = 该路径上有真实渲染证据；`—` = 该路径按设计不经过此参数；
`?` = EVIDENCE_INSUFFICIENT（未在该路径上真实度量）。

### 时间轴类

- `speed`：single_pass ✓实测；fallback ✓实测（paths3 PATH_PARAMS speed=1.05）；
  merge_reencode ✓实测（合并后总帧数）；whole_file ✓实测
- `trim_head` / `trim_tail`：single_pass ✓；fallback ✓；whole_file
  —（整文件路径 `in_duration=None`，不裁窗口）
- `frame_dup`：single_pass ✓；fallback ✓（paths3 frame_dup=2）；whole_file ✓
- `frame_drop`：single_pass ✓；fallback ?；whole_file ?
- `reverse_loop`（段级 plan）：single_pass ✓；fallback ?；
  whole_file ✓实测（B37：整文件路径的 plan 级 rl）
- `normalize.fps`：single_pass ✓（FPS 矩阵 25/29.97/30/50/60/120）；
  fallback ?；whole_file ?

### 几何 / 事件类

- `zoom_drift`（窗口切段）：single_pass ✓实测（B55 前后各 24 组）；
  fallback — （降级路径走 `build_full_chain` 的**全长** zoompan，
  不经 `build_zoom_window_complex`）；whole_file — 同上
- `rotate_drift`（timeline enable）：single_pass ✓（B36 相位进入 FFmpeg）；
  fallback ?（全长 rotate，无 enable 窗口）；whole_file ?
- `lens_k1`：single_pass ✓；fallback ?；whole_file ?
- `asym_crop_*`：single_pass ✓（仅当 `norm_spec` 存在）；
  fallback/whole_file ?（`norm_spec` 未设时该参数不生成滤镜）
- `scale`：single_pass ✓；fallback ✓（几何链是公共 pre_chain）；whole_file ✓
- `mask_drift_*`：全部路径 INEFFECTIVE by design（出厂配置门关闭）

### 音频类

- `audio_pitch` / `audio_atempo` / `audio_fade` / `av_offset`：
  single_pass ✓；fallback ✓（paths3 av_offset/audio_fade/atempo）；whole_file ✓
- `audio_highpass` / `audio_lowpass` / `audio_eq`：single_pass ✓；
  fallback ?；whole_file ?
- `audio_noise_db`：仅 aggressive 预设启用 → 其余预设 INEFFECTIVE by design
- `noise`（视频噪点）：出厂 `video.noise.enable=false` → INEFFECTIVE by design
- 采样率：single_pass ✓（44100 / 48000 双矩阵，无硬编码 44100）；
  fallback ?；whole_file ?

### 编码类

- `normalize.video_codec`（NVENC ↔ CPU 回退）：single_pass ✓（B35 map 修复）；
  fallback ✓（`M5_encode_spec`）；merge_reencode ✓（`M8_merge_command`）
- `sc_threshold`：仅 libx264/libx265 分支 → NVENC 下 INEFFECTIVE by design
- AAC 码率：种子派生（`base+17`），single_pass ✓；分布覆盖见证据库

## 3. 路径专属能力（只在单一路径存在，必须单独取证）

- `build_zoom_window_complex`（窗口化推镜）→ 只有 `single_pass` / `segmented`
- `build_full_chain`（全长 zoompan/rotate/lens）→ 只有 `fallback` / `whole_file`
- `_merge_reencode` → 只有 `fallback` 之后
- 输入窗口帧精确 `-t`（B39 attempt_03）→ 只有 `fallback` 的 `process_clip`
- 快照级 `rl_*` 参数 → 只有 `whole_file`（B37）

## 4. 已知残留

- `merge_reencode` 相对 `single_pass` 稳定 **+1 帧**（Δ时长 0.033s @30fps），
  已记为 B39 的 PARTIAL；容差 2 帧内，两条路径各自与台账一致。
- 标 `?` 的格子是本轮**未在该路径上真实度量**的项，按规则记
  EVIDENCE_INSUFFICIENT，不得当作通过。
