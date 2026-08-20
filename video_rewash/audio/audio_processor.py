# -*- coding: utf-8 -*-
"""audio.audio_processor — 音频变换参数构建

方案 2.2「不换BGM」指纹破坏组合：变速+变调+EQ+高低通+淡入淡出+重编码。

客观说明：该组合提高 ChromaPrint/Shazam 类频谱指纹失效的概率，
但不同平台音频指纹算法鲁棒性差异大，不能假设必然失效。
重编码为必做项（零感知），其余项由预设档位控制强度。
"""
from ..video.filters import build_audio_filter


def build_audio_args(snap: dict, has_audio: bool,
                     audio_codec: str = "aac") -> tuple:
    """
    返回 (input_pre_args, audio_out_args, audio_mixed: bool)：
    - input_pre_args: 放在 -i 之前的参数（如 -itsoffset 音画偏移）
    - audio_out_args: -af / -filter_complex / -c:a 等输出侧参数
    - audio_mixed: True 表示音频已在 filter_complex 中输出 [amix]，
      调用方需 -map [amix]；False 时调用方自行 -map 0:a
    无音频流时返回空参数（调用方用 -an）。
    """
    if not has_audio:
        return [], [], False

    p = snap.get("params", {})
    input_pre = []

    # 音画偏移（观感风险项：仅标准/激进档小幅启用，输出过质检）
    av = float(p.get("av_offset", 0.0) or 0.0)
    if abs(av) >= 0.02:
        input_pre += ["-itsoffset", f"{av:.3f}"]

    out_args = []
    audio_mixed = False
    noise_db = p.get("audio_noise_db")
    af = build_audio_filter(snap)

    if noise_db:
        # 激进档极低音量粉噪（-45~-50dB 人耳不可闻）：anoisesrc 混音
        # 注意：[va] 标签必须紧跟滤镜链，前面不能有逗号
        chain = f"[0:a]{af if af else ''}[va];"
        amp = 10 ** (float(noise_db) / 20)
        chain += (f"anoisesrc=c=pink:a={amp:.7f}[n];"
                  f"[va][n]amix=inputs=2:duration=first:dropout_transition=0[amix]")
        out_args += ["-filter_complex", chain]
        audio_mixed = True
    elif af:
        out_args += ["-af", af]

    # 重编码必做：AAC 码率按档位随机（破坏压缩域频谱）
    pname = snap.get("preset", "standard")
    import random as _r
    rng = _r.Random(snap.get("seed", 0) + 17)
    if audio_codec == "mp3":
        br = rng.choice([128, 160, 192])
        out_args += ["-c:a", "libmp3lame", "-b:a", f"{br}k"]
    else:
        br = rng.randint(192, 256) if pname == "gentle" else rng.randint(128, 256)
        out_args += ["-c:a", "aac", "-b:a", f"{br}k"]

    return input_pre, out_args, audio_mixed
