# -*- coding: utf-8 -*-
"""core.config — 配置系统

v7.0 修正：
- Bug#4: 所有配置读取走 config_get(key, default)，缺键不崩溃
- Bug#6: 统一开关读取函数 config_enabled，消除重复判断
- 线程安全：copy-on-write 快照，处理线程读快照不受运行中修改影响
"""
import copy
import json
import threading
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent.parent.parent / "config.json"

_CONFIG_LOCK = threading.RLock()

# v7.0 全局配置（非预设部分：性能/路径/输出标准化/流程开关）
DEFAULT_CONFIG = {
    "version_count": 1,        # 每个素材生成几个视频（1-10）
    "segment_count": 4,        # 分段数（1-20，1=不分段）
    "output_dir": "",          # 记住上次输出目录
    "input_dir": "",           # 记住上次输入目录
    "last_used_preset": "standard",
    "preset_overrides": {},  # 去重页参数微调的全局覆盖（按预设名存，启动自动还原）
    "switches": {            # 3.1 流程类独立开关（与档位无关）
        "segment": True,     # 兼容旧配置（v7.1 以 segment_count 为准）
        "normalize": True,   # 输出标准化
        "quality_check": True,  # 输出质量检测
    },
    "normalize": {           # 3.3 输出标准化可配置项
        "aspect_ratio": "3:4",
        "width": 1080,
        "height": 1440,
        "fps": 30,
        "pix_fmt": "yuv420p",
        "video_codec": "h264",
        "audio_codec": "aac",
        "bitrate_kbps": 0,   # 目标码率(kbps)：0=自动对齐源码率（输出体积≈源体积）
    },
    "encode": {
        "gpu_auto": True,
        "nvenc_preset": "p3",
        "cpu_preset": "medium",
    },
    "performance": {
        "workers": {"auto": True, "max": 4},
        "nvenc_max_sessions": 3,
    },
    "fingerprint": {         # 指纹作为质检工具（不阻断处理）
        "enable": True,
        "sample_frames": 8,
        "max_similarity": 0.70,
    },
    "retry": {               # 工程化：失败重试
        "max_retries": 1,
    },
}


def _deep_merge(dst: dict, src: dict) -> dict:
    """深合并：src 覆盖 dst，dst 独有的键保留（不丢配置）"""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


class ConfigStore:
    """线程安全配置容器（copy-on-write）"""

    def __init__(self):
        self._data = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self):
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    disk = json.load(f)
                with _CONFIG_LOCK:
                    # 与默认结构深合并，保证新增键总有默认值
                    self._data = _deep_merge(copy.deepcopy(DEFAULT_CONFIG), disk)
        except Exception as e:
            print(f"[config] 加载失败，使用默认配置: {e}")

    def save(self, data: dict = None):
        try:
            with _CONFIG_LOCK:
                payload = copy.deepcopy(data if data is not None else self._data)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[config] 保存失败: {e}")
            return False

    def snapshot(self) -> dict:
        """深拷贝快照：处理任务全程使用，不受后续修改影响"""
        with _CONFIG_LOCK:
            return copy.deepcopy(self._data)

    def replace(self, new_data: dict):
        """原子替换（copy-on-write）"""
        with _CONFIG_LOCK:
            merged = _deep_merge(copy.deepcopy(DEFAULT_CONFIG), copy.deepcopy(new_data))
            self._data = merged

    def get_data(self) -> dict:
        with _CONFIG_LOCK:
            return self._data


# 全局单例
STORE = ConfigStore()


def config_get(data: dict, path: str, default=None):
    """
    安全读取嵌套配置（Bug#4 修正）：
        config_get(cfg, "performance.workers.max", 2)
    任何一层缺失/类型不符都返回 default，绝不抛异常。
    """
    try:
        node = data
        for key in path.split("."):
            if not isinstance(node, dict):
                return default
            node = node.get(key)
            if node is None:
                return default
        return node
    except Exception:
        return default


def config_enabled(data: dict, path: str, default: bool = False) -> bool:
    """
    统一开关读取（Bug#6 修正）：全局唯一判断入口。
    支持 bool 值或 {"enable": bool} 结构。
    """
    val = config_get(data, path, None)
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, dict):
        return bool(val.get("enable", default))
    return default


def rand_range(data: dict, path: str, default_min: float, default_max: float):
    """读取 {min,max} 范围，缺失时用默认值（保证永不崩溃）"""
    node = config_get(data, path, None)
    if isinstance(node, dict):
        try:
            lo = float(node.get("min", default_min))
            hi = float(node.get("max", default_max))
        except (TypeError, ValueError):
            lo, hi = default_min, default_max
    else:
        lo, hi = default_min, default_max
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi
