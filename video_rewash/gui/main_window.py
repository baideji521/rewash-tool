# -*- coding: utf-8 -*-
"""
gui.main_window — v7.1 主窗口（回归老版 UI 布局）

- 布局参照老版 video_rewash.py：标题 / 状态条 / 输入输出文件夹 / 按钮行 / 进度条 / 日志
- 输入输出均为可拖放可手填的编辑框（拖入文件夹直接生效）
- 参数/分段/标准化等全部收进「设置」对话框（SettingsDialog）
- 启动横幅日志 + 后台显卡加速检测（状态条显示结果）
- 全部界面状态持久化（输入/输出目录、设置项、上次预设）
- 不再显示“参数已微调”状态
"""

import os
import time
import copy
import subprocess

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon, QFont, QDragEnterEvent, QDropEvent
from PyQt5.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QGroupBox,
    QFileDialog,
    QProgressBar,
    QPlainTextEdit,
    QMessageBox,
)

try:
    import windnd
except ImportError:
    windnd = None

from ..core import preset as preset_mod
from ..core.config import STORE, config_get
from ..core.ffmpeg_runner import (
    STOP_EVENT,
    detect_ffmpeg,
    detect_ffprobe,
    no_window_kwargs,
)
from ..batch.worker import BatchRunner
from .settings_dialog import SettingsDialog
from .styles import QSS


BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )
)

VIDEO_EXTS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".flv",
    ".ts",
    ".wmv",
    ".m4v",
    ".webm",
}


# ============================================================
# 拖放输入框
# ============================================================

class DropLineEdit(QLineEdit):
    """支持文件夹拖放的路径编辑框（可手动编辑路径）"""

    path_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setAcceptDrops(True)
        self.setReadOnly(False)

        # 拖放高亮样式
        self._drag_style = (
            "background-color: #3a5a3a;"
            "border: 2px solid #6a9fd8;"
            "color: #e0e0e0;"
        )

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self._drag_style)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet("")

    def dropEvent(self, event: QDropEvent):
        self.setStyleSheet("")

        urls = event.mimeData().urls()

        if urls:
            self.path_dropped.emit(
                urls[0].toLocalFile()
            )


# ============================================================
# GPU 检测线程
# ============================================================

class GpuDetectThread(QThread):
    """后台检测 NVENC 硬件加速能力"""

    sig_result = pyqtSignal(bool, str)

    def run(self):
        has_nvenc = False
        gpu_name = "未知GPU"

        # ----------------------------------------------------
        # 检测 FFmpeg NVENC
        # ----------------------------------------------------

        try:
            ff = (
                config_get(
                    STORE.get_data(),
                    "runtime.ffmpeg",
                    "ffmpeg",
                )
                or "ffmpeg"
            )

            r = subprocess.run(
                [
                    ff,
                    "-hide_banner",
                    "-encoders",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=20,
                **no_window_kwargs(),
            )

            has_nvenc = "h264_nvenc" in (r.stdout or "")

        except Exception:
            pass

        # ----------------------------------------------------
        # 获取 NVIDIA GPU 名称
        # ----------------------------------------------------

        if has_nvenc:

            try:
                r2 = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=name",
                        "--format=csv,noheader",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    **no_window_kwargs(),
                )

                name = (r2.stdout or "").strip()

                if name:
                    gpu_name = name.splitlines()[0]

            except Exception:
                pass

        self.sig_result.emit(
            has_nvenc,
            gpu_name,
        )


# ============================================================
# 视频处理线程
# ============================================================

class ProcessThread(QThread):

    sig_log = pyqtSignal(str)

    sig_progress = pyqtSignal(
        int,
        int,
        float,
        str,
    )

    sig_file_progress = pyqtSignal(
        str,
        float,
    )

    sig_finished = pyqtSignal(dict)

    def __init__(
        self,
        inputs,
        output_dir,
        config,
        preset_snap,
        parent=None,
    ):
        super().__init__(parent)

        self.inputs = inputs
        self.output_dir = output_dir
        self.config = config
        self.preset_snap = preset_snap

    def run(self):

        try:

            runner = BatchRunner(
                self.config,
                self.preset_snap,

                log_fn=lambda m:
                    self.sig_log.emit(m),

                progress_cb=lambda d, t, e, c:
                    self.sig_progress.emit(
                        d,
                        t,
                        e,
                        c,
                    ),

                file_progress_cb=lambda p, f:
                    self.sig_file_progress.emit(
                        p,
                        f,
                    ),
            )

            result = runner.run(
                self.inputs,
                self.output_dir,
            )

            self.sig_finished.emit(result)

        except Exception as e:

            self.sig_finished.emit(
                {
                    "success": False,
                    "error": str(e),
                    "total": len(self.inputs),
                    "done": 0,
                    "failed": len(self.inputs),
                }
            )


# ============================================================
# 主窗口
# ============================================================

