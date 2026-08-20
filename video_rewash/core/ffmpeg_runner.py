# -*- coding: utf-8 -*-
"""core.ffmpeg_runner — 统一 FFmpeg 执行器

v7.0 修正：
- Bug#16: 自动注入 -nostdin，批量运行不因 stdin 继承挂起
- Bug#6: Popen + 轮询，支持停止信号 terminate/kill
- 后台线程读管道防缓冲区死锁；超时自动 kill
- 附带 ffprobe 媒体探测（带缓存，Bug#9）
"""
import os
import re
import shutil
import subprocess as sp
import sys
import threading
import time
from pathlib import Path

# 停止信号：set() 后所有运行中的子进程会被 terminate
STOP_EVENT = threading.Event()

_PROBE_CACHE = {}
_PROBE_LOCK = threading.Lock()


def no_window_kwargs() -> dict:
    """Windows 下隐藏子进程控制台黑窗口（ffmpeg/ffprobe/nvidia-smi 通用）"""
    if sys.platform != "win32":
        return {}
    si = sp.STARTUPINFO()
    si.dwFlags |= sp.STARTF_USESHOWWINDOW
    return {"startupinfo": si,
            "creationflags": getattr(sp, "CREATE_NO_WINDOW", 0)}


class FFResult:
    """与 sp.run 返回对象兼容的结果"""
    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run_ffmpeg(cmd, timeout=900, progress_cb=None, total_duration=None):
    """
    统一执行 ffmpeg/ffprobe 命令。
    被停止返回 returncode=-15，超时返回 -9。
    progress_cb(frac 0~1)：传入且 total_duration>0 时，自动注入
    -progress pipe:1 实时解析输出时长并回调（用于进度条）。
    """
    if not cmd:
        return FFResult(-1, "", "empty command")
    if "-nostdin" not in cmd:
        cmd = [cmd[0], "-nostdin"] + list(cmd[1:])
    use_progress = (progress_cb is not None and total_duration
                    and float(total_duration) > 0.5
                    and "-progress" not in cmd)
    if use_progress:
        cmd = cmd + ["-progress", "pipe:1", "-nostats"]

    if STOP_EVENT.is_set():
        return FFResult(-15, "", "stopped before start")

    try:
        proc = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.PIPE, **no_window_kwargs())
    except Exception as e:
        return FFResult(-1, "", str(e))

    out_buf, err_buf = [], []
    total_dur = float(total_duration or 0)
    _last_emit = [0.0]

    def _emit(t_sec):
        """解析到输出时长 → 换算进度回调（限频 0.5s）"""
        now = time.time()
        if now - _last_emit[0] < 0.5:
            return
        _last_emit[0] = now
        try:
            frac = min(0.99, max(0.0, t_sec / total_dur))
            progress_cb(frac)
        except Exception:
            pass

    def _reader(stream, buf, parse_progress=False):
        leftover = b""
        try:
            for chunk in iter(stream.read, b""):
                buf.append(chunk)
                if parse_progress:
                    data = leftover + chunk
                    lines = data.split(b"\n")
                    leftover = lines[-1]
                    for line in lines[:-1]:
                        m = re.match(rb"out_time_ms=(\d+)", line.strip())
                        if m:
                            _emit(int(m.group(1)) / 1_000_000.0)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t_out = threading.Thread(target=_reader,
                             args=(proc.stdout, out_buf, bool(use_progress)),
                             daemon=True)
    t_err = threading.Thread(target=_reader, args=(proc.stderr, err_buf, False),
                             daemon=True)
    t_out.start()
    t_err.start()

    start = time.time()
    reason = None  # "stopped" | "timeout"
    while proc.poll() is None:
        if STOP_EVENT.is_set():
            reason = "stopped"
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            break
        if time.time() - start > timeout:
            reason = "timeout"
            try:
                proc.kill()
            except Exception:
                pass
            break
        time.sleep(0.25)

    rc = proc.wait()
    t_out.join(timeout=5)
    t_err.join(timeout=5)
    stdout = b"".join(out_buf).decode("utf-8", errors="ignore")
    stderr = b"".join(err_buf).decode("utf-8", errors="ignore")

    if reason == "timeout":
        return FFResult(-9, stdout, stderr + "\n[timeout]")
    if reason == "stopped":
        return FFResult(-15, stdout, stderr + "\n[stopped by user]")
    return FFResult(rc, stdout, stderr)


# ──────────────────────────────────────────────────────────────
#  路径探测
# ──────────────────────────────────────────────────────────────

