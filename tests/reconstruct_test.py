# -*- coding: utf-8 -*-
import math
import random
import sys
import os

# 模拟项目环境
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from video_rewash.core.randomizer import generate_snapshot, generate_segment_plan
from video_rewash.core._graph import rl_extra_seconds

def reconstruct():
    seed = 1786869947670
    preset_name = "标准-自定义"
    preset = {
        "name": preset_name,
        "label": preset_name,
        "params": {
            "scale": {"min": 0.98, "max": 1.08},
            "brightness": {"min": 10.0, "max": 25.0},
            "contrast": {"min": 5.0, "max": 15.0},
            "saturation": {"min": 3.0, "max": 7.0},
            "hue": {"min": 3.0, "max": 8.0},
            "speed": {"min": 0.97, "max": 1.04},
            "frame_dup": {"min": 1.0, "max": 3.0},
            "trim": {"min": 0.5, "max": 1.2},
            "audio_speed": {"min": 0.99, "max": 1.01},
            "audio_pitch": {"min": 0.8, "max": 1.3},
            "audio_eq": {"min": 1.5, "max": 2.5},
            "av_offset": {"enable": True, "min": 0.08, "max": 0.15},
            "crf": {"min": 19.0, "max": 26.0},
            "gop": {"min": 25.0, "max": 60.0},
            "zoom_drift": {"amp_min": 0.015, "amp_max": 0.06, "period": 3.5},
            "rotate_drift": {
                "amp_min": 0.05, "amp_max": 0.1,
                "period_min": 8.0, "period_max": 15.0,
                "speed_min": 0.01, "speed_max": 0.02
            }
        }
    }
    
    config = {
        "video": {
            "asymmetric_crop": {"enable": True, "min": 0.03, "max": 0.05},
            "lens_distortion": {"enable": True, "k1_range": 0.006, "k2_range": 0.008, "probability": 1.0, "duration": {"min": 99.0, "max": 99.0}, "count": {"min": 1, "max": 2}},
            "rotate_drift": {"probability": 1.0, "duration": {"min": 99.0, "max": 99.0}},
            "zoom_drift": {"probability": 0.8, "duration": {"min": 3.0, "max": 8.0}, "pause": {"min": 1.0, "max": 4.0}},
            "reverse_loop": {"enable": True, "probability": 0.4, "event_length": {"min": 0.1, "max": 0.2}},
            "frame_drop": {"enable": True, "probability": 1.0, "interval": {"min": 10, "max": 25}, "duration": {"min": 0.1, "max": 1.0}}
        }
    }
    
    # 1. 生成快照
    snap = generate_snapshot(preset, config, seed=seed)
    p = snap["params"]
    print(f"Snapshot Parameters:")
    print(f"  speed: {p['speed']}")
    print(f"  frame_dup: {p['frame_dup']}")
    print(f"  trim_head: {p['trim_head']}")
    print(f"  trim_tail: {p['trim_tail']}")
    print(f"  av_offset: {p['av_offset']}")
    
    # 2. 生成分段计划
    source_dur = 21.1
    trim_head = p['trim_head']
    trim_tail = p['trim_tail']
    effective_source_dur = source_dur - trim_head - trim_tail
    speed = p['speed']
    expected_dur_with_speed = effective_source_dur / speed
    
    print(f"\nTimeline Calculation:")
    print(f"  Source: {source_dur}s")
    print(f"  Effective Source (after trim): {effective_source_dur:.3f}s")
    print(f"  After Speed ({speed}): {expected_dur_with_speed:.3f}s")
    
    segment_count = 6
    seg_len_base = expected_dur_with_speed / segment_count
    
    total_extra = 0
    for i in range(1, segment_count + 1):
        plan = generate_segment_plan(snap, config, i, seg_len_base)
        extra = rl_extra_seconds(plan, speed)
        total_extra += extra
        rl = plan.get("reverse_loop") or {}
        if rl.get("mode"):
            print(f"  Segment {i}: mode={rl['mode']}, repeats={rl['repeats']}, seg_len={rl['seg_len']}, extra={extra:.3f}s")
        else:
            print(f"  Segment {i}: No loop")

    # Frame Dup 增加的时长
    # build_frame_dup_filter 在 processor.py 中实现
    # 它在特定位置插入 N 帧。FFmpeg 插入帧会增加总帧数，从而增加时长。
    # 增加时长 = p['frame_dup'] / fps
    fps = 30 # From config.json
    dup_extra = p['frame_dup'] / fps
    print(f"  Frame Dup Extra: {p['frame_dup']} frames / {fps} fps = {dup_extra:.3f}s")
    
    # AV Offset
    # av_offset = 0.105. adelay=105.
    # 这会使音频流延长 0.105s。如果视频流较短，FFmpeg 默认输出会包含这段音频延迟。
    av_extra = max(0.0, p['av_offset'])
    print(f"  AV Offset Extra: {av_extra:.3f}s")
    
    final_theory = expected_dur_with_speed + total_extra + dup_extra + av_extra
    print(f"\nFinal Theoretical Duration: {final_theory:.3f}s")
    print(f"Actual Log Duration: 22.4s")
    print(f"Difference: {22.4 - final_theory:+.3f}s")

if __name__ == "__main__":
    reconstruct()
