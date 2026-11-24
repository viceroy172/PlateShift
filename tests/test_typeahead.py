from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plateshift.typeahead import Buffer, find, matches, typed_char  # noqa: E402

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.update_idletasks()
    _root.destroy()
    HAVE_TK = True
except Exception:  # no tk, or no display
    HAVE_TK = False

ROWS = ["Generic PLA @Creality", "Generic PETG", "Hyper PLA", "Creality K1 Max (0.4 nozzle)",
        "Polymaker PolyTerra PLA", "PLA Basic"]


class MatchTests(unittest.TestCase):
    def test_start_beats_word(self):
        self.assertEqual(matches(ROWS, "pl"), [5])          # only "PLA Basic" starts with it
        self.assertEqual(find(ROWS, "po", -1), 4)
        self.assertEqual(find(ROWS, "GENERIC P", -1), 0)    # case doesn't matter

    def test_word_start_when_nothing_starts_with_it(self):
        self.assertEqual(matches(ROWS, "petg"), [1])
        self.assertEqual(matches(ROWS, "nozzle"), [3])      # word after "("
        self.assertEqual(matches(ROWS, "creal"), [3])       # a start match hides the word matches
        self.assertEqual(matches(ROWS, "max"), [3])
        self.assertEqual(matches(ROWS, "ozz"), [])          # not from the middle of a word

    def test_no_match_stays_put(self):
        self.assertIsNone(find(ROWS, "zz", 2))
        self.assertIsNone(find(ROWS, "", 2))

    def test_repeated_letter_cycles(self):
        self.assertEqual(find(ROWS, "g", -1), 0)
        self.assertEqual(find(ROWS, "gg", 0), 1)
        self.assertEqual(find(ROWS, "ggg", 1), 0)           # wraps round
        self.assertEqual(find(ROWS, "pp", 4), 5)
        self.assertEqual(find(ROWS, "ppp", 5), 4)

    def test_pause_resets(self):
        now = [0.0]
        buffer = Buffer(clock=lambda: now[0])
        self.assertEqual(buffer.add("g"), "g")
        now[0] = 0.5
        self.assertEqual(buffer.add("e"), "ge")
        now[0] = 2.0
        self.assertEqual(buffer.add("h"), "h")

    def test_shortcuts_ignored(self):
        self.assertEqual(typed_char("a", 0), "a")
        self.assertEqual(typed_char("A", 0x1), "A")          # shift is fine
        self.assertIsNone(typed_char("a", 0x4))              # ctrl
        self.assertIsNone(typed_char("", 0))                 # arrows etc.
        self.assertIsNone(typed_char("\r", 0))


@unittest.skipUnless(HAVE_TK, "needs Tk and a display")
class WidgetTests(unittest.TestCase):
    def setUp(self):
        from tkinter import ttk

        from plateshift import typeahead
        self.root = tk.Tk()
        self.var = tk.StringVar(value=ROWS[0])
        self.combo = ttk.Combobox(self.root, textvariable=self.var, values=ROWS, state="readonly")
        self.combo.pack()
        typeahead.attach(self.combo)
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def key(self, widget, char):
        widget.event_generate("<KeyPress>", keysym=char, when="now")

    def test_closed_box(self):
        seen = []
        self.combo.bind("<<ComboboxSelected>>", lambda e: seen.append(self.var.get()))
        self.combo.focus_force()
        self.root.update()
        for c in "hy":
            self.key(self.combo, c)
        self.assertEqual(self.var.get(), "Hyper PLA")
        self.assertEqual(seen, ["Hyper PLA"])

    def test_open_list(self):
        popdown = self.combo.tk.call("ttk::combobox::PopdownWindow", str(self.combo))
        listbox = f"{popdown}.f.l"
        self.combo.tk.call(listbox, "insert", "end", *ROWS)   # tk fills it when the list opens
        self.combo.tk.call(listbox, "selection", "set", 0)
        cmd = self.combo.tk.call("bind", self.combo.tk.splitlist(self.combo.tk.call("bindtags", listbox))[0],
                                 "<KeyPress>")
        self.assertIn("break", str(cmd))
        # drive the binding the way tk would for the letter "p"
        script = str(cmd).replace("%W", listbox).replace("%A", "p").replace("%s", "0")
        self.combo.tk.eval(f"catch {{{script}}}")
        self.assertEqual(self.combo.tk.splitlist(self.combo.tk.call(listbox, "curselection")), (4,))
        self.assertEqual(self.var.get(), ROWS[0])           # nothing chosen until enter or a click


if __name__ == "__main__":
    unittest.main()
