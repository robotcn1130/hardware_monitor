# -*- coding: utf-8 -*-
"""画布控件与弹窗。

本程序没有用任何 GUI 框架：所有界面元素都是画在一张 tkinter Canvas 上的
图形与文字。这里放通用的绘制辅助、对话框外壳 DialogShell 和右键菜单
CanvasMenu。
"""
import ctypes
import tkinter as tk

from PIL import Image, ImageDraw, ImageFont

from hm_theme import *
from hm_utils import virtual_screen

# ---------------------------------------------------------------- 托盘图标
def make_icon_image(text="--"):
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [2, 2, size - 3, size - 3], radius=14,
        fill=(28, 32, 38, 255), outline=(0, 210, 160, 255), width=3,
    )
    try:
        font = ImageFont.truetype("arialbd.ttf", 24)
    except Exception:
        font = ImageFont.load_default()
    d.text((size / 2, size / 2), text, font=font, fill=(0, 230, 170, 255), anchor="mm")
    return img


# ---------------------------------------------------------------- 主界面
def rect(cv, x0, y0, x1, y1, **kw):
    """直角矩形；圆角改用 create_polygon 的平滑曲线会发毛，这里统一走直线。"""
    return cv.create_rectangle(x0, y0, x1, y1, **kw)


CK = 13                      # 勾选框/单选框边长（效果图里的 13px）


def draw_check(cv, x, y, on, tag):
    rect(cv, x, y, x + CK, y + CK,
         fill=C_ACCENT if on else C_PANEL,
         outline=C_ACCENT if on else "#4a5866", width=1, tags=(tag,))
    if on:
        cv.create_text(x + CK / 2, y + CK / 2, text="✓", fill="#08130f",
                       font=(FONT, 7, "bold"), tags=(tag,))


def draw_radio(cv, x, y, on, tag):
    cv.create_oval(x, y, x + CK, y + CK, fill=C_PANEL,
                   outline=C_ACCENT if on else "#4a5866", width=1, tags=(tag,))
    if on:
        cv.create_oval(x + 3.5, y + 3.5, x + CK - 3.5, y + CK - 3.5,
                       fill=C_ACCENT, outline="", tags=(tag,))


def hover(cv, *tags):
    """鼠标移到这些元素上时变成手型。"""
    for tag in tags:
        cv.tag_bind(tag, "<Enter>", lambda e: cv.configure(cursor="hand2"))
        cv.tag_bind(tag, "<Leave>", lambda e: cv.configure(cursor=""))


def ellipsize(font, text, max_w):
    """按像素宽度截断文字并加省略号（对应 CSS 的 text-overflow:ellipsis）。"""
    if max_w <= 0:
        return ""
    if font.measure(text) <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + "…") <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…" if lo else ""


