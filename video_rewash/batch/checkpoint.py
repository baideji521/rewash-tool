# -*- coding: utf-8 -*-
"""batch.checkpoint — 批次断点恢复

批次中断后跳过已完成文件，不全部重跑。
检查点按批次 ID 存 logs/checkpoint_<batch_id>.json。
"""
import json
import time
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


class Checkpoint:
    def __init__(self, batch_id: str):
        self.batch_id = batch_id
        _LOG_DIR.mkdir(exist_ok=True)
        self.path = _LOG_DIR / f"checkpoint_{batch_id}.json"
        self._state = {"done": {}, "failed": {}, "started": time.time()}
        self.load()

    def load(self):
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._state.update(data)
        except Exception:
            pass

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def is_done(self, input_path: str) -> bool:
        key = str(input_path).lower()
        return key in self._state.get("done", {})

    def mark_done(self, input_path: str, output_path: str, elapsed: float):
        self._state.setdefault("done", {})[str(input_path).lower()] = {
            "output": output_path, "elapsed": elapsed,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._state.get("failed", {}).pop(str(input_path).lower(), None)
        self._save()

    def mark_failed(self, input_path: str, reason: str):
        self._state.setdefault("failed", {})[str(input_path).lower()] = {
            "reason": reason[:500],
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save()

    def list_pending(self, all_paths: list) -> list:
        """断点恢复：过滤已完成项"""
        return [p for p in all_paths if not self.is_done(p)]

    def failed_items(self) -> dict:
        return dict(self._state.get("failed", {}))

    def cleanup(self):
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass
