《rewash-tool 当前 main 第二轮深度审计报告》
一、代码版本锁定
text

插入

复制
origin  https://github.com/baideji521/rewash-tool.git
git fetch origin main  →  FETCH_HEAD
local HEAD        = 08062c628b025dc847fe971933815da3a6fca277
origin/main       = 08062c628b025dc847fe971933815da3a6fca277
git diff HEAD origin/main = 空
git status --porcelain    = 空
本地工作树 完全等于 GitHub main，本轮所有结论都基于这些 blob：

text

插入

复制
video_rewash/core/segment.py         bc08258b3f9fbf8edf83b9006d126a365a197071
video_rewash/core/_graph.py          b0e85f8d96084b99c81e6e9ea8613c6a8d6bf909
video_rewash/video/filters.py        dbb884a1f937b0775c823c70609d1da271fb0ee2
video_rewash/core/randomizer.py      91b4af87263d4edb12fda0fda6a645fbc05da3e8
video_rewash/core/processor.py       8d483bae0ff59bc71572ba3995af3e5ae0973981
video_rewash/core/ffmpeg_runner.py   3ecd983538fdd0e844c8d597f5205308df621010
video_rewash/audio/audio_processor.py eff307832ad74dab2f936d445da98eea6ddd1948
video_rewash/core/normalize.py       5c90d25166adcc4ddf9342d2439dcb1d34439eb8
取证方法
用当前 main 的真实模块（segment.py 打印路径已确认为工作区文件）+ 真实 config.json/custom.json + 真实素材（21.108s / 29.97fps / 48000 Hz 音频），monkeypatch run_ffmpeg 只捕获命令不执行，然后手工执行捕获到的命令并 ffprobe。所有数字均为实测，未写入任何项目文件，未 commit / push。

当前 main 与第一轮那次运行（旧工作树）的差异 —— 已修复项
项	18:13 那次运行	当前 main	状态
cuts 基准	make_equal_cuts(duration, n)，trim 未生效	make_equal_cuts(duration−trim_head−trim_tail, n) + trim_head+cuts[i]	已修复
rotate 模型	线性漂移 +speed*deg2rad*t（无界）	双正弦 A1*sin(t/T1)+A2*sin(t/60)	已修复
av_offset 是否产生相对偏移	否（只同移窗口）	是（adelay / atrim）	已修复（但引入新问题，见四）
-itsoffset	build_audio_args 里存在	已删除	已修复
实测当前 main 的 seg0 分支即为证据：trim=start=0.859:end=4.101（=0.964−0.105 … 0.964+3.2422−0.105），rotate=(0.0980*...sin(2*PI*t/10.95)+-0.1623*...sin(2*PI*t/60.00))，...,afade=t=in:d=0.50,adelay=105:all=1[af_0]。

二、SPEED 审计
调用链逐层
text

插入

复制
randomizer.generate_snapshot        p["speed"] = round(uniform(0.97,1.04),4)          randomizer.py:220-222
segment._child_snapshot            seed=base+i*7919 → 每段重新随机 speed              segment.py:59-70
segment.process_single_pass        speed = max(0.1, p["speed"])                        segment.py:123
                                   exp_dur += seg_len / speed                          segment.py:124
                                   plan = generate_segment_plan(..., seg_len/speed)    segment.py:128
_graph.build_segment_branch  ④     chain.append(f"setpts={1.0/speed:.6f}*PTS")         _graph.py:102-103
_graph.build_segment_branch  ⑦     if norm_spec:
                                       tail += [f"setpts=N/{nf}/TB", f"fps={nf}", ...] _graph.py:134-137
                                   else:
                                       tail += [scale, setsar, format]                 _graph.py:141-143
segment.process_single_pass        concat=n=6:v=1:a=1                                  segment.py:151-159
1. speed 是否真的改变最终视频时长？
标准化开启时（switches.normalize=true，config 当前即为 true）：不改变。实测。

实测本次 6 段（当前 main，seed=1786869947670）：

text

插入

复制
seg   [vt](trim后)  [vb](drop+setpts speed后)  [vm](rotate+标准化尾后)
 0        97              96                        96
 1        97              96                        96
 2        97              96                        96
 3        97              97                        97
 4        97              97                        97
 5        97              97                        97
[vb] → [vm] 帧数完全不变，而 6 段 child_speed 分别为 0.9805 / 0.9979 / 1.0362 / 0.9921 / 1.0046 / 1.0166（差异达 ±3.6%）。段输出时长恒等于 帧数/30，与 speed 无关。

标准化关闭时：speed 生效（_graph.py:141-143 的 else 分支不含 setpts=N/TB）。 video_processor.build_command 的简单链路径（use_complex=False）：speed 也生效（video_processor.py:184-199 只加 setpts 再加 fps=30，fps 滤镜按 PTS 重采样 → 时长真变）。

→ 即 同一个参数在 4 条路径里有 2 种相反行为：

路径	条件	speed 是否生效
process_single_pass	normalize=on	否
process_single_pass	normalize=off	是
build_command 简单链（-vf）	任意	是
build_command 复杂链（走 build_segment_branch）	normalize=on	否
2. 具体是哪一个 setpts 覆盖的？
_graph.py:136 的 setpts=N/{nf}/TB。

它把 PTS 重写为 帧序号 / 目标帧率，丢弃输入 PTS，因此 _graph.py:103 的 setpts={1/speed}*PTS 结果被整体作废。后面的 fps={nf} 此时输入已是精确 nf fps，为 1:1 直通，不再补救。

3. 删除/移动 normalization 的 setpts，其它事件的时间坐标会不会变？
会，必须同步处理。当前依赖 setpts=N/nf/TB 的隐含前提逐个列出：

frame_drop：select 按帧号 n 判断，与 PTS 无关 → 不受影响；但 build_frame_drop_expr 自带的 setpts=N/FRAME_RATE/TB（filters.py:436）负责删帧后填补 PTS 空洞，必须保留。
rotate：enable='between(t,start/speed,(start+dur)/speed)'（_graph.py:126-129）。窗口值已按 /speed 换算到变速后时间轴。当前 setpts=N/nf/TB 是在 rotate 之后执行的（_graph.py:131 先 append rot，:136 再 append setpts，同一条 chain 内顺序=rotate→setpts），所以 rotate 看到的是变速后的 t。移除末尾 setpts 不改变 rotate 的输入 → 不受影响。
zoom 窗口：build_zoom_window_complex 用 trim=duration/start 切段，输入是 z_in = {start/speed, dur/speed}（_graph.py:112-113），同样在变速后空间，且发生在末尾 setpts 之前 → 不受影响。
frame_dup：build_frame_dup_complex(..., seg_len_in/speed, ...)（_graph.py:153），t_pos = duration*pos 用的是变速后时长；而它作用在 [vm]（末尾 setpts 之后）。当前 [vm] 的时间轴是 帧数/30，与传入的 seg_len_in/speed 不一致 → 当前就已经错位（见第七节）。移除末尾 setpts 反而让二者一致。
concat：需要各输入 PTS 从 0 起、时基一致。移除 setpts=N/nf/TB 后必须保留 fps={nf} 来保证 CFR 与时基统一，并在段首保留 setpts=PTS-STARTPTS。
结论：唯一真正依赖它的是"把变速后的可变 PTS 重整为 CFR"，而这件事 fps 滤镜自己就能做。

4. 正确方案
当前时间轴（normalize=on）
text

插入