class DialogShell:
    """无边框深色弹窗：标题栏 + 内容画布，对齐效果图里的 .dlg。"""

    def __init__(self, parent, title, width, height, fonts):
        self.width = int(width)
        self.height = int(height)
        self.f = fonts
        self.closed = False

        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=TRANSPARENT_KEY)
        try:
            self.win.attributes("-transparentcolor", TRANSPARENT_KEY)
        except Exception:
            pass

        self.cv = tk.Canvas(self.win, bg=TRANSPARENT_KEY, highlightthickness=0,
                            bd=0, width=self.width, height=self.height)
        self.cv.pack(fill="both", expand=True)

        # 卡片 + 标题栏
        rect(self.cv, 0, 0, self.width, self.height,
             fill=C_PANEL, outline=C_LINE, width=BORDER)
        rect(self.cv, BORDER, BORDER, self.width - BORDER - 1, TITLE_H,
             fill=C_PANEL_2, outline="", tags=("chrome",))
        self.cv.create_line(0, TITLE_H, self.width, TITLE_H, fill=C_LINE, tags=("chrome",))
        self.cv.create_text(BORDER + 12, TITLE_H / 2, text=title, fill=C_MUTED,
                            font=fonts["title"], anchor="w", tags=("chrome",))

        self.body_x = 14          # .dlg-body padding 13px 14px
        self.body_y = TITLE_H + 13
        self._zones = []

        # 只能拖标题栏，避免与内容里的点击冲突
        self._off = None
        self.cv.bind("<Button-1>", self._press)
        self.cv.bind("<B1-Motion>", self._move)
        self.win.bind("<Escape>", lambda e: self.close())

    # ---- 内容区小控件（对应 .dlg-h / .dlg-row）
    def section(self, y, text, gap=0):
        """青色小节标题，返回下一行的 y。"""
        y += gap
        self.cv.create_text(self.body_x, y, text=text, fill=C_ACCENT,
                            font=self.f["sec"], anchor="nw")
        return y + self.f["sec"].metrics("linespace") + 7

    def row_height(self):
        return self.f["row"].metrics("linespace") + 9      # .dlg-row padding 4.5px 0

    def row_bg(self, x, y, width, tag, fill):
        """选中/隔行底色，贴在文字背后。"""
        rect(self.cv, x, y + 1, x + width, y + self.row_height() - 1,
             fill=fill, outline="", tags=(tag,))

    def option(self, x, y, text, kind, on, tag, width=0, fill=None):
        """一行单选框/勾选框 + 文字，返回下一行的 y。"""
        h = self.row_height()
        if fill:
            self.row_bg(x, y, width, tag, fill)
        cy = y + h / 2
        if kind == "check":
            draw_check(self.cv, x + 6, cy - CK / 2, on, tag)
        else:
            draw_radio(self.cv, x + 6, cy - CK / 2, on, tag)
        self.cv.create_text(x + 6 + CK + 10, cy, text=text, fill=C_SUB,
                            font=self.f["row"], anchor="w", tags=(tag,))
        return y + h

    # ---- 点击热区：画布统一做命中判定，避免空填充元素收不到点击
    def zone(self, x0, y0, x1, y1, command):
        if not self._zones:
            self.cv.bind("<Button-1>", self._hit_click, add="+")
            self.cv.bind("<Motion>", self._hit_motion, add="+")
        self._zones.append((x0, y0, x1, y1, command))

    def _zone_at(self, x, y):
        for z in reversed(self._zones):
            if z[0] <= x < z[2] and z[1] <= y < z[3]:
                return z[4]
        return None

    def _hit_click(self, event):
        fn = self._zone_at(event.x, event.y)
        if fn:
            fn()

    def _hit_motion(self, event):
        self.cv.configure(cursor="hand2" if self._zone_at(event.x, event.y) else "")

    def _press(self, event):
        self._off = ((event.x_root - self.win.winfo_x(),
                      event.y_root - self.win.winfo_y()) if event.y < TITLE_H else None)

    def _move(self, event):
        if not self._off:
            return
        self.win.geometry(f"+{int(event.x_root - self._off[0])}"
                          f"+{int(event.y_root - self._off[1])}")

    def button(self, right_x, y, text, primary, command):
        """右下角小按钮，返回下一个按钮可用的右边界。"""
        f = self.f["btn"]
        w = f.measure(text) + 30
        h = 24
        x = right_x - w
        tag = f"btn-{text}"
        rect(self.cv, x, y, x + w, y + h,
             fill=C_ACCENT_DEEP if primary else C_BTN,
             outline="", tags=(tag, "body"))
        self.cv.create_text(x + w / 2, y + h / 2, text=text,
                            fill="#ffffff" if primary else C_SUB,
                            font=f, tags=(tag, "body"))
        self.cv.tag_bind(tag, "<Button-1>", lambda e: command())
        hover(self.cv, tag)
        return x - 8

    def show(self, anchor, dy=40):
        self.win.update_idletasks()
        px = anchor.winfo_x() + (anchor.winfo_width() - self.width) // 2
        py = anchor.winfo_y() + dy
        vx, vy, vw, vh = virtual_screen()
        px = max(vx + 4, min(px, vx + vw - self.width - 4))
        py = max(vy + 4, min(py, vy + vh - self.height - 4))
        self.win.geometry(f"{self.width}x{self.height}+{int(px)}+{int(py)}")
        self.win.grab_set()
        self.win.focus_force()

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.win.grab_release()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass


HOVER_BG = "#283c40"        # .mi:hover 的 rgba(127,230,196,.13) 叠在 panel-2 上


