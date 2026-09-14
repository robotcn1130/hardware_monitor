# -*- coding: utf-8 -*-
"""主窗口 MonitorApp。

数据流：
    _worker 线程（采集） -> queue.Queue -> _poll_queue（主线程）
    -> _render（整张 Canvas 重画）-> _autofit（窗口自适应）

采集在后台线程（读硬件慢、可能卡），绘制只在主线程（tkinter 非线程安全），
两者只通过 queue 传递已经格式化好的结果。
"""
import collections
import ctypes
import os
import queue
import threading
import time

import tkinter as tk
import tkinter.font as tkfont

import pystray

from hm_config import (ALERT_ITEMS, ALERT_LABEL, DEFAULT_ALERTS, GPU_PARAMS,
                       METRIC_LABEL, METRIC_ORDER, METRIC_SHORT, load_config,
                       save_config)
from hm_fps import (_window_titles, fps_sampler, list_fps_candidates, pick_fps,
                    shutdown_fps)
from hm_log import log_error
from hm_sensors import HIST_KEY, Collector
from hm_theme import *
from hm_utils import (APP_NAME, APP_VERSION, autostart_enabled, set_autostart,
                      virtual_screen)
from hm_widgets import (CK, CanvasMenu, DialogShell, draw_check, draw_radio,
                        ellipsize, hover, make_icon_image, rect)

ALERT_COOLDOWN = 120.0      # 同一指标两次托盘告警之间的最短间隔（秒）