复制
源(29.97fps) --trim[t0,t1]--> 段内 PTS 0..(t1-t0)
  --select(丢帧)+setpts=N/FRAME_RATE/TB--> PTS 0..(N-1)/29.97
  --setpts=1/speed*PTS--> PTS 0..(N-1)/29.97/speed        ← 变速在此生效
  --rotate(enable 按变速后 t)-->
  --setpts=N/30/TB--> PTS 0..(N-1)/30                     ← ★变速被丢弃
  --fps=30--> 1:1 直通
段输出时长 = N/30      （与 speed 无关）
正确时间轴
text

插入

复制
源(29.97fps) --trim[t0,t1]--> PTS 0..(t1-t0)
  --select+setpts=N/FRAME_RATE/TB--> PTS 0..(N-1)/29.97
  --setpts=1/speed*PTS--> PTS 0..(N-1)/29.97/speed
  --rotate(enable 按变速后 t)-->
  --zoom 窗口(变速后坐标)-->
  --fps=30--> 按 PTS 重采样为 CFR 30fps，帧数 ≈ round((t1-t0)/speed*30)
  --format/setsar-->
  --frame_dup(t_pos 用 (t1-t0)/speed)-->
段输出时长 = (t1-t0)/speed + dup_n/30    （speed 真正生效）
修改后的滤镜顺序（_graph.build_segment_branch 第 ⑦ 步）
只需把 setpts=N/{nf}/TB 从标准化尾中移除，其余顺序不动：

text

插入

复制
① rl 事件（split/trim/concat）
② pre_chain（几何+畸变+颜色，仅整片/降级路径）
③ frame_drop:  select='not(...)' , setpts=N/FRAME_RATE/TB      ← 保留（补空洞）
④ speed:       setpts={1/speed}*PTS                             ← 保留
⑤ zoom 窗口（trim 切段 + zoompan + concat）
⑥ rotate（timeline enable，按变速后 t）
⑦ 标准化尾:   fps={nf} , format={pix} , setsar=1                ← 删掉 setpts=N/{nf}/TB
                （+ geq）
⑧ frame_dup（tpad + concat）
风险点（必须实测确认，不能只改）：fps 滤镜在 speed<1（PTS 拉长）时会复制帧、speed>1 时会丢帧，与 frame_drop 的删帧效果可能相互抵消或叠加。这需要 FFmpeg 实测，见第十节。

修改后 exp_dur 计算公式
text

插入

复制
对每段 i：
    win_i    = t1_i - t0_i                        # 输入侧窗口长度（秒）
    n_in_i   = round(win_i * src_fps)             # 输入帧数
    drop_i   = len(frame_drop_positions(...))     # 实际删帧数（已可精确取得）
    rl_i     = (repeats_i - 1) * rl_seg_len_i     # 仅 mode=loop；输入空间
    dup_i    = frame_dup_i  if win_i/speed_i > 1.0 else 0

    dur_i    = (win_i + rl_i - drop_i/src_fps) / speed_i + dup_i / out_fps

exp_dur = Σ dur_i
并且 process_single_pass:210 的 ±10% 阈值应收紧到 ±2%（见第九节）。

三、AUDIO PITCH 审计
当前代码（filters.py:476-483，当前 main 原文）
Python

插入

复制
pitch = float(p.get("audio_pitch", 0.0))
if abs(pitch) > 0.01:
    rate = 2.0 ** (pitch / 12.0)
    # asetrate 变调 + aresample 回原采样率 + atempo 补偿时长
    sr = 44100
    filters.append(f"asetrate={int(sr * rate)}")
    filters.append(f"aresample={sr}")
    filters.append(f"atempo={1.0 / rate:.6f}")
build_audio_filter(snap) 只接受 snap，签名里没有 sample_rate，调用点也没有传：

_graph.build_segment_audio:202 → build_audio_filter(snap)
audio_processor.build_audio_args:33 → build_audio_filter(snap)
video_processor.build_command:150 → build_audio_filter(snap)
而真实采样率早已被探测到：ffmpeg_runner.probe_media 返回 a_sample_rate（ffmpeg_runner.py:244），实测本素材 "a_sample_rate": 48000。信息有，但没往下传。

实测矩阵（5.000s 正弦输入，pitch=1.158 → rate=1.069176）
text

插入

复制
线上链: asetrate=47150,aresample=44100,atempo=0.935299

  sr_in     baseline      线上链      按真实 sr 修正      线上链/baseline    sr_in/44100
  44100     5.000000     4.998435     4.998435            0.999687          1.000000
  48000     5.000000     5.441723     4.999521            1.088345          1.088435   ← 拉长 8.8%
  32000     5.000000     3.626281     4.997469            0.725256          0.725624   ← 缩短 27.5%
  22050     5.000000     2.497098     4.998458            0.499420          0.500000   ← 缩短 50%
放大倍率恒等于 sr_in / 44100。 这不是近似，是精确关系。48k 素材拉长 8.84%，32k 素材缩短 27.5%，22.05k 缩短 50%。

audio_atempo + pitch 同时存在（48k，atempo=0.98269，pitch=1.158）
text

插入

复制
baseline                = 5.000000
线上链                  = 5.534195   (×1.106839)   ← 期望只应 ×1.017615 (=1/atempo)
按真实 sr 修正          = 5.084500   (×1.016900)   ← 与期望 ×1.017615 一致（误差 0.07%）
→ audio_atempo 的时长效果本身是对的；被污染的是 pitch 分支。

修改方案
build_audio_filter(snap, sample_rate=None) 增加形参；sr = int(sample_rate) if sample_rate and sample_rate > 0 else 44100（保留兜底，不删功能）。
调用链传参（三处，都能拿到 media_info）：
segment.process_single_pass 已有 media_info → 传给 build_segment_audio(..., sample_rate=media_info["a_sample_rate"]) → 转给 build_audio_filter。
video_processor.build_command 已有 media_info → 同理，并传给 build_audio_args(snap, has_audio, audio_codec, sample_rate=...)。
audio_processor.build_audio_args 增加形参并透传。
不要用固定中间采样率：asetrate={int(sr*rate)} → aresample={sr} → atempo={1/rate}。三处都用同一个 sr。
atempo 单滤镜范围 0.5~2.0。1/rate 在 pitch ∈ [−12, +12] 半音内落在 [0.5, 2.0]，当前预设 pitch ∈ [0.8, 1.3] 完全安全；但建议加 clamp 与拆链兜底（不改行为，只防越界）。
修改后预期行为
pitch 仍改变音高（asetrate 比例不变，仍为 rate）。
时长只受 audio_atempo 影响，× 1/audio_atempo。
44.1k / 48k / 32k / 22.05k 全部不产生额外膨胀（实测 fix 列全部 ≈ 4.9985~4.9995，偏差 <0.06%）。
四、AV_OFFSET 审计
调用链
text

插入

复制
randomizer.generate_snapshot:268-273     p["av_offset"] = ±uniform(0.08,0.15)  （每段 child 重新随机）
segment.process_single_pass:125-127      av = p["av_offset"]
                                         t0 = max(0.0, seg_start - av)
                                         t1 = max(t0 + 0.05, seg_end - av)
segment.process_single_pass:135          [gN]trim=start=t0:end=t1              ← 视频
segment.process_single_pass:145          [0:a]atrim=start=t0:end=t1            ← 音频（同一个 t0/t1）
_graph.build_segment_audio:202-205       → build_audio_filter(snap)
filters.build_audio_filter:508-513       av>=0.02  → adelay={av*1000}:all=1
                                         av<=-0.02 → atrim=start={-av}, asetpts=PTS-STARTPTS
segment.process_single_pass:151-154      concat=n=6:v=1:a=1
实测当前 main 生成的 6 段音频尾部：

text

插入

