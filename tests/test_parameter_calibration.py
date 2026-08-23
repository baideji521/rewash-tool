# -*- coding: utf-8 -*-
import math
import random
import unittest
import sys
import os

# 模拟项目环境
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from video_rewash.core.randomizer import generate_snapshot
from video_rewash.video.filters import (
    build_color_chain, build_rotate_filter, build_zoompan_expr,
    build_audio_filter, frame_drop_positions, build_frame_drop_expr
)

class TestParameterCalibration(unittest.TestCase):
    def setUp(self):
        self.preset = {
            "name": "test",
            "params": {
                "brightness": {"min": 15, "max": 15},
                "contrast": {"min": 15, "max": 15},
                "saturation": {"min": 15, "max": 15},
                "hue": {"min": 5, "max": 5},
                "speed": {"min": 1.04, "max": 1.04},
                "audio_speed": {"min": 1.0, "max": 1.0}, # factor 1.0
                "audio_pitch": {"min": 1.0, "max": 1.0},
                "rotate_drift": {
                    "amp_min": 1.0, "amp_max": 1.0,
                    "period_min": 10, "period_max": 10,
                    "speed_min": 0.02, "speed_max": 0.02
                },
                "zoom_drift": {"amp_min": 0.05, "amp_max": 0.05, "period": 4.0},
                "av_offset": {"enable": True, "min": 0.1, "max": 0.1}
            }
        }
        self.config = {
            "video": {
                "zoom_drift": {"enable": True},
                "rotate_drift": {"enable": True},
                "frame_drop": {"enable": True, "probability": 1.0, "interval": {"min": 15, "max": 15}}
            }
        }

    def test_calibration(self):
        print("\n" + "="*50)
        print("PARAMETER CALIBRATION TEST")
        print("="*50)

        # 生成快照
        snap = generate_snapshot(self.preset, self.config, seed=42)
        p = snap["params"]

        # 1. Brightness
        print(f"\nbrightness:")
        print(f"  config = 15")
        print(f"  randomized = {p['brightness']}")
        color_filter = build_color_chain(snap)
        print(f"  ffmpeg = {color_filter}")
        self.assertIn(f"brightness={p['brightness']/100.0:.4f}", color_filter)

        # 2. Contrast
        print(f"\ncontrast:")
        print(f"  config = 15")
        print(f"  randomized = {p['contrast']}")
        self.assertIn(f"contrast={1.0 + p['contrast']/100.0:.4f}", color_filter)

        # 3. Saturation
        print(f"\nsaturation:")
        print(f"  config = 15")
        print(f"  randomized = {p['saturation']}")
        self.assertIn(f"saturation={1.0 + p['saturation']/100.0:.4f}", color_filter)

        # 4. Hue
        print(f"\nhue:")
        print(f"  config = 5")
        print(f"  randomized = {p['hue']}")
        self.assertIn(f"hue=h={p['hue']:.2f}", color_filter)

        # 5. Speed
        print(f"\nspeed:")
        print(f"  config = 1.04")
        print(f"  randomized = {p['speed']}")
        self.assertEqual(p['speed'], 1.04)

        # 6. Audio Speed
        print(f"\naudio_speed:")
        print(f"  config = 1.01 (factor 1.0)")
        print(f"  randomized = {p['audio_atempo']}")
        audio_filter = build_audio_filter(snap)
        print(f"  ffmpeg = {audio_filter}")

        # 7. Audio Pitch
        print(f"\naudio_pitch:")
        print(f"  config = 1 (semitone)")
        print(f"  randomized = {p['audio_pitch']}")
        rate = 2.0 ** (p['audio_pitch'] / 12.0)
        self.assertIn(f"asetrate={int(44100 * rate)}", audio_filter)

        # 8. Rotate
        print(f"\nrotate:")
        print(f"  config: amp=1, period=10, speed=0.02")
        print(f"  randomized: amp={p['rotate_drift_amp']}, period={p['rotate_drift_period']}, speed={p['rotate_drift_speed']}")
        rotate_filter = build_rotate_filter(p)
        print(f"  ffmpeg = {rotate_filter}")
        deg2rad = math.pi / 180
        # 验证双正弦波模型
        t2 = 60.0
        amp2 = p['rotate_drift_speed'] * t2 / (2 * math.pi)
        self.assertIn(f"{p['rotate_drift_amp']:.4f}*{deg2rad:.6f}*sin(2*PI*t/{p['rotate_drift_period']:.2f}", rotate_filter)
        self.assertIn(f"{amp2:.4f}*{deg2rad:.6f}*sin(2*PI*t/{t2:.2f}", rotate_filter)

        # 9. Zoom
        print(f"\nzoom:")
        print(f"  config: amp=0.05")
        print(f"  randomized: {p['zoom_drift_amp']}")
        zoom_expr = build_zoompan_expr(p, 30)
        print(f"  ffmpeg zoom expr = {zoom_expr}")
        self.assertIn(f"1+{p['zoom_drift_amp']:.4f}", zoom_expr)

        # 10. Frame Drop
        print(f"\nframe_drop:")
        print(f"  config: interval=15")
        drops = frame_drop_positions(100, 15, 15, random.Random(42))
        print(f"  randomized drops = {drops}")
        fd_expr = build_frame_drop_expr(drops)
        print(f"  ffmpeg = {fd_expr}")
        self.assertIn("select='not(", fd_expr)

        # 11. AV Offset
        print(f"\nav_offset:")
        print(f"  config = 0.1")
        print(f"  randomized = {p['av_offset']}")
        if p['av_offset'] > 0:
            self.assertIn(f"adelay={int(p['av_offset']*1000)}", audio_filter)
        else:
            self.assertIn(f"atrim=start={-p['av_offset']:.3f}", audio_filter)

        print("\n" + "="*50)
        print("ALL TESTS PASSED")
        print("="*50)

if __name__ == "__main__":
    unittest.main()
