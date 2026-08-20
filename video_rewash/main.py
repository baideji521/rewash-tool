# -*- coding: utf-8 -*-
"""video_rewash v7.0 入口

启动方式：
    python -m video_rewash          （推荐，等价于 python -m video_rewash.main）
    python -m video_rewash.main

设计原则（v7.0 审定版）：
- 全参数随机扩大输出差异空间，但不保证绕过任何平台内容识别；
  通过率等指标属于"目标指标"，需以固定测试集实测，不写入代码承诺。
- 核心链 preset → randomizer → processor → ffmpeg_runner 必须稳定，
  其余环节（质检/指纹/二次处理）失败不阻断主流程。
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def main():
    # 日志目录保证存在
    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"),
                exist_ok=True)

    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import Qt
    try:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    except Exception:
        pass

    from video_rewash.gui.main_window import MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName("视频去重冲洗工具 v7.0")
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