复制
seg0 av=+0.105 → ...,afade=t=in:d=0.50,adelay=105:all=1[af_0]
seg1 av=-0.119 → ...,afade=t=in:d=0.50,atrim=start=0.119,asetpts=PTS-STARTPTS[af_1]
seg2 av=+0.121 → ...,adelay=121:all=1[af_2]
seg3 av=-0.122 → ...,atrim=start=0.122,...[af_3]
seg4 av=-0.149 → ...,atrim=start=0.149,...[af_4]
seg5 av=-0.111 → ...,atrim=start=0.111,...[af_5]
1. 是否真的产生音画相对偏移？
是。由 adelay / atrim 产生，与 trim 窗口平移无关。

数学推导（段内输出时刻 τ）：

text

插入

复制
窗口平移对视频、音频完全相同 → 在相对同步上互相抵消，贡献 0
av>0:  视频 τ ↔ 源 (seg_start-av)+τ
       音频 τ ↔ 源 (seg_start-av)+(τ-av) = seg_start-2av+τ
       → 音频内容比视频内容早 av → 音频滞后视频 av        ✅ 相对偏移 = av
av<0（|av|=a）:
       视频 τ ↔ 源 (seg_start+a)+τ
       音频 τ ↔ 源 (seg_start+a)+(τ+a) = seg_start+2a+τ
       → 音频内容比视频内容晚 a → 音频超前视频 a          ✅ 相对偏移 = -a
正负语义已统一（正=音频滞后，负=音频超前），量级都是 |av|，不存在双倍计算。

2. 窗口平移现在是纯副作用
t0 = seg_start - av 已经不承担偏移功能，但仍在改变每段"取源哪一段"。由于 av 是每段独立随机，段边界不再连续。实测当前 main 的 6 个窗口：

text

插入

复制
seg0  0.859 → 4.101
seg1  4.325 → 7.567     seg0尾 4.101 vs seg1头 4.325  →  跳过源内容 0.224s
seg2  7.327 → 10.570    seg1尾 7.567 vs seg2头 7.327  →  重复源内容 0.240s
seg3 10.813 → 14.055    seg2尾10.570 vs seg3头10.813  →  跳过 0.243s
seg4 14.082 → 17.324    seg3尾14.055 vs seg4头14.082  →  跳过 0.027s
seg5 17.286 → 20.528    seg4尾17.324 vs seg5头17.286  →  重复 0.038s
而名义切点是连续的（0.964 + k×3.2422）。5 个段边界全部出现内容跳帧或重复，这是可见的观感缺陷，且完全由这行平移造成。

3. "日志说 adelay 但 filtergraph 没有 adelay"
当前 main 已不存在这个问题。 randomizer.log_parameter_calibration:351-353 打印 adelay=105，filters.py:509-510 确实生成 adelay=105:all=1，二者一致。

但 segment.process_single_pass 的 docstring（segment.py:81-82）仍写着：

text

插入

复制
av_offset 等效：旧方案 -itsoffset 作用于整输入（音视频同步平移，
相对同步不变），此处两支路 trim 窗口同步平移，语义一致。
这段注释已经过时并且误导——真正的偏移来自 adelay/atrim，窗口平移只剩副作用。属 P2。

4. 正确实现
text

插入

复制
1. 删除 t0/t1 的 av 平移，恢复 t0 = seg_start, t1 = seg_end
   → 段边界连续，无跳帧/重复，无首段钳位
2. 相对偏移全部交给音频侧滤镜（保持现有 adelay / atrim 实现）
3. 时长中性化（关键）：
   av > 0:  adelay={av*1000}:all=1  之后追加  atrim=end={win_len}
   av < 0:  atrim=start={-av},asetpts=PTS-STARTPTS 之后追加  apad=whole_dur={win_len}
   → 音频段长恒等于 win_len，与视频段长一致 → concat 不再产生空洞
5. 首段 max(0.0, seg_start-av) 是否造成时间损失？
当前 main 下不触发，但是潜在缺陷。

本次 seg0：seg_start = trim_head + 0 = 0.964，av = +0.105 → 0.964-0.105 = 0.859 > 0，未被钳位（实测 trim=start=0.859）。

触发条件：trim_head < av。当前预设 trim ∈ [0.5, 1.2]、av ∈ [0.08, 0.15] → 不会触发。但 GUI 允许 trim 设为 0~0，此时 seg_start = 0，av>0 必然钳位，首段直接短掉 av 秒（第一轮那次运行就是这种情况，实测短了 0.105s）。属 P1 潜在缺陷。

6. 最终定性
问题	当前 main 答案
av_offset 是否改变 A/V 相对同步	是，偏移量 = av，方向正确
av_offset 是否改变 duration	是，且不应该：adelay 使该段音频 +av（实测 +0.104989），atrim 使该段音频 −|av|（实测 −0.119000）；进而通过 concat 影响视频长度
正负语义是否统一	是
五、REVERSE LOOP 审计
调用链
text

插入

复制
randomizer.generate_segment_plan:509-520   rl.enable + rng.random()<prob(0.4)
                                           mode = choice(["reverse","loop"])
                                           pos_rel = uniform(0.15,0.85)
                                           seg_len = uniform(0.1,0.2)
                                           repeats = choice([2,3])
                                           ← rng 用 seed+seg_idx*104729，掷骰参数来自 seg_len/speed（输出空间）
_graph.build_segment_branch:79-86          if rl.mode and seg_len_in >= 2.0:
                                               build_reverse_loop_complex(_rl_snap(p,rl), seg_len_in, False, ...)
                                                                                    ↑ 输入空间 seg_len
filters.build_reverse_loop_complex:348-372 t1 = pos_rel * seg_dur ; t2 = t1 + d
                                           split=3 → A/B/C → concat=n=repeats+2
_graph.build_segment_audio:174-201         同一公式，同一 t1/t2（音频内联，防标签碰撞）
_graph.rl_extra_seconds:32-38              loop → (repeats-1)*seg_len/max(0.1,speed)
segment.process_single_pass:130            exp_dur += rl_extra_seconds(plan, speed)
实测（当前 main，三个真实触发的 seed）
用例	seed	mode	repeats	seg_len	视频增量	音频增量	rl_extra_seconds()
reverse	1786869947673	reverse	2	0.119	0 帧 = 0.0000s	0.000000s	0.000000
loop×2	1786869947680	loop	2	0.121	4 帧 = 0.1335s	0.121000s	0.124434
loop×3	1786869947700	loop	3	0.115	6 帧 = 0.2002s	0.230000s	0.225093
（视频增量 = [vt0]→[vt_0] 帧数差，@29.97；音频增量 = [ab_0]→[at_0] 精确 wav 时长差）

实测生成的切拼结构（loop×3，逐字）：

text

插入

复制
[vt0]split=3[r1_0][r2_0][r3_0]
[r1_0]trim=end=1.912,setpts=PTS-STARTPTS[vA_0]
[r2_0]trim=start=1.912:end=2.027,setpts=PTS-STARTPTS[vB_0]
[r3_0]trim=start=2.027,setpts=PTS-STARTPTS[vC_0]
（视频侧 repeats>1 的 split/concat 由 build_reverse_loop_complex 内部生成）
[ab_0]asplit=3[s1_0][s2_0][s3_0]
[s1_0]atrim=end=1.912,asetpts=PTS-STARTPTS[aA_0]
[s2_0]atrim=start=1.912:end=2.027,asetpts=PTS-STARTPTS[aB_0]
[s3_0]atrim=start=2.027,asetpts=PTS-STARTPTS[aC_0]
[aB_0]asplit=3[ab0_0][ab1_0][ab2_0]
[aA_0][ab0_0][ab1_0][ab2_0][aC_0]concat=n=5:v=0:a=1[at_0]
切点 1.912 / 2.027 视频音频完全相同，n=5 = repeats+2 正确。

