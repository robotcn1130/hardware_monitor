# -*- coding: utf-8 -*-
"""通用工具：路径常量、格式化、开机自启、管理员权限、传感器驱动安装。

这一层不依赖任何界面代码，可被其余模块自由引用。
"""
import ctypes
import os
import subprocess
import sys

import tkinter as tk

from hm_log import log_error

APP_NAME = "硬件监控"

# 版本号：改动界面上能看见的行为就 +1，写在菜单里方便用户报问题时说清版本。
#   1.x   单文件版本（9 项指标）
#   2.0   阶段一：拆模块 + 15 项指标 + 帧时间/低帧/历史曲线/阈值告警
#   2.1   右键菜单交互修复 + 告警项可单独关闭 + 退出清理加固
APP_VERSION = "2.1.0"


def _app_dir():
    """程序所在目录：打包成 exe 后是 exe 所在目录，否则是脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _app_dir()
# 随程序一起发布的资源目录：打包后位于 _internal，未打包时就是脚本目录
BUNDLE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
CREATE_NO_WINDOW = 0x08000000


def _find_bundled(rel):
    """在随程序发布的资源目录里找一个文件：打包后位于 _internal，
    未打包时就是脚本目录；两处都找不到时返回打包目录下的路径。"""
    for base in (BUNDLE_DIR, BASE_DIR):
        p = os.path.join(base, rel)
        if os.path.exists(p):
            return p
    return os.path.join(BUNDLE_DIR, rel)


# FPS 采集工具（Intel PresentMon，随程序一起分发，无需另外安装）
PM_EXE = _find_bundled("PresentMon.exe")


# ---------------------------------------------------------------- 开机自启
# 用「计划任务 + 最高权限」实现：开机不弹 UAC，同时具备读取 CPU 温度所需的权限
def _pythonw_path():
    exe = sys.executable
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.exists(cand) else exe


def _launch_cmd():
    """返回启动本程序的完整命令行片段（含参数），供自启/提权复用。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{_pythonw_path()}" "{os.path.abspath(__file__)}"'


def _run(cmd):
    # schtasks 等命令在中文 Windows 下输出为本地编码（GBK），
    # 显式指定编码并容错，避免后台读取线程抛 UnicodeDecodeError
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=20,
        encoding="mbcs", errors="ignore",
        creationflags=CREATE_NO_WINDOW,
    )


def _remove_legacy_autostart():
    """清理旧版本写入的注册表自启项，避免开机启动两个实例。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
    except Exception:
        pass


def autostart_enabled():
    try:
        return _run(["schtasks", "/query", "/tn", APP_NAME]).returncode == 0
    except Exception:
        return False


def set_autostart(enable):
    # 创建/删除最高权限的计划任务需要管理员权限
    if not is_admin():
        return False
    _remove_legacy_autostart()
    try:
        if enable:
            action = _launch_cmd()
            out = _run([
                "schtasks", "/create", "/tn", APP_NAME, "/tr", action,
                "/sc", "onlogon", "/rl", "highest", "/f",
            ])
        else:
            out = _run(["schtasks", "/delete", "/tn", APP_NAME, "/f"])
        return out.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------- 管理员权限
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _elevate():
    """以管理员身份重新启动自己；用户同意并成功启动返回 True。"""
    try:
        if getattr(sys, "frozen", False):
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, None, BASE_DIR, 1
            )
        else:
            script = os.path.abspath(__file__)
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", _pythonw_path(), f'"{script}"', BASE_DIR, 1
            )
        return int(rc) > 32
    except Exception:
        return False


# ---------------------------------------------------------------- 传感器内核驱动
# AMD/Intel 的 CPU 温度不走 Windows 公开接口，而是直接读处理器内部寄存器。
# LibreHardwareMonitor 0.9.6 起改用 PawnIO 内核驱动完成这一步（旧的 WinRing0
# 已被 Windows Defender 拉黑）。没装这个驱动时，CPU 传感器能枚举出来但读到的
# 值全是 0，表现为「CPU 温度不可用」。安装包随程序一起分发，首次运行自动安装。
PAWNIO_SETUP = _find_bundled("PawnIO_setup.exe")
# 本次运行是否刚装过驱动：装完可能需重启才生效，界面据此给出提示
_pawnio_just_installed = False


def pawnio_installed():
    """检测 PawnIO 驱动是否已安装。

    优先看驱动服务键（最可靠），其次看「程序和功能」的卸载项。"""
    import winreg

    paths = (
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\PawnIO"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO"),
    )
    for hive, path in paths:
        try:
            with winreg.OpenKey(hive, path):
                return True
        except OSError:
            pass
    return False


def install_pawnio(timeout=180):
    """静默安装随程序分发的 PawnIO 驱动，成功返回 True。需管理员权限。

    silent 模式下安装器用 3010/1641 表示「装好了但要重启才生效」，
    这两个返回码同样算成功（否则界面提示不出「请重启电脑」）。"""
    global _pawnio_just_installed
    if not os.path.exists(PAWNIO_SETUP):
        return False
    try:
        out = subprocess.run(
            [PAWNIO_SETUP, "-install", "-silent"],
            capture_output=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        ok = out.returncode in (0, 3010, 1641)
        _pawnio_just_installed = ok
        return ok
    except Exception as exc:
        log_error("install_pawnio", exc)
        return False


def pawnio_just_installed():
    """本次运行是否刚装过内核驱动（装完可能要重启才生效，界面据此提示）。"""
    return _pawnio_just_installed


# ---------------------------------------------------------------- 运行环境
def skip_elevate():
    """调试开关：环境变量 HM_SKIP_ELEVATE=1 时跳过自我提权。

    便于在非管理员下启动界面调样式（此时 CPU 温度读不到，属正常现象）。
    """
    try:
        return os.environ.get("HM_SKIP_ELEVATE", "").strip().lower() not in (
            "", "0", "false", "no")
    except Exception:
        return False


def virtual_screen():
    """多显示器：整块虚拟桌面的范围（含左侧/上方副屏的负坐标）。"""
    try:
        u32 = ctypes.windll.user32
        vx = u32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        vy = u32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
        vw = u32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        vh = u32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
        if vw > 0 and vh > 0:
            return vx, vy, vw, vh
    except Exception:
        pass
    root = tk._default_root
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


# ---------------------------------------------------------------- 格式化
def _fmt_bytes(n):
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.0f}{u}" if u in ("B", "KB") else f"{f:.1f}{u}"
        f /= 1024


def _fmt_speed(n):
    return _fmt_bytes(n) + "/s"


def _fmt_duration(sec):
    sec = int(sec)
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, _ = divmod(sec, 60)
    if d:
        return f"{d}天{h}小时"
    if h:
        return f"{h}小时{m}分"
    return f"{m}分钟"

