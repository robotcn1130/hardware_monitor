# -*- coding: utf-8 -*-
"""一键打包脚本

把主程序、Python 运行时、psutil / pystray / Pillow / pythonnet 等依赖，
以及 PresentMon.exe、传感器 DLL 和 PawnIO 驱动安装包一起打进 dist/硬件监控/ 文件夹。
打包完成后该文件夹可直接拷给别人运行，无需安装 Python 或任何第三方软件。

用法：
    python build.py
"""
import os
import sys
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))

APP_NAME = "硬件监控"
ENTRY = os.path.join(HERE, "hardware_monitor.pyw")
LIB_DIR = os.path.join(HERE, "lib")
PRESENTMON = os.path.join(HERE, "PresentMon.exe")
# 传感器内核驱动安装包（读 CPU 温度必需，首次运行自动静默安装）
PAWNIO = os.path.join(HERE, "PawnIO_setup.exe")
ICON = os.path.join(HERE, "app.ico")

DIST_DIR = os.path.join(HERE, "dist")
WORK_DIR = os.path.join(HERE, "build")

# 需要在打包后保留的、随程序一起分发的额外文件
ADD_BINARIES = [(PRESENTMON, "."), (PAWNIO, ".")]
ADD_DATAS = [(LIB_DIR, "lib")]

# pystray 的后端是运行时动态导入的，PyInstaller 静态分析发现不了
HIDDEN_IMPORTS = [
    "pystray._win32",
    "PIL._tkinter_finder",
    "clr_loader",
    "pythonnet",
]


def _check_sources():
    missing = []
    if not os.path.exists(ENTRY):
        missing.append(ENTRY)
    if not os.path.exists(PRESENTMON):
        missing.append(PRESENTMON)
    if not os.path.exists(PAWNIO):
        missing.append(PAWNIO)
    if not os.path.exists(os.path.join(LIB_DIR, "LibreHardwareMonitorLib.dll")):
        missing.append(os.path.join(LIB_DIR, "LibreHardwareMonitorLib.dll"))
    if missing:
        print("缺少必需文件：")
        for m in missing:
            print("  -", m)
        sys.exit(1)


def _make_icon():
    """用托盘图标同样的画法生成 exe 图标（没有就跳过，不影响打包）。"""
    if os.path.exists(ICON):
        return
    try:
        from PIL import Image, ImageDraw

        size = 256
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([8, 8, size - 8, size - 8], radius=48,
                            fill=(32, 40, 56, 255))
        # 温度计外形
        d.rounded_rectangle([size // 2 - 26, 46, size // 2 + 26, 168],
                            radius=26, fill=(240, 244, 250, 255))
        d.ellipse([size // 2 - 46, 150, size // 2 + 46, 242],
                  fill=(232, 84, 66, 255))
        d.rounded_rectangle([size // 2 - 13, 74, size // 2 + 13, 176],
                            radius=13, fill=(232, 84, 66, 255))
        img.save(ICON, sizes=[(16, 16), (32, 32), (48, 48), (64, 64),
                              (128, 128), (256, 256)])
        print(f"已生成图标 {ICON}")
    except Exception as exc:
        print(f"生成图标失败（忽略）：{exc}")


def main():
    _check_sources()
    _make_icon()

    out = os.path.join(DIST_DIR, APP_NAME)
    # 上一版发布目录里的 config.json 是用户调好的设置，重建前先留一份
    keep_cfg = os.path.join(out, "config.json")
    saved_cfg = None
    if os.path.exists(keep_cfg):
        with open(keep_cfg, "rb") as f:
            saved_cfg = f.read()

    for d in (DIST_DIR, WORK_DIR):
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)

    args = [
        ENTRY,
        "--name", APP_NAME,
        "--onedir",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--distpath", DIST_DIR,
        "--workpath", WORK_DIR,
        "--specpath", WORK_DIR,
    ]
    if os.path.exists(ICON):
        args += ["--icon", ICON]
    for src, dest in ADD_BINARIES:
        args += ["--add-binary", f"{src}{os.pathsep}{dest}"]
    for src, dest in ADD_DATAS:
        args += ["--add-data", f"{src}{os.pathsep}{dest}"]
    for mod in HIDDEN_IMPORTS:
        args += ["--hidden-import", mod]
    # pythonnet / clr_loader 含原生运行库，整包收集最稳妥
    args += ["--collect-all", "clr_loader", "--collect-all", "pythonnet"]

    import PyInstaller.__main__

    PyInstaller.__main__.run(args)

    if not os.path.isdir(out):
        print("打包失败：未生成输出目录")
        sys.exit(1)

    # 把用户原来的设置放回去，保证交付目录开箱即用
    if saved_cfg is not None:
        try:
            with open(os.path.join(out, "config.json"), "wb") as f:
                f.write(saved_cfg)
        except Exception as exc:
            print(f"恢复 config.json 失败（忽略）：{exc}")

    # 打包完成后做一次自检，确认关键文件都在
    need = [
        os.path.join(out, APP_NAME + ".exe"),
        os.path.join(out, "_internal", "PresentMon.exe"),
        os.path.join(out, "_internal", "PawnIO_setup.exe"),
        os.path.join(out, "_internal", "lib", "LibreHardwareMonitorLib.dll"),
    ]
    ok = True
    for p in need:
        if not os.path.exists(p):
            print("自检缺失：", p)
            ok = False

    total = sum(
        os.path.getsize(os.path.join(r, f))
        for r, _dirs, files in os.walk(out) for f in files
    )
    print()
    print("发布目录：", out)
    print("体积：%.1f MB" % (total / 1024 / 1024))
    print("自检：", "通过" if ok else "存在缺失")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