逐条回答
视频增加多少秒：loop 时 (repeats-1)×seg_len，但被帧量化。loop×2：0.121×29.97=3.63 → 取 4 帧 = 0.1335s（多 0.0125s）；loop×3：0.230×29.97=6.89 → 取 6 帧 = 0.2002s（少 0.0298s）。reverse 时 0。
音频增加多少秒：精确 (repeats-1)×seg_len（0.121 / 0.230）。reverse 时 0。
video/audio 增量是否严格一致：不一致。实测差 +0.0125s（loop×2）/ −0.0298s（loop×3）。每个 loop 事件引入最多约 ±0.03s 的音画漂移，且方向随机。属 P1。
repeats=3 是否真增加 2×seg_len：音频是（0.230 = 2×0.115 精确）；视频约是，帧量化后 0.2002s（差 0.0298s）。
speed≠1 时应按哪个时间轴算：当前 rl_extra_seconds 除以 speed（loop×2：0.121/0.9724=0.1244），但 build_reverse_loop_complex 拿的是输入空间 seg_len_in，且 speed 本身已被覆盖（第二节）→ 实际输出增量 = 帧数/30 = 0.1333。三个数互不相等。正确做法：rl 事件发生在变速之前（输入空间），所以输出增量 = (repeats-1)*rl_seg_len / speed；这个公式只有在 speed 被修好之后才成立。另外 pos_rel 是按输出空间 seg_len/speed 掷的骰，却乘在输入空间 seg_len_in 上（filters.py:349），空间混用（对随机位置无实际危害，但应统一）。
exp_dur 是否与真实输出一致：不一致。loop×2 段：rl_extra=0.1244，实际视频 0.1333，实际音频 0.121。三者都不同。
六、FRAME DROP 审计
调用链
text

插入

复制
randomizer.generate_snapshot:245-250        p["frame_drop_on"] = enable and rng.random()<prob
randomizer.generate_segment_plan:503-506    _window(vcfg["frame_drop"], 1.0, 2.0, 5.0)
                                            ← 窗口在【输出空间】：seg_len 参数 = seg_len_in/speed
_graph.build_segment_branch:96-98           frame_drop_chain(snap, config, seg_idx,
                                                             int(seg_len_in * eff_fps), fwin, eff_fps)
                                                                  ↑ 输入空间 seg_len × 目标帧率 30
_graph.frame_drop_chain:54-60               rng = Random(seed + 29 + seg_idx)
                                            window = (start*eff_fps, (start+dur)*eff_fps)
filters.frame_drop_positions:398-428        每 interval(10~25) 帧删 1 帧，cap = n_frames//50
filters.build_frame_drop_expr:431-436       select='not(eq(n,..)+..)',setpts=N/FRAME_RATE/TB
逐条回答
1. n_frames 是否正确？——不正确。

int(seg_len_in * eff_fps) = int(3.2422 × 30) = 97。 真实帧数：源 29.97fps，窗口 3.2422s → 实测 [vt] = 97 帧。数值巧合相符，因为 29.97×3.2422=97.17、30×3.2422=97.27，都截断到 97。 但公式在概念上是错的：用目标帧率 30 乘输入空间时长。当源为 60fps、25fps、23.976fps 时会显著偏离。且 cap = n_frames//50 与 w1 = n_frames-3 都依赖它 → 删帧上限和窗口右边界会跟着偏。

2. 窗口是 input time 还是 output time？——混用。

_window() 收到的 seg_len 是输出空间（seg_len_in/speed，segment.py:128），所以 fwin.start/dur 是输出空间秒；frame_drop_chain 却直接 × eff_fps 当作输入侧帧号用（_graph.py:57-59）。speed≠1 时窗口位置有 (1-speed) 比例的偏移。本次 seg2 speed=1.0362 → 窗口偏移约 3.6%。

3. 发生在 speed 之前还是之后？——之前。

_graph.py:99-103：chain = [frame_drop_expr, setpts=1/speed*PTS]，select 在前。这是正确的（按帧号删帧不受 PTS 影响）。

4. 删除 N 帧后 PTS 是否连续？——是。

build_frame_drop_expr 附带 setpts=N/FRAME_RATE/TB（filters.py:436）按新帧序号重排 → 无空洞。实测 [vb] 帧数 = [vt] − 删帧数，PTS 连续。

5. CFR normalization 是否再次复制/删除帧？——当前不会。

setpts=N/30/TB 之后流已是精确 30fps，fps=30 为 1:1 直通。实测 [vb]→[vm] 帧数不变（96→96、97→97）。 ⚠️ 一旦按第二节修好 speed（删掉 setpts=N/30/TB），fps=30 就会真的重采样，届时会与 frame_drop 相互作用——这是必须实测确认的点。

6. 时长计算是否准确？——不准确。 exp_dur 完全不含 frame_drop（见第九节）。

时间轴表（当前 main，seed=1786869947670，实测）
seg	输入窗口(s)	n_frames 传入值	实测 [vt]	删帧位置	删帧数	实测 [vb]	[vm]	窗口(输出空间 s)
0	0.859→4.101 (3.242)	97	97	n=32	1	96	96	0.986→1.956
1	4.325→7.567 (3.242)	97	97	n=78	1	96	96	2.270→3.056
2	7.327→10.570 (3.243)	97	97	n=60	1	96	96	1.985→2.653
3	10.813→14.055 (3.242)	97	97	窗口内无落点	0	97	97	0.024→0.744
4	14.082→17.324 (3.242)	97	97	未触发	0	97	97	—
5	17.286→20.528 (3.242)	97	97	未触发	0	97	97	—
Σ			582		3	579	579	
七、FRAME DUP 审计
调用链
text

插入

复制
randomizer.generate_snapshot:223-225     p["frame_dup"]     = randint(1,3)
                                         p["frame_dup_pos"] = uniform(0.25,0.75)
_graph.build_segment_branch:149-161       if frame_dup>0 and seg_len_in/speed > 1.0:
                                              p2["_fps"] = eff_fps
                                              build_frame_dup_complex({"params":p2},
                                                                      seg_len_in / speed,   ← ★
                                                                      cur.strip("[]"))
                                          作用对象 cur = [vm_i]（标准化尾之后）
filters.build_frame_dup_complex:439-460   t_pos = duration * pos
                                          [x]split=2 → trim=end=t_pos / trim=start=t_pos
                                                      + tpad=start={n}:start_mode=clone
                                          → concat=n=2:v=1:a=0
逐条回答
1. 增加的帧数是否准确？——准确。 实测 [vm]→[v]：

seg	frame_dup	[vm]	[v]	实测增量
0	2	96	98	+2
1	1	96	97	+1
2	3	96	99	+3
3	2	97	99	+2
4	1	97	98	+1
5	1	97	98	+1
Σ	10	579	589	+10 = 0.3333s @30fps
2. 增加的是哪一帧？ tpad=start=n:start_mode=clone 克隆后半段的第一帧，即 t_pos 处那一帧，重复 n 次（≈冻结 n/30 秒）。

3. t_pos = duration * pos 里的 duration 到底是什么？—— 明确回答：seg_len_in / speed，即"trim 后的输入窗口长度 ÷ speed"。

_graph.py:153 传入 seg_len_in / speed。而 seg_len_in 就是 seg_end - seg_start（segment.py:121），是已 trim 的输入窗口长度，不含 av 平移造成的钳位差、不含 frame_drop 缩短、不含 rl 增量。

所以它不是四个选项中任何一个纯粹的量：

不是原始 segment duration（源全长/6）
不是"speed 后 duration"的真实值（因为 speed 实际不生效）
是 trim 后 duration 除以一个不生效的 speed
不是 output timeline duration（真实值 = [vm]帧数/30）
实测量化误差（seg0）：传入 3.2422/0.9805 = 3.3066，t_pos = 3.3066×0.355 = 1.174（实测 trim=end=1.174）。而 [vm] 的真实时长 = 96/30 = 3.2000，正确 t_pos 应为 3.2000×0.355 = 1.136。位置偏移 0.038s ≈ 1.1 帧。

seg2（speed=1.0362，删 1 帧）：传入 3.2426/1.0362=3.1294，t_pos=1.126；真实 [vm]=96/30=3.2000，应为 1.136 → 偏移 −0.010s。

4. speed≠1 时是否位置偏移？——是。 偏移量 = (seg_len_in/speed − 实际[vm]时长) × pos，随 speed 偏离 1 线性增大。当前 6 段偏移 −0.010 ~ +0.038s。

5. dup 后 normalization 是否改变实际增加量？——不会。 frame_dup 是链尾第 ⑧ 步，在标准化尾（第 ⑦ 步）之后，其后无任何 fps/setpts。实测增量精确等于 frame_dup。

6. exp_dur 是否计算 frame_dup 增量？——不计算。 segment.py:124,130 只累加 seg_len/speed 和 rl_extra_seconds。10 帧 = 0.3333s 完全遗漏。

八、CONCAT 审计
数学模型
concat=n=N:v=1:a=1 对第 i 段：

text

插入

复制
seg_dur_i = max(v_dur_i, a_dur_i)
下一段 PTS 起点 = Σ_{k<i} seg_dur_k
当 a_dur_i > v_dur_i 时，第 i 段视频末尾留下时间空洞

text

插入

复制
gap_i = a_dur_i - v_dur_i
该空洞由后续 CFR 编码器（h264_nvenc，-r 缺省按输入 CFR）复制帧填补：

text

插入

复制
最终视频帧数  N_out = Σ v_frames_i + round( (Σ_{i<N-1} gap_i) × out_fps )
最终视频时长  = N_out / out_fps
最终音频时长  = Σ a_dur_i
format.duration = max(视频时长, 音频时长)
末段的 gap_{N-1} 不被填补（流已结束），这是公式里排除最后一项的原因。

实测验证（当前 main）
seg	v_dur = 帧/30	a_dur（精确）	gap = a − v
0	98 帧 = 3.2667	3.675397	+0.4087
1	97 帧 = 3.2333	3.422313	+0.1890
2	99 帧 = 3.3000	3.491542	+0.1915
3	99 帧 = 3.3000	3.438503	+0.1385
4	98 帧 = 3.2667	3.403764	+0.1371
5	98 帧 = 3.2667	3.365011	+0.0983
Σ	589 帧 = 19.6333	20.796530	+1.1631
代入公式：

text

插入

复制
Σ_{i<5} gap_i = 1.1631 - 0.0983 = 1.0648
N_out = 589 + round(1.0648 × 30) = 589 + 32 = 621
视频时长 = 621/30 = 20.700000
实测 ffprobe：video duration = 20.700000，nb_frames = 621。公式精确成立。 audio = 20.796009，format = 20.796009 = max(20.700, 20.796) ✓

修复 audio pitch 后 concat 膨胀是否自动消失？
大部分消失，但不会完全消失。实测四组对照（同一 filter_complex，仅替换字符串，未改代码）：

text

插入

复制
exp_dur(代码自算) = 19.3691 ; 源 = 21.108 ; 支路视频合计 = 589 帧 = 19.6333

A  当前 main                      video=20.700000s (621帧)  audio=20.796009s  format=20.796009s
B  仅修 sr（asetrate/aresample 用 48000） video=19.766667s (593帧)  audio=19.568000s  format=19.766667s
C  仅去掉 adelay/atrim            video=20.866667s (626帧)  audio=21.070998s  format=21.070998s
D  修 sr + 去掉 adelay/atrim      video=19.666667s (590帧)  audio=19.601000s  format=19.666667s
B（只修 sr）：膨胀从 +32 帧 (1.067s) 降到 +4 帧 (0.133s)。残留来自 adelay：seg0 +0.105 与 seg2 +0.121 共 0.226s 静音使这两段音频仍长于视频。
D（sr + 时长中性化 av）：膨胀只剩 +1 帧 (0.033s)，属正常量化残差。
C（只去 av 滤镜、不修 sr）：膨胀反而增大到 +37 帧 —— 证明 sr bug 是主因，atrim 的负偏移原本还在意外地抵消一部分膨胀。
结论：修 audio pitch 是必要条件，但不是充分条件。要彻底消除，还必须让 adelay/atrim 时长中性（第四节第 4 点：adelay 后接 atrim=end=win_len，atrim 后接 apad=whole_dur=win_len）。

九、EXP_DUR 审计
当前存在两套互不相干的期望值，都错
① segment.process_single_pass 内部（用于进度条 + ±10% 时长门禁）

Python

插入

复制
exp_dur += seg_len / speed              # segment.py:124
exp_dur += rl_extra_seconds(plan, speed)  # segment.py:130
...
if out_dur < exp_dur*0.90 or out_dur > exp_dur*1.10:  # segment.py:210
实测 exp_dur = 19.3691，实际 format.duration = 20.7960 → +7.36%，落在 ±10% 内，门禁放行。

② processor.process_one 质检（quality_check.check_output，容差 25%）

Python

插入

复制
if seg_count > 1:
    exp_dur = duration                              # processor.py:257  ← 完全不扣 trim
else:
    exp_dur = duration - trim_head - trim_tail      # processor.py:259-262
exp = {"duration": exp_dur / speed * 0.98}          # processor.py:264-266
...
elif exp_dur > 0.5 and abs(dur-exp_dur)/exp_dur > 0.25:  # quality_check.py:61
分段路径下：21.108 / 0.9805 × 0.98 = 21.0977，实际 20.7960 → 偏差 −1.43%，通过。

而按当前代码本应期望的值是 (21.108−0.964−0.691)/0.9805×0.98 = 19.4433，实际 20.7960 → 偏差 +6.96%，仍在 25% 内。

→ 两套门禁同时失效，其中 ② 因为漏扣 trim 而多出 1.65s 余量，恰好把膨胀"吃"进去了。 这正是"错误输出被合法化"的机制。

逐项对照表
参数	实际影响 duration	实测量（本次）	exp_dur ① 是否计算	quality_check ② 是否计算	是否正确
trim_head/trim_tail	是，−1.655s	已从 cuts 扣除，seg_len 已含	✅ 隐含在 seg_len	❌ 分段路径 exp_dur=duration 未扣	① 正确 / ② 错
av_offset（窗口平移）	段间跳帧/重复，时长中性	5 处边界不连续	❌	❌	错
av_offset（adelay/atrim）	是，音频 ±|av| → 经 concat 影响视频	+0.105/+0.121/−0.119/−0.122/−0.149/−0.111	❌	❌	错
frame_drop	是，−3 帧 = −0.100s	3 帧	❌	❌	错
frame_dup	是，+10 帧 = +0.333s	10 帧	❌	❌	错
speed	否（被覆盖，normalize=on）	0	✅ 除以 speed	✅ 除以 speed	两者都错（算了但实际无效）
reverse_loop	loop 时 +帧量化的 (n−1)·d	本 seed 0 次	✅ rl_extra_seconds（除以无效的 speed）	❌	不准
audio_atempo	是，音频 ×1/atempo	×1.0176 等	❌	❌	错
audio_pitch	是，音频 ×(sr_in/44100)	×1.088435	❌	❌	错（主因）
29.97 → 30 帧率重解释	是，−0.021s/21s	−0.021s	❌	❌	错（量小）
concat CFR 补帧	是，+32 帧 = +1.067s	32 帧	❌	❌	错（最大项）
应有的公式与门禁
text

