#!/usr/bin/env python3
"""Cursor SAND 模式切换工具 - 图形界面版（现代深色 UI，纯标准库 tkinter/ttk）。"""

from __future__ import annotations

import ctypes
import io
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, ".sand_gui_config.json")

if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import cursor_sand_min as sand  # noqa: E402
import license_gate  # noqa: E402  (授权校验: license_web.html 生成密钥, 过期锁定)

# 子进程不弹黑窗（pythonw 启动时尤其明显）
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


# ---------------------------------------------------------------- 业务逻辑


def is_valid_app_dir(path: str) -> bool:
    if not path:
        return False
    return os.path.isfile(os.path.join(path.strip().strip('"'), sand.PRODUCT))


def load_config() -> dict:
    default = {"use_custom_path": False, "custom_app_dir": ""}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            default.update({k: data[k] for k in default if k in data})
    except (OSError, json.JSONDecodeError):
        pass
    return default


def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def resolve_app_dir(config: dict) -> tuple[str | None, str]:
    """根据配置解析 Cursor 路径：默认自动读取，可选用户自定义。"""
    if config.get("use_custom_path"):
        custom = (config.get("custom_app_dir") or "").strip().strip('"')
        if is_valid_app_dir(custom):
            return os.path.normpath(custom), "用户自定义配置"
        return None, "用户自定义路径无效"

    path, source = sand.detect_app_dir_with_source()
    if path:
        return path, source
    return None, ""


def is_admin() -> bool:
    if sys.platform != "win32":
        return os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> None:
    if sys.platform != "win32":
        return
    params = " ".join(f'"{arg}"' for arg in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1
    )


def kill_cursor_processes() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/IM", "Cursor.exe"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
        )
        check = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Cursor.exe"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
        )
        still_running = "Cursor.exe" in check.stdout
        if still_running:
            return False, "仍有 Cursor 进程未退出，请在任务管理器中手动结束。"
        if result.returncode in (0, 128):
            return True, "Cursor 进程已关闭。"
        return False, result.stderr.strip() or result.stdout.strip() or "关闭 Cursor 失败。"
    except Exception as exc:
        return False, str(exc)


def collect_status(app_dir: str) -> dict:
    """读取 4 个目标文件的 SAND/IDE 状态与 product.json 完整性校验。

    纯计算，不碰任何界面对象，可安全放到后台线程执行。
    """
    result: dict = {
        "error": "",
        "rows": [],
        "sand": 0,
        "ide": 0,
        "bad": 0,
        "missing": 0,
    }
    try:
        with open(os.path.join(app_dir, sand.PRODUCT), encoding="utf-8") as f:
            product = json.load(f)
        checksums = product.get("checksums", {})
    except Exception as exc:
        result["error"] = f"读取 product.json 失败: {exc}"
        return result

    for rel in sand.TARGETS:
        path = os.path.join(app_dir, *rel.split("/"))
        if not os.path.isfile(path):
            result["rows"].append({"rel": rel, "state": "MISSING", "check": "NA"})
            result["missing"] += 1
            continue
        try:
            text = sand.read(path)
        except Exception as exc:
            result["rows"].append(
                {"rel": rel, "state": "ERROR", "check": "NA", "note": str(exc)}
            )
            continue

        state = "SAND" if sand.is_patched(text) else "IDE"
        del text
        if state == "SAND":
            result["sand"] += 1
        else:
            result["ide"] += 1

        check = "NA"
        for ck_rel, expected in checksums.items():
            if ck_rel.endswith(os.path.basename(rel)):
                fp = os.path.join(app_dir, "out", *ck_rel.split("/"))
                if os.path.isfile(fp):
                    try:
                        ok = sand.vscode_checksum(fp) == expected
                    except Exception:
                        ok = False
                    check = "OK" if ok else "BAD"
                    if not ok:
                        result["bad"] += 1
                break
        result["rows"].append({"rel": rel, "state": state, "check": check})

    return result


def summary_text(result: dict) -> str:
    """保持与命令行/旧界面一致的汇总措辞。"""
    total = len(sand.TARGETS)
    if result["sand"] == total:
        summary = "当前模式：SAND（已全部开启）"
    elif result["ide"] == total:
        summary = "当前模式：IDE（默认状态）"
    else:
        summary = f"当前模式：混合（SAND {result['sand']} / IDE {result['ide']}）"
    if result["bad"]:
        summary += f"  |  ⚠ {result['bad']} 项校验不匹配"
    return summary


# ---------------------------------------------------------------- 主题


class C:
    """深色配色（GitHub Dark 风格，偏冷蓝）。"""

    BG = "#0D1117"
    CARD = "#161B22"
    CARD_SOFT = "#1B212B"
    FIELD = "#0B0F16"
    SURFACE_2 = "#1F2630"
    SURFACE_3 = "#2A323E"
    BORDER = "#232B36"
    BORDER_HI = "#323C4B"

    TEXT = "#E6EDF3"
    TEXT_DIM = "#AEB9C7"
    MUTED = "#7D8899"
    FAINT = "#5A6474"

    ACCENT = "#4C8DFF"
    ACCENT_HI = "#6BA1FF"
    ACCENT_LO = "#3B76E0"

    OK = "#3FD07A"
    OK_BG = "#10291C"
    OK_BORDER = "#1E5236"

    WARN = "#E3B341"
    WARN_BG = "#2B2410"
    WARN_BORDER = "#5C4A17"

    DANGER = "#FF6B63"
    DANGER_BG = "#2C1517"
    DANGER_BORDER = "#5E2326"

    INFO_BG = "#101E33"
    INFO_BORDER = "#1F3C68"

    DISABLED_BG = "#1A1F27"
    DISABLED_FG = "#525C6B"


TONES = {
    "ok": (C.OK, C.OK_BG, C.OK_BORDER),
    "warn": (C.WARN, C.WARN_BG, C.WARN_BORDER),
    "danger": (C.DANGER, C.DANGER_BG, C.DANGER_BORDER),
    "info": (C.ACCENT_HI, C.INFO_BG, C.INFO_BORDER),
    "neutral": (C.TEXT_DIM, C.SURFACE_2, C.BORDER_HI),
    "muted": (C.MUTED, C.SURFACE_2, C.BORDER),
}

UI_SCALE = 1.0
FONTS: dict[str, tkfont.Font] = {}


def sc(value: float) -> int:
    """把 96dpi 下设计的像素值换算到当前 DPI。"""
    if not value:
        return 0
    return max(1, int(round(value * UI_SCALE)))


def enable_dpi_awareness() -> float:
    """开启高 DPI 感知，返回系统 DPI。必须在创建 Tk 之前调用。"""
    if sys.platform != "win32":
        return 96.0
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        dpi = float(ctypes.windll.user32.GetDpiForSystem())
    except Exception:
        dpi = 0.0
    if dpi <= 0:
        try:
            hdc = ctypes.windll.user32.GetDC(0)
            dpi = float(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))
            ctypes.windll.user32.ReleaseDC(0, hdc)
        except Exception:
            dpi = 96.0
    return dpi if dpi > 0 else 96.0


def enable_dark_titlebar(window: tk.Misc) -> None:
    """Windows 10/11 深色标题栏，失败静默降级为系统默认。"""
    if sys.platform != "win32":
        return
    try:
        window.update_idletasks()
        handles = []
        own = window.winfo_id()
        parent = ctypes.windll.user32.GetParent(own)
        if parent:
            handles.append(parent)
        handles.append(own)
        flag = ctypes.c_int(1)
        for hwnd in handles:
            for attribute in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd),
                    ctypes.c_int(attribute),
                    ctypes.byref(flag),
                    ctypes.sizeof(flag),
                )
    except Exception:
        pass