def detect_ffmpeg() -> str:
    base_dir = Path(__file__).resolve().parent.parent.parent
    candidates = [
        base_dir / "ffmpeg" / "bin" / "ffmpeg.exe",
        base_dir / "ffmpeg.exe",
        Path.cwd() / "ffmpeg" / "bin" / "ffmpeg.exe",
        Path.cwd() / "ffmpeg.exe",
    ]
    for item in candidates:
        if item.exists():
            return str(item.resolve())
    return shutil.which("ffmpeg")


def detect_ffprobe(ffmpeg_path: str) -> str:
    if not ffmpeg_path:
        return shutil.which("ffprobe")
    p = Path(ffmpeg_path)
    for item in (p.parent / "ffprobe.exe", p.parent.parent / "bin" / "ffprobe.exe"):
        if item.exists():
            return str(item.resolve())
    return shutil.which("ffprobe")


# ──────────────────────────────────────────────────────────────
#  媒体探测（带缓存，Bug#9 修正）
# ──────────────────────────────────────────────────────────────

def probe_media(ffprobe_path: str, video_path: str) -> dict:
    """
    一次性探测媒体信息：时长/流/分辨率/帧率/编码。
    按 path+size+mtime 缓存，文件变更后自动失效。
    失败返回空 dict。
    """
    try:
        st = os.stat(video_path)
        key = (video_path, st.st_size, int(st.st_mtime))
        with _PROBE_LOCK:
            if key in _PROBE_CACHE:
                return _PROBE_CACHE[key]

        cmd = [
            ffprobe_path, "-v", "error",
            "-show_entries",
            "format=duration,bit_rate:stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels,bit_rate",
            "-of", "json", video_path
        ]
        r = sp.run(cmd, capture_output=True, text=True, encoding="utf-8",
                   errors="ignore", timeout=15, **no_window_kwargs())
        if r.returncode != 0:
            return {}
        import json as _json
        info = _json.loads(r.stdout)
        result = {
            "duration": 0.0,
            "has_video": False,
            "has_audio": False,
            "width": 0, "height": 0, "fps": 0.0,
            "v_codec": "", "a_codec": "",
            "bit_rate": 0,      # format 总码率(bps)，体积对齐用
            "a_bit_rate": 0,    # 音频流码率(bps)
        }
        try:
            result["duration"] = float(info.get("format", {}).get("duration", 0))
        except (TypeError, ValueError):
            pass
        try:
            result["bit_rate"] = int(float(info.get("format", {}).get("bit_rate", 0) or 0))
        except (TypeError, ValueError):
            pass
        for s in info.get("streams", []):
            if s.get("codec_type") == "video" and not result["has_video"]:
                result["has_video"] = True
                result["width"] = int(s.get("width", 0) or 0)
                result["height"] = int(s.get("height", 0) or 0)
                result["v_codec"] = s.get("codec_name", "")
                try:
                    num, _, den = (s.get("r_frame_rate") or "0/1").partition("/")
                    result["fps"] = float(num) / float(den) if float(den or 0) else 0.0
                except (ValueError, ZeroDivisionError):
                    result["fps"] = 0.0
            elif s.get("codec_type") == "audio" and not result["has_audio"]:
                result["has_audio"] = True
                result["a_codec"] = s.get("codec_name", "")
                try:
                    result["a_bit_rate"] = int(float(s.get("bit_rate", 0) or 0))
                except (TypeError, ValueError):
                    pass
        with _PROBE_LOCK:
            _PROBE_CACHE[key] = result
        return result
    except Exception:
        return {}


def has_audio(ffprobe_path: str, video_path: str) -> bool:
    return bool(probe_media(ffprobe_path, video_path).get("has_audio", False))


def get_duration(ffprobe_path: str, video_path: str) -> float:
    return float(probe_media(ffprobe_path, video_path).get("duration", 0.0) or 0.0)


# ──────────────────────────────────────────────────────────────
#  GPU 能力探测（Bug#1: 调度前一次性探测）
# ──────────────────────────────────────────────────────────────

def test_nvenc(ffmpeg_path: str) -> bool:
    """实际编码 1 帧验证 NVENC 可用（编码器列表有 ≠ 能用）"""
    try:
        r = run_ffmpeg([
            ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
            "-t", "0.5", "-c:v", "h264_nvenc", "-preset", "p1",
            "-f", "null", "-"
        ], timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def get_gpu_name() -> str:
    try:
        r = sp.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                   capture_output=True, text=True, timeout=10,
                   **no_window_kwargs())
        return r.stdout.strip() or "未知GPU"
    except Exception:
        return "未知GPU"


def get_gpu_encoder_usage() -> int:
    """GPU 编码器利用率（%），失败返回 0"""
    try:
        r = sp.run(["nvidia-smi", "--query-gpu=utilization.encoder",
                    "--format=csv,noheader,nounits"],
                   capture_output=True, text=True, timeout=10,
                   **no_window_kwargs())
        return int(r.stdout.strip())
    except Exception:
        return 0
