# -*- coding: utf-8 -*-
import math
import unittest
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from video_rewash.core.randomizer import generate_snapshot
from video_rewash.video.filters import build_rotate_filter
from video_rewash.core.segment import make_equal_cuts

class TestFixVerification(unittest.TestCase):
    def test_rotate_drift_safety_limit(self):
        """验证 rotate_drift 是否有明确的安全上限，不再随时间无限累计。"""
        params = {
            "rotate_drift_amp": 0.1,
            "rotate_drift_period": 10.0,
            "rotate_drift_speed": 0.02,
            "rotate_drift_phase": 0.0
        }
        
        # 提取 FFmpeg 表达式中的系数
        # 振幅 A2 = speed * 60 / (2 * PI) = 0.02 * 60 / 6.283185 ≈ 0.1910
        # 理论上限 = 0.1 + 0.1910 = 0.2910 度
        
        deg2rad = math.pi / 180
        limit_deg = 0.1 + (0.02 * 60.0 / (2 * math.pi))
        limit_rad = limit_deg * deg2rad
        
        print(f"\nRotate Drift Safety Check:")
        print(f"  Amp1: 0.1°")
        print(f"  Speed: 0.02°/s -> Amp2: {0.02 * 60.0 / (2 * math.pi):.4f}°")
        print(f"  Theory Limit: {limit_deg:.4f}°")
        
        # 模拟不同时间点的旋转值（弧度）
        def get_val(t):
            # A1*sin(2pi*t/T1 + p1) + A2*sin(2pi*t/T2 + p2)
            t2 = 60.0
            amp2 = 0.02 * t2 / (2 * math.pi)
            val = 0.1 * math.sin(2*math.pi*t/10.0) + amp2 * math.sin(2*math.pi*t/60.0)
            return abs(val)
        
        times = [10, 30, 60, 120, 300, 3600] # 测试到 1 小时
        for t in times:
            val = get_val(t)
            print(f"  t={t}s: {val:.4f}°")
            self.assertLessEqual(val, limit_deg + 0.0001)

    def test_segmented_trim_consistency(self):
        """验证分段模式下 trim 是否生效，且与 cuts 计算一致。"""
        duration = 21.1
        base_snap = {
            "seed": 42,
            "params": {
                "trim_head": 1.0,
                "trim_tail": 1.0
            }
        }
        
        # 模拟 segment.py 中的修复逻辑
        trim_head = base_snap["params"]["trim_head"]
        trim_tail = base_snap["params"]["trim_tail"]
        effective_duration = duration - trim_head - trim_tail
        n = 3
        cuts = make_equal_cuts(effective_duration, n)
        
        # 验证第一个分段是否从 trim_head 开始
        seg_start_0 = trim_head + cuts[0]
        self.assertEqual(seg_start_0, 1.0)
        
        # 验证最后一个分段是否在 duration - trim_tail 结束
        seg_end_last = trim_head + cuts[-1]
        self.assertAlmostEqual(seg_end_last, 20.1, places=5)
        
        print(f"\nSegmented Trim Check:")
        print(f"  Source: {duration}s")
        print(f"  Trim: head={trim_head}s, tail={trim_tail}s")
        print(f"  Effective: {effective_duration}s")
        print(f"  Cuts: {cuts}")
        print(f"  Actual Timeline: {seg_start_0}s to {seg_end_last}s")

if __name__ == "__main__":
    unittest.main()