插入

复制
exp_dur = Σ_i [ (win_i + rl_i − drop_i/src_fps) / speed_i + dup_i/out_fps ]

audio_dur_i = win_i / audio_atempo_i          （pitch 修复后不再引入因子）
                + (av_i > 0 ? 0 : 0)          （av 时长中性化后为 0）

不变式（必须成立）：|audio_dur_i − video_dur_i| ≤ 1/out_fps   对每个 i
门禁：±2%（当前 ±10% / ±25% 都太松）
质检 ②：分段路径必须改成 duration − trim_head − trim_tail
十、当前 main 真实问题清单
P0 —— 直接导致时长 / 音画 / 核心功能错误
P0-1　audio pitch 硬编码 44100，按输入采样率成比例改变时长
文件/函数：video_rewash/video/filters.py → build_audio_filter()，第 476-483 行
当前逻辑：rate = 2**(pitch/12)；sr = 44100；asetrate=int(sr*rate) → aresample=sr → atempo=1/rate
实际错误：输出时长被乘以 sr_in / 44100。实测：48k → ×1.088345；32k → ×0.725256；22.05k → ×0.499420；44.1k → ×0.999687
为什么错：asetrate 是"重新解释采样率"，其时长效应是 sr_in / asetrate_value。代码假定 sr_in == 44100，于是 atempo=1/rate 只抵消了 rate，残留 sr_in/44100
修改方案：build_audio_filter(snap, sample_rate=None)；sr = int(sample_rate) if sample_rate and sample_rate>0 else 44100；asetrate={int(sr*rate)} / aresample={sr} / atempo={1/rate}。调用链传参：probe_media()["a_sample_rate"] → process_single_pass → build_segment_audio → build_audio_filter；build_command → build_audio_args → build_audio_filter
修改后预期：pitch 仍改音高；时长只受 audio_atempo 影响（实测 sr-correct 48k = 5.084500 vs 期望 5.000×1.017615 = 5.088，误差 0.07%）
需要新增测试：test_audio_pitch_duration_invariance —— 对 44100/48000/32000/22050 四种采样率，用 sine 生成 5s 输入，跑 build_audio_filter 生成的链，断言输出时长 / (5.0/audio_atempo) 偏差 < 1%
P0-2　concat 段间空洞 → CFR 补帧 → 视频时长膨胀
文件/函数：video_rewash/core/segment.py → process_single_pass()，第 151-159 行（concat=n={n}:v=1:a=1）
当前逻辑：各段视频与音频分别构建，长度不做对齐，直接 concat
实际错误：实测 Σv = 589 帧 = 19.6333s，Σa = 20.7965s，最终 video = 621 帧 = 20.7000s（+32 帧 = +1.0667s）
为什么错：concat 按 max(v_i, a_i) 推进下一段起点，视频侧留空洞，CFR 编码器复制帧填补。公式：N_out = Σv_frames + round(Σ_{i<N-1}(a_i−v_i)×out_fps)，实测 621 = 589 + 32 精确成立
修改方案：让每段 a_dur_i == v_dur_i。①先修 P0-1；②adelay 后追加 atrim=end={win_len}，atrim=start 后追加 apad=whole_dur={win_len}；③保留 concat 结构不动
修改后预期：实测对照组 D（sr 修复 + av 时长中性）膨胀降至 +1 帧 (0.0333s)
需要新增测试：test_concat_no_padding —— 逐支路测量 [v_i] 帧数与 [af_i] 时长，断言每段 |a_i − v_i| ≤ 1/fps；再断言最终 nb_frames == Σ v_frames
P0-3　质检期望时长在分段路径漏扣 trim，把膨胀合法化
文件/函数：video_rewash/core/processor.py → process_one()，第 256-266 行
当前逻辑：if seg_count > 1: exp_dur = duration（不扣 trim_head/trim_tail），再 /speed*0.98，交给 check_output（容差 25%）
实际错误：实测期望 21.0977，实际 20.7960 → 偏差 −1.43% 通过；而真实应期望 19.4433 → 实际偏差 +6.96%
为什么错：分段路径当前（segment.py:250-256）已经扣了 trim，质检却按未扣 trim 计算，凭空多出 1.655s 容差，正好覆盖膨胀量
修改方案：分段路径改为 exp_dur = max(0.5, duration - trim_head - trim_tail)，与 segment.make_equal_cuts 的 effective_duration 保持同一来源；容差从 25% 收到 5%
修改后预期：本次输出 20.7960 vs 期望 19.4433 → +6.96% > 5% → 质检报警，膨胀不再被放行
需要新增测试：test_quality_check_expect_matches_cuts —— 断言 processor 计算的 exp_dur 与 segment.process_segmented 使用的 effective_duration 相等
P0-4　±10% 时长门禁过松且基于错误的 exp_dur
文件/函数：video_rewash/core/segment.py → process_single_pass()，第 119-130 行 + 第 210 行
当前逻辑：exp_dur = Σ(seg_len/speed) + Σ rl_extra；if out_dur < exp*0.90 or > exp*1.10: 失败降级
实际错误：exp_dur=19.3691，实际 20.7960（+7.36%）→ 放行
为什么错：exp_dur 缺 frame_drop、frame_dup、audio 因子、concat 补帧、av 时长项；且除以一个实际不生效的 speed。±10% 大于这些误差之和
修改方案：按第九节公式重算 exp_dur（frame_drop 数量在 frame_drop_chain 里已可精确取得，frame_dup 已知，rl 已知），门禁收紧到 ±2%
修改后预期：修完 P0-1/P0-2 后 exp_dur 与实际偏差应 < 1%
需要新增测试：test_exp_dur_matches_actual —— 对至少 5 个 seed 跑完整单进程路径，断言 |out_dur − exp_dur| / exp_dur < 0.02
P1 —— 功能实际上不生效 / 语义错误
P1-1　speed 参数在主路径完全不生效
文件/函数：video_rewash/core/_graph.py → build_segment_branch()，第 102-103 行与第 134-137 行
当前逻辑：setpts={1/speed}*PTS 之后紧跟 setpts=N/{nf}/TB
实际错误：实测 [vb]→[vm] 帧数完全不变（6 段 child_speed 差 ±3.6%，输出零差异）。segment 输出时长恒 = 帧数/30
为什么错：setpts=N/nf/TB 按帧序号重写 PTS，丢弃输入 PTS，覆盖变速结果。行为还随路径分裂：build_command 的 -vf 简单链（video_processor.py:184-199）没有这个 setpts，speed 在那里是生效的
修改方案：从 _graph.py:136 的 tail 中删除 f"setpts=N/{nf}/TB"，保留 fps={nf}（由它完成 CFR 化）。frame_drop 自带的 setpts=N/FRAME_RATE/TB 必须保留。滤镜顺序见第二节第 4 点
修改后预期：段输出时长 = win_i/speed_i，各段 speed 差异真实反映到输出
需要新增测试：test_speed_changes_duration —— 固定 seed，分别构造 speed=0.90 / 1.00 / 1.10 的快照，断言输出时长比 ≈ 1/0.90 : 1 : 1/1.10，容差 2%；同时断言 frame_drop 删帧数与 frame_dup 增帧数在三种 speed 下保持不变
⚠ 必须 FFmpeg 实测确认：删掉该 setpts 后 fps=30 会真的重采样，可能与 select 删帧、tpad 克隆帧相互作用。改前必须跑上述测试
P1-2　av_offset 的窗口平移造成段间内容跳帧/重复
文件/函数：video_rewash/core/segment.py → process_single_pass()，第 125-127 行
当前逻辑：t0 = max(0.0, seg_start - av)，t1 = max(t0+0.05, seg_end - av)，视频音频共用
实际错误：av 是每段独立随机，实测 6 个窗口 0.859-4.101 / 4.325-7.567 / 7.327-10.570 / 10.813-14.055 / 14.082-17.324 / 17.286-20.528 —— 5 个边界不连续：跳过 0.224s、重复 0.240s、跳过 0.243s、跳过 0.027s、重复 0.038s
为什么错：相对偏移已由 adelay/atrim（filters.py:508-513）实现，窗口平移在同步上是 no-op，只剩破坏段边界连续性的副作用。segment.py:81-82 的 docstring 仍把它描述为 av_offset 的实现机制，已过时
修改方案：恢复 t0 = seg_start、t1 = seg_end；偏移完全交给音频滤镜；同步更新 docstring
修改后预期：段边界严格连续，无跳帧/重复；max(0.0, ...) 钳位隐患一并消除
需要新增测试：test_segment_windows_contiguous —— 断言 t1_i == t0_{i+1}（容差 1e-6）对所有 i
P1-3　av_offset 改变段音频时长
文件/函数：video_rewash/video/filters.py → build_audio_filter()，第 508-513 行
当前逻辑：av>=0.02 → adelay={av*1000}:all=1；av<=-0.02 → atrim=start={-av},asetpts=PTS-STARTPTS
实际错误：实测 adelay=105 使音频 +0.104989s；atrim=start=0.119 使音频 −0.119000s
为什么错：adelay 前置静音、atrim 截掉开头，都改变流长度；A/V 相对偏移本应是时长中性操作
修改方案：adelay 之后追加 atrim=end={win_len}（切掉尾部溢出）；atrim=start 之后追加 apad=whole_dur={win_len}（尾部补静音）。win_len 由调用方传入（process_single_pass 已有 seg_len）
修改后预期：相对偏移 = av 保持不变，段音频长度恒等于段视频长度
需要新增测试：test_av_offset_duration_neutral —— 对 av = +0.10 / −0.10 / 0，断言音频时长与 av=0 时相同（容差 1 个音频帧）；另用 ffprobe -show_packets 或已知参考音验证相对偏移方向与量级
P1-4　reverse_loop 视频/音频增量不一致，A/V 漂移
文件/函数：video_rewash/core/_graph.py → build_segment_branch() 第 79-86 行 / build_segment_audio() 第 174-201 行；video_rewash/video/filters.py → build_reverse_loop_complex() 第 348-372 行
当前逻辑：视频与音频使用相同的 t1/t2 切点与相同 repeats，但视频侧受 30fps 帧栅格约束
实际错误：loop×2（seg_len=0.121）视频 +4 帧 = 0.1335s，音频 +0.121000s → 差 +0.0125s；loop×3（seg_len=0.115）视频 +6 帧 = 0.2002s，音频 +0.230000s → 差 −0.0298s。reverse 模式两侧都为 0，正确
为什么错：rl_seg_len 从 uniform(0.1,0.2) 连续取值，不是帧长整数倍；视频 trim 只能落在帧边界，音频不受此限
修改方案：在 generate_segment_plan（randomizer.py:518）把 rl_seg_len 量化到帧栅格：round(uniform(elo,ehi) * out_fps) / out_fps。需要把 out_fps 传入 generate_segment_plan（该函数已有 config 参数，可从 normalize.fps 读）
修改后预期：视频与音频增量精确相等，无逐事件漂移累积
需要新增测试：test_reverse_loop_av_delta_equal —— 对 reverse/loop×2/loop×3 三种情况，断言 |Δvideo − Δaudio| ≤ 1/fps
P1-5　rl_extra_seconds 与真实增量三方不一致
文件/函数：video_rewash/core/_graph.py → rl_extra_seconds()，第 32-38 行
当前逻辑：(repeats-1) * seg_len / max(0.1, speed)
实际错误：loop×2 实测 rl_extra=0.124434，视频实际 0.1333，音频实际 0.121000 —— 三个都不同
为什么错：/speed 假定 speed 生效（P1-1 证明不生效）；且 build_reverse_loop_complex 收到的是输入空间 seg_len_in，与 pos_rel 所基于的输出空间 seg_len/speed 混用（_graph.py:82 vs segment.py:128）
修改方案：P1-1 修完后 /speed 才正确；同时统一 rl 事件的时间空间（要么都用输入空间，要么都用输出空间并在 build_reverse_loop_complex 里换算）
修改后预期：rl_extra_seconds() == 实测视频增量 == 实测音频增量
需要新增测试：并入 test_exp_dur_matches_actual，覆盖至少一个 loop 触发的 seed
P1-6　frame_drop 的 n_frames 与窗口使用了错误的帧率/时间空间
文件/函数：video_rewash/core/_graph.py → build_segment_branch() 第 97 行、frame_drop_chain() 第 55-59 行
当前逻辑：int(seg_len_in * eff_fps) 作为 n_frames；window = (fwin.start*eff_fps, (fwin.start+fwin.dur)*eff_fps)
实际错误：n_frames 用目标帧率 30 乘输入空间时长（真实应为源 29.97）；本例 int(3.2422*30)=97，实测 [vt]=97，数值巧合相符，源为 60/25/23.976fps 时会显著偏离。fwin 是输出空间（segment.py:128 传 seg_len/speed），却被当输入帧号用，speed≠1 时窗口偏移 (1−speed) 比例（seg2 speed=1.0362 → 约 3.6%）
为什么错：select 在变速之前、标准化之前执行，此时流仍是源帧率；而 eff_fps 是目标帧率
修改方案：n_frames = int(seg_len_in * src_fps)（src_fps = media_info["fps"]，需透传）；窗口换算改为 fwin.start * speed * src_fps（把输出空间秒换回输入帧号）
修改后预期：删帧位置与上限对任意源帧率、任意 speed 都落在预期窗口内
需要新增测试：test_frame_drop_window —— 构造 25fps / 60fps / 29.97fps 三种源，断言删帧帧号全部落在 [start*src_fps, end*src_fps] 内，且数量 ≤ n_frames//50
P1-7　frame_dup 的 t_pos 基于错误时长，位置偏移
文件/函数：video_rewash/core/_graph.py 第 149-154 行 / video_rewash/video/filters.py → build_frame_dup_complex() 第 452 行
当前逻辑：传入 duration = seg_len_in / speed；t_pos = duration * pos
实际错误：seg0 传入 3.3066 → t_pos=1.174（实测 trim=end=1.174）；而作用对象 [vm_0] 的真实时长 = 96/30 = 3.2000，正确 t_pos 应为 1.136 → 偏移 0.038s ≈ 1.1 帧。6 段偏移范围 −0.010 ~ +0.038s
为什么错：frame_dup 作用在标准化尾之后的 [vm] 上，其时间轴是 帧数/30（已扣 frame_drop、已被 setpts 重写）；传入的却是"未扣删帧、除以无效 speed"的输入空间长度
修改方案：把 frame_dup 的 duration 改为该分支的真实输出时长 (n_in − drop + rl_frames) / out_fps（P1-1 修完后为 (win/speed)，此时二者自然一致）；或把 frame_dup 移到标准化尾之前并使用同一时间空间
修改后预期：t_pos 落在真实输出时间轴的 pos 比例处，误差 < 1 帧
需要新增测试：test_frame_dup_position —— 用 select='eq(n,K)' 抽取重复帧位置，断言其帧号 ≈ pos × 输出帧数，误差 ≤ 1 帧
P2 —— 日志 / 测试 / 可追溯性
P2-1　FFmpeg 命令从不落盘，事故不可复查
文件/函数：video_rewash/core/ffmpeg_runner.py → run_ffmpeg()，第 48-71 行
当前逻辑：直接 sp.Popen(cmd, ...)，不记录 cmd
实际错误：第一轮审计必须靠 git archive 重放才能取得当次命令；正常运维无法定位任何滤镜级问题
修改方案：run_ffmpeg 增加可选 log_fn，或在模块级加 _DUMP_CMD = os.environ.get("REWASH_DUMP_CMD")，非空时把 cmd（JSON）与时间戳写到 logs/cmd_{batch}_{seq}.json。默认关闭，不影响性能
修改后预期：出问题时可直接拿到 -filter_complex 原文
需要新增测试：test_cmd_dump —— 设置环境变量后跑一次，断言文件生成且能 JSON 解析
P2-2　参数快照不落盘
文件/函数：video_rewash/core/processor.py → process_one()，第 186-190 行
当前逻辑：result["snap"] = snap，只在内存；日志只打印 snapshot_summary + log_parameter_calibration 的部分字段
实际错误：日志缺 trim_head/trim_tail（log_parameter_calibration 有打印，但缺 rl_*、frame_drop_on、asym_crop_*、lens_*、audio_atempo、audio_highpass/lowpass）；第一轮必须靠 seed 重放才能取得完整快照
修改方案：把 snap（含 seed 与全部 params）与各段 child snapshot 一并 dump 到 logs/snapshot_{batch}_{file}.json
需要新增测试：断言 dump 出的 JSON 用同一 seed 重建 generate_snapshot 后完全相等
P2-3　process_single_pass docstring 描述的 av_offset 机制已过时
文件/函数：video_rewash/core/segment.py，第 81-82 行
当前内容："此处两支路 trim 窗口同步平移，语义一致"
实际错误：真正的偏移由 filters.build_audio_filter 的 adelay/atrim 产生；窗口平移在同步上是 no-op、在内容上是缺陷（P1-2）
修改方案：随 P1-2 一并更新
P2-4　tests/reconstruct_test.py 内含与真实代码路径不符的假设
文件：tests/reconstruct_test.py
实际错误：用 1-based seg_idx（真实为 0-based，segment.py:128 传 i）；用 seg_len_base = expected_dur_with_speed/6 而非真实的 seg_len/speed；dup_extra = frame_dup/fps 假定 dup 全生效（正确但未考虑 seg_len/speed>1.0 条件）；av_extra = max(0, av_offset) 假定 av 直接叠加（真实是 adelay 且各段 av 不同）；fps = 30 # From config.json 硬编码
修改方案：改为不自行推导，而是调用 segment.process_segmented（monkeypatch run_ffmpeg 捕获命令）后解析真实 filter_complex，与实测帧数比对。或直接删除，由 P0/P1 各项的新测试替代
十一、总结：状态分类
已修复（相比第一轮那次运行）
trim_head/trim_tail 已真正进入时间轴（cuts 用 effective_duration + trim_head+cuts[i]）
rotate_drift 从无界线性漂移改为双正弦，上限可控
-itsoffset 已从 build_audio_args 移除
av_offset 现在确实产生音画相对偏移（adelay/atrim），正负语义统一，量级 = |av|，无双倍计算
当前 main 仍然存在（实测确认）
编号	问题	实测证据
P0-1	audio pitch 硬编码 44100	倍率 = sr_in/44100，48k → ×1.088345
P0-2	concat CFR 补帧膨胀	Σv=589帧 → 输出 621帧，+1.0667s
P0-3	质检期望漏扣 trim，膨胀被合法化	期望 21.0977 vs 实际 20.7960 → −1.43% 通过
P0-4	±10% 门禁过松 + exp_dur 错	exp_dur=19.3691 vs 实际 20.7960 → +7.36% 通过
P1-1	speed 在主路径不生效	[vb]→[vm] 帧数 6 段全部不变
P1-2	av 窗口平移造成段间跳帧/重复	5 个边界不连续，最大重复 0.240s
P1-3	av_offset 改变段音频时长	adelay +0.104989s / atrim −0.119000s
P1-4	reverse_loop 音视频增量不等	loop×3：视频 0.2002s vs 音频 0.230000s
P1-5	rl_extra_seconds 三方不一致	0.124434 / 0.1333 / 0.121000
P1-6	frame_drop n_frames/窗口帧率与空间错	用 eff_fps=30 乘输入时长；窗口输出空间当输入帧号
P1-7	frame_dup t_pos 偏移	seg0 偏 0.038s ≈ 1.1 帧
P2-1~4	命令/快照不落盘、docstring 过时、测试脚本假设错	见上
必须修改（按优先级与依赖顺序）
text

