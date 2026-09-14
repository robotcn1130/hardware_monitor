# -*- coding: utf-8 -*-
"""把打包产物压成分发包——就是官网下载按钮后面那个 zip。

## 为什么不用 Compress-Archive / 右键压缩

两个原因，都踩过：

1. **没法排除单个文件**。`build.py` 会把本机用户的设置写回 `dist/硬件监控/`，
   直接压缩就把「别人电脑上窗口摆哪、勾了哪几项」一起发给用户了。
2. **保证不了压缩包内是单根目录**。不用 `-Path "src\\*"` 就会多套一层，
   用户双击解压后得到一屏散文件。

`Compress-Archive` 的 `-Exclude` 只对顶层文件名生效，递归目录里排不掉，所以
用 `zipfile` + `os.walk` 自己走一遍，逐条判断。

## 用法

```powershell
python package.py                                 # dist/硬件监控 → web/硬件监控.zip
python package.py --src signed --out 硬件监控.zip   # CI 里打包签名后的产物
```

压缩包内的结构固定为单根 `硬件监控/`：

```
硬件监控/
├── 硬件监控.exe
└── _internal/...
```
"""
import argparse
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

APP_NAME = "硬件监控"
EXE_NAME = APP_NAME + ".exe"

DEFAULT_SRC = os.path.join(HERE, "dist", APP_NAME)
DEFAULT_OUT = os.path.join(HERE, "web", APP_NAME + ".zip")

# 不能进分发包的东西
EXCLUDE_FILES = {"config.json"}          # 本机个人设置
EXCLUDE_DIRS = {"logs", "__pycache__"}   # 运行日志、字节码缓存


def resolve_src(src):
    """定位真正放 exe 的那一层。

    正常就是 dist/硬件监控。但签名服务返回的产物有时会多包一层目录，
    所以这里下探一层：如果 src 里没有 exe、只有一个子目录里有，就用那个子目录。
    """
    if os.path.isfile(os.path.join(src, EXE_NAME)):
        return src

    try:
        subs = sorted(d for d in os.listdir(src)
                      if os.path.isdir(os.path.join(src, d)))
    except OSError:
        return src

    for d in subs:
        if os.path.isfile(os.path.join(src, d, EXE_NAME)):
            return os.path.join(src, d)
    return src


def main():
    ap = argparse.ArgumentParser(description="把打包产物压成分发包")
    ap.add_argument("--src", default=DEFAULT_SRC, help="解包后的产物目录")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出的 zip 路径")
    ap.add_argument("--root", default=APP_NAME, help="压缩包内的根目录名")
    args = ap.parse_args()

    src = resolve_src(args.src)
    if not os.path.isfile(os.path.join(src, EXE_NAME)):
        print("在 %s 里找不到 %s，先跑 python build.py" % (args.src, EXE_NAME))
        return 1

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    count = 0
    skipped = []
    total = 0
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fn in filenames:
                if fn in EXCLUDE_FILES:
                    skipped.append(fn)
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, src)
                z.write(full, os.path.join(args.root, rel))
                count += 1
                try:
                    total += os.path.getsize(full)
                except OSError:
                    pass

    size = os.path.getsize(args.out)
    print("源目录：  ", src)
    print("输出：    ", args.out)
    print("文件数：  %d" % count)
    print("原始体积：%.1f MB" % (total / 1024 / 1024))
    print("压缩后：  %.1f MB" % (size / 1024 / 1024))
    if skipped:
        print("已排除：  %s" % ", ".join(sorted(set(skipped))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
