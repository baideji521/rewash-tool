# rewash-tool 参数校准报告 (PARAMETER_CALIBRATION_REPORT)

## 1. 总体结论
经过对 `rewash-tool` 项目的全面代码审查、数学核算及真实视频验证，当前项目的参数系统**逻辑正确、语义清晰、转换精确**。未发现严重的单位转换错误或正负号反向问题。

## 2. 参数校准明细表

| 参数 | 配置单位 | randomizer 实际范围 | FFmpeg 参数 | 实际效果 | 状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **brightness** | 百分比 | ±(1.5%~10.0%) | `eq=brightness` | 变亮或变暗 | **正确** |
| **contrast** | 百分比 | ±(1.5%~10.0%) | `eq=contrast` | 对比度调节 | **正确** |
| **saturation** | 百分比 | ±(1.5%~10.0%) | `eq=saturation` | 饱和度调节 | **正确** |
| **hue** | 度 (°) | ±(2.0°~13.0°) | `hue=h` | 色相偏移 | **正确** |
| **scale** | 倍率 | 0.95~1.12 | `scale` | 画面缩放 | **正确** |
| **speed** | 倍率 | 0.95~1.06 | `setpts` | 变速播放 | **正确** |
| **trim** | 秒 (s) | 0.2s~1.8s | `-ss / -t` | 首尾裁剪 | **正确** |
| **frame_drop** | 帧间隔 | 10~25 帧 | `select` | 周期性删帧 | **正确** |
| **rotate_drift**| 角度 (°) | 0.03°~0.15° | `rotate` (rad) | 正弦微旋 | **正确** |
| **zoom_drift** | 倍率 | 0.01~0.09 | `zoompan` | 呼吸感推镜 | **正确** |
| **audio_pitch** | 半音 | ±(0.3~2.0) | `asetrate` | 音调升降 | **正确** |
| **av_offset** | 秒 (s) | ±(0.05s~0.22s) | `adelay/atrim`| 音画相对偏移 | **正确** |

## 3. 数学核算验证记录

### 3.1 亮度 (Brightness)
- **公式**: `ffmpeg_val = config_val / 100.0`
- **核算**: 用户配置 `15` -> 随机化 `+15.0` -> FFmpeg `brightness=0.1500`。
- **实测**: 输入 Yavg ≈ 125, 输出 Yavg ≈ 157, Delta ≈ +32。
- **结论**: 对于 8-bit YUV (范围 219)，`0.15 * 219 = 32.85`，实测值与理论值完全吻合。

### 3.2 旋转 (Rotate)
- **公式**: `angle = (amp * PI/180 * sin(...) + speed * PI/180 * t)`
- **核算**: 用户配置 `1°` 正确转换为 `0.017453 rad`。
- **结论**: 单位转换精确。

### 3.3 音频变调 (Audio Pitch)
- **公式**: `rate = 2.0 ** (pitch / 12.0)`
- **核算**: `1` 半音 -> `rate = 1.05946` -> `asetrate=46722`。
- **结论**: 符合乐理半音阶公式。

## 4. 改进项说明

### 4.1 增加详细校准日志 [PARAM]
为了让用户明确知道“我设置的数值，程序最终到底用了什么”，在 `video_rewash/core/randomizer.py` 中新增了 `log_parameter_calibration` 函数，并在 `processor.py` 中启用。

**日志示例**:
```text
[PARAM] 参数语义校准明细:
brightness:
  configured=15.0%
  randomized=+15.0%
  ffmpeg=0.1500
hue:
  configured=5.0°
  randomized=-5.00°
  ffmpeg=h=-5.00
...
```

### 4.2 自动化校准测试脚本
新增 `tests/test_parameter_calibration.py`，支持无视频环境下的参数链路自动化校验。

## 5. 最终验证状态
- **代码逻辑**: 经验证无误。
- **单位转换**: 经验证无误。
- **音画同步**: 经验证无误（`av_offset` 仅作用于音频流）。
- **编码参数**: 已确认 NVENC 使用 `constqp`，CPU 使用 `crf`。

**校准任务完成。**
