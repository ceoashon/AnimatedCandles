"""
AnimatedCandles — Candlestick Chart Drawing and Teaching Tool
Requirements: Python 3.11+, stdlib only (no pip packages).
Run:   python3 animated_candles.py
Build: pyinstaller AnimatedCandles.spec
"""

from dataclasses import dataclass
import math
import tkinter as tk
from tkinter import ttk, colorchooser
import time
import threading


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
        self.root.configure(bg="#1a1a2e")

        self.style_config = StyleConfig()

        self._canvas_w      = 540
        self._canvas_h      = 480
        self.candle_width   = 20
        self.candle_spacing = 8
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
        self.pane_count       = 2
        self.pane_orientation = "row"   # "row" | "grid"

        for tf_str in TF_DEFAULTS:
            self.panes.append({
                "canvas":      None,        # tk.Canvas — set by rebuild_pane_layout
                "tf_minutes":  TF_MINUTES[tf_str],
                "tf_label":    tf_str,
                "candles":     [],
                "tf_var":      tk.StringVar(value=tf_str),
                "tf_selector": None,        # ttk.OptionMenu widget
            })

        # ── Pencil state ───────────────────────────────────────────────────
        self.pencil_state              = 0   # 0=off, 1=temp, 2=persist
        self.freehand_strokes: list[dict] = []
        self._current_stroke_points: list[tuple[int, int]] = []

        # ── Level lines ────────────────────────────────────────────────────
        # Each entry: {"y", "x1", "x2", "canvas_line_id", "canvas_dot_id"}
        self.level_lines: list[dict] = []
        self.line_mode              = False
        self.line_phase             = 0     # 0=idle, 1=await start, 2=await end
        self.line_start_x: int | None   = None
        self.line_start_y: float | None = None
        self.line_snapped_y: float | None = None
        self.line_temp_dot_id: int | None = None
        self._suppress_next_right_press   = False

        # Moving an existing line
        self.moving_line: dict | None = None
        self.move_grab_offset_y       = 0
        self._suppress_next_left_release = False

        # ── Eraser ────────────────────────────────────────────────────────
        self.tool_mode  = "pencil"   # "pencil" | "eraser"
        self.is_erasing = False

        # ── Undo / redo ───────────────────────────────────────────────────
        self.redo_stack: list[dict] = []

        self._setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.root.bind("<Escape>", self.exit_line_mode)

    # ── Property alias ───────────────────────────────────────────────────────

    @property
    def ltf_candles(self) -> list:
        """Alias for panes[0]['candles']."""
        return self.panes[0]["candles"]

    # ── UI construction ──────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        BG = "#1a1a2e"
        TB = "#16213e"
        EB = "#0f3460"

        toolbar = tk.Frame(self.root, bg=TB, pady=4)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        def sep() -> None:
            tk.Label(toolbar, text="|", bg=TB, fg="#444",
                     padx=4, font=("Helvetica", 11)).pack(side=tk.LEFT)

        def tbtn(text: str, cmd, bg: str = EB, **kw) -> tk.Button:
            b = tk.Button(toolbar, text=text, command=cmd,
                          bg=bg, fg="white",
                          activebackground=bg, activeforeground="white",
                          relief=tk.FLAT, padx=6, pady=2, **kw)
            b.pack(side=tk.LEFT, padx=(0, 4))
            return b

        # ── Undo / Redo ────────────────────────────────────────────────────
        self.undo_btn = tbtn("< Back",    self.undo_candle,  state=tk.DISABLED)
        self.redo_btn = tbtn("Forward >", self.redo_candle,  state=tk.DISABLED)
        sep()

        # ── Pane count [1][2][3][4] ────────────────────────────────────────
        tk.Label(toolbar, text="Panes:", bg=TB, fg="white",
                 font=("Helvetica", 10)).pack(side=tk.LEFT, padx=(0, 4))
        self._pane_count_btns: list[tk.Button] = []
        for n in range(1, 5):
            b = tk.Button(
                toolbar, text=str(n), width=2,
                command=lambda x=n: self.set_pane_count(x),
                bg=EB, fg="white",
                relief=tk.SUNKEN if n == self.pane_count else tk.RAISED,
                padx=4, pady=2)
            b.pack(side=tk.LEFT, padx=(0, 2))
            self._pane_count_btns.append(b)

        # Orientation toggle — always packed, disabled when pane_count==1
        self._orient_btn = tk.Button(
            toolbar, text="", width=7,
            command=self.toggle_orientation,
            bg=EB, fg="white",
            activebackground=EB, activeforeground="white",
            relief=tk.FLAT, padx=4, pady=2)
        self._orient_btn.pack(side=tk.LEFT, padx=(4, 4))
        self.update_orientation_button_label()
        self.update_orientation_button_visibility()
        sep()

        # ── TF selectors in their own sub-frame (grid so hide/show is ordered) ──
        self._tf_frame = tk.Frame(toolbar, bg=TB)
        self._tf_frame.pack(side=tk.LEFT)
        self._tf_selector_frames: list[tk.Frame] = []

        for i, pane in enumerate(self.panes):
            frame = tk.Frame(self._tf_frame, bg=TB)
            frame.grid(row=0, column=i, padx=(0, 6))
            tk.Label(frame, text=f"P{i + 1}:", bg=TB, fg="white",
                     font=("Helvetica", 10)).pack(side=tk.LEFT)
            menu = ttk.OptionMenu(
                frame, pane["tf_var"], pane["tf_label"], *TF_OPTIONS,
                command=lambda _v, p=i: self._on_tf_change(p))
            menu.pack(side=tk.LEFT)
            pane["tf_selector"] = menu
            self._tf_selector_frames.append(frame)

        # TF validation error label sits in column 4 of the tf_frame grid
        self._tf_error_lbl = tk.Label(
            self._tf_frame, textvariable=self.tf_error_var,
            bg=TB, fg="#ff5555", font=("Helvetica", 9))
        self._tf_error_lbl.grid(row=0, column=4, padx=(0, 4))
        sep()

        # ── Pencil three-state + Eraser ────────────────────────────────────
        self.pencil_border_frame = tk.Frame(toolbar, bd=2, relief=tk.FLAT, bg=TB)
        self.pencil_btn = tk.Button(
            self.pencil_border_frame, text="Draw",
            command=self.toggle_pencil,
            bg=TB, fg="white",
            activebackground=TB, activeforeground="white",
            relief=tk.RAISED, padx=6, pady=2)
        self.pencil_btn.pack()
        self.pencil_border_frame.pack(side=tk.LEFT, padx=(0, 4))

        self._eraser_btn = tk.Button(
            toolbar, text="Erase", command=self._toggle_eraser,
            bg="#3d2a00", fg="white",
            activebackground="#3d2a00", activeforeground="white",
            relief=tk.FLAT, padx=6, pady=2)
        self._eraser_btn.pack(side=tk.LEFT, padx=(0, 4))
        sep()

        # ── Style / Strat ──────────────────────────────────────────────────
        tbtn("Style", self._open_style_settings, "#5a0090")
        tk.Checkbutton(
            toolbar, text="Strat #s", variable=self.show_strat,
            command=self._on_strat_toggle,
            bg=TB, fg="white", selectcolor="#5a0090",
            activebackground=TB, activeforeground="white",
        ).pack(side=tk.LEFT, padx=(0, 8))
        sep()

        # ── Replay ────────────────────────────────────────────────────────
        tbtn("Replay", self._start_replay, "#1a5e3a")
        tbtn("Stop",   self._stop_replay,  "#6e1a1a")
        tk.Checkbutton(
            toolbar, text="Loop", variable=self.loop_replay,
            bg=TB, fg="white", selectcolor="#5a0090",
            activebackground=TB, activeforeground="white",
        ).pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(toolbar, text="Speed:", bg=TB, fg="white",
                 font=("Helvetica", 10)).pack(side=tk.LEFT, padx=(0, 2))
        tk.Scale(
            toolbar, variable=self.replay_speed,
            from_=0.01, to=0.30, resolution=0.01,
            orient=tk.HORIZONTAL, length=100,
            bg=TB, fg="white", highlightthickness=0, troughcolor=EB,
        ).pack(side=tk.LEFT, padx=(0, 4))
        sep()

        # ── Canvas size + Clear ────────────────────────────────────────────
        tk.Label(toolbar, text="W:", bg=TB, fg="white",
                 font=("Helvetica", 10)).pack(side=tk.LEFT, padx=(0, 2))
        self.width_entry = tk.Entry(toolbar, width=4, bg=EB, fg="white",
                                    insertbackground="white")
        self.width_entry.insert(0, str(self._canvas_w))
        self.width_entry.pack(side=tk.LEFT, padx=(0, 4))

        tk.Label(toolbar, text="H:", bg=TB, fg="white",
                 font=("Helvetica", 10)).pack(side=tk.LEFT, padx=(0, 2))
        self.height_entry = tk.Entry(toolbar, width=4, bg=EB, fg="white",
                                     insertbackground="white")
        self.height_entry.insert(0, str(self._canvas_h))
        self.height_entry.pack(side=tk.LEFT, padx=(0, 4))

        tbtn("Apply", self._apply_size)
        tbtn("Clear", self._clear_all, "#3d0000")

        # ── Main canvas area ───────────────────────────────────────────────
        self._main_frame = tk.Frame(self.root, bg="#1a1a2e")
        self._main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # ── Status bar ────────────────────────────────────────────────────
        tk.Label(
            self.root, textvariable=self.status_var,
            bg=TB, fg="white", anchor=tk.W, padx=8, pady=3,
            font=("Courier", 10),
        ).pack(side=tk.BOTTOM, fill=tk.X)

        # Initial layout
        self.rebuild_pane_layout()
        self.update_tf_selector_visibility()

    # ── Pane count / orientation management ─────────────────────────────────

    def set_pane_count(self, n: int) -> None:
        """Switch to n active panes and rebuild layout."""
        self.pane_count = n
        self.redo_stack.clear()
        for i, b in enumerate(self._pane_count_btns):
            b.config(relief=tk.SUNKEN if i + 1 == n else tk.RAISED)
        self.rebuild_pane_layout()
        self.update_tf_selector_visibility()
        self.update_orientation_button_visibility()
        self.redraw_canvas()

    def toggle_orientation(self) -> None:
        """Toggle between row and grid (stacked/2x2) layout."""
        self.pane_orientation = (
            "grid" if self.pane_orientation == "row" else "row")
        self.rebuild_pane_layout()
        self.update_orientation_button_label()
        self.redraw_canvas()

    def update_orientation_button_label(self) -> None:
        """Update the orientation button schematic text."""
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
        """Disable orientation button for single-pane mode."""
        if self.pane_count == 1:
            self._orient_btn.config(state=tk.DISABLED, fg="#555555")
        else:
            self._orient_btn.config(state=tk.NORMAL, fg="white")

    def update_tf_selector_visibility(self) -> None:
        """Show TF selectors for active panes; hide others via grid_remove."""
        for i, frame in enumerate(self._tf_selector_frames):
            if i < self.pane_count:
                frame.grid(row=0, column=i, padx=(0, 6))
            else:
                frame.grid_remove()

    def _on_tf_change(self, pane_idx: int) -> None:
        """Handle TF OptionMenu change for one pane."""
        pane = self.panes[pane_idx]
        tf_str = pane["tf_var"].get()
        pane["tf_minutes"] = TF_MINUTES.get(tf_str, 5)
        pane["tf_label"]   = tf_str
        self._validate_tfs()
        self.recompute_all_htf()
        self.redraw_canvas()

    def _validate_tfs(self) -> bool:
        """Ensure each active HTF pane's TF exceeds pane 0's TF."""
        tf0 = self.panes[0]["tf_minutes"]
        for p in range(1, self.pane_count):
            if self.panes[p]["tf_minutes"] <= tf0:
                self.tf_error_var.set(f"P{p + 1} TF must be > P1 TF")
                return False
        self.tf_error_var.set("")
        return True

    # ── Layout rebuild ───────────────────────────────────────────────────────

    def rebuild_pane_layout(self) -> None:
        """Destroy all canvas widgets and recreate according to count/orientation."""
        for w in self._main_frame.winfo_children():
            w.destroy()
        for pane in self.panes:
            pane["canvas"] = None

        BG = "#1a1a2e"

        def make_canvas_frame(parent: tk.Widget, pane_idx: int) -> tk.Frame:
            """Create a Frame containing one canvas, store canvas in pane dict."""
            pane  = self.panes[pane_idx]
            outer = tk.Frame(parent, bg=BG)
            cv    = tk.Canvas(
                outer, bg="#0a0a0f",
                highlightthickness=2,
                highlightbackground=_PANE_BORDERS[pane_idx % 4])
            cv.pack(fill=tk.BOTH, expand=True)
            pane["canvas"] = cv
            return outer

        n = self.pane_count
        o = self.pane_orientation
        PAD = {"padx": 4, "pady": 4}

        if n == 1:
            make_canvas_frame(self._main_frame, 0).pack(
                fill=tk.BOTH, expand=True, **PAD)

        elif n == 2:
            if o == "row":
                row = tk.Frame(self._main_frame, bg=BG)
                row.pack(fill=tk.BOTH, expand=True)
                for i in range(2):
                    make_canvas_frame(row, i).pack(
                        side=tk.LEFT, fill=tk.BOTH, expand=True, **PAD)
            else:  # stacked
                col = tk.Frame(self._main_frame, bg=BG)
                col.pack(fill=tk.BOTH, expand=True)
                for i in range(2):
                    make_canvas_frame(col, i).pack(
                        fill=tk.BOTH, expand=True, **PAD)

        elif n == 3:
            if o == "row":
                row = tk.Frame(self._main_frame, bg=BG)
                row.pack(fill=tk.BOTH, expand=True)
                for i in range(3):
                    make_canvas_frame(row, i).pack(
                        side=tk.LEFT, fill=tk.BOTH, expand=True, **PAD)
            else:  # 1 top + 2 bottom
                col = tk.Frame(self._main_frame, bg=BG)
                col.pack(fill=tk.BOTH, expand=True)
                make_canvas_frame(col, 0).pack(
                    fill=tk.BOTH, expand=True,
                    padx=4, pady=(4, 2))
                bot = tk.Frame(col, bg=BG)
                bot.pack(fill=tk.BOTH, expand=True)
                for i in range(1, 3):
                    make_canvas_frame(bot, i).pack(
                        side=tk.LEFT, fill=tk.BOTH, expand=True,
                        padx=4, pady=(2, 4))

        elif n == 4:
            if o == "row":
                row = tk.Frame(self._main_frame, bg=BG)
                row.pack(fill=tk.BOTH, expand=True)
                for i in range(4):
                    make_canvas_frame(row, i).pack(
                        side=tk.LEFT, fill=tk.BOTH, expand=True, **PAD)
            else:  # 2×2
                col = tk.Frame(self._main_frame, bg=BG)
                col.pack(fill=tk.BOTH, expand=True)
                top = tk.Frame(col, bg=BG)
                top.pack(fill=tk.BOTH, expand=True)
                for i in range(2):
                    make_canvas_frame(top, i).pack(
                        side=tk.LEFT, fill=tk.BOTH, expand=True,
                        padx=4, pady=(4, 2))
                bot = tk.Frame(col, bg=BG)
                bot.pack(fill=tk.BOTH, expand=True)
                for i in range(2, 4):
                    make_canvas_frame(bot, i).pack(
                        side=tk.LEFT, fill=tk.BOTH, expand=True,
                        padx=4, pady=(2, 4))

        self._bind_pane0()

    def _bind_pane0(self) -> None:
        """Apply all interactive mouse bindings to pane 0's canvas."""
        cv = self.panes[0]["canvas"]
        cv.bind("<ButtonPress-1>",   self._start_candle)
        cv.bind("<B1-Motion>",       self._drag_candle)
        cv.bind("<ButtonRelease-1>", self._end_candle)
        cv.bind("<ButtonPress-3>",   self._start_drawing)
        cv.bind("<B3-Motion>",       self._drag_drawing)
        cv.bind("<ButtonRelease-3>", self._end_drawing)
        cv.bind("<Double-Button-3>", self.enter_line_mode)
        cv.bind("<Double-Button-1>", self.on_double_left_click)
        cv.bind("<Motion>",          self.on_mouse_motion)
        cv.config(cursor="crosshair")

    # ── Canvas size ──────────────────────────────────────────────────────────

    def _apply_size(self) -> None:
        """Resize canvases from the W/H entry fields."""
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
        """Wipe all candles, level lines, and freehand strokes."""
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
        """Aggregate pane 0 LTF candles into all active HTF panes."""
        ltf = self.panes[0]["candles"]
        cur = self.current_candle if current_override is ... else current_override
        all_ltf = list(ltf) + ([cur] if cur is not None else [])
        tf0     = self.panes[0]["tf_minutes"]

        for p in range(1, self.pane_count):
            pane  = self.panes[p]
            ratio = max(1, pane["tf_minutes"] // tf0)
            pane["candles"] = self._aggregate(all_ltf, ltf, ratio, p)

    def _aggregate(self, all_ltf: list, finalized_ltf: list,
                   ratio: int, pane_idx: int) -> list:
        """Group LTF candles into one HTF candle list for a pane."""
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
        """Return aggregated HTF lists for all panes for one replay frame."""
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
        """Return Strat label for curr relative to prev (1, 2U, 2D, F2U, F2D, 3)."""
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
        """Draw one candle (wick + body) onto canvas."""
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
        """Draw a Strat number label near the candle."""
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
        else:  # below
            canvas.create_text(x, candle["low_y"] + 12, text=label,
                                fill="white", font=("Helvetica", 11, "bold"))

    def _draw_tf_label(self, canvas: tk.Canvas, label: str) -> None:
        """Draw a small TF badge at the top-left of a canvas."""
        canvas.create_rectangle(0, 0, 60, 20, fill="#1E1E1E", outline="")
        canvas.create_text(30, 10, text=label, fill="#FFFFFF",
                           font=("TkDefaultFont", 10))

    def _draw_active_highlight(self, canvas: tk.Canvas,
                                hc: dict, ratio: int) -> None:
        """Amber dashed highlight on the in-progress HTF candle."""
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
        """Recompute HTF and repaint all active panes — single source of truth."""
        self.recompute_all_htf()
        self._redraw_pane0()
        for p in range(1, self.pane_count):
            self._redraw_htf_pane(p)

    def _redraw_pane0(self) -> None:
        """Repaint pane 0: candles, level lines, freehand strokes, TF label."""
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
        # Level lines
        for line in self.level_lines:
            line["canvas_line_id"] = cv.create_line(
                line["x1"], line["y"], line["x2"], line["y"],
                fill="#FFFFFF", width=2)
            r = 4
            line["canvas_dot_id"] = cv.create_oval(
                line["x1"] - r, line["y"] - r,
                line["x1"] + r, line["y"] + r,
                fill="#FFFFFF", outline="")
        # Persistent freehand strokes
        for stroke in self.freehand_strokes:
            if len(stroke["points"]) >= 2:
                new_id = cv.create_line(
                    *[c for pt in stroke["points"] for c in pt],
                    fill="yellow", width=2, tags="freehand")
                stroke["canvas_id"] = new_id
        self._draw_tf_label(cv, self.panes[0]["tf_label"])

    def _redraw_htf_pane(self, pane_idx: int) -> None:
        """Repaint one HTF pane."""
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

    # ── Mouse handlers (pane 0 only) ─────────────────────────────────────────

    def _start_candle(self, event: tk.Event) -> None:
        """ButtonPress-1: start candle draw, eraser stroke, or no-op."""
        if self.is_replaying:
            return
        if self.tool_mode == "eraser":
            self.is_erasing = True
            self.erase_at(event.x, event.y)
            return
        if self.moving_line is not None:
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
        """B1-Motion: extend candle drag or continue erasing."""
        if self.is_erasing:
            self.erase_at(event.x, event.y)
            return
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
        """ButtonRelease-1: finalise candle, drop moved line, or stop eraser."""
        # Drop a line that was being dragged
        if self.moving_line is not None:
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
            return

        if self._suppress_next_left_release:
            self._suppress_next_left_release = False
            return

        if self.is_erasing:
            self.is_erasing = False
            return

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

    def _start_drawing(self, event: tk.Event) -> None:
        """ButtonPress-3: handle line mode or start freehand stroke."""
        if self._suppress_next_right_press:
            self._suppress_next_right_press = False
            return

        # Line mode — first right-click sets start point
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

        # Line mode — second right-click commits the line
        if self.line_mode and self.line_phase == 2:
            self.commit_level_line(self.line_start_x,
                                   self.line_start_y, event.x)
            return

        if self.pencil_state == 0 or self.is_replaying:
            return

        self.is_drawing = True
        self._current_stroke_points = [(event.x, event.y)]

    def _drag_drawing(self, event: tk.Event) -> None:
        """B3-Motion: extend freehand stroke segment."""
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
        """ButtonRelease-3: finish freehand stroke."""
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
        """<Motion> on pane 0: update moving line or show placement preview."""
        # Drag an existing level line
        if self.moving_line is not None:
            raw_y     = event.y + self.move_grab_offset_y
            snapped_y = self.snap_y_to_candle_levels(raw_y)
            delta_y   = snapped_y - self.moving_line["y"]
            self.moving_line["y"] = snapped_y
            cv = self.panes[0]["canvas"]
            cv.move(self.moving_line["canvas_line_id"], 0, delta_y)
            cv.move(self.moving_line["canvas_dot_id"],  0, delta_y)
            return

        # Show snap indicator and line preview during placement
        if self.line_mode:
            snapped_y           = self.snap_y_to_candle_levels(event.y)
            self.line_snapped_y = snapped_y
            cv = self.panes[0]["canvas"]
            cv.delete("snap_indicator")
            cv.create_line(event.x - 8, snapped_y,
                           event.x + 8, snapped_y,
                           fill="#FFD700", width=1, tags="snap_indicator")
            if self.line_phase == 2:
                cv.delete("line_preview")
                cv.create_line(self.line_start_x, self.line_start_y,
                               event.x, self.line_start_y,
                               fill="#FFFFFF", width=1, dash=(4, 4),
                               tags="line_preview")

    # ── Double-click handlers ────────────────────────────────────────────────

    def on_double_left_click(self, event: tk.Event) -> None:
        """Double-left-click: grab an existing level line for dragging."""
        if self.tool_mode == "eraser":
            return
        hit = self.find_line_at(event.x, event.y)
        if hit is not None:
            self.moving_line        = hit
            self.move_grab_offset_y = hit["y"] - event.y
            self.panes[0]["canvas"].config(cursor="fleur")
            self._suppress_next_left_release = True

    def enter_line_mode(self, event: tk.Event) -> None:
        """Double-right-click: enter horizontal level line placement mode."""
        if self.tool_mode == "eraser":
            return
        self.line_mode  = True
        self.line_phase = 1
        self.panes[0]["canvas"].config(cursor="circle")
        self._suppress_next_right_press = True

    # ── Level line placement ─────────────────────────────────────────────────

    def snap_y_to_candle_levels(self, y: float) -> float:
        """Snap y to the nearest candle OHLC / midpoint level within threshold."""
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
        """Finalise and store a horizontal level line on the canvas."""
        cv = self.panes[0]["canvas"]
        cv.delete("line_preview")
        cv.delete("snap_indicator")
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
        self.panes[0]["canvas"].config(cursor="crosshair")

    def exit_line_mode(self, event: tk.Event | None = None) -> None:
        """Escape: cancel any in-progress level line placement."""
        cv = (self.panes[0]["canvas"]
              if self.panes and self.panes[0]["canvas"] else None)
        if cv:
            cv.delete("line_preview")
            cv.delete("snap_indicator")
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
        """Return the first level line under (x, y) or None."""
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
        """Cycle pencil state: 0 (off) → 1 (temp) → 2 (persist) → 0."""
        self.pencil_state = (self.pencil_state + 1) % 3
        self.update_pencil_visual()

    def update_pencil_visual(self) -> None:
        """Reflect current pencil state in button appearance."""
        TB = "#16213e"
        if self.pencil_state == 0:
            self.pencil_btn.config(relief=tk.RAISED)
            self.pencil_border_frame.config(bg=TB)
        elif self.pencil_state == 1:
            self.pencil_btn.config(relief=tk.SUNKEN)
            self.pencil_border_frame.config(bg=TB)
        else:  # 2 — gold border signals persistent mode
            self.pencil_btn.config(relief=tk.SUNKEN)
            self.pencil_border_frame.config(bg="#FFD700")

    def _toggle_eraser(self) -> None:
        """Toggle between eraser and pencil tool mode."""
        if self.tool_mode == "eraser":
            self.tool_mode = "pencil"
            self._eraser_btn.config(relief=tk.FLAT)
            if self.panes and self.panes[0]["canvas"]:
                self.panes[0]["canvas"].config(cursor="crosshair")
        else:
            self.tool_mode = "eraser"
            self._eraser_btn.config(relief=tk.SUNKEN)
            if self.panes and self.panes[0]["canvas"]:
                self.panes[0]["canvas"].config(cursor="X_cursor")

    # ── Eraser ───────────────────────────────────────────────────────────────

    def erase_at(self, x: int, y: int) -> None:
        """Delete level lines and freehand strokes within the eraser radius."""
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
        """Remove the last drawn LTF candle and push it onto the redo stack."""
        if not self.panes[0]["candles"]:
            return
        self.redo_stack.append(self.panes[0]["candles"].pop())
        self.recompute_all_htf()
        self.redraw_canvas()
        self.update_undo_redo_buttons()

    def redo_candle(self) -> None:
        """Restore the last undone LTF candle from the redo stack."""
        if not self.redo_stack:
            return
        self.panes[0]["candles"].append(self.redo_stack.pop())
        self.recompute_all_htf()
        self.redraw_canvas()
        self.update_undo_redo_buttons()

    def update_undo_redo_buttons(self) -> None:
        """Enable or disable Back/Forward buttons based on stack state."""
        self.undo_btn.config(
            state=tk.NORMAL if self.panes[0]["candles"] else tk.DISABLED)
        self.redo_btn.config(
            state=tk.NORMAL if self.redo_stack else tk.DISABLED)

    # ── Replay system ────────────────────────────────────────────────────────

    def _start_replay(self) -> None:
        """Start replay animation in a background thread."""
        if self.is_replaying or not self.panes[0]["candles"]:
            return
        self._replay_thread = threading.Thread(
            target=self._replay_loop, daemon=True)
        self._replay_thread.start()

    def _stop_replay(self) -> None:
        """Signal the replay thread to stop."""
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
        """Replay a single candle frame-by-frame."""
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
        """Paint all panes for one replay frame (runs on main thread)."""
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
        """Open the Candle Style Settings modal window."""
        modal = tk.Toplevel(self.root)
        modal.title("Candle Style Settings")
        modal.configure(bg="#16213e")
        modal.resizable(False, False)
        modal.grab_set()

        COLOR_FIELDS = [
            ("Bull Body Color", "bull_body_color"),
            ("Bear Body Color", "bear_body_color"),
            ("Wick Color",      "wick_color"),
            ("Border Color",    "border_color"),
        ]
        for row_idx, (label_text, field) in enumerate(COLOR_FIELDS):
            frame = tk.Frame(modal, bg="#16213e")
            frame.grid(row=row_idx, column=0, sticky="w", padx=14, pady=5)
            tk.Label(frame, text=f"{label_text}:", bg="#16213e", fg="white",
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

        tf = tk.Frame(modal, bg="#16213e")
        tf.grid(row=nrow, column=0, sticky="w", padx=14, pady=5)
        tk.Label(tf, text="Border Thickness:", bg="#16213e", fg="white",
                 width=17, anchor="w").pack(side=tk.LEFT)
        thick_var = tk.IntVar(value=self.style_config.border_thickness)
        tk.Spinbox(tf, from_=0, to=5, textvariable=thick_var, width=5,
                   bg="#0f3460", fg="white",
                   buttonbackground="#0f3460").pack(side=tk.LEFT)

        pf = tk.Frame(modal, bg="#16213e")
        pf.grid(row=nrow + 1, column=0, sticky="w", padx=14, pady=5)
        tk.Label(pf, text="Label Position:", bg="#16213e", fg="white",
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
                  bg="#5a0090", fg="white",
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