class CanvasMenu:
    """右键菜单：画布绘制的深色圆角菜单，支持二级子菜单（对齐 .menu / .mi）。"""

    PAD = 5             # .menu padding
    GAP = 9             # .mi gap
    CK = 13
    AR = 9              # ▸ 的占位宽
    MIN_W = 190         # .menu min-width
    MID = 12            # .mi 左右 padding

    def __init__(self, app, items):
        self.app = app
        self.f = app.dlg_fonts["row"]
        self.fs = app.dlg_fonts["btn"]
        self.items = items
        self.panels = []
        self.item_h = self.f.metrics("linespace") + 10     # .mi padding 6px 12px
        self.sep_h = 11                                    # .mdiv 1px + margin 5px
        self._job = None
        self.root_panel = None

    # ---- 量出面板尺寸
    def _size(self, items):
        w = 0
        for it in items:
            if it[0] != "item":
                continue
            opts = it[2]
            lw = self.MID * 2
            if opts.get("arrow"):
                lw += self.AR + self.GAP
            if opts.get("check") is not None or opts.get("radio") is not None:
                lw += self.CK + self.GAP
            w = max(w, lw + self.f.measure(it[1]))
        w = max(w + self.PAD * 2, self.MIN_W)
        h = self.PAD * 2 + sum(self.sep_h if it[0] == "sep" else self.item_h
                               for it in items)
        return w, h

    def _panel(self, items, parent, x, y):
        w, h = self._size(items)
        vx, vy, vw, vh = virtual_screen()
        if x + w > vx + vw - 4:
            x = (parent["x"] - w + 1) if parent else vx + vw - w - 4
        x = max(vx + 4, min(x, vx + vw - w - 4))
        y = max(vy + 4, min(y, vy + vh - h - 4))

        win = tk.Toplevel(self.app.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=TRANSPARENT_KEY)
        try:
            win.attributes("-transparentcolor", TRANSPARENT_KEY)
        except Exception:
            pass
        cv = tk.Canvas(win, bg=TRANSPARENT_KEY, highlightthickness=0, bd=0,
                       width=w, height=h)
        cv.pack()
        win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
        # 置顶必须放在 geometry 之后：Windows 上先设 -topmost 再改窗口位置/尺寸，
        # 会把窗口从顶层 Z 序里踢出去，结果被同样置顶的主窗口压住一部分。
        self._raise(win)

        p = {"win": win, "cv": cv, "items": items, "x": int(x), "y": int(y),
             "w": w, "h": h, "child": None, "parent": parent}
        self.panels.append(p)
        self._draw(p)
        cv.bind("<Motion>", lambda e, pp=p: self._on_motion(pp, e))
        cv.bind("<Button-1>", lambda e, pp=p: self._on_click(pp, e))
        cv.bind("<Leave>", lambda e, pp=p: self._schedule_close())
        win.bind("<Escape>", lambda e: self.close())
        return p

    @staticmethod
    def _raise(win):
        """把面板顶到最上层。

        Windows 上多个 `-topmost` 窗口之间仍有先后：先建的在下面。主窗口也置顶，
        所以菜单必须显式再置一次顶并 lift，否则会被主窗口盖住一部分。
        """
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        try:
            win.lift()
        except Exception:
            pass

    # ---- 画一整张面板
    def _draw(self, p):
        cv, items = p["cv"], p["items"]
        w, h = p["w"], p["h"]
        cv.delete("all")
        rect(cv, 0, 0, w, h, fill=C_PANEL_2, outline=C_LINE, width=BORDER)
        y = self.PAD
        for i, it in enumerate(items):
            tag = f"mi{i}"
            if it[0] == "sep":
                cv.create_line(self.PAD + 8, y + 5, w - self.PAD - 8, y + 5,
                               fill=C_LINE, tags=(tag,))
                y += self.sep_h
                continue
            _, label, opts = it
            r = (self.PAD, y, w - self.PAD, y + self.item_h)
            if p.get("hot") == i:
                rect(cv, *r, fill=HOVER_BG, outline="", tags=(tag,))
            cx = self.PAD + self.MID
            cy = y + self.item_h / 2
            if opts.get("arrow"):
                cv.create_text(cx + self.AR / 2, cy, text="▸", fill=C_MUTED,
                               font=self.fs, tags=(tag,))
                cx += self.AR + self.GAP
            on = opts.get("check")
            on = on() if callable(on) else on
            if opts.get("radio") is not None:
                on = opts["radio"]() if callable(opts["radio"]) else opts["radio"]
                draw_radio(cv, cx, cy - self.CK / 2, bool(on), tag)
                cx += self.CK + self.GAP
            elif on is not None:
                draw_check(cv, cx, cy - self.CK / 2, bool(on), tag)
                cx += self.CK + self.GAP
            cv.create_text(cx, cy, text=label,
                           fill=C_MUTED if opts.get("disabled") else C_SUB,
                           font=self.f, anchor="w", tags=(tag,))
            y += self.item_h

    # ---- 悬停：高亮 + 展开/收起子菜单
    def _on_motion(self, p, event):
        self._cancel_close()
        idx = None
        y = self.PAD
        for i, it in enumerate(p["items"]):
            hh = self.sep_h if it[0] == "sep" else self.item_h
            if it[0] == "item" and y <= event.y < y + hh:
                idx = i
                break
            y += hh
        if idx == p.get("hot"):
            return
        p["hot"] = idx
        self._draw(p)
        cv = p["cv"]
        # 纯展示项（disabled）不给手型，也不该有"能点"的暗示
        hot = idx is not None and not p["items"][idx][2].get("disabled")
        cv.configure(cursor="hand2" if hot else "")
        # 关掉这条链上更深的子菜单
        self._drop_children(p)
        if idx is not None:
            it = p["items"][idx]
            sub = it[2].get("sub")
            if sub:
                sy = p["y"] + self.PAD + sum(
                    self.sep_h if x[0] == "sep" else self.item_h
                    for x in p["items"][:idx])
                p["child"] = self._panel(sub, p, p["x"] + p["w"] - 1, sy)

    def _on_click(self, p, event):
        self._cancel_close()
        y = self.PAD
        for i, it in enumerate(p["items"]):
            hh = self.sep_h if it[0] == "sep" else self.item_h
            if it[0] == "item" and y <= event.y < y + hh:
                opts = it[2]
                if opts.get("sub") or opts.get("disabled"):
                    return
                cmd = opts.get("cmd")
                # 勾选 / 单选是「连续设置」类操作：点了不关菜单，方便连着勾好几项。
                # 只有真正要跳走的动作项（打开弹窗、退出）才收起来。
                if opts.get("check") is not None or opts.get("radio") is not None:
                    if cmd:
                        cmd()
                    self._refresh()
                    return
                self.close()
                if cmd:
                    cmd()
                return
            y += hh
        self.close()

    def _drop_children(self, p):
        q = p.get("child")
        while q:
            self._drop_children(q)
            self._destroy(q)
            q = q.get("child")
        p["child"] = None

    def _destroy(self, p):
        try:
            p["win"].destroy()
        except Exception:
            pass
        if p in self.panels:
            self.panels.remove(p)

    def _refresh(self):
        """勾选状态变了以后重画所有面板（check/radio 是 callable，会重新求值）。

        顺带再置一次顶：勾选会触发主窗口重绘/自适应改 geometry，有可能把菜单
        重新压到下面去。
        """
        for p in self.panels:
            try:
                self._draw(p)
                self._raise(p["win"])
            except Exception:
                pass

    # ---- 点到菜单以外的地方就收起来
    def _schedule_close(self):
        self._cancel_close()
        self._job = self.app.root.after(120, self._maybe_close)

    def _cancel_close(self):
        if self._job is not None:
            try:
                self.app.root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _maybe_close(self):
        self._job = None
        x, y = self.app.root.winfo_pointerxy()
        for p in self.panels:
            if p["x"] <= x < p["x"] + p["w"] and p["y"] <= y < p["y"] + p["h"]:
                return
        self.close()

    def popup(self, x, y):
        self.root_panel = self._panel(self.items, None, x, y)
        self.root_panel["win"].focus_force()
        for p in self.panels:
            self._raise(p["win"])
            p["win"].bind("<FocusOut>", lambda e: self._schedule_close())

    def close(self):
        self._cancel_close()
        for p in self.panels[:]:
            self._destroy(p)
        self.panels = []
