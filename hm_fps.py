# -*- coding: utf-8 -*-
"""帧率采集（PresentMon）。

Windows 不向普通程序提供游戏帧率。这里用随程序一起分发的 Intel PresentMon
（MIT 许可、免安装）通过 ETW 采集帧事件，再由 Python 算出帧率、帧时间与
1% / 0.1% 低帧。

采集方式：**长驻子进程 + 逐行解析 stdout**。
相比早期「每轮启动一次、采几秒、解析 CSV」的滚动窗口方案，省掉了反复启停
进程与重建 ETW 会话的开销，出数延迟从数秒降到 1 秒内；也正因为拿到了逐帧
数据，才能算出 1% / 0.1% 低帧。

安全性：全程只用 ETW（Windows 官方诊断机制）观测，**不注入游戏进程、不读写
游戏内存、不挂钩子**，不会触发反作弊。
"""
import collections
import ctypes
import os
import subprocess
import threading
import time

import psutil

from hm_log import log_error
from hm_utils import CREATE_NO_WINDOW, PM_EXE

# 采集会话用固定名字：程序异常退出会残留同名的 ETW 会话，固定名字能让下一轮
# 采集自动接管并清掉它；若按 PID 命名，残留会话会越积越多，会挤占系统会话名额。
_FPS_SESSION = "HardwareMonitorFps"
# dwm.exe 是桌面合成器，始终在出帧，不是用户想看的程序
_SKIP_PROCS = {"dwm.exe", "explorer.exe"}

_WINDOW = 5.0          # 统计窗口（秒）：只统计最近这么多秒的帧
_MAX_FRAMES = 8000     # 单进程窗口内最多保留的帧数（防高帧率下内存膨胀）
_STAT_EVERY = 0.25     # 统计快照刷新间隔（秒）
_MAX_LINES_PER_SEC = 6000   # 每秒最多解析的行数，防极端高帧率拖垮 CPU
_RETRY_WAIT = 3.0      # 子进程异常退出后的重试间隔（秒）

# PresentMon 不同版本的列名不一致，这里给出候选，运行时按实际表头自适应
_COL_APP = ("application",)
_COL_FT = ("msbetweenpresents", "frametime", "msbetweendisplaychange")
_COL_TS = ("cpustarttime", "timeinseconds")


def _purge_stale_sessions():
    """停掉历史遗留的本程序采集会话（含旧版本按 PID 命名的残留）。"""
    try:
        out = subprocess.run(
            ["logman", "query", "-ets"],
            capture_output=True, text=True, encoding="mbcs", errors="ignore",
            timeout=20, creationflags=CREATE_NO_WINDOW,
        ).stdout
    except Exception:
        return
    for line in out.splitlines():
        name = line.split()[0] if line.strip() else ""
        if name.startswith(_FPS_SESSION):
            try:
                subprocess.run(
                    ["logman", "stop", name, "-ets"],
                    capture_output=True, timeout=20,
                    creationflags=CREATE_NO_WINDOW,
                )
            except Exception:
                pass


def kill_presentmon_tree():
    """兜底：杀掉本进程派生出去、还活着的 PresentMon。

    正常退出走 `FpsSampler.stop() -> _kill_proc()`，但万一 `_proc` 追踪丢了
    （采集线程异常、切换目标时序错开等），子进程就会变成孤儿留在系统里，
    表现是「程序已经退出了，任务管理器里还有 PresentMon.exe」。
    这里按父进程链精确匹配，**只杀我们自己拉起来的**，不影响其它 PresentMon。
    """
    try:
        for child in psutil.Process().children(recursive=True):
            try:
                if child.name().lower() == "presentmon.exe":
                    child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception as exc:
        log_error("fps.kill_tree", exc)


def shutdown_fps():
    """程序退出前的收尾：停采集 → 杀残留子进程 → 清 ETW 会话。

    三步都要做：`stop()` 只处理它自己记录的那个 Popen，会话清理只处理 ETW，
    两者漏掉任何一个都会留下痕迹（进程或会话）。
    """
    try:
        fps_sampler().stop()
    except Exception as exc:
        log_error("fps.shutdown", exc)
    kill_presentmon_tree()
    _purge_stale_sessions()


def _foreground_pid():
    try:
        u32 = ctypes.windll.user32
        u32.GetForegroundWindow.restype = ctypes.c_void_p
        u32.GetWindowThreadProcessId.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        hwnd = u32.GetForegroundWindow()
        pid = ctypes.c_uint32()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value
    except Exception:
        return 0


def _foreground_proc_name():
    """当前前台窗口所属进程名；取不到时返回空串。"""
    pid = _foreground_pid()
    if not pid:
        return ""
    try:
        return psutil.Process(pid).name()
    except Exception:
        return ""


def _map_columns(header):
    """把 CSV 表头映射成列下标；认不出来时返回 None。"""
    cols = [c.strip().lower() for c in header.split(",")]

    def find(cands):
        for c in cands:
            if c in cols:
                return cols.index(c)
        return None

    app = find(_COL_APP)
    ft = find(_COL_FT)
    ts = find(_COL_TS)
    if app is None or (ft is None and ts is None):
        return None
    return (app, ft, ts)


