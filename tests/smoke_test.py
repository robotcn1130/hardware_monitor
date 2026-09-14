# -*- coding: utf-8 -*-
"""冒烟测试：导入各模块 + 跑一次真实采集 + 建一次窗口（不进消息循环）。

用法（在项目根目录，或任意位置都可以）：
    python tests/smoke_test.py

全部通过时退出码 0，任一项 FAIL 时退出码 1。

说明：
  - 会屏蔽开机自启检测（要调 schtasks，普通权限/沙箱下会被拦）
  - 会屏蔽 FPS 采集（PresentMon 需要管理员权限才有输出，见 03 文档 §2.5）
  - 会真实读一次传感器（需要几秒）
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

OUT = []


def step(name, fn):
    try:
        r = fn()
        OUT.append("OK   %-26s %s" % (name, "" if r is None else r))
    except Exception as exc:
        OUT.append("FAIL %-26s %r" % (name, exc))
        OUT.append(traceback.format_exc())


def body():
    # 冒烟测试不碰系统：屏蔽开机自启检测（会调 schtasks，被沙箱拦）
    # 和 FPS 采集（需要管理员权限启动 PresentMon）
    import hm_utils
    hm_utils.autostart_enabled = lambda: False
    hm_utils.set_autostart = lambda enable: False

    for m in ("hm_log", "hm_theme", "hm_utils", "hm_config", "hm_fps",
              "hm_sensors", "hm_widgets", "hm_ui"):
        step("import " + m, lambda m=m: __import__(m))

    import hm_config
    cfg = hm_config.load_config()
    step("config keys", lambda: ",".join(sorted(cfg.keys()))[:150])
    step("alerts", lambda: str(cfg.get("alerts")))
    step("sparkline", lambda: str(cfg.get("sparkline")))
    step("version", lambda: str(cfg.get("config_version")))
    step("metric count", lambda: "metrics=%d order=%d"
         % (len(hm_config.METRICS), len(hm_config.METRIC_ORDER)))

    import hm_sensors
    box = {}
    step("Collector()", lambda: box.setdefault("c", hm_sensors.Collector(cfg)) and "built")

    def do_sample():
        d = box["c"].sample()
        return " | ".join("%s=%s" % (k, str(v).replace("\n", "/")[:24])
                          for k, v in d.items())

    step("Collector.sample()", do_sample)
    step("gpus", lambda: str([g.get("name") for g in box["c"].gpus])[:80])
    step("lhm_error", lambda: str(box["c"].lhm_error))

    import hm_fps
    step("pick_fps(empty)", lambda: str(hm_fps.pick_fps({}, None)))
    step("fps_sampler", lambda: type(hm_fps.fps_sampler()).__name__)
    step("PM_EXE", lambda: "%s exists=%s" % (os.path.basename(hm_fps.PM_EXE),
                                             os.path.exists(hm_fps.PM_EXE)))

    import tkinter  # noqa: F401
    import hm_ui

    class _DummySampler:
        def start(self, target=None):
            pass

        def stop(self):
            pass

        def snapshot(self):
            return {}

        def set_target(self, target):
            pass

        def is_fresh(self, max_age=8.0):
            return False

        def last_error(self):
            return None

    hm_ui.fps_sampler = lambda: _DummySampler()
    MonitorApp = hm_ui.MonitorApp

    def build_ui():
        app = MonitorApp()
        app.root.update_idletasks()
        info = "content=%s geo=%s" % (app._content_size, app.root.geometry())
        app.running = False
        try:
            app.icon.stop()
        except Exception:
            pass
        try:
            app.root.destroy()
        except Exception:
            pass
        return info

    step("MonitorApp()", build_ui)


def main():
    try:
        body()
    except Exception:
        OUT.append("!!! 未捕获异常")
        OUT.append(traceback.format_exc())

    import hm_log
    OUT.append("")
    OUT.append("log_path = %s" % hm_log.log_path())
    OUT.append("")
    print("\n".join(OUT))
    failed = [l for l in OUT if l.startswith("FAIL") or l.startswith("!!!")]
    print("结果：%d 项通过，%d 项失败" % (
        len([l for l in OUT if l.startswith("OK")]), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
