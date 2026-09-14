# -*- coding: utf-8 -*-
"""一键打包脚本

把主程序、Python 运行时、psutil / pystray / Pillow / pythonnet 等依赖，
以及 PresentMon.exe、传感器 DLL 和 PawnIO 驱动安装包一起打进 dist/硬件监控/ 文件夹。
打包完成后该文件夹可直接拷给别人运行，无需安装 Python 或任何第三方软件。

用法：
    python build.py

打包完会自动尝试给 exe 签名。证书从环境变量读（见下），**没配就跳过、不报错**：

    HM_SIGN_SHA1=证书指纹         本机证书存储（云签名服务走这条）
    HM_SIGN_SUBJECT=证书主题名     本机证书存储，按主题名选
    HM_SIGN_PFX=路径 HM_SIGN_PASS=密码   老式 PFX（2023 年前发的证书）

签名细节和证书怎么来，见 sign.py 开头的说明。正式发布不需要在本机配证书——
SignPath 路线的签名在 CI 里做（.github/workflows/release.yml）。
"""
import os
import sys
import shutil
import subprocess

# stdout 接管道时（CI 就是），编码取自系统区域设置，英文 runner 上是 cp1252，
# 下面那些中文 print 会抛 UnicodeEncodeError 把构建判成失败。强制 UTF-8 输出，
# 编不出来的字符替换掉，别让日志编码影响构建结果。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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


def _sign(out):
    """打包完给 exe 签名。

    证书从环境变量读——**私钥绝不能进仓库**，所以不走配置文件、不走命令行参数
    （命令行参数会留在 shell 历史和进程列表里）。没配环境变量时只提示、不报错，
    保证普通开发者打完包就能用。

    真正的签名逻辑在 sign.py 里，这里只是把环境变量翻译成它的参数。
    它在 dist 目录下扫「没有有效签名」的 PE 文件，只签没签过的。
    """
    sha1 = os.environ.get("HM_SIGN_SHA1")
    subject = os.environ.get("HM_SIGN_SUBJECT")
    pfx = os.environ.get("HM_SIGN_PFX")

    if not (sha1 or subject or pfx):
        print()
        print("跳过签名：未配置证书（设 HM_SIGN_SHA1 / HM_SIGN_SUBJECT / HM_SIGN_PFX 后重跑）")
        return

    args = [sys.executable, os.path.join(HERE, "sign.py"), "--dir", out]
    if sha1:
        args += ["--sha1", sha1]
    elif subject:
        args += ["--n", subject]
    else:
        args += ["--pfx", pfx]
        if os.environ.get("HM_SIGN_PASS"):
            args += ["--pass", os.environ["HM_SIGN_PASS"]]

    print()
    # 签名失败不算打包失败：产物本身是好的，只是没签名，用户还能跑
    subprocess.run(args, check=False)


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

    # 自检过了才签名：产物是残的就没必要浪费一次签名请求
    if ok:
        _sign(out)

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
