# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import subprocess
import math

# 模拟项目环境
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from video_rewash.core.ffmpeg_runner import probe_media
from video_rewash.video.video_processor import build_command

def run_objective_test():
    ffmpeg_path = r"C:\Users\Administrator\Desktop\video-rewash-tool-main\ffmpeg\bin\ffmpeg.exe"
    ffprobe_path = r"C:\Users\Administrator\Desktop\video-rewash-tool-main\ffmpeg\bin\ffprobe.exe"
    input_path = "tests/input_test.mp4"
    output_dir = "tests/output_parameter_calibration"
    os.makedirs(output_dir, exist_ok=True)
    
    output_path = os.path.join(output_dir, "output.mp4")
    
    # 固定参数快照
    snap = {
        "preset": "calibration",
        "seed": 12345,
        "params": {
            "brightness": 15.0,
            "contrast": 15.0,
            "saturation": 15.0,
            "hue": 5.0,
            "scale": 1.0,
            "speed": 1.0,
            "rotate_drift_amp": 1.0,
            "rotate_drift_period": 10.0,
            "rotate_drift_speed": 0.02,
            "rotate_drift_phase": 0.0,
            "zoom_drift_amp": 0.0,
            "crf": 23,
            "gop": 40,
            "bframes": 2,
            "audio_atempo": 1.0,
            "audio_pitch": 0.0,
            "audio_eq_bands": [],
            "audio_highpass": 0,
            "audio_lowpass": 0,
            "av_offset": 0.0,
            "trim_head": 0.0,
            "trim_tail": 0.0
        }
    }
    
    config = {
        "runtime": {"ffmpeg": ffmpeg_path, "ffprobe": ffprobe_path},
        "video": {
            "zoom_drift": {"enable": False},
            "rotate_drift": {"enable": True, "probability": 1.0, "duration": {"min": 10, "max": 10}},
            "lens_distortion": {"enable": False},
            "mask_drift": {"enable": False},
            "asymmetric_crop": {"enable": False},
            "black_crop": {"enable": False},
            "frame_drop": {"enable": False}
        },
        "normalize": {
            "width": 1280,
            "height": 720,
            "fps": 30,
            "pix_fmt": "yuv420p"
        }
    }
    
    media_info = probe_media(ffprobe_path, input_path)
    
    cmd = build_command(input_path, output_path, snap, config, media_info, use_nvenc=False)
    
    print(f"Executing FFmpeg command...")
    with open(os.path.join(output_dir, "ffmpeg_command.txt"), "w") as f:
        f.write(" ".join(cmd))
    
    with open(os.path.join(output_dir, "parameter_snapshot.json"), "w") as f:
        json.dump(snap, f, indent=2)
        
    subprocess.run(cmd, check=True)
    
    print("Verification completed. Analyzing results...")
    
    # 客观检测：使用 FFmpeg 统计亮度
    def get_stats(path):
        stats_cmd = [
            ffmpeg_path, "-i", path, "-vf", "signalstats", "-f", "null", "-"
        ]
        res = subprocess.run(stats_cmd, capture_output=True, text=True)
        # 解析 stderr 中的 signalstats 输出
        y_avgs = []
        for line in res.stderr.split('\n'):
            if "Yavg:" in line:
                try:
                    # Yavg:16 Uavg:128 Vavg:128
                    parts = line.split("Yavg:")[1].split()
                    y_avgs.append(float(parts[0]))
                except: pass
        if not y_avgs:
            print(f"Debug: No Yavg found for {path}. Stderr snippet: {res.stderr[-500:]}")
        return sum(y_avgs) / len(y_avgs) if y_avgs else 0

    input_y = get_stats(input_path)
    output_y = get_stats(output_path)
    
    print(f"\nObjective Stats:")
    print(f"  Input Mean Y: {input_y:.2f}")
    print(f"  Output Mean Y: {output_y:.2f}")
    print(f"  Delta Y: {output_y - input_y:+.2f}")
    
    if output_y > input_y:
        print("  Status: PASS (Brightness increased as expected)")
    else:
        print("  Status: FAIL (Brightness did not increase)")

if __name__ == "__main__":
    run_objective_test()
