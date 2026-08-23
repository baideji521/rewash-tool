# -*- coding: utf-8 -*-
"""gui.settings_dialog — 设置对话框（v7.2 参数分类重整）

页1 去重参数：
    局部项 · 视觉扰动（影响单个视频片段的画面变化）：微旋/推镜/
    镜头畸变/非对称裁剪/亮度/对比度/饱和度/色相/倒放循环/周期抽帧
    其他扰动参数（时序/音频/编码）：缩放/裁剪/变速/重复帧/音频微调/
    CRF/GOP（噪点与通道混合为性能减法项，不进 GUI）

页2 高级选项：
    全局项（影响整个处理任务）：视频并发/版本数/分段数/指纹检测/
    NVENC 编码档位/质量检测 + 输出标准化开关与规格（比例/分辨率/
    帧率/编码/码率）

页3 预设管理：预设选择 + 重命名 / 删除 / 导出为 JSON / 从 JSON 导入

按钮语义：
- 确定：预设 + 全部设置应用到主界面（预设参数为临时微调）
- 保存为自定义：当前参数存为自定义预设（主界面下次打开可直接选择）
- 恢复当前预设：丢弃参数微调，重新载入当前所选预设的原始参数

统一规则：视觉扰动参数 min = max = 0 → 自动关闭该扰动（复用
randomizer 现有判断逻辑，不另起一套）。
"""
import os
import json
import copy

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QDoubleSpinBox, QSpinBox, QPushButton, QScrollArea, QWidget,
    QGroupBox, QCheckBox, QInputDialog, QMessageBox, QTabWidget, QComboBox,
    QListWidget, QListWidgetItem, QFileDialog, QAbstractItemView
)

from ..core import preset as preset_mod
from ..video.filters import ENCODER_TABLE
from .styles import QSS

# 局部项 · 视觉扰动中的色彩参数（预设 params，min/max 对称幅度）
VISUAL_COLOR_DEFS = [
    ("brightness", "亮度(%)", 2, True),
    ("contrast", "对比度(%)", 2, True),
    ("saturation", "饱和度(%)", 2, True),
    ("hue", "色相(°)", 2, True),
]

# 其他扰动参数（时序/音频/编码，预设 params；噪点/通道混合不进 GUI）
OTHER_DEFS = [
    ("scale", "缩放", 4, False),
    ("trim", "首尾裁剪(s)", 3, False),
    ("speed", "变速", 4, False),
    ("frame_dup", "重复帧(帧)", 0, False),
    ("audio_speed", "音频微变速", 4, False),
    ("audio_pitch", "音频变调(半音)", 3, True),
    ("audio_eq", "音频EQ(dB)", 2, True),
    ("av_offset", "音画偏移(s)", 3, True),
    ("crf", "CRF/CQ", 0, False),
    ("gop", "GOP", 0, False),
]

# 比例 → 常用分辨率列表（选比例后二级联动选分辨率）
RESOLUTION_PRESETS = {
    "1:1": [(480, 480), (512, 512), (720, 720), (800, 800), (1080, 1080),
            (1440, 1440), (2160, 2160)],
    "4:5": [(480, 600), (600, 750), (720, 900), (800, 1000), (1080, 1350),
            (1440, 1800), (2160, 2700)],
    "3:4": [(480, 640), (540, 720), (600, 800), (720, 960), (810, 1080),
            (900, 1200), (1080, 1440), (1200, 1600), (1440, 1920),
            (1620, 2160), (2160, 2880)],
    "2:3": [(480, 720), (600, 900), (720, 1080), (800, 1200), (900, 1350),
            (1080, 1620), (1200, 1800), (1440, 2160)],
    "9:16": [(360, 640), (405, 720), (480, 854), (540, 960), (576, 1024),
             (608, 1080), (640, 1136), (720, 1280), (810, 1440),
             (900, 1600), (1080, 1920), (1152, 2048), (1440, 2560),
             (1620, 2880), (2160, 3840)],
    "16:9": [(640, 360), (854, 480), (960, 540), (1024, 576), (1280, 720),
             (1366, 768), (1600, 900), (1920, 1080), (2560, 1440),
             (3840, 2160), (4096, 2304), (7680, 4320)],
    "3:2": [(480, 320), (600, 400), (720, 480), (900, 600), (1080, 720),
            (1200, 800), (1440, 960), (1800, 1200), (2160, 1440),
            (3000, 2000), (3600, 2400)],
    "5:4": [(500, 400), (640, 512), (800, 640), (1000, 800), (1280, 1024),
            (1600, 1280), (2000, 1600)],
    "4:3": [(640, 480), (800, 600), (960, 720), (1024, 768), (1280, 960),
            (1440, 1080), (1600, 1200), (1920, 1440), (2048, 1536),
            (2560, 1920), (2880, 2160)],
    "21:9": [(2560, 1080), (3440, 1440), (3840, 1600), (5120, 2160)],
}


