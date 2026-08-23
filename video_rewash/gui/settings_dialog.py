# -*- coding: utf-8 -*-
"""gui.settings_dialog — 设置对话框（参照老版 ConfigEditorDialog 分页设计）

页1 去重参数：预设选择 + 生成设置 + 参数 min/max 网格
页2 流程与输出：分段/标准化/质检开关 + 标准化规格（含画质/码率调节）

按钮语义：
- 确定：预设 + 全部设置应用到主界面（预设参数为临时微调）
- 保存为自定义：当前参数存为自定义预设（主界面下次打开可直接选择）
- 恢复当前预设：丢弃参数微调，重新载入当前所选预设的原始参数

页3 预设管理：自定义预设的删除 / 导出为 JSON / 从 JSON 导入
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

# (参数键, 中文名, 分组, 小数位, 是否对称参数)
# v7.1：静态旋转已删除（与微旋功能重叠），保留 rotate_drift 动态微旋
PARAM_DEFS = [
    ("scale", "缩放", "几何", 4, False),
    ("trim", "首尾裁剪(s)", "几何", 3, False),
    ("brightness", "亮度(%)", "色彩", 2, True),
    ("contrast", "对比度(%)", "色彩", 2, True),
    ("saturation", "饱和度(%)", "色彩", 2, True),
    ("hue", "色相(°)", "色彩", 2, True),
    ("channel_mix", "通道混合", "色彩", 4, True),
    ("noise", "噪点强度", "色彩", 2, False),
    ("speed", "变速", "时序", 4, False),
    ("frame_dup", "重复帧(帧)", "时序", 0, False),
    ("audio_speed", "音频微变速", "音频", 4, False),
    ("audio_pitch", "音频变调(半音)", "音频", 3, True),
    ("audio_eq", "音频EQ(dB)", "音频", 2, True),
    ("av_offset", "音画偏移(s)", "音频", 3, True),
    ("crf", "CRF/CQ", "编码", 0, False),
    ("gop", "GOP", "编码", 0, False),
]

GROUP_ORDER = ["几何", "色彩", "时序", "音频", "编码"]

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
    """设置 = 预设参数微调 + 流程开关 + 输出标准化规格"""

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
        tabs.addTab(self._build_tab_flow(), "流程与输出")
        tabs.addTab(self._build_tab_manage(), "预设管理")
        root.addWidget(tabs, 1)

        btns = QHBoxLayout()
        hint = QLabel("提示：min=max 可固定参数；确定=应用，保存=存为自定义预设")
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
        tab = QWidget()
        outer = QVBoxLayout(tab)

        # 预设选择
        pl = QHBoxLayout()
        pl.addWidget(QLabel("预设:"))
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
        outer.addLayout(pl)

        # 参数区（可滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._host = QWidget()
        self._host_lay = QVBoxLayout(self._host)
        self._build_gen_box()
        self._build_param_boxes()
        self._host_lay.addStretch()
        scroll.setWidget(self._host)
        outer.addWidget(scroll, 1)
        return tab

    def _build_gen_box(self):
        gen_box = QGroupBox("生成设置")
        gl = QHBoxLayout(gen_box)
        gl.addWidget(QLabel("每个素材生成版本数:"))
        self._ver_spin = QSpinBox()
        self._ver_spin.setRange(1, 10)
        self._ver_spin.setValue(max(1, min(10, self._version_count)))
        gl.addWidget(self._ver_spin)
        gl.addWidget(QLabel("(每版本独立随机参数，输出名带时间戳)"))
        gl.addStretch()
        self._host_lay.addWidget(gen_box)

    def _build_param_boxes(self):
        """按分组生成参数 min/max 控件（切换/恢复预设时重建）"""
        self._boxes = {}
        params = self._preset.get("params", {})
        for group in GROUP_ORDER:
            box = QGroupBox(group)
            grid = QGridLayout(box)
            grid.addWidget(QLabel("参数"), 0, 0)
            grid.addWidget(QLabel("最小"), 0, 1)
            grid.addWidget(QLabel("最大"), 0, 2)
            row = 1
            for key, label, grp, dec, signed in PARAM_DEFS:
                if grp != group:
                    continue
                node = params.get(key) or {}
                if not isinstance(node, dict):
                    continue
                grid.addWidget(QLabel(label + (" ±" if signed else "")), row, 0)
                lo_spin = QDoubleSpinBox()
                hi_spin = QDoubleSpinBox()
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
            # 渐变类参数（amp 范围）
            if group == "几何":
                for key, label in (("zoom_drift", "推镜幅度"), ("rotate_drift", "微旋幅度(°)")):
                    node = params.get(key) or {}
                    if not isinstance(node, dict):
                        continue
                    grid.addWidget(QLabel(label + " ±"), row, 0)
                    lo_spin, hi_spin = QDoubleSpinBox(), QDoubleSpinBox()
                    for spin in (lo_spin, hi_spin):
                        spin.setRange(-1000, 1000)
                        spin.setDecimals(3)
                    try:
                        lo_spin.setValue(float(node.get("amp_min", 0)))
                        hi_spin.setValue(float(node.get("amp_max", 0)))
                    except (TypeError, ValueError):
                        pass
                    grid.addWidget(lo_spin, row, 1)
                    grid.addWidget(hi_spin, row, 2)
                    self._boxes[key] = (lo_spin, hi_spin)
                    row += 1
                # 微旋速度（rotate_drift 的 speed_min/speed_max）
                rd_node = params.get("rotate_drift") or {}
                if isinstance(rd_node, dict):
                    grid.addWidget(QLabel("微旋速度(°/s) ±"), row, 0)
                    spd_lo, spd_hi = QDoubleSpinBox(), QDoubleSpinBox()
                    for spin in (spd_lo, spd_hi):
                        spin.setRange(-1000, 1000)
                        spin.setDecimals(3)
                        spin.setSingleStep(0.01)
                    try:
                        spd_lo.setValue(float(rd_node.get("speed_min", 0.02)))
                        spd_hi.setValue(float(rd_node.get("speed_max", 0.08)))
                    except (TypeError, ValueError):
                        pass
                    grid.addWidget(spd_lo, row, 1)
                    grid.addWidget(spd_hi, row, 2)
                    self._boxes["rotate_drift_speed"] = (spd_lo, spd_hi)
                    row += 1
            if group == "音频":
                node = params.get("av_offset") or {}
                if isinstance(node, dict) and "enable" in node:
                    self._av_chk = QCheckBox("启用音画偏移（观感风险项，输出过质检）")
                    self._av_chk.setChecked(bool(node.get("enable", False)))
                    grid.addWidget(self._av_chk, row, 0, 1, 3)
            self._host_lay.addWidget(box)

    def _rebuild_param_area(self):
        """清空参数分组框（保留生成设置）后重建"""
        while self._host_lay.count() > 1:
            item = self._host_lay.takeAt(1)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._build_param_boxes()
        self._host_lay.addStretch()

    def _build_tab_flow(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        u = self._ui_in

        proc_box = QGroupBox("流程开关（与档位无关）")
        fl = QGridLayout(proc_box)
        fl.addWidget(QLabel("分段:"), 0, 0)
        self.seg_spin = QSpinBox()
        self.seg_spin.setRange(1, 20)
        self.seg_spin.setValue(int(u.get("segment_count", 4) or 4))
        fl.addWidget(self.seg_spin, 0, 1)
        fl.addWidget(QLabel("(1=不分段，每段独立随机参数)"), 0, 2)
        self.chk_norm = QCheckBox("输出标准化")
        self.chk_norm.setChecked(bool(u.get("normalize", True)))
        self.chk_qc = QCheckBox("质量检测（推荐开启）")
        self.chk_qc.setChecked(bool(u.get("quality_check", True)))
        fl.addWidget(self.chk_norm, 1, 0, 1, 3)
        fl.addWidget(self.chk_qc, 2, 0, 1, 3)
        lay.addWidget(proc_box)

        conc_box = QGroupBox("视频并发数")
        cl = QGridLayout(conc_box)
        cl.addWidget(QLabel("视频并发数:"), 0, 0)
        self.conc_spin = QSpinBox()
        self.conc_spin.setRange(1, 16)
        self.conc_spin.setValue(int(u.get("video_concurrency", 1) or 1))
        cl.addWidget(self.conc_spin, 0, 1)
        conc_tip = QLabel("(默认 1 顺序处理；同时处理的视频/版本任务数。"
                          "数值越高吞吐量可能越高，但 CPU/GPU/显存压力也会增加)")
        conc_tip.setStyleSheet("color:#888;font-size:11px")
        cl.addWidget(conc_tip, 1, 0, 1, 3)
        lay.addWidget(conc_box)

        norm_box = QGroupBox("输出标准化规格")
        nl = QGridLayout(norm_box)
        nl.addWidget(QLabel("比例:"), 0, 0)
        self.aspect_combo = QComboBox()
        self.aspect_combo.addItems(list(RESOLUTION_PRESETS.keys()) + ["原始比例"])
        cur_ar = str(u.get("aspect_ratio", "3:4"))
        idx = self.aspect_combo.findText(cur_ar)
        self.aspect_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.aspect_combo.currentTextChanged.connect(self._on_aspect_changed)
        nl.addWidget(self.aspect_combo, 0, 1)
        nl.addWidget(QLabel("分辨率:"), 0, 2)
        self.res_combo = QComboBox()
        self._refresh_res_combo(int(u.get("width", 1080)), int(u.get("height", 1440)))
        nl.addWidget(self.res_combo, 0, 3, 1, 3)
        nl.addWidget(QLabel("fps:"), 1, 0)
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["24", "25", "30", "50", "60"])
        self.fps_combo.setCurrentText(str(u.get("fps", 30)))
        nl.addWidget(self.fps_combo, 1, 1)
        nl.addWidget(QLabel("像素格式:"), 1, 2)
        self.pix_combo = QComboBox()
        self.pix_combo.addItems(["yuv420p", "yuv444p", "yuv422p"])
        self.pix_combo.setCurrentText(str(u.get("pix_fmt", "yuv420p")))
        nl.addWidget(self.pix_combo, 1, 3, 1, 3)
        nl.addWidget(QLabel("编码:"), 2, 0)
        self.vcodec_combo = QComboBox()
        cur_enc = str(u.get("video_codec", "h264_nvenc"))
        for enc_key, (label, _ff) in ENCODER_TABLE.items():
            self.vcodec_combo.addItem(label, enc_key)
        enc_idx = self.vcodec_combo.findData(cur_enc)
        if enc_idx < 0:  # 旧配置 h264/h265 兼容
            enc_idx = self.vcodec_combo.findData(
                "h265_nvenc" if cur_enc == "h265" else "h264_nvenc")
        self.vcodec_combo.setCurrentIndex(max(0, enc_idx))
        nl.addWidget(self.vcodec_combo, 2, 1, 1, 3)
        nl.addWidget(QLabel("画质/码率(kbps):"), 3, 0)
        self.br_spin = QSpinBox()
        self.br_spin.setRange(0, 50000)
        self.br_spin.setSpecialValueText("自动")  # 0 显示文案
        self.br_spin.setValue(int(u.get("bitrate_kbps", 0) or 0))
        nl.addWidget(self.br_spin, 3, 1)
        br_tip = QLabel("(0=体积≈源；值越大画质越好体积越大，推荐 1080p 填 2000~4000)")
        br_tip.setStyleSheet("color:#888;font-size:11px")
        nl.addWidget(br_tip, 3, 2, 1, 4)
        lay.addWidget(norm_box)
        lay.addStretch()
        return tab

    def _build_tab_manage(self) -> QWidget:
        """预设管理：自定义预设的删除/导出/导入"""
        tab = QWidget()
        lay = QVBoxLayout(tab)
        tip = QLabel("选中自定义预设后可删除或导出为 JSON 文件；也可从 JSON 文件导入预设")
        tip.setStyleSheet("color:#888;font-size:11px")
        lay.addWidget(tip)
        self.mgr_list = QListWidget()
        self.mgr_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._reload_mgr_list()
        lay.addWidget(self.mgr_list, 1)
        btns = QHBoxLayout()
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
        """刷新去重参数页的预设下拉框（导入/删除后同步）"""
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
        """流程开关 + 标准化规格（主界面持久化与处理使用）"""
        return {
            "segment_count": self.seg_spin.value(),
            "video_concurrency": self.conc_spin.value(),
            "normalize": self.chk_norm.isChecked(),
            "quality_check": self.chk_qc.isChecked(),
            "aspect_ratio": self.aspect_combo.currentText(),
            "width": int(self.res_combo.currentData()[0]),
            "height": int(self.res_combo.currentData()[1]),
            "fps": int(self.fps_combo.currentText()),
            "pix_fmt": self.pix_combo.currentText(),
            "video_codec": self.vcodec_combo.currentData(),
            "bitrate_kbps": self.br_spin.value(),
        }

    def get_params(self) -> dict:
        """返回编辑后的 params 字典（深结构）"""
        params = copy.deepcopy(self._preset.get("params", {}))
        for key, (lo_spin, hi_spin) in self._boxes.items():
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
            else:
                node["min"], node["max"] = lo, hi
            params[key] = node
        if hasattr(self, "_av_chk"):
            node = params.get("av_offset")
            if isinstance(node, dict):
                node["enable"] = self._av_chk.isChecked()
        return params