def init_fonts() -> None:
    families = set(tkfont.families())
    fallback = tkfont.nametofont("TkDefaultFont").actual("family")

    def pick(*names: str) -> str:
        for name in names:
            if name in families:
                return name
        return fallback

    ui = pick("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI")
    mono = pick("Consolas", "Cascadia Mono", "Cascadia Code", "Courier New")

    FONTS.update(
        {
            "h1": tkfont.Font(family=ui, size=17, weight="bold"),
            "h2": tkfont.Font(family=ui, size=11, weight="bold"),
            "body": tkfont.Font(family=ui, size=10),
            "small": tkfont.Font(family=ui, size=9),
            "btn": tkfont.Font(family=ui, size=10, weight="bold"),
            "btn_sm": tkfont.Font(family=ui, size=9),
            "badge": tkfont.Font(family=ui, size=9, weight="bold"),
            "logo": tkfont.Font(family=ui, size=15, weight="bold"),
            "mono": tkfont.Font(family=mono, size=10),
            "mono_sm": tkfont.Font(family=mono, size=9),
        }
    )


def setup_ttk(root: tk.Misc) -> None:
    """只有进度条和滚动条用 ttk —— clam 主题才允许自定义配色。"""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(
        "Sand.Horizontal.TProgressbar",
        troughcolor=C.SURFACE_2,
        bordercolor=C.SURFACE_2,
        background=C.ACCENT,
        lightcolor=C.ACCENT,
        darkcolor=C.ACCENT,
        thickness=sc(4),
    )
    try:
        style.layout(
            "Sand.Vertical.TScrollbar",
            [
                (
                    "Vertical.Scrollbar.trough",
                    {
                        "sticky": "ns",
                        "children": [
                            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})
                        ],
                    },
                )
            ],
        )
    except tk.TclError:
        pass
    style.configure(
        "Sand.Vertical.TScrollbar",
        troughcolor=C.FIELD,
        bordercolor=C.FIELD,
        background=C.SURFACE_3,
        darkcolor=C.SURFACE_3,
        lightcolor=C.SURFACE_3,
        arrowcolor=C.MUTED,
        relief="flat",
        width=sc(10),
    )
    style.map(
        "Sand.Vertical.TScrollbar",
        background=[("pressed", C.ACCENT), ("active", C.BORDER_HI)],
    )


# ---------------------------------------------------------------- 绘图基元


