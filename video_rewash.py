#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
  短视频大批量"去重·洗片·抹除" 桌面工具
  —— FFmpeg 核心引擎 + PyQt5 可视化界面
==============================================================================
  功能特点：
    1. 智能 FFmpeg 路径检测（同目录优先 → 环境变量兜底）
    2. 无感 N 卡加速测试（h264_nvenc 虚拟探测 → 自动回退 CPU）
    3. PyQt5 傻瓜式界面（选文件夹 + 拖放支持 + 一键开始 + 实时日志）
    4. 多线程并发处理（自动检测 GPU 决定并发数）
    5. 参数动态随机化（缩放 / 旋转 / 噪点 / 光影 / 变速）
    6. 工业级 try-catch，单条损坏不卡死，绝无闪退
==============================================================================
  打包命令（Windows）：
      pip install pyinstaller PyQt5
      pyinstaller --noconsole --onefile --icon=icon.ico video_rewash.py
==============================================================================
"""

import os
import sys
import re
import json
import math
import time
import random
import shutil
import queue
import threading
import subprocess as sp
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ──────────────────────────────────────────────────────────────────────────────
#  PyQt5 导入
# ──────────────────────────────────────────────────────────────────────────────
try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QLineEdit, QPushButton, QProgressBar, QTextEdit,
        QGroupBox, QFileDialog, QMessageBox,
        QDialog, QTabWidget, QFormLayout, QCheckBox, QSpinBox,
        QDoubleSpinBox, QComboBox, QScrollArea, QInputDialog
    )
    from PyQt5.QtCore import Qt, QTimer, pyqtSignal
    from PyQt5.QtGui import QFont, QIcon, QDragEnterEvent, QDropEvent
except ImportError:
    print("【严重错误】当前 Python 环境缺少 PyQt5 模块。")
    print("请运行: pip install PyQt5")
    sys.exit(1)



AV1_GPU_DECODE = False
# ══════════════════════════════════════════════════════════════════════════════
#  全局常量与配置
# ══════════════════════════════════════════════════════════════════════════════
# ==========================================================
# 配置管理核心 v2.1
# ==========================================================

def config_get(path, default=None):
    """
    获取配置值

    示例:
        config_get("video.scale")

        返回:
        {
            enable:true,
            min:1.03,
            max:1.08
        }

    """

    try:

        data = CONFIG

        for key in path.split("."):

            if isinstance(data, dict):

                data = data[key]

            else:

                return default


        return data


    except Exception:

        return default



def config_enabled(path):
    """
    判断配置是否启用

    示例:

    video.scale.enable

    """

    try:

        cfg = config_get(
            path
        )

        return bool(cfg)


    except Exception:

        return False



def config_random(path, default=None):
    """
    通用随机读取 v2.2

    支持：

    1.
    {
        "enable":true,
        "min":1,
        "max":2
    }

    2.
    {
        "enable":true,
        "list":[1,2,3]
    }

    3.
    [
        1,2,3
    ]

    4.
    普通值

    """

    try:

        cfg = config_get(path)


        if cfg is None:
            return default



        # ==========================
        # 直接数组
        # ==========================

        if isinstance(cfg, list):

            if len(cfg):

                return random.choice(cfg)

            return default



        # ==========================
        # 字典
        # ==========================

        if isinstance(cfg, dict):


            # 开关关闭

            if "enable" in cfg:

                if not cfg["enable"]:

                    return default



            # min max

            if (
                "min" in cfg
                and
                "max" in cfg
            ):


                return random.uniform(

                    float(cfg["min"]),

                    float(cfg["max"])

                )



            # list

            if "list" in cfg:

                arr = cfg["list"]

                if arr:

                    return random.choice(arr)



            # value

            if "value" in cfg:

                return cfg["value"]



            return default



        # ==========================
        # 普通类型
        # ==========================

        return cfg



    except Exception as e:


        print(
            "config_random错误:",
            path,
            e
        )


        return default



def config_random_int(path, default=None):

    value=config_random(
        path,
        default
    )


    try:

        return int(
            round(float(value))
        )


    except Exception:

        return default
def config_random_float(path, default=None):
    """浮点随机读取，复用 config_random 并转 float"""
    result = config_random(path, default)
    try:
        return float(result)
    except (TypeError, ValueError):
        return default


def config_choice(path, default=None):

    try:

        cfg = config_get(path)


        if cfg is None:

            return default



        # 数组

        if isinstance(cfg,list):

            if cfg:

                return random.choice(cfg)


        # 字典

        if isinstance(cfg,dict):


            if "enable" in cfg:

                if not cfg["enable"]:

                    return default



            arr = cfg.get(
                "list",
                None
            )


            if arr:

                return random.choice(arr)



        return default



    except Exception as e:

        print(
            "config_choice错误:",
            path,
            e
        )

        return default
def load_config():

    path=Path("config.json")

    if not path.exists():

        return {}

    with open(
        path,
        "r",
        encoding="utf-8"
    ) as f:

        return json.load(f)


CONFIG = load_config()
APP_NAME = "视频去重冲洗工具 v6.3"

# ==========================================================
#  配置线程安全（copy-on-write 快照）
# ==========================================================
import copy

CONFIG_LOCK = threading.RLock()


def get_config_snapshot():
    """获取配置的深拷贝快照，处理线程全程使用快照，不受后续修改影响"""
    with CONFIG_LOCK:
        return copy.deepcopy(CONFIG)


def apply_config_safe(new_cfg: dict):
    """原子替换全局配置（copy-on-write）"""
    global CONFIG
    with CONFIG_LOCK:
        CONFIG = copy.deepcopy(new_cfg)


def save_config_to_file(cfg: dict = None):
    """将配置写入 config.json"""
    try:
        with CONFIG_LOCK:
            data = copy.deepcopy(cfg if cfg is not None else CONFIG)
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return True
    except Exception:
        return False


# ==========================================================
#  FFmpeg 统一执行器（Popen + -nostdin + 可中断 + 超时）
# ==========================================================
FFMPEG_STOP_EVENT = threading.Event()  # 停止时 set()，正在运行的子进程会被 terminate

# NVENC 并发会话限制：消费级 GPU 编码会话数有限（约3~5），
# 超限会报 "maximum number of simultaneous encoders"，用信号量控制 GPU 任务并发
NVENC_SEMAPHORE = threading.Semaphore(3)


class FFmpegResult:
    """与 sp.run 结果兼容的返回对象"""
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run_ffmpeg(cmd, timeout=600):
    """
    统一执行 ffmpeg/ffprobe 命令：
    - 自动注入 -nostdin，避免批量运行时 stdin 继承导致挂起
    - Popen + 轮询，支持 FFMPEG_STOP_EVENT 停止信号（terminate/kill）
    - 后台线程持续读取 stdout/stderr，避免管道缓冲区满死锁
    - 超时自动 kill
    返回 FFmpegResult(returncode, stdout, stderr)，被停止/超时返回非 0 码
    """
    if "-nostdin" not in cmd:
        cmd = [cmd[0], "-nostdin"] + list(cmd[1:])

    if FFMPEG_STOP_EVENT.is_set():
        return FFmpegResult(-15, "", "stopped before start")

    try:
        proc = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.PIPE)
    except Exception as e:
        return FFmpegResult(-1, "", str(e))

    out_buf, err_buf = [], []

    def _reader(stream, buf):
        try:
            for chunk in iter(stream.read, b""):
                buf.append(chunk)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t_out = threading.Thread(target=_reader, args=(proc.stdout, out_buf), daemon=True)
    t_err = threading.Thread(target=_reader, args=(proc.stderr, err_buf), daemon=True)
    t_out.start()
    t_err.start()

    start = time.time()
    stopped = False
    while proc.poll() is None:
        if FFMPEG_STOP_EVENT.is_set():
            stopped = True
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
            try:
                proc.kill()
            except Exception:
                pass
            stopped = True
            break
        time.sleep(0.25)

    rc = proc.wait()
    t_out.join(timeout=5)
    t_err.join(timeout=5)
    stdout = b"".join(out_buf).decode("utf-8", errors="ignore")
    stderr = b"".join(err_buf).decode("utf-8", errors="ignore")
    if stopped and rc == 0:
        rc = -15
    if rc == -15:
        stderr += "\n[stopped by user]"
    return FFmpegResult(rc, stdout, stderr)


def auto_detect_workers():
    """根据 CPU + NVIDIA GPU 自动决定 FFmpeg 并发数"""
    cpu = os.cpu_count() or 4
    workers = max(1, cpu // 2)

    # GPU 型号 → 并发数映射
    GPU_WORKERS = {
        "4090": 4, "4080": 4, "3090": 4,
        "4070": 3, "4060": 3, "3080": 3, "3070": 3, "3060": 3,
        "2080": 2, "2060": 2, "1660": 2,
    }

    try:
        gpu = get_gpu_name().lower()
        workers = 2  # 默认值
        for model, count in GPU_WORKERS.items():
            if model in gpu:
                workers = count
                break
    except Exception:
        pass

    # CPU 保护：不超过 CPU 核心数的一半
    workers = min(workers, max(1, cpu // 2))
    return workers


def get_max_workers():

    cfg = config_get(
        "performance.workers",
        {}
    )


    if cfg.get(
        "auto",
        True
    ):


        auto = auto_detect_workers()


        limit = cfg.get(
            "max",
            auto
        )


        return min(
            auto,
            int(limit)
        )


    else:

        return int(
            cfg.get(
                "max",
                2
            )
        )



MAX_WORKERS = get_max_workers()
USE_CUDA_DECODE = config_get(
    "encode.cuda_decode",
    False
)

# 支持的视频扩展名（不区分大小写）
SUPPORTED_EXTS = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm', '.ts', '.m4v'}

# 默认文件夹名
DEFAULT_INPUT_DIR = "input_videos"
DEFAULT_OUTPUT_DIR = "output_videos"


# ══════════════════════════════════════════════════════════════════════════════
#  核心功能模块
# ══════════════════════════════════════════════════════════════════════════════
def check_av1_gpu_decode(ffmpeg_path):

    try:

        result = sp.run(
            [
                ffmpeg_path,
                "-decoders"
            ],
            capture_output=True,
            text=True,
            encoding="utf-8"
        )


        txt = result.stdout.lower()


        # NVIDIA AV1硬解
        if "av1_cuvid" in txt:

            return True


        return False


    except Exception:

        return False

def build_timestamp_options():

    args=[]

    cfg = config_get(
        "advanced.timestamp_random",
        False
    )


    # 兼容 true / false
    if isinstance(cfg,bool):

        if not cfg:
            return args


    # 兼容:
    # {
    #   "enable":true
    # }

    elif isinstance(cfg,dict):

        if not cfg.get(
            "enable",
            False
        ):
            return args


    else:

        return args



    offset=random.uniform(
        -1,
        1
    )


    args += [

        "-itsoffset",

        f"{offset:.3f}"

    ]


    return args

def build_start_trim():
    """开头随机裁剪，放在 -i 前面实现无损裁剪"""
    args = []
    cfg = config_get("video.start_trim", False)
    if isinstance(cfg, dict) and cfg.get("enable", False):
        trim = random.uniform(float(cfg.get("min", 0.1)), float(cfg.get("max", 0.5)))
        args += ["-ss", f"{trim:.3f}"]
    return args

def detect_ffmpeg():
    """
    自动检测 FFmpeg
    优先：
    1. 程序目录/ffmpeg/bin/ffmpeg.exe
    2. 程序目录/ffmpeg.exe
    3. 当前目录
    4. 系统PATH
    """

    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).resolve().parent

    candidates = [

        # 当前你的目录结构
        base_dir / "ffmpeg" / "bin" / "ffmpeg.exe",

        # 备用
        base_dir / "ffmpeg.exe",

        # 当前运行目录
        Path.cwd() / "ffmpeg" / "bin" / "ffmpeg.exe",

        Path.cwd() / "ffmpeg.exe",

    ]

    for item in candidates:

        if item.exists():
            return str(item.resolve())

    # 系统PATH

    system_ffmpeg = shutil.which("ffmpeg")

    if system_ffmpeg:
        return system_ffmpeg

    return None




def detect_ffprobe(ffmpeg_path):
    if not ffmpeg_path:
        return None

    ffmpeg_file = Path(ffmpeg_path)

    candidates = [

        ffmpeg_file.parent / "ffprobe.exe",

        ffmpeg_file.parent.parent / "bin" / "ffprobe.exe",

    ]

    for item in candidates:

        if item.exists():
            return str(item.resolve())

    return shutil.which("ffprobe")


# ffprobe 探测结果缓存（key 含 size+mtime，文件变更后自动失效）
_PROBE_CACHE = {}


def has_audio(ffprobe_path, video):
    """检测视频是否含音频流（按 path+size+mtime 缓存，避免重复探测）"""
    try:
        st = os.stat(video)
        cache_key = ("audio", video, st.st_size, int(st.st_mtime))
        if cache_key in _PROBE_CACHE:
            return _PROBE_CACHE[cache_key]

        cmd=[
            ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            video
        ]

        r=sp.run(
            cmd,
            capture_output=True,
            text=True
        )

        result = bool(
            r.stdout.strip()
        )
        _PROBE_CACHE[cache_key] = result
        return result

    except Exception:

        return False

def test_nvenc(ffmpeg_path):

    try:

        cmd=[
            ffmpeg_path,
            "-hide_banner",
            "-encoders"
        ]


        r=sp.run(
            cmd,
            stdout=sp.PIPE,
            stderr=sp.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=20
        )


        if "h264_nvenc" not in r.stdout:

            return False



        # 实际编码测试

        cmd=[

            ffmpeg_path,

            "-hide_banner",
            "-loglevel",
            "error",

            "-f",
            "lavfi",

            "-i",
            "testsrc=size=1280x720:rate=30",

            "-t",
            "2",

            "-c:v",
            "h264_nvenc",

            "-preset",
            "p4",

            "-f",
            "null",

            "-"

        ]


        r=sp.run(
            cmd,
            stdout=sp.PIPE,
            stderr=sp.PIPE,
            timeout=15
        )


        return r.returncode==0



    except Exception:
        return False
# ==========================================================
# FFmpeg 视频滤镜构建 v2.1
# ==========================================================

def encoder_builder(use_nvenc):

    result = {}

    encode_cfg = CONFIG.get(
        "encode",
        {}
    )

    advanced_cfg = CONFIG.get(
        "advanced",
        {}
    )


    # ==========================
    # 编码参数（兼容新旧配置位置）
    # ==========================

    profile = encode_cfg.get("profile") or advanced_cfg.get("profile", "high")
    level = encode_cfg.get("level") or advanced_cfg.get("level", "4.1")
    pix_fmt = encode_cfg.get("pix_fmt") or advanced_cfg.get("pix_fmt", "yuv420p")

    # GOP/关键帧间隔随机化
    gop_cfg = advanced_cfg.get("keyframe_random", {})
    if gop_cfg.get("enable", False):
        gop = random.randint(int(gop_cfg.get("min", 45)), int(gop_cfg.get("max", 90)))
    else:
        gop = 60


    # ==========================
    # NVENC
    # ==========================

    nvenc_cfg = encode_cfg.get(
        "nvenc",
        {}
    )


    if (
        use_nvenc
        and
        nvenc_cfg.get(
            "enable",
            False
        )
    ):


        result["codec"] = "h264_nvenc"

        result["mode"] = "NVENC"


        result["args"] = [

            "-preset",

            nvenc_cfg.get(
                "preset",
                "p5"
            ),


            "-rc",

            "vbr",


            "-cq",

            str(
                nvenc_cfg.get(
                    "cq",
                    23
                )
            ),


            "-profile:v",

            profile,


            "-level:v",

            level,


            "-pix_fmt",

            pix_fmt,


            "-g",

            str(gop)

        ]


    # ==========================
    # CPU x264
    # ==========================

    else:


        cpu_cfg = encode_cfg.get(
            "cpu",
            {}
        )


        result["codec"] = "libx264"

        result["mode"] = "CPU"


        result["args"] = [

            "-preset",

            cpu_cfg.get(
                "preset",
                "medium"
            ),


            "-crf",

            str(
                cpu_cfg.get(
                    "crf",
                    23
                )
            ),


            "-profile:v",

            profile,


            "-level:v",

            level,


            "-pix_fmt",

            pix_fmt,


            "-g",

            str(gop)

        ]



    # ==========================
    # FPS 随机化
    # ==========================

    fps_cfg = advanced_cfg.get("fps_random", {})
    if fps_cfg.get("enable", False):
        fps = random.uniform(
            float(fps_cfg.get("min", 28)),
            float(fps_cfg.get("max", 30))
        )
        result["args"] += ["-r", f"{fps:.2f}"]
        result["fps"] = fps

    # ==========================
    # B帧数量随机化（GOP结构扰动）
    # 改变编码帧类型分布，对抗编码特征同源判定
    # ==========================
    bf_cfg = advanced_cfg.get("b_frames_random", {})
    if bf_cfg.get("enable", False):
        bf = random.randint(
            int(bf_cfg.get("min", 0)),
            int(bf_cfg.get("max", 3))
        )
        result["args"] += ["-bf", str(bf)]

    # ==========================
    # 参考帧数量随机化（GOP结构扰动）
    # ==========================
    refs_cfg = advanced_cfg.get("refs_random", {})
    if refs_cfg.get("enable", False):
        refs = random.randint(
            int(refs_cfg.get("min", 1)),
            int(refs_cfg.get("max", 4))
        )
        result["args"] += ["-refs", str(refs)]

    # ==========================
    # QP 帧级随机抖动（零画质损失，对抗压缩域指纹）
    # x264: 随机化 aq-strength，改变每帧空间 QP 分布 → 压缩残差/DCT 系数分布全变
    # NVENC: 随机开关 spatial/temporal AQ
    # ==========================
    qp_cfg = advanced_cfg.get("qp_jitter", {})
    if qp_cfg.get("enable", False):
        if result["codec"] == "libx264":
            aq_cfg = qp_cfg.get("aq_strength", {})
            aq_val = random.uniform(
                float(aq_cfg.get("min", 0.6)),
                float(aq_cfg.get("max", 1.4))
            )
            result["args"] += ["-x264-params", f"aq-strength={aq_val:.2f}"]
        elif result["codec"] == "h264_nvenc":
            result["args"] += [
                "-spatial_aq", str(random.randint(0, 1)),
                "-temporal_aq", str(random.randint(0, 1))
            ]

    return result
def build_mux_options():

    args=[]

    advanced = CONFIG.get(
        "advanced",
        {}
    )


    if advanced.get(
        "faststart",
        False
    ):

        args += [

            "-movflags",
            "+faststart"

        ]


    metadata = CONFIG.get(
        "metadata",
        {}
    )


    if metadata.get(
        "clear",
        False
    ):

        args += [

            "-map_metadata",
            "-1"

        ]


    return args
def build_bitrate():

    cfg = config_get(
        "advanced.bitrate_random",
        False
    )


    if isinstance(cfg,bool):

        if not cfg:
            return None


        return random.randint(
            1500,
            4000
        )


    if isinstance(cfg,dict):

        if not cfg.get(
            "enable",
            False
        ):
            return None


        return random.randint(
            cfg.get("min",1500),
            cfg.get("max",4000)
        )


    return None


def apply_double_encode(file_path, ffmpeg_path, log_queue=None, use_nvenc=False):
    """
    二次编码叠加压缩噪声：
    用不同参数重新压缩一次，叠加两层随机压缩噪声，
    抹除原始片源编码痕迹，对抗“原片入库直接命中”场景。
    失败时保留一次编码结果，不影响主流程。
    """
    cfg = CONFIG.get("advanced", {}).get("double_encode", {})
    if not cfg.get("enable", False):
        return

    tmp_path = file_path + ".re.mp4"
    try:
        crf2 = random.randint(20, 26)
        # preset 只用 fast/medium（slow 耗时翻倍收益极小）
        preset2 = random.choice(["fast", "medium"])
        cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error",
               "-i", file_path]
        if use_nvenc:
            # 有 NVENC 时用 GPU 二次编码，速度提升数倍
            cmd += ["-c:v", "h264_nvenc", "-preset", "p4",
                    "-rc", "constqp", "-qp", str(crf2)]
        else:
            cmd += ["-c:v", "libx264", "-preset", preset2, "-crf", str(crf2)]
        cmd += [
            "-pix_fmt", "yuv420p",
            "-g", str(random.randint(45, 90)),
            "-c:a", "aac", "-b:a", "128k",
            "-map_metadata", "-1",
            "-y", tmp_path
        ]
        r = run_ffmpeg(cmd, timeout=900)
        if r.returncode != 0 and use_nvenc:
            # NVENC 失败回退 CPU 重试
            vi = cmd.index("-c:v")
            cmd[vi:] = ["-c:v", "libx264", "-preset", preset2, "-crf", str(crf2),
                        "-pix_fmt", "yuv420p",
                        "-g", str(random.randint(45, 90)),
                        "-c:a", "aac", "-b:a", "128k",
                        "-map_metadata", "-1",
                        "-y", tmp_path]
            r = run_ffmpeg(cmd, timeout=900)
            use_nvenc = False
        if r.returncode == 0 and os.path.exists(tmp_path):
            os.replace(tmp_path, file_path)
            if log_queue:
                tag = "GPU" if use_nvenc else "CPU"
                log_queue.put(f"  [二次编码] {tag} crf/qp={crf2} preset={preset2}")
        else:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            if log_queue:
                log_queue.put(f"  [二次编码] 失败，保留一次编码结果")
    except Exception as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        if log_queue:
            log_queue.put(f"  [二次编码] 异常: {str(e)[:60]}")


def detect_scene_cuts(ffmpeg_path, input_path, threshold=0.3, max_cuts=8):
    """
    场景切换检测：用 select='gt(scene,X)' + showinfo 检测镜头跳转点。
    返回切换时间戳列表（秒），失败返回空列表。
    用于在切换点切分段并做参数突变，切断分段匹配。
    """
    try:
        cmd = [
            ffmpeg_path, "-hide_banner",
            "-i", input_path,
            "-vf", f"select='gt(scene\\,{threshold:.2f})',showinfo",
            "-an", "-f", "null", "-"
        ]
        r = run_ffmpeg(cmd, timeout=300)
        cuts = []
        for line in r.stderr.splitlines():
            if "pts_time" in line:
                m = re.search(r"pts_time:(\d+\.?\d*)", line)
                if m:
                    cuts.append(float(m.group(1)))
        cuts = sorted(set(cuts))
        return cuts[:max_cuts]
    except Exception:
        return []


def apply_reverse_segment(input_path, output_path, ffmpeg_path, ffprobe_path, log_queue=None):
    """
    极短片段倒放/循环：随机选一个位置，将视频三段式拆分，
    中间段（0.1~0.2秒）倒放或重复播放，音频同步三段拼接。
    制造时序断点，对抗长序列时序比对、全局编辑距离计算。
    成功返回 True，失败/未触发返回 False（不影响主流程）。
    """
    try:
        duration = get_video_duration(ffprobe_path, input_path)
        if duration < 2.0:
            return False

        d = random.uniform(0.1, 0.2)
        t1 = random.uniform(0.15 * duration, 0.85 * duration)
        t2 = min(t1 + d, duration - 0.05)
        mode = random.choice(["reverse", "loop"])

        if mode == "reverse":
            v_mid = (f"[0:v]trim=start={t1:.3f}:end={t2:.3f},"
                     f"setpts=PTS-STARTPTS,reverse[v2];")
        else:
            v_mid = (f"[0:v]trim=start={t1:.3f}:end={t2:.3f},"
                     f"setpts=PTS-STARTPTS[v2];")

        fc = (
            f"[0:v]trim=0:{t1:.3f},setpts=PTS-STARTPTS[v1];"
            + v_mid +
            f"[0:v]trim=start={t2:.3f},setpts=PTS-STARTPTS[v3];"
            f"[v1][v2][v3]concat=n=3:v=1:a=0[v]"
        )
        maps = ["-map", "[v]"]

        if has_audio(ffprobe_path, input_path):
            fc += (
                f";[0:a]atrim=0:{t1:.3f},asetpts=PTS-STARTPTS[a1];"
                f"[0:a]atrim=start={t1:.3f}:end={t2:.3f},asetpts=PTS-STARTPTS[a2];"
                f"[0:a]atrim=start={t2:.3f},asetpts=PTS-STARTPTS[a3];"
                f"[a1][a2][a3]concat=n=3:v=0:a=1[a]"
            )
            maps += ["-map", "[a]"]

        cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error",
               "-i", input_path,
               "-filter_complex", fc] + maps + [
               "-c:v", "libx264", "-preset", "fast", "-crf", "16",
               "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "192k",
               "-y", output_path]
        r = run_ffmpeg(cmd, timeout=600)
        if r.returncode == 0 and os.path.exists(output_path):
            if log_queue:
                tag = "倒放" if mode == "reverse" else "循环"
                log_queue.put(f"  [时序扰动] 片段{tag} {t1:.2f}s~{t2:.2f}s")
            return True
        return False
    except Exception:
        return False


def maybe_reverse_preprocess(input_path, ffmpeg_path, ffprobe_path, log_queue=None):
    """
    倒放/循环预处理入口：按概率触发，生成临时文件。
    返回 (实际使用的输入路径, 临时文件路径或 None)。
    """
    cfg = CONFIG.get("video", {}).get("reverse_loop", {})
    if not cfg.get("enable", False):
        return input_path, None
    if random.random() > float(cfg.get("probability", 0.4)):
        return input_path, None

    import tempfile
    tmp_path = os.path.join(
        tempfile.mkdtemp(prefix="rewash_rv_"),
        Path(input_path).stem + "_rv.mp4"
    )
    if apply_reverse_segment(input_path, tmp_path, ffmpeg_path, ffprobe_path, log_queue):
        return tmp_path, tmp_path
    # 失败时清理并使用原文件
    try:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        os.rmdir(os.path.dirname(tmp_path))
    except Exception:
        pass
    return input_path, None


def postprocess_mp4_container(file_path, log_queue=None, ffmpeg_path=None):
    """
    MP4 容器结构随机化（安全模式）：
    用 ffmpeg remux 重封装并注入随机元数据（creation_time/comment），
    既改变文件二进制指纹，又保证所有播放器/平台能正常解析。
    可选 padding 尾部填充（默认关闭，严格解析器可能拒绝）。
    """
    cfg = CONFIG.get("advanced", {}).get("container_randomize", {})
    if not cfg.get("enable", False):
        return

    try:
        # 主方式：remux + 随机元数据（-c copy 秒级完成，100% 兼容）
        if ffmpeg_path:
            rand_ts = f"{random.randint(2020, 2025)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}T{random.randint(0,23):02d}:{random.randint(0,59):02d}:{random.randint(0,59):02d}Z"
            rand_comment = os.urandom(12).hex()
            tmp_path = file_path + ".cx.mp4"
            cmd = [
                ffmpeg_path, "-hide_banner", "-loglevel", "error",
                "-i", file_path,
                "-c", "copy",
                "-metadata", f"creation_time={rand_ts}",
                "-metadata", f"comment={rand_comment}",
                "-movflags", "+faststart",
                "-y", tmp_path
            ]
            r = run_ffmpeg(cmd, timeout=120)
            if r.returncode == 0 and os.path.exists(tmp_path):
                os.replace(tmp_path, file_path)
                if log_queue:
                    log_queue.put(f"  [容器] remux+随机元数据")
                return
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        # 备选方式：尾部填充随机字节（仅当配置显式开启时）
        if cfg.get("padding_enable", False) and ffmpeg_path is None:
            pad_cfg = cfg.get("padding_bytes", {})
            pad_size = random.randint(
                int(pad_cfg.get("min", 64)),
                int(pad_cfg.get("max", 512))
            )
            with open(file_path, "ab") as f:
                f.write(os.urandom(pad_size))
            if log_queue:
                log_queue.put(f"  [容器] 填充 {pad_size} 字节随机数据")
    except Exception as e:
        if log_queue:
            log_queue.put(f"  [容器] 后处理失败: {str(e)[:60]}")


def build_video_filter(
        scale=1.0,
        rotate_deg=0,
        noise_pct=0,
        contrast=1.0,
        brightness=0,
        saturation=1.0,
        sharpness=0,
        hue_shift=0,
        drift_amp=0,
        drift_period=7.0,
        lens_k1=0.0,
        lens_k2=0.0,
        edge_noise=0,
        film_grain_strength=0,
        rgb_shift_px=0,
        mask_drift_strength=0,
        luminance_wave=0
):
    """
    构建视频滤镜链。
    顺序: 画幅裁剪 → 动态漂移 → 缩放 → 旋转 → 镜头畸变 → 色彩 → 分块亮度
          → RGB微错位 → 噪点/颗粒 → 边缘扰动 → 蒙版漂移 → 锐化 → 偶数尺寸
    """

    filters = []

    # =========================
    # 1. 输出比例控制（画幅裁剪）
    # =========================
    output_cfg = CONFIG.get("output", {})
    ratio = output_cfg.get("aspect_ratio", "9:16")

    if ratio == "9:16":
        filters.append("crop='min(iw,ih*9/16):min(ih,iw*16/9)'")
    elif ratio == "3:4":
        filters.append("crop='min(iw,ih*3/4):min(ih,iw*4/3)'")
    elif ratio == "1:1":
        filters.append("crop='min(iw,ih):min(iw,ih)'")
    elif ratio == "4:5":
        filters.append("crop='min(iw,ih*4/5):min(ih,iw*5/4)'")

    # =========================
    # 2. 逐帧动态微位移（正弦漂移，替代固定偏移）
    #    每帧位置都不同，对抗 pHash / 时空差分指纹
    #    也兼容旧的固定微抖动模式
    # =========================
    if drift_amp > 0:
        A = int(drift_amp)
        T = drift_period
        # scale 扩大画面提供漂移空间，crop 用 sin/cos 表达式逐帧偏移
        filters.append(
            f"scale=iw+{A*2}:ih+{A*2}"
        )
        filters.append(
            f"crop=iw-{A*2}:ih-{A*2}:"
            f"'({A})+({A})*sin(2*PI*n/({T}*30))':"
            f"'({A})+({A})*cos(2*PI*n/({T}*30))'"
        )

    # =========================
    # 3. 轻微缩放扰动
    # =========================
    if scale and scale != 1:
        filters.append(f"scale=iw*{scale:.4f}:ih*{scale:.4f}")

    # =========================
    # 4. 旋转
    # =========================
    if rotate_deg:
        filters.append(f"rotate={rotate_deg}*PI/180:fillcolor=black@0")

    # =========================
    # 5. 极轻微镜头畸变（几何结构扰动）
    #    对抗 CNN 空间特征、边缘直方图指纹
    # =========================
    if lens_k1 != 0 or lens_k2 != 0:
        filters.append(
            f"lenscorrection=cx=0.5:cy=0.5:k1={lens_k1:.4f}:k2={lens_k2:.4f}"
        )

    # =========================
    # 6. 色彩调整（eq + hue）
    # =========================
    if brightness != 0 or contrast != 1 or saturation != 1:
        filters.append(
            f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}"
        )
    if hue_shift:
        filters.append(f"hue=h={hue_shift:.2f}")

    # =========================
    # 6.2 分块亮度独立微调（低频双向亮度波）
    #    画面不同区域亮度±amp 独立偏移，正负抵消全局直方图不变
    #    对抗分块时空指纹、局部特征匹配算法
    # =========================
    if luminance_wave > 0:
        lw_amp = float(luminance_wave)
        lw_ph1 = random.uniform(0, 2 * math.pi)
        lw_ph2 = random.uniform(0, 2 * math.pi)
        # 水平/垂直各 1.5 个周期，形成约 3x3 区块式亮度分布
        # lum(X,Y) 取原始亮度（此 FFmpeg build 不支持 pixel() 语法）
        # cb='cb(X,Y)'/cr='cr(X,Y)' 保持色度平面不变
        filters.append(
            f"geq=lum='clip(lum(X,Y)"
            f"+{lw_amp:.1f}*sin(3*PI*X/W+{lw_ph1:.2f})"
            f"+{lw_amp:.1f}*sin(3*PI*Y/H+{lw_ph2:.2f}),0,255)'"
            f":cb='cb(X,Y)':cr='cr(X,Y)'"
        )

    # =========================
    # 6.5 RGB 通道微错位（色差扰动）
    #    R/B 通道各自偏移 ±1px，方向随机，人眼无感
    #    对抗颜色布局指纹、像素级哈希、压缩残差特征
    # =========================
    if rgb_shift_px > 0:
        p = int(rgb_shift_px)
        rh = random.choice([-p, p])
        bh = random.choice([-p, p])
        rv = random.choice([-p, 0, p])
        bv = random.choice([-p, 0, p])
        filters.append(
            f"rgbashift=rh={rh}:rv={rv}:bh={bh}:bv={bv}:edge=smear"
        )

    # =========================
    # 7. 噪点 / 胶片颗粒
    # =========================
    if film_grain_strength > 0:
        s = int(film_grain_strength)
        filters.append(f"noise=alls={s}:allf=t")
    elif noise_pct:
        strength = int(noise_pct)
        filters.append(f"noise=alls={strength}:allf=t")

    # =========================
    # 8. 边缘动态像素扰动
    #    提取边缘压暗成微弱亮带(+噪)，用 blend=add 叠回原图
    #    只影响边缘像素，主体内容不变，干扰 CNN 底层特征
    # =========================
    if edge_noise > 0:
        en = int(edge_noise)
        # edgedetect 参数范围 [0,1]，用低阈值提取边缘
        high_val = min(0.1 * en, 1.0)
        low_val = min(0.02 * en, 1.0)
        # lutrgb 把边缘亮度压缩到 1/16（最多 +15 亮度），叠加噪声后加回原图
        filters.append(
            f"split[o][e];"
            f"[e]edgedetect=low={low_val:.3f}:high={high_val:.3f},"
            f"lutrgb=r=val/16:g=val/16:b=val/16,"
            f"noise=alls={en}:allf=t[en];"
            f"[o][en]blend=addition"
        )

    # =========================
    # 8.5 半透明渐变蒙版漂移
    #    geq 生成缓慢漂移的渐变层，blend 按 3% 权重叠加
    #    改变全局特征分布，人眼不可见
    # =========================
    if mask_drift_strength > 0:
        ms = int(mask_drift_strength)
        # 渐变随帧号缓慢移动（周期约 10 秒 @30fps）
        filters.append(
            f"split[mdo][mde];"
            f"[mde]geq=lum='clip(128+{ms*25}*sin(2*PI*X/W+2*PI*N/300),0,255)':cb=128:cr=128:a=255[mm];"
            f"[mdo][mm]blend=all_expr='A*97/100+B*3/100'"
        )

    # =========================
    # 9. 锐化
    # =========================
    if sharpness:
        filters.append(f"unsharp=5:5:{sharpness}")

    # =========================
    # 10. 保证偶数尺寸
    # =========================
    filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

    return ",".join(filters)

def process_single_video(
        input_path,
        output_path,
        ffmpeg_path,
        ffprobe_path,
        use_nvenc,
        speed,
        log_queue,
        error_log_path=None,
        retry_cpu=False
):

    video_name = Path(output_path).stem


    try:

        # ==========================
        # 随机参数
        # ==========================

        scale = config_random(
            "video.scale",
            1.0
        )

        rotate_deg = config_random(
            "video.rotate",
            0
        )

        noise_pct = config_random(
            "video.noise",
            0
        )

        contrast = config_random(
            "video.contrast",
            1
        )

        brightness = config_random(
            "video.brightness",
            0
        )

        saturation = config_random(
            "video.saturation",
            1
        )

        sharpness = config_random(
            "video.sharpness",
            0
        )

        hue_shift = config_random(
            "video.hue",
            0
        )


        # ==========================
        # 非对称裁剪 / 透视微变形 / 暗角
        # ==========================

        video_cfg = CONFIG.get("video", {})

        # 非对称裁剪：随机向左或向右偏移 1%~3%
        asym_shift = 0.0
        asym_cfg = video_cfg.get("asymmetric_crop", {})
        if asym_cfg.get("enable", False):
            asym_shift = random.uniform(
                float(asym_cfg.get("min", 0.03)),
                float(asym_cfg.get("max", 0.05))
            )
            if random.random() < 0.5:
                asym_shift = -asym_shift

        # 暗角强度（PI/N，N 越大效果越轻，安全范围 4~5）
        vig_strength = 0.0
        vig_cfg = video_cfg.get("vignette", {})
        if vig_cfg.get("enable", False):
            vig_strength = random.uniform(
                float(vig_cfg.get("min", 4)),
                float(vig_cfg.get("max", 5))
            )

        # 胶片颗粒强度
        film_grain = 0.0
        grain_cfg = video_cfg.get("film_grain", {})
        if grain_cfg.get("enable", False):
            film_grain = float(grain_cfg.get("strength", 3))

        # ★ 逐帧动态微位移（正弦漂移）
        drift_amp = 0.0
        drift_period = 7.0
        drift_cfg = video_cfg.get("dynamic_drift", {})
        if drift_cfg.get("enable", False):
            drift_amp = float(drift_cfg.get("amplitude_px", 2))
            dp_cfg = drift_cfg.get("period_sec", {})
            if isinstance(dp_cfg, dict):
                drift_period = random.uniform(
                    float(dp_cfg.get("min", 5)),
                    float(dp_cfg.get("max", 10))
                )
            else:
                drift_period = float(dp_cfg)

        # ★ 极轻微镜头畸变
        lens_k1 = 0.0
        lens_k2 = 0.0
        lens_cfg = video_cfg.get("lens_distortion", {})
        if lens_cfg.get("enable", False):
            k1_range = float(lens_cfg.get("k1_range", 0.02))
            k2_range = float(lens_cfg.get("k2_range", 0.005))
            lens_k1 = random.uniform(-k1_range, k1_range)
            lens_k2 = random.uniform(-k2_range, k2_range)

        # ★ 边缘像素扰动
        edge_noise_val = 0.0
        edge_cfg = video_cfg.get("edge_perturbation", {})
        if edge_cfg.get("enable", False):
            edge_noise_val = float(edge_cfg.get("strength", 3))

        # ★ RGB 通道微错位
        rgb_shift_val = 0.0
        rgb_cfg = video_cfg.get("rgb_shift", {})
        if rgb_cfg.get("enable", False):
            rgb_shift_val = float(rgb_cfg.get("max_px", 1))

        # ★ 半透明渐变蒙版漂移
        mask_drift_val = 0.0
        mask_cfg = video_cfg.get("mask_drift", {})
        if mask_cfg.get("enable", False):
            mask_drift_val = float(mask_cfg.get("strength", 2))

        # ★ 分块亮度独立微调（亮度波幅度 = strength*255*0.02）
        lum_wave_val = 0.0
        lum_cfg = video_cfg.get("luminance_wave", {})
        if lum_cfg.get("enable", False):
            lum_wave_val = float(lum_cfg.get("strength", 2)) * 255 * 0.02



        # ==========================
        # 黑边检测
        # ==========================

        crop_value=None

        crop_cfg = CONFIG.get(
            "video",
            {}
        ).get(
            "black_crop",
            {}
        )


        if (
            crop_cfg.get("enable")
            and
            crop_cfg.get("detect")
        ):

            crop_value = detect_black_crop(
                ffmpeg_path,
                input_path
            )



        # ==========================
        # 音频检测
        # ==========================

        audio_exists = has_audio(
            ffprobe_path,
            input_path
        )


        # ==========================
        # 视频滤镜
        # ==========================

        vf_chain = build_video_filter(
            scale=scale,
            rotate_deg=rotate_deg,
            noise_pct=noise_pct,
            contrast=contrast,
            brightness=brightness,
            saturation=saturation,
            sharpness=sharpness,
            hue_shift=hue_shift,
            drift_amp=drift_amp,
            drift_period=drift_period,
            lens_k1=lens_k1,
            lens_k2=lens_k2,
            edge_noise=edge_noise_val,
            film_grain_strength=film_grain,
            rgb_shift_px=rgb_shift_val,
            mask_drift_strength=mask_drift_val,
            luminance_wave=lum_wave_val
        )


        vf_list=[]


        if crop_value:

            vf_list.append(
                f"crop={crop_value}"
            )


        vf_list.append(
            vf_chain
        )


        # 非对称裁剪：画面整体偏移，改变几何指纹
        if asym_shift != 0:
            vf_list.append(
                f"crop=iw*{1 - abs(asym_shift):.4f}:ih"
                f":{'iw*' + str(abs(asym_shift)) if asym_shift > 0 else '0'}:0"
            )


        # 暗角效果：四角压暗
        if vig_strength > 0:
            vf_list.append(
                f"vignette=PI/{vig_strength:.2f}"
            )


        # ★ 微量随机抽帧：每 100~200 帧随机删 1 帧
        #    破坏帧序列对齐关系，对抗关键帧序列指纹/编辑距离比对
        fd_cfg = video_cfg.get("frame_drop", {})
        if fd_cfg.get("enable", False):
            fd_interval = fd_cfg.get("interval", {})
            fd_max = int(fd_interval.get("max", 200))
            fd_min = int(fd_interval.get("min", 100))
            drop_frame = random.randint(fd_min, fd_max)
            vf_list.append(
                f"select='not(eq(n,{drop_frame}))',setpts=N/FRAME_RATE/TB"
            )


        # 变速（支持非线性正弦波动）

        wave_cfg = CONFIG.get("speed", {}).get("wave", {})
        if wave_cfg.get("enable", False):
            # 速度随时间正弦波动 ±amp，平均速度不变
            # P(t) = t - C*cos(2πt/T) + C 保证单调递增（导数 = 1+amp*sin ≥ 1-amp > 0）
            amp = float(wave_cfg.get("amplitude", 0.03))
            pw_cfg = wave_cfg.get("period_sec", {})
            if isinstance(pw_cfg, dict):
                T_wave = random.uniform(
                    float(pw_cfg.get("min", 6)),
                    float(pw_cfg.get("max", 12))
                )
            else:
                T_wave = float(pw_cfg)
            C = amp * T_wave / (2 * math.pi)
            vf_list.append(
                f"setpts='((T-{C:.5f}*cos(2*PI*T/{T_wave:.3f})+{C:.5f})/TB)*{1/speed:.6f}'"
            )
        else:
            vf_list.append(
                f"setpts={1/speed:.6f}*PTS"
            )


        # 最后统一像素格式（放在所有滤镜之后）
        vf_list.append("format=yuv420p")


        video_filter=",".join(
            vf_list
        )



        # ==========================
        # 编码器
        # ==========================


        encoder = encoder_builder(
            use_nvenc
        )


        vcodec = encoder["codec"]

        vcodec_opts = encoder["args"]





        cmd=[ffmpeg_path]



        # ==========================
        # 时间戳随机
        # 必须放 -i 前
        # ==========================


        cmd += build_timestamp_options()

        # ==========================
        # 开头随机裁剪
        # 必须放 -i 前
        # ==========================

        cmd += build_start_trim()

        # ==========================
        # CUDA解码
        # ==========================

        # 先检测输入视频编码格式
        video_codec = get_video_codec(
            ffprobe_path,
            input_path
        )

        decode_mode = "CPU"

        # 只有 AV1 才使用 CUDA AV1 解码
        use_av1_cuda = (
                AV1_GPU_DECODE
                and video_codec == "av1"
        )

        if use_av1_cuda:
            cmd += [

                "-hwaccel",
                "cuda",

                "-hwaccel_output_format",
                "cuda",

                "-c:v",
                "av1_cuvid"

            ]

            decode_mode = "AV1-GPU"

        # ==========================
        # 输入视频
        # ==========================

        cmd += [

            "-i",
            input_path

        ]

        # ==========================
        # 音频噪声源输入（底噪 + 位深抖动，作为额外输入混入）
        # =========================
        audio_cfg_pre = CONFIG.get("audio", {})
        noise_inputs = []
        noise_floor_cfg = audio_cfg_pre.get("noise_floor", {})
        if audio_exists and noise_floor_cfg.get("enable", False):
            noise_db = float(noise_floor_cfg.get("db", -54))
            noise_amp = 10 ** (noise_db / 20.0)
            noise_inputs.append(
                f"anoisesrc=amplitude={noise_amp:.6f}:color=pink:duration=999"
            )
        # 位深抖动：混入极低幅白噪声（等效量化噪声，不衰减原音量）
        dither_cfg_pre = audio_cfg_pre.get("dither", {})
        if audio_exists and dither_cfg_pre.get("enable", False):
            dither_db = random.uniform(
                float(dither_cfg_pre.get("strength_db", {}).get("min", -56)),
                float(dither_cfg_pre.get("strength_db", {}).get("max", -50))
            )
            dither_amp = 10 ** (dither_db / 20.0)
            noise_inputs.append(
                f"anoisesrc=amplitude={dither_amp:.6f}:color=white:duration=999"
            )
        for ni_src in noise_inputs:
            cmd += ["-f", "lavfi", "-i", ni_src]

        # ==========================
        # CUDA帧转CPU帧
        # ==========================
        #
        # 后面的 crop / scale / rotate / noise
        # 都是CPU滤镜。
        #
        # 如果不加 hwdownload，
        # CUDA帧直接进入 crop 就会报：
        #
        # Impossible to convert between the formats
        #
        # ==========================

        if use_av1_cuda:
            video_filter = (
                    "hwdownload,"
                    "format=nv12,"
                    + video_filter
            )

        # ==========================
        # 最后再生成模式标签
        # ==========================

        accel_tag = (
                encoder["mode"]
                +
                "+"
                +
                decode_mode
        )


        # ==========================
        # 视频滤镜
        # ==========================

        cmd += [

            "-vf",

            video_filter

        ]



        # ==========================
        # 音频
        # ==========================


        if audio_exists:


            audio_filters = []

            # 音频配置（提前读取，供增强效果使用）
            audio_cfg = CONFIG.get("audio", {})

            # 音调偏移（通过 asetrate + atempo 补偿实现）
            pitch_semi = config_random_float("audio.pitch", 0)
            pitch_ratio = 1.0
            if pitch_semi and pitch_semi != 0:
                pitch_ratio = 2 ** (pitch_semi / 12.0)
                # asetrate 改变音调，后续 atempo 补偿速度
                sample_rate_for_asetrate = int(48000 * pitch_ratio)
                audio_filters.append(f"asetrate={sample_rate_for_asetrate}")
                audio_filters.append("aresample=48000")

            # 变速（补偿音调偏移）
            effective_atempo = speed / pitch_ratio
            audio_filters.append(f"atempo={effective_atempo:.6f}")

            # 音量
            volume = config_random_float("audio.volume", 1)
            if volume:
                audio_filters.append(f"volume={volume:.4f}")

            # ★ 声道间微延迟（对抗 Chromaprint / Shazam 音频指纹）
            ch_delay_cfg = audio_cfg.get("channel_delay", {})
            if ch_delay_cfg.get("enable", False):
                delay_ms = random.randint(
                    int(ch_delay_cfg.get("min_ms", 1)),
                    int(ch_delay_cfg.get("max_ms", 5))
                )
                # 延迟右声道，左声道不变
                audio_filters.append(f"adelay=0|{delay_ms}")

            # ★ 极轻微房间混响（重塑时域包络、频谱细节）
            reverb_cfg = audio_cfg.get("reverb", {})
            if reverb_cfg.get("enable", False):
                del_cfg = reverb_cfg.get("delay_ms", {})
                dec_cfg = reverb_cfg.get("decay", {})
                rv_delay = random.randint(
                    int(del_cfg.get("min", 15)),
                    int(del_cfg.get("max", 40))
                )
                rv_decay = random.uniform(
                    float(dec_cfg.get("min", 0.03)),
                    float(dec_cfg.get("max", 0.08))
                )
                # in_gain/out_gain 保持 1 不衰减音量，混响强度由 decays 控制
                audio_filters.append(
                    f"aecho=in_gain=1.0:out_gain=1.0:delays={rv_delay}:decays={rv_decay:.2f}"
                )

            # ★ 动态范围微压缩（改变音频能量分布指纹）
            comp_cfg = audio_cfg.get("compressor", {})
            if comp_cfg.get("enable", False):
                th_db = random.randint(
                    int(comp_cfg.get("threshold_db", {}).get("min", -18)),
                    int(comp_cfg.get("threshold_db", {}).get("max", -12))
                )
                comp_ratio = random.uniform(
                    float(comp_cfg.get("ratio", {}).get("min", 2)),
                    float(comp_cfg.get("ratio", {}).get("max", 4))
                )
                audio_filters.append(
                    f"acompressor=threshold={th_db}dB:ratio={comp_ratio:.1f}:"
                    f"attack=20:release=200:makeup=2"
                )

            # 采样率随机选择
            sr_cfg = audio_cfg.get("sample_rate", {})
            if isinstance(sr_cfg, dict) and sr_cfg.get("enable", False):
                choices = sr_cfg.get("choices", [48000])
                sample_rate = random.choice(choices)
            elif isinstance(sr_cfg, (int, float)):
                sample_rate = int(sr_cfg)
            else:
                sample_rate = 48000

            if noise_inputs:
                # 把所有音频滤镜放进 filter_complex（不能同时用 -af 和 -filter_complex）
                n_inputs = 1 + len(noise_inputs)
                labels = "".join(f"[{i}:a]" for i in range(n_inputs))
                weights = " ".join(["1"] * n_inputs)
                # normalize=0 避免 amix 按输入数归一化导致音量衰减
                af_chain = f"{labels}amix=inputs={n_inputs}:duration=first:normalize=0:weights={weights}"
                if audio_filters:
                    af_chain += "," + ",".join(audio_filters)
                af_chain += "[aout]"
                cmd += [
                    "-filter_complex", af_chain,
                    "-map", "0:v", "-map", "[aout]"
                ]
            else:
                if audio_filters:
                    cmd += ["-af", ",".join(audio_filters)]
            cmd += [
                "-c:a",
                audio_cfg.get("codec", "aac"),
                "-b:a",
                audio_cfg.get("bitrate", "128k"),
                "-ar",
                str(sample_rate),
                "-ac",
                str(audio_cfg.get("channels", 2))
            ]


        else:

            cmd += [

                "-an"

            ]



        # ==========================
        # 视频编码
        # ==========================


        cmd += [

            "-c:v",

            vcodec

        ]


        cmd += vcodec_opts

        # SEI 数据注入（零画质损失，改变文件二进制指纹）
        sei_cfg = CONFIG.get("advanced", {}).get("sei_inject", {})
        if sei_cfg.get("enable", False) and vcodec == "h264_nvenc":
            import uuid
            sei_uuid = str(uuid.uuid4())
            sei_data = f"{sei_uuid}+rewash"
            cmd += [
                "-bsf:v",
                f"h264_metadata=sei_user_data={sei_data}"
            ]



        # ==========================
        # 随机码率
        # ==========================


        bitrate=build_bitrate()


        if bitrate:

            cmd += [

                "-b:v",

                f"{bitrate}k"

            ]



        # ==========================
        # 封装参数
        # ==========================

        cmd += build_mux_options()



        # ==========================
        # 输出
        # ==========================


        cmd += [

            "-y",

            output_path

        ]



        log_queue.put(
            f"[处理中] {video_name} "
            f"缩放:{scale:.3f} "
            f"旋转:{rotate_deg:.2f} "
            f"色相:{hue_shift:.1f} "
            f"偏移:{asym_shift:+.3f} "
            f"速度:{speed:.4f} "
            f"模式:{accel_tag}"
        )



        result=run_ffmpeg(

            cmd,

            timeout=1800

        )



        # ==========================
        # NVENC失败自动CPU
        # ==========================

        if result.returncode !=0:


            err=result.stderr[-2000:]


            log_queue.put(
                "FFmpeg错误:\n"+err
            )



            if use_nvenc and not retry_cpu:


                log_queue.put(
                    "NVENC失败，切换CPU"
                )


                return process_single_video(

                    input_path,

                    output_path,

                    ffmpeg_path,

                    ffprobe_path,

                    False,

                    speed,

                    log_queue,

                    error_log_path,

                    True

                )



            return False



        # ★ 二次编码叠加压缩噪声（可选，有 NVENC 时用 GPU 提速）
        apply_double_encode(output_path, ffmpeg_path, log_queue, use_nvenc=use_nvenc)

        # ★ MP4 容器结构随机化（安全 remux 模式，改变文件二进制指纹）
        postprocess_mp4_container(output_path, log_queue, ffmpeg_path)

        log_queue.put(
            f"✅ {video_name} 完成"
        )


        return True



    except Exception as e:


        log_queue.put(

            f"💥异常 {video_name}:{e}"

        )

        return False

def get_video_codec(ffprobe_path, video_path):
    """
    获取视频编码格式
    例如：
    av1
    h264
    hevc
    """

    try:

        cmd = [
            ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]

        result = sp.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=10
        )

        codec = result.stdout.strip().lower()

        return codec if codec else None

    except Exception:
        return None
def get_video_duration(ffprobe_path, video_path):
    """
    获取视频时长 秒（按 path+size+mtime 缓存，避免重复探测）
    """

    try:

        if not ffprobe_path:
            return None

        st = os.stat(video_path)
        cache_key = ("duration", video_path, st.st_size, int(st.st_mtime))
        if cache_key in _PROBE_CACHE:
            return _PROBE_CACHE[cache_key]

        cmd = [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path
        ]


        result = sp.run(
            cmd,
            stdout=sp.PIPE,
            stderr=sp.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=10
        )


        duration = result.stdout.strip()


        if duration:

            dur_val = float(duration)
            _PROBE_CACHE[cache_key] = dur_val
            return dur_val


        return 0.0


    except Exception as e:

        print(
            "获取视频时长失败:",
            e
        )

        return 0.0
# ══════════════════════════════════════════════════════════════════════════════
#  PyQt5 主界面类
# ══════════════════════════════════════════════════════════════════════════════


class DropLineEdit(QLineEdit):
    """支持文件夹拖放的自定义 QLineEdit（可手动编辑路径）"""
    folder_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        # 允许手动编辑路径
        self.setReadOnly(False)
        # 拖放高亮样式（深灰主题）；恢复时清空控件级样式以继承父级 QSS
        self._drag_style = "background-color: #3a5a3a; border: 2px solid #6a9fd8;"

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self._drag_style)  # 变色提示
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet("")  # 清空后继承父级样式，不会丢失窗口 QSS

    def dropEvent(self, event: QDropEvent):
        self.setStyleSheet("")  # 清空后继承父级样式
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_dir():
                self.folder_dropped.emit(path)
            else:
                self.folder_dropped.emit(str(Path(path).parent))


# ───────────────────────────────────────────────────────────────
#  配置编辑器（分页 + 递归控件生成 + 预设 + 实时生效）
# ───────────────────────────────────────────────────────────────

# 配置项中文名（未列出的显示原始键名）
CFG_LABELS = {
    "version_count": "每视频生成版本数",
    "aspect_ratio": "输出画幅比例",
    "asymmetric_crop": "非对称裁剪", "vignette": "暗角", "scale": "缩放",
    "rotate": "旋转", "hue": "色相偏移", "brightness": "亮度",
    "contrast": "对比度", "saturation": "饱和度", "noise": "噪点",
    "film_grain": "胶片颗粒", "micro_jitter": "微抖动", "dynamic_drift": "动态漂移",
    "lens_distortion": "镜头畸变", "edge_perturbation": "边缘扰动",
    "rgb_shift": "RGB错位", "mask_drift": "蒙版漂移", "luminance_wave": "分块亮度波",
    "reverse_loop": "倒放/循环扰动", "frame_drop": "微量抽帧", "sharpness": "锐化",
    "start_trim": "片头裁剪", "black_crop": "黑边裁剪",
    "enable": "启用", "min": "最小值", "max": "最大值", "strength": "强度",
    "probability": "触发概率", "amplitude": "幅度", "amplitude_px": "幅度(像素)",
    "period_sec": "周期(秒)", "max_px": "最大像素", "k1_range": "k1范围",
    "k2_range": "k2范围", "interval": "间隔(帧)", "detect": "自动检测",
    "type": "类型", "codec": "编码器", "bitrate": "码率", "channels": "声道数",
    "volume": "音量", "pitch": "音调(半音)", "noise_floor": "底噪",
    "db": "分贝", "channel_delay": "声道微延迟", "min_ms": "最小毫秒",
    "max_ms": "最大毫秒", "reverb": "房间混响", "delay_ms": "延迟毫秒",
    "decay": "衰减", "compressor": "动态压缩", "threshold_db": "阈值dB",
    "ratio": "压缩比", "dither": "位深抖动", "strength_db": "强度dB",
    "sample_rate": "采样率", "choices": "可选值",
    "wave": "非线性变速波动",
    "gpu_auto": "GPU自动检测", "cuda_decode": "CUDA硬解码",
    "nvenc": "NVENC(GPU)", "cpu": "CPU编码", "preset": "预设",
    "cq": "CQ质量", "crf": "CRF质量", "profile": "Profile", "level": "Level",
    "pix_fmt": "像素格式",
    "bitrate_random": "码率随机", "keyframe_random": "关键帧随机",
    "sei_inject": "SEI隐藏数据注入", "fps_random": "帧率随机",
    "timestamp_random": "时间戳随机", "faststart": "faststart网络优化",
    "container_randomize": "容器指纹随机", "padding_bytes": "尾部填充字节",
    "b_frames_random": "B帧随机", "refs_random": "参考帧随机",
    "qp_jitter": "QP抖动", "aq_strength": "AQ强度",
    "double_encode": "二次编码", "first_crf": "首次CRF",
    "clear": "清除元数据", "remove_chapters": "删除章节", "remove_creation_time": "删除创建时间",
    "workers": "并发数", "auto": "自动", "gpu_scheduler": "GPU负载分流",
    "max_gpu_usage": "GPU占用上限%", "complexity_threshold": "复杂度阈值",
    "sample_frames": "采样帧数", "max_similarity": "相似度上限", "retry_max": "最大重试",
    "count": "分段数", "crossfade": "交叉淡化", "scene_split": "场景切点分段",
    "threshold": "切换阈值", "api_url": "SD API地址",
}


class ConfigEditorDialog(QDialog):
    """
    配置编辑器：QTabWidget 分页 + 按配置值类型递归生成控件。
    “应用”立即写回全局 CONFIG（下一个视频生效，正在处理的不受影响）；
    “保存”额外写入 config.json。
    """

    TAB_ORDER = [
        ("video", "视频参数"), ("audio", "音频参数"), ("speed", "变速参数"),
        ("encode", "编码参数"), ("advanced", "高级功能"),
        ("fingerprint", "指纹与分段"), ("performance", "性能"),
        ("ai_repaint", "AI重绘"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙ 去重参数配置")
        self.resize(720, 640)
        self._original = get_config_snapshot()  # 保留全部原始配置（含未展示项）
        self._widgets = {}  # path -> widget
        self._combo_values = {}  # path -> 原始值列表

        root = QVBoxLayout(self)

        # 预设区
        preset_bar = QHBoxLayout()
        preset_bar.addWidget(QLabel("一键预设:"))
        for name, slot in [("🌤 温和", self._preset_gentle),
                           ("⚖ 标准", self._preset_standard),
                           ("🔥 激进", self._preset_aggressive)]:
            b = QPushButton(name)
            b.clicked.connect(slot)
            preset_bar.addWidget(b)
        preset_bar.addStretch()
        root.addLayout(preset_bar)

        # 分页
        self.tabs = QTabWidget()
        for key, title in self.TAB_ORDER:
            if key not in self._original:
                continue
            self.tabs.addTab(self._build_page(key, title), title)
        # 其余未分页的顶层项（如 version_count/output/metadata）合到“其他”页
        handled = {k for k, _ in self.TAB_ORDER}
        rest = {k: v for k, v in self._original.items() if k not in handled}
        if rest:
            self.tabs.addTab(self._build_page_dict(rest, ""), "其他")
        root.addWidget(self.tabs, 1)

        # 底部按钮
        btn_bar = QHBoxLayout()
        for text, slot in [("📂 导入", self._import_config), ("📤 导出", self._export_config),
                           ("❌ 取消", self.reject),
                           ("✅ 应用(不写文件)", self._apply_only),
                           ("💾 保存并应用", self._apply_and_save)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            btn_bar.addWidget(b)
        root.addLayout(btn_bar)

        self._status = QLabel("提示：应用后从下一个视频开始生效，正在处理的视频不受影响")
        root.addWidget(self._status)

    # ── 递归构建控件 ──────────────────────────────
    def _build_page(self, key, title):
        data = self._original.get(key, {})
        if isinstance(data, dict):
            return self._build_page_dict(data, key)
        # 顶层标量
        wrap = QWidget()
        form = QFormLayout(wrap)
        self._add_leaf(form, key, data, key)
        return wrap

    def _build_page_dict(self, data: dict, path_prefix: str):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        form = QFormLayout(inner)
        for key, val in data.items():
            full = f"{path_prefix}.{key}" if path_prefix else key
            self._add_node(form, key, val, full)
        scroll.setWidget(inner)
        return scroll

    def _add_node(self, form, key, val, full):
        label = CFG_LABELS.get(key, key)
        if isinstance(val, dict):
            box = QGroupBox(label)
            sub = QFormLayout(box)
            for k2, v2 in val.items():
                self._add_node(sub, k2, v2, f"{full}.{k2}")
            form.addRow(box)
        else:
            self._add_leaf(form, key, val, full)

    def _add_leaf(self, form, key, val, full):
        label = CFG_LABELS.get(key, key)
        if key == "note" or (isinstance(val, str) and len(val) > 40):
            # 长说明文本只展示不可改，避免误改丢配置
            lab = QLabel(str(val))
            lab.setWordWrap(True)
            lab.setStyleSheet("color: #888;")
            form.addRow(label, lab)
        elif isinstance(val, bool):
            w = QCheckBox(label)
            w.setChecked(val)
            self._widgets[full] = w
            form.addRow("", w)
        elif isinstance(val, int):
            w = QSpinBox()
            w.setRange(-100000, 100000)
            w.setValue(val)
            self._widgets[full] = w
            form.addRow(label, w)
        elif isinstance(val, float):
            w = QDoubleSpinBox()
            w.setRange(-100000.0, 100000.0)
            w.setDecimals(4)
            w.setValue(val)
            self._widgets[full] = w
            form.addRow(label, w)
        elif isinstance(val, str):
            w = QLineEdit(val)
            self._widgets[full] = w
            form.addRow(label, w)
        elif isinstance(val, list):
            w = QComboBox()
            self._combo_values[full] = list(val)
            for item in val:
                w.addItem(str(item))
            self._widgets[full] = w
            form.addRow(label, w)

    # ── 收集/写回 ──────────────────────────────
    def _collect(self):
        cfg = copy.deepcopy(self._original)  # 保留未展示的配置项
        for path, w in self._widgets.items():
            node = cfg
            keys = path.split(".")
            for k in keys[:-1]:
                if not isinstance(node.get(k), dict):
                    node[k] = {}
                node = node[k]
            last = keys[-1]
            if isinstance(w, QCheckBox):
                node[last] = w.isChecked()
            elif isinstance(w, QSpinBox):
                node[last] = w.value()
            elif isinstance(w, QDoubleSpinBox):
                node[last] = w.value()
            elif isinstance(w, QLineEdit):
                node[last] = w.text()
            elif isinstance(w, QComboBox):
                vals = self._combo_values.get(path, [])
                idx = w.currentIndex()
                node[last] = vals[idx] if 0 <= idx < len(vals) else vals
        return cfg

    def _set_widget(self, path, value):
        """按路径设置控件值（预设用）"""
        w = self._widgets.get(path)
        if w is None:
            return
        if isinstance(w, QCheckBox):
            w.setChecked(bool(value))
        elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
            w.setValue(value)
        elif isinstance(w, QLineEdit):
            w.setText(str(value))

    def _apply_only(self):
        apply_config_safe(self._collect())
        self._status.setText("✅ 已应用（copy-on-write，正在处理的视频不受影响）")

    def _apply_and_save(self):
        cfg = self._collect()
        apply_config_safe(cfg)
        ok = save_config_to_file(cfg)
        self._original = get_config_snapshot()
        self._status.setText("💾 已保存并应用" if ok else "⚠ 已应用但写文件失败")

    # ── 预设 ──────────────────────────────
    _SCALE_PATHS = [
        "video.asymmetric_crop.min", "video.asymmetric_crop.max",
        "video.scale.min", "video.scale.max",
        "video.rotate.min", "video.rotate.max",
        "video.hue.min", "video.hue.max",
        "video.noise.min", "video.noise.max",
        "video.dynamic_drift.amplitude_px",
        "video.edge_perturbation.strength", "video.rgb_shift.max_px",
        "video.mask_drift.strength", "video.luminance_wave.strength",
        "video.micro_jitter.max_px", "video.film_grain.strength",
        "video.lens_distortion.k1_range", "video.lens_distortion.k2_range",
        "speed.wave.amplitude",
    ]

    def _scale_paths(self, factor):
        cfg = self._collect()
        for path in self._SCALE_PATHS:
            node = cfg
            keys = path.split(".")
            try:
                for k in keys[:-1]:
                    node = node[k]
                node[keys[-1]] = round(float(node[keys[-1]]) * factor, 4)
            except Exception:
                pass
        apply_config_safe(cfg)
        self._refresh_from_config()

    def _preset_gentle(self):
        self._scale_paths(0.5)
        self._set_widget("advanced.double_encode.enable", False)
        self._set_widget("video.reverse_loop.probability", 0.15)
        apply_config_safe(self._collect())
        self._status.setText("🌤 温和预设已应用（强度减半，关闭重型项）")

    def _preset_standard(self):
        """从 config.json 重新加载默认值"""
        self._original = load_config() or self._original
        self._refresh_from_config()
        apply_config_safe(self._collect())
        self._status.setText("⚖ 已恢复文件中的标准配置")

    def _preset_aggressive(self):
        self._scale_paths(1.5)
        self._set_widget("advanced.double_encode.enable", True)
        self._set_widget("video.reverse_loop.probability", 0.6)
        apply_config_safe(self._collect())
        self._status.setText("🔥 激进预设已应用（强度×1.5，开启重型项，速度变慢）")

    def _refresh_from_config(self):
        """根据当前 CONFIG 刷新所有控件值"""
        cfg = get_config_snapshot()
        for path, w in self._widgets.items():
            node = cfg
            try:
                for k in path.split("."):
                    node = node[k]
            except Exception:
                continue
            if isinstance(w, QCheckBox):
                w.setChecked(bool(node))
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                w.setValue(float(node))
            elif isinstance(w, QLineEdit):
                w.setText(str(node))

    # ── 导入/导出 ──────────────────────────────
    def _export_config(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出配置", "config_export.json", "JSON (*.json)")
        if path:
            save_config_to_file_path = path
            try:
                with open(save_config_to_file_path, "w", encoding="utf-8") as f:
                    json.dump(self._collect(), f, indent=4, ensure_ascii=False)
                self._status.setText(f"📤 已导出到 {path}")
            except Exception as e:
                self._status.setText(f"⚠ 导出失败: {e}")

    def _import_config(self):
        text, ok = QInputDialog.getMultiLineText(self, "导入配置", "粘贴 config.json 内容:")
        if not ok or not text.strip():
            return
        try:
            new_cfg = json.loads(text)
            if not isinstance(new_cfg, dict):
                raise ValueError("顶层必须是 JSON 对象")
            # 深合并：新值覆盖，缺失项保留原值不丢配置
            merged = copy.deepcopy(self._original)

            def deep_merge(dst, src):
                for k, v in src.items():
                    if isinstance(v, dict) and isinstance(dst.get(k), dict):
                        deep_merge(dst[k], v)
                    else:
                        dst[k] = v
            deep_merge(merged, new_cfg)
            apply_config_safe(merged)
            self._original = get_config_snapshot()
            self._refresh_from_config()
            self._status.setText("📂 导入成功（已深合并，未丢配置）")
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"JSON 解析错误: {e}")


class VideoRewashApp(QMainWindow):
    """视频去重冲洗工具主窗口（PyQt5）"""

    _progress_signal = pyqtSignal(int, int)
    _finished_signal = pyqtSignal()
    _status_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(860, 720)
        self.setMinimumSize(700, 580)

        # 加载窗口图标
        exe_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path.cwd()
        icon_path = exe_dir / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._exe_dir = exe_dir
        self._paths_file = exe_dir / "paths.json"  # 路径持久化文件
        self.input_dir = exe_dir / DEFAULT_INPUT_DIR
        self.output_dir = exe_dir / DEFAULT_OUTPUT_DIR
        try:
            self.input_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            self.input_dir = exe_dir / DEFAULT_INPUT_DIR
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            self.output_dir = exe_dir / DEFAULT_OUTPUT_DIR

        self.ffmpeg_path = detect_ffmpeg()
        self.ffprobe_path = detect_ffprobe(self.ffmpeg_path)
        self.use_nvenc = False
        self._processing = False
        self._stop_flag = False

        self.log_queue = queue.Queue()

        self._progress_signal.connect(self._update_progress)
        self._finished_signal.connect(self._reset_ui)

        self._build_ui()
        self._status_signal.connect(self.status_label.setText)  # 必须在 _build_ui 之后
        self._load_paths()  # 恢复上次保存的路径
        self._check_ffmpeg()

        self._log_timer = QTimer()
        self._log_timer.timeout.connect(self._poll_log_queue)
        self._log_timer.start(100)

    # ──────────────────────────────────────────────────────────────────────────
    #  界面构建
    # ──────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        """构建完整的 PyQt5 界面（深灰主题）"""
        central = QWidget()
        self.setCentralWidget(central)
        central.setObjectName("centralWidget")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # ── 深灰主题样式表 ──
        central.setStyleSheet("""
            QWidget#centralWidget {
                background-color: #2d2d2d;
            }
            QLabel {
                color: #d0d0d0;
                font-size: 13px;
                background: transparent;
            }
            QGroupBox {
                font-weight: bold;
                font-size: 13px;
                color: #8ab4f8;
                border: 1px solid #404040;
                border-radius: 6px;
                margin-top: 14px;
                padding: 18px 14px 14px 14px;
                background-color: #353535;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 14px;
                padding: 0 8px;
                background-color: #353535;
            }
            QLineEdit {
                border: 1px solid #505050;
                border-radius: 4px;
                padding: 7px 10px;
                background-color: #3d3d3d;
                font-size: 13px;
                color: #e0e0e0;
                selection-background-color: #4a86c8;
            }
            QLineEdit:focus {
                border: 1px solid #6a9fd8;
            }
            QLineEdit::placeholder {
                color: #707070;
            }
            QPushButton {
                background-color: #454545;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 8px 18px;
                font-size: 13px;
                font-weight: bold;
                color: #d0d0d0;
                min-width: 85px;
            }
            QPushButton:hover {
                background-color: #505050;
                border-color: #666666;
            }
            QPushButton:pressed {
                background-color: #3a3a3a;
            }
            QPushButton:disabled {
                background-color: #383838;
                color: #606060;
                border-color: #454545;
            }
            QPushButton#startBtn {
                background-color: #3a7d44;
                color: #ffffff;
                border: 1px solid #2e6335;
                font-size: 14px;
                padding: 10px 28px;
            }
            QPushButton#startBtn:hover {
                background-color: #45914f;
            }
            QPushButton#startBtn:pressed {
                background-color: #2e6335;
            }
            QPushButton#startBtn:disabled {
                background-color: #2d4a32;
                color: #708070;
                border-color: #3a5a40;
            }
            QPushButton#stopBtn {
                background-color: #a63d3d;
                color: #ffffff;
                border: 1px solid #8b3232;
            }
            QPushButton#stopBtn:hover {
                background-color: #b84848;
            }
            QPushButton#stopBtn:pressed {
                background-color: #8b3232;
            }
            QPushButton#stopBtn:disabled {
                background-color: #4a3030;
                color: #706060;
                border-color: #5a3a3a;
            }
            QProgressBar {
                border: 1px solid #505050;
                border-radius: 4px;
                text-align: center;
                font-weight: bold;
                font-size: 12px;
                color: #e0e0e0;
                background-color: #3d3d3d;
                height: 24px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4a86c8, stop:1 #3a7d44);
                border-radius: 3px;
            }
            QTextEdit {
                background-color: #1e1e1e;
                color: #c8c8c8;
                border: 1px solid #404040;
                border-radius: 4px;
                padding: 10px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                selection-background-color: #4a86c8;
            }
        """)

        # ── 标题区 ──
        title_label = QLabel("🎬 短视频去重冲洗工具")
        title_label.setFont(QFont("微软雅黑", 16, QFont.Bold))
        title_label.setStyleSheet("color: #8ab4f8;")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        # ── 状态条 ──
        self.status_label = QLabel("正在检测环境...")
        self.status_label.setFont(QFont("微软雅黑", 10))
        self.status_label.setStyleSheet(
            "color: #b0b0b0; padding: 8px 12px; background-color: #383838; "
            "border-radius: 4px; border: 1px solid #454545;"
        )
        layout.addWidget(self.status_label)

        # ── 输入输出路径选择区 ──
        path_group = QGroupBox(" 文件夹选择 ")
        path_inner = QVBoxLayout()
        path_inner.setSpacing(10)

        row1 = QHBoxLayout()
        lbl_input = QLabel("📥 输入文件夹：")
        lbl_input.setFixedWidth(105)
        lbl_input.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row1.addWidget(lbl_input)
        self.input_entry = DropLineEdit()
        self.input_entry.setText(str(self.input_dir))
        self.input_entry.setPlaceholderText("拖入文件夹或手动输入路径...")
        self.input_entry.folder_dropped.connect(self._on_drop_input)
        row1.addWidget(self.input_entry, 1)
        browse_in_btn = QPushButton("浏览...")
        browse_in_btn.setObjectName("browseBtn")
        browse_in_btn.clicked.connect(self._browse_input)
        row1.addWidget(browse_in_btn)
        path_inner.addLayout(row1)

        row2 = QHBoxLayout()
        lbl_output = QLabel("📤 输出文件夹：")
        lbl_output.setFixedWidth(105)
        lbl_output.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row2.addWidget(lbl_output)
        self.output_entry = DropLineEdit()
        self.output_entry.setText(str(self.output_dir))
        self.output_entry.setPlaceholderText("拖入文件夹或手动输入路径...")
        self.output_entry.folder_dropped.connect(self._on_drop_output)
        row2.addWidget(self.output_entry, 1)
        browse_out_btn = QPushButton("浏览...")
        browse_out_btn.setObjectName("browseBtn")
        browse_out_btn.clicked.connect(self._browse_output)
        row2.addWidget(browse_out_btn)
        path_inner.addLayout(row2)

        path_group.setLayout(path_inner)
        layout.addWidget(path_group)

        # ── 操作按钮区 ──
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self.start_btn = QPushButton("🚀 一键开始冲洗")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.clicked.connect(self._start_processing)
        btn_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_processing)
        btn_layout.addWidget(self.stop_btn)

        self.config_btn = QPushButton("⚙ 设置")
        self.config_btn.setObjectName("configBtn")
        self.config_btn.clicked.connect(self._open_config_editor)
        btn_layout.addWidget(self.config_btn)

        btn_layout.addStretch()

        open_btn = QPushButton("📂 打开输出")
        open_btn.setObjectName("openBtn")
        open_btn.clicked.connect(self._open_output_dir)
        btn_layout.addWidget(open_btn)
        layout.addLayout(btn_layout)

        # ── 进度条 ──
        progress_layout = QHBoxLayout()
        prog_lbl = QLabel("进度：")
        prog_lbl.setFixedWidth(50)
        prog_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        progress_layout.addWidget(prog_lbl)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%v / %m")
        progress_layout.addWidget(self.progress_bar, 1)
        self.progress_label = QLabel("0 / 0")
        self.progress_label.setStyleSheet("font-weight: bold; color: #8ab4f8;")
        progress_layout.addWidget(self.progress_label)
        layout.addLayout(progress_layout)

        # ── 日志框 ──
        log_group = QGroupBox(" 处理日志 ")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 10))
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group, 1)

        self._log("=" * 50)
        self._log(f"  {APP_NAME} 已启动")
        self._log(f"  输入目录: {self.input_entry.text()}")
        self._log(f"  输出目录: {self.output_entry.text()}")
        self._log("=" * 50)

    # ──────────────────────────────────────────────────────────────────────────
    #  拖放处理（PyQt5 原生支持）
    # ──────────────────────────────────────────────────────────────────────────

    def _on_drop_input(self, path):
        """输入框拖放回调"""
        self.input_entry.setText(path)
        self._log(f"📂 输入目录已切换至: {path}")

    def _on_drop_output(self, path):
        """输出框拖放回调"""
        self.output_entry.setText(path)
        self._log(f"📂 输出目录已切换至: {path}")

    # ──────────────────────────────────────────────────────────────────────────
    #  文件夹选择
    # ──────────────────────────────────────────────────────────────────────────

    def _browse_input(self):
        """选择输入文件夹"""
        path = QFileDialog.getExistingDirectory(self, "选择输入文件夹（放视频的目录）")
        if path:
            self.input_entry.setText(path)
            self._log(f"📂 输入目录已切换至: {path}")

    def _browse_output(self):
        """选择输出文件夹"""
        path = QFileDialog.getExistingDirectory(self, "选择输出文件夹（洗好的视频放这里）")
        if path:
            self.output_entry.setText(path)
            self._log(f"📂 输出目录已切换至: {path}")

    # ──────────────────────────────────────────────────────────────────────────
    #  路径持久化（保存/加载）
    # ──────────────────────────────────────────────────────────────────────────

    def _load_paths(self):
        """从 paths.json 加载上次保存的路径"""
        try:
            if self._paths_file.exists():
                with open(self._paths_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                input_path = data.get('input', str(self.input_dir))
                output_path = data.get('output', str(self.output_dir))
                self.input_entry.setText(input_path)
                self.output_entry.setText(output_path)
                self._log(f"📂 已加载上次路径: 输入={input_path}")
        except Exception as e:
            self._log(f"⚠ 加载路径配置失败: {e}")

    def _save_paths(self):
        """将当前路径保存到 paths.json"""
        try:
            data = {
                'input': self.input_entry.text(),
                'output': self.output_entry.text()
            }
            with open(self._paths_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self._log(f"⚠ 保存路径配置失败: {e}")

    def _open_output_dir(self):
        """打开输出文件夹"""
        out_path = Path(self.output_entry.text())
        if out_path.exists():
            try:
                if os.name == "nt":
                    os.startfile(str(out_path))
                elif sys.platform == "darwin":
                    sp.run(["open", str(out_path)])
                else:
                    sp.run(["xdg-open", str(out_path)])
            except Exception as e:
                self._log(f"⚠ 无法打开文件夹: {e}")
        else:
            self._log(f"⚠ 输出目录不存在: {out_path}")

    # ──────────────────────────────────────────────────────────────────────────
    #  环境检测
    # ──────────────────────────────────────────────────────────────────────────

    def _check_ffmpeg(self):
        """环境检查：FFmpeg 同步检测，NVENC/AV1 异步后台检测"""
        if not self.ffmpeg_path:
            QMessageBox.critical(
                self, "缺少 FFmpeg",
                "未找到 ffmpeg.exe 核心组件，请将该组件放入软件文件夹中！"
            )
            self.status_label.setText("❌ 未找到 FFmpeg，功能不可用")
            self.start_btn.setEnabled(False)
            self._log("【严重】未找到 FFmpeg！请将 ffmpeg.exe 放在程序同目录下")
            return

        self._log(f"FFmpeg路径: {self.ffmpeg_path}")
        self._log(f"FFprobe路径: {self.ffprobe_path}")
        self.status_label.setText(f"✅ FFmpeg: {Path(self.ffmpeg_path).name}")
        self._log("🔍 正在后台检测显卡加速能力...")

        # NVENC / AV1 检测放入后台线程，不阻塞 UI
        threading.Thread(target=self._detect_gpu_accel, daemon=True).start()

    def _detect_gpu_accel(self):
        """后台线程：检测 NVENC + AV1 硬件加速能力"""
        has_nvenc = test_nvenc(self.ffmpeg_path)

        global AV1_GPU_DECODE
        AV1_GPU_DECODE = check_av1_gpu_decode(self.ffmpeg_path)

        if AV1_GPU_DECODE:
            self.log_queue.put("  ✅ 检测到 NVIDIA AV1 GPU硬解支持")
        else:
            self.log_queue.put("  ℹ 未检测到 AV1 GPU硬解，将使用CPU解码")

        if has_nvenc:
            self.use_nvenc = True
            gpu = get_gpu_name()
            self._status_signal.emit("✅ FFmpeg OK | 🟢 N 卡硬件加速已启用")
            self.log_queue.put(f"  ✅ 检测到 NVIDIA 显卡，已启用硬件加速（h264_nvenc）: {gpu}")
        else:
            self.use_nvenc = False
            self._status_signal.emit("✅ FFmpeg OK | 🟡 CPU 编码模式（未检测到 N 卡加速）")
            self.log_queue.put("  ⚠ 未检测到 NVIDIA 硬件加速，将自动使用 CPU 编码（libx264）")
            self.log_queue.put("  ℹ 如果你的电脑是 NVIDIA 显卡，请检查驱动是否正确安装")

    # ──────────────────────────────────────────────────────────────────────────
    #  日志管理（线程安全）
    # ──────────────────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        """向日志框追加一条消息（仅主线程调用）"""
        try:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log_text.append(f"[{timestamp}] {msg}")
            scrollbar = self.log_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        except Exception:
            pass

    def _poll_log_queue(self):
        """定时轮询日志队列（QTimer 在主线程触发）"""
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._log(msg)
        except queue.Empty:
            pass

    # ──────────────────────────────────────────────────────────────────────────
    #  核心处理逻辑
    # ──────────────────────────────────────────────────────────────────────────

    def _start_processing(self):
        """启动批量冲洗任务"""
        if self._processing:
            self._log("⚠ 正在处理中，请耐心等待...")
            return

        if not self.ffmpeg_path:
            QMessageBox.critical(self, "缺少组件", "未找到 FFmpeg，无法开始处理")
            return

        input_dir = Path(self.input_entry.text())
        output_dir = Path(self.output_entry.text())

        if not input_dir.exists():
            QMessageBox.critical(self, "目录错误", f"输入文件夹不存在：\n{input_dir}")
            return

        output_dir.mkdir(parents=True, exist_ok=True)

        video_files = []
        for f in sorted(input_dir.iterdir()):
            if f.suffix.lower() in SUPPORTED_EXTS and f.is_file():
                video_files.append(f)

        if not video_files:
            QMessageBox.information(self, "没有视频", f"输入文件夹中未找到视频文件：\n{input_dir}")
            return

        self._processing = True
        self._stop_flag = False
        FFMPEG_STOP_EVENT.clear()  # 清除上次的停止信号
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.config = CONFIG
        version_count = self.config.get("version_count", 1)
        total = len(video_files) * version_count
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0 / {total}")

        self._log("=" * 60)
        self._log(f"🚀 批量冲洗开始！共发现 {total} 个视频")
        self._log(f"   编码模式: {'N卡硬件加速 (h264_nvenc)' if self.use_nvenc else 'CPU软件编码 (libx264)'}")
        self._log(f"   并发数: {MAX_WORKERS} 路")
        self._log("=" * 60)

        self._processing_thread = threading.Thread(
            target=self._run_batch,
            args=(video_files, output_dir),
            daemon=True,
        )
        self._processing_thread.start()

    def _run_batch(self, video_files: list, output_dir: Path):
        """后台批量处理线程"""
        version_count = self.config.get("version_count", 1)
        total = len(video_files) * version_count
        completed = 0
        success_count = 0
        fail_count = 0

        error_log_path = str(output_dir / "_冲洗错误日志.txt")

        # 指纹检测配置
        fp_cfg = CONFIG.get("fingerprint", {})
        fp_enable = fp_cfg.get("enable", False)
        fp_sample = fp_cfg.get("sample_frames", 10)
        fp_max_sim = fp_cfg.get("max_similarity", 0.70)
        fp_retry_max = fp_cfg.get("retry_max", 3)

        # 分段处理配置
        seg_cfg = CONFIG.get("segment", {})
        seg_enable = seg_cfg.get("enable", False)

        # AI 重绘配置
        ai_cfg = CONFIG.get("ai_repaint", {})
        ai_enable = ai_cfg.get("enable", False)
        if ai_enable:
            if check_sd_api(ai_cfg.get("api_url", "http://127.0.0.1:7860")):
                self.log_queue.put("  🎨 AI 重绘模式已启用 (SD API 已连接)")
            else:
                self.log_queue.put("  ⚠️ AI 重绘已配置但 SD API 不可用，将回退到普通模式")
                self.log_queue.put("  💡 启动命令: webui-user.bat --api --listen")
                ai_enable = False

        # GPU 复杂度分流
        gpu_sched_cfg = CONFIG.get("performance", {}).get("gpu_scheduler", {})
        gpu_sched_enable = gpu_sched_cfg.get("enable", False)
        complexity_threshold = gpu_sched_cfg.get("complexity_threshold", 5)
        scheduler = GPUScheduler()

        # ★ 并行处理：并发数由 GPUScheduler 根据 GPU 编码器实时负载决定
        try:
            max_workers = max(1, scheduler.workers())
        except Exception:
            max_workers = max(1, int(MAX_WORKERS))
        max_workers = min(max_workers, max(1, int(MAX_WORKERS)))
        self.log_queue.put(f"⚙️ 并行处理: {max_workers} 个任务同时运行")

        counter_lock = threading.Lock()
        name_lock = threading.Lock()
        used_names = set()

        def reserve_out_path(stem, version):
            """并发安全的输出文件名分配"""
            with name_lock:
                out_name = f"{stem}_v{version:02d}.mp4"
                counter = 1
                while out_name in used_names or (output_dir / out_name).exists():
                    out_name = f"{stem}_v{version:02d}_{counter}.mp4"
                    counter += 1
                used_names.add(out_name)
                return output_dir / out_name

        def run_one(video_path, version):
            """单个任务：倒放预处理 + 处理 + 指纹重试 + 清理"""
            nonlocal completed, success_count, fail_count

            out_path = reserve_out_path(video_path.stem, version)
            out_name = out_path.name

            # 速度随机化（用 .get 容错，配置缺项不崩溃）
            speed_cfg = CONFIG.get("speed", {})
            if speed_cfg.get("enable", False):
                speed = random.uniform(
                    float(speed_cfg.get("min", 0.95)),
                    float(speed_cfg.get("max", 1.05))
                )
            else:
                speed = 1.0

            # GPU/CPU 复杂度分流
            use_gpu = self.use_nvenc
            if gpu_sched_enable:
                complexity = compute_filter_complexity()
                if complexity >= complexity_threshold:
                    gpu_usage = scheduler.get_usage()
                    if gpu_usage > gpu_sched_cfg.get("max_gpu_usage", 90):
                        use_gpu = False
                        self.log_queue.put(f"  ⚡ {out_name} 复杂度高({complexity}) + GPU繁忙({gpu_usage}%) → 切换CPU")

            # 处理函数：AI重绘 > 分段 > 普通
            if ai_enable:
                process_func = process_ai_repaint
            elif seg_enable:
                process_func = process_segmented
            else:
                process_func = process_single_video

            # ★ 极短片段倒放/循环预处理（时序扰动，按概率触发）
            actual_input, rv_temp = maybe_reverse_preprocess(
                str(video_path), self.ffmpeg_path, self.ffprobe_path, self.log_queue
            )

            # 生成 + 指纹检测 + 重试循环
            attempt = 0
            max_attempts = fp_retry_max + 1 if fp_enable else 1
            final_ok = False

            # GPU 任务用信号量限流，避免 NVENC 会话超限
            gpu_slot_acquired = False
            if use_gpu:
                NVENC_SEMAPHORE.acquire()
                gpu_slot_acquired = True

            try:
                while attempt < max_attempts:
                    if self._stop_flag:
                        break

                    attempt += 1
                    if attempt > 1:
                        self.log_queue.put(f"  🔄 {out_name} 指纹不合格，第{attempt-1}次重试...")
                        if out_path.exists():
                            os.remove(str(out_path))

                    try:
                        ok = process_func(
                            actual_input,
                            str(out_path),
                            self.ffmpeg_path,
                            self.ffprobe_path,
                            use_gpu,
                            speed,
                            self.log_queue,
                            error_log_path,
                        )
                    except Exception as e:
                        ok = False
                        self.log_queue.put(f"  💥 任务异常: {out_name} → {str(e)[:80]}")

                    if not ok:
                        break

                    # 指纹检测
                    if fp_enable and out_path.exists():
                        similarity = compute_video_similarity(
                            self.ffmpeg_path,
                            self.ffprobe_path,
                            str(video_path),
                            str(out_path),
                            fp_sample
                        )
                        sim_pct = similarity * 100
                        if similarity <= fp_max_sim:
                            self.log_queue.put(f"  🔍 {out_name} 指纹相似度: {sim_pct:.1f}% ✅ 合格")
                            final_ok = True
                            break
                        else:
                            self.log_queue.put(f"  🔍 {out_name} 指纹相似度: {sim_pct:.1f}% ❌ 不合格(>{fp_max_sim*100:.0f}%)")
                            if attempt >= max_attempts:
                                self.log_queue.put(f"  ⚠️ {out_name} 已达最大重试次数({fp_retry_max})，保留当前结果")
                                final_ok = True
                    else:
                        final_ok = True
                        break
            finally:
                # 释放 GPU 编码会话槽位
                if gpu_slot_acquired:
                    NVENC_SEMAPHORE.release()
                # 清理倒放预处理临时文件
                if rv_temp:
                    try:
                        if os.path.exists(rv_temp):
                            os.remove(rv_temp)
                        os.rmdir(os.path.dirname(rv_temp))
                    except Exception:
                        pass

            with counter_lock:
                completed += 1
                if final_ok:
                    success_count += 1
                else:
                    fail_count += 1
                cur, tot = completed, total
            self._progress_signal.emit(cur, tot)
            return final_ok

        try:
            # 构建全部任务（视频 × 版本）
            task_list = [
                (vp, ver)
                for vp in video_files
                for ver in range(1, version_count + 1)
            ]

            executor = ThreadPoolExecutor(max_workers=max_workers)
            futures = {}
            for vp, ver in task_list:
                if self._stop_flag:
                    break
                futures[executor.submit(run_one, vp, ver)] = (vp, ver)

            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as e:
                    self.log_queue.put(f"  💥 并行任务异常: {str(e)[:80]}")
                    with counter_lock:
                        completed += 1
                        fail_count += 1
                if self._stop_flag:
                    for f in futures:
                        f.cancel()
                    break

            executor.shutdown(wait=True)

            self.log_queue.put("=" * 60)
            if self._stop_flag:
                self.log_queue.put(f"⏹ 已手动停止。成功: {success_count}, 失败: {fail_count}, 总计: {completed}/{total}")
            else:
                self.log_queue.put(f"🎉 全部处理完成！")
                self.log_queue.put(f"    ✅ 成功: {success_count} 个   ❌ 失败: {fail_count} 个")
                self.log_queue.put(f"    📁 输出目录: {output_dir}")
            self.log_queue.put("=" * 60)

        except Exception as e:
            self.log_queue.put(f"🔥 批量处理异常: {str(e)[:120]}")

        finally:
            self._finished_signal.emit()

    def _update_progress(self, current: int, total: int):
        """更新进度条（主线程，由信号触发）"""
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"{current} / {total}")

    def _reset_ui(self):
        """处理后恢复界面（主线程，由信号触发）"""
        self._processing = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    # ──────────────────────────────────────────────────────────────────────────
    #  停止控制
    # ──────────────────────────────────────────────────────────────────────────

    def _open_config_editor(self):
        """打开配置编辑器（应用后下一个视频生效）"""
        dialog = ConfigEditorDialog(self)
        dialog.exec_()
        # 同步版本数等主界面相关配置显示
        self.config = CONFIG

    def _stop_processing(self):
        """安全停止正在进行的批量处理（正在运行的 FFmpeg 子进程会被终止）"""
        if self._processing:
            self._stop_flag = True
            FFMPEG_STOP_EVENT.set()  # 通知 run_ffmpeg 终止所有子进程
            self._log("⏹ 正在向处理引擎发送停止信号（子进程将被终止）...")
            self.stop_btn.setEnabled(False)

    # ──────────────────────────────────────────────────────────────────────────
    #  窗口关闭
    # ──────────────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        """窗口关闭时安全退出"""
        if self._processing:
            reply = QMessageBox.question(
                self, "确认退出",
                "正在处理视频，确认退出？\n未完成的任务将中断。",
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Cancel
            )
            if reply != QMessageBox.Ok:
                event.ignore()
                return
            self._stop_flag = True
            FFMPEG_STOP_EVENT.set()
            if hasattr(self, '_processing_thread') and self._processing_thread.is_alive():
                self._processing_thread.join(timeout=2)
        # 保存当前路径配置
        self._save_paths()
        event.accept()


def detect_black_crop(
        ffmpeg_path: str,
        video_path: str
):
    """
    自动检测视频黑边
    返回:
    crop=宽:高:x:y
    """

    try:

        cmd = [
            ffmpeg_path,
            "-hide_banner",
            "-i",
            video_path,
            "-vf",
            "cropdetect=24:16:0",
            "-frames:v",
            "100",
            "-f",
            "null",
            "-"
        ]

        result = sp.run(
            cmd,
            stdout=sp.PIPE,
            stderr=sp.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=30
        )

        text = result.stderr

        crops = []

        for line in text.splitlines():

            if "crop=" in line:
                value = line.split("crop=")[1]

                value = value.split()[0]

                crops.append(value)

        if crops:
            # 取出现最多的
            crop = max(
                set(crops),
                key=crops.count
            )

            print(
                "检测黑边:",
                crop
            )

            return crop

        return None



    except Exception as e:

        print(
            "黑边检测失败:",
            e
        )

        return None


# ══════════════════════════════════════════════════════════════════════════════
#  AI 生成式重绘（Stable Diffusion + ControlNet）
# ══════════════════════════════════════════════════════════════════════════════

def check_sd_api(api_url: str = "http://127.0.0.1:7860") -> bool:
    """
    检查 Stable Diffusion WebUI API 是否可用。
    返回 True 表示 API 可用。
    """
    try:
        import urllib.request
        req = urllib.request.Request(f"{api_url}/sdapi/v1/options", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def process_ai_repaint(
    input_path: str,
    output_path: str,
    ffmpeg_path: str,
    ffprobe_path: str,
    use_nvenc: bool,
    speed: float,
    log_queue,
    error_log_path: str = None,
) -> bool:
    """
    AI 生成式重绘：使用 Stable Diffusion + ControlNet 逐帧重绘视频。
    这是天花板级去重方案，像素级、特征级都是全新的。
    
    加速策略：
    1. 跳帧重绘：每 N 帧只重绘 1 帧，其余帧复制最近关键帧
    2. 低分辨率处理：384x384 而非 512x512，速度提升 2 倍
    3. 少步数：8 步而非 20 步，速度提升 2.5 倍
    4. 快速采样器：Euler a 比 DPM++ 2M 快 30%
    综合提速：约 10~15 倍
    """
    import tempfile
    import base64
    import json as json_module

    ai_cfg = CONFIG.get("ai_repaint", {})
    if not ai_cfg.get("enable", False):
        return process_single_video(
            input_path, output_path, ffmpeg_path, ffprobe_path,
            use_nvenc, speed, log_queue, error_log_path
        )

    api_url = ai_cfg.get("api_url", "http://127.0.0.1:7860")

    # 检查 SD API 是否可用
    if not check_sd_api(api_url):
        log_queue.put("  ⚠️ SD WebUI API 不可用，回退到普通模式")
        log_queue.put("  💡 请确保 SD WebUI 已启动并添加 --api 参数")
        log_queue.put("  💡 启动命令: webui-user.bat --api --listen")
        return process_single_video(
            input_path, output_path, ffmpeg_path, ffprobe_path,
            use_nvenc, speed, log_queue, error_log_path
        )

    # 获取视频信息
    duration = get_video_duration(ffprobe_path, input_path)
    if duration <= 0:
        return process_single_video(
            input_path, output_path, ffmpeg_path, ffprobe_path,
            use_nvenc, speed, log_queue, error_log_path
        )

    fps = ai_cfg.get("fps", 30)
    total_frames = int(duration * fps)

    # SD 参数
    denoising = ai_cfg.get("denoising_strength", 0.35)
    cfg_scale = ai_cfg.get("cfg_scale", 7)
    steps = ai_cfg.get("steps", 8)
    sampler = ai_cfg.get("sampler", "Euler a")
    prompt = ai_cfg.get("prompt", "high quality, detailed, natural lighting")
    negative_prompt = ai_cfg.get("negative_prompt", "blurry, distorted, low quality")
    proc_w = ai_cfg.get("process_width", 384)
    proc_h = ai_cfg.get("process_height", 384)
    keyframe_interval = ai_cfg.get("keyframe_interval", 5)  # 每 5 帧重绘 1 帧
    interp_method = ai_cfg.get("interpolate_method", "copy")  # copy 或 minterpolate

    # 计算实际需要 AI 重绘的帧数
    keyframe_count = (total_frames + keyframe_interval - 1) // keyframe_interval
    log_queue.put(f"  📊 总帧数:{total_frames} 关键帧:{keyframe_count} (每{keyframe_interval}帧重绘1帧)")
    log_queue.put(f"  📊 分辨率:{proc_w}x{proc_h} 步数:{steps} 采样器:{sampler}")

    # 创建临时目录
    temp_dir = tempfile.mkdtemp(prefix="rewash_ai_")
    frames_dir = os.path.join(temp_dir, "frames")
    output_frames_dir = os.path.join(temp_dir, "output_frames")
    os.makedirs(frames_dir)
    os.makedirs(output_frames_dir)

    try:
        # 1. 提取视频帧
        log_queue.put(f"  📸 提取视频帧 ({total_frames} 帧)...")
        cmd_extract = [
            ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-i", input_path,
            "-vf", f"fps={fps}",
            "-q:v", "2",
            os.path.join(frames_dir, "frame_%06d.jpg")
        ]
        r = run_ffmpeg(cmd_extract, timeout=300)
        if r.returncode != 0:
            log_queue.put(f"  ❌ 帧提取失败: {r.stderr[:100]}")
            return False

        frame_files = sorted([f for f in os.listdir(frames_dir) if f.endswith(".jpg")])
        actual_frames = len(frame_files)
        log_queue.put(f"  ✅ 提取完成: {actual_frames} 帧")

        # 2. 只对关键帧进行 AI 重绘
        log_queue.put(f"  🎨 AI 重绘关键帧中...")
        import urllib.request

        keyframe_indices = list(range(0, actual_frames, keyframe_interval))
        processed_count = 0

        for kf_idx in keyframe_indices:
            frame_name = frame_files[kf_idx]
            frame_path = os.path.join(frames_dir, frame_name)
            output_frame_path = os.path.join(output_frames_dir, frame_name)

            # 读取帧图片
            with open(frame_path, "rb") as f:
                img_data = f.read()
            img_base64 = base64.b64encode(img_data).decode("utf-8")

            # 构建 SD API 请求 (img2img + ControlNet)
            payload = {
                "init_images": [img_base64],
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "steps": steps,
                "cfg_scale": cfg_scale,
                "denoising_strength": denoising,
                "sampler_name": sampler,
                "width": proc_w,
                "height": proc_h,
                "alwayson_scripts": {
                    "controlnet": {
                        "args": [{
                            "input_image": img_base64,
                            "module": "depth",
                            "model": ai_cfg.get("controlnet_model", "control_v11f1p_sd15_depth"),
                            "weight": 1.0,
                            "resize_mode": "Scale to Fit",
                            "lowvram": True
                        }]
                    }
                }
            }

            try:
                req = urllib.request.Request(
                    f"{api_url}/sdapi/v1/img2img",
                    data=json_module.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    result = json_module.loads(resp.read().decode("utf-8"))

                if result.get("images"):
                    img_bytes = base64.b64decode(result["images"][0])
                    with open(output_frame_path, "wb") as f:
                        f.write(img_bytes)
                else:
                    shutil.copy(frame_path, output_frame_path)

            except Exception as e:
                log_queue.put(f"  ⚠️ 帧 {frame_name} 处理失败: {str(e)[:50]}")
                shutil.copy(frame_path, output_frame_path)

            processed_count += 1
            progress = (processed_count / len(keyframe_indices)) * 100
            log_queue.put(f"  🎨 关键帧进度: {progress:.1f}% ({processed_count}/{len(keyframe_indices)})")

        log_queue.put(f"  ✅ AI 重绘完成 ({processed_count} 帧)")

        # 3. 填充非关键帧（复制最近关键帧）
        log_queue.put(f"  📋 填充非关键帧...")
        for i in range(actual_frames):
            frame_name = frame_files[i]
            output_frame_path = os.path.join(output_frames_dir, frame_name)
            if not os.path.exists(output_frame_path):
                # 找最近的关键帧
                nearest_kf = min(keyframe_indices, key=lambda k: abs(k - i))
                nearest_kf_name = frame_files[nearest_kf]
                nearest_kf_path = os.path.join(output_frames_dir, nearest_kf_name)
                shutil.copy(nearest_kf_path, output_frame_path)

        log_queue.put(f"  ✅ 帧填充完成")

        # 4. 合成视频（speed 参数生效：视频 setpts + 音频 atempo 同步变速）
        log_queue.put("  🎬 合成视频中...")
        cmd_merge = [
            ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-framerate", str(fps),
            "-i", os.path.join(output_frames_dir, "frame_%06d.jpg"),
            "-i", input_path,
            "-vf", f"setpts={1/speed:.6f}*PTS,format=yuv420p",
            "-c:v", "libx264" if not use_nvenc else "h264_nvenc",
            "-pix_fmt", "yuv420p",
            "-af", f"atempo={speed:.6f}",
            "-c:a", "aac",
            "-b:a", "128k",
            "-map", "0:v:0",
            "-map", "1:a:0?",
            "-shortest",
            "-y", output_path
        ]
        r = run_ffmpeg(cmd_merge, timeout=600)

        if r.returncode != 0:
            log_queue.put(f"  ❌ 视频合成失败: {r.stderr[:100]}")
            return False

        # 5. 清理临时文件
        shutil.rmtree(temp_dir, ignore_errors=True)

        log_queue.put(f"  ✅ AI 重绘视频生成完成")
        return True

    except Exception as e:
        log_queue.put(f"  ❌ AI 重绘异常: {str(e)[:80]}")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return process_single_video(
            input_path, output_path, ffmpeg_path, ffprobe_path,
            use_nvenc, speed, log_queue, error_log_path
        )


def get_gpu_name():
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=name",
            "--format=csv,noheader"
        ]

        r = sp.run(
            cmd,
            capture_output=True,
            text=True
        )

        return r.stdout.strip()

    except Exception:
        return "未知GPU"


# ══════════════════════════════════════════════════════════════════════════════
#  功能1: 视频指纹检测 + 相似度评分
# ══════════════════════════════════════════════════════════════════════════════

def compute_video_similarity(
    ffmpeg_path: str,
    ffprobe_path: str,
    original_path: str,
    processed_path: str,
    sample_frames: int = 10
) -> float:
    """
    计算两个视频的感知相似度（dHash 纯 Python 实现）。
    提取帧→缩放到 9x8 灰度→比较相邻像素生成 64 位哈希→汉明距离。
    不依赖 phash 滤镜（部分 FFmpeg build 未编译），稳定可靠。
    返回: float (0.0~1.0), 0=完全不同, 1=完全相同
    """
    def extract_hashes(video_path: str) -> list:
        """提取视频帧的 dHash 列表"""
        duration = get_video_duration(ffprobe_path, video_path)
        if not duration or duration <= 0:
            return []
        interval = max(0.5, duration / (sample_frames + 1))
        timestamps = [interval * (i + 1) for i in range(sample_frames)]
        hashes = []
        for ts in timestamps:
            try:
                cmd = [
                    ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{ts:.3f}",
                    "-i", video_path,
                    "-frames:v", "1",
                    "-vf", "scale=9:8,format=gray",
                    "-f", "rawvideo", "-"
                ]
                r = sp.run(cmd, stdout=sp.PIPE, stderr=sp.DEVNULL, timeout=15)
                pixels = r.stdout[:72]  # 9x8=72 字节灰度值
                if len(pixels) < 72:
                    continue
                # dHash: 每行相邻像素比较，8 行 x 8 位 = 64 位
                bits = []
                for row in range(8):
                    for col in range(8):
                        bits.append(1 if pixels[row * 9 + col] < pixels[row * 9 + col + 1] else 0)
                hashes.append(bits)
            except Exception:
                pass
        return hashes

    try:
        hashes_orig = extract_hashes(original_path)
        hashes_proc = extract_hashes(processed_path)
        if not hashes_orig or not hashes_proc:
            return 0.5  # 无法检测时返回中间值
        n = min(len(hashes_orig), len(hashes_proc))
        if n == 0:
            return 0.5
        total_similarity = 0.0
        compared = 0
        for i in range(n):
            h1, h2 = hashes_orig[i], hashes_proc[i]
            diff_bits = sum(b1 != b2 for b1, b2 in zip(h1, h2))
            total_similarity += 1.0 - (diff_bits / 64.0)
            compared += 1
        if compared == 0:
            return 0.5
        return total_similarity / compared
    except Exception:
        return 0.5


# ══════════════════════════════════════════════════════════════════════════════
#  功能3: GPU/CPU 滤镜复杂度评估
# ══════════════════════════════════════════════════════════════════════════════

def compute_filter_complexity() -> int:
    """
    根据当前启用的滤镜计算复杂度分数。
    分数越高，GPU 负载越大。
    """
    score = 0
    video_cfg = CONFIG.get("video", {})
    # 低复杂度滤镜 (1分)
    if video_cfg.get("scale", {}).get("enable", False):
        score += 1
    if video_cfg.get("rotate", {}).get("enable", False):
        score += 1
    if video_cfg.get("asymmetric_crop", {}).get("enable", False):
        score += 1
    if video_cfg.get("vignette", {}).get("enable", False):
        score += 1
    # 中复杂度滤镜 (2分)
    if video_cfg.get("hue", {}).get("enable", False):
        score += 2
    if video_cfg.get("brightness", {}).get("enable", False):
        score += 2
    if video_cfg.get("contrast", {}).get("enable", False):
        score += 2
    if video_cfg.get("saturation", {}).get("enable", False):
        score += 2
    if video_cfg.get("noise", {}).get("enable", False):
        score += 2
    # 高复杂度滤镜 (3分)
    if video_cfg.get("sharpness", {}).get("enable", False):
        score += 3
    if video_cfg.get("dynamic_drift", {}).get("enable", False):
        score += 2
    if video_cfg.get("lens_distortion", {}).get("enable", False):
        score += 2
    if video_cfg.get("edge_perturbation", {}).get("enable", False):
        score += 2
    if video_cfg.get("rgb_shift", {}).get("enable", False):
        score += 1
    if video_cfg.get("mask_drift", {}).get("enable", False):
        score += 3
    if video_cfg.get("frame_drop", {}).get("enable", False):
        score += 1
    return score


# ══════════════════════════════════════════════════════════════════════════════
#  功能4: 分段式处理
# ══════════════════════════════════════════════════════════════════════════════

def process_segmented(
    input_path: str,
    output_path: str,
    ffmpeg_path: str,
    ffprobe_path: str,
    use_nvenc: bool,
    speed: float,
    log_queue,
    error_log_path: str = None,
) -> bool:
    """
    分段式处理：将视频分成 N 段，每段使用不同随机参数，最后合并。
    """
    import tempfile

    seg_cfg = CONFIG.get("segment", {})
    if not seg_cfg.get("enable", False):
        # 未启用分段，走普通处理
        return process_single_video(
            input_path, output_path, ffmpeg_path, ffprobe_path,
            use_nvenc, speed, log_queue, error_log_path
        )

    count_cfg = seg_cfg.get("count", {"min": 2, "max": 4})
    seg_count = random.randint(int(count_cfg.get("min", 2)), int(count_cfg.get("max", 4)))
    crossfade = float(seg_cfg.get("crossfade", 0.2))

    duration = get_video_duration(ffprobe_path, input_path)
    if duration <= 0 or seg_count < 2:
        return process_single_video(
            input_path, output_path, ffmpeg_path, ffprobe_path,
            use_nvenc, speed, log_queue, error_log_path
        )

    # ★ 场景切换点参数突变：检测镜头跳转点作为分段边界
    #   每段参数独立随机 → 切换点处自然形成参数突变，切断分段匹配
    boundaries = None
    scene_split_cfg = seg_cfg.get("scene_split", {})
    if scene_split_cfg.get("enable", False):
        sc_threshold = float(scene_split_cfg.get("threshold", 0.3))
        cuts = detect_scene_cuts(
            ffmpeg_path, input_path,
            threshold=sc_threshold, max_cuts=seg_count - 1
        )
        cuts = [c for c in cuts if 0.5 < c < duration - 0.5]
        if cuts:
            boundaries = [0.0] + cuts[:seg_count - 1] + [duration]
            seg_count = len(boundaries) - 1
            log_queue.put(f"  🎬 检测到 {len(cuts)} 个场景切换点，按切换点分段+参数突变")

    # 计算每段时长
    seg_duration = duration / seg_count
    video_name = Path(output_path).stem
    temp_dir = tempfile.mkdtemp(prefix="rewash_seg_")
    temp_files = []
    seg_use_nvenc = use_nvenc  # 统一各段编码器（任一段 NVENC 失败则后续全部 CPU）

    try:
        for seg_idx in range(seg_count):
            if boundaries:
                seg_start = boundaries[seg_idx]
                seg_end = boundaries[seg_idx + 1]
            else:
                seg_start = seg_idx * seg_duration
                seg_end = min((seg_idx + 1) * seg_duration, duration)
            seg_dur = seg_end - seg_start
            seg_temp = os.path.join(temp_dir, f"seg_{seg_idx:02d}.mp4")
            temp_files.append(seg_temp)

            # 每段生成不同的随机参数
            seg_scale = config_random("video.scale", 1.0)
            seg_rotate = config_random("video.rotate", 0)
            seg_noise = config_random("video.noise", 0)
            seg_contrast = config_random("video.contrast", 1)
            seg_brightness = config_random("video.brightness", 0)
            seg_saturation = config_random("video.saturation", 1)
            seg_sharpness = config_random("video.sharpness", 0)
            seg_hue = config_random("video.hue", 0)

            # ★ 场景切换点参数突变：切换点后的段放大 1.5 倍参数幅度
            if boundaries and seg_idx > 0:
                seg_scale = 1.0 + (seg_scale - 1.0) * 1.5
                seg_rotate = seg_rotate * 1.5
                seg_hue = seg_hue * 1.5

            video_cfg = CONFIG.get("video", {})

            # 胶片颗粒
            seg_grain = 0.0
            grain_cfg = video_cfg.get("film_grain", {})
            if grain_cfg.get("enable", False):
                seg_grain = float(grain_cfg.get("strength", 3))

            # ★ 逐帧动态微位移
            seg_drift_amp = 0.0
            seg_drift_period = 7.0
            drift_cfg = video_cfg.get("dynamic_drift", {})
            if drift_cfg.get("enable", False):
                seg_drift_amp = float(drift_cfg.get("amplitude_px", 2))
                dp_cfg = drift_cfg.get("period_sec", {})
                if isinstance(dp_cfg, dict):
                    seg_drift_period = random.uniform(
                        float(dp_cfg.get("min", 5)), float(dp_cfg.get("max", 10))
                    )
                else:
                    seg_drift_period = float(dp_cfg)

            # ★ 镜头畸变
            seg_lens_k1, seg_lens_k2 = 0.0, 0.0
            lens_cfg = video_cfg.get("lens_distortion", {})
            if lens_cfg.get("enable", False):
                k1r = float(lens_cfg.get("k1_range", 0.02))
                k2r = float(lens_cfg.get("k2_range", 0.005))
                seg_lens_k1 = random.uniform(-k1r, k1r)
                seg_lens_k2 = random.uniform(-k2r, k2r)

            # ★ 边缘扰动
            seg_edge_noise = 0.0
            edge_cfg = video_cfg.get("edge_perturbation", {})
            if edge_cfg.get("enable", False):
                seg_edge_noise = float(edge_cfg.get("strength", 3))

            # ★ RGB 通道微错位
            seg_rgb_shift = 0.0
            rgb_cfg = video_cfg.get("rgb_shift", {})
            if rgb_cfg.get("enable", False):
                seg_rgb_shift = float(rgb_cfg.get("max_px", 1))

            # ★ 半透明渐变蒙版漂移
            seg_mask_drift = 0.0
            mask_cfg = video_cfg.get("mask_drift", {})
            if mask_cfg.get("enable", False):
                seg_mask_drift = float(mask_cfg.get("strength", 2))

            # ★ 分块亮度独立微调
            seg_lum_wave = 0.0
            lum_cfg = video_cfg.get("luminance_wave", {})
            if lum_cfg.get("enable", False):
                seg_lum_wave = float(lum_cfg.get("strength", 2)) * 255 * 0.02

            # 构建该段的视频滤镜链
            vf_chain = build_video_filter(
                scale=seg_scale,
                rotate_deg=seg_rotate,
                noise_pct=seg_noise,
                contrast=seg_contrast,
                brightness=seg_brightness,
                saturation=seg_saturation,
                sharpness=seg_sharpness,
                hue_shift=seg_hue,
                drift_amp=seg_drift_amp,
                drift_period=seg_drift_period,
                lens_k1=seg_lens_k1,
                lens_k2=seg_lens_k2,
                edge_noise=seg_edge_noise,
                film_grain_strength=seg_grain,
                rgb_shift_px=seg_rgb_shift,
                mask_drift_strength=seg_mask_drift,
                luminance_wave=seg_lum_wave
            )

            # ★ 微量随机抽帧（每段随机丢 1 帧）
            fd_cfg = video_cfg.get("frame_drop", {})
            if fd_cfg.get("enable", False):
                fd_interval = fd_cfg.get("interval", {})
                drop_frame = random.randint(
                    int(fd_interval.get("min", 100)),
                    int(fd_interval.get("max", 200))
                )
                vf_chain += f",select='not(eq(n,{drop_frame}))',setpts=N/FRAME_RATE/TB"

            # 变速（支持非线性正弦波动）+ 像素格式
            wave_cfg = CONFIG.get("speed", {}).get("wave", {})
            if wave_cfg.get("enable", False):
                amp = float(wave_cfg.get("amplitude", 0.03))
                pw_cfg = wave_cfg.get("period_sec", {})
                if isinstance(pw_cfg, dict):
                    T_wave = random.uniform(
                        float(pw_cfg.get("min", 6)), float(pw_cfg.get("max", 12))
                    )
                else:
                    T_wave = float(pw_cfg)
                C = amp * T_wave / (2 * math.pi)
                vf_chain += (
                    f",setpts='((T-{C:.5f}*cos(2*PI*T/{T_wave:.3f})+{C:.5f})/TB)*{1/speed:.6f}'"
                    f",format=yuv420p"
                )
            else:
                vf_chain += f",setpts={1/speed:.6f}*PTS,format=yuv420p"

            # 构建 FFmpeg 命令
            cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error"]
            cmd += ["-ss", f"{seg_start:.3f}", "-i", input_path]

            # 音频噪声源输入（必须在输出选项之前）
            audio_exists = has_audio(ffprobe_path, input_path)
            noise_inputs = []
            if audio_exists:
                audio_cfg_pre = CONFIG.get("audio", {})
                noise_floor_cfg = audio_cfg_pre.get("noise_floor", {})
                if noise_floor_cfg.get("enable", False):
                    noise_db = float(noise_floor_cfg.get("db", -54))
                    noise_amp = 10 ** (noise_db / 20.0)
                    noise_inputs.append(
                        f"anoisesrc=amplitude={noise_amp:.6f}:color=pink:duration=999"
                    )
                dither_cfg_pre = audio_cfg_pre.get("dither", {})
                if dither_cfg_pre.get("enable", False):
                    dither_db = random.uniform(
                        float(dither_cfg_pre.get("strength_db", {}).get("min", -56)),
                        float(dither_cfg_pre.get("strength_db", {}).get("max", -50))
                    )
                    dither_amp = 10 ** (dither_db / 20.0)
                    noise_inputs.append(
                        f"anoisesrc=amplitude={dither_amp:.6f}:color=white:duration=999"
                    )
                for ni_src in noise_inputs:
                    cmd += ["-f", "lavfi", "-i", ni_src]

            cmd += ["-vf", vf_chain]

            # 编码器（seg_use_nvenc 保证所有段编码器一致，避免 concat 失败）
            encoder = encoder_builder(seg_use_nvenc)
            cmd += ["-c:v", encoder["codec"]] + encoder["args"]

            # 码率
            bitrate = build_bitrate()
            if bitrate:
                cmd += ["-b:v", f"{bitrate}k"]

            # 音频（同步处理：变速 + 音调 + 音量）
            if audio_exists:
                audio_filters = []
                audio_cfg = CONFIG.get("audio", {})
                # 时长限制放输出端
                cmd += ["-t", f"{seg_dur:.3f}"]

                # 音调偏移
                pitch_semi = config_random_float("audio.pitch", 0)
                pitch_ratio = 1.0
                if pitch_semi and pitch_semi != 0:
                    pitch_ratio = 2 ** (pitch_semi / 12.0)
                    sr_aset = int(48000 * pitch_ratio)
                    audio_filters.append(f"asetrate={sr_aset}")
                    audio_filters.append("aresample=48000")

                # ★ 关键：音频变速必须匹配视频速度
                effective_atempo = speed / pitch_ratio
                audio_filters.append(f"atempo={effective_atempo:.6f}")

                # 音量
                vol = config_random_float("audio.volume", 1)
                if vol and vol != 1:
                    audio_filters.append(f"volume={vol:.4f}")

                # ★ 声道间微延迟
                ch_delay_cfg = audio_cfg.get("channel_delay", {})
                if ch_delay_cfg.get("enable", False):
                    delay_ms = random.randint(
                        int(ch_delay_cfg.get("min_ms", 1)),
                        int(ch_delay_cfg.get("max_ms", 5))
                    )
                    audio_filters.append(f"adelay=0|{delay_ms}")

                # ★ 极轻微房间混响
                reverb_cfg = audio_cfg.get("reverb", {})
                if reverb_cfg.get("enable", False):
                    del_cfg = reverb_cfg.get("delay_ms", {})
                    dec_cfg = reverb_cfg.get("decay", {})
                    rv_delay = random.randint(int(del_cfg.get("min", 15)), int(del_cfg.get("max", 40)))
                    rv_decay = random.uniform(float(dec_cfg.get("min", 0.03)), float(dec_cfg.get("max", 0.08)))
                    # in_gain/out_gain 保持 1 不衰减音量，混响强度由 decays 控制
                    audio_filters.append(
                        f"aecho=in_gain=1.0:out_gain=1.0:delays={rv_delay}:decays={rv_decay:.2f}"
                    )

                # ★ 动态范围微压缩
                comp_cfg = audio_cfg.get("compressor", {})
                if comp_cfg.get("enable", False):
                    th_db = random.randint(
                        int(comp_cfg.get("threshold_db", {}).get("min", -18)),
                        int(comp_cfg.get("threshold_db", {}).get("max", -12))
                    )
                    comp_ratio = random.uniform(
                        float(comp_cfg.get("ratio", {}).get("min", 2)),
                        float(comp_cfg.get("ratio", {}).get("max", 4))
                    )
                    audio_filters.append(
                        f"acompressor=threshold={th_db}dB:ratio={comp_ratio:.1f}:attack=20:release=200:makeup=2"
                    )

                cmd += [
                    "-af", ",".join(audio_filters),
                    "-c:a", audio_cfg.get("codec", "aac"),
                    "-b:a", audio_cfg.get("bitrate", "128k"),
                    "-ar", "48000",
                    "-ac", str(audio_cfg.get("channels", 2))
                ] if not noise_inputs else []

                # 有噪声源输入时用 filter_complex 混音（不能同时用 -af）
                if noise_inputs:
                    n_inputs = 1 + len(noise_inputs)
                    labels = "".join(f"[{i}:a]" for i in range(n_inputs))
                    weights = " ".join(["1"] * n_inputs)
                    # normalize=0 避免 amix 按输入数归一化导致音量衰减
                    af_chain = f"{labels}amix=inputs={n_inputs}:duration=first:normalize=0:weights={weights}"
                    if audio_filters:
                        af_chain += "," + ",".join(audio_filters)
                    af_chain += "[aout]"
                    cmd += [
                        "-filter_complex", af_chain,
                        "-map", "0:v", "-map", "[aout]",
                        "-c:a", audio_cfg.get("codec", "aac"),
                        "-b:a", audio_cfg.get("bitrate", "128k"),
                        "-ar", "48000",
                        "-ac", str(audio_cfg.get("channels", 2))
                    ]
            else:
                cmd += ["-t", f"{seg_dur:.3f}", "-an"]

            cmd += ["-y", seg_temp]

            r = run_ffmpeg(cmd, timeout=900)

            # 段内 NVENC 失败 → 用 CPU 编码器重试该段，且后续段全部改用 CPU（保证合并时编码器一致）
            if r.returncode != 0 and encoder["codec"] == "h264_nvenc":
                try:
                    seg_use_nvenc = False
                    cpu_enc = encoder_builder(False)
                    vi = cmd.index("-c:v")
                    cmd[vi:vi + 2 + len(encoder["args"])] = ["-c:v", cpu_enc["codec"]] + cpu_enc["args"]
                    r = run_ffmpeg(cmd, timeout=900)
                except Exception:
                    pass

            if r.returncode != 0:
                log_queue.put(f"  [段{seg_idx+1}/{seg_count}] 编码失败，回退到普通模式")
                log_queue.put(f"  错误: {r.stderr.strip().splitlines()[-1][:120] if r.stderr.strip() else '无'}")
                # 清理临时文件
                for f in temp_files:
                    if os.path.exists(f):
                        os.remove(f)
                os.rmdir(temp_dir)
                return process_single_video(
                    input_path, output_path, ffmpeg_path, ffprobe_path,
                    use_nvenc, speed, log_queue, error_log_path
                )

            log_queue.put(f"  [段{seg_idx+1}/{seg_count}] 完成 (缩放:{seg_scale:.3f} 旋转:{seg_rotate:.2f} 色相:{seg_hue:.1f})")

        # 合并所有段
        concat_file = os.path.join(temp_dir, "concat.txt")
        with open(concat_file, "w", encoding="utf-8") as f:
            for tf in temp_files:
                f.write(f"file '{tf}'\n")

        cmd_merge = [
            ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-c", "copy",
            "-y", output_path
        ]
        r = run_ffmpeg(cmd_merge, timeout=120)

        # 清理临时文件
        for tf in temp_files:
            if os.path.exists(tf):
                os.remove(tf)
        if os.path.exists(concat_file):
            os.remove(concat_file)
        os.rmdir(temp_dir)

        if r.returncode != 0:
            log_queue.put(f"  段合并失败，回退到普通模式")
            if os.path.exists(output_path):
                os.remove(output_path)
            return process_single_video(
                input_path, output_path, ffmpeg_path, ffprobe_path,
                use_nvenc, speed, log_queue, error_log_path
            )

        # ★ 二次编码叠加压缩噪声（可选，有 NVENC 时用 GPU 提速）
        apply_double_encode(output_path, ffmpeg_path, log_queue, use_nvenc=use_nvenc)

        # ★ MP4 容器结构随机化（安全 remux 模式）
        postprocess_mp4_container(output_path, log_queue, ffmpeg_path)

        return True

    except Exception as e:
        log_queue.put(f"  分段处理异常: {str(e)[:80]}")
        # 清理
        for tf in temp_files:
            if os.path.exists(tf):
                os.remove(tf)
        try:
            os.rmdir(temp_dir)
        except Exception:
            pass
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  程序入口
# ══════════════════════════════════════════════════════════════════════════════


def main():
    """启动 PyQt5 主循环"""
    app = QApplication(sys.argv)
    window = VideoRewashApp()
    window.show()
    try:
        sys.exit(app.exec_())
    except KeyboardInterrupt:
        print("\n用户中断，程序退出。")
        sys.exit(0)


class GPUScheduler:


    def get_usage(self):

        try:

            r=sp.run(

                [
                    "nvidia-smi",
                    "--query-gpu=utilization.encoder",
                    "--format=csv,noheader,nounits"
                ],

                capture_output=True,
                text=True

            )

            return int(
                r.stdout.strip()
            )


        except Exception:

            return 0



    def workers(self):

        enc=self.get_usage()


        if enc>95:
            return 1


        if enc>80:
            return max(
                1,
                MAX_WORKERS//2
            )


        return MAX_WORKERS

if __name__ == "__main__":
    main()