class SettingsDialog(QDialog):
    """设置 = 全局项 + 视觉扰动微调 + 输出标准化规格"""

    def __init__(self, preset_snap: dict, ui_state: dict, parent=None,
                 version_count: int = 1):
        super().__init__(parent)
        self.setWindowTitle("⚙ 设置")
        self.resize(640, 680)
        self.setStyleSheet(QSS)
        self._preset = copy.deepcopy(preset_snap)
        self._ui_in = copy.deepcopy(ui_state or {})
        self._boxes = {}
        self._version_count = int(version_count or 1)
        self._action = "apply"        # apply=确定 / save=保存为自定义
        self._saved_name = ""
        self._build_ui()

    # ─────────────────────────── UI 构建 ───────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_tab_params(), "去重参数")
        tabs.addTab(self._build_tab_advanced(), "高级选项")
        tabs.addTab(self._build_tab_manage(), "预设管理")
        root.addWidget(tabs, 1)

        btns = QHBoxLayout()
        hint = QLabel("提示：视觉扰动 min=max=0 自动关闭；确定=应用，保存=存为自定义预设")
        hint.setStyleSheet("color:#888;font-size:11px")
        btns.addWidget(hint)
        btns.addStretch()
        restore_btn = QPushButton("恢复当前预设")
        restore_btn.clicked.connect(self._restore_current)
        btns.addWidget(restore_btn)
        save_btn = QPushButton("保存为自定义")
        save_btn.clicked.connect(self._save_as_custom)
        btns.addWidget(save_btn)
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        ok_btn.clicked.connect(self._on_ok)
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        root.addLayout(btns)

    def _build_tab_params(self) -> QWidget:
        """去重参数页：仅扰动参数（预设切换在「预设管理」页）"""
        tab = QWidget()
        outer = QVBoxLayout(tab)

        # 参数区（可滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._host = QWidget()
        self._host_lay = QVBoxLayout(self._host)
        self._build_visual_box()
        self._build_other_box()
        self._host_lay.addStretch()
        scroll.setWidget(self._host)
        outer.addWidget(scroll, 1)
        return tab

    # ── 全局项（影响整个处理任务，位于高级选项页）──
    def _build_global_box(self):
        u = self._ui_in
        box = QGroupBox("全局项（影响整个处理任务）")
        g = QGridLayout(box)

        g.addWidget(QLabel("视频并发:"), 0, 0)
        self.conc_combo = QComboBox()
        self.conc_combo.addItems(["1", "2", "3", "4"])
        cur_conc = max(1, min(4, int(u.get("video_concurrency", 1) or 1)))
        self.conc_combo.setCurrentText(str(cur_conc))
        g.addWidget(self.conc_combo, 0, 1)
        conc_tip = QLabel("(默认 1 顺序处理；不同 CPU/GPU 最佳并发不同，按需上调)")
        conc_tip.setStyleSheet("color:#888;font-size:11px")
        g.addWidget(conc_tip, 0, 2, 1, 2)

        g.addWidget(QLabel("版本数:"), 1, 0)
        self._ver_spin = QSpinBox()
        self._ver_spin.setRange(1, 10)
        self._ver_spin.setValue(max(1, min(10, self._version_count)))
        g.addWidget(self._ver_spin, 1, 1)
        g.addWidget(QLabel("分段数:"), 1, 2)
        self.seg_spin = QSpinBox()
        self.seg_spin.setRange(1, 20)
        self.seg_spin.setValue(int(u.get("segment_count", 4) or 4))
        g.addWidget(self.seg_spin, 1, 3)

        self.fp_chk = QCheckBox("启用指纹检测（相似度对比，失败自动重试）")
        self.fp_chk.setChecked(bool(u.get("fp_enable", True)))
        g.addWidget(self.fp_chk, 2, 0, 1, 4)
        g.addWidget(QLabel("    最大相似度:"), 3, 0)
        self.fp_thresh_spin = QDoubleSpinBox()
        self.fp_thresh_spin.setRange(0.30, 0.99)
        self.fp_thresh_spin.setDecimals(2)
        self.fp_thresh_spin.setSingleStep(0.01)
        self.fp_thresh_spin.setValue(float(u.get("fp_threshold", 0.70)))
        g.addWidget(self.fp_thresh_spin, 3, 1)
        g.addWidget(QLabel("    采样帧数:"), 4, 0)
        self.fp_frames_spin = QSpinBox()
        self.fp_frames_spin.setRange(2, 30)
        self.fp_frames_spin.setValue(int(u.get("fp_frames", 10) or 10))
        g.addWidget(self.fp_frames_spin, 4, 1)
        g.addWidget(QLabel("    最大重试次数:"), 5, 0)
        self.fp_retry_spin = QSpinBox()
        self.fp_retry_spin.setRange(0, 10)
        self.fp_retry_spin.setValue(int(u.get("fp_retry", 3) or 0))
        g.addWidget(self.fp_retry_spin, 5, 1)
        fp_tip = QLabel("(阈值越低越严格；关闭后跳过指纹与重试)")
        fp_tip.setStyleSheet("color:#888;font-size:11px")
        g.addWidget(fp_tip, 5, 2, 1, 2)
        self.fp_chk.toggled.connect(self._on_fp_toggled)
        self._on_fp_toggled(self.fp_chk.isChecked())

        g.addWidget(QLabel("NVENC 编码档位:"), 6, 0)
        self.nvenc_combo = QComboBox()
        self.nvenc_combo.addItems(["p1", "p2", "p3", "p4", "p5", "p6", "p7"])
        cur_nv = str(u.get("nvenc_preset", "p3"))
        idx = self.nvenc_combo.findText(cur_nv)
        self.nvenc_combo.setCurrentIndex(idx if idx >= 0 else 2)
        g.addWidget(self.nvenc_combo, 6, 1)
        nv_tip = QLabel("(p1 最快体积大 ~ p7 最慢体积小，仅 NVENC 编码器生效)")
        nv_tip.setStyleSheet("color:#888;font-size:11px")
        g.addWidget(nv_tip, 6, 2, 1, 2)

        self.chk_qc = QCheckBox("质量检测（推荐开启）")
        self.chk_qc.setChecked(bool(u.get("quality_check", True)))
        g.addWidget(self.chk_qc, 7, 0, 1, 4)

        g.setColumnStretch(1, 0)
        g.setColumnStretch(3, 1)
        return box

    def _on_fp_toggled(self, on: bool):
        for w in (self.fp_thresh_spin, self.fp_frames_spin, self.fp_retry_spin):
            w.setEnabled(on)

    # ── 局部项 · 视觉扰动（影响单个视频片段的画面变化）──
    def _build_visual_box(self):
        u = self._ui_in
        box = QGroupBox("局部项 · 视觉扰动（影响单个视频片段的画面变化）")
        grid = QGridLayout(box)
        grid.addWidget(QLabel("参数"), 0, 0)
        grid.addWidget(QLabel("最小"), 0, 1)
        grid.addWidget(QLabel("最大"), 0, 2)
        row = 1

        params = self._preset.get("params", {})

        def _range_row(label, key_lo, key_hi, lo_val, hi_val, dec=3, step=0.01):
            nonlocal row
            grid.addWidget(QLabel(label), row, 0)
            lo_spin, hi_spin = QDoubleSpinBox(), QDoubleSpinBox()
            for spin in (lo_spin, hi_spin):
                spin.setRange(-1000, 1000)
                spin.setDecimals(dec)
                spin.setSingleStep(step)
            lo_spin.setValue(float(lo_val))
            hi_spin.setValue(float(hi_val))
            grid.addWidget(lo_spin, row, 1)
            grid.addWidget(hi_spin, row, 2)
            self._boxes[key_lo] = (lo_spin, hi_spin)
            row += 1

        # 微旋（正弦摆动 + 单向恒速漂移）：幅度/速度/周期 均为 0~0 → 对应分量关闭
        rd_node = params.get("rotate_drift") or {}
        if isinstance(rd_node, dict):
            _range_row("微旋幅度(°) ±", "rotate_drift", None,
                       rd_node.get("amp_min", 0.3), rd_node.get("amp_max", 0.8))
            _range_row("微旋速度(°/s) ±", "rotate_drift_speed", None,
                       rd_node.get("speed_min", 0.02), rd_node.get("speed_max", 0.08))
            _range_row("微旋周期(s)", "rotate_drift_period", None,
                       rd_node.get("period_min", 3.0), rd_node.get("period_max", 6.0))
        # 微旋事件窗口（分段级：概率 + 窗口时长；时长 0~0 → 关闭窗口化）
        self._rdw_prob, self._rdw_lo, self._rdw_hi = self._event_row(
            grid, row, "微旋窗口(概率/时长s)", u, "rdw", 0.8, 3.0, 8.0)
        row += 1

        # 推镜（zoompan 渐变）
        zd_node = params.get("zoom_drift") or {}
        if isinstance(zd_node, dict):
            _range_row("推镜幅度 ±", "zoom_drift", None,
                       zd_node.get("amp_min", 0.01), zd_node.get("amp_max", 0.03))
        # 推镜事件窗口（概率 + 窗口时长；前后留白用配置默认 1~4s）
        self._zdw_prob, self._zdw_lo, self._zdw_hi = self._event_row(
            grid, row, "推镜窗口(概率/时长s)", u, "zdw", 0.8, 3.0, 8.0)
        row += 1

        # 镜头畸变（lenscorrection；配置 video.lens_distortion，单值 0=关闭）
        grid.addWidget(QLabel("镜头畸变 k1 ±"), row, 0)
        self._ld_k1_spin = QDoubleSpinBox()
        self._ld_k1_spin.setRange(0, 1)
        self._ld_k1_spin.setDecimals(4)
        self._ld_k1_spin.setSingleStep(0.005)
        self._ld_k1_spin.setValue(float(u.get("ld_k1", 0.02)))
        grid.addWidget(self._ld_k1_spin, row, 1)
        grid.addWidget(QLabel("k2 ±"), row, 2)
        self._ld_k2_spin = QDoubleSpinBox()
        self._ld_k2_spin.setRange(0, 1)
        self._ld_k2_spin.setDecimals(4)
        self._ld_k2_spin.setSingleStep(0.001)
        self._ld_k2_spin.setValue(float(u.get("ld_k2", 0.005)))
        grid.addWidget(self._ld_k2_spin, row, 3)
        row += 1
        # 畸变事件窗口（全片级：k1/k2 只随机一次，窗口内出现）
        self._ldw_prob, self._ldw_lo, self._ldw_hi = self._event_row(
            grid, row, "畸变窗口(概率/时长s)", u, "ldw", 0.6, 1.5, 4.0)
        row += 1
        grid.addWidget(QLabel("畸变窗口次数"), row, 0)
        self._ldw_clo, self._ldw_chi = QSpinBox(), QSpinBox()
        for spin in (self._ldw_clo, self._ldw_chi):
            spin.setRange(0, 10)
            spin.setSingleStep(1)
        self._ldw_clo.setValue(int(u.get("ldw_count_min", 1) or 1))
        self._ldw_chi.setValue(int(u.get("ldw_count_max", 2) or 2))
        grid.addWidget(self._ldw_clo, row, 1)
        grid.addWidget(self._ldw_chi, row, 2)
        row += 1

        # 非对称裁剪（配置 video.asymmetric_crop，0~0 → 关闭）
        _range_row("非对称裁剪 ±", "asym_crop", None,
                   u.get("ac_min", 0.03), u.get("ac_max", 0.05), dec=4, step=0.01)

        # 色彩（预设 params，min/max 对称幅度，0~0 → 关闭）
        for key, label, dec, signed in VISUAL_COLOR_DEFS:
            node = params.get(key) or {}
            if not isinstance(node, dict):
                continue
            _range_row(label + (" ±" if signed else ""), key, None,
                       node.get("min", 0), node.get("max", 0), dec=dec, step=0.5)

        # 倒放/循环（配置 video.reverse_loop：复选框 + 概率）
        self._rl_chk = QCheckBox("启用倒放/循环（极短片段倒放或循环）")
        self._rl_chk.setChecked(bool(u.get("rl_enable", True)))
        grid.addWidget(self._rl_chk, row, 0, 1, 2)
        grid.addWidget(QLabel("概率:"), row, 2)
        self._rl_prob = QDoubleSpinBox()
        self._rl_prob.setRange(0, 1)
        self._rl_prob.setDecimals(2)
        self._rl_prob.setSingleStep(0.05)
        self._rl_prob.setValue(float(u.get("rl_prob", 0.4)))
        grid.addWidget(self._rl_prob, row, 3)
        row += 1
        # 倒放/循环片段时长（段内事件长度，0~0 → 用默认）
        grid.addWidget(QLabel("片段时长(s)"), row, 0)
        self._rl_len_lo, self._rl_len_hi = QDoubleSpinBox(), QDoubleSpinBox()
        for spin in (self._rl_len_lo, self._rl_len_hi):
            spin.setRange(0, 5)
            spin.setDecimals(2)
            spin.setSingleStep(0.05)
        self._rl_len_lo.setValue(float(u.get("rl_len_min", 0.1)))
        self._rl_len_hi.setValue(float(u.get("rl_len_max", 0.2)))
        grid.addWidget(self._rl_len_lo, row, 1)
        grid.addWidget(self._rl_len_hi, row, 2)
        row += 1

        # 周期抽帧（配置 video.frame_drop：复选框 + 间隔）
        self._fd_chk = QCheckBox("启用周期抽帧（每 N 帧随机删 1 帧）")
        self._fd_chk.setChecked(bool(u.get("fd_enable", True)))
        grid.addWidget(self._fd_chk, row, 0, 1, 1)
        grid.addWidget(QLabel("间隔(帧):"), row, 1)
        self._fd_lo = QSpinBox()
        self._fd_hi = QSpinBox()
        for spin in (self._fd_lo, self._fd_hi):
            spin.setRange(2, 10000)
            spin.setSingleStep(10)
        self._fd_lo.setValue(int(u.get("fd_interval_min", 100) or 100))
        self._fd_hi.setValue(int(u.get("fd_interval_max", 200) or 200))
        grid.addWidget(self._fd_lo, row, 2)
        grid.addWidget(self._fd_hi, row, 3)
        row += 1
        # 抽帧事件窗口（只在窗口内抽帧；概率 + 窗口时长）
        self._fdw_prob, self._fdw_lo, self._fdw_hi = self._event_row(
            grid, row, "抽帧窗口(概率/时长s)", u, "fdw", 1.0, 2.0, 5.0)
        row += 1

        grid.setColumnStretch(0, 1)
        self._host_lay.addWidget(box)

    def _event_row(self, grid, row, label, u, prefix, d_prob, d_lo, d_hi):
        """事件窗口通用行：标签@0 + 概率@1 + 窗口时长 min/max@2/3。
        概率≤0 或时长 0~0 → 该事件关闭（与处理层规则一致）。"""
        grid.addWidget(QLabel(label), row, 0)
        prob = QDoubleSpinBox()
        prob.setRange(0, 1)
        prob.setDecimals(2)
        prob.setSingleStep(0.05)
        prob.setValue(float(u.get(prefix + "_prob", d_prob)))
        grid.addWidget(prob, row, 1)
        lo_spin, hi_spin = QDoubleSpinBox(), QDoubleSpinBox()
        for spin in (lo_spin, hi_spin):
            spin.setRange(0, 600)
            spin.setDecimals(1)
            spin.setSingleStep(0.5)
        lo_spin.setValue(float(u.get(prefix + "_dur_min", d_lo)))
        hi_spin.setValue(float(u.get(prefix + "_dur_max", d_hi)))
        grid.addWidget(lo_spin, row, 2)
        grid.addWidget(hi_spin, row, 3)
        return prob, lo_spin, hi_spin

    # ── 其他扰动参数（时序/音频/编码）──
    def _build_other_box(self):
        box = QGroupBox("其他扰动参数（时序/音频/编码）")
        grid = QGridLayout(box)
        grid.addWidget(QLabel("参数"), 0, 0)
        grid.addWidget(QLabel("最小"), 0, 1)
        grid.addWidget(QLabel("最大"), 0, 2)
        row = 1
        params = self._preset.get("params", {})
        for key, label, dec, signed in OTHER_DEFS:
            node = params.get(key) or {}
            if not isinstance(node, dict):
                continue
            grid.addWidget(QLabel(label + (" ±" if signed else "")), row, 0)
            lo_spin, hi_spin = QDoubleSpinBox(), QDoubleSpinBox()
            for spin in (lo_spin, hi_spin):
                spin.setRange(-100000, 100000)
                spin.setDecimals(dec)
                if dec == 0:
                    spin.setSingleStep(1)
            try:
                lo_spin.setValue(float(node.get("min", 0)))
                hi_spin.setValue(float(node.get("max", 0)))
            except (TypeError, ValueError):
                pass
            grid.addWidget(lo_spin, row, 1)
            grid.addWidget(hi_spin, row, 2)
            self._boxes[key] = (lo_spin, hi_spin)
            row += 1
            if key == "av_offset":
                self._av_chk = QCheckBox("启用音画偏移（观感风险项，输出过质检）")
                self._av_chk.setChecked(bool(node.get("enable", False)))
                grid.addWidget(self._av_chk, row, 0, 1, 3)
                row += 1
        grid.setColumnStretch(0, 1)
        self._host_lay.addWidget(box)

    def _rebuild_param_area(self):
        """清空扰动分组框后重建（切换/恢复预设时，局部项/其他项随预设刷新）。
        配置级扰动不随预设变化：先把当前值固化回 _ui_in，
        重建时自然保留用户已编辑的内容；全局项在高级选项页，不受影响。"""
        self._ui_in.update(self.get_ui_state())
        while self._host_lay.count() > 0:
            item = self._host_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._build_visual_box()
        self._build_other_box()
        self._host_lay.addStretch()

    def _build_tab_advanced(self) -> QWidget:
        """高级选项页：全局项 + 输出标准化规格"""
        tab = QWidget()
        lay = QVBoxLayout(tab)
        u = self._ui_in

        lay.addWidget(self._build_global_box())
        norm_box = QGroupBox("输出标准化规格")
        nl = QGridLayout(norm_box)
        self.chk_norm = QCheckBox("输出标准化")
        self.chk_norm.setChecked(bool(u.get("normalize", True)))
        nl.addWidget(self.chk_norm, 0, 0, 1, 6)
        nl.addWidget(QLabel("比例:"), 1, 0)
        self.aspect_combo = QComboBox()
        self.aspect_combo.addItems(list(RESOLUTION_PRESETS.keys()) + ["原始比例"])
        cur_ar = str(u.get("aspect_ratio", "3:4"))
        idx = self.aspect_combo.findText(cur_ar)
        self.aspect_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.aspect_combo.currentTextChanged.connect(self._on_aspect_changed)
        nl.addWidget(self.aspect_combo, 1, 1)
        nl.addWidget(QLabel("分辨率:"), 1, 2)
        self.res_combo = QComboBox()
        self._refresh_res_combo(int(u.get("width", 1080)), int(u.get("height", 1440)))
        nl.addWidget(self.res_combo, 1, 3, 1, 3)
        nl.addWidget(QLabel("fps:"), 2, 0)
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["24", "25", "30", "50", "60"])
        self.fps_combo.setCurrentText(str(u.get("fps", 30)))
        nl.addWidget(self.fps_combo, 2, 1)
        nl.addWidget(QLabel("像素格式:"), 2, 2)
        self.pix_combo = QComboBox()
        self.pix_combo.addItems(["yuv420p", "yuv444p", "yuv422p"])
        self.pix_combo.setCurrentText(str(u.get("pix_fmt", "yuv420p")))
        nl.addWidget(self.pix_combo, 2, 3, 1, 3)
        nl.addWidget(QLabel("编码:"), 3, 0)
        self.vcodec_combo = QComboBox()
        cur_enc = str(u.get("video_codec", "h264_nvenc"))
        for enc_key, (label, _ff) in ENCODER_TABLE.items():
            self.vcodec_combo.addItem(label, enc_key)
        enc_idx = self.vcodec_combo.findData(cur_enc)
        if enc_idx < 0:  # 旧配置 h264/h265 兼容
            enc_idx = self.vcodec_combo.findData(
                "h265_nvenc" if cur_enc == "h265" else "h264_nvenc")
        self.vcodec_combo.setCurrentIndex(max(0, enc_idx))
        nl.addWidget(self.vcodec_combo, 3, 1, 1, 3)
        nl.addWidget(QLabel("画质/码率(kbps):"), 4, 0)
        self.br_spin = QSpinBox()
        self.br_spin.setRange(0, 50000)
        self.br_spin.setSpecialValueText("自动")  # 0 显示文案
        self.br_spin.setValue(int(u.get("bitrate_kbps", 0) or 0))
        nl.addWidget(self.br_spin, 4, 1)
        br_tip = QLabel("(0=体积≈源；值越大画质越好体积越大，推荐 1080p 填 2000~4000)")
        br_tip.setStyleSheet("color:#888;font-size:11px")
        nl.addWidget(br_tip, 4, 2, 1, 4)
        lay.addWidget(norm_box)
        lay.addStretch()
        return tab

    def _build_tab_manage(self) -> QWidget:
        """预设管理：预设选择 + 重命名/删除/导出/导入"""
        tab = QWidget()
        lay = QVBoxLayout(tab)

        # 预设选择（切换后去重参数页的范围随之刷新）
        pl = QHBoxLayout()
        pl.addWidget(QLabel("当前预设:"))
        self.preset_combo = QComboBox()
        for key, label, builtin in preset_mod.list_presets():
            mark = "📌 " if builtin else "👤 "
            self.preset_combo.addItem(f"{mark}{label}", key)
        cur = self._preset.get("name", "standard")
        for i in range(self.preset_combo.count()):
            if self.preset_combo.itemData(i) == cur:
                self.preset_combo.setCurrentIndex(i)
                break
        self.preset_combo.currentIndexChanged.connect(self._on_preset_switch)
        pl.addWidget(self.preset_combo, 1)
        lay.addLayout(pl)

        tip = QLabel("切换预设后去重参数页的范围随之刷新；选中列表项可重命名/删除/导出，也可导入 JSON 预设")
        tip.setStyleSheet("color:#888;font-size:11px")
        lay.addWidget(tip)
        self.mgr_list = QListWidget()
        self.mgr_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._reload_mgr_list()
        lay.addWidget(self.mgr_list, 1)
        btns = QHBoxLayout()
        b_ren = QPushButton("✏ 重命名")
        b_ren.clicked.connect(self._mgr_rename)
        btns.addWidget(b_ren)
        b_del = QPushButton("🗑 删除选中")
        b_del.clicked.connect(self._mgr_delete)
        btns.addWidget(b_del)
        b_exp = QPushButton("📤 导出选中")
        b_exp.clicked.connect(self._mgr_export)
        btns.addWidget(b_exp)
        b_imp = QPushButton("📥 导入预设")
        b_imp.clicked.connect(self._mgr_import)
        btns.addWidget(b_imp)
        btns.addStretch()
        lay.addLayout(btns)
        return tab

    def _reload_mgr_list(self, select_key=None):
        self.mgr_list.clear()
        for key, label, builtin in preset_mod.list_presets():
            if builtin:
                continue
            it = QListWidgetItem(f"👤 {label}")
            it.setData(Qt.UserRole, key)
            self.mgr_list.addItem(it)
        if self.mgr_list.count() == 0:
            it = QListWidgetItem("（暂无自定义预设，可在“去重参数”页修改后“保存为自定义”）")
            it.setFlags(Qt.NoItemFlags)
            self.mgr_list.addItem(it)
            return
        if select_key:
            for i in range(self.mgr_list.count()):
                if self.mgr_list.item(i).data(Qt.UserRole) == select_key:
                    self.mgr_list.setCurrentRow(i)
                    break

    def _mgr_selected_key(self):
        it = self.mgr_list.currentItem()
        key = it.data(Qt.UserRole) if it else None
        if not key:
            QMessageBox.information(self, "提示", "请先在列表中选择一个自定义预设")
            return None
        return key

    def _mgr_rename(self):
        key = self._mgr_selected_key()
        if not key:
            return
        snap = preset_mod.get_preset(key)
        new_name, ok = QInputDialog.getText(self, "重命名预设", "新名称:", text=key)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == key:
            return
        builtin_names = set(preset_mod.BUILTIN_KEYS)
        builtin_names.update(l for _k, l, b in preset_mod.list_presets() if b)
        if new_name in builtin_names:
            QMessageBox.warning(self, "重命名失败", "不能使用内置预设名称（温和/标准/激进）")
            return
        exists = [k for k, _l, b in preset_mod.list_presets() if not b and k == new_name]
        if exists:
            r = QMessageBox.question(self, "同名预设已存在",
                                     f"自定义预设「{new_name}」已存在，是否覆盖？",
                                     QMessageBox.Yes | QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        old_label = snap.get("label") or key
        label = new_name if old_label == key else old_label
        if preset_mod.save_custom(new_name, snap.get("params", {}), label) \
                and preset_mod.delete_custom(key):
            if self.preset_combo.currentData() == key:
                self._preset = preset_mod.get_preset(new_name)
            self._reload_mgr_list(select_key=new_name)
            self._reload_preset_combo(select_key=new_name)
            QMessageBox.information(self, "重命名成功", f"预设「{key}」→「{new_name}」")
        else:
            QMessageBox.warning(self, "重命名失败", "预设写入失败，请检查 presets 目录权限")

    def _mgr_delete(self):
        key = self._mgr_selected_key()
        if not key:
            return
        r = QMessageBox.question(self, "删除预设", f"确认删除自定义预设「{key}」？",
                                 QMessageBox.Yes | QMessageBox.No)
        if r == QMessageBox.Yes and preset_mod.delete_custom(key):
            self._reload_mgr_list()
            self._reload_preset_combo()

    def _mgr_export(self):
        key = self._mgr_selected_key()
        if not key:
            return
        snap = preset_mod.get_preset(key)
        payload = {"name": key, "label": snap.get("label", key),
                   "params": snap.get("params", {})}
        path, _ = QFileDialog.getSaveFileName(
            self, "导出预设", f"{snap.get('label', key)}.json", "预设文件 (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4, ensure_ascii=False)
            QMessageBox.information(self, "导出成功", f"预设已导出至:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    def _mgr_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入预设", "", "预设文件 (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"文件读取失败: {e}")
            return
        params = data.get("params") if isinstance(data, dict) else None
        if not isinstance(params, dict) or not params:
            QMessageBox.warning(self, "导入失败", "文件不是有效的预设文件（缺少 params）")
            return
        default_name = str(data.get("name") or data.get("label")
                           or os.path.splitext(os.path.basename(path))[0]).strip()
        name, ok = QInputDialog.getText(self, "导入预设", "保存为名称:", text=default_name)
        if not ok or not name.strip():
            return
        name = name.strip()
        builtin_names = set(preset_mod.BUILTIN_KEYS)
        builtin_names.update(l for _k, l, b in preset_mod.list_presets() if b)
        if name in builtin_names:
            QMessageBox.warning(self, "导入失败", "不能使用内置预设名称（温和/标准/激进）")
            return
        exists = [k for k, _l, b in preset_mod.list_presets() if not b and k == name]
        if exists:
            r = QMessageBox.question(self, "同名预设已存在",
                                     f"自定义预设「{name}」已存在，是否覆盖？",
                                     QMessageBox.Yes | QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        if preset_mod.save_custom(name, params, str(data.get("label", name))):
            self._reload_mgr_list(select_key=name)
            self._reload_preset_combo(select_key=name)
            QMessageBox.information(self, "导入成功", f"预设「{name}」已导入")
        else:
            QMessageBox.warning(self, "导入失败", "预设写入失败，请检查 presets 目录权限")

    def _reload_preset_combo(self, select_key=None):
        """刷新去重参数页的预设下拉框（导入/删除/重命名后同步）"""
        cur = select_key or self.preset_combo.currentData()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for key, label, builtin in preset_mod.list_presets():
            mark = "📌 " if builtin else "👤 "
            self.preset_combo.addItem(f"{mark}{label}", key)
        for i in range(self.preset_combo.count()):
            if self.preset_combo.itemData(i) == cur:
                self.preset_combo.setCurrentIndex(i)
                break
        self.preset_combo.blockSignals(False)

    def _refresh_res_combo(self, cur_w, cur_h):
        """按当前比例刷新分辨率下拉，选中与当前宽高最接近的项。
        全量对接：已保存的自定义宽高若符合当前比例，保留为选项，不被隐式替换"""
        ar = self.aspect_combo.currentText()
        res_list = list(RESOLUTION_PRESETS.get(ar, []))
        if ar == "原始比例":
            res_list = [(cur_w, cur_h)]
        elif (cur_w, cur_h) not in res_list and cur_w > 0 and cur_h > 0:
            # 保存值符合所选比例（容差 1%）才保留，否则由列表项接管（比例约束）
            rw, rh = (float(x) for x in ar.split(":"))
            if abs(cur_w / cur_h - rw / rh) / (rw / rh) <= 0.01:
                res_list.append((cur_w, cur_h))
        self.res_combo.blockSignals(True)
        self.res_combo.clear()
        for w, h in res_list:
            self.res_combo.addItem(f"{w}×{h}", (w, h))
        best = 0
        if res_list:
            best = min(range(len(res_list)),
                       key=lambda i: (abs(res_list[i][0] - cur_w)
                                      + abs(res_list[i][1] - cur_h)))
            self.res_combo.setCurrentIndex(best)
        self.res_combo.blockSignals(False)

    def _on_aspect_changed(self, text):
        cur = self.res_combo.currentData() or (1080, 1440)
        if text == "原始比例":
            self._refresh_res_combo(*cur)
            return
        res_list = RESOLUTION_PRESETS.get(text, [])
        # 切换比例后选像素量最接近的分辨率，避免画面突变
        best = min(res_list, key=lambda wh: abs(wh[0] * wh[1] - cur[0] * cur[1])) \
            if res_list else (1080, 1440)
        self._refresh_res_combo(*best)

    # ─────────────────────────── 交互 ───────────────────────────
    def _on_preset_switch(self, _idx):
        key = self.preset_combo.currentData()
        if not key or key == self._preset.get("name"):
            return
        self._preset = preset_mod.get_preset(key)
        self._rebuild_param_area()

    def _restore_current(self):
        """丢弃未保存微调，重新载入当前所选预设的参数"""
        key = self.preset_combo.currentData() or "standard"
        self._preset = preset_mod.get_preset(key)
        self._rebuild_param_area()

    def _on_ok(self):
        self._action = "apply"
        self.accept()

    def _save_as_custom(self):
        """保存为自定义预设（下次打开设置可直接选择）"""
        default_name = f"{self._preset.get('label', '')}-自定义"
        name, ok = QInputDialog.getText(self, "保存为自定义预设", "预设名称:",
                                        text=default_name)
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, "保存失败", "预设名称不能为空")
            return
        builtin_names = set(preset_mod.BUILTIN_KEYS)
        builtin_names.update(l for _k, l, b in preset_mod.list_presets() if b)
        if name in builtin_names:
            QMessageBox.warning(self, "保存失败", "不能使用内置预设名称（温和/标准/激进）")
            return
        exists = [k for k, _l, b in preset_mod.list_presets() if not b and k == name]
        if exists:
            r = QMessageBox.question(self, "同名预设已存在",
                                     f"自定义预设「{name}」已存在，是否覆盖？",
                                     QMessageBox.Yes | QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        if preset_mod.save_custom(name, self.get_params(), name):
            self._reload_mgr_list(select_key=name)
            self._reload_preset_combo(select_key=name)
            self._action = "save"
            self._saved_name = name
            self.accept()
        else:
            QMessageBox.warning(self, "保存失败", "预设保存失败，请检查 presets 目录权限")

    # ─────────────────────────── 结果读取 ───────────────────────────
    def get_action(self) -> str:
        """apply=确定；save=已保存为自定义预设"""
        return self._action

    def get_saved_name(self) -> str:
        return self._saved_name

    def get_selected_key(self) -> str:
        return self.preset_combo.currentData() or "standard"

    def get_version_count(self) -> int:
        return self._ver_spin.value()

    def get_ui_state(self) -> dict:
        """全局项 + 标准化规格 + 配置级视觉扰动（主界面持久化与处理使用）"""
        return {
            # 全局项
            "video_concurrency": int(self.conc_combo.currentText()),
            "segment_count": self.seg_spin.value(),
            "fp_enable": self.fp_chk.isChecked(),
            "fp_threshold": self.fp_thresh_spin.value(),
            "fp_frames": self.fp_frames_spin.value(),
            "fp_retry": self.fp_retry_spin.value(),
            "nvenc_preset": self.nvenc_combo.currentText(),
            "quality_check": self.chk_qc.isChecked(),
            # 标准化规格
            "normalize": self.chk_norm.isChecked(),
            "aspect_ratio": self.aspect_combo.currentText(),
            "width": int(self.res_combo.currentData()[0]),
            "height": int(self.res_combo.currentData()[1]),
            "fps": int(self.fps_combo.currentText()),
            "pix_fmt": self.pix_combo.currentText(),
            "video_codec": self.vcodec_combo.currentData(),
            "bitrate_kbps": self.br_spin.value(),
            # 配置级视觉扰动（video.* 节点）
            "ld_k1": self._ld_k1_spin.value(),
            "ld_k2": self._ld_k2_spin.value(),
            "rl_enable": self._rl_chk.isChecked(),
            "rl_prob": self._rl_prob.value(),
            "fd_enable": self._fd_chk.isChecked(),
            "fd_interval_min": self._fd_lo.value(),
            "fd_interval_max": self._fd_hi.value(),
            # 事件窗口参数（v8.1 全片预处理 + 分段动态扰动）
            "rdw_prob": self._rdw_prob.value(),
            "rdw_dur_min": self._rdw_lo.value(),
            "rdw_dur_max": self._rdw_hi.value(),
            "zdw_prob": self._zdw_prob.value(),
            "zdw_dur_min": self._zdw_lo.value(),
            "zdw_dur_max": self._zdw_hi.value(),
            "ldw_prob": self._ldw_prob.value(),
            "ldw_dur_min": self._ldw_lo.value(),
            "ldw_dur_max": self._ldw_hi.value(),
            "ldw_count_min": int(self._ldw_clo.value()),
            "ldw_count_max": int(self._ldw_chi.value()),
            "rl_len_min": self._rl_len_lo.value(),
            "rl_len_max": self._rl_len_hi.value(),
            "fdw_prob": self._fdw_prob.value(),
            "fdw_dur_min": self._fdw_lo.value(),
            "fdw_dur_max": self._fdw_hi.value(),
        }

    def get_asym_crop_range(self) -> tuple:
        """非对称裁剪 min/max（写入 video.asymmetric_crop）"""
        lo_spin, hi_spin = self._boxes["asym_crop"]
        lo, hi = lo_spin.value(), hi_spin.value()
        return (lo, hi) if lo <= hi else (hi, lo)

    def get_params(self) -> dict:
        """返回编辑后的预设 params 字典（深结构）"""
        params = copy.deepcopy(self._preset.get("params", {}))
        for key, (lo_spin, hi_spin) in self._boxes.items():
            if key == "asym_crop":
                continue  # 配置级（video.asymmetric_crop），不属于预设参数
            node = params.get(key)
            if not isinstance(node, dict):
                node = {}
            lo, hi = lo_spin.value(), hi_spin.value()
            if lo > hi:
                lo, hi = hi, lo
            if key in ("zoom_drift", "rotate_drift"):
                node["amp_min"], node["amp_max"] = lo, hi
            elif key == "rotate_drift_speed":
                # 微旋速度写入 rotate_drift 节点的 speed_min/speed_max
                rd_node = params.get("rotate_drift") or {}
                if not isinstance(rd_node, dict):
                    rd_node = {}
                rd_node["speed_min"], rd_node["speed_max"] = lo, hi
                params["rotate_drift"] = rd_node
                continue  # 不覆盖 params[key]
            elif key == "rotate_drift_period":
                # 微旋周期写入 rotate_drift 节点的 period_min/period_max
                rd_node = params.get("rotate_drift") or {}
                if not isinstance(rd_node, dict):
                    rd_node = {}
                rd_node["period_min"], rd_node["period_max"] = lo, hi
                params["rotate_drift"] = rd_node
                continue  # 不覆盖 params[key]
            else:
                node["min"], node["max"] = lo, hi
            params[key] = node
        if hasattr(self, "_av_chk"):
            node = params.get("av_offset")
            if isinstance(node, dict):
                node["enable"] = self._av_chk.isChecked()
        return params
