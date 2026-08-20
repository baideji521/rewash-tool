# -*- coding: utf-8 -*-
"""batch.worker — 批量处理工作器

伪并行教训（Bug#1 关联）：禁止任务循环内每任务新建线程池并同步等待；
必须共享线程池一次性提交 + as_completed 收集，计数器与文件名分配加锁。

工程化能力：实时 ETA / 失败重试 / 断点恢复 / 批次日志。
"""
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..core.processor import process_one
from ..core.config import config_get
from ..core.ffmpeg_runner import STOP_EVENT
from .scheduler import GPUScheduler
from .checkpoint import Checkpoint
from .retry import run_with_retry

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


class BatchLogger:
    """批次日志：时间/文件/阶段/耗时/结果/错误原因，按批次存文件"""

    def __init__(self, batch_id: str, log_fn=None):
        _LOG_DIR.mkdir(exist_ok=True)
        self.path = _LOG_DIR / f"batch_{batch_id}.log"
        self._lock = threading.Lock()
        self._ui_log = log_fn or (lambda m: None)

    def write(self, msg: str, file: str = ""):
        line = f"[{time.strftime('%H:%M:%S')}] {file + ' | ' if file else ''}{msg}"
        with self._lock:
            self._ui_log(line)
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass


class BatchRunner:
    def __init__(self, config: dict, preset: dict, batch_id: str = None,
                 log_fn=None, progress_cb=None, file_progress_cb=None):
        self.config = config
        self.preset = preset
        self.batch_id = batch_id or time.strftime("%Y%m%d_%H%M%S")
        self.logger = BatchLogger(self.batch_id, log_fn)
        self.progress_cb = progress_cb or (lambda done, total, eta_s, cur: None)
        # file_progress_cb(path, frac 0~1)：单文件内部实时进度（进度条平滑）
        self.file_progress_cb = file_progress_cb or (lambda path, frac: None)
        self.scheduler = GPUScheduler(config)
        self.checkpoint = Checkpoint(self.batch_id)
        self._lock = threading.Lock()
        self._name_lock = threading.Lock()
        self.results = []

    def _alloc_output(self, input_path: str, output_dir: str, ver_idx: int,
                      ver_total: int, stamp: str) -> str:
        """线程安全输出文件名：原名_时间戳（_隔开），多版本追加 _vN"""
        stem = Path(input_path).stem
        suffix = f"_v{ver_idx}" if ver_total > 1 else ""
        with self._name_lock:
            candidate = os.path.join(output_dir, f"{stem}_{stamp}{suffix}.mp4")
            if not os.path.exists(candidate):
                return candidate
            return os.path.join(output_dir,
                                f"{stem}_{stamp}{suffix}_{os.getpid()}%{int(time.time() * 1000) % 10000}.mp4".replace("%", "_"))

    def run(self, inputs: list, output_dir: str, resume: bool = True) -> dict:
        """
        批量处理。resume=True 时按检查点跳过已完成项（断点恢复）。
        返回 {"total","done","failed","elapsed","failed_items"}
        """
        t0 = time.time()
        os.makedirs(output_dir, exist_ok=True)
        STOP_EVENT.clear()

        # GPU 调度前一次性探测（Bug#1 修正）
        self.scheduler.probe()
        self.logger.write(f"批次 {self.batch_id} 开始 | {self.scheduler.summary()} | "
                          f"预设={self.preset.get('label')}")

        pending = self.checkpoint.list_pending(inputs) if resume else list(inputs)
        skipped = len(inputs) - len(pending)
        if skipped:
            self.logger.write(f"断点恢复：跳过 {skipped} 个已完成文件")
        if not pending:
            self.logger.write("无待处理文件")
            return {"total": len(inputs), "done": skipped, "failed": 0,
                    "elapsed": 0.0, "failed_items": {}}

        total = len(pending)
        done_cnt, fail_cnt = 0, 0
        elapsed_list = []
        max_retries = int(config_get(self.config, "retry.max_retries", 1))
        version_count = max(1, int(config_get(self.config, "version_count", 1) or 1))

        def task_one(input_path: str):
            nonlocal done_cnt, fail_cnt
            if STOP_EVENT.is_set():
                return input_path, False, "stopped"
            acquired = self.scheduler.acquire_gpu()
            try:
                ver_ok = 0
                last_res = None
                for v in range(1, version_count + 1):
                    if STOP_EVENT.is_set():
                        break
                    stamp = time.strftime("%Y%m%d_%H%M%S")
                    out_path = self._alloc_output(input_path, output_dir,
                                                  v, version_count, stamp)
                    if version_count > 1:
                        self.logger.write(f"生成版本 {v}/{version_count}",
                                          os.path.basename(input_path))

                    def attempt(att):
                        res = process_one(
                            input_path, out_path, self.preset, self.config,
                            use_nvenc=self.scheduler.use_nvenc,
                            log_fn=lambda m: self.logger.write(m, os.path.basename(input_path)),
                            progress_cb=lambda stage, frac: self.file_progress_cb(input_path, frac))
                        return res["success"], res
                    ok, res, attempts = run_with_retry(
                        attempt, max_retries=max_retries,
                        log_fn=lambda m: self.logger.write(m, os.path.basename(input_path)),
                        label=os.path.basename(input_path))
                    last_res = res
                    if ok:
                        ver_ok += 1
                        self.checkpoint.mark_done(input_path, out_path, res.get("elapsed", 0))
                        issues = res.get("issues") or []
                        sim = res.get("fingerprint_sim")
                        extra = f" 指纹={sim:.3f}" if sim is not None else ""
                        extra += (" ⚠" + ";".join(issues)) if issues else ""
                        self.logger.write(
                            f"✓ 完成 耗时{res.get('elapsed')}s 重试{attempts - 1}次{extra}",
                            os.path.basename(input_path))
                        elapsed_list.append(res.get("elapsed", 30.0))
                    else:
                        reason = "; ".join(res.get("issues", [])) if isinstance(res, dict) else str(res)
                        self.logger.write(f"✗ 版本{v}失败: {reason[:200]}",
                                          os.path.basename(input_path))
                ok = ver_ok == version_count and ver_ok > 0
                res = last_res if isinstance(last_res, dict) else {"issues": [str(last_res)]}
            finally:
                self.scheduler.release_gpu(acquired)

            with self._lock:
                if ok:
                    done_cnt += 1
                    self.file_progress_cb(input_path, 1.0)
                else:
                    fail_cnt += 1
                    reason = "; ".join(res.get("issues", [])) if isinstance(res, dict) else str(res)
                    self.checkpoint.mark_failed(input_path, reason or "未知错误")
                    self.logger.write(f"✗ 失败: {reason[:200]}", os.path.basename(input_path))

                # 实时 ETA：已完成平均耗时 × 剩余 / 并发
                finished = done_cnt + fail_cnt
                remaining = total - finished
                avg = sum(elapsed_list) / len(elapsed_list) if elapsed_list else 60.0
                workers = max(1, self.scheduler.max_workers)
                eta = avg * remaining / workers * max(1, version_count)
                self.progress_cb(finished, total, eta, os.path.basename(input_path))
            return input_path, ok, ""

        workers = max(1, min(self.scheduler.max_workers, total))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(task_one, p): p for p in pending}
            for fut in as_completed(futures):
                path = futures[fut]
                try:
                    _, ok, _info = fut.result()
                    self.results.append((path, ok))
                except Exception as e:
                    with self._lock:
                        fail_cnt += 1
                    self.checkpoint.mark_failed(path, str(e))
                    self.logger.write(f"✗ 异常: {e}", os.path.basename(path))
                    self.results.append((path, False))
                if STOP_EVENT.is_set():
                    self.logger.write("⏹ 收到停止信号，等待运行中任务终止…")

        total_elapsed = round(time.time() - t0, 1)
        self.logger.write(
            f"批次结束：成功 {done_cnt}/{total}，失败 {fail_cnt}，耗时 {total_elapsed}s")
        return {
            "total": len(inputs), "done": done_cnt + skipped, "failed": fail_cnt,
            "elapsed": total_elapsed,
            "failed_items": self.checkpoint.failed_items(),
        }

    def rerun_failed(self, inputs: list, output_dir: str) -> dict:
        """只重跑失败文件"""
        failed = set(k.lower() for k in self.checkpoint.failed_items())
        subset = [p for p in inputs if str(p).lower() in failed]
        # 清除失败记录使其可重新处理
        for p in subset:
            self.checkpoint._state.get("failed", {}).pop(str(p).lower(), None)
        self.checkpoint._save()
        return self.run(subset, output_dir, resume=False)