def _low_fps(desc, pct):
    """把最慢的 pct 比例帧的平均帧时间换算成 FPS。

    desc 是「帧时间降序」列表，即最慢的帧排在最前。
    """
    n = len(desc)
    if n == 0:
        return None
    k = max(1, min(n, int(round(n * pct))))
    avg = sum(desc[:k]) / k
    return (1000.0 / avg) if avg > 0 else None


class _FrameLog:
    """单个进程的滚动帧窗口。"""

    __slots__ = ("d",)

    def __init__(self):
        self.d = collections.deque()      # (接收时刻, 帧时间ms)

    def add(self, t, ft):
        self.d.append((t, ft))
        cut = t - _WINDOW
        while self.d and self.d[0][0] < cut:
            self.d.popleft()
        while len(self.d) > _MAX_FRAMES:
            self.d.popleft()

    def stats(self):
        d = self.d
        n = len(d)
        if n < 3:
            return None
        span = d[-1][0] - d[0][0]
        if span <= 0:
            return None
        fts = [ft for _t, ft in d]
        avg = sum(fts) / n
        desc = sorted(fts, reverse=True)     # 最慢的帧在前
        return {
            "fps": (n - 1) / span,
            "frametime": avg,
            "low1": _low_fps(desc, 0.01),
            "low01": _low_fps(desc, 0.001),
            "frames": n,
        }


class FpsSampler:
    """后台长驻采集各程序的帧率，供界面随时读取最近统计。"""

    def __init__(self):
        self.lock = threading.Lock()
        self._ctl = threading.Lock()
        self.rates = {}          # {进程名: {fps, frametime, low1, low01, frames}}
        self.updated = 0.0       # 最近一次统计刷新的时间
        self.error = None        # 最近一次失败原因
        self._stop = threading.Event()
        self._thread = None
        self._proc = None
        self._target = None      # 定向采集的进程名；None = 采集全部
        self._bufs = {}

    # ---- 生命周期
    def start(self, target=None):
        """启动采集。已在运行时只有 target 变化才会重启子进程。"""
        with self._ctl:
            t = (target or "").strip() or None
            if self._thread is not None and self._thread.is_alive():
                if t == self._target:
                    return
                self._switch(t)
                return
            self._target = t
            _purge_stale_sessions()
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def set_target(self, target):
        """更换定向采集的进程；空表示采集全部进程。"""
        t = (target or "").strip() or None
        with self._ctl:
            if t == self._target:
                return
            if self._thread is None or not self._thread.is_alive():
                self._target = t
                return
            self._switch(t)

    def _switch(self, target):
        self._target = target
        self._kill_proc()
        with self.lock:
            self.rates = {}
            self.updated = 0.0
        self._bufs.clear()

    def stop(self):
        self._stop.set()
        self._kill_proc()
        with self._ctl:
            self._thread = None
        self._cleanup_session()

    def _kill_proc(self):
        p = self._proc
        self._proc = None
        if p is not None:
            try:
                p.kill()
            except Exception:
                pass

    def _cleanup_session(self):
        """通知 PresentMon 停掉残留的采集会话，避免影响其它程序。"""
        try:
            subprocess.run(
                [PM_EXE, "--terminate_existing_session",
                 "--session_name", _FPS_SESSION],
                capture_output=True, timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            pass

    # ---- 对外接口
    def snapshot(self):
        with self.lock:
            return dict(self.rates)

    def detail(self, target=None):
        """取要显示的那一项统计，返回 (stats, 进程名)。"""
        return pick_fps(self.snapshot(), target)

    def is_fresh(self, max_age=8.0):
        with self.lock:
            return bool(self.rates) and (time.time() - self.updated) <= max_age

    def last_error(self):
        with self.lock:
            return self.error

    # ---- 采集循环
    def _loop(self):
        while not self._stop.is_set():
            try:
                self._run_once()
            except Exception as exc:
                with self.lock:
                    self.error = str(exc)
                log_error("fps.run", exc)
                self._stop.wait(_RETRY_WAIT)
            else:
                # 子进程正常返回，说明它退出了（游戏关了 / PresentMon 报错）
                if not self._stop.is_set():
                    self._clear()
                    self._stop.wait(1.0)

    def _clear(self):
        with self.lock:
            self.rates = {}
            self.updated = 0.0
        self._bufs.clear()

    def _command(self, target):
        cmd = [
            PM_EXE,
            "--output_stdout",
            "--no_console_stats",
            "--v1_metrics",
            "--no_track_gpu",
            "--no_track_input",
            "--exclude_dropped",
            "--stop_existing_session",
            "--session_name", _FPS_SESSION,
        ]
        if target:
            cmd += ["--process_name", target]
        else:
            for name in sorted(_SKIP_PROCS):
                cmd += ["--exclude", name]
        return cmd

    def _run_once(self):
        if not os.path.exists(PM_EXE):
            with self.lock:
                self.error = "缺少 PresentMon.exe"
            self._stop.wait(10.0)
            return

        # 上一轮若被强杀，ETW 会话会残留，导致本轮采集不到任何数据
        self._cleanup_session()

        proc = subprocess.Popen(
            self._command(self._target),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="ignore", bufsize=1,
            cwd=os.path.dirname(PM_EXE), creationflags=CREATE_NO_WINDOW,
        )
        self._proc = proc

        idx = None
        prev_ts = {}
        last_stat = 0.0
        rate_t = time.time()
        rate_n = 0
        try:
            for raw in proc.stdout:
                if self._stop.is_set():
                    break
                line = raw.strip()
                if not line:
                    continue
                if idx is None:
                    idx = _map_columns(line.lstrip("\ufeff"))
                    if idx is None:
                        with self.lock:
                            self.error = "无法识别的数据格式：" + line[:120]
                        log_error("fps.header", line[:300])
                        return
                    continue

                now = time.time()
                if now - rate_t >= 1.0:
                    rate_t, rate_n = now, 0
                rate_n += 1
                if rate_n > _MAX_LINES_PER_SEC:
                    continue        # 极端高帧率时丢弃多余行，保证界面不卡

                i_app, i_ft, i_ts = idx
                parts = line.split(",")
                need = max(x for x in (i_app, i_ft, i_ts) if x is not None)
                if len(parts) <= need:
                    continue

                app = os.path.basename(parts[i_app].strip())
                if not app or app.lower() in _SKIP_PROCS:
                    continue

                if i_ft is not None:
                    try:
                        ft = float(parts[i_ft])
                    except ValueError:
                        continue
                else:
                    # 只有时间戳列时，用相邻两帧的时间差当帧时间
                    try:
                        ts = float(parts[i_ts])
                    except ValueError:
                        continue
                    prev = prev_ts.get(app)
                    prev_ts[app] = ts
                    if prev is None:
                        continue
                    ft = (ts - prev) * 1000.0

                if not (0.0 < ft < 10000.0):
                    continue

                buf = self._bufs.get(app)
                if buf is None:
                    buf = self._bufs[app] = _FrameLog()
                buf.add(now, ft)

                if now - last_stat >= _STAT_EVERY:
                    last_stat = now
                    self._publish()
        finally:
            self._proc = None
            try:
                proc.kill()
            except Exception:
                pass

    def _publish(self):
        """把各进程的滚动统计整理成快照，供界面读取。"""
        out = {}
        for name, buf in list(self._bufs.items()):
            s = buf.stats()
            if s is None:
                if not buf.d:
                    self._bufs.pop(name, None)
                continue
            out[name] = s
        out = dict(sorted(out.items(), key=lambda kv: kv[1]["fps"], reverse=True))
        with self.lock:
            self.rates = out
            if out:
                self.updated = time.time()
                self.error = None


_FPS_SAMPLER = FpsSampler()


def fps_sampler():
    """FPS 采集器单例（按需启动）。"""
    return _FPS_SAMPLER


def _window_titles():
    """返回 {进程名小写: (进程名, 窗口标题)}，取每个进程第一个有标题的可见窗口。"""
    out = {}
    try:
        u32 = ctypes.windll.user32
        u32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        u32.GetWindowTextLengthW.restype = ctypes.c_int
        u32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        cb_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        me = os.getpid()

        def cb(hwnd, _lparam):
            if not u32.IsWindowVisible(hwnd):
                return True
            pid = ctypes.c_uint32()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value or pid.value == me:
                return True
            n = u32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            title = (buf.value or "").strip()
            if not title:
                return True
            try:
                name = psutil.Process(pid.value).name()
            except Exception:
                return True
            key = (name or "").lower()
            if key and key != "explorer.exe":
                out.setdefault(key, (name, title))
            return True

        u32.EnumWindows(cb_type(cb), 0)
    except Exception:
        return out
    return out


def list_fps_candidates():
    """FPS 候选程序：已采集到帧率的程序（带帧率）在前，其余有窗口的程序在后。

    每项为 (进程名, fps 或 None, 窗口标题)。
    """
    titles = _window_titles()
    out = []
    seen = set()
    me = "硬件监控.exe"
    for name, st in fps_sampler().snapshot().items():
        key = name.lower()
        if key in seen or key in _SKIP_PROCS or key == me:
            continue
        seen.add(key)
        out.append((name, st.get("fps"), titles.get(key, ("", ""))[1]))
    for key, (name, title) in sorted(titles.items()):
        if key in seen or key in _SKIP_PROCS or key == me:
            continue
        seen.add(key)
        out.append((name, None, title))
    return out


def pick_fps(rates, target=None):
    """从 {进程名: 统计} 中选出要显示的一项，返回 (stats, 进程名)。

    target 为进程名时只看该程序；为空时优先取当前前台程序，否则取帧率最高者。
    """
    if not rates:
        return None, None
    if target:
        key = os.path.basename(target).lower()
        for name, st in rates.items():
            if os.path.basename(name).lower() == key:
                return st, name
        return None, None
    fg = _foreground_proc_name()
    if fg:
        for name, st in rates.items():
            if name.lower() == fg.lower():
                return st, name
    name, st = next(iter(rates.items()))
    return st, name
