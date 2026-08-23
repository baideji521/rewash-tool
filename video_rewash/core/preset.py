# -*- coding: utf-8 -*-
"""core.preset — 预设管理

v7.1 设计：
- 内置三档（温和/标准/激进）出厂值在 builtin.json
- 用户可覆盖内置预设（存 custom.json 同名键），也可恢复出厂
- 自定义预设存 presets/custom.json，可保存/覆盖/删除
- Bug#5 修正：预设是完整 min/max 字典，不做比例乘法
- 激活 = 深拷贝快照，后续修改不影响原预设
"""
import copy
import json
import threading
from pathlib import Path

_PRESET_DIR = Path(__file__).resolve().parent.parent / "presets"
BUILTIN_FILE = _PRESET_DIR / "builtin.json"
CUSTOM_FILE = _PRESET_DIR / "custom.json"

BUILTIN_KEYS = ("gentle", "standard", "aggressive")

_LOCK = threading.RLock()

_builtin_cache = None
_custom_cache = None


def _load_builtin() -> dict:
    global _builtin_cache
    with _LOCK:
        if _builtin_cache is None:
            try:
                with open(BUILTIN_FILE, "r", encoding="utf-8") as f:
                    _builtin_cache = json.load(f)
            except Exception as e:
                print(f"[preset] 内置预设加载失败: {e}")
                _builtin_cache = {}
        return _builtin_cache


def _load_custom() -> dict:
    global _custom_cache
    with _LOCK:
        if _custom_cache is None:
            _custom_cache = {}
            try:
                if CUSTOM_FILE.exists():
                    with open(CUSTOM_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        _custom_cache = data
            except Exception as e:
                print(f"[preset] 自定义预设加载失败: {e}")
        return _custom_cache


def _save_custom():
    try:
        _PRESET_DIR.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            data = copy.deepcopy(_custom_cache or {})
        with open(CUSTOM_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[preset] 自定义预设保存失败: {e}")
        return False


def list_presets() -> list:
    """返回 [(key, label, builtin), ...]，内置在前。被覆盖的内置预设仍显示为内置（带*标记由 UI 处理）"""
    result = []
    bi = _load_builtin()
    for key in BUILTIN_KEYS:
        if key in bi:
            result.append((key, bi[key].get("label", key), True))
    for key, val in _load_custom().items():
        if (not key.startswith("_") and isinstance(val, dict)
                and key not in BUILTIN_KEYS):
            result.append((key, val.get("label", key), False))
    return result


def is_overridden(name: str) -> bool:
    """内置预设是否已被用户覆盖"""
    return name in BUILTIN_KEYS and name in _load_custom()


def get_preset(name: str) -> dict:
    """
    获取预设的深拷贝快照（激活语义）。
    内置预设优先取 custom.json 中的用户覆盖，没有则取 builtin.json 出厂值。
    结构: {"name","label","builtin","overridden","params":{...}}
    找不到返回标准档。
    """
    bi = _load_builtin()
    cu = _load_custom()
    if name in bi and isinstance(bi[name], dict):
        node = bi[name]
        override = cu.get(name)
        overridden = isinstance(override, dict)
        params = override.get("params", {}) if overridden else node.get("params", {})
        return {
            "name": name,
            "label": node.get("label", name),
            "builtin": True,
            "overridden": overridden,
            "params": copy.deepcopy(params),
        }
    if name in cu and isinstance(cu[name], dict):
        node = cu[name]
        return {
            "name": name,
            "label": node.get("label", name),
            "builtin": False,
            "overridden": False,
            "params": copy.deepcopy(node.get("params", {})),
        }
    # 回退标准档
    return get_preset("standard") if name != "standard" else {
        "name": "standard", "label": "标准", "builtin": True,
        "overridden": False, "params": {}
    }


def get_param(preset: dict, key: str, default=None):
    """安全读取预设参数（永不抛异常）"""
    try:
        return preset.get("params", {}).get(key, default)
    except Exception:
        return default


def save_custom(name: str, params: dict, label: str = None) -> bool:
    """
    保存/覆盖预设。v7.1：允许内置键名（写入 custom.json 作为用户覆盖，
    可用 restore_builtin 恢复出厂）。
    """
    if name.startswith("_") or not name.strip():
        return False
    with _LOCK:
        _load_custom()
        node = {"params": copy.deepcopy(params)}
        if name in BUILTIN_KEYS:
            # 覆盖内置：label 保持出厂名称
            bi = _load_builtin()
            node["label"] = bi.get(name, {}).get("label", name)
        else:
            node["label"] = label or name
        _custom_cache[name] = node
    return _save_custom()


def restore_builtin(name: str) -> bool:
    """恢复内置预设为出厂状态（删除 custom.json 中的覆盖）"""
    if name not in BUILTIN_KEYS:
        return False
    with _LOCK:
        _load_custom()
        if name in _custom_cache:
            del _custom_cache[name]
            return _save_custom()
    return True


def delete_custom(name: str) -> bool:
    """删除自定义预设（内置键名请走 restore_builtin）"""
    if name in BUILTIN_KEYS:
        return False
    with _LOCK:
        _load_custom()
        if name in _custom_cache:
            del _custom_cache[name]
            return _save_custom()
    return False
