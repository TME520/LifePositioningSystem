"""curses front-end for LPS Console (keyboard only, no mouse)."""
from __future__ import annotations

import curses
import locale
import os

from .app import App

# Big digits for the countdown (Companion draws a 4x scaled font).
BIG = {
    "0": ["###", "# #", "# #", "# #", "###"], "1": ["  #", "  #", "  #", "  #", "  #"],
    "2": ["###", "  #", "###", "#  ", "###"], "3": ["###", "  #", "###", "  #", "###"],
    "4": ["# #", "# #", "###", "  #", "  #"], "5": ["###", "#  ", "###", "  #", "###"],
    "6": ["###", "#  ", "###", "# #", "###"], "7": ["###", "  #", "  #", "  #", "  #"],
    "8": ["###", "# #", "###", "# #", "###"], "9": ["###", "# #", "###", "  #", "###"],
    ":": ["   ", " # ", "   ", " # ", "   "],
}
PALETTE_COLORS = [curses.COLOR_RED, curses.COLOR_GREEN, curses.COLOR_BLUE, curses.COLOR_YELLOW, curses.COLOR_MAGENTA]
MIN_W, MIN_H = 40, 20

KEYMAP = {
    curses.KEY_UP: "UP", curses.KEY_DOWN: "DOWN", curses.KEY_LEFT: "LEFT", curses.KEY_RIGHT: "RIGHT",
    curses.KEY_ENTER: "ENTER", curses.KEY_BACKSPACE: "BS", curses.KEY_DC: "DEL",
    curses.KEY_F1: "F1", curses.KEY_F2: "F2", curses.KEY_F3: "F3", curses.KEY_F4: "F4", curses.KEY_F5: "F5",
}


def big_text(value: str):
    rows = ["", "", "", "", ""]
    for ch in value:
        g = BIG.get(ch, BIG[":"])
        for i in range(5):
            rows[i] += "".join(c * 2 for c in g[i]).replace("#", "\u2588") + "  "
    return rows


def translate(ch) -> str:
    if isinstance(ch, str):
        if ch in ("\n", "\r"):
            return "ENTER"
        if ch == "\x1b":
            return "ESC"
        if ch in ("\x7f", "\b"):
            return "BS"
        if ch == "\t":
            return "TAB"
        return ch if ch.isprintable() else ""
    if ch == curses.KEY_RESIZE:
        return "RESIZE"
    return KEYMAP.get(ch, "")


class Tui:
    def __init__(self, app: App):
        self.app = app
        self.colors = False

    def setup(self, scr):
        curses.curs_set(0)
        scr.keypad(True)
        scr.timeout(200)
        curses.mousemask(0)  # mouse explicitly unused
        if curses.has_colors():
            try:
                curses.start_color()
                curses.use_default_colors()
                for i, c in enumerate(PALETTE_COLORS):
                    curses.init_pair(i + 1, c, -1)
                curses.init_pair(10, curses.COLOR_RED, -1)
                self.colors = True
            except curses.error:
                self.colors = False

    def attr(self, style: str) -> int:
        app = self.app
        pal = app.pending_palette if app.screen == "Colors" else app.state.palette
        base = curses.color_pair(pal + 1) if self.colors else 0
        if style == "rev":
            return base | curses.A_REVERSE
        if style in ("alert", "bigred"):
            return (curses.color_pair(10) if self.colors else 0) | curses.A_BOLD
        if style in ("bold", "big"):
            return base | curses.A_BOLD
        return base

    def draw(self, scr):
        scr.erase()
        h, w = scr.getmaxyx()
        if w < MIN_W or h < MIN_H:
            msg = ["LPS Console", "Too small/Trop petit", "min %dx%d (%dx%d)" % (MIN_W, MIN_H, w, h), "q: quit/quitter"]
            for i, t in enumerate(msg[:h]):
                self._put(scr, i, 0, t[:w - 1], 0)
            scr.refresh()
            return
        width = min(w, 80)
        left = (w - width) // 2
        self.app.width = width
        top = (h - 20) // 2 if h > 22 else 0
        for row, text, style in self.app.render():
            y = top + row
            if style in ("big", "bigred"):
                for i, t in enumerate(big_text(text)):
                    self._put(scr, y + i, left + max(0, (width - len(t)) // 2), t, self.attr(style))
                continue
            text = text[:width]
            if style == "rev":
                text = text.ljust(width)
            self._put(scr, y, left, text, self.attr(style))
        scr.refresh()

    @staticmethod
    def _put(scr, y, x, text, attr):
        h, w = scr.getmaxyx()
        if y >= h or x >= w:
            return
        try:
            scr.addstr(y, x, text[: max(0, w - x - (1 if y == h - 1 else 0))], attr)
        except curses.error:
            pass

    def loop(self, scr):
        self.setup(scr)
        self.draw(scr)
        while not self.app.quit:
            try:
                ch = scr.get_wch()
            except curses.error:
                ch = None
            if ch is None:
                if self.app.tick():
                    self.draw(scr)
                continue
            k = translate(ch)
            if k == "RESIZE":
                curses.update_lines_cols()
            elif k:
                h, w = scr.getmaxyx()
                if (w < MIN_W or h < MIN_H) and k in ("q", "ESC"):
                    break
                self.app.key(k)
            self.app.tick()
            self.draw(scr)


def run(app: App) -> None:
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")
    curses.wrapper(Tui(app).loop)
