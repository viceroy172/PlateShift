# SPDX-License-Identifier: AGPL-3.0-or-later
"""Type-ahead for dropdowns: typed letters jump to a matching row.

A row starting with the typed text wins, else a row with a word starting with it.
Pressing the same letter again cycles through the rows it matches, and a pause
of about a second starts a fresh search. Works on the closed box and on the open list.
"""

from __future__ import annotations

import re
import sys
import time
import tkinter as tk
from tkinter import ttk

RESET_AFTER = 1.0  # seconds without typing before a new search starts

# modifier bits in a key event's state: ctrl, plus alt (windows) or alt/command (x11, mac)
_SHORTCUT = 0x4 | (0x20000 if sys.platform == "win32" else 0x8)


def _words(text: str) -> list[str]:
    """the text from each later word start onwards, eg "Generic PLA @Creality" -> "PLA @Creality", "Creality" """
    return [text[m.start():] for m in re.finditer(r"(?<![^\W_])[^\W_]", text)][1:]


def matches(values: list[str], typed: str) -> list[int]:
    """rows matching the typed text: those starting with it, else those with a word starting with it"""
    typed = typed.casefold()
    if not typed:
        return []
    starts = [i for i, v in enumerate(values) if v.casefold().startswith(typed)]
    if starts:
        return starts
    return [i for i, v in enumerate(values) if any(w.casefold().startswith(typed) for w in _words(v))]


def find(values: list[str], typed: str, current: int) -> int | None:
    """the row to jump to for the typed text, or None to stay put"""
    if len(typed) > 1 and typed == typed[0] * len(typed):
        # the same letter again: cycle on from the current row
        rows = matches(values, typed[0])
        later = [i for i in rows if i > current]
        return (later or rows or [None])[0]
    rows = matches(values, typed)
    return rows[0] if rows else None


class Buffer:
    """the letters typed so far, forgotten after a pause"""

    def __init__(self, clock=time.monotonic):
        self.text = ""
        self.when = float("-inf")
        self.clock = clock

    def add(self, char: str) -> str:
        now = self.clock()
        if now - self.when > RESET_AFTER:
            self.text = ""
        self.when = now
        self.text += char
        return self.text


def typed_char(char: str, state: int) -> str | None:
    """the character a key press types, or None for shortcuts and non-printing keys"""
    if int(state) & _SHORTCUT or len(char) != 1 or not char.isprintable():
        return None
    return char


def attach(combo: ttk.Combobox) -> None:
    """give a readonly combobox type-ahead, both closed and with its list open"""
    buffer = Buffer()
    tk_ = combo.tk

    def values() -> list[str]:
        return [str(v) for v in tk_.splitlist(combo.cget("values"))]

    def closed_key(event):
        char = typed_char(event.char, event.state)
        if char is None or str(combo.cget("state")) != "readonly":
            return None
        row = find(values(), buffer.add(char), combo.current())
        if row is not None and row != combo.current():
            combo.current(row)
            combo.event_generate("<<ComboboxSelected>>")
        return "break"

    def open_key(listbox, char, state):
        # the open list is a tk-internal listbox, so it's driven through tcl
        char = typed_char(char, int(state))
        if char is None:
            return ""
        sel = tk_.splitlist(tk_.call(listbox, "curselection"))
        row = find(values(), buffer.add(char), int(sel[0]) if sel else -1)
        if row is not None:
            tk_.call(listbox, "selection", "clear", 0, "end")
            tk_.call(listbox, "selection", "set", row)
            tk_.call(listbox, "activate", row)
            tk_.call(listbox, "see", row)
        return "break"

    tag = f"TypeAhead{id(buffer)}"
    combo.bind_class(tag, "<KeyPress>", closed_key)
    combo.bindtags((tag,) + combo.bindtags())
    try:
        popdown = tk_.call("ttk::combobox::PopdownWindow", str(combo))
    except tk.TclError:
        return
    listbox = f"{popdown}.f.l"
    command = combo.register(open_key)
    tk_.call("bind", f"{tag}Open", "<KeyPress>", f'if {{[{command} %W %A %s] eq "break"}} break')
    tk_.call("bindtags", listbox, (f"{tag}Open",) + tk_.splitlist(tk_.call("bindtags", listbox)))