def round_points(x1: float, y1: float, x2: float, y2: float, r: float) -> list[float]:
    """给 create_polygon(smooth=True) 用的圆角矩形控制点。"""
    r = max(0.0, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    return [
        x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1,
        x2, y1, x2, y1 + r, x2, y1 + r, x2, y2 - r,
        x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2,
        x1 + r, y2, x1 + r, y2, x1, y2, x1, y2 - r,
        x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1,
    ]


def bg_of(widget: tk.Misc) -> str:
    try:
        return str(widget.cget("bg"))
    except Exception:
        return C.BG


def label(master: tk.Misc, text: str, *, font: str = "body", fg: str = C.TEXT, **kw) -> tk.Label:
    kw.setdefault("bg", bg_of(master))
    return tk.Label(master, text=text, font=FONTS[font], fg=fg, **kw)


def hsep(master: tk.Misc, color: str = C.BORDER) -> tk.Frame:
    return tk.Frame(master, bg=color, height=1)


class Card(tk.Canvas):
    """圆角卡片：Canvas 画背景，内容放在 self.body 这个普通 Frame 里。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        stretch: bool = False,
        radius: int = 14,
        padx: int = 18,
        pady: int = 16,
        fill: str = C.CARD,
        outline: str = C.BORDER,
        accent: str | None = None,
        min_height: int = 40,
    ) -> None:
        super().__init__(
            master,
            bg=bg_of(master),
            highlightthickness=0,
            bd=0,
            width=sc(520),
            height=sc(min_height),
        )
        self.stretch = stretch
        self.radius = sc(radius)
        self.pad_x = sc(padx)
        self.pad_y = sc(pady)
        self.fill = fill
        self.outline = outline
        self.accent = accent
        self._redraw_pending = False
        self._req_height = sc(min_height)

        self.body = tk.Frame(self, bg=fill)
        self._window = self.create_window(self.pad_x, self.pad_y, window=self.body, anchor="nw")
        self.bind("<Configure>", self._on_configure)
        self.body.bind("<Configure>", self._on_body_configure)

    def _on_configure(self, event: tk.Event) -> None:
        self.itemconfigure(self._window, width=max(1, event.width - self.pad_x * 2))
        if self.stretch:
            self.itemconfigure(self._window, height=max(1, event.height - self.pad_y * 2))
        self._schedule_redraw()

    def _on_body_configure(self, event: tk.Event) -> None:
        if not self.stretch:
            wanted = event.height + self.pad_y * 2
            if wanted != self._req_height:
                self._req_height = wanted
                self.configure(height=wanted)
        self._schedule_redraw()

    def sync_height(self) -> None:
        """立刻把卡片高度对齐到内容高度，不用等 <Configure> 事件。"""
        if self.stretch:
            return
        wanted = self.body.winfo_reqheight() + self.pad_y * 2
        if wanted != self._req_height:
            self._req_height = wanted
            self.configure(height=wanted)

    def _schedule_redraw(self) -> None:
        if self._redraw_pending:
            return
        self._redraw_pending = True
        self.after_idle(self._redraw)

    def _redraw(self) -> None:
        self._redraw_pending = False
        if not self.winfo_exists():
            return
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 2 or height <= 2:
            return
        self.delete("cardbg")
        self.create_polygon(
            round_points(1, 1, width - 1, height - 1, self.radius),
            smooth=True,
            splinesteps=24,
            fill=self.fill,
            outline=self.outline,
            width=1,
            tags="cardbg",
        )
        if self.accent:
            bar = sc(3)
            self.create_polygon(
                round_points(sc(2), sc(12), sc(2) + bar, height - sc(12), bar / 2),
                smooth=True,
                splinesteps=8,
                fill=self.accent,
                outline="",
                tags="cardbg",
            )


def sync_cards(widget: tk.Misc) -> None:
    """自内向外把所有卡片高度算准，供窗口初始定高使用。"""
    for child in widget.winfo_children():
        sync_cards(child)
    if isinstance(widget, Card):
        widget.sync_height()


class Badge(tk.Canvas):
    """彩色圆角徽章。"""

    def __init__(
        self,
        master: tk.Misc,
        text: str = "",
        tone: str = "neutral",
        *,
        font: str = "badge",
        padx: int = 9,
        pady: int = 3,
    ) -> None:
        self._font = FONTS[font]
        self._pad_x = sc(padx)
        self._pad_y = sc(pady)
        self._parent_bg = bg_of(master)
        self._text = text
        self._tone = tone
        self._cw = self._measure(text)
        self._ch = self._font.metrics("linespace") + self._pad_y * 2
        super().__init__(
            master,
            bg=self._parent_bg,
            highlightthickness=0,
            bd=0,
            width=self._cw,
            height=self._ch,
        )
        self._paint()

    def _measure(self, text: str) -> int:
        return max(sc(20), self._font.measure(text) + self._pad_x * 2)

    def set(self, text: str, tone: str | None = None) -> None:
        self._text = text
        if tone:
            self._tone = tone
        self._cw = self._measure(text)
        self.configure(width=self._cw)
        self._paint()

    def _paint(self) -> None:
        self.delete("all")
        width = self._cw
        height = self._ch
        fg, bg, border = TONES.get(self._tone, TONES["neutral"])
        self.create_polygon(
            round_points(0.5, 0.5, width - 0.5, height - 0.5, height / 2.0),
            smooth=True,
            splinesteps=16,
            fill=bg,
            outline=border,
            width=1,
        )
        self.create_text(width / 2, height / 2, text=self._text, fill=fg, font=self._font)


class PillButton(tk.Canvas):
    """自绘按钮：圆角 + 悬停/按下/禁用状态。"""

    PALETTE = {
        "primary": {
            "fill": C.ACCENT,
            "hover": C.ACCENT_HI,
            "press": C.ACCENT_LO,
            "fg": "#FFFFFF",
            "border": None,
        },
        "secondary": {
            "fill": C.SURFACE_2,
            "hover": C.SURFACE_3,
            "press": C.SURFACE_2,
            "fg": C.TEXT,
            "border": C.BORDER_HI,
        },
        "ghost": {
            "fill": None,
            "hover": C.SURFACE_2,
            "press": C.SURFACE_2,
            "fg": C.TEXT_DIM,
            "border": None,
        },
        "warn": {
            "fill": C.WARN_BG,
            "hover": "#3A3116",
            "press": C.WARN_BG,
            "fg": C.WARN,
            "border": C.WARN_BORDER,
        },
    }

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command=None,
        *,
        kind: str = "primary",
        font: str = "btn",
        padx: int = 20,
        pady: int = 11,
        radius: int = 9,
        min_width: int = 0,
    ) -> None:
        self.kind = kind if kind in self.PALETTE else "primary"
        self.command = command
        self._text = text
        self._font = FONTS[font]
        self._radius = sc(radius)
        self._parent_bg = bg_of(master)
        self._hover = False
        self._pressed = False
        self._state = "normal"

        self._cw = max(sc(min_width), self._font.measure(text) + sc(padx) * 2)
        self._ch = self._font.metrics("linespace") + sc(pady) * 2
        super().__init__(
            master,
            bg=self._parent_bg,
            highlightthickness=0,
            bd=0,
            width=self._cw,
            height=self._ch,
            cursor="hand2",
        )
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._paint()

    def set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        if state == "disabled":
            self._hover = False
            self._pressed = False
        self.configure(cursor="" if state == "disabled" else "hand2")
        self._paint()

    def _on_enter(self, _event: tk.Event) -> None:
        if self._state != "disabled":
            self._hover = True
            self._paint()

    def _on_leave(self, _event: tk.Event) -> None:
        self._hover = False
        self._pressed = False
        self._paint()

    def _on_press(self, _event: tk.Event) -> None:
        if self._state != "disabled":
            self._pressed = True
            self._paint()

    def _on_release(self, _event: tk.Event) -> None:
        was_pressed = self._pressed
        self._pressed = False
        self._paint()
        if was_pressed and self._state != "disabled" and self.command:
            self.command()

    def _paint(self) -> None:
        self.delete("all")
        width = self._cw
        height = self._ch
        palette = self.PALETTE[self.kind]

        if self._state == "disabled":
            fill, fg, border = C.DISABLED_BG, C.DISABLED_FG, C.BORDER
        else:
            if self._pressed:
                fill = palette["press"]
            elif self._hover:
                fill = palette["hover"]
            else:
                fill = palette["fill"]
            fg = palette["fg"]
            border = palette["border"]
            if self._hover and self.kind == "ghost":
                fg = C.TEXT
        if fill is None:
            fill = self._parent_bg

        self.create_polygon(
            round_points(0.5, 0.5, width - 0.5, height - 0.5, self._radius),
            smooth=True,
            splinesteps=20,
            fill=fill,
            outline=border or fill,
            width=1,
        )
        self.create_text(width / 2, height / 2, text=self._text, fill=fg, font=self._font)


class Switch(tk.Canvas):
    """自绘开关，替代样式不可控的 tk.Checkbutton。"""

    def __init__(
        self,
        master: tk.Misc,
        variable: tk.BooleanVar,
        command=None,
        *,
        width: int = 40,
        height: int = 22,
    ) -> None:
        self.variable = variable
        self.command = command
        self._state = "normal"
        self._cw = sc(width)
        self._ch = sc(height)
        super().__init__(
            master,
            bg=bg_of(master),
            highlightthickness=0,
            bd=0,
            width=self._cw,
            height=self._ch,
            cursor="hand2",
        )
        self.bind("<Button-1>", self._on_click)
        self._paint()

    def set_state(self, state: str) -> None:
        self._state = state
        self.configure(cursor="" if state == "disabled" else "hand2")
        self._paint()

    def toggle(self) -> None:
        if self._state == "disabled":
            return
        self.variable.set(not self.variable.get())
        self._paint()
        if self.command:
            self.command()

    def _on_click(self, _event: tk.Event) -> None:
        self.toggle()

    def refresh(self) -> None:
        self._paint()

    def _paint(self) -> None:
        self.delete("all")
        on = bool(self.variable.get())
        disabled = self._state == "disabled"
        if disabled:
            track = C.DISABLED_BG
            border = C.BORDER
            knob = C.DISABLED_FG
        elif on:
            track = C.ACCENT
            border = C.ACCENT
            knob = "#FFFFFF"
        else:
            track = C.SURFACE_2
            border = C.BORDER_HI
            knob = C.MUTED

        self.create_polygon(
            round_points(0.5, 0.5, self._cw - 0.5, self._ch - 0.5, self._ch / 2.0),
            smooth=True,
            splinesteps=16,
            fill=track,
            outline=border,
            width=1,
        )
        r = self._ch / 2.0 - sc(3.5)
        cx = self._cw - self._ch / 2.0 if on else self._ch / 2.0
        cy = self._ch / 2.0
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=knob, outline="")


class LogoMark(tk.Canvas):
    """标题左侧的圆角标识块。"""

    def __init__(self, master: tk.Misc, size: int = 38) -> None:
        self._size = sc(size)
        super().__init__(
            master,
            bg=bg_of(master),
            highlightthickness=0,
            bd=0,
            width=self._size,
            height=self._size,
        )
        self.create_polygon(
            round_points(0.5, 0.5, self._size - 0.5, self._size - 0.5, sc(10)),
            smooth=True,
            splinesteps=20,
            fill=C.ACCENT,
            outline=C.ACCENT_HI,
            width=1,
        )
        self.create_text(
            self._size / 2,
            self._size / 2,
            text="S",
            fill="#FFFFFF",
            font=FONTS["logo"],
        )


class TargetRow:
    """状态表格里的一行：文件名 + 模式徽章 + 校验徽章。"""

    def __init__(self, master: tk.Misc, rel: str) -> None:
        self.rel = rel
        self.frame = tk.Frame(master, bg=bg_of(master))

        head, _, base = rel.rpartition("/")
        self.dot = tk.Canvas(
            self.frame,
            bg=bg_of(master),
            highlightthickness=0,
            bd=0,
            width=sc(14),
            height=sc(14),
        )
        self._dot_item = self.dot.create_oval(
            sc(4), sc(4), sc(10), sc(10), fill=C.FAINT, outline=""
        )
        self.dot.pack(side="left", padx=(0, sc(8)))

        self.check_badge = Badge(self.frame, "—", "muted")
        self.check_badge.pack(side="right")
        self.state_badge = Badge(self.frame, "—", "muted")
        self.state_badge.pack(side="right", padx=(0, sc(8)))

        label(self.frame, (head + "/") if head else "", font="mono_sm", fg=C.FAINT).pack(side="left")
        label(self.frame, base, font="mono_sm", fg=C.TEXT_DIM).pack(side="left")

    def update(self, info: dict | None) -> None:
        if not info:
            self.dot.itemconfigure(self._dot_item, fill=C.FAINT)
            self.state_badge.set("—", "muted")
            self.check_badge.set("—", "muted")
            return

        state = info.get("state", "")
        if state == "SAND":
            self.dot.itemconfigure(self._dot_item, fill=C.OK)
            self.state_badge.set("SAND", "ok")
        elif state == "IDE":
            self.dot.itemconfigure(self._dot_item, fill=C.MUTED)
            self.state_badge.set("IDE", "neutral")
        elif state == "MISSING":
            self.dot.itemconfigure(self._dot_item, fill=C.DANGER)
            self.state_badge.set("缺失", "danger")
        else:
            self.dot.itemconfigure(self._dot_item, fill=C.WARN)
            self.state_badge.set("读取失败", "warn")

        check = info.get("check", "NA")
        if check == "OK":
            self.check_badge.set("校验 OK", "ok")
        elif check == "BAD":
            self.check_badge.set("校验不匹配", "warn")
        else:
            self.check_badge.set("无校验项", "muted")


# ---------------------------------------------------------------- 深色对话框


class ModalDialog(tk.Toplevel):
    ICONS = {
        "info": ("i", "info"),
        "question": ("?", "info"),
        "ok": ("✓", "ok"),
        "warn": ("!", "warn"),
        "error": ("✕", "danger"),
    }

    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        message: str,
        tone: str = "info",
        detail: str = "",
        buttons: list[tuple[str, object, str]] | None = None,
        default: object = True,
        cancel: object = False,
    ) -> None:
        super().__init__(parent, bg=C.BG)
        self.result: object = cancel
        self._cancel_value = cancel
        self._default_value = default

        self.withdraw()
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        glyph, glyph_tone = self.ICONS.get(tone, self.ICONS["info"])
        fg, bg, border = TONES[glyph_tone]

        outer = tk.Frame(self, bg=C.BG)
        outer.pack(fill="both", expand=True, padx=sc(22), pady=sc(20))

        head = tk.Frame(outer, bg=C.BG)
        head.pack(fill="x")

        mark = tk.Canvas(head, bg=C.BG, highlightthickness=0, bd=0, width=sc(30), height=sc(30))
        mark.create_oval(0, 0, sc(29), sc(29), fill=bg, outline=border, width=1)
        mark.create_text(sc(15), sc(15), text=glyph, fill=fg, font=FONTS["btn"])
        mark.pack(side="left", padx=(0, sc(12)), anchor="n")

        text_col = tk.Frame(head, bg=C.BG)
        text_col.pack(side="left", fill="x", expand=True)
        label(text_col, title, font="h2", fg=C.TEXT).pack(anchor="w")
        label(
            text_col,
            message,
            font="body",
            fg=C.TEXT_DIM,
            justify="left",
            wraplength=sc(400),
        ).pack(anchor="w", pady=(sc(6), 0))

        if detail:
            box = tk.Frame(outer, bg=C.FIELD, highlightthickness=1, highlightbackground=C.BORDER)
            box.pack(fill="x", pady=(sc(14), 0))
            label(
                box,
                detail,
                font="mono_sm",
                fg=C.MUTED,
                justify="left",
                wraplength=sc(400),
            ).pack(anchor="w", padx=sc(10), pady=sc(8))

        row = tk.Frame(outer, bg=C.BG)
        row.pack(fill="x", pady=(sc(20), 0))
        for text, value, kind in reversed(buttons or [("确定", True, "primary")]):
            PillButton(
                row,
                text,
                command=lambda v=value: self._finish(v),
                kind=kind,
                padx=18,
                pady=9,
                min_width=88,
            ).pack(side="right", padx=(sc(8), 0))

        self.bind("<Escape>", lambda _e: self._on_cancel())
        self.bind("<Return>", lambda _e: self._finish(self._default_value))

        enable_dark_titlebar(self)
        self._center_on(parent)
        self.deiconify()
        self.lift()
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.wait_window(self)

    def _center_on(self, parent: tk.Misc) -> None:
        self.update_idletasks()
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            if pw <= 1 or ph <= 1:
                raise ValueError
        except Exception:
            px = py = 0
            pw, ph = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, px + (pw - width) // 2)
        y = max(0, py + (ph - height) // 3)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _finish(self, value: object) -> None:
        self.result = value
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _on_cancel(self) -> None:
        self._finish(self._cancel_value)


def _dialog(parent, *, title, message, tone, detail="", buttons=None, default=True, cancel=False):
    try:
        return ModalDialog(
            parent,
            title=title,
            message=message,
            tone=tone,
            detail=detail,
            buttons=buttons,
            default=default,
            cancel=cancel,
        ).result
    except Exception:
        body = message + (f"\n\n{detail}" if detail else "")
        if buttons and len(buttons) > 1:
            return messagebox.askyesno(title, body)
        if tone in ("error", "warn"):
            messagebox.showerror(title, body)
        else:
            messagebox.showinfo(title, body)
        return True


def show_info(parent, title, message, detail="", tone="ok") -> None:
    _dialog(parent, title=title, message=message, tone=tone, detail=detail,
            buttons=[("好的", True, "primary")])


def show_error(parent, title, message, detail="") -> None:
    _dialog(parent, title=title, message=message, tone="error", detail=detail,
            buttons=[("知道了", True, "secondary")])


def ask_confirm(parent, title, message, detail="", yes="继续", no="取消", tone="question") -> bool:
    return bool(
        _dialog(
            parent,
            title=title,
            message=message,
            tone=tone,
            detail=detail,
            buttons=[(no, False, "secondary"), (yes, True, "primary")],
            default=True,
            cancel=False,
        )
    )


# ---------------------------------------------------------------- 应用窗口


class SandGuiApp:
    HINT_IDLE = "就绪"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.busy = False
        self.scanning = False
        self.config = load_config()
        self.current_path: str | None = None
        self.current_source = ""
        self.advanced_open = bool(self.config.get("use_custom_path"))
        self._icon = None
        self._rescan_pending = False
        self._rescan_log_path = False

        root.title("Cursor SAND 模式切换工具")
        root.configure(bg=C.BG)

        self._build_ui()
        self._apply_window_chrome()

    # ---------------------------------------------------------- 界面搭建

    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, bg=C.BG)
        outer.pack(fill="both", expand=True, padx=sc(22), pady=sc(14))
        self._outer = outer

        self._build_header(outer)
        if not is_admin():
            self._build_admin_warning(outer)
        self._build_path_card(outer)
        self._build_status_card(outer)
        self._build_action_bar(outer)

        # 先占住底部一行，日志卡片才不会把它挤出窗口外
        label(
            outer,
            "提示：换电脑无需改配置，默认自动读取；Cursor 更新后需要重新开启 SAND。",
            font="small",
            fg=C.FAINT,
        ).pack(side="bottom", anchor="w", pady=(sc(8), 0))

        self._build_log_card(outer)

    def _build_header(self, parent: tk.Misc) -> None:
        header = tk.Frame(parent, bg=C.BG)
        header.pack(fill="x", pady=(0, sc(12)))

        LogoMark(header).pack(side="left", padx=(0, sc(12)))

        titles = tk.Frame(header, bg=C.BG)
        titles.pack(side="left", fill="x", expand=True)
        label(titles, "Cursor SAND 模式切换", font="h1").pack(anchor="w")
        label(
            titles,
            "把客户端类型在 ide / sand 之间切换，并同步 product.json 完整性校验",
            font="small",
            fg=C.MUTED,
        ).pack(anchor="w", pady=(sc(3), 0))

        if is_admin():
            Badge(header, "✓ 管理员", "ok").pack(side="right", pady=(sc(6), 0))
        else:
            Badge(header, "⚠ 非管理员", "warn").pack(side="right", pady=(sc(6), 0))

    def _build_admin_warning(self, parent: tk.Misc) -> None:
        card = Card(parent, fill=C.WARN_BG, outline=C.WARN_BORDER, accent=C.WARN,
                    padx=20, pady=13, radius=12)
        card.pack(fill="x", pady=(0, sc(12)))
        body = card.body

        text_col = tk.Frame(body, bg=card.fill)
        text_col.pack(side="left", fill="x", expand=True)
        label(text_col, "当前不是管理员权限", font="h2", fg=C.WARN).pack(anchor="w")
        label(
            text_col,
            "修改 Cursor 安装目录通常需要管理员权限，否则写入会失败。",
            font="small",
            fg=C.TEXT_DIM,
        ).pack(anchor="w", pady=(sc(3), 0))

        PillButton(
            body,
            "以管理员重启",
            command=self._relaunch_admin,
            kind="warn",
            font="btn_sm",
            padx=16,
            pady=9,
        ).pack(side="right", padx=(sc(12), 0))

    def _build_path_card(self, parent: tk.Misc) -> None:
        card = Card(parent, pady=14)
        card.pack(fill="x", pady=(0, sc(10)))
        body = card.body

        head = tk.Frame(body, bg=card.fill)
        head.pack(fill="x")
        label(head, "Cursor 安装位置", font="h2").pack(side="left")
        PillButton(
            head, "打开目录", command=self._open_app_dir, kind="ghost",
            font="btn_sm", padx=12, pady=6,
        ).pack(side="right")
        self.btn_relocate = PillButton(
            head, "重新读取", command=self._relocate, kind="secondary",
            font="btn_sm", padx=14, pady=6,
        )
        self.btn_relocate.pack(side="right", padx=(0, sc(8)))

        field = tk.Frame(body, bg=C.FIELD, highlightthickness=1,
                         highlightbackground=C.BORDER, highlightcolor=C.BORDER, bd=0)
        field.pack(fill="x", pady=(sc(10), sc(6)))
        self.path_display_var = tk.StringVar(value="正在自动读取…")
        self.path_display = tk.Entry(
            field,
            textvariable=self.path_display_var,
            state="readonly",
            font=FONTS["mono_sm"],
            readonlybackground=C.FIELD,
            bg=C.FIELD,
            fg=C.TEXT,
            disabledforeground=C.MUTED,
            insertbackground=C.TEXT,
            selectbackground=C.ACCENT_LO,
            selectforeground="#FFFFFF",
            relief="flat",
            bd=0,
            highlightthickness=0,
        )
        self.path_display.pack(fill="x", padx=sc(10), pady=sc(8))

        self.source_var = tk.StringVar(value="")
        self.source_label = label(body, "", font="small", fg=C.MUTED, textvariable=self.source_var)
        self.source_label.pack(anchor="w")

        hsep(body).pack(fill="x", pady=(sc(12), 0))
        self._build_advanced(body, card.fill)

    def _build_advanced(self, parent: tk.Misc, card_bg: str) -> None:
        toggle = tk.Frame(parent, bg=card_bg, cursor="hand2")
        toggle.pack(fill="x", pady=(sc(8), 0))
        self.chevron = label(toggle, "▸", font="small", fg=C.MUTED, bg=card_bg)
        self.chevron.pack(side="left", padx=(0, sc(7)))
        adv_label = label(
            toggle, "高级选项 · 手动指定 Cursor 路径", font="small", fg=C.TEXT_DIM, bg=card_bg
        )
        adv_label.pack(side="left")
        for widget in (toggle, self.chevron, adv_label):
            widget.bind("<Button-1>", lambda _e: self._toggle_advanced())

        self.advanced_holder = tk.Frame(parent, bg=card_bg)

        panel = Card(
            self.advanced_holder, fill=C.CARD_SOFT, outline=C.BORDER,
            padx=14, pady=12, radius=10,
        )
        panel.pack(fill="x", pady=(sc(10), 0))
        inner = panel.body

        switch_row = tk.Frame(inner, bg=panel.fill)
        switch_row.pack(fill="x")
        self.use_custom_var = tk.BooleanVar(value=bool(self.config.get("use_custom_path")))
        self.custom_switch = Switch(switch_row, self.use_custom_var, command=self._on_custom_toggle)
        self.custom_switch.pack(side="left", padx=(0, sc(10)))
        switch_text = label(
            switch_row,
            "使用自定义 Cursor 路径（resources/app）",
            font="small",
            fg=C.TEXT_DIM,
            bg=panel.fill,
            cursor="hand2",
        )
        switch_text.pack(side="left")
        switch_text.bind("<Button-1>", lambda _e: self.custom_switch.toggle())

        self.custom_field = tk.Frame(
            inner, bg=C.FIELD, highlightthickness=1,
            highlightbackground=C.BORDER, highlightcolor=C.ACCENT, bd=0,
        )
        self.custom_field.pack(fill="x", pady=(sc(10), sc(10)))
        self.custom_path_var = tk.StringVar(value=self.config.get("custom_app_dir", ""))
        self.custom_entry = tk.Entry(
            self.custom_field,
            textvariable=self.custom_path_var,
            font=FONTS["mono_sm"],
            bg=C.FIELD,
            fg=C.TEXT,
            disabledbackground=C.FIELD,
            disabledforeground=C.FAINT,
            insertbackground=C.ACCENT,
            selectbackground=C.ACCENT_LO,
            selectforeground="#FFFFFF",
            relief="flat",
            bd=0,
            highlightthickness=0,
        )
        self.custom_entry.pack(fill="x", padx=sc(10), pady=sc(8))

        buttons = tk.Frame(inner, bg=panel.fill)
        buttons.pack(fill="x")
        self.btn_save_custom = PillButton(
            buttons, "保存配置", command=self._save_custom_path, kind="secondary",
            font="btn_sm", padx=14, pady=8,
        )
        self.btn_save_custom.pack(side="left")
        self.btn_browse = PillButton(
            buttons, "浏览…", command=self._browse_dir, kind="ghost",
            font="btn_sm", padx=14, pady=8,
        )
        self.btn_browse.pack(side="left", padx=(sc(8), 0))
        label(
            buttons,
            "选择包含 product.json 的目录",
            font="small",
            fg=C.FAINT,
            bg=panel.fill,
        ).pack(side="right", pady=(sc(6), 0))

        if self.advanced_open:
            self.advanced_holder.pack(fill="x")
            self.chevron.configure(text="▾")
        self._update_custom_widgets()

    def _build_status_card(self, parent: tk.Misc) -> None:
        card = Card(parent, pady=14)
        card.pack(fill="x", pady=(0, sc(10)))
        body = card.body

        head = tk.Frame(body, bg=card.fill)
        head.pack(fill="x")
        label(head, "运行状态", font="h2").pack(side="left")
        self.mode_badge = Badge(head, "检测中…", "muted")
        self.mode_badge.pack(side="right")

        rows_holder = tk.Frame(body, bg=card.fill)
        rows_holder.pack(fill="x", pady=(sc(10), 0))
        self.rows: list[TargetRow] = []
        for index, rel in enumerate(sand.TARGETS):
            if index:
                hsep(rows_holder).pack(fill="x")
            row = TargetRow(rows_holder, rel)
            row.frame.pack(fill="x", pady=sc(4))
            self.rows.append(row)

        self.summary_var = tk.StringVar(value="尚未检测")
        label(body, "", font="small", fg=C.MUTED, textvariable=self.summary_var).pack(
            anchor="w", pady=(sc(10), 0)
        )

    def _build_action_bar(self, parent: tk.Misc) -> None:
        bar = tk.Frame(parent, bg=C.BG)
        bar.pack(fill="x")

        self.btn_apply = PillButton(
            bar, "开启 SAND 模式", command=lambda: self._run_action("apply"),
            kind="primary", min_width=150,
        )
        self.btn_apply.pack(side="left")
        self.btn_restore = PillButton(
            bar, "还原 IDE 模式", command=lambda: self._run_action("restore"),
            kind="secondary", min_width=140,
        )
        self.btn_restore.pack(side="left", padx=(sc(10), 0))
        self.btn_refresh = PillButton(
            bar, "刷新状态", command=self.refresh_status, kind="ghost", padx=16,
        )
        self.btn_refresh.pack(side="right")

        # 固定高度的容器，避免不同 ttk 主题下进度条被撑成一个空输入框
        progress_wrap = tk.Frame(parent, bg=C.BG, height=sc(5))
        progress_wrap.pack(fill="x", pady=(sc(12), 0))
        progress_wrap.pack_propagate(False)
        self.progress = ttk.Progressbar(
            progress_wrap, style="Sand.Horizontal.TProgressbar",
            mode="determinate", maximum=100, value=0,
        )
        self.progress.pack(fill="both", expand=True)

        self.hint_var = tk.StringVar(value=self.HINT_IDLE)
        label(parent, "", font="small", fg=C.MUTED, textvariable=self.hint_var).pack(
            anchor="w", pady=(sc(7), sc(10))
        )

    def _build_log_card(self, parent: tk.Misc) -> None:
        card = Card(parent, stretch=True, fill=C.FIELD, outline=C.BORDER,
                    pady=14, min_height=86)
        card.pack(fill="both", expand=True)
        body = card.body

        head = tk.Frame(body, bg=card.fill)
        head.pack(fill="x")
        label(head, "操作日志", font="h2", fg=C.TEXT_DIM, bg=card.fill).pack(side="left")
        PillButton(
            head, "清空", command=self._clear_log, kind="ghost",
            font="btn_sm", padx=12, pady=5,
        ).pack(side="right")

        text_wrap = tk.Frame(body, bg=card.fill)
        text_wrap.pack(fill="both", expand=True, pady=(sc(10), 0))

        self.log = tk.Text(
            text_wrap,
            wrap="word",
            font=FONTS["mono_sm"],
            bg=card.fill,
            fg=C.TEXT_DIM,
            relief="flat",
            bd=0,
            highlightthickness=0,
            insertbackground=C.ACCENT,
            selectbackground=C.ACCENT_LO,
            selectforeground="#FFFFFF",
            spacing1=sc(1),
            spacing3=sc(2),
            padx=0,
            pady=0,
            state="disabled",
            height=6,
        )
        self.log_scroll = ttk.Scrollbar(
            text_wrap, orient="vertical", style="Sand.Vertical.TScrollbar",
            command=self.log.yview,
        )
        self._scroll_shown = False
        self.log.configure(yscrollcommand=self._sync_log_scroll)
        self.log.pack(side="left", fill="both", expand=True)

        self.log.tag_configure("ts", foreground=C.FAINT)
        self.log.tag_configure("info", foreground=C.TEXT_DIM)
        self.log.tag_configure("muted", foreground=C.MUTED)
        self.log.tag_configure("head", foreground=C.ACCENT_HI)
        self.log.tag_configure("ok", foreground=C.OK)
        self.log.tag_configure("warn", foreground=C.WARN)
        self.log.tag_configure("err", foreground=C.DANGER)

    def _apply_window_chrome(self) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        # 按实际内容定高：管理员警告条 / 展开的高级选项都会自动被算进去，
        # 再额外留一段给日志区，最后被屏幕高度封顶。
        self.root.update_idletasks()
        sync_cards(self.root)
        self.root.update_idletasks()
        needed = self._outer.winfo_reqheight() + sc(28)

        cap = max(sc(520), screen_h - sc(96))
        height = min(needed + sc(150), cap)
        width = min(sc(900), max(sc(760), screen_w - sc(80)))
        self.root.minsize(min(sc(760), width), min(needed - sc(40), cap))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        icon = make_app_icon()
        if icon is not None:
            self._icon = icon
            try:
                self.root.iconphoto(True, icon)
            except Exception:
                pass
        enable_dark_titlebar(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------- 小工具

    def _log(self, message: str, kind: str = "info") -> None:
        if not message:
            return
        self.log.configure(state="normal")
        for line in str(message).rstrip("\n").split("\n"):
            self.log.insert("end", time.strftime("%H:%M:%S") + "  ", "ts")
            self.log.insert("end", line.rstrip() + "\n", kind)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log_block(self, text: str) -> None:
        for line in text.rstrip("\n").split("\n"):
            self._log(line, classify_line(line))

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _sync_log_scroll(self, first, last) -> None:
        """内容装得下就把滚动条藏起来，避免一条常驻的灰杠。"""
        needed = not (float(first) <= 0.0 and float(last) >= 1.0)
        if needed != self._scroll_shown:
            self._scroll_shown = needed
            if needed:
                self.log_scroll.pack(side="right", fill="y", before=self.log)
            else:
                self.log_scroll.pack_forget()
        self.log_scroll.set(first, last)

    def _set_hint(self, text: str) -> None:
        self.hint_var.set(text)

    def _update_controls(self) -> None:
        locked = self.busy or self.scanning
        state = "disabled" if locked else "normal"
        for widget in (self.btn_apply, self.btn_restore, self.btn_refresh, self.btn_relocate):
            widget.set_state(state)
        self.custom_switch.set_state("disabled" if self.busy else "normal")
        self._update_custom_widgets()

    def _update_custom_widgets(self) -> None:
        enabled = self.use_custom_var.get() and not self.busy
        state = "normal" if enabled else "disabled"
        self.custom_entry.configure(state=state)
        self.custom_field.configure(highlightbackground=C.BORDER_HI if enabled else C.BORDER)
        self.btn_browse.set_state(state)
        self.btn_save_custom.set_state(state)
        self.custom_switch.refresh()

    def _toggle_advanced(self) -> None:
        self.advanced_open = not self.advanced_open
        if self.advanced_open:
            self.advanced_holder.pack(fill="x")
            self.chevron.configure(text="▾")
            sync_cards(self.advanced_holder)
            self.root.update_idletasks()
            self._grow_window(self.advanced_holder.winfo_reqheight())
        else:
            sync_cards(self.advanced_holder)
            delta = self.advanced_holder.winfo_reqheight()
            self.advanced_holder.pack_forget()
            self.chevron.configure(text="▸")
            self._grow_window(-delta)

    def _grow_window(self, delta: int) -> None:
        """展开/收起高级选项时顺带调整窗口高度，别把日志区挤没了。"""
        if not delta:
            return
        try:
            if self.root.state() != "normal":
                return
        except tk.TclError:
            return
        self.root.update_idletasks()
        height = self.root.winfo_height()
        floor = self.root.minsize()[1]
        limit = max(floor, self.root.winfo_screenheight() - sc(96))
        target = min(max(height + delta, floor), limit)
        if target != height:
            self.root.geometry(f"{self.root.winfo_width()}x{target}")

    def _relaunch_admin(self) -> None:
        relaunch_as_admin()
        self.root.after(300, self.root.destroy)

    def _open_app_dir(self) -> None:
        if not self.current_path or not os.path.isdir(self.current_path):
            show_error(self.root, "无法打开", "还没有定位到 Cursor 安装目录。")
            return
        try:
            if sys.platform == "win32":
                os.startfile(self.current_path)
            else:
                subprocess.Popen(["xdg-open", self.current_path])
        except Exception as exc:
            show_error(self.root, "无法打开", f"打开目录失败：{exc}")

    def _on_close(self) -> None:
        if self.busy and not ask_confirm(
            self.root,
            "操作尚未完成",
            "补丁正在写入，现在关闭可能让 Cursor 处于半修改状态。确定要退出吗？",
            yes="仍然退出",
            no="继续等待",
            tone="warn",
        ):
            return
        self.root.destroy()

    # ---------------------------------------------------------- 路径设置

    def _on_custom_toggle(self) -> None:
        self.config["use_custom_path"] = bool(self.use_custom_var.get())
        try:
            save_config(self.config)
        except OSError as exc:
            self._log(f"配置保存失败: {exc}", "err")
        self._update_custom_widgets()
        self.refresh_status()

    def _save_custom_path(self) -> None:
        custom = self.custom_path_var.get().strip().strip('"')
        if not is_valid_app_dir(custom):
            show_error(
                self.root,
                "路径无效",
                "自定义路径无效，请选择包含 product.json 的 resources/app 目录。",
                detail=custom or "（未填写路径）",
            )
            return
        self.config["use_custom_path"] = True
        self.config["custom_app_dir"] = os.path.normpath(custom)
        self.use_custom_var.set(True)
        try:
            save_config(self.config)
        except OSError as exc:
            show_error(self.root, "保存失败", f"无法写入配置文件：{exc}")
            return
        self._update_custom_widgets()
        self._log(f"已保存自定义路径: {self.config['custom_app_dir']}", "ok")
        self.refresh_status()

    def _relocate(self) -> None:
        if self.use_custom_var.get():
            if ask_confirm(
                self.root,
                "重新读取",
                "当前启用了自定义路径。是否切换回自动读取本机 Cursor 安装位置？",
                yes="切回自动",
                no="保持自定义",
            ):
                self.use_custom_var.set(False)
                self.config["use_custom_path"] = False
                try:
                    save_config(self.config)
                except OSError as exc:
                    self._log(f"配置保存失败: {exc}", "err")
                self._update_custom_widgets()
            else:
                return
        self.refresh_status(log_path=True)

    def _browse_dir(self) -> None:
        chosen = filedialog.askdirectory(title="选择 Cursor resources/app 目录")
        if not chosen:
            return
        chosen = os.path.normpath(chosen)
        self.custom_path_var.set(chosen)
        self.use_custom_var.set(True)
        self._update_custom_widgets()
        if is_valid_app_dir(chosen):
            # 选中的目录本身就有效，直接落盘，省掉一次「保存配置」
            self._save_custom_path()
        else:
            show_error(
                self.root,
                "路径无效",
                "所选目录里没有 product.json，请选择 Cursor 的 resources/app 目录。",
                detail=chosen,
            )

    # ---------------------------------------------------------- 状态扫描

    def refresh_status(self, log_path: bool = False) -> None:
        if self.busy:
            return
        if self.scanning:
            # 扫描期间又被触发（例如刚保存了自定义路径）：排队，扫完再来一次
            self._rescan_pending = True
            self._rescan_log_path = self._rescan_log_path or log_path
            return
        self.scanning = True
        self._update_controls()
        self._set_hint("正在读取 Cursor 安装位置并检测文件状态…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(14)
        self.mode_badge.set("检测中…", "muted")
        threading.Thread(target=self._scan_worker, args=(log_path,), daemon=True).start()

    def _scan_worker(self, log_path: bool) -> None:
        try:
            path, source = resolve_app_dir(dict(self.config))
            result = collect_status(path) if path else None
        except Exception as exc:  # 后台线程里任何意外都不能吞掉
            self._post(self._scan_failed, str(exc))
            return
        self._post(self._scan_done, path, source, result, log_path)

    def _scan_failed(self, message: str) -> None:
        self.scanning = False
        self._stop_progress()
        self._set_hint("检测失败")
        self.mode_badge.set("检测失败", "danger")
        self.summary_var.set(message)
        self._log(f"检测状态失败: {message}", "err")
        self._update_controls()
        self._flush_pending_rescan()

    def _scan_done(
        self, path: str | None, source: str, result: dict | None, log_path: bool
    ) -> None:
        self.scanning = False
        self._stop_progress()
        try:
            self._apply_scan(path, source, result, log_path)
        finally:
            self._update_controls()
            self._flush_pending_rescan()

    def _flush_pending_rescan(self) -> None:
        if not self._rescan_pending:
            return
        self._rescan_pending = False
        log_path = self._rescan_log_path
        self._rescan_log_path = False
        self.root.after(40, lambda: self.refresh_status(log_path=log_path))

    def _apply_scan(
        self, path: str | None, source: str, result: dict | None, log_path: bool
    ) -> None:
        self.current_path = path
        self.current_source = source

        if not path:
            self.path_display_var.set("（未找到 Cursor 安装目录）")
            if self.config.get("use_custom_path"):
                self.source_var.set("⚠ 自定义路径无效，请重新选择或关闭自定义模式")
            else:
                self.source_var.set(
                    "⚠ 未能自动读取，请先启动 Cursor 后点「重新读取」，或启用手动指定"
                )
            self.source_label.configure(fg=C.WARN)
            self.mode_badge.set("未找到 Cursor", "danger")
            self.summary_var.set("未找到 Cursor，无法检测状态")
            for row in self.rows:
                row.update(None)
            self._set_hint("未找到 Cursor 安装目录")
            self._update_controls()
            return

        self.path_display_var.set(path)
        if source == "用户自定义配置":
            self.source_var.set("✓ 使用自定义路径")
        else:
            self.source_var.set(f"✓ 自动读取来源：{source}")
        self.source_label.configure(fg=C.OK)
        if log_path:
            self._log(f"已定位 Cursor: {path} ({source})", "ok")

        if result and result.get("error"):
            self.mode_badge.set("读取失败", "danger")
            self.summary_var.set(result["error"])
            for row in self.rows:
                row.update(None)
            self._log(result["error"], "err")
            self._set_hint("product.json 读取失败")
            self._update_controls()
            return

        result = result or {"rows": [], "sand": 0, "ide": 0, "bad": 0, "missing": 0}
        by_rel = {item["rel"]: item for item in result["rows"]}
        for row in self.rows:
            row.update(by_rel.get(row.rel))

        total = len(sand.TARGETS)
        if result["sand"] == total:
            self.mode_badge.set("SAND 已全部开启", "ok")
        elif result["ide"] == total:
            self.mode_badge.set("IDE 默认状态", "neutral")
        elif result["missing"] == total:
            self.mode_badge.set("文件缺失", "danger")
        else:
            self.mode_badge.set(f"混合 {result['sand']}/{total}", "warn")

        self.summary_var.set(summary_text(result))
        self._set_hint(self.HINT_IDLE)
        self._update_controls()

    def _stop_progress(self) -> None:
        try:
            self.progress.stop()
        except tk.TclError:
            pass
        self.progress.configure(mode="determinate", value=0)

    # ---------------------------------------------------------- 应用/还原

    def _run_action(self, action: str) -> None:
        if self.busy or self.scanning:
            return
        app_dir = self.current_path
        if not app_dir or not is_valid_app_dir(app_dir):
            show_error(
                self.root,
                "未找到 Cursor",
                "无法读取 Cursor 安装位置。\n\n"
                "建议：\n"
                "1. 先启动一次 Cursor，再点「重新读取」\n"
                "2. 或在高级选项中手动指定 resources/app 路径",
            )
            self.refresh_status()
            return

        title = "开启 SAND 模式" if action == "apply" else "还原 IDE 模式"
        verb = "开启 SAND" if action == "apply" else "还原 IDE"
        if not ask_confirm(
            self.root,
            title,
            f"即将{verb}，并自动关闭所有 Cursor 进程。完成后需要手动重新启动 Cursor。",
            detail=f"目标路径：{app_dir}",
            yes=title,
            no="取消",
            tone="warn" if action == "restore" else "question",
        ):
            return

        self._begin_busy()
        threading.Thread(
            target=self._action_worker, args=(action, app_dir), daemon=True
        ).start()

    def _begin_busy(self) -> None:
        self.busy = True
        self._update_controls()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self._set_hint("正在关闭 Cursor 进程…")

    def _end_busy(self) -> None:
        self.busy = False
        self._stop_progress()
        self._update_controls()
        self._set_hint(self.HINT_IDLE)
        self.root.after(60, self.refresh_status)

    def _post(self, func, *args) -> None:
        """把后台线程的结果丢回主线程；窗口已关掉就安静地丢弃。"""
        try:
            self.root.after(0, lambda: func(*args))
        except (tk.TclError, RuntimeError):
            pass

    def _action_worker(self, action: str, app_dir: str) -> None:
        try:
            verb = "APPLY" if action == "apply" else "RESTORE"
            self._post(self._log, f"===== {verb} 开始 =====", "head")
            self._post(self._log, f"目标: {app_dir}", "muted")

            ok, message = kill_cursor_processes()
            self._post(self._log, message, "ok" if ok else "err")
            if not ok:
                self._post(show_error, self.root, "无法继续", message)
                return

            self._post(self._set_hint, "正在写入补丁并更新完整性校验…")

            buffer = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = buffer
            try:
                if action == "apply":
                    sand.cmd_apply(app_dir)
                else:
                    sand.cmd_restore(app_dir)
            finally:
                sys.stdout = old_stdout
                output = buffer.getvalue().strip()
                if output:
                    self._post(self._log_block, output)

            self._post(
                show_info,
                self.root,
                "操作完成",
                ("SAND 模式已开启。" if action == "apply" else "已还原为 IDE 模式。")
                + "\n请重新启动 Cursor 使更改生效。",
            )
        except sand.PatchError as exc:
            self._post(self._log, f"错误: {exc}", "err")
            self._post(show_error, self.root, "操作失败", str(exc))
        except PermissionError as exc:
            tip = (
                f"权限不足或文件被占用：{exc}\n\n"
                "请确认：\n"
                "1. Cursor 已完全退出\n"
                "2. 以管理员身份运行本工具"
            )
            self._post(self._log, tip, "err")
            self._post(show_error, self.root, "权限错误", tip)
        except SystemExit as exc:
            # cursor_sand_min.preflight 用 sys.exit 中止，这里翻译成界面提示
            reason = str(exc.code) if exc.code not in (None, 0) else "预检未通过，操作已中止。"
            self._post(self._log, reason, "err")
            self._post(show_error, self.root, "操作中止", reason)
        except Exception as exc:
            self._post(self._log, f"未知错误: {exc}", "err")
            self._post(show_error, self.root, "操作失败", str(exc))
        finally:
            self._post(self._end_busy)


def classify_line(line: str) -> str:
    text = line.strip()
    if not text:
        return "info"
    if text.startswith("x ") or text.startswith("×"):
        return "err"
    if any(word in text for word in ("错误", "失败", "无法", "不匹配", "损坏")):
        return "err"
    if text.startswith("!") or text.startswith("⚠") or "警告" in text:
        return "warn"
    if "完成" in text or text.startswith("✓"):
        return "ok"
    if text.startswith("["):
        return "head"
    return "info"


def make_app_icon() -> tk.PhotoImage | None:
    """用 PhotoImage 画一个圆角方块图标，避免默认的 Tk 羽毛图标。"""
    glyph = (
        ".#####.",
        "##...##",
        "##.....",
        "##.....",
        ".#####.",
        ".....##",
        ".....##",
        "##...##",
        ".#####.",
    )
    try:
        size = 40
        radius = 9
        image = tk.PhotoImage(width=size, height=size)

        def inside(x: int, y: int) -> bool:
            cx = min(max(x, radius), size - 1 - radius)
            cy = min(max(y, radius), size - 1 - radius)
            dx, dy = x - cx, y - cy
            return dx * dx + dy * dy <= radius * radius

        pixels = [[C.ACCENT if inside(x, y) else C.BG for x in range(size)] for y in range(size)]

        scale = 3
        off_x = (size - len(glyph[0]) * scale) // 2
        off_y = (size - len(glyph) * scale) // 2
        for gy, row in enumerate(glyph):
            for gx, ch in enumerate(row):
                if ch != "#":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        pixels[off_y + gy * scale + dy][off_x + gx * scale + dx] = "#FFFFFF"

        image.put(" ".join("{" + " ".join(row) + "}" for row in pixels))
        for y in range(size):
            for x in range(size):
                if not inside(x, y):
                    image.transparency_set(x, y, True)
        return image
    except Exception:
        return None


def _show_license_badge(app, lic_state) -> None:
    """在 GUI 标题右侧显示授权到期信息。"""
    try:
        import datetime

        if not lic_state.expires_at:
            return
        exp = datetime.datetime.fromtimestamp(lic_state.expires_at).strftime("%Y-%m-%d")
        badge = Badge(app.root, f"授权至 {exp}", "ok" if lic_state.days_left and lic_state.days_left > 7 else "warn")
        badge.pack(side="right", padx=(0, 8))
    except Exception:
        pass


def main() -> None:
    global UI_SCALE

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    dpi = enable_dpi_awareness()
    UI_SCALE = max(1.0, min(3.0, dpi / 96.0))

    root: tk.Tk | None = None
    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass

        init_fonts()
        setup_ttk(root)

        # 授权校验：未激活 -> 弹窗输入密钥；无效/过期 -> 弹窗后退出（软件打不开）
        lic_state = license_gate.check_and_gate(root)
        root.deiconify()

        app = SandGuiApp(root)
        if lic_state.allowed:
            _show_license_badge(app, lic_state)
        root.deiconify()
        root.after(120, lambda: app.refresh_status(log_path=True))
        root.mainloop()
    except Exception as exc:
        message = (
            f"工具无法启动：\n{exc}\n\n"
            "请确认已安装 Python 3，且包含 tkinter 组件。"
        )
        try:
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass
            fallback = tk.Tk()
            fallback.withdraw()
            messagebox.showerror("启动失败", message)
            fallback.destroy()
        except Exception:
            print(f"启动失败: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