class MonitorApp:
    def __init__(self):
        self.cfg = load_config()
        self.queue = queue.Queue()
        self.collector = None
        self.running = True
        self.drag_off = (0, 0)
        self._last_size = (0, 0)
        self._width_peak = 0
        # 历史曲线：每个指标一条环形缓冲（原始数值，由 Collector 提供）
        self._hist = collections.defaultdict(lambda: collections.deque(maxlen=HIST_LEN))
        # 告警状态：当前越界的指标 + 每个指标上次弹通知的时间
        self._alerts_hit = set()
        self._alert_last = {}
        # 当前打开的右键菜单（同一时刻只允许一个）
        self._menu = None

        self._setup_dpi()

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(self.cfg["topmost"]))
        self.root.configure(bg=TRANSPARENT_KEY)
        # 四角透掉，窗口看起来才是圆角卡片（alpha 与透明色可共存）
        try:
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
        except Exception:
            pass
        self.root.attributes("-alpha", float(self.cfg["alpha"]))

        self._build_ui()
        self._place_window()
        self._bind_drag_widgets()

        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.after(200, self._poll_queue)

        self.icon = pystray.Icon(
            APP_NAME, make_icon_image(), APP_NAME, menu=self._build_menu()
        )
        threading.Thread(target=self.icon.run, daemon=True).start()
        threading.Thread(target=self._worker, daemon=True).start()

    # ---- 高分屏下文字更清晰
    @staticmethod
    def _setup_dpi():
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    # ---- 界面：整窗画在一张画布上，才能做出圆角卡片、1px 描边和分隔线
    def _build_ui(self):
        self.canvas = tk.Canvas(
            self.root, bg=TRANSPARENT_KEY, highlightthickness=0, bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self.f_title = tkfont.Font(family=FONT, size=9)
        self.f_key = tkfont.Font(family=FONT, size=9)
        self.f_val = tkfont.Font(family=FONT, size=10, weight="bold")
        self.f_gpu = tkfont.Font(family=FONT, size=9, weight="bold")
        self.f_tag = tkfont.Font(family=FONT, size=8)
        self.dlg_fonts = {
            "title": self.f_tag,
            "sec": tkfont.Font(family=FONT, size=9, weight="bold"),
            "row": tkfont.Font(family=FONT, size=9),
            "hint": tkfont.Font(family=FONT, size=8),
            "warn": tkfont.Font(family=FONT, size=8, weight="bold"),
            "btn": tkfont.Font(family=FONT, size=8),
        }

        self._data = {}              # 最近一次采样的原始文本
        self._content_size = (200, 40)
        self.apply_layout()

    # ---- 显卡数量
    def _gpu_count(self):
        try:
            n = len(self.collector.gpus) if self.collector else 0
        except Exception:
            n = 0
        return max(n, 1)

    # ---- 显卡名称（用于菜单里区分多张卡）
    def _gpu_name(self, idx):
        try:
            return (self.collector.gpus[idx].get("name") or f"显卡{idx + 1}")
        except Exception:
            return f"显卡{idx + 1}"

    def _metric_label(self, key):
        return METRIC_LABEL.get(key, key)

    def _metric_short(self, key):
        """横版一行放不下全名，用短名，与效果图一致。"""
        return METRIC_SHORT.get(key, METRIC_LABEL.get(key, key))

    def _is_shown(self, key):
        return bool(self.cfg["show"].get(key))

    # ---- 实际参与显示的指标（按用户在配置里排定的顺序）
    def _ordered_metrics(self):
        order = [k for k in self.cfg.get("order") or [] if k in METRIC_ORDER]
        return order + [k for k in METRIC_ORDER if k not in order]

    def _visible_keys(self):
        return [k for k in self._ordered_metrics() if self._is_shown(k)]

    # ---- 竖版：标题栏 + 每项一行；横版：全部内容排成一行
    def apply_layout(self):
        self.rebuild_rows()

    def _split_fps(self, value):
        """FPS 数值与后面的程序名/窗口标题分开，后者按效果图画成灰色小字。

        附加信息（程序名 + 窗口标题）最长只占 FPS_TAG_MAX 像素，超出部分省略。
        """
        head, sep, tail = value.partition("  ")
        if not sep:
            return value, ""
        return head, ellipsize(self.f_tag, tail.strip(), FPS_TAG_MAX)

    def _fps_width(self, value):
        """FPS 一行的总宽度（数值 + 附加信息）。"""
        head, tag = self._split_fps(value)
        return self.f_val.measure(head) + (6 + self.f_tag.measure(tag) if tag else 0)

    def rebuild_rows(self):
        self._width_peak = 0
        self._render()

    def update_values(self, data):
        # 采集结果里附带的原始数值，只用来画曲线和判告警，不直接显示
        hist = data.pop(HIST_KEY, None)
        self._data.update(data)
        if hist:
            for key, value in hist.items():
                try:
                    self._hist[key].append(float(value))
                except (TypeError, ValueError):
                    continue
            self._check_alerts(hist)
        self._render()

    # ---- 历史曲线
    def _hist_for(self, key):
        """取某个指标的历史序列；显卡行用 GPU 占用。"""
        if key == "gpu":
            return list(self._hist.get("gpu_load") or ())
        return list(self._hist.get(key) or ())

    def _spark_on(self, key):
        return bool(self.cfg.get("sparkline")) and len(self._hist_for(key)) >= 2

    @staticmethod
    def _draw_spark(cv, x, y, w, h, values, color):
        """在指定区域画一条迷你折线（按数据自身的最小/最大值归一化）。"""
        n = len(values)
        if n < 2 or w <= 4 or h <= 4:
            return
        lo, hi = min(values), max(values)
        if hi - lo < 1e-9:
            lo, hi = lo - 1.0, hi + 1.0
        rect(cv, x, y, x + w, y + h, fill=SPARK_BG, outline=C_LINE, width=1)
        step = (w - 2) / (n - 1)
        pts = []
        for i, v in enumerate(values):
            pts.extend((x + 1 + i * step,
                        y + h - 1 - (v - lo) / (hi - lo) * (h - 2)))
        cv.create_line(*pts, fill=color, width=1)

    # ---- 阈值告警
    def _check_alerts(self, hist):
        alerts = self.cfg.get("alerts") or {}
        if not alerts.get("enabled"):
            self._alerts_hit.clear()
            return
        now = time.time()
        for key, _label, unit, _step, _limit in ALERT_ITEMS:
            limit = alerts.get(key) or 0
            value = hist.get(key)
            if not limit or value is None:
                self._alerts_hit.discard(key)
                continue
            if value >= float(limit):
                self._alerts_hit.add(key)
                # 同一指标在冷却时间内只提醒一次，避免托盘被刷屏
                if now - self._alert_last.get(key, 0.0) >= ALERT_COOLDOWN:
                    self._alert_last[key] = now
                    self._notify_alert(key, value, limit, unit)
            else:
                self._alerts_hit.discard(key)

    def _notify_alert(self, key, value, limit, unit):
        label = ALERT_LABEL.get(key, key)
        text = f"{label} {value:.0f}{unit} 超过阈值 {limit}{unit}"
        log_error("alert.hit", text)
        try:
            self.icon.notify(text, APP_NAME)
        except Exception:
            pass

    def _render(self):
        cv = self.canvas
        cv.delete("all")
        keys = self._visible_keys()
        if self.cfg.get("layout") == "h":
            self._content_size = self._render_h(cv, keys)
        else:
            self._content_size = self._render_v(cv, keys)

    # ---- 竖版
    def _render_v(self, cv, keys):
        # 显卡与 FPS 自成一段，段前加一条分隔线（与效果图一致）
        items, seen = [], False
        for key in keys:
            if key in ("gpu", "fps") and seen:
                items.append(("sep",))
            items.append(("row", key, self._data.get(key) or "--",
                          key if key in ("gpu", "fps") else "text"))
            seen = True

        hint = "（未选择任何显示项，请在托盘菜单中勾选）"
        left = BORDER + PAD_X

        # 指标名列宽：取最宽的名字，且不小于效果图的 74px
        key_w = KEY_W
        if items:
            for item in items:
                if item[0] == "row":
                    key_w = max(key_w, self.f_key.measure(self._metric_label(item[1])) + COL_GAP)

        # 逐项排版，求出内容宽高
        y = BORDER + TITLE_H + PAD_TOP
        layout = []
        for item in items:
            if item[0] == "sep":
                line_y = y - ROW_GAP + SEP_GAP
                layout.append(("sep", line_y))
                y = line_y + SEP_GAP
                continue
            _, key, value, style = item
            f = self.f_gpu if style == "gpu" else self.f_val
            layout.append(("row", y, key, value, style))
            y += (value.count("\n") + 1) * f.metrics("linespace") + ROW_GAP

        # 数值区宽度取所有行的最大值：历史曲线统一从这个位置往右画，视觉才整齐
        val_w = 0
        for item in items:
            if item[0] != "row":
                continue
            _, key, value, style = item
            f = self.f_gpu if style == "gpu" else self.f_val
            if key == "fps":
                vw = self._fps_width(value)
            else:
                vw = max(f.measure(line) for line in value.split("\n"))
            val_w = max(val_w, vw)

        spark_keys = [k for k in keys if self._spark_on(k)]
        spark_x = (left + key_w + val_w + SPARK_GAP) if spark_keys else 0

        if items:
            content_w = key_w + val_w
            if spark_keys:
                content_w += SPARK_GAP + SPARK_W
            bottom = y - ROW_GAP + PAD_BOTTOM
        else:
            content_w = self.f_key.measure(hint)
            bottom = y + self.f_key.metrics("linespace") + PAD_BOTTOM

        title = f"{APP_NAME} · 竖版"
        bar_w = BORDER + 12 + 3 * 15 + 8 + self.f_title.measure(title) + 12
        width = max(content_w + PAD_X * 2 + BORDER * 2, bar_w, 200)
        height = bottom + BORDER

        # 卡片 + 标题栏
        rect(cv, 0, 0, width, height, fill=C_PANEL, outline=C_LINE, width=BORDER)
        rect(cv, BORDER, BORDER, width - BORDER - 1, TITLE_H,
             fill=C_PANEL_2, outline="")
        cv.create_line(0, TITLE_H, width, TITLE_H, fill=C_LINE)

        # 标题栏左侧三个圆点：红=退出，黄=收到托盘，绿=切换窗口置顶
        for i, (color, tag) in enumerate(
                ((LIGHT_RED, "dot_close"), (LIGHT_YELLOW, "dot_hide"), (LIGHT_GREEN, "dot_top"))):
            cx = BORDER + 12 + i * 15
            cv.create_oval(cx, (TITLE_H - LIGHT_D) / 2, cx + LIGHT_D, (TITLE_H + LIGHT_D) / 2,
                           fill=color, outline="", tags=(tag,))
        cv.tag_bind("dot_close", "<Button-1>", lambda e: self.quit())
        cv.tag_bind("dot_hide", "<Button-1>", lambda e: self.hide_window())
        cv.tag_bind("dot_top", "<Button-1>", lambda e: self.toggle_topmost(not self.cfg["topmost"]))
        cv.create_text(BORDER + 12 + 3 * 15 + 8, TITLE_H / 2, text=title,
                       fill=C_MUTED, font=self.f_title, anchor="w")

        # 内容
        if not items:
            cv.create_text(left, BORDER + TITLE_H + PAD_TOP, text=hint,
                           fill=C_MUTED, font=self.f_key, anchor="nw")
            return width, height

        for item in layout:
            if item[0] == "sep":
                cv.create_line(left, item[1], width - BORDER - PAD_X, item[1], fill=C_LINE)
                continue
            _, row_y, key, value, style = item
            f = self.f_gpu if style == "gpu" else self.f_val
            # 指标名与数值第一行按基线对齐
            cv.create_text(left, row_y + f.metrics("ascent") - self.f_key.metrics("ascent"),
                           text=self._metric_label(key), fill=C_MUTED,
                           font=self.f_key, anchor="nw")
            # 越过告警阈值的项整行变红，优先于按类型着色
            if key in self._alerts_hit:
                color = C_ALERT
            elif style == "gpu":
                color = C_ACCENT
            elif style == "fps":
                color = C_WARN
            else:
                color = C_TEXT
            vx = left + key_w
            if style == "fps":
                head, tag = self._split_fps(value)
                cv.create_text(vx, row_y, text=head, fill=color, font=f, anchor="nw")
                if tag:
                    cv.create_text(vx + f.measure(head) + 6,
                                   row_y + f.metrics("ascent") - self.f_tag.metrics("ascent"),
                                   text=tag, fill=C_MUTED, font=self.f_tag, anchor="nw")
            else:
                cv.create_text(vx, row_y, text=value, fill=color, font=f,
                               anchor="nw", justify="left")

            # 历史曲线：贴在数值右侧
            if spark_x and self._spark_on(key):
                self._draw_spark(cv, spark_x, row_y + 1, SPARK_W,
                                 max(8, f.metrics("linespace") - 2),
                                 self._hist_for(key),
                                 C_ALERT if key in self._alerts_hit else C_ACCENT)

        # 最后补一遍卡片描边，压住标题栏和内容溢出到边框上的部分
        rect(cv, 0, 0, width, height, fill="", outline=C_LINE, width=BORDER)
        return width, height

    # ---- 横版：一条横排，没有标题栏，占用高度极小
    def _render_h(self, cv, keys):
        gap = 5          # 指标名与数值之间的空隙
        hint = "（未选择任何显示项，请在托盘菜单中勾选）"
        items = []
        for key in keys:
            value = (self._data.get(key) or "--").replace("\n", "  ")
            style = key if key in ("gpu", "fps") else "text"
            items.append((key, self._metric_short(key), value, style))

        if items:
            widths = []
            for _, name, value, style in items:
                f = self.f_gpu if style == "gpu" else self.f_val
                if style == "fps":
                    vw = self._fps_width(value)
                else:
                    vw = f.measure(value)
                widths.append(self.f_key.measure(name) + gap + vw)
            content_w = sum(widths) + HGAP * (len(items) - 1)
            tallest = max((self.f_gpu if s == "gpu" else self.f_val).metrics("linespace")
                          for _, _, _, s in items)
        else:
            content_w = self.f_key.measure(hint)
            tallest = self.f_key.metrics("linespace")

        width = max(content_w + PAD_X * 2 + BORDER * 2, 160)
        height = tallest + HPAD_Y * 2 + BORDER * 2

        rect(cv, 0, 0, width, height, fill=C_PANEL, outline=C_LINE, width=BORDER)

        top = BORDER + HPAD_Y
        if not items:
            cv.create_text(BORDER + PAD_X, top, text=hint, fill=C_MUTED,
                           font=self.f_key, anchor="nw")
            return width, height

        base = max((self.f_gpu if s == "gpu" else self.f_val).metrics("ascent")
                   for _, _, _, s in items)
        x = BORDER + PAD_X
        for idx, (key, name, value, style) in enumerate(items):
            f = self.f_gpu if style == "gpu" else self.f_val
            cv.create_text(x, top + base - self.f_key.metrics("ascent"), text=name,
                           fill=C_MUTED, font=self.f_key, anchor="nw")
            x += self.f_key.measure(name) + gap
            if key in self._alerts_hit:
                color = C_ALERT
            elif style == "gpu":
                color = C_ACCENT
            elif style == "fps":
                color = C_WARN
            else:
                color = C_TEXT
            if style == "fps":
                head, tag = self._split_fps(value)
                cv.create_text(x, top + base - f.metrics("ascent"), text=head,
                               fill=color, font=f, anchor="nw")
                if tag:
                    cv.create_text(x + f.measure(head) + 6,
                                   top + base - self.f_tag.metrics("ascent"),
                                   text=tag, fill=C_MUTED, font=self.f_tag, anchor="nw")
            else:
                cv.create_text(x, top + base - f.metrics("ascent"), text=value,
                               fill=color, font=f, anchor="nw")
            x += widths[idx] - self.f_key.measure(name) - gap + HGAP
        return width, height

    def _place_window(self):
        w = max(self._content_size[0], 200)
        min_h = 20 if self.cfg.get("layout") == "h" else 60
        h = max(self._content_size[1], min_h)
        x = self.cfg.get("x")
        y = self.cfg.get("y")
        if x is None or y is None:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x, y = sw - w - 20, sh - h - 80
        x, y = self._clamp_on_screen(int(x), int(y), w, h)
        self._last_size = (w, h)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ---- 根据内容自适应窗口大小（磁盘多行时自动变高）
    def _autofit(self):
        need_w, need_h = self._content_size
        if self.cfg.get("layout") == "h":
            # 横版：宽度随时跟随内容，避免数值变化时留白
            w = need_w
        else:
            # 竖版：宽度只增不减，避免数值位数变化导致窗口左右抖动
            w = max(need_w, self._width_peak)
        self._width_peak = w
        vx, vy, vw, vh = self._virtual_screen()
        h = min(need_h, vh - 20)
        if (w, h) == self._last_size:
            return
        self._last_size = (w, h)
        x, y = self._clamp_on_screen(self.root.winfo_x(), self.root.winfo_y(), w, h)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.cfg["x"], self.cfg["y"] = x, y
        save_config(self.cfg)

    # 多显示器范围：实现在 hm_utils.virtual_screen，这里留个同名别名
    _virtual_screen = staticmethod(virtual_screen)

    def _clamp_on_screen(self, x, y, w, h):
        """把窗口约束在它当前所在的那块显示器内，允许副屏的负坐标。"""
        vx, vy, vw, vh = self._virtual_screen()
        # 仅当窗口中心已经跑到虚拟桌面之外时才做整体修正
        cx = x + w // 2
        cy = y + h // 2
        if cx < vx:
            x = vx
        elif cx > vx + vw:
            x = vx + vw - w
        if cy < vy:
            y = vy
        elif cy > vy + vh:
            y = vy + vh - h
        return int(x), int(y)

    # ---- 拖动（整窗只有一张画布，绑定在画布上即可）
    def _bind_drag_widgets(self):
        for widget in (self.root, self.canvas):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)
            widget.bind("<Button-3>", self._popup_menu)
        # 三个圆点：鼠标移上去变手型，且不参与拖动
        for tag in ("dot_close", "dot_hide", "dot_top"):
            self.canvas.tag_bind(tag, "<Enter>",
                                 lambda e: self.canvas.configure(cursor="hand2"))
            self.canvas.tag_bind(tag, "<Leave>",
                                 lambda e: self.canvas.configure(cursor=""))

    def _drag_start(self, event):
        # 在主窗口按下左键视为「操作完毕」，收掉还开着的右键菜单
        self.close_menu()
        self.drag_off = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_move(self, event):
        x = event.x_root - self.drag_off[0]
        y = event.y_root - self.drag_off[1]
        self.root.geometry(f"+{int(x)}+{int(y)}")

    def _drag_end(self, _event):
        self.cfg["x"] = self.root.winfo_x()
        self.cfg["y"] = self.root.winfo_y()
        save_config(self.cfg)

    def _popup_menu(self, event):
        # 连续右键时先收掉上一个，否则会一层层叠出多个菜单
        self.close_menu()
        self._menu = CanvasMenu(self, self._menu_items())
        self._menu.popup(event.x_root, event.y_root)

    def close_menu(self):
        """关掉当前右键菜单（没有打开时静默返回）。"""
        menu = getattr(self, "_menu", None)
        if menu is None:
            return
        self._menu = None
        try:
            menu.close()
        except Exception as exc:
            log_error("close_menu", exc)

    def _menu_items(self):
        """右键菜单的内容，对应效果图里的 .menu。"""
        def item(label, cmd=None, **opts):
            opts.setdefault("cmd", cmd)
            return ("item", label, opts)
        show = [item(self._metric_label(k),
                     lambda k=k: self.toggle_metric(k, not self._is_shown(k)),
                     check=lambda k=k: self._is_shown(k))
                for k in self._ordered_metrics()]
        layout = [
            item("竖版", lambda: self.set_layout("v"),
                 radio=lambda: (self.cfg.get("layout") or "v") == "v"),
            item("横版", lambda: self.set_layout("h"),
                 radio=lambda: (self.cfg.get("layout") or "v") == "h"),
        ]
        items = [
            item("显示内容", arrow=True, sub=show),
            item("排列方式", arrow=True, sub=layout),
            item("调整显示顺序…", self.open_order_dialog),
        ]
        if self.collector is not None and self.collector.gpus:
            items.append(item("显卡设置…", self.open_gpu_dialog))
        items += [
            item("监视 FPS 的程序…", self.choose_fps_target),
            item("设置…", self.open_settings_dialog),
            ("sep",),
            item("窗口置顶", lambda: self.toggle_topmost(not self.cfg["topmost"]),
                 check=lambda: bool(self.cfg["topmost"])),
            item("开机自启", lambda: self.toggle_autostart(not autostart_enabled()),
                 check=lambda: autostart_enabled()),
            ("sep",),
            # 纯展示：让用户报问题时能说清版本
            item(f"版本 {APP_VERSION}", disabled=True),
            item("退出", self.quit),
        ]
        return items

    # ---- 托盘菜单
    def _build_menu(self):
        def metric_item(key):
            return pystray.MenuItem(
                self._metric_label(key),
                lambda: self.toggle_metric(key, not self._is_shown(key)),
                checked=lambda item, k=key: self._is_shown(k),
            )

        has_gpu = self.collector is not None and bool(self.collector.gpus)

        menu_items = [
            pystray.MenuItem("显示窗口", self.show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "显示内容",
                pystray.Menu(*[metric_item(k) for k in self._ordered_metrics()]),
            ),
        ]
        if has_gpu:
            menu_items.append(
                pystray.MenuItem("显卡设置…", lambda: self.open_gpu_dialog())
            )
        menu_items += [
            pystray.MenuItem(
                "调整显示顺序…", lambda: self.open_order_dialog()
            ),
            pystray.MenuItem(
                "排列方式",
                pystray.Menu(
                    pystray.MenuItem(
                        "竖版",
                        lambda: self.set_layout("v"),
                        checked=lambda item: (self.cfg.get("layout") or "v") == "v",
                        radio=True,
                    ),
                    pystray.MenuItem(
                        "横版",
                        lambda: self.set_layout("h"),
                        checked=lambda item: self.cfg.get("layout") == "h",
                        radio=True,
                    ),
                ),
            ),
            pystray.MenuItem("监视 FPS 的程序…", lambda: self.choose_fps_target()),
            pystray.MenuItem("设置…", lambda: self.open_settings_dialog()),
            pystray.MenuItem(
                "窗口置顶",
                lambda: self.toggle_topmost(not self.cfg["topmost"]),
                checked=lambda item: bool(self.cfg["topmost"]),
            ),
            pystray.MenuItem(
                "开机自启",
                lambda: self.toggle_autostart(not autostart_enabled()),
                checked=lambda item: autostart_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            # 纯展示：让用户报问题时能说清版本
            pystray.MenuItem(f"版本 {APP_VERSION}", lambda: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self.quit),
        ]
        return pystray.Menu(*menu_items)

    def _refresh_menu(self):
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def _rebuild_menu(self):
        """菜单结构本身有变化（如显卡列表）时整体重建。"""
        try:
            self.icon.menu = self._build_menu()
        except Exception:
            pass

    # ---- 各项开关
    def toggle_metric(self, key, value):
        self.cfg["show"][key] = bool(value)
        save_config(self.cfg)
        if key == "fps" and value:
            # 勾选 FPS 时启动采集（若已指定目标进程则定向采集，开销更低）
            try:
                fps_sampler().start(self.cfg.get("fps_target"))
            except Exception as exc:
                log_error("fps.start", exc)
        self.queue.put(("rebuild", None))
        self._refresh_menu()

    # ---- FPS 数值后是否附上窗口标题 / 程序名
    def toggle_fps_detail(self, key, value):
        self.cfg[key] = bool(value)
        save_config(self.cfg)
        self._refresh_menu()

    def _invalidate_gpu(self):
        """显卡项的显示设置变了，作废缓存，下一次采样立即按新设置重算。"""
        if self.collector is not None:
            self.collector.cache_gpu = []

    # ---- 横竖版切换
    def set_layout(self, mode):
        self.cfg["layout"] = "h" if mode == "h" else "v"
        save_config(self.cfg)
        self.queue.put(("layout", None))
        self._refresh_menu()

    # ---- 选择要监视 FPS 的程序
    def choose_fps_target(self):
        threading.Thread(target=self._collect_fps_candidates, daemon=True).start()

    def _collect_fps_candidates(self):
        try:
            items = list_fps_candidates()
        except Exception:
            items = []
        self.queue.put(("fps_picker", items))

    def open_fps_picker(self, items):
        """弹出程序选择窗；列表过长时可用鼠标滚轮滚动。"""
        F = self.dlg_fonts
        LIST_W, ROW_H, MAX_ROWS = 300, 26, 8
        rows = [("", "自动（跟随前台程序）", None, None)]
        for name, fps, title in items:
            rows.append((name, name, fps, title))
        total = len(rows)
        show = min(total, MAX_ROWS)
        body_h = (F["hint"].metrics("linespace") + 8 + show * ROW_H
                  + 11 + F["row"].metrics("linespace") + 9 + 18 + 24)
        shell = DialogShell(self.root, "选择监视 FPS 的程序",
                            300 + 28, TITLE_H + 13 + body_h + 13, F)

        x = shell.body_x
        y = shell.body_y
        shell.cv.create_text(x, y, text="选择要监视的程序（双击直接确定）：",
                             fill=C_MUTED, font=F["hint"], anchor="nw")
        y += F["hint"].metrics("linespace") + 8

        current = (self.cfg.get("fps_target") or "").lower()
        picked = {"i": 0}
        for i, (value, _name, _fps, _title) in enumerate(rows):
            if value and value.lower() == current:
                picked["i"] = i
                break
        # 列表比可视区高时，把选中项滚进视野
        top = max(0, min(picked["i"] - show // 2, total - show))

        list_top = y

        def paint():
            shell.cv.delete("fl")
            for slot in range(show):
                idx = top + slot
                if idx >= total:
                    break
                _, name, fps, title = rows[idx]
                ry = list_top + slot * ROW_H
                sel = idx == picked["i"]
                if sel:
                    rect(shell.cv, x, ry, x + LIST_W, ry + ROW_H,
                         fill=C_ACCENT_DEEP, outline="", tags=("fl", f"flr{idx}"))
                elif slot % 2 == 1:
                    rect(shell.cv, x, ry, x + LIST_W, ry + ROW_H,
                         fill="#151a21", outline="", tags=("fl", f"flr{idx}"))
                tx = x + 8
                if fps:
                    fx = x + LIST_W - 8 - F["warn"].measure(f"{fps:.0f} FPS")
                    tw = fx - tx - 8
                    shell.cv.create_text(tx, ry + ROW_H / 2 - 2, text=name,
                                         fill=C_SUB, font=F["row"], anchor="w",
                                         tags=("fl", f"flr{idx}"))
                    if title and tw > 20:
                        shell.cv.create_text(
                            tx + F["row"].measure(name) + 8, ry + ROW_H / 2 + 3,
                            text=ellipsize(F["hint"], title, tw - F["row"].measure(name)),
                            fill=C_MUTED, font=F["hint"], anchor="w",
                            tags=("fl", f"flr{idx}"))
                    shell.cv.create_text(fx, ry + ROW_H / 2, text=f"{fps:.0f} FPS",
                                         fill=C_WARN, font=F["warn"], anchor="w",
                                         tags=("fl", f"flr{idx}"))
                else:
                    shell.cv.create_text(tx, ry + ROW_H / 2, text=name,
                                         fill=C_SUB, font=F["row"], anchor="w",
                                         tags=("fl", f"flr{idx}"))

        def pick(i):
            if 0 <= i < total:
                picked["i"] = i
                paint()

        def on_wheel(event):
            nonlocal top
            if total <= show:
                return
            step = -1 if event.delta > 0 else 1
            top = max(0, min(top + step, total - show))
            paint()

        paint()
        # 每行一个热区，点击时按当前滚动位置换算真实序号
        for slot in range(show):
            shell.zone(x, list_top + slot * ROW_H, x + LIST_W,
                       list_top + (slot + 1) * ROW_H,
                       lambda s=slot: pick(top + s))
        shell.cv.bind("<MouseWheel>", on_wheel)
        shell.cv.tag_bind("fl", "<Double-Button-1>", lambda e: confirm())

        # ---- 数值后面显示什么
        y = list_top + show * ROW_H
        shell.cv.create_line(x, y + 11, x + LIST_W, y + 11, fill=C_LINE)
        oy = y + 11 + 10
        shell.cv.create_text(x, oy + 2, text="数值后面显示：", fill=C_SUB,
                             font=F["row"], anchor="nw")
        ox = x + F["row"].measure("数值后面显示：") + 14
        flags = {"fps_show_title": bool(self.cfg.get("fps_show_title")),
                 "fps_show_proc": bool(self.cfg.get("fps_show_proc"))}

        def paint_opts():
            shell.cv.delete("opt")
            px_ = ox
            for key, text in (("fps_show_title", "窗口标题"), ("fps_show_proc", "程序名")):
                draw_check(shell.cv, px_, oy + (F["row"].metrics("linespace") // 2 - CK / 2),
                           flags[key], "opt")
                shell.cv.create_text(px_ + CK + 6, oy + 2, text=text, fill=C_SUB,
                                     font=F["row"], anchor="nw", tags=("opt",))
                px_ += CK + 6 + F["row"].measure(text) + 14

        def toggle(key):
            flags[key] = not flags[key]
            paint_opts()

        paint_opts()

        def confirm():
            self.set_fps_target(rows[picked["i"]][0])
            # FPS 数值后面附带的附加信息，随选择一起保存
            self.toggle_fps_detail("fps_show_title", flags["fps_show_title"])
            self.toggle_fps_detail("fps_show_proc", flags["fps_show_proc"])
            shell.close()

        by = shell.height - 13 - 24
        bx = shell.button(shell.width - 14, by, "确定", True, confirm)
        shell.button(bx, by, "取消", False, shell.close)
        # 两个勾选框各自热区（含勾选框本身与文字）
        opt_h = F["row"].metrics("linespace")
        px_ = ox
        for key, text in (("fps_show_title", "窗口标题"), ("fps_show_proc", "程序名")):
            w = CK + 6 + F["row"].measure(text)
            shell.zone(px_, oy - 2, px_ + w, oy + opt_h + 2,
                       lambda k=key: toggle(k))
            px_ += w + 14

        shell.show(self.root)
        return shell

    def set_fps_target(self, name):
        self.cfg["fps_target"] = name or ""
        save_config(self.cfg)
        # 采集器改为定向采集后开销更低，目标变了要让它换进程
        try:
            fps_sampler().set_target(self.cfg["fps_target"])
        except Exception as exc:
            log_error("fps.set_target", exc)
        self._refresh_menu()

    # ---- 显卡设置弹窗：选显示哪一张 + 勾选要显示的参数
    def open_gpu_dialog(self):
        self.queue.put(("gpu_dialog", None))

    def _show_gpu_dialog(self):
        if not (self.collector and self.collector.gpus):
            self.queue.put(("notify", "未识别到显卡"))
            return
        F = self.dlg_fonts
        rh = F["row"].metrics("linespace") + 9        # .dlg-row 行高
        sec_h = F["sec"].metrics("linespace") + 7
        choices = [(-1, "全部显卡")]
        choices += [(i, self._gpu_name(i)) for i in range(self._gpu_count())]
        rows_per_col = (len(GPU_PARAMS) + 1) // 2

        body_h = (sec_h + len(choices) * rh
                  + 11 + sec_h + rows_per_col * rh
                  + 13 + 24)
        W = 300
        shell = DialogShell(self.root, "显卡设置", W, TITLE_H + 13 + body_h + 13, F)

        x = shell.body_x
        inner = W - shell.body_x * 2

        y = shell.section(shell.body_y, "显示的显卡")
        gpu_top = y                        # 固定住单选项起始位置，避免被后续 y 复用
        target = {"v": int(self.cfg.get("gpu_target", -1))}

        def paint_gpus():
            shell.cv.delete("gpu")
            ry = gpu_top
            for idx, label in choices:
                shell.option(x, ry, label, "radio", idx == target["v"], "gpu")
                ry += rh

        paint_gpus()
        for i, _ in enumerate(choices):
            shell.zone(x, gpu_top + i * rh, x + inner, gpu_top + (i + 1) * rh,
                       lambda i=i: (target.update(v=choices[i][0]), paint_gpus()))

        y = gpu_top + len(choices) * rh + 11
        y = shell.section(y, "显示哪些参数")

        want = self.cfg.get("gpu_params") or {}
        flags = {k: bool(want.get(k, True)) for k, _ in GPU_PARAMS}
        col_w = (inner - 12) / 2
        grid_top = y

        def paint_params():
            shell.cv.delete("par")
            for i, (key, label) in enumerate(GPU_PARAMS):
                col, row = i % 2, i // 2
                shell.option(x + col * (col_w + 12), grid_top + row * rh,
                             label, "check", flags[key], "par")

        paint_params()
        for i, (key, _label) in enumerate(GPU_PARAMS):
            col, row = i % 2, i // 2
            shell.zone(x + col * (col_w + 12), grid_top + row * rh,
                       x + col * (col_w + 12) + col_w,
                       grid_top + (row + 1) * rh,
                       lambda k=key: (flags.update({k: not flags[k]}), paint_params()))

        def save():
            self.apply_gpu_settings(target["v"], flags)
            shell.close()

        by = shell.height - 13 - 24
        bx = shell.button(shell.width - 14, by, "保存", True, save)
        shell.button(bx, by, "取消", False, shell.close)

        shell.show(self.root)
        return shell

    def apply_gpu_settings(self, target, params):
        """一次性写入显卡设置，立即生效。"""
        self.cfg["gpu_target"] = int(target)
        self.cfg["gpu_params"] = {k: bool(params.get(k, True)) for k, _ in GPU_PARAMS}
        save_config(self.cfg)
        self._invalidate_gpu()
        self.queue.put(("rebuild", None))
        self._refresh_menu()

    # ---- 调整显示顺序弹窗：鼠标拖动排序
    def open_order_dialog(self):
        self.queue.put(("order_dialog", None))

    def _show_order_dialog(self):
        F = self.dlg_fonts
        visible = [k for k in self._ordered_metrics() if self._is_shown(k)]
        if not visible:
            self.queue.put(("notify", "当前没有显示中的项目"))
            return
        rh = F["row"].metrics("linespace") + 9
        W = 300
        pad = 5                                  # 列表外框内边距
        body_h = (F["hint"].metrics("linespace") + 8
                  + pad * 2 + len(visible) * rh
                  + 13 + 24)
        shell = DialogShell(self.root, "调整显示顺序", W, TITLE_H + 13 + body_h + 13, F)

        x = shell.body_x
        inner = W - shell.body_x * 2
        shell.cv.create_text(x, shell.body_y, text="上下拖动条目调整先后顺序",
                             fill=C_MUTED, font=F["hint"], anchor="nw")
        py = shell.body_y + F["hint"].metrics("linespace") + 8

        # 列表外框（对应效果图里的 panel-2 框）
        rect(shell.cv, x, py, x + inner, py + pad * 2 + len(visible) * rh,
             fill=C_PANEL_2, outline=C_LINE, width=BORDER)
        list_x, list_y = x + pad, py + pad
        list_w = inner - pad * 2
        order = list(visible)
        sel = {"i": 0}

        def paint():
            shell.cv.delete("ord")
            for i, key in enumerate(order):
                ry = list_y + i * rh
                if i == sel["i"]:
                    rect(shell.cv, list_x, ry, list_x + list_w, ry + rh,
                         fill="#254b41", outline="", tags=("ord",))
                shell.cv.create_text(list_x + 7, ry + rh / 2, text="⠿", fill="#4a5866",
                                     font=F["row"], anchor="w", tags=("ord",))
                shell.cv.create_text(list_x + 7 + 16, ry + rh / 2,
                                     text=self._metric_label(key), fill=C_SUB,
                                     font=F["row"], anchor="w", tags=("ord",))

        paint()

        def on_press(event):
            if list_y <= event.y < list_y + len(order) * rh:
                sel["i"] = min(len(order) - 1, int((event.y - list_y) // rh))
                paint()

        def on_motion(event):
            if not order:
                return
            dst = max(0, min(len(order) - 1, int((event.y - list_y) // rh)))
            src = sel["i"]
            if dst == src:
                return
            order.insert(dst, order.pop(src))
            sel["i"] = dst
            paint()

        shell.cv.bind("<Button-1>", on_press, add="+")
        shell.cv.bind("<B1-Motion>", on_motion, add="+")
        hover(shell.cv, "ord")

        def save():
            prev = self._ordered_metrics()
            self.set_order(order + [k for k in prev if k not in order])
            shell.close()

        by = shell.height - 13 - 24
        bx = shell.button(shell.width - 14, by, "保存", True, save)
        shell.button(bx, by, "取消", False, shell.close)

        shell.show(self.root)
        return shell

    # ---- 设置弹窗：告警阈值 + 历史曲线开关
    def open_settings_dialog(self):
        self.queue.put(("settings_dialog", None))

    def _show_settings_dialog(self):
        F = self.dlg_fonts
        rh = F["row"].metrics("linespace") + 9
        W = 355
        n = len(ALERT_ITEMS)
        body_h = (rh + 8 + n * rh + 11 + rh + 13 + 24)
        shell = DialogShell(self.root, "设置", W, TITLE_H + 13 + body_h + 13, F)
        x = shell.body_x
        inner = W - shell.body_x * 2

        alerts = self.cfg.get("alerts") or {}
        vals = {k: int(alerts.get(k) or 0) for k, _l, _u, _s, _m in ALERT_ITEMS}
        state = {"enabled": bool(alerts.get("enabled")),
                 "sparkline": bool(self.cfg.get("sparkline"))}
        defaults = {k: int(DEFAULT_ALERTS.get(k) or 0)
                    for k, _l, _u, _s, _m in ALERT_ITEMS}
        steps = {k: s for k, _l, _u, s, _m in ALERT_ITEMS}
        limits = {k: m for k, _l, _u, _s, m in ALERT_ITEMS}
        units = {k: u for k, _l, u, _s, _m in ALERT_ITEMS}
        labels = {k: l for k, l, _u, _s, _m in ALERT_ITEMS}

        BW, VW = 22, 56                     # 步进按钮宽、数值区宽
        plus_x = x + inner - BW
        val_x = plus_x - 6 - VW
        minus_x = val_x - 6 - BW
        val_cx = val_x + VW / 2
        ck_x = x + 6                        # 每行左侧的独立开关
        lb_x = ck_x + CK + 10               # 标签文字起点
        y0 = shell.body_y
        row_y = lambda i: y0 + rh + 8 + i * rh       # noqa: E731
        line_y = y0 + rh + 8 + n * rh + 11
        sy = line_y + 5

        def paint():
            cv = shell.cv
            cv.delete("set")
            # 告警总开关
            draw_check(cv, x + 6, y0 + rh / 2 - CK / 2, state["enabled"], "set")
            cv.create_text(x + 6 + CK + 10, y0 + rh / 2, text="启用阈值告警",
                           fill=C_SUB, font=F["row"], anchor="w", tags=("set",))
            # 各告警阈值
            for i, (key, _l, _u, _s, _m) in enumerate(ALERT_ITEMS):
                ry = row_y(i)
                cy = ry + rh / 2
                item_on = vals[key] > 0          # 阈值为 0 = 这一项不告警
                on = state["enabled"] and item_on
                draw_check(cv, ck_x, cy - CK / 2, item_on, "set")
                cv.create_text(lb_x, cy, text=labels[key],
                               fill=C_SUB if on else C_MUTED, font=F["row"],
                               anchor="w", tags=("set",))
                v = vals[key]
                cv.create_text(val_cx, cy,
                               text=(f"{v}{units[key]}" if item_on else "关闭"),
                               fill=(C_WARN if item_on else C_MUTED)
                                    if state["enabled"] else C_MUTED,
                               font=F["row"], tags=("set",))
                for bx, sign in ((minus_x, "−"), (plus_x, "+")):
                    rect(cv, bx, ry + 3, bx + BW, ry + rh - 3,
                         fill=C_BTN if on else C_PANEL, outline="", tags=("set",))
                    cv.create_text(bx + BW / 2, cy, text=sign,
                                   fill=C_SUB if on else C_MUTED, font=F["row"],
                                   tags=("set",))
            # 分隔线 + 历史曲线开关
            cv.create_line(x, line_y, x + inner, line_y, fill=C_LINE, tags=("set",))
            draw_check(cv, x + 6, sy + rh / 2 - CK / 2, state["sparkline"], "set")
            cv.create_text(x + 6 + CK + 10, sy + rh / 2, text="显示历史曲线",
                           fill=C_SUB, font=F["row"], anchor="w", tags=("set",))

        def bump(key, delta):
            # 总开关关着、或这一项本身是关闭的，步进器不响应
            if not state["enabled"] or vals[key] <= 0:
                return
            vals[key] = max(0, min(limits[key], vals[key] + delta * steps[key]))
            paint()

        def toggle(which):
            if which == "enabled":
                state["enabled"] = not state["enabled"]
            else:
                state["sparkline"] = not state["sparkline"]
            paint()

        def toggle_item(key):
            """单独开关某一项告警：关闭时阈值归 0，重新打开时给回默认值。"""
            if not state["enabled"]:
                return
            vals[key] = 0 if vals[key] > 0 else (defaults[key] or limits[key])
            paint()

        paint()
        shell.zone(x, y0, x + inner, y0 + rh, lambda: toggle("enabled"))
        for i, (key, _l, _u, _s, _m) in enumerate(ALERT_ITEMS):
            ry = row_y(i)
            # 开关 + 标签整片区域都能点，比只点小方框好按
            shell.zone(x, ry, minus_x - 6, ry + rh, lambda k=key: toggle_item(k))
            shell.zone(minus_x, ry + 3, minus_x + BW, ry + rh - 3,
                       lambda k=key: bump(k, -1))
            shell.zone(plus_x, ry + 3, plus_x + BW, ry + rh - 3,
                       lambda k=key: bump(k, 1))
        shell.zone(x, sy, x + inner, sy + rh, lambda: toggle("sparkline"))

        def save():
            self.cfg["alerts"] = dict(
                {"enabled": bool(state["enabled"])},
                **{k: int(vals[k]) for k, _l, _u, _s, _m in ALERT_ITEMS})
            self.cfg["sparkline"] = bool(state["sparkline"])
            save_config(self.cfg)
            self._alerts_hit.clear()
            self._alert_last.clear()
            self._render()
            self._autofit()
            self._refresh_menu()
            shell.close()

        by = shell.height - 13 - 24
        bx = shell.button(shell.width - 14, by, "保存", True, save)
        shell.button(bx, by, "取消", False, shell.close)
        shell.show(self.root)
        return shell

    def set_order(self, keys):
        self.cfg["order"] = list(keys)
        save_config(self.cfg)
        self.queue.put(("rebuild", None))
        self._refresh_menu()

    def toggle_topmost(self, value):
        self.cfg["topmost"] = bool(value)
        save_config(self.cfg)
        self.queue.put(("topmost", self.cfg["topmost"]))
        self._refresh_menu()

    def toggle_autostart(self, value):
        ok = set_autostart(bool(value))
        if ok:
            self.queue.put(("notify", "已开启开机自启" if value else "已关闭开机自启"))
        else:
            self.queue.put(("notify", "设置开机自启失败"))
        self._refresh_menu()

    def show_window(self):
        self.queue.put(("show", None))

    def hide_window(self):
        self.queue.put(("hide", None))

    # ---- 主线程消息循环
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "sample":
                    self.update_values(payload)
                    self._autofit()
                elif kind == "rebuild":
                    self.rebuild_rows()
                    self._autofit()
                elif kind == "layout":
                    self.apply_layout()
                    self._place_window()
                elif kind == "fps_picker":
                    self.open_fps_picker(payload)
                elif kind == "gpu_dialog":
                    self._show_gpu_dialog()
                elif kind == "order_dialog":
                    self._show_order_dialog()
                elif kind == "settings_dialog":
                    self._show_settings_dialog()
                elif kind == "menu":
                    self._rebuild_menu()
                elif kind == "topmost":
                    self.root.attributes("-topmost", bool(payload))
                elif kind == "show":
                    self.root.deiconify()
                    self.root.attributes("-topmost", bool(self.cfg["topmost"]))
                elif kind == "hide":
                    self.root.withdraw()
                elif kind == "icon":
                    try:
                        self.icon.icon = payload
                    except Exception:
                        pass
                elif kind == "notify":
                    try:
                        self.icon.notify(payload, APP_NAME)
                    except Exception:
                        pass
                elif kind == "quit":
                    self._shutdown()
                    return
        except queue.Empty:
            pass
        if self.running:
            self.root.after(150, self._poll_queue)

    # ---- 采集线程
    def _worker(self):
        # 需要 FPS 时启动后台帧率采集（PresentMon 随程序分发）
        if self.cfg["show"].get("fps"):
            try:
                fps_sampler().start(self.cfg.get("fps_target"))
            except Exception as exc:
                log_error("worker.fps_start", exc)
        try:
            self.collector = Collector(self.cfg)
        except Exception as exc:
            log_error("worker.collector", exc)
            return
        # 显卡列表此时才确定，重建界面与菜单
        self.queue.put(("rebuild", None))
        self.queue.put(("menu", None))
        interval = float(self.cfg.get("refresh") or 1.5)
        while self.running:
            try:
                data = self.collector.sample()
                self.queue.put(("sample", data))
                pct = data.get("cpu", "--").split("%")[0].strip()
                self.queue.put(("icon", make_icon_image(pct[:3] if pct else "--")))
            except Exception as exc:
                # 单次采样失败不该让整条采集线停掉，留痕后继续
                log_error("worker.sample", exc)
            time.sleep(interval)

    # ---- 退出
    def quit(self):
        self.running = False
        self.queue.put(("quit", None))

    def _shutdown(self):
        self.cfg["x"] = self.root.winfo_x()
        self.cfg["y"] = self.root.winfo_y()
        save_config(self.cfg)
        # 最后一道保险：正常退出时 mainloop 会先返回、进程直接结束，
        # 这个定时器根本不会触发。若 2 秒后还活着，说明有线程或子进程没
        # 收干净，强制结束——否则就是「托盘图标没了，任务管理器里还有进程」。
        try:
            self.root.after(2000, self._force_exit)
        except Exception:
            pass
        # 停掉 FPS 采集：杀掉派生的 PresentMon 子进程并清掉 ETW 会话
        try:
            shutdown_fps()
        except Exception as exc:
            log_error("shutdown.fps", exc)
        try:
            self.icon.stop()
        except Exception as exc:
            log_error("shutdown.icon", exc)
        try:
            self.root.destroy()
        except Exception as exc:
            log_error("shutdown.destroy", exc)

    def _force_exit(self):
        """清理超时后直接结束进程，不再等线程收尾。"""
        log_error("shutdown.force", "退出清理超时，强制结束进程")
        os._exit(0)
