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
from ..core.ffmpeg_runner import STOP_EVENT  # noqa: F401 (re-exported)
from .scheduler import GPUScheduler
from .checkpoint import Checkpoint
# retry 已移入 process_one() 内部（指纹重试）

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
        version_count = max(1, int(config_get(self.config, "version_count", 1) or 1))

        # 任务单位 = (视频, 版本)：并发数 N 即最多 N 个（视频/版本）任务同时跑；
        # 1 视频×1 版本=1 任务（顺序），1 视频×5 版本=5 任务，5 视频×1 版本=5 任务；
        # 任务总数低于并发数时按任务数开线程。
        tasks = [(p, v) for p in pending for v in range(1, version_count + 1)]
        # 按视频聚合：一个视频的全部版本结束后才计 1 个完成（检查点/进度按视频粒度）
        ver_left = {p: version_count for p in pending}
        ver_ok_cnt = {p: 0 for p in pending}
        last_out = {}
        last_reason = {}

        def task_one(input_path: str, ver: int):
            if STOP_EVENT.is_set():
                return input_path, ver, False, {"issues": ["stopped"]}
            acquired = self.scheduler.acquire_gpu()
            try:
                stamp = time.strftime("%Y%m%d_%H%M%S")
                out_path = self._alloc_output(input_path, output_dir,
                                              ver, version_count, stamp)
                if version_count > 1:
                    self.logger.write(f"生成版本 {ver}/{version_count}",
                                      os.path.basename(input_path))

                # process_one 内部已包含指纹重试逻辑（单视频流程不变：
                # 每次重试重新生成随机参数，最多 4 次）
                res = process_one(
                    input_path, out_path, self.preset, self.config,
                    use_nvenc=self.scheduler.use_nvenc,
                    log_fn=lambda m: self.logger.write(
                        m, os.path.basename(input_path)),
                    progress_cb=lambda stage, frac:
                        self.file_progress_cb(input_path, frac))
                ok = res.get("success", False)
                fp_pass = res.get("fp_pass", True)
                attempts = res.get("fp_attempts", 1)
                if ok:
                    # 文件已生成即计入完成（指纹未达标仅警告，保留文件）
                    if not fp_pass:
                        issues = [i for i in (res.get("issues") or [])
                                  if "指纹相似度" not in i and "超过阈值" not in i
                                  and "最大重试次数" not in i]
                        sim = res.get("fingerprint_sim")
                        extra = f" 指纹={sim:.3f}" if sim is not None else ""
                        if issues:
                            extra += " ⚠ " + ";".join(issues)
                        tag = "⚠ 完成（指纹未达标）"
                    else:
                        issues = res.get("issues") or []
                        sim = res.get("fingerprint_sim")
                        extra = f" 指纹={sim:.3f}" if sim is not None else ""
                        extra += (" ⚠ " + ";".join(issues)) if issues else ""
                        tag = "✓ 完成"
                    enc_t = res.get('encode_time')
                    fp_t = res.get('fp_time')
                    timing = ""
                    if enc_t is not None:
                        timing += f" 编码={enc_t}s"
                    if fp_t is not None:
                        timing += f" 指纹={fp_t}s"
                    self.logger.write(
                        f"{tag} 耗时{res.get('elapsed')}s"
                        f" 重试{attempts - 1}次{timing}{extra}",
                        os.path.basename(input_path))
                    elapsed_list.append(res.get("elapsed", 30.0))
                else:
                    reason = "; ".join(res.get("issues", [])) if isinstance(res, dict) else str(res)
                    self.logger.write(f"✗ 版本{ver}失败: {reason[:200]}",
                                      os.path.basename(input_path))
                return input_path, ver, ok, res
            finally:
                self.scheduler.release_gpu(acquired)

        workers = max(1, min(self.scheduler.max_workers, len(tasks)))
        self.logger.write(f"并发={workers}（任务数={len(tasks)}："
                          f"{total} 视频 × {version_count} 版本）")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(task_one, p, v): (p, v) for p, v in tasks}
            for fut in as_completed(futures):
                path, ver = futures[fut]
                try:
                    input_path, _v, ok, res = fut.result()
                except Exception as e:
                    ok, res = False, {"issues": [str(e)]}
                    self.logger.write(f"✗ 异常: {e}", os.path.basename(path))

                with self._lock:
                    if ok:
                        ver_ok_cnt[path] += 1
                    else:
                        reason = "; ".join(res.get("issues", [])) if isinstance(res, dict) else str(res)
                        last_reason[path] = reason or "未知错误"
                    # 记录该视频最新成功输出（用于检查点）
                    if ok and isinstance(res, dict) and res.get("output"):
                        last_out[path] = res.get("output")
                    ver_left[path] -= 1
                    if ver_left[path] == 0:
                        # 该视频全部版本结束 → 视频粒度结算
                        if ver_ok_cnt[path] == version_count and ver_ok_cnt[path] > 0:
                            done_cnt += 1
                            self.checkpoint.mark_done(
                                path, last_out.get(path, ""),
                                sum(elapsed_list[-version_count:])
                                / max(1, len(elapsed_list[-version_count:])))
                            self.file_progress_cb(path, 1.0)
                        else:
                            fail_cnt += 1
                            # 指纹未达标但文件已生成：不重复输出“✗ 失败”
                            if ver_ok_cnt[path] == 0:
                                self.checkpoint.mark_failed(
                                    path, last_reason.get(path, "未知错误"))
                                self.logger.write(
                                    f"✗ 失败: {last_reason.get(path, '未知错误')[:200]}",
                                    os.path.basename(path))
                            else:
                                self.checkpoint.mark_done(
                                    path, last_out.get(path, ""), 0)
                        self.results.append(
                            (path, ver_ok_cnt[path] == version_count
                             and ver_ok_cnt[path] > 0))

                        # 实时 ETA：已完成平均耗时 × 剩余任务 / 并发（视频粒度）
                        finished = done_cnt + fail_cnt
                        remaining_versions = sum(ver_left.values())
                        avg = sum(elapsed_list) / len(elapsed_list) if elapsed_list else 60.0
                        eta = avg * remaining_versions / max(1, workers)
                        self.progress_cb(finished, total, eta,
                                         os.path.basename(path))
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
