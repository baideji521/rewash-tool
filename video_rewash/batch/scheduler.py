# -*- coding: utf-8 -*-
"""batch.scheduler — GPU 调度与并发控制

Bug#1 修正：GPU 能力在调度前一次性探测（而非运行时反复查询），
信号量控制 NVENC 并发数（消费级 GPU 编码会话数有限，超限会报错）。
"""
import os
import threading

from ..core.ffmpeg_runner import test_nvenc, get_gpu_name, get_gpu_encoder_usage
from ..core.config import config_get


class GPUScheduler:
    """调度前一次性探测 GPU 能力，决定并发策略"""

    def __init__(self, config: dict):
        self._config = config
        self.use_nvenc = False
        self.gpu_name = "未知GPU"
        self.max_workers = 2
        self.nvenc_semaphore = None
        self._probed = False

    def probe(self):
        """调度前探测（只执行一次）"""
        if self._probed:
            return
        self._probed = True
        cfg = self._config
        ffmpeg_path = config_get(cfg, "runtime.ffmpeg", "ffmpeg")
        gpu_auto = bool(config_get(cfg, "encode.gpu_auto", True))
        self.gpu_name = get_gpu_name() if gpu_auto else "禁用"
        self.use_nvenc = gpu_auto and test_nvenc(ffmpeg_path)

        max_sessions = int(config_get(cfg, "performance.nvenc_max_sessions", 3))
        self.nvenc_semaphore = threading.Semaphore(max(1, max_sessions))

        # 并发数：auto 按 CPU 核数，但不超过显式上限
        workers_cfg = config_get(cfg, "performance.workers", {}) or {}
        if workers_cfg.get("auto", True):
            cpu = os.cpu_count() or 4
            self.max_workers = max(1, min(4, cpu // 2))
        else:
            self.max_workers = max(1, int(workers_cfg.get("max", 2)))
        # GPU 编码时并发受 NVENC 会话数约束
        if self.use_nvenc:
            self.max_workers = min(self.max_workers, max_sessions + 1)

    def summary(self) -> str:
        return (f"GPU={self.gpu_name} NVENC={'可用' if self.use_nvenc else '不可用'} "
                f"并发={self.max_workers}")

    def acquire_gpu(self):
        if self.use_nvenc and self.nvenc_semaphore:
            self.nvenc_semaphore.acquire()
            return True
        return False

    def release_gpu(self, acquired: bool):
        if acquired and self.nvenc_semaphore:
            self.nvenc_semaphore.release()

    def encoder_load(self) -> int:
        """运行时负载查询（仅用于日志/ETA显示，不参与调度决策）"""
        return get_gpu_encoder_usage() if self.use_nvenc else 0
