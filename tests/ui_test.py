# -*- coding: utf-8 -*-
"""逻辑与 UI 细节验证：帧率列映射 / 帧统计 / 低帧算法 / 曲线 / 告警 / 四个弹窗。

用法（在项目根目录，或任意位置都可以）：
    python tests/ui_test.py

全部通过时退出码 0，任一项 FAIL 时退出码 1。

说明：
  - 全是纯函数 + 假数据测试，**不需要管理员权限、不需要真实出帧的程序**
  - 会真实创建一个 MonitorApp 窗口（无边框、会短暂闪现），走 `update()` 但不进 mainloop
  - 会屏蔽开机自启检测与 FPS 采集
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
        OUT.append("OK   %-30s %s" % (name, "" if r is None else r))
    except Exception as exc:
        OUT.append("FAIL %-30s %r" % (name, exc))
        OUT.append(traceback.format_exc())


def body():
    import hm_utils
    hm_utils.autostart_enabled = lambda: False
    hm_utils.set_autostart = lambda enable: False

    import hm_fps
    from hm_fps import _FrameLog, _low_fps, _map_columns

    step("列映射(含帧时间列)", lambda: str(_map_columns(
        "Application,ProcessID,msBetweenPresents,CPUStartTime")))
    step("列映射(仅时间戳)", lambda: str(_map_columns(
        "Application,ProcessID,CPUStartTime")))
    step("列映射(2.x FrameTime)", lambda: str(_map_columns(
        "Application,ProcessID,FrameTime,CPUStartTime")))
    step("列映射(表头不认识)", lambda: str(_map_columns("Foo,Bar")))

    def frame_log():
        buf = _FrameLog()
        t = 1000.0
        for i in range(99):
            buf.add(t + i * 0.01, 10.0)          # 稳定 100fps
        buf.add(t + 99 * 0.01, 100.0)            # 一帧卡了 100ms
        s = buf.stats()
        return ("fps=%.1f ft=%.2fms low1=%s low01=%s frames=%d"
                % (s["fps"], s["frametime"],
                   ("%.0f" % s["low1"]) if s["low1"] else "-",
                   ("%.0f" % s["low01"]) if s["low01"] else "-",
                   s["frames"]))

    step("帧统计(100帧含1次卡顿)", frame_log)
    step("_low_fps 空", lambda: str(_low_fps([], 0.01)))

    def window_trim():
        buf = _FrameLog()
        for i in range(10):
            buf.add(0.0 + i * 1.0, 10.0)         # 老数据，会被窗口裁掉
        for i in range(10):
            buf.add(1000.0 + i * 0.01, 10.0)
        return "剩余帧=%d" % len(buf.d)

    step("滑动窗口裁剪", window_trim)

    # ---------------- UI ----------------
    import hm_ui
    from hm_ui import MonitorApp

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

    app = MonitorApp()
    app.root.update()

    def feed(n, spark):
        app.cfg["sparkline"] = spark
        app.cfg["show"]["cpu"] = True
        app.cfg["show"]["cpu_temp"] = True
        for i in range(n):
            app.update_values({"cpu": "%d%%  (12 核)" % (30 + i % 40),
                               "cpu_temp": "%d°C" % (60 + i % 20),
                               "_hist": {"cpu": 30.0 + i, "cpu_temp": 60.0 + i}})
        app.root.update()
        return "content=%s 画布元素=%d" % (app._content_size, len(app.canvas.find_all()))

    step("渲染无曲线", lambda: feed(5, False))
    step("渲染含曲线", lambda: feed(40, True))

    def spark_size():
        app.cfg["sparkline"] = False
        app._render()
        w_off = app._content_size[0]
        app.cfg["sparkline"] = True
        app._render()
        w_on = app._content_size[0]
        return "关闭=%d 开启=%d 差值=%d" % (w_off, w_on, w_on - w_off)

    step("曲线占用宽度", spark_size)

    def alert_fire():
        app.cfg["alerts"] = {"enabled": True, "cpu": 50, "cpu_temp": 0,
                             "gpu_temp": 0, "hdd_temp": 0, "mem": 0}
        app._alerts_hit.clear()
        app._alert_last.clear()
        app.update_values({"cpu": "80%  (12 核)", "_hist": {"cpu": 80.0}})
        hit_hi = set(app._alerts_hit)
        app.update_values({"cpu": "20%  (12 核)", "_hist": {"cpu": 20.0}})
        hit_lo = set(app._alerts_hit)
        return "超阈=%s 回落后=%s" % (sorted(hit_hi), sorted(hit_lo))

    step("告警触发与恢复", alert_fire)

    def alert_off():
        app.cfg["alerts"]["enabled"] = False
        app.update_values({"cpu": "99%  (12 核)", "_hist": {"cpu": 99.0}})
        return "关闭后=%s" % sorted(app._alerts_hit)

    step("告警总开关", alert_off)

    def settings_dialog():
        app.cfg["alerts"]["enabled"] = True
        sh = app._show_settings_dialog()
        n = len(sh.cv.find_all())
        zones = len(sh._zones)
        sh.close()
        return "元素=%d 热区=%d" % (n, zones)

    step("设置弹窗构建", settings_dialog)

    # ---- 右键菜单行为（勾选保持打开 / 动作项才关闭 / 同时只开一个）
    def ev(x=0, y=0, xr=0, yr=0):
        return type("E", (), {"x": x, "y": y, "x_root": xr, "y_root": yr})()

    def menu_check_keeps_open():
        import hm_widgets
        st = {"on": True}
        items = [("item", "测试项", {
            "check": lambda: st["on"],
            "cmd": lambda: st.__setitem__("on", not st["on"])})]
        m = hm_widgets.CanvasMenu(app, items)
        m.popup(120, 120)
        app.root.update()
        p = m.panels[0]
        before = st["on"]
        m._on_click(p, ev(10, m.PAD + m.item_h / 2))
        flipped = before != st["on"]
        still = len(m.panels)
        m.close()
        return "状态翻转=%s 点击后仍打开=%s" % (flipped, still == 1)

    step("菜单勾选不关闭", menu_check_keeps_open)

    def menu_action_closes():
        import hm_widgets
        fired = {"n": 0}
        items = [("item", "退出", {"cmd": lambda: fired.__setitem__("n", 1)})]
        m = hm_widgets.CanvasMenu(app, items)
        m.popup(120, 120)
        app.root.update()
        m._on_click(m.panels[0], ev(10, m.PAD + m.item_h / 2))
        return "动作已执行=%s 菜单已关闭=%s" % (fired["n"] == 1, len(m.panels) == 0)

    step("菜单动作项会关闭", menu_action_closes)

    def menu_single_instance():
        hm_ui.autostart_enabled = lambda: False
        app._popup_menu(ev(xr=300, yr=300))
        first = app._menu
        app._popup_menu(ev(xr=300, yr=300))
        second = app._menu
        changed = first is not second
        panels = len(second.panels)
        app.close_menu()
        return "换成新菜单=%s 面板数=%d 关闭后=%s" % (
            changed, panels, app._menu is None)

    step("重复右键只留一个", menu_single_instance)

    def alert_item_toggle():
        """设置弹窗里每项可单独开关：关掉后该项显示"关闭"，再点恢复。"""
        app.cfg["alerts"] = {"enabled": True, "cpu": 95, "cpu_temp": 85,
                             "gpu_temp": 88, "hdd_temp": 55, "mem": 90}
        sh = app._show_settings_dialog()

        def n_off():
            n = 0
            for i in sh.cv.find_withtag("set"):
                try:
                    if sh.cv.itemcget(i, "text") == "关闭":
                        n += 1
                except Exception:
                    pass
            return n

        n0 = n_off()
        sh._zones[1][4]()        # 热区 1 = 第一项（CPU 使用率）的开关
        n1 = n_off()
        sh._zones[1][4]()        # 再点一次恢复
        n2 = n_off()
        sh.close()
        return "关闭项数 %d→%d→%d" % (n0, n1, n2)

    step("告警项可单独关闭", alert_item_toggle)

    def version_defined():
        from hm_utils import APP_VERSION
        parts = APP_VERSION.split(".")
        ok = len(parts) == 3 and all(p.isdigit() for p in parts)
        # 菜单里要能看到版本，否则用户报问题时说不清是哪一版
        items = app._menu_items()
        shown = [it[1] for it in items if it[0] == "item" and "版本" in it[1]]
        return "%s 格式正确=%s 菜单显示=%s" % (APP_VERSION, ok, shown)

    step("版本号", version_defined)

    def shutdown_cleanup():
        """退出前的子进程兜底：只杀本进程派生的 PresentMon，不误伤别人的。

        这里只跑 `kill_presentmon_tree()`——它纯走 psutil，不启外部程序。
        完整的 `shutdown_fps()` 还会调 PresentMon / logman，在受限环境里会被
        安全策略拦下，不适合放进测试。
        """
        import hm_fps
        hm_fps.kill_presentmon_tree()
        ok = callable(hm_fps.shutdown_fps)
        return "兜底清理已执行，shutdown_fps 可用=%s" % ok

    step("退出清理", shutdown_cleanup)

    def order_dialog():
        sh = app._show_order_dialog()
        sh.close()
        return "ok"

    step("顺序弹窗构建", order_dialog)

    def gpu_dialog():
        sh = app._show_gpu_dialog()
        if sh is None:
            return "无显卡，已跳过"
        sh.close()
        return "ok"

    step("显卡弹窗构建", gpu_dialog)

    def all_metrics():
        for k in hm_ui.METRIC_ORDER:
            app.cfg["show"][k] = True
        app.update_values({k: "测试值" for k in hm_ui.METRIC_ORDER})
        return "全部 %d 项一起渲染 content=%s" % (len(hm_ui.METRIC_ORDER),
                                                 app._content_size)

    step("全部指标渲染", all_metrics)

    app.running = False
    try:
        app.icon.stop()
    except Exception:
        pass
    try:
        app.root.destroy()
    except Exception:
        pass


def main():
    try:
        body()
    except Exception:
        OUT.append("!!! 未捕获异常")
        OUT.append(traceback.format_exc())

    print("\n".join(OUT))
    failed = [l for l in OUT if l.startswith("FAIL") or l.startswith("!!!")]
    print("结果：%d 项通过，%d 项失败" % (
        len([l for l in OUT if l.startswith("OK")]), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
