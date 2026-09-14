# -*- coding: utf-8 -*-
"""错误日志。

采集与界面逻辑里大量使用 try/except 兜底，出问题时的现场很容易被吞掉。
这里统一把异常落到程序目录下的 logs/error.log，便于事后定位。
日志超过 512KB 自动轮转一次（error.log -> error.log.1）。
"""
import os
import sys
import time
import threading

_LOCK = threading.Lock()
_MAX_BYTES = 512 * 1024
_DIR = None


def base_dir():
    """程序所在目录：打包后是 exe 所在目录，否则是脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def set_log_dir(path):
    """由入口指定日志根目录（默认与程序同目录）。"""
    global _DIR
    _DIR = path


def log_path():
    return os.path.join(_DIR or base_dir(), "logs", "error.log")


def log_error(where, exc=None):
    """记一条错误。where 是出问题的大致位置，exc 是异常对象（可为 None）。"""
    try:
        if exc is None:
            detail = ""
        elif isinstance(exc, BaseException):
            detail = "%s: %s" % (type(exc).__name__, exc)
        else:
            detail = str(exc)
        line = "%s  [%s]  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), where, detail)
        with _LOCK:
            p = log_path()
            d = os.path.dirname(p)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            if os.path.exists(p) and os.path.getsize(p) > _MAX_BYTES:
                try:
                    os.replace(p, p + ".1")
                except OSError:
                    pass
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass

