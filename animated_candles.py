"""
AnimatedCandles — Candlestick Chart Drawing and Teaching Tool
Requirements: Python 3.11+; macOS also needs: pip install tkmacosx
Run:   python3 animated_candles.py
Build: pyinstaller AnimatedCandles.spec
"""

import math
import platform
import threading
import time
from dataclasses import dataclass

import tkinter as tk
from tkinter import colorchooser, ttk

# ---------------------------------------------------------------------------
# Platform-aware button (respects bg/fg on macOS)
# ---------------------------------------------------------------------------
if platform.system() == "Darwin":
    try:
        from tkmacosx import Button as MacButton
        PLATFORM_BUTTON = MacButton
    except ImportError:
        PLATFORM_BUTTON = tk.Button
else:
    PLATFORM_BUTTON = tk.Button

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
_BG     = "#0d0d0d"   # window / frame background
_TB     = "#1a1a1a"   # toolbar background
_BTN    = "#2d2d2d"   # default button face
_BTN_FG = "#ffffff"
_DIM    = "#aaaaaa"   # dimmed / disabled text
_SEP    = "#444444"   # separator bars
_CV     = "#0a0a0a"   # canvas background

# ---------------------------------------------------------------------------
# Style configuration
# ---------------------------------------------------------------------------

@dataclass
class StyleConfig:
    """Visual style for all candles in the session."""
    bull_body_color:  str = "#00C805"
    bear_body_color:  str = "#FF3B30"
    wick_color:       str = "#FFFFFF"
    border_color:     str = "#FFFFFF"
    border_thickness: int = 1
    label_position:   str = "below"   # "below" | "above" | "inside"


# ---------------------------------------------------------------------------
# Timeframe constants
# ---------------------------------------------------------------------------

TF_OPTIONS  = ["1m", "3m", "5m", "15m", "30m", "1H", "4H", "D"]
TF_MINUTES  = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15,
    "30m": 30, "1H": 60, "4H": 240, "D": 1440,
}
TF_DEFAULTS = ["5m", "15m", "1H", "4H"]   # one per pane slot

_PANE_BORDERS = ["#6a0dad", "#2d7a2d", "#2d5a7a", "#7a5a2d"]


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class CandlestickDrawerApp:
    """Main application — variable-pane candlestick drawing and teaching tool."""

    # ── Initialisation ──────────────────────────────────────────────────────

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("AnimatedCandles")
        self.root.minsize(1200, 700)
        self.root.configure(bg=_BG)

        self.style_config = StyleConfig()

        self._canvas_w          = 540
        self._canvas_h          = 480
        self.candle_width       = 20
        self.candle_spacing     = 8
        self.htf_candle_width   = 30
        self.htf_candle_spacing = 14

        self.current_candle: dict | None = None
        self.is_drawing   = False
        self.is_replaying = False
        self._replay_thread: threading.Thread | None = None

        # ── UI string/bool vars ────────────────────────────────────────────
        self.show_strat   = tk.BooleanVar(value=False)
        self.replay_speed = tk.DoubleVar(value=0.05)
        self.loop_replay  = tk.BooleanVar(value=False)
        self.tf_error_var = tk.StringVar(value="")
        self.status_var   = tk.StringVar(
            value="Draw a candle: left-click and drag on P1")

        # ── Pane system ────────────────────────────────────────────────────
        self.panes: list[dict] = []
        self.pane_count        = 2
        self.pane_orientation  = "row"   # "row" | "grid"

        for tf_str in TF_DEFAULTS:
            self.panes.append({
                "canvas":      None,
                "tf_minutes":  TF_MINUTES[tf_str],
                "tf_label":    tf_str,
                "candles":     [],
                "tf_var":      tk.StringVar(value=tf_str),
                "tf_selector": None,
                "tf_btn":      None,
            })

        # ── Pencil state ───────────────────────────────────────────────────
        self.pencil_state              = 0   # 0=off, 1=temp, 2=persist
        self.freehand_strokes: list[dict] = []
        self._current_stroke_points: list[tuple[int, int]] = []

        # ── Level lines ────────────────────────────────────────────────────
        self.level_lines: list[dict] = []
        self.line_mode               = False
        self.line_phase              = 0
        self.line_start_x: int | None    = None
        self.line_start_y: float | None  = None
        self.line_snapped_y: float | None = None
        self.line_temp_dot_id: int | None = None

        # Moving an existing line
        self.moving_line: dict | None    = None
        self.move_grab_offset_y          = 0
        self._suppress_next_left_release = False

        # ── Eraser ────────────────────────────────────────────────────────
        self.tool_mode  = "pencil"   # "pencil" | "eraser"
        self.is_erasing = False

        # ── Undo / redo ───────────────────────────────────────────────────
        self.redo_stack: list[dict] = []

        # ── Right-click manual double-click detector ───────────────────────
        self._last_right_click_time        = 0.0
        self._right_double_click_threshold = 0.35   # seconds

        self._setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.root.bind("<Escape>", self.exit_line_mode)

    # ── Property alias ───────────────────────────────────────────────────────

    @property
    def ltf_candles(self) -> list:
        return self.panes[0]["candles"]

    # ── UI construction ──────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        toolbar = tk.Frame(self.root, bg=_TB, pady=4)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        def sep() -> None:
            tk.Frame(toolbar, width=1, bg=_SEP).pack(
                side=tk.LEFT, fill=tk.Y, padx=1, pady=3)

        def tbtn(text: str, cmd, bg: str = _BTN, fg: str = _BTN_FG,
                 abg: str | None = None, **kw):
            b = PLATFORM_BUTTON(
                toolbar, text=text, command=cmd,
                bg=bg, fg=fg,
                activebackground=abg or bg, activeforeground=fg,
                relief=tk.FLAT, padx=0, pady=0,
                font=("Arial", 9),
                **kw)
            b.pack(side=tk.LEFT, ipadx=3, ipady=1, padx=(0, 2))
            return b

        # ── Undo / Redo ────────────────────────────────────────────────────
        self.undo_btn = PLATFORM_BUTTON(
            toolbar, text="◀", command=self.undo_candle,
            bg=_BTN, fg=_BTN_FG,
            activebackground="#3d3d3d", activeforeground=_BTN_FG,
            relief=tk.FLAT, padx=0, pady=0,
            font=("Arial", 9),
            state=tk.DISABLED)
        self.undo_btn.pack(side=tk.LEFT, ipadx=3, ipady=1, padx=(0, 1))

        self.redo_btn = PLATFORM_BUTTON(
            toolbar, text="▶", command=self.redo_candle,
            bg=_BTN, fg=_BTN_FG,
            activebackground="#3d3d3d", activeforeground=_BTN_FG,
            relief=tk.FLAT, padx=0, pady=0,
            font=("Arial", 9),
            state=tk.DISABLED)
        self.redo_btn.pack(side=tk.LEFT, ipadx=3, ipady=1, padx=(0, 2))
        sep()

        # ── Pane count [1][2][3][4] ────────────────────────────────────────
        self._pane_count_btns: list = []
        for n in range(1, 5):
            active = (n == self.pane_count)
            b = PLATFORM_BUTTON(
                toolbar, text=str(n),
                command=lambda x=n: self.set_pane_count(x),
                bg="#4a4a4a" if active else _BTN,
                fg=_BTN_FG if active else _DIM,
                activebackground="#4a4a4a", activeforeground=_BTN_FG,
                relief=tk.FLAT, padx=0, pady=0,
                font=("Arial", 9, "bold"))
            b.pack(side=tk.LEFT, ipadx=2, ipady=1, padx=(0, 1))
            self._pane_count_btns.append(b)

        # Orientation toggle
        self._orient_btn = PLATFORM_BUTTON(
            toolbar, text="",
            command=self.toggle_orientation,
            bg=_BTN, fg=_BTN_FG,
            activebackground="#3d3d3d", activeforeground=_BTN_FG,
            relief=tk.FLAT, padx=0, pady=0,
            font=("Arial", 9))
        self._orient_btn.pack(side=tk.LEFT, ipadx=2, ipady=1, padx=(0, 2))
        self.update_orientation_button_label()
        self.update_orientation_button_visibility()
        sep()

        # ── TF selectors (custom dark dropdowns) ──────────────────────────
        self._tf_frame = tk.Frame(toolbar, bg=_TB)
        self._tf_frame.pack(side=tk.LEFT)
        self._tf_selector_frames: list[tk.Frame] = []

        for i, pane in enumerate(self.panes):
            frame = tk.Frame(self._tf_frame, bg=_TB)
            frame.grid(row=0, column=i, padx=(0, 2))
            tk.Label(frame, text=f"{i + 1}:", bg=_TB, fg=_DIM,
                     font=("Arial", 9)).pack(side=tk.LEFT)
            btn = PLATFORM_BUTTON(
                frame, text=pane["tf_label"],
                bg="#1e3a5f", fg=_BTN_FG,
                activebackground="#2d5a8e", activeforeground=_BTN_FG,
                relief=tk.FLAT, padx=0, pady=0,
                font=("Arial", 9, "bold"))
            btn.configure(command=self._make_tf_cmd(i, btn))
            btn.pack(side=tk.LEFT, ipadx=3, ipady=1)
            pane["tf_btn"]      = btn
            pane["tf_selector"] = btn
            self._tf_selector_frames.append(frame)

        # TF validation error label
        self._tf_error_lbl = tk.Label(
            self._tf_frame, textvariable=self.tf_error_var,
            bg=_TB, fg="#ff5555", font=("Arial", 9))
        self._tf_error_lbl.grid(row=0, column=4, padx=(0, 2))
        sep()

        # ── Pencil three-state ─────────────────────────────────────────────
        self.pencil_border_frame = tk.Frame(toolbar, bd=2, relief=tk.FLAT, bg=_TB)
        self.pencil_btn = PLATFORM_BUTTON(
            self.pencil_border_frame, text="✏",
            command=self.toggle_pencil,
            bg=_BTN, fg=_DIM,
            activebackground=_BTN, activeforeground=_BTN_FG,
            relief=tk.FLAT, padx=0, pady=0,
            font=("Arial", 9))
        self.pencil_btn.pack(ipadx=3, ipady=1)
        self.pencil_border_frame.pack(side=tk.LEFT, padx=(0, 2))

        # ── Eraser ────────────────────────────────────────────────────────
        self._eraser_btn = PLATFORM_BUTTON(
            toolbar, text="⌫", command=self._toggle_eraser,
            bg="#4a1e1e", fg="#e05555",
            activebackground="#6a2a2a", activeforeground="#ff6666",
            relief=tk.FLAT, padx=0, pady=0,
            font=("Arial", 9))
        self._eraser_btn.pack(side=tk.LEFT, ipadx=3, ipady=1, padx=(0, 2))
        sep()

        # ── Style / Strat ──────────────────────────────────────────────────
        tbtn("Style", self._open_style_settings,
             bg="#2d2d4a", fg="#8888ff", abg="#3d3d5a")
        tk.Checkbutton(
            toolbar, text="Strat#", variable=self.show_strat,
            command=self._on_strat_toggle,
            bg=_TB, fg=_BTN_FG, selectcolor=_BTN,
            activebackground=_TB, activeforeground=_BTN_FG,
            font=("Arial", 9),
        ).pack(side=tk.LEFT, padx=(0, 2))
        sep()

        # ── Replay ────────────────────────────────────────────────────────
        tbtn("▶", self._start_replay, bg="#1e4a1e", fg="#00cc00", abg="#2a6a2a")
        tbtn("■", self._stop_replay,  bg="#4a1e1e", fg="#cc0000", abg="#6a2a2a")
        tk.Checkbutton(
            toolbar, text="Loop", variable=self.loop_replay,
            bg=_TB, fg=_BTN_FG, selectcolor=_BTN,
            activebackground=_TB, activeforeground=_BTN_FG,
            font=("Arial", 9),
        ).pack(side=tk.LEFT, padx=(0, 2))
        tk.Scale(
            toolbar, variable=self.replay_speed,
            from_=0.01, to=0.30, resolution=0.01,
            orient=tk.HORIZONTAL, length=55,
            bg=_TB, fg=_BTN_FG, highlightthickness=0, troughcolor=_BTN,
        ).pack(side=tk.LEFT, padx=(0, 2))
        sep()

        # ── Canvas size + Clear ────────────────────────────────────────────
        tk.Label(toolbar, text="W:", bg=_TB, fg=_DIM,
                 font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 1))
        self.width_entry = tk.Entry(toolbar, width=3, bg=_BTN, fg=_BTN_FG,
                                    insertbackground=_BTN_FG, relief=tk.FLAT)
        self.width_entry.insert(0, str(self._canvas_w))
        self.width_entry.pack(side=tk.LEFT, padx=(0, 1))

        tk.Label(toolbar, text="H:", bg=_TB, fg=_DIM,
                 font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 1))
        self.height_entry = tk.Entry(toolbar, width=3, bg=_BTN, fg=_BTN_FG,
                                     insertbackground=_BTN_FG, relief=tk.FLAT)
        self.height_entry.insert(0, str(self._canvas_h))
        self.height_entry.pack(side=tk.LEFT, padx=(0, 2))

        tbtn("Apply", self._apply_size, bg="#2d4a2d", fg="#88cc88", abg="#3a5a3a")
        tbtn("Clear", self._clear_all,  bg="#4a2d2d", fg="#cc8888", abg="#5a3a3a")

        # ── Main canvas area ───────────────────────────────────────────────
        self._main_frame = tk.Frame(self.root, bg=_BG)
        self._main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # ── Status bar ────────────────────────────────────────────────────
        tk.Label(
            self.root, textvariable=self.status_var,
            bg=_TB, fg=_DIM, anchor=tk.W, padx=8, pady=3,
            font=("TkDefaultFont", 9),
        ).pack(side=tk.BOTTOM, fill=tk.X)

        self.rebuild_pane_layout()
        self.update_tf_selector_visibility()

    # ── TF dropdown helpers ──────────────────────────────────────────────────

    def _make_tf_cmd(self, pane_idx: int, btn) -> callable:
        return lambda: self._show_tf_dropdown(pane_idx, btn)

    def _show_tf_dropdown(self, pane_idx: int, anchor: tk.Widget) -> None:
        """Open a custom dark TF selection dropdown below anchor."""
        popup = tk.Toplevel(self.root)
        popup.wm_overrideredirect(True)
        popup.configure(bg="#1e1e1e")

        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height()
        popup.geometry(f"+{x}+{y}")

        def select(tf_str: str) -> None:
            popup.destroy()
            pane = self.panes[pane_idx]
            pane["tf_var"].set(tf_str)
            pane["tf_minutes"] = TF_MINUTES[tf_str]
            pane["tf_label"]   = tf_str
            pane["tf_btn"].config(text=tf_str)
            self._validate_tfs()
            self.recompute_all_htf()
            self.redraw_canvas()

        for tf_str in TF_OPTIONS:
            item = tk.Button(
                popup, text=tf_str, anchor="w",
                bg="#1e1e1e", fg="#ffffff",
                activebackground="#2d5a8e", activeforeground="#ffffff",
                relief=tk.FLAT, padx=12, pady=4, width=8,
                command=lambda t=tf_str: select(t))
            item.pack(fill=tk.X)

        popup.bind("<FocusOut>", lambda e: popup.destroy())
        popup.focus_force()

    # ── Pane count / orientation management ─────────────────────────────────

    def set_pane_count(self, n: int) -> None:
        self.pane_count = n
        self.redo_stack.clear()
        for i, b in enumerate(self._pane_count_btns):
            active = (i + 1 == n)
            b.config(
                bg="#4a4a4a" if active else _BTN,
                fg=_BTN_FG if active else _DIM)
        self.rebuild_pane_layout()
        self.update_tf_selector_visibility()
        self.update_orientation_button_visibility()
        self.redraw_canvas()

    def toggle_orientation(self) -> None:
        self.pane_orientation = (
            "grid" if self.pane_orientation == "row" else "row")
        self.rebuild_pane_layout()
        self.update_orientation_button_label()
        self.redraw_canvas()

    def update_orientation_button_label(self) -> None:
        n, o = self.pane_count, self.pane_orientation
        label_map = {
            (1, "row"):  "[ ]",
            (2, "row"):  "[][]",
            (2, "grid"): "[]\n[]",
            (3, "row"):  "[][][]",
            (3, "grid"): "[ ]\n[][]",
            (4, "row"):  "[][][][]",
            (4, "grid"): "[][]\n[][]",
        }
        self._orient_btn.config(text=label_map.get((n, o), "[]"))

    def update_orientation_button_visibility(self) -> None:
        if self.pane_count == 1:
            self._orient_btn.config(state=tk.DISABLED, fg=_DIM)
        else:
            self._orient_btn.config(state=tk.NORMAL, fg=_BTN_FG)

    def update_tf_selector_visibility(self) -> None:
        for i, frame in enumerate(self._tf_selector_frames):
            if i < self.pane_count:
                frame.grid(row=0, column=i, padx=(0, 6))
            else:
                frame.grid_remove()

    def _on_tf_change(self, pane_idx: int) -> None:
        pane = self.panes[pane_idx]
        tf_str = pane["tf_var"].get()
        pane["tf_minutes"] = TF_MINUTES.get(tf_str, 5)
        pane["tf_label"]   = tf_str
        self._validate_tfs()
        self.recompute_all_htf()
        self.redraw_canvas()

    def _validate_tfs(self) -> bool:
        tf0 = self.panes[0]["tf_minutes"]
        for p in range(1, self.pane_count):
            if self.panes[p]["tf_minutes"] <= tf0:
                self.tf_error_var.set(f"P{p + 1} TF must be > P1 TF")
                return False
        self.tf_error_var.set("")
        return True

    # ── Layout rebuild ───────────────────────────────────────────────────────

    def rebuild_pane_layout(self) -> None:
        for w in self._main_frame.winfo_children():
            w.destroy()
        for pane in self.panes:
            pane["canvas"] = None

        def make_canvas_frame(parent: tk.Widget, pane_idx: int) -> tk.Frame:
            pane  = self.panes[pane_idx]
            outer = tk.Frame(parent, bg=_BG)
            cv    = tk.Canvas(
                outer, bg=_CV,
                highlightthickness=2,
                highlightbackground=_PANE_BORDERS[pane_idx % 4])
            cv.pack(fill=tk.BOTH, expand=True)
            pane["canvas"] = cv
            return outer

        n   = self.pane_count
        o   = self.pane_orientation
        PAD = {"padx": 4, "pady": 4}

        if n == 1:
            make_canvas_frame(self._main_frame, 0).pack(
                fill=tk.BOTH, expand=True, **PAD)

        elif n == 2:
            if o == "row":
                row = tk.Frame(self._main_frame, bg=_BG)
                row.pack(fill=tk.BOTH, expand=True)
                for i in range(2):
                    make_canvas_frame(row, i).pack(
                        side=tk.LEFT, fill=tk.BOTH, expand=True, **PAD)
            else:
                col = tk.Frame(self._main_frame, bg=_BG)
                col.pack(fill=tk.BOTH, expand=True)
                for i in range(2):
                    make_canvas_frame(col, i).pack(
                        fill=tk.BOTH, expand=True, **PAD)

        elif n == 3:
            if o == "row":
                row = tk.Frame(self._main_frame, bg=_BG)
                row.pack(fill=tk.BOTH, expand=True)
                for i in range(3):
                    make_canvas_frame(row, i).pack(
                        side=tk.LEFT, fill=tk.BOTH, expand=True, **PAD)
            else:
                col = tk.Frame(self._main_frame, bg=_BG)
                col.pack(fill=tk.BOTH, expand=True)
                make_canvas_frame(col, 0).pack(
                    fill=tk.BOTH, expand=True, padx=4, pady=(4, 2))
                bot = tk.Frame(col, bg=_BG)
                bot.pack(fill=tk.BOTH, expand=True)
                for i in range(1, 3):
                    make_canvas_frame(bot, i).pack(
                        side=tk.LEFT, fill=tk.BOTH, expand=True,
                        padx=4, pady=(2, 4))

        elif n == 4:
            if o == "row":
                row = tk.Frame(self._main_frame, bg=_BG)
                row.pack(fill=tk.BOTH, expand=True)
                for i in range(4):
                    make_canvas_frame(row, i).pack(
                        side=tk.LEFT, fill=tk.BOTH, expand=True, **PAD)
            else:
                col = tk.Frame(self._main_frame, bg=_BG)
                col.pack(fill=tk.BOTH, expand=True)
                top = tk.Frame(col, bg=_BG)
                top.pack(fill=tk.BOTH, expand=True)
                for i in range(2):
                    make_canvas_frame(top, i).pack(
                        side=tk.LEFT, fill=tk.BOTH, expand=True,
                        padx=4, pady=(4, 2))
                bot = tk.Frame(col, bg=_BG)
                bot.pack(fill=tk.BOTH, expand=True)
                for i in range(2, 4):
                    make_canvas_frame(bot, i).pack(
                        side=tk.LEFT, fill=tk.BOTH, expand=True,
                        padx=4, pady=(2, 4))

        self.reapply_all_bindings(self.panes[0]["canvas"])

    def reapply_all_bindings(self, cv: tk.Canvas) -> None:
        """Bind all interactive events to a pane-0 canvas widget."""
        cv.bind("<ButtonPress-1>",   self.on_left_press)
        cv.bind("<B1-Motion>",       self.on_left_motion)
        cv.bind("<ButtonRelease-1>", self.on_left_release)
        # Bind both Button-2 and Button-3 for macOS trackpad compatibility
        cv.bind("<ButtonPress-2>",   self.on_right_press)
        cv.bind("<B2-Motion>",       self.on_right_motion)
        cv.bind("<ButtonRelease-2>", self.on_right_release)
        cv.bind("<ButtonPress-3>",   self.on_right_press)
        cv.bind("<B3-Motion>",       self.on_right_motion)
        cv.bind("<ButtonRelease-3>", self.on_right_release)
        cv.bind("<Double-Button-1>", self.on_double_left_click)
        cv.bind("<Motion>",          self.on_mouse_motion)
        cv.config(cursor="crosshair")

    # ── Canvas size ──────────────────────────────────────────────────────────

    def _apply_size(self) -> None:
        try:
            w = max(100, int(self.width_entry.get()))
            h = max(100, int(self.height_entry.get()))
        except ValueError:
            return
        self._canvas_w = w
        self._canvas_h = h
        for pane in self.panes:
            if pane["canvas"]:
                pane["canvas"].config(width=w, height=h)
        self.redraw_canvas()

    # ── Clear ────────────────────────────────────────────────────────────────

    def _clear_all(self) -> None:
        self.is_replaying = False
        for pane in self.panes:
            pane["candles"].clear()
            if pane["canvas"]:
                pane["canvas"].delete("all")
        self.current_candle = None
        self.level_lines.clear()
        self.freehand_strokes.clear()
        self._current_stroke_points = []
        self.redo_stack.clear()
        self.update_undo_redo_buttons()
        self.exit_line_mode()
        self.status_var.set("Cleared. Left-click drag on P1 to draw a candle.")

    # ── Position helpers ─────────────────────────────────────────────────────

    def _ltf_x(self, index: int) -> int:
        return (self.candle_spacing
                + index * (self.candle_width + self.candle_spacing)
                + self.candle_width // 2)

    def _htf_x(self, index: int) -> int:
        return (self.htf_candle_spacing
                + index * (self.htf_candle_width + self.htf_candle_spacing)
                + self.htf_candle_width // 2)

    # ── HTF aggregation ──────────────────────────────────────────────────────

    def recompute_all_htf(self, current_override=...) -> None:
        ltf     = self.panes[0]["candles"]
        cur     = self.current_candle if current_override is ... else current_override
        all_ltf = list(ltf) + ([cur] if cur is not None else [])
        tf0     = self.panes[0]["tf_minutes"]
        for p in range(1, self.pane_count):
            pane  = self.panes[p]
            ratio = max(1, pane["tf_minutes"] // tf0)
            pane["candles"] = self._aggregate(all_ltf, ltf, ratio, p)

    def _aggregate(self, all_ltf: list, finalized_ltf: list,
                   ratio: int, pane_idx: int) -> list:
        if not all_ltf:
            return []
        n          = len(all_ltf)
        num_groups = (n + ratio - 1) // ratio
        result     = []
        for g in range(num_groups):
            s, e    = g * ratio, min((g + 1) * ratio, n)
            group   = all_ltf[s:e]
            is_comp = (e - s == ratio) and (e <= len(finalized_ltf))
            result.append({
                "open_y":          group[0]["open_y"],
                "high_y":          min(c["high_y"] for c in group),
                "low_y":           max(c["low_y"]  for c in group),
                "close_y":         group[-1]["close_y"],
                "x":               self._htf_x(g),
                "index":           g,
                "mouse_positions": [y for c in group for y in c["mouse_positions"]],
                "took_high":       False,
                "took_low":        False,
                "is_complete":     is_comp,
                "group_size":      len(group),
            })
        return result

    def _compute_panes_for_replay(self, replayed: list,
                                   frame: dict) -> list[list]:
        all_ltf = list(replayed) + [frame]
        tf0     = self.panes[0]["tf_minutes"]
        result  = []
        for p in range(1, self.pane_count):
            pane  = self.panes[p]
            ratio = max(1, pane["tf_minutes"] // tf0)
            result.append(self._aggregate(all_ltf, replayed, ratio, p))
        return result

    # ── Strat classification ─────────────────────────────────────────────────

    def classify_candle(self, curr: dict, prev: dict | None) -> str:
        if prev is None:
            return ""
        ch, cl    = curr["high_y"], curr["low_y"]
        ph, pl    = prev["high_y"], prev["low_y"]
        took_high = ch < ph
        took_low  = cl > pl
        bullish   = curr["close_y"] < curr["open_y"]
        if took_high and took_low:
            return "3"
        if took_high:
            return "F2U" if not bullish else "2U"
        if took_low:
            return "F2D" if bullish else "2D"
        return "1"

    # ── Drawing primitives ───────────────────────────────────────────────────

    def draw_candle(self, canvas: tk.Canvas, candle: dict,
                    style: StyleConfig, half_w: int) -> None:
        x       = candle["x"]
        oy, cy  = candle["open_y"], candle["close_y"]
        hy, ly  = candle["high_y"], candle["low_y"]
        bullish = cy < oy
        fill    = style.bull_body_color if bullish else style.bear_body_color
        canvas.create_line(x, hy, x, ly, fill=style.wick_color, width=1)
        top = min(oy, cy)
        bot = max(oy, cy)
        if bot - top < 2:
            bot = top + 2
        if style.border_thickness > 0:
            canvas.create_rectangle(
                x - half_w, top, x + half_w, bot,
                fill=fill, outline=style.border_color,
                width=style.border_thickness)
        else:
            canvas.create_rectangle(
                x - half_w, top, x + half_w, bot,
                fill=fill, outline="")

    def draw_strat_label(self, canvas: tk.Canvas, candle: dict,
                         label: str, style: StyleConfig) -> None:
        if not label:
            return
        x, pos = candle["x"], style.label_position
        if pos == "above":
            canvas.create_text(x, candle["high_y"] - 12, text=label,
                                fill="white", font=("Helvetica", 11, "bold"),
                                anchor=tk.S)
        elif pos == "inside":
            mid = (min(candle["open_y"], candle["close_y"]) +
                   max(candle["open_y"], candle["close_y"])) / 2
            canvas.create_text(x, mid, text=label,
                                fill="white", font=("Helvetica", 11, "bold"))
        else:
            canvas.create_text(x, candle["low_y"] + 12, text=label,
                                fill="white", font=("Helvetica", 11, "bold"))

    def _draw_tf_label(self, canvas: tk.Canvas, label: str) -> None:
        canvas.create_rectangle(0, 0, 60, 20, fill="#1E1E1E", outline="")
        canvas.create_text(30, 10, text=label, fill="#FFFFFF",
                           font=("TkDefaultFont", 10))

    def _draw_active_highlight(self, canvas: tk.Canvas,
                                hc: dict, ratio: int) -> None:
        x  = hc["x"]
        hw = self.htf_candle_width // 2 + 4
        canvas.create_rectangle(
            x - hw, hc["high_y"] - 5, x + hw, hc["low_y"] + 5,
            outline="#FFB300", dash=(4, 3), width=1)
        canvas.create_text(
            x, hc["high_y"] - 18,
            text=f"{hc['group_size']}/{ratio}",
            fill="#888888", font=("Helvetica", 10))

    # ── Canvas redraw ────────────────────────────────────────────────────────

    def redraw_canvas(self) -> None:
        self.recompute_all_htf()
        self._redraw_pane0()
        for p in range(1, self.pane_count):
            self._redraw_htf_pane(p)

    def _redraw_pane0(self) -> None:
        cv = self.panes[0]["canvas"]
        if cv is None:
            return
        cv.delete("all")
        hw   = self.candle_width // 2
        prev = None
        for c in self.panes[0]["candles"]:
            self.draw_candle(cv, c, self.style_config, hw)
            if self.show_strat.get():
                self.draw_strat_label(cv, c, self.classify_candle(c, prev),
                                      self.style_config)
            prev = c
        if self.current_candle:
            c = self.current_candle
            self.draw_candle(cv, c, self.style_config, hw)
            if self.show_strat.get() and prev:
                self.draw_strat_label(cv, c, self.classify_candle(c, prev),
                                      self.style_config)
        for line in self.level_lines:
            line["canvas_line_id"] = cv.create_line(
                line["x1"], line["y"], line["x2"], line["y"],
                fill="#FFFFFF", width=2)
            r = 4
            line["canvas_dot_id"] = cv.create_oval(
                line["x1"] - r, line["y"] - r,
                line["x1"] + r, line["y"] + r,
                fill="#FFFFFF", outline="")
        for stroke in self.freehand_strokes:
            if len(stroke["points"]) >= 2:
                new_id = cv.create_line(
                    *[c for pt in stroke["points"] for c in pt],
                    fill="yellow", width=2, tags="freehand")
                stroke["canvas_id"] = new_id
        self._draw_tf_label(cv, self.panes[0]["tf_label"])

    def _redraw_htf_pane(self, pane_idx: int) -> None:
        pane = self.panes[pane_idx]
        cv   = pane["canvas"]
        if cv is None:
            return
        tf0   = self.panes[0]["tf_minutes"]
        ratio = max(1, pane["tf_minutes"] // tf0)
        cv.delete("all")
        hw   = self.htf_candle_width // 2
        prev = None
        for hc in pane["candles"]:
            self.draw_candle(cv, hc, self.style_config, hw)
            if not hc["is_complete"]:
                self._draw_active_highlight(cv, hc, ratio)
            if self.show_strat.get():
                self.draw_strat_label(cv, hc, self.classify_candle(hc, prev),
                                      self.style_config)
            prev = hc
        self._draw_tf_label(cv, pane["tf_label"])

    # ── Left-click dispatcher (pane 0) ───────────────────────────────────────

    def on_left_press(self, event: tk.Event) -> None:
        if self.moving_line is not None:
            return
        if self.tool_mode == "eraser":
            self.is_erasing = True
            self.erase_at(event.x, event.y)
            return
        self._start_candle(event)

    def on_left_motion(self, event: tk.Event) -> None:
        if self.is_erasing:
            self.erase_at(event.x, event.y)
            return
        if self.moving_line is not None:
            self._drag_move_line(event)
            return
        self._drag_candle(event)

    def on_left_release(self, event: tk.Event) -> None:
        if self.is_erasing:
            self.is_erasing = False
            return
        if self._suppress_next_left_release:
            self._suppress_next_left_release = False
            return
        if self.moving_line is not None:
            self._end_move_line(event)
            return
        self._end_candle(event)

    # ── Right-click dispatcher (pane 0) ──────────────────────────────────────

    def on_right_press(self, event: tk.Event) -> None:
        """Manual double-click detector — quick double-press enters line mode."""
        now = time.time()
        if now - self._last_right_click_time < self._right_double_click_threshold:
            self._last_right_click_time = 0.0
            self.enter_line_mode(event)
            return
        self._last_right_click_time = now
        self._start_drawing(event)

    def on_right_motion(self, event: tk.Event) -> None:
        self._drag_drawing(event)

    def on_right_release(self, event: tk.Event) -> None:
        self._end_drawing(event)

    # ── Internal candle handlers ─────────────────────────────────────────────

    def _start_candle(self, event: tk.Event) -> None:
        if self.is_replaying:
            return
        y   = event.y
        idx = len(self.panes[0]["candles"])
        self.current_candle = {
            "open_y": y, "high_y": y, "low_y": y, "close_y": y,
            "x": self._ltf_x(idx), "index": idx,
            "mouse_positions": [y],
            "took_high": False, "took_low": False,
        }

    def _drag_candle(self, event: tk.Event) -> None:
        if self.current_candle is None or self.is_replaying:
            return
        y = event.y
        c = self.current_candle
        c["mouse_positions"].append(y)
        c["high_y"]  = min(c["high_y"], y)
        c["low_y"]   = max(c["low_y"],  y)
        c["close_y"] = y
        if self.panes[0]["candles"]:
            prev = self.panes[0]["candles"][-1]
            c["took_high"] = c["high_y"] < prev["high_y"]
            c["took_low"]  = c["low_y"]  > prev["low_y"]
        self.recompute_all_htf()
        self._redraw_pane0()
        for p in range(1, self.pane_count):
            self._redraw_htf_pane(p)
        self._update_status(c)

    def _end_candle(self, event: tk.Event) -> None:
        if self.current_candle is None or self.is_replaying:
            return
        c = self.current_candle
        c["close_y"] = event.y
        c["mouse_positions"].append(event.y)
        self.panes[0]["candles"].append(c)
        self.current_candle = None
        self.redo_stack.clear()
        self.update_undo_redo_buttons()
        self.recompute_all_htf()
        self._redraw_pane0()
        for p in range(1, self.pane_count):
            self._redraw_htf_pane(p)

    def _drag_move_line(self, event: tk.Event) -> None:
        raw_y     = event.y + self.move_grab_offset_y
        snapped_y = self.snap_y_to_candle_levels(raw_y)
        delta_y   = snapped_y - self.moving_line["y"]
        self.moving_line["y"] = snapped_y
        cv = self.panes[0]["canvas"]
        cv.move(self.moving_line["canvas_line_id"], 0, delta_y)
        cv.move(self.moving_line["canvas_dot_id"],  0, delta_y)

    def _end_move_line(self, event: tk.Event) -> None:
        final_y = self.snap_y_to_candle_levels(
            event.y + self.move_grab_offset_y)
        delta_y = final_y - self.moving_line["y"]
        self.moving_line["y"] = final_y
        cv = self.panes[0]["canvas"]
        cv.move(self.moving_line["canvas_line_id"], 0, delta_y)
        cv.move(self.moving_line["canvas_dot_id"],  0, delta_y)
        self.moving_line        = None
        self.move_grab_offset_y = 0
        cv.config(cursor="crosshair")

    def _start_drawing(self, event: tk.Event) -> None:
        if self.line_mode and self.line_phase == 1:
            self.line_start_x = event.x
            self.line_start_y = (self.line_snapped_y
                                 if self.line_snapped_y is not None
                                 else float(event.y))
            self.line_phase = 2
            r = 4
            self.line_temp_dot_id = self.panes[0]["canvas"].create_oval(
                self.line_start_x - r, self.line_start_y - r,
                self.line_start_x + r, self.line_start_y + r,
                fill="#FFFFFF", outline="")
            return

        if self.line_mode and self.line_phase == 2:
            self.commit_level_line(self.line_start_x,
                                   self.line_start_y, event.x)
            return

        if self.pencil_state == 0 or self.is_replaying:
            return

        self.is_drawing = True
        self._current_stroke_points = [(event.x, event.y)]

    def _drag_drawing(self, event: tk.Event) -> None:
        if not self.is_drawing or self.pencil_state == 0:
            return
        self._current_stroke_points.append((event.x, event.y))
        cv  = self.panes[0]["canvas"]
        pts = self._current_stroke_points
        if len(pts) >= 2:
            tag = "freehand_temp" if self.pencil_state == 1 else "freehand_live"
            cv.create_line(pts[-2][0], pts[-2][1],
                           pts[-1][0], pts[-1][1],
                           fill="yellow", width=2, tags=tag)

    def _end_drawing(self, event: tk.Event) -> None:
        if not self.is_drawing:
            return
        self.is_drawing = False
        cv = self.panes[0]["canvas"]
        if self.pencil_state == 1:
            cv.delete("freehand_temp")
            self._current_stroke_points = []
        elif self.pencil_state == 2:
            cv.delete("freehand_live")
            if len(self._current_stroke_points) >= 2:
                stroke_id = cv.create_line(
                    *[c for pt in self._current_stroke_points for c in pt],
                    fill="yellow", width=2, tags="freehand")
                self.freehand_strokes.append({
                    "points":    list(self._current_stroke_points),
                    "canvas_id": stroke_id,
                })
            self._current_stroke_points = []

    # ── Motion handler ───────────────────────────────────────────────────────

    def on_mouse_motion(self, event: tk.Event) -> None:
        cv = self.panes[0]["canvas"]
        if cv is None:
            return

        cv.delete("cursor_dot")

        if not self.line_mode:
            return

        snapped_y           = self.snap_y_to_candle_levels(event.y)
        self.line_snapped_y = snapped_y
        cv.delete("snap_indicator")
        cv.create_line(event.x - 8, snapped_y,
                       event.x + 8, snapped_y,
                       fill="#FFD700", width=1, tags="snap_indicator")

        # Cursor dot — gold filled circle drawn on canvas (cross-platform)
        r = 5
        cv.create_oval(event.x - r, event.y - r,
                       event.x + r, event.y + r,
                       fill="#FFD700", outline="", tags="cursor_dot")

        if self.line_phase == 2:
            cv.delete("line_preview")
            cv.create_line(self.line_start_x, self.line_start_y,
                           event.x, self.line_start_y,
                           fill="#FFFFFF", width=1, dash=(4, 4),
                           tags="line_preview")

    # ── Double-click handlers ────────────────────────────────────────────────

    def on_double_left_click(self, event: tk.Event) -> None:
        if self.tool_mode == "eraser":
            return
        hit = self.find_line_at(event.x, event.y)
        if hit is not None:
            self.moving_line         = hit
            self.move_grab_offset_y  = hit["y"] - event.y
            self.current_candle      = None   # cancel any in-progress candle
            self.panes[0]["canvas"].config(cursor="fleur")
            self._suppress_next_left_release = True

    def enter_line_mode(self, event: tk.Event) -> None:
        if self.tool_mode == "eraser":
            return
        self.line_mode  = True
        self.line_phase = 1
        # Use crosshair — "circle" cursor not available on macOS
        self.panes[0]["canvas"].config(cursor="crosshair")

    # ── Level line placement ─────────────────────────────────────────────────

    def snap_y_to_candle_levels(self, y: float) -> float:
        snap_threshold = 12
        best_dist = snap_threshold + 1
        best_y    = y
        for candle in self.panes[0]["candles"]:
            mid = (candle["high_y"] + candle["low_y"]) / 2
            for level_y in (candle["open_y"], candle["high_y"],
                             candle["low_y"], candle["close_y"], mid):
                dist = abs(y - level_y)
                if dist < best_dist:
                    best_dist = dist
                    best_y    = level_y
        return best_y

    def commit_level_line(self, x1: int, y: float, x2: int) -> None:
        cv = self.panes[0]["canvas"]
        cv.delete("line_preview")
        cv.delete("snap_indicator")
        cv.delete("cursor_dot")
        if self.line_temp_dot_id:
            cv.delete(self.line_temp_dot_id)
        line_id = cv.create_line(x1, y, x2, y, fill="#FFFFFF", width=2)
        r       = 4
        dot_id  = cv.create_oval(x1 - r, y - r, x1 + r, y + r,
                                  fill="#FFFFFF", outline="")
        self.level_lines.append({
            "y": y, "x1": x1, "x2": x2,
            "canvas_line_id": line_id,
            "canvas_dot_id":  dot_id,
        })
        self.line_mode        = False
        self.line_phase       = 0
        self.line_start_x     = None
        self.line_start_y     = None
        self.line_snapped_y   = None
        self.line_temp_dot_id = None
        cv.config(cursor="crosshair")

    def exit_line_mode(self, event: tk.Event | None = None) -> None:
        cv = (self.panes[0]["canvas"]
              if self.panes and self.panes[0]["canvas"] else None)
        if cv:
            cv.delete("line_preview")
            cv.delete("snap_indicator")
            cv.delete("cursor_dot")
            if self.line_temp_dot_id:
                cv.delete(self.line_temp_dot_id)
            cv.config(cursor="crosshair")
        self.line_mode        = False
        self.line_phase       = 0
        self.line_start_x     = None
        self.line_start_y     = None
        self.line_snapped_y   = None
        self.line_temp_dot_id = None

    def find_line_at(self, x: int, y: int) -> dict | None:
        hit_threshold = 6
        for line in self.level_lines:
            if abs(y - line["y"]) <= hit_threshold:
                x_min = min(line["x1"], line["x2"]) - hit_threshold
                x_max = max(line["x1"], line["x2"]) + hit_threshold
                if x_min <= x <= x_max:
                    return line
        return None

    # ── Pencil / Eraser mode ─────────────────────────────────────────────────

    def toggle_pencil(self) -> None:
        self.pencil_state = (self.pencil_state + 1) % 3
        self.update_pencil_visual()

    def update_pencil_visual(self) -> None:
        if self.pencil_state == 0:
            self.pencil_btn.config(bg=_BTN, fg=_DIM, relief=tk.FLAT)
            self.pencil_border_frame.config(bg=_TB)
        elif self.pencil_state == 1:
            self.pencil_btn.config(bg="#2d4a1e", fg="#7ec850", relief=tk.SUNKEN)
            self.pencil_border_frame.config(bg=_TB)
        else:
            self.pencil_btn.config(bg="#2d4a1e", fg="#7ec850", relief=tk.SUNKEN)
            self.pencil_border_frame.config(bg="#FFD700")

    def _toggle_eraser(self) -> None:
        if self.tool_mode == "eraser":
            self.tool_mode = "pencil"
            self._eraser_btn.config(bg="#4a1e1e", fg="#e05555", relief=tk.FLAT)
            if self.panes and self.panes[0]["canvas"]:
                self.panes[0]["canvas"].config(cursor="crosshair")
        else:
            self.tool_mode = "eraser"
            self._eraser_btn.config(bg="#6a2a2a", fg="#ff6666", relief=tk.SUNKEN)
            if self.panes and self.panes[0]["canvas"]:
                self.panes[0]["canvas"].config(cursor="X_cursor")

    # ── Eraser ───────────────────────────────────────────────────────────────

    def erase_at(self, x: int, y: int) -> None:
        eraser_r = 18
        cv = self.panes[0]["canvas"]

        for line in self.level_lines[:]:
            if abs(y - line["y"]) <= eraser_r:
                x_min = min(line["x1"], line["x2"]) - eraser_r
                x_max = max(line["x1"], line["x2"]) + eraser_r
                if x_min <= x <= x_max:
                    cv.delete(line["canvas_line_id"])
                    cv.delete(line["canvas_dot_id"])
                    self.level_lines.remove(line)

        for stroke in self.freehand_strokes[:]:
            pts = stroke["points"]
            hit = False
            for i in range(len(pts) - 1):
                x1, y1 = pts[i]
                x2, y2 = pts[i + 1]
                dx, dy = x2 - x1, y2 - y1
                if dx == 0 and dy == 0:
                    dist = math.hypot(x - x1, y - y1)
                else:
                    t    = max(0.0, min(1.0,
                               ((x - x1) * dx + (y - y1) * dy)
                               / (dx * dx + dy * dy)))
                    dist = math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))
                if dist <= eraser_r:
                    hit = True
                    break
            if hit:
                cv.delete(stroke["canvas_id"])
                self.freehand_strokes.remove(stroke)

    # ── Undo / Redo ──────────────────────────────────────────────────────────

    def undo_candle(self) -> None:
        if not self.panes[0]["candles"]:
            return
        self.redo_stack.append(self.panes[0]["candles"].pop())
        self.recompute_all_htf()
        self.redraw_canvas()
        self.update_undo_redo_buttons()

    def redo_candle(self) -> None:
        if not self.redo_stack:
            return
        self.panes[0]["candles"].append(self.redo_stack.pop())
        self.recompute_all_htf()
        self.redraw_canvas()
        self.update_undo_redo_buttons()

    def update_undo_redo_buttons(self) -> None:
        self.undo_btn.config(
            state=tk.NORMAL if self.panes[0]["candles"] else tk.DISABLED)
        self.redo_btn.config(
            state=tk.NORMAL if self.redo_stack else tk.DISABLED)

    # ── Replay system ────────────────────────────────────────────────────────

    def _start_replay(self) -> None:
        if self.is_replaying or not self.panes[0]["candles"]:
            return
        self._replay_thread = threading.Thread(
            target=self._replay_loop, daemon=True)
        self._replay_thread.start()

    def _stop_replay(self) -> None:
        self.is_replaying = False

    def _replay_loop(self) -> None:
        self.is_replaying = True
        while self.is_replaying:
            self._run_single_replay()
            if not self.loop_replay.get():
                break
        self.is_replaying = False
        self.root.after(0, self.redraw_canvas)

    def _run_single_replay(self) -> None:
        replayed: list[dict] = []
        for candle in list(self.panes[0]["candles"]):
            if not self.is_replaying:
                return
            self._animate_candle(candle, replayed)
            replayed.append(candle)

    def _animate_candle(self, candle: dict, replayed: list[dict]) -> None:
        positions = candle["mouse_positions"]
        if not positions:
            return
        open_y = candle["open_y"]
        for j, y in enumerate(positions):
            if not self.is_replaying:
                return
            frame: dict = {
                "open_y":          open_y,
                "high_y":          min(positions[:j + 1]),
                "low_y":           max(positions[:j + 1]),
                "close_y":         y,
                "x":               candle["x"],
                "index":           candle["index"],
                "mouse_positions": positions[:j + 1],
                "took_high":       candle.get("took_high", False),
                "took_low":        candle.get("took_low",  False),
            }
            pane_lists = self._compute_panes_for_replay(replayed, frame)

            def paint(f=frame, rs=list(replayed), pl=list(pane_lists)) -> None:
                self._draw_replay_frame(f, rs, pl)

            self.root.after(0, paint)
            time.sleep(self.replay_speed.get())

    def _draw_replay_frame(self, frame: dict, replayed: list[dict],
                            pane_lists: list[list]) -> None:
        cv0 = self.panes[0]["canvas"]
        if cv0 is None:
            return
        cv0.delete("all")
        hw   = self.candle_width // 2
        prev = None
        for rc in replayed:
            self.draw_candle(cv0, rc, self.style_config, hw)
            if self.show_strat.get():
                self.draw_strat_label(cv0, rc, self.classify_candle(rc, prev),
                                      self.style_config)
            prev = rc
        self.draw_candle(cv0, frame, self.style_config, hw)
        if self.show_strat.get() and prev:
            self.draw_strat_label(cv0, frame, self.classify_candle(frame, prev),
                                  self.style_config)
        self._draw_tf_label(cv0, self.panes[0]["tf_label"])
        self._update_status(frame)

        hw_h = self.htf_candle_width // 2
        for p in range(1, self.pane_count):
            pane = self.panes[p]
            cvp  = pane["canvas"]
            if cvp is None:
                continue
            tf0   = self.panes[0]["tf_minutes"]
            ratio = max(1, pane["tf_minutes"] // tf0)
            cvp.delete("all")
            prev_h = None
            cl     = pane_lists[p - 1] if (p - 1) < len(pane_lists) else []
            for hc in cl:
                self.draw_candle(cvp, hc, self.style_config, hw_h)
                if not hc["is_complete"]:
                    self._draw_active_highlight(cvp, hc, ratio)
                if self.show_strat.get():
                    self.draw_strat_label(cvp, hc,
                                          self.classify_candle(hc, prev_h),
                                          self.style_config)
                prev_h = hc
            self._draw_tf_label(cvp, pane["tf_label"])

    # ── Status bar ───────────────────────────────────────────────────────────

    def _update_status(self, candle: dict) -> None:
        o, h, l, c = (candle["open_y"], candle["high_y"],
                      candle["low_y"],  candle["close_y"])
        direction = "Bullish" if c < o else "Bearish"
        self.status_var.set(
            f"O:{o:.0f}  H:{h:.0f}  L:{l:.0f}  C:{c:.0f}  [{direction}]")

    # ── Strat toggle ─────────────────────────────────────────────────────────

    def _on_strat_toggle(self) -> None:
        self.redraw_canvas()

    # ── Style Settings modal ─────────────────────────────────────────────────

    def _open_style_settings(self) -> None:
        modal = tk.Toplevel(self.root)
        modal.title("Candle Style Settings")
        modal.configure(bg=_TB)
        modal.resizable(False, False)
        modal.grab_set()

        COLOR_FIELDS = [
            ("Bull Body Color", "bull_body_color"),
            ("Bear Body Color", "bear_body_color"),
            ("Wick Color",      "wick_color"),
            ("Border Color",    "border_color"),
        ]
        for row_idx, (label_text, field) in enumerate(COLOR_FIELDS):
            frame = tk.Frame(modal, bg=_TB)
            frame.grid(row=row_idx, column=0, sticky="w", padx=14, pady=5)
            tk.Label(frame, text=f"{label_text}:", bg=_TB, fg=_BTN_FG,
                     width=17, anchor="w").pack(side=tk.LEFT)
            cur_color = getattr(self.style_config, field)
            swatch = tk.Button(frame, bg=cur_color, width=5, height=1,
                               relief=tk.RAISED)
            swatch.pack(side=tk.LEFT, padx=6)

            def make_pick(fld=field, btn=swatch):
                def pick():
                    cur    = getattr(self.style_config, fld)
                    result = colorchooser.askcolor(
                        color=cur,
                        title=f"Choose {fld.replace('_', ' ').title()}")
                    if result and result[1]:
                        setattr(self.style_config, fld, result[1])
                        btn.configure(bg=result[1])
                return pick
            swatch.configure(command=make_pick())

        nrow = len(COLOR_FIELDS)

        tf = tk.Frame(modal, bg=_TB)
        tf.grid(row=nrow, column=0, sticky="w", padx=14, pady=5)
        tk.Label(tf, text="Border Thickness:", bg=_TB, fg=_BTN_FG,
                 width=17, anchor="w").pack(side=tk.LEFT)
        thick_var = tk.IntVar(value=self.style_config.border_thickness)
        tk.Spinbox(tf, from_=0, to=5, textvariable=thick_var, width=5,
                   bg=_BTN, fg=_BTN_FG,
                   buttonbackground=_BTN).pack(side=tk.LEFT)

        pf = tk.Frame(modal, bg=_TB)
        pf.grid(row=nrow + 1, column=0, sticky="w", padx=14, pady=5)
        tk.Label(pf, text="Label Position:", bg=_TB, fg=_BTN_FG,
                 width=17, anchor="w").pack(side=tk.LEFT)
        disp_to_val = {
            "Below body":  "below",
            "Above wick":  "above",
            "Inside body": "inside",
        }
        val_to_disp = {v: k for k, v in disp_to_val.items()}
        pos_var = tk.StringVar(
            value=val_to_disp.get(self.style_config.label_position, "Below body"))
        ttk.OptionMenu(pf, pos_var, pos_var.get(),
                       *disp_to_val).pack(side=tk.LEFT)

        def apply_all() -> None:
            self.style_config.border_thickness = thick_var.get()
            self.style_config.label_position   = disp_to_val[pos_var.get()]
            self.redraw_canvas()

        tk.Button(modal, text="Apply", command=apply_all,
                  bg="#2d4a2d", fg="#88cc88",
                  padx=14, pady=5).grid(row=nrow + 2, column=0, pady=12)

    # ── Window close ─────────────────────────────────────────────────────────

    def _on_closing(self) -> None:
        self.is_replaying = False
        self.root.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    root = tk.Tk()
    CandlestickDrawerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