class MainWindow(QMainWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle(
            "🎬 视频冲洗工具 🎬"
        )

        self.resize(880, 720)

        self.setMinimumSize(
            720,
            600,
        )

        # ----------------------------------------------------
        # 图标
        # ----------------------------------------------------

        icon_path = os.path.join(
            BASE_DIR,
            "icon.png",
        )

        if os.path.exists(icon_path):
            self.setWindowIcon(
                QIcon(icon_path)
            )

        # ----------------------------------------------------
        # 运行状态
        # ----------------------------------------------------

        self._thread = None

        self._file_frac = {}

        self._last_done = 0

        self._last_total = 0

        # ----------------------------------------------------
        # 运行时 FFmpeg 配置
        # ----------------------------------------------------

        cfg = STORE.get_data()

        cfg.setdefault(
            "runtime",
            {},
        )

        ff = detect_ffmpeg()

        cfg["runtime"]["ffmpeg"] = (
            ff or "ffmpeg"
        )

        cfg["runtime"]["ffprobe"] = (
            detect_ffprobe(ff)
            or "ffprobe"
        )

        # ----------------------------------------------------
        # UI 状态
        # ----------------------------------------------------

        self._ui = {

            "segment_count":
                int(
                    config_get(
                        cfg,
                        "segment_count",
                        4,
                    )
                    or 4
                ),

            "video_concurrency":
                int(
                    config_get(
                        cfg,
                        "performance.video_concurrency",
                        1,
                    )
                    or 1
                ),

            "normalize":
                bool(
                    config_get(
                        cfg,
                        "switches.normalize",
                        True,
                    )
                ),

            "quality_check":
                bool(
                    config_get(
                        cfg,
                        "switches.quality_check",
                        True,
                    )
                ),

            "aspect_ratio":
                str(
                    config_get(
                        cfg,
                        "normalize.aspect_ratio",
                        "3:4",
                    )
                ),

            "width":
                int(
                    config_get(
                        cfg,
                        "normalize.width",
                        1080,
                    )
                ),

            "height":
                int(
                    config_get(
                        cfg,
                        "normalize.height",
                        1440,
                    )
                ),

            "fps":
                int(
                    config_get(
                        cfg,
                        "normalize.fps",
                        30,
                    )
                ),

            "pix_fmt":
                str(
                    config_get(
                        cfg,
                        "normalize.pix_fmt",
                        "yuv420p",
                    )
                ),

            "video_codec":
                str(
                    config_get(
                        cfg,
                        "normalize.video_codec",
                        "h264",
                    )
                ),

            "bitrate_kbps":
                int(
                    config_get(
                        cfg,
                        "normalize.bitrate_kbps",
                        0,
                    )
                    or 0
                ),

            # ── 全局项：指纹检测 ──
            "fp_enable":
                bool(
                    config_get(
                        cfg,
                        "fingerprint.enable",
                        True,
                    )
                ),

            "fp_threshold":
                float(
                    config_get(
                        cfg,
                        "fingerprint.max_similarity",
                        0.70,
                    )
                ),

            "fp_frames":
                int(
                    config_get(
                        cfg,
                        "fingerprint.sample_frames",
                        10,
                    )
                    or 10
                ),

            "fp_retry":
                int(
                    config_get(
                        cfg,
                        "fingerprint.retry_max",
                        3,
                    )
                    or 0
                ),

            # ── 全局项：NVENC 编码档位 ──
            "nvenc_preset":
                str(
                    config_get(
                        cfg,
                        "encode.nvenc_preset",
                        "p3",
                    )
                ),

            # ── 配置级视觉扰动（video.* 节点）──
            "ld_k1":
                float(
                    config_get(
                        cfg,
                        "video.lens_distortion.k1_range",
                        0.02,
                    )
                ),

            "ld_k2":
                float(
                    config_get(
                        cfg,
                        "video.lens_distortion.k2_range",
                        0.005,
                    )
                ),

            "ac_min":
                float(
                    config_get(
                        cfg,
                        "video.asymmetric_crop.min",
                        0.03,
                    )
                ),

            "ac_max":
                float(
                    config_get(
                        cfg,
                        "video.asymmetric_crop.max",
                        0.05,
                    )
                ),

            "rl_enable":
                bool(
                    config_get(
                        cfg,
                        "video.reverse_loop.enable",
                        True,
                    )
                ),

            "rl_prob":
                float(
                    config_get(
                        cfg,
                        "video.reverse_loop.probability",
                        0.4,
                    )
                ),

            "fd_enable":
                bool(
                    config_get(
                        cfg,
                        "video.frame_drop.enable",
                        True,
                    )
                ),

            "fd_interval_min":
                int(
                    config_get(
                        cfg,
                        "video.frame_drop.interval.min",
                        100,
                    )
                    or 100
                ),

            "fd_interval_max":
                int(
                    config_get(
                        cfg,
                        "video.frame_drop.interval.max",
                        200,
                    )
                    or 200
                ),

            # ── 事件窗口参数（v8.1 全片预处理 + 分段动态扰动）──
            "rdw_prob": float(config_get(cfg, "video.rotate_drift.probability", 0.8)),
            "rdw_dur_min": float(config_get(cfg, "video.rotate_drift.duration.min", 3.0)),
            "rdw_dur_max": float(config_get(cfg, "video.rotate_drift.duration.max", 8.0)),
            "zdw_prob": float(config_get(cfg, "video.zoom_drift.probability", 0.8)),
            "zdw_dur_min": float(config_get(cfg, "video.zoom_drift.duration.min", 3.0)),
            "zdw_dur_max": float(config_get(cfg, "video.zoom_drift.duration.max", 8.0)),
            "ldw_prob": float(config_get(cfg, "video.lens_distortion.probability", 0.6)),
            "ldw_dur_min": float(config_get(cfg, "video.lens_distortion.duration.min", 1.5)),
            "ldw_dur_max": float(config_get(cfg, "video.lens_distortion.duration.max", 4.0)),
            "ldw_count_min": int(config_get(cfg, "video.lens_distortion.count.min", 1) or 1),
            "ldw_count_max": int(config_get(cfg, "video.lens_distortion.count.max", 2) or 2),
            "rl_len_min": float(config_get(cfg, "video.reverse_loop.event_length.min", 0.1)),
            "rl_len_max": float(config_get(cfg, "video.reverse_loop.event_length.max", 0.2)),
            "fdw_prob": float(config_get(cfg, "video.frame_drop.probability", 1.0)),
            "fdw_dur_min": float(config_get(cfg, "video.frame_drop.duration.min", 2.0)),
            "fdw_dur_max": float(config_get(cfg, "video.frame_drop.duration.max", 5.0)),
        }

        # ----------------------------------------------------
        # 当前预设
        # ----------------------------------------------------

        last_used = (
            config_get(
                cfg,
                "last_used_preset",
                "standard",
            )
            or "standard"
        )

        self._preset_snap = (
            preset_mod.get_preset(
                last_used
            )
        )
        # 注：预设参数完全由 get_preset() 从预设文件（builtin/custom）加载；
        # 设置对话框的参数微调仅在当前会话生效，永久保存请用「保存为自定义」。

        # ----------------------------------------------------
        # 构建界面
        # ----------------------------------------------------

        self._build_ui()

        self._log_startup()

        # ----------------------------------------------------
        # 后台检测 GPU
        # ----------------------------------------------------

        self.status_label.setText(
            "正在检测环境..."
        )

        self._gpu_thread = GpuDetectThread(
            self
        )

        self._gpu_thread.sig_result.connect(
            self._on_gpu_result
        )

        self._gpu_thread.start()

    # ========================================================
    # UI 构建
    # ========================================================

    def _build_ui(self):

        central = QWidget()

        central.setObjectName(
            "centralWidget"
        )

        self.setCentralWidget(
            central
        )

        central.setStyleSheet(QSS)

        root = QVBoxLayout(
            central
        )

        root.setContentsMargins(
            20,
            16,
            20,
            16,
        )

        root.setSpacing(12)

        # ----------------------------------------------------
        # 标题
        # ----------------------------------------------------

        title = QLabel(
            "🎬   视 频 冲 洗 工 具  🎬"
        )

        title.setFont(
            QFont(
                "微软雅黑",
                20,
                QFont.Bold,
            )
        )

        title.setStyleSheet(
            "color: #f5f5f5;"
        )

        title.setAlignment(
            Qt.AlignCenter
        )

        root.addWidget(title)

        # ----------------------------------------------------
        # 状态栏
        # ----------------------------------------------------

        status_row = QHBoxLayout()

        status_row.setSpacing(8)

        self.status_label = QLabel(
            "正在检测环境..."
        )

        self.status_label.setFont(
            QFont(
                "微软雅黑",
                10,
            )
        )

        self.status_label.setStyleSheet(
            "color: #b0b0b0;"
            "padding: 8px 12px;"
            "background-color: #383838;"
            "border-radius: 4px;"
            "border: 1px solid #454545;"
        )

        status_row.addWidget(
            self.status_label,
            1,
        )

        self.preset_label = QLabel("")

        self.preset_label.setFont(
            QFont(
                "微软雅黑",
                10,
            )
        )

        self.preset_label.setStyleSheet(
            "color: #ffffff;"
            "padding: 7px 14px;"
            "background-color: #30343b;"
            "border: 1px solid #4d6585;"
            "border-radius: 6px;"
        )


        self.preset_label.setAlignment(
            Qt.AlignRight
            | Qt.AlignVCenter
        )
        

        status_row.addWidget(
            self.preset_label
        )

        root.addLayout(
            status_row
        )

        # ----------------------------------------------------
        # 输入输出路径
        # ----------------------------------------------------

        path_group = QGroupBox()

        path_inner = QVBoxLayout(
            path_group
        )

        path_inner.setSpacing(10)

        cfg = STORE.get_data()

        # 输入目录
        row1 = QHBoxLayout()

        lbl_input = QLabel(
            "📥 输入文件夹："
        )

        lbl_input.setFixedWidth(
            105
        )

        lbl_input.setAlignment(
            Qt.AlignRight
            | Qt.AlignVCenter
        )

        row1.addWidget(
            lbl_input
        )

        self.in_dir_edit = DropLineEdit()

        saved_in = str(
            config_get(
                cfg,
                "input_dir",
                "",
            )
            or ""
        )

        self.in_dir_edit.setText(
            saved_in
            or os.path.join(
                BASE_DIR,
                "input_videos",
            )
        )

        self.in_dir_edit.setPlaceholderText(
            "拖入文件夹或手动输入路径..."
        )

        self.in_dir_edit.path_dropped.connect(
            self._on_drop_input
        )

        row1.addWidget(
            self.in_dir_edit,
            1,
        )

        browse_in_btn = QPushButton(
            "浏览..."
        )

        browse_in_btn.clicked.connect(
            self._browse_input
        )

        row1.addWidget(
            browse_in_btn
        )

        path_inner.addLayout(
            row1
        )

        # 输出目录
        row2 = QHBoxLayout()

        lbl_output = QLabel(
            "📤 输出文件夹："
        )

        lbl_output.setFixedWidth(
            105
        )

        lbl_output.setAlignment(
            Qt.AlignRight
            | Qt.AlignVCenter
        )

        row2.addWidget(
            lbl_output
        )

        self.out_dir_edit = DropLineEdit()

        saved_out = str(
            config_get(
                cfg,
                "output_dir",
                "",
            )
            or ""
        )

        self.out_dir_edit.setText(
            saved_out
            or os.path.join(
                BASE_DIR,
                "output_videos",
            )
        )

        self.out_dir_edit.setPlaceholderText(
            "拖入文件夹或手动输入路径..."
        )

        self.out_dir_edit.path_dropped.connect(
            self._on_drop_output
        )

        row2.addWidget(
            self.out_dir_edit,
            1,
        )

        browse_out_btn = QPushButton(
            "浏览..."
        )

        browse_out_btn.clicked.connect(
            self._browse_output
        )

        row2.addWidget(
            browse_out_btn
        )

        path_inner.addLayout(
            row2
        )

        root.addWidget(
            path_group
        )

        # ----------------------------------------------------
        # 操作按钮
        # ----------------------------------------------------

        btn_layout = QHBoxLayout()

        btn_layout.setSpacing(12)

        self.btn_start = QPushButton(
            "🚀 一键开始冲洗"
        )

        self.btn_start.setObjectName(
            "startBtn"
        )

        self.btn_start.clicked.connect(
            self._start
        )

        btn_layout.addWidget(
            self.btn_start
        )

        self.btn_stop = QPushButton(
            "⏹ 停止"
        )

        self.btn_stop.setObjectName(
            "stopBtn"
        )

        self.btn_stop.setEnabled(False)

        self.btn_stop.clicked.connect(
            self._stop
        )

        btn_layout.addWidget(
            self.btn_stop
        )

        self.btn_settings = QPushButton(
            "⚙ 设置"
        )

        self.btn_settings.clicked.connect(
            self._open_settings
        )

        btn_layout.addWidget(
            self.btn_settings
        )

        btn_layout.addStretch()

        open_in_btn = QPushButton(
            "📂 打开输入"
        )

        open_in_btn.clicked.connect(
            lambda:
            self._open_dir(
                self.in_dir_edit.text()
            )
        )

        btn_layout.addWidget(
            open_in_btn
        )

        open_out_btn = QPushButton(
            "📂 打开输出"
        )

        open_out_btn.clicked.connect(
            lambda:
            self._open_dir(
                self.out_dir_edit.text()
            )
        )

        btn_layout.addWidget(
            open_out_btn
        )

        root.addLayout(
            btn_layout
        )

        # ----------------------------------------------------
        # 进度条
        # ----------------------------------------------------

        progress_layout = QHBoxLayout()

        prog_lbl = QLabel(
            "进度："
        )

        prog_lbl.setFixedWidth(
            50
        )

        prog_lbl.setAlignment(
            Qt.AlignRight
            | Qt.AlignVCenter
        )

        progress_layout.addWidget(
            prog_lbl
        )

        self.progress = QProgressBar()

        self.progress.setRange(
            0,
            1000,
        )

        self.progress.setFormat(
            "%p%"
        )

        self.progress.setValue(
            0
        )

        progress_layout.addWidget(
            self.progress,
            1,
        )

        root.addLayout(
            progress_layout
        )

        self.eta_label = QLabel("")

        root.addWidget(
            self.eta_label
        )

        # ----------------------------------------------------
        # 日志
        # ----------------------------------------------------

        self.log_box = QPlainTextEdit()

        self.log_box.setReadOnly(
            True
        )

        self.log_box.setMaximumBlockCount(
            2000
        )

        root.addWidget(
            self.log_box,
            1,
        )

        # ----------------------------------------------------
        # windnd 系统级拖放
        # ----------------------------------------------------

        if windnd:

            try:

                windnd.hook_dropfiles(
                    int(
                        self.in_dir_edit.winId()
                    ),
                    func=self._on_native_drop_input,
                )

                windnd.hook_dropfiles(
                    int(
                        self.out_dir_edit.winId()
                    ),
                    func=self._on_native_drop_output,
                )

            except Exception as e:

                print(
                    f"[gui] windnd 拖放 hook 失败: {e}"
                )

        self._update_window_title()

        self._update_preset_label()

    # ========================================================
    # 启动日志
    # ========================================================

    def _log_startup(self):

        self._log(
            "=" * 50
        )

        self._log(
            "  🎦频冲洗工具 v7.1 已启动 🎦"
        )

        self._log(
            f"  输入目录: {self.in_dir_edit.text()}"
        )

        self._log(
            f"  输出目录: {self.out_dir_edit.text()}"
        )

        self._log(
            "=" * 50
        )

        cfg = STORE.get_data()

        self._log(
            f"FFmpeg路径: "
            f"{config_get(cfg, 'runtime.ffmpeg', '')}"
        )

        self._log(
            f"FFprobe路径: "
            f"{config_get(cfg, 'runtime.ffprobe', '')}"
        )

        self._log(
            "🔍 正在后台检测显卡加速能力..."
        )

    # ========================================================
    # GPU 检测结果
    # ========================================================

    def _on_gpu_result(
        self,
        has_nvenc,
        gpu_name,
    ):

        if has_nvenc:

            self.status_label.setText(
                "✅ FFmpeg OK | 🟢 N 卡硬件加速已启用"
            )

            self._log(
                "  ✅ 检测到 NVIDIA 显卡，"
                f"已启用硬件加速（h264_nvenc）: {gpu_name}"
            )

        else:

            self.status_label.setText(
                "✅ FFmpeg OK | 🟡 CPU 编码模式（未检测到 N 卡加速）"
            )

            self._log(
                "  ⚠ 未检测到 NVIDIA 硬件加速，"
                "将自动使用 CPU 编码（libx264）"
            )

            self._log(
                "  ℹ 如果你的电脑是 NVIDIA 显卡，"
                "请检查驱动是否正确安装"
            )

    # ========================================================
    # 拖放
    # ========================================================

    def _on_drop_input(self, path):

        """输入框拖放回调"""

        if path and os.path.isfile(path):
            path = os.path.dirname(path)

        if path and os.path.isdir(path):

            if (
                self.in_dir_edit.text()
                == path
            ):
                return

            self.in_dir_edit.setText(
                path
            )

            self._persist_paths()

            self._log(
                f"📂 输入目录已切换至: {path}"
            )

    def _on_drop_output(self, path):

        """输出框拖放回调"""

        if path and os.path.isfile(path):
            path = os.path.dirname(path)

        if path and os.path.isdir(path):

            if (
                self.out_dir_edit.text()
                == path
            ):
                return

            self.out_dir_edit.setText(
                path
            )

            self._persist_paths()

            self._log(
                f"📂 输出目录已切换至: {path}"
            )

    @staticmethod
    def _decode_drop_files(files):

        """windnd 回调路径解码"""

        for f in files:

            try:

                p = (
                    f.decode(
                        "gbk",
                        errors="ignore",
                    )
                    if isinstance(f, bytes)
                    else str(f)
                )

            except Exception:

                continue

            if p:
                return p

        return None

    def _on_native_drop_input(
        self,
        files,
    ):

        p = self._decode_drop_files(
            files
        )

        if p:
            self._on_drop_input(
                p
            )

    def _on_native_drop_output(
        self,
        files,
    ):

        p = self._decode_drop_files(
            files
        )

        if p:
            self._on_drop_output(
                p
            )

    # ========================================================
    # 浏览目录
    # ========================================================

    def _browse_input(self):

        path = QFileDialog.getExistingDirectory(
            self,
            "选择输入文件夹（放视频的目录）",
            self.in_dir_edit.text(),
        )

        if path:

            self.in_dir_edit.setText(
                path
            )

            self._persist_paths()

            self._log(
                f"📂 输入目录已切换至: {path}"
            )

    def _browse_output(self):

        path = QFileDialog.getExistingDirectory(
            self,
            "选择输出文件夹（洗好的视频放这里）",
            self.out_dir_edit.text(),
        )

        if path:

            self.out_dir_edit.setText(
                path
            )

            self._persist_paths()

            self._log(
                f"📂 输出目录已切换至: {path}"
            )

    def _open_dir(self, d):

        d = (d or "").strip()

        if not d:
            return

        try:

            os.makedirs(
                d,
                exist_ok=True,
            )

            os.startfile(d)

        except Exception as e:

            QMessageBox.warning(
                self,
                "打开失败",
                f"无法打开目录: {e}",
            )

    def _persist_paths(self):

        cfg = STORE.get_data()

        cfg["input_dir"] = (
            self.in_dir_edit.text().strip()
        )

        cfg["output_dir"] = (
            self.out_dir_edit.text().strip()
        )

        STORE.save()

    # ========================================================
    # 设置
    # ========================================================

    def _open_settings(self):

        ver = int(
            config_get(
                STORE.get_data(),
                "version_count",
                1,
            )
            or 1
        )

        dlg = SettingsDialog(
            self._preset_snap,
            self._ui,
            self,
            version_count=ver,
        )

        if not dlg.exec_():
            return

        self._ui.update(
            dlg.get_ui_state()
        )

        ac_lo, ac_hi = (
            dlg.get_asym_crop_range()
        )

        self._ui["ac_min"] = ac_lo

        self._ui["ac_max"] = ac_hi

        cfg = STORE.get_data()

        cfg["version_count"] = (
            dlg.get_version_count()
        )

        # ----------------------------------------------------
        # 保存为自定义预设
        # ----------------------------------------------------

        if dlg.get_action() == "save":

            key = dlg.get_saved_name()

            self._preset_snap = (
                preset_mod.get_preset(
                    key
                )
            )

            self._record_last_used(
                key
            )

            self._log(
                f"✓ 已保存为自定义预设：{key}"
            )

        # ----------------------------------------------------
        # 普通确定
        # ----------------------------------------------------

        else:

            key = dlg.get_selected_key()

            if (
                key
                != self._preset_snap.get("name")
            ):

                self._record_last_used(
                    key
                )

                self._log(
                    f"切换预设："
                    f"{preset_mod.get_preset(key).get('label')}"
                )

            self._preset_snap = (
                preset_mod.get_preset(
                    key
                )
            )

            self._preset_snap["params"] = (
                dlg.get_params()
            )

            self._log(
                f"参数已保存"
                f" | 每素材版本数={dlg.get_version_count()}"
            )

        self._persist_ui_state()

        self._update_window_title()

        self._update_preset_label()

    def _persist_ui_state(self):

        """全局持久化流程开关和标准化规格"""

        cfg = STORE.get_data()

        u = self._ui

        cfg["switches"] = {
            "normalize":
                u["normalize"],

            "quality_check":
                u["quality_check"],
        }

        cfg["segment_count"] = (
            u["segment_count"]
        )

        cfg.setdefault(
            "performance",
            {},
        )["video_concurrency"] = max(
            1,
            min(
                16,
                int(
                    u.get(
                        "video_concurrency",
                        1,
                    )
                    or 1
                ),
            ),
        )

        cfg["normalize"] = {

            "aspect_ratio":
                u["aspect_ratio"],

            "width":
                u["width"],

            "height":
                u["height"],

            "fps":
                u["fps"],

            "pix_fmt":
                u["pix_fmt"],

            "video_codec":
                u["video_codec"],

            "audio_codec":
                "aac",

            "bitrate_kbps":
                u.get(
                    "bitrate_kbps",
                    0,
                ),
        }

        # ----------------------------------------------------
        # 全局项：指纹检测（fingerprint 节点，合并写入）
        # ----------------------------------------------------

        fp = cfg.setdefault("fingerprint", {})

        fp["enable"] = bool(u.get("fp_enable", True))

        fp["max_similarity"] = float(
            u.get("fp_threshold", 0.70)
        )

        fp["sample_frames"] = int(
            u.get("fp_frames", 10) or 10
        )

        fp["retry_max"] = max(
            0,
            int(u.get("fp_retry", 3) or 0),
        )

        # ----------------------------------------------------
        # 全局项：NVENC 编码档位（encode.nvenc_preset）
        # ----------------------------------------------------

        cfg.setdefault(
            "encode",
            {},
        )["nvenc_preset"] = str(
            u.get("nvenc_preset", "p3")
        )

        # ----------------------------------------------------
        # 局部项：配置级视觉扰动（video.* 合并写入，
        # 不影响 noise/mask_drift/black_crop 等其他节点）
        # ----------------------------------------------------

        vid = cfg.setdefault("video", {})

        ld = vid.setdefault("lens_distortion", {})
        ld["enable"] = True  # GUI 在调即启用；关闭 = k1/k2 置 0（下游自动跳过）
        ld["k1_range"] = float(u.get("ld_k1", 0.02))
        ld["k2_range"] = float(u.get("ld_k2", 0.005))
        ld["probability"] = float(u.get("ldw_prob", 0.6))
        ld.setdefault("duration", {})["min"] = float(u.get("ldw_dur_min", 1.5))
        ld["duration"]["max"] = float(u.get("ldw_dur_max", 4.0))
        ld.setdefault("count", {})["min"] = int(u.get("ldw_count_min", 1) or 1)
        ld["count"]["max"] = int(u.get("ldw_count_max", 2) or 2)

        rd = vid.setdefault("rotate_drift", {})
        rd["probability"] = float(u.get("rdw_prob", 0.8))
        rd.setdefault("duration", {})["min"] = float(u.get("rdw_dur_min", 3.0))
        rd["duration"]["max"] = float(u.get("rdw_dur_max", 8.0))

        zd = vid.setdefault("zoom_drift", {})
        zd["probability"] = float(u.get("zdw_prob", 0.8))
        zd.setdefault("duration", {})["min"] = float(u.get("zdw_dur_min", 3.0))
        zd["duration"]["max"] = float(u.get("zdw_dur_max", 8.0))

        ac = vid.setdefault("asymmetric_crop", {})
        ac["enable"] = True  # 同上；0~0 时采样值全 0，下游自然不裁剪
        ac["min"] = float(u.get("ac_min", 0.03))
        ac["max"] = float(u.get("ac_max", 0.05))

        rl = vid.setdefault("reverse_loop", {})
        rl["enable"] = bool(u.get("rl_enable", True))
        rl["probability"] = float(u.get("rl_prob", 0.4))
        rl.setdefault("event_length", {})["min"] = float(u.get("rl_len_min", 0.1))
        rl["event_length"]["max"] = float(u.get("rl_len_max", 0.2))

        fd = vid.setdefault("frame_drop", {})
        fd["enable"] = bool(u.get("fd_enable", True))
        fd.setdefault("interval", {})["min"] = int(
            u.get("fd_interval_min", 100) or 100
        )
        fd["interval"]["max"] = int(
            u.get("fd_interval_max", 200) or 200
        )
        fd["probability"] = float(u.get("fdw_prob", 1.0))
        fd.setdefault("duration", {})["min"] = float(u.get("fdw_dur_min", 2.0))
        fd["duration"]["max"] = float(u.get("fdw_dur_max", 5.0))

        STORE.save()

    def _record_last_used(
        self,
        key,
    ):

        cfg = STORE.get_data()

        cfg["last_used_preset"] = key

        STORE.save()

    def _update_window_title(self):

        """更新窗口标题"""

        self.setWindowTitle(
            "🎬 视频冲洗工具 v7.1 🎬"
        )

    def _update_preset_label(self):

        """状态条右侧显示当前预设"""

        snap = self._preset_snap or {}

        name = (
            snap.get("label")
            or snap.get("name")
            or "标准"
        )

        self.preset_label.setText(
            f" ✨  {name}  ✨"
        )

    # ========================================================
    # 收集输入文件
    # ========================================================

    def _collect_inputs(self):

        """递归收集输入目录中的所有视频"""

        in_dir = (
            self.in_dir_edit
            .text()
            .strip()
        )

        inputs = []

        if in_dir and os.path.isdir(
            in_dir
        ):

            for r, _dirs, files in os.walk(
                in_dir
            ):

                for f in sorted(files):

                    if (
                        os.path.splitext(
                            f
                        )[1].lower()
                        in VIDEO_EXTS
                    ):

                        inputs.append(
                            os.path.join(
                                r,
                                f,
                            )
                        )

        return inputs

    # ========================================================
    # 收集配置快照
    # ========================================================

    def _collect_config(self) -> dict:

        """收集界面状态为配置快照"""

        cfg = copy.deepcopy(
            STORE.snapshot()
        )

        u = self._ui

        cfg["switches"] = {

            "normalize":
                u["normalize"],

            "quality_check":
                u["quality_check"],
        }

        cfg["segment_count"] = (
            u["segment_count"]
        )

        cfg.setdefault(
            "performance",
            {},
        )["video_concurrency"] = max(
            1,
            min(
                16,
                int(
                    u.get(
                        "video_concurrency",
                        1,
                    )
                    or 1
                ),
            ),
        )

        cfg["normalize"] = {

            "aspect_ratio":
                u["aspect_ratio"],

            "width":
                u["width"],

            "height":
                u["height"],

            "fps":
                u["fps"],

            "pix_fmt":
                u["pix_fmt"],

            "video_codec":
                u["video_codec"],

            "audio_codec":
                "aac",

            "bitrate_kbps":
                u.get(
                    "bitrate_kbps",
                    0,
                ),
        }

        return cfg

    # ========================================================
    # 开始处理
    # ========================================================

    def _start(self):

        if (
            self._thread
            and self._thread.isRunning()
        ):
            return

        # ----------------------------------------------------
        # 检查输入目录
        # ----------------------------------------------------

        in_dir = (
            self.in_dir_edit
            .text()
            .strip()
        )

        if (
            not in_dir
            or not os.path.isdir(in_dir)
        ):

            QMessageBox.information(
                self,
                "提示",
                "请先选择有效的输入文件夹（可拖入文件夹）",
            )

            return

        # ----------------------------------------------------
        # 收集视频
        # ----------------------------------------------------

        inputs = self._collect_inputs()

        if not inputs:

            QMessageBox.information(
                self,
                "提示",
                "输入文件夹中没有找到视频文件",
            )

            return

        # ----------------------------------------------------
        # 输出目录
        # ----------------------------------------------------

        out_dir = (
            self.out_dir_edit
            .text()
            .strip()
            or os.path.join(
                BASE_DIR,
                "output_videos",
            )
        )

        self._persist_paths()

        # ----------------------------------------------------
        # 配置快照
        # ----------------------------------------------------

        cfg = self._collect_config()

        snap = copy.deepcopy(
            self._preset_snap
        )

        ver = int(
            config_get(
                STORE.get_data(),
                "version_count",
                1,
            )
            or 1
        )

        # ----------------------------------------------------
        # 初始化进度
        # ----------------------------------------------------

        self._file_frac = {}

        self._last_done = 0

        self._last_total = len(
            inputs
        )

        self.progress.setValue(
            0
        )

        self.eta_label.setText(
            ""
        )

        self.btn_start.setEnabled(
            False
        )

        self.btn_stop.setEnabled(
            True
        )

        # ----------------------------------------------------
        # 日志
        # ----------------------------------------------------

        self._log(
            f"📂 输入目录共找到 "
            f"{len(inputs)} 个视频文件"
        )

        self._log(
            f"═══ 开始处理 "
            f"{len(inputs)} 个文件 × "
            f"{ver} 版本 ═══"
        )

        # ----------------------------------------------------
        # 启动任务
        # ----------------------------------------------------

        STOP_EVENT.clear()

        self._thread = ProcessThread(
            inputs,
            out_dir,
            cfg,
            snap,
            self,
        )

        self._thread.sig_log.connect(
            self._log
        )

        self._thread.sig_progress.connect(
            self._on_progress
        )

        self._thread.sig_file_progress.connect(
            self._on_file_progress
        )

        self._thread.sig_finished.connect(
            self._on_finished
        )

        self._thread.start()

    # ========================================================
    # 停止
    # ========================================================

    def _stop(self):

        self._log(
            "⏹ 正在停止（等待运行中的 FFmpeg 终止）…"
        )

        STOP_EVENT.set()

    # ========================================================
    # 更新进度
    # ========================================================

    def _update_bar(self):

        """进度条 = 已完成 + 当前文件小数进度"""

        total = max(
            1,
            self._last_total,
        )

        active = sum(
            f
            for f in self._file_frac.values()
            if f < 1.0
        )

        val = int(
            (
                self._last_done
                + active
            )
            / total
            * 1000
        )

        self.progress.setValue(
            min(
                1000,
                val,
            )
        )

    def _on_file_progress(
        self,
        path,
        frac,
    ):

        if frac >= 1.0:

            self._file_frac.pop(
                path,
                None,
            )

        else:

            self._file_frac[path] = (
                frac
            )

        self._update_bar()

    def _on_progress(
        self,
        done,
        total,
        eta_s,
        cur,
    ):

        self._last_done = done

        self._last_total = max(
            1,
            total,
        )

        self._update_bar()

        if eta_s > 60:

            eta_str = (
                f"{int(eta_s // 60)}分"
                f"{int(eta_s % 60)}秒"
            )

        else:

            eta_str = (
                f"{int(eta_s)}秒"
            )

        self.eta_label.setText(
            f"进度 {done}/{total}"
            f" | 预计剩余 {eta_str}"
            f" | 最近完成: {cur}"
        )

    # ========================================================
    # 处理完成
    # ========================================================

    def _on_finished(
        self,
        result,
    ):

        self.btn_start.setEnabled(
            True
        )

        self.btn_stop.setEnabled(
            False
        )

        self._file_frac = {}

        if "total" in result:

            self._log(
                f"═══ 完成：成功 "
                f"{result.get('done', 0)}/"
                f"{result.get('total', 0)}，"
                f"失败 "
                f"{result.get('failed', 0)}，"
                f"耗时 "
                f"{result.get('elapsed', 0)}s ═══"
            )

            if result.get(
                "failed_items"
            ):

                self._log(
                    "失败文件："
                )

                for k, v in result[
                    "failed_items"
                ].items():

                    self._log(
                        f"  ✗ "
                        f"{os.path.basename(k)}: "
                        f"{v.get('reason', '')[:120]}"
                    )

        else:

            self._log(
                "✗ 处理失败: "
                + str(
                    result.get(
                        "error",
                        "",
                    )
                )
            )

        self.progress.setValue(
            1000
            if result.get(
                "failed",
                0,
            ) == 0
            else self.progress.value()
        )

    # ========================================================
    # 日志
    # ========================================================

    def _log(
        self,
        msg,
    ):

        self.log_box.appendPlainText(
            f"[{time.strftime('%H:%M:%S')}] {msg}"
        )

    # ========================================================
    # 关闭窗口
    # ========================================================

    def closeEvent(
        self,
        event,
    ):

        if (
            self._thread
            and self._thread.isRunning()
        ):

            r = QMessageBox.question(
                self,
                "退出确认",
                "处理仍在进行，确定退出？",
                QMessageBox.Yes
                | QMessageBox.No,
            )

            if r == QMessageBox.No:

                event.ignore()

                return

            STOP_EVENT.set()

            self._thread.wait(
                5000
            )

        # ----------------------------------------------------
        # 保存全部界面状态
        # ----------------------------------------------------

        self._persist_ui_state()

        cfg = STORE.get_data()

        cfg["input_dir"] = (
            self.in_dir_edit
            .text()
            .strip()
        )

        cfg["output_dir"] = (
            self.out_dir_edit
            .text()
            .strip()
        )

        STORE.save()

        event.accept()