插入

复制
1. P0-1  audio pitch 采样率        ← 独立，收益最大，实测可降 1.067s → 0.133s 膨胀
2. P1-3  av_offset 时长中性化      ← 依赖 1，合起来实测降到 +1 帧 (0.033s)
3. P0-3  质检期望对齐 effective_duration + 容差收紧到 5%
4. P0-4  exp_dur 重算 + 门禁收紧到 2%   ← 依赖 1、2、P1-1
5. P1-2  删除 av 窗口平移           ← 独立，修内容连续性
6. P1-1  speed 修复（删 setpts=N/nf/TB） ← 风险最高，必须先有测试
7. P1-6 / P1-7 / P1-4 / P1-5       ← 依赖 6
8. P2-1 / P2-2                     ← 独立，建议与 1 同批做，为后续验证提供证据
必须实际 FFmpeg 实测才能确认（不能只靠读码）
删掉 setpts=N/{nf}/TB 后 fps={nf} 的重采样行为（P1-1）：speed<1 时会复制帧、speed>1 时会丢帧，与 select 删帧、tpad 克隆帧的相互作用无法从源码推断。必须对 speed ∈ {0.90, 0.97, 1.00, 1.04, 1.10} × frame_drop 开/关 × frame_dup 开/关 做矩阵实测。
zoompan 在窗口切段路径下的帧数行为（本次 6 段 zoom 全部未触发，zoom_win={'on': False}）：zoompan 的 d=1 与 fps= 参数是否改变帧数、concat 回拼是否引入空洞 —— 本轮无实测证据，属【证据不足】。
apad=whole_dur / atrim=end 在 concat 上游的实际生效边界（P1-3 方案）：apad 对已有 asetpts 的流是否精确补到目标长度，需实测。
非 48k 素材的端到端表现：本轮 sr 矩阵是用 sine 合成音频隔离测的，真实 44.1k / 32k 素材走完整流水线的结果需实测。
h264_nvenc 与 libx264 在填补 concat 空洞时的补帧策略是否一致：本轮只测了 h264_nvenc（+32 帧）。CPU 回退路径是否给出相同帧数，未测。
未修改任何代码，未新增项目内文件，未 commit，未 push。所有实测产物均在 %TEMP% 生成并已删除；git status --porcelain 为空。

（注：%TEMP% 下残留两个 rewash_work_* 目录，是本工具早前真实运行留下的工作目录，非本次审计产物，我未动它们 —— 若需要清理请告知。）
