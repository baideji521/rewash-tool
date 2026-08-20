# -*- coding: utf-8 -*-
"""gui.styles — 统一深灰主题（老版 video_rewash.py 配色）

主窗口与设置对话框共用，保证整体观感一致。
"""

QSS = """
    QDialog, QWidget#centralWidget { background-color: #2d2d2d; }
    QLabel { color: #d0d0d0; font-size: 13px; background: transparent; }
    QGroupBox {
        font-weight: bold; font-size: 13px; color: #8ab4f8;
        border: 1px solid #404040; border-radius: 6px; margin-top: 10px;
        padding: 16px 14px 14px 14px; background-color: #353535;
    }
    QGroupBox::title {
        subcontrol-origin: margin; subcontrol-position: top left;
        left: 14px; padding: 0 8px; background-color: #353535;
    }
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
        border: 1px solid #505050; border-radius: 4px; padding: 6px 10px;
        background-color: #3d3d3d; font-size: 13px; color: #e0e0e0;
        selection-background-color: #4a86c8;
    }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #6a9fd8; }
    QLineEdit::placeholder { color: #707070; }
    QComboBox::drop-down { border: none; width: 20px; }
    QComboBox QAbstractItemView {
        background-color: #3d3d3d; color: #e0e0e0;
        selection-background-color: #4a86c8; border: 1px solid #505050;
    }
    QPushButton {
        background-color: #454545; border: 1px solid #555555; border-radius: 4px;
        padding: 8px 18px; font-size: 13px; font-weight: bold;
        color: #d0d0d0; min-width: 85px;
    }
    QPushButton:hover { background-color: #505050; border-color: #666666; }
    QPushButton:pressed { background-color: #3a3a3a; }
    QPushButton:disabled { background-color: #383838; color: #606060; border-color: #454545; }
    QPushButton#startBtn {
        background-color: #3a7d44; color: #ffffff; border: 1px solid #2e6335;
        font-size: 14px; padding: 10px 28px;
    }
    QPushButton#startBtn:hover { background-color: #45914f; }
    QPushButton#startBtn:pressed { background-color: #2e6335; }
    QPushButton#startBtn:disabled { background-color: #2d4a32; color: #708070; }
    QPushButton#stopBtn {
        background-color: #a63d3d; color: #ffffff; border: 1px solid #8b3232;
    }
    QPushButton#stopBtn:hover { background-color: #b84848; }
    QPushButton#stopBtn:pressed { background-color: #8b3232; }
    QPushButton#stopBtn:disabled { background-color: #4a3030; color: #706060; }
    QProgressBar {
        border: 1px solid #505050; border-radius: 4px; text-align: center;
        font-weight: bold; font-size: 12px; color: #e0e0e0;
        background-color: #3d3d3d; height: 26px;
    }
    QProgressBar::chunk {
        background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #4a86c8, stop:1 #3a7d44);
        border-radius: 3px;
    }
    QPlainTextEdit {
        background-color: #1e1e1e; color: #c8c8c8;
        border: 1px solid #404040; border-radius: 4px; padding: 8px;
        font-family: 'Consolas', 'Courier New', monospace; font-size: 12px;
        selection-background-color: #4a86c8;
    }
    QListWidget {
        background-color: #3d3d3d; color: #e0e0e0;
        border: 1px solid #505050; border-radius: 4px; padding: 4px;
        font-size: 13px;
    }
    QListWidget::item:selected { background-color: #4a86c8; color: #ffffff; }
    QCheckBox { color: #d0d0d0; font-size: 13px; spacing: 6px; }
    QCheckBox::indicator { width: 15px; height: 15px; }
    QTabWidget::pane {
        border: 1px solid #404040; border-radius: 4px; background-color: #2d2d2d;
    }
    QTabBar::tab {
        background-color: #3d3d3d; color: #d0d0d0; padding: 8px 20px;
        border: 1px solid #505050; border-bottom: none;
        border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px;
    }
    QTabBar::tab:selected { background-color: #4a86c8; color: #ffffff; }
    QScrollArea { border: none; background: transparent; }
    QScrollArea > QWidget > QWidget { background-color: #2d2d2d; }
"""
