# -*- coding: utf-8 -*-
"""硬件监控悬浮窗（程序入口）

功能：
  - 无控制台窗口运行（.pyw）
  - 右下角托盘图标，可勾选显示哪些硬件信息
  - 无边框悬浮窗，可拖动、可置顶
  - 支持开机自启

双击本文件即可运行（.pyw 不会弹出命令行窗口）。

模块划分：
    hm_theme.py     配色与布局常量
    hm_log.py       错误日志
    hm_utils.py     路径常量 / 格式化 / 自启 / 权限 / 驱动安装
    hm_config.py    指标定义与配置读写
    hm_fps.py       PresentMon 帧率采集（帧率、帧时间、1% Low）
    hm_sensors.py   硬件传感器采集
    hm_widgets.py   画布控件与弹窗
    hm_ui.py        主窗口 MonitorApp
"""

import ctypes
import os
import sys

from hm_log import log_error, set_log_dir
from hm_ui import MonitorApp
from hm_utils import (BASE_DIR, _elevate, install_pawnio, is_admin,
                      pawnio_installed, skip_elevate)


def main():
    set_log_dir(BASE_DIR)

    # 读取 CPU 温度需要管理员权限：非管理员时尝试提权后重新启动
    # （开机自启由「最高权限计划任务」拉起，不会弹 UAC）
    if not is_admin() and not skip_elevate():
        if _elevate():
            return

    # 首次运行：装上随程序分发的传感器内核驱动，否则 CPU 温度读不到
    if not pawnio_installed():
        try:
            install_pawnio()
        except Exception as exc:
            log_error("install_pawnio", exc)

    # 单实例
    try:
        ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\HardwareMonitorTray")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return
    except Exception as exc:
        log_error("CreateMutex", exc)
    try:
        MonitorApp().root.mainloop()
    except Exception as exc:
        log_error("mainloop", exc)
        raise


if __name__ == "__main__":
    main()
