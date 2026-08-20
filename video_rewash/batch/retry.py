# -*- coding: utf-8 -*-
"""batch.retry — 失败重试策略

自动重试 1 次（配置 retry.max_retries），最终失败入失败队列，
支持只重跑失败文件。
"""


def run_with_retry(task_fn, max_retries: int = 1, log_fn=None, label: str = ""):
    """
    task_fn(attempt) -> (success: bool, payload)
    返回最终 (success, payload, attempts)。
    """
    log_fn = log_fn or (lambda m: None)
    attempts = 0
    while True:
        attempts += 1
        try:
            success, payload = task_fn(attempts)
        except Exception as e:
            success, payload = False, {"error": str(e)}
        if success:
            return True, payload, attempts
        if attempts > max_retries:
            return False, payload, attempts
        log_fn(f"⟳ 重试 {label}（第{attempts}次失败: "
               f"{str(payload.get('error', payload) if isinstance(payload, dict) else payload)[:120]}）")
