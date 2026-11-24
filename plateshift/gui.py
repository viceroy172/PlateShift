# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tk front end."""

from __future__ import annotations

import base64
import json
import math
import os
import queue
import sys
import shutil
import tempfile
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

from . import REPO_URL, __version__, bugreport, catalogue, dnd, integration, sendprint, typeahead
from .icon_data import ICONS
from .preview import plate_pictures
from .price import cost_lines, filament_total, money, parse_price, regional_currency
from .convert import (ConversionError, _family, analyse, convert, default_output, lead_colour, plate_names,
                      plate_output, process_choices, suggest, unique_output)
from .printers import TARGETS
from .profiles import ProfileLibrary, locate, user_preset_dirs
from .slicers import CAN_TEST, Slicer, find_slicers, open_in, test_slicer
from .slicers import show_in_folder as reveal
from .testslice import run as test_slice

OK_COLOUR = "#1a7f37"
FAIL_COLOUR = "#b42318"
WARN_COLOUR = "#9a6700"
MUTED = "#555555"
# the website's colours, used sparingly
BRAND_BLUE = "#2f81f7"
BRAND_DARK = "#0d1118"
BRAND_TEXT = "#e8edf5"
BRAND_MUTED = "#93a0b4"
DONT_OPEN = "Don't open"

# each system's own fonts; mac tk draws the same point size noticeably smaller
if sys.platform == "win32":
    _UI, _MONO, _BUMP = "Segoe UI", "Consolas", 0
elif sys.platform == "darwin":
    _UI, _MONO, _BUMP = "Helvetica Neue", "Menlo", 3
else:
    _UI, _MONO, _BUMP = "DejaVu Sans", "DejaVu Sans Mono", 0


def ui_font(size: int, *style: str) -> tuple:
    return (_UI, size + _BUMP, *style)


def mono_font(size: int) -> tuple:
    return (_MONO, size + _BUMP)



def _settings_file() -> Path:
    if os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "PlateShift" / "settings.json"
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "plateshift" / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(_settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    try:
        path = _settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError:
        pass


def _explain(exc: Exception) -> str:
    if isinstance(exc, (ConversionError, OSError)):
        return str(exc)
    return f"unexpected problem ({type(exc).__name__}: {exc}). Please report this with the file."


def _model_name(target) -> str:
    """the printer's name without its brand: Flashforge AD5X -> AD5X"""
    prefix = target.vendor + " "
    return target.label[len(prefix):] if target.label.startswith(prefix) else target.label


AUTO_PROCESS = "Automatic (closest to the project's layer height)"


def _material(profile: dict) -> str:
    """the material family of a filament preset"""
    kind = profile.get("filament_type")
    kind = kind[0] if isinstance(kind, list) and kind else kind
    return _family(str(kind or "").upper())  # silk pla counts as pla, as when converting


def _display(profile: dict) -> str:
    return f"{profile['name']}  (yours, {profile['_user']})" if profile.get("_user") else profile["name"]


def _preset_cost(preset: dict) -> float | None:
    """a filament preset's own price per kg (orca's filament_cost), if it has one"""
    value = preset.get("filament_cost")
    if isinstance(value, list):
        value = value[0] if value else None
    try:
        cost = float(value)
    except (TypeError, ValueError):
        return None
    return cost if cost > 0 else None


def tooltip(widget) -> None:
    """show widget.tooltip while the mouse is over it"""
    if getattr(widget, "_tip_bound", False):
        return
    widget._tip_bound = True
    tip: list = []

    def show(_event):
        text = getattr(widget, "tooltip", "")
        if not text or tip:
            return
        win = tk.Toplevel(widget)
        win.wm_overrideredirect(True)
        win.wm_geometry(f"+{widget.winfo_rootx() + 16}+{widget.winfo_rooty() + widget.winfo_height() + 2}")
        tk.Label(win, text=text, bg="#ffffe1", relief="solid", bd=1, padx=6, pady=2, font=ui_font(9)).pack()
        tip.append(win)

    def hide(_event):
        while tip:
            tip.pop().destroy()
    widget.bind("<Enter>", show, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<Button-1>", hide, add="+")


def _blue_progress(style: ttk.Style) -> str:
    """a progress bar style in the website's blue. native windows and mac bars
    ignore colours, so borrow clam's bar element, which takes them"""
    name = "Blue.Horizontal.TProgressbar"
    try:
        if "blue.pbar" not in style.element_names():
            style.element_create("blue.pbar", "from", "clam")
        style.layout(name, [("Horizontal.Progressbar.trough", {"sticky": "nswe", "children": [
            ("blue.pbar", {"side": "left", "sticky": "ns"})]})])
        style.configure(name, background=BRAND_BLUE, troughcolor="#e6e9ee", bordercolor="#c9ced6",
                        lightcolor=BRAND_BLUE, darkcolor=BRAND_BLUE)
        return name
    except tk.TclError:
        return "Horizontal.TProgressbar"


class App:
    def __init__(self, root: tk.Tk, initial_files: list[Path]):
        self.root = root
        self.libraries: dict[str, ProfileLibrary] = {}
        self.last_output: Path | None = None
        self.busy = False
        self._quiet = False
        self._calls: queue.Queue = queue.Queue()
        self.settings = load_settings()
        # price per kg of each filament preset, and the currency they're in
        self.prices: dict[str, float] = {}
        for name, value in (self.settings.get("filament_prices") or {}).items():
            if isinstance(value, (int, float)) and value >= 0:
                self.prices[name] = float(value)
        self.settings.pop("open_in", None)  # old single choice, now per printer
        self.slicers: dict[str, Slicer] = {s.label: s for s in find_slicers()}
        self.tester: Slicer | None = None
        root.title("PlateShift")
        root.minsize(680, 460)
        # tk drops images nobody holds, so keep them
        self.icons = {size: tk.PhotoImage(data=data) for size, data in ICONS.items()}
        try:
            root.iconphoto(True, *(self.icons[s] for s in sorted(self.icons, reverse=True)))
        except tk.TclError:
            pass
        # tk's stock linux look is dated; clam is built in and much cleaner.
        # windows and macos keep their native themes
        style = ttk.Style(root)
        self.bg = None
        if sys.platform.startswith("linux") and "clam" in style.theme_names():
            style.theme_use("clam")
            self.bg = style.lookup("TFrame", "background")
            root.configure(bg=self.bg)
        self.progress_style = _blue_progress(style)
        self.header(root)

        top = ttk.Frame(root, padding=(12, 12, 12, 4))
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Target printer").grid(row=0, column=0, sticky="w")
        # the built-in printers, then the ones you added
        self.targets = list(TARGETS) + self.my_printers()
        labels = [t.label for t in self.targets]
        saved_printer = self.settings.get("printer")
        # the chosen printer's full label; the brand and model boxes show it in two parts
        self.printer = tk.StringVar(value=saved_printer if saved_printer in labels else labels[0])
        pickers = ttk.Frame(top)
        pickers.grid(row=0, column=1, sticky="w", padx=8)
        self.brand = tk.StringVar()
        self.brand_picker = ttk.Combobox(pickers, textvariable=self.brand, state="readonly", width=12)
        self.brand_picker.pack(side="left")
        typeahead.attach(self.brand_picker)
        self.model = tk.StringVar()
        self.picker = ttk.Combobox(pickers, textvariable=self.model, state="readonly", width=30)
        self.picker.pack(side="left", padx=(6, 0))
        ttk.Button(pickers, text="Add printer…", command=self.printer_wizard).pack(side="left", padx=(6, 0))
        typeahead.attach(self.picker)
        self._syncing = False
        self.show_printer()
        self.brand.trace_add("write", lambda *_: self.brand_changed())
        self.model.trace_add("write", lambda *_: self.model_changed())
        self.button = ttk.Button(top, text="Choose 3MF files…", command=self.choose)
        self.button.grid(row=0, column=2, sticky="e")

        ttk.Label(top, text="Then open in").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.open_choice = tk.StringVar(value=DONT_OPEN)
        self.opener = ttk.Combobox(top, textvariable=self.open_choice, state="readonly", width=34)
        self.opener.grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))
        typeahead.attach(self.opener)
        self._quiet = True
        self.update_slicer_choices()
        self._quiet = False
        self.printer.trace_add("write", lambda *_: self.printer_changed())

        # print profile: automatic, or one of the slicer's or the user's own
        ttk.Label(top, text="Print profile").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.process_choice = tk.StringVar(value=AUTO_PROCESS)
        self.process_names: dict[str, str] = {}   # shown text -> preset name
        self.process_picker = ttk.Combobox(top, textvariable=self.process_choice, state="readonly",
                                           width=48, values=[AUTO_PROCESS])
        self.process_picker.grid(row=2, column=1, sticky="w", padx=8, pady=(8, 0))
        typeahead.attach(self.process_picker)
        self.keep_design = tk.BooleanVar(value=bool(self.settings.get("keep_design", False)))
        self.keeper = ttk.Checkbutton(top, variable=self.keep_design,
                                      text="Keep the designer's layer height, walls, infill, supports…")
        self.keeper.grid(row=3, column=1, sticky="w", padx=8, pady=(4, 0))

        self.check = tk.BooleanVar(value=bool(self.settings.get("test_slice", True)))
        self.checker = ttk.Checkbutton(top, variable=self.check)
        self.checker.grid(row=4, column=1, sticky="w", padx=8, pady=(4, 0))
        self.update_tester()
        self.update_processes()
        ttk.Button(top, text="About", command=self.about).grid(row=1, column=2, sticky="e", pady=(8, 0))
        ttk.Button(top, text="Options…", command=self.options).grid(row=2, column=2, sticky="e", pady=(8, 0))
        self.watcher = None
        if self.settings.get("watch_downloads"):
            root.after(1000, self.start_watching)
        if sys.platform == "darwin":
            root.createcommand("tkAboutDialog", self.about)  # the app menu's "About PlateShift"

        self.open_choice.trace_add("write", lambda *_: self.remember(slicer=True))
        self.open_choice.trace_add("write", lambda *_: hasattr(self, "checker") and self.update_tester())
        self.process_choice.trace_add("write", lambda *_: self.remember(process=True))
        self.keep_design.trace_add("write", lambda *_: self.remember())
        for var in (self.printer, self.check):
            var.trace_add("write", lambda *_: self.remember())

        self.hint = ttk.Label(root, padding=(12, 2), foreground=MUTED,
                              text="Choose files, or drop .3mf files on this window. Converted copies are "
                                   "saved next to the originals; open them with File → Open Project.")
        self.hint.pack(fill="x")
        # wrap rather than cut off when the window is narrow
        self.hint.bind("<Configure>", lambda e: self.hint.configure(wraplength=max(200, e.width - 24)))

        self.plan_frame = ttk.Frame(root, padding=(12, 6, 12, 0))

        status = ttk.Frame(root, padding=(12, 4, 12, 12))
        status.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(status, mode="determinate", length=160, style=self.progress_style)
        self.progress.pack(side="left")
        self.status = tk.Label(status, text="Ready. Choose files to convert.", anchor="w", fg=MUTED,
                               **({"bg": self.bg} if self.bg else {}))
        self.status.pack(side="left", fill="x", expand=True, padx=10)
        self.show = ttk.Button(status, text="Show in folder", command=self.show_in_folder)
        self.open_button = ttk.Button(status, text="Open", command=self.open_last)

        self.log_frame = ttk.Frame(root, padding=(12, 12, 12, 4))
        self.log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(self.log_frame, wrap="word", height=12, state="disabled", font=mono_font(9))
        self.log.tag_configure("ok", foreground=OK_COLOUR)
        self.log.tag_configure("fail", foreground=FAIL_COLOUR)
        self.log.tag_configure("done", foreground=OK_COLOUR, font=ui_font(10, "bold"))
        scroll = ttk.Scrollbar(self.log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        if not dnd.enable(root, self.handle_files):
            self.hint.configure(text="Choose files to convert. Converted copies are saved next to the "
                                     "originals; open them with File → Open Project.")
        if initial_files:
            root.after(100, lambda: self.handle_files(initial_files))
        self.drain()

    # --- talking to the window from worker threads ----------------------
    # tk must only be touched from its own thread; workers queue calls instead

    def later(self, fn):
        self._calls.put(fn)

    def drain(self):
        while True:
            try:
                fn = self._calls.get_nowait()
            except queue.Empty:
                break
            fn()
        self.root.after(40, self.drain)

    # --- settings and small helpers -------------------------------------

    def target(self):
        return next((t for t in self.targets if t.label == self.printer.get()), self.targets[0])

    def brands(self) -> list[str]:
        return list(dict.fromkeys(t.vendor for t in self.targets))

    def my_printers(self) -> list:
        found = [catalogue.from_dict(d) for d in self.settings.get("my_printers", []) if isinstance(d, dict)]
        return [t for t in found if t is not None]

    def library(self, target):
        """(target with the preset names this pc has, its profile library)"""
        found = self.libraries.get(target.key)
        if found is None:
            located, root = locate(target)
            found = self.libraries[target.key] = (located, ProfileLibrary(root, target.vendor, user_preset_dirs()))
        return found

    def update_slicer_choices(self):
        # each printer only offers the slicers that have its profiles
        target = self.target()
        choices = [DONT_OPEN] + [s for s in target.slicers if s in self.slicers]
        saved = self.settings.get("open_in_by_printer", {}).get(target.key)
        self.opener.configure(values=choices)
        quiet, self._quiet = self._quiet, True
        self.open_choice.set(saved if saved in choices else DONT_OPEN)
        self._quiet = quiet
        self.update_open_button()

    def remember(self, slicer: bool = False, process: bool = False):
        self.update_keeper()
        if self._quiet:
            return
        if slicer:
            by_printer = self.settings.setdefault("open_in_by_printer", {})
            by_printer[self.target().key] = self.open_choice.get()
        if process:
            by_printer = self.settings.setdefault("process_by_printer", {})
            by_printer[self.target().key] = self.chosen_process() or ""
        target = self.target()
        self.settings.setdefault("printer_by_brand", {})[target.vendor] = target.label
        self.settings.update(printer=self.printer.get(), test_slice=self.check.get(),
                             keep_design=self.keep_design.get())
        save_settings(self.settings)
        self.update_open_button()

    def chosen_slicer(self) -> Slicer | None:
        return self.slicers.get(self.open_choice.get())

    def update_open_button(self):
        if not hasattr(self, "open_button"):
            return
        slicer = self.chosen_slicer()
        if slicer and self.last_output and self.last_output.exists():
            self.open_button.configure(text=f"Open in {slicer.label}")
            self.open_button.pack(side="right", padx=(6, 0))
        else:
            self.open_button.pack_forget()

    def open_last(self) -> bool:
        slicer = self.chosen_slicer()
        if not (slicer and self.last_output and self.last_output.exists()):
            return False
        try:
            open_in(slicer, self.last_output)
        except OSError as exc:
            self.write(f"✗ Could not start {slicer.label}: {exc}\n", "fail")
            return False
        return True

    def show_in_folder(self):
        if self.last_output and self.last_output.exists() and not reveal(self.last_output):
            self.write(f"Saved in {self.last_output.parent}\n")

    # called from the worker thread
    def write(self, text: str, tag: str | None = None):
        def append():
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n", tag or ())
            self.log.see("end")
            self.log.configure(state="disabled")
        self.later(append)

    def set_status(self, text: str, colour: str = MUTED):
        self.later(lambda: self.status.configure(text=text, fg=colour, font=ui_font(9)))

    def set_busy(self, busy: bool):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.button.configure(state=state)
        self.picker.configure(state="disabled" if busy else "readonly")
        self.brand_picker.configure(state="disabled" if busy else "readonly")
        self.opener.configure(state="disabled" if busy else "readonly")
        self.process_picker.configure(state="disabled" if busy else "readonly")
        self.update_keeper()
        if self.tester:
            self.checker.configure(state=state)
        self.root.configure(cursor="watch" if busy else "")

    # --- choosing files ---------------------------------------------------

    def choose(self):
        names = filedialog.askopenfilenames(title="Choose 3MF projects",
                                            filetypes=[("3MF projects", "*.3mf")])
        if names:
            self.handle_files([Path(n) for n in names])

    def handle_files(self, files):
        if self.busy:
            return
        files = [Path(f) for f in files if str(f).lower().endswith(".3mf")]
        if not files:
            self.status.configure(text="Only .3mf project files can be converted.", fg=FAIL_COLOUR)
            return
        self.close_plan()
        if len(files) == 1:
            self.prepare(files[0])
        else:
            self.write(f"{len(files)} files: colours and filaments are matched automatically.\n")
            self.run(files)

    # --- single file: show the colour and filament choices -----------------

    def prepare(self, source: Path):
        target = self.target()
        self.set_busy(True)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.set_status(f"Reading {source.name}…")

        def work():
            result = (None, None, None, [], [])
            try:
                located, lib = self.library(target)
                result = (source, located, analyse(source, located, lib), plate_pictures(source),
                          plate_names(source))
            except Exception as exc:  # anything at all: the window must never stay busy
                self.write(f"✗ {source.name}: not converted", "fail")
                self.write(f"  {_explain(exc)}\n")
            finally:
                self.later(lambda: self.after_prepare(*result))

        threading.Thread(target=work, daemon=True).start()

    def after_prepare(self, source, target, plan, pictures=(), names=()):
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.set_busy(False)
        if plan is None:
            self.status.configure(text="✗ Nothing was converted (see below)", fg=FAIL_COLOUR,
                                  font=ui_font(10, "bold"))
            return
        self.show_plan(source, target, plan, pictures, names)

    def show_plan(self, source: Path, target, plan, pictures=(), names=()):
        self.plan = plan
        self.plan_source = source
        self.plan_target = target
        for child in self.plan_frame.winfo_children():
            child.destroy()
        self.plan_frame.pack(fill="x", before=self.log_frame)

        word = target.slot_word
        heads = [str(i) for i in range(1, target.toolheads + 1)]
        box = ttk.LabelFrame(self.plan_frame, text=f" {source.name} ", padding=10)
        box.pack(fill="x")
        self.plate_labels = list(names) or ["Plate 1"]
        self.plate_vars = [tk.BooleanVar(value=True) for _ in self.plate_labels]
        self.split_plates = tk.BooleanVar(value=False)
        side = ttk.Frame(box)
        side.pack(side="left", anchor="n", padx=(0, 12))
        self.show_thumbnail(side, source, pictures)
        self.show_plate_choice(side)

        left = ttk.Frame(box)
        left.pack(side="left", fill="y", anchor="n")
        ttk.Label(left, text="Project colours", font=ui_font(9, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        total = sum(f.usage for f in plan.filaments) or 1
        self.merge_vars = []
        for row, (f, t) in enumerate(zip(plan.filaments, plan.merge), start=1):
            tk.Label(left, width=2, bg=f.colour, relief="solid", bd=1).grid(row=row, column=0, padx=(0, 6), pady=1)
            name = (f.name or "(unnamed)").split("@")[0].strip()
            ttk.Label(left, text=f"{f.index}. {name} ({f.type})").grid(row=row, column=1, sticky="w")
            use = f"{100 * f.usage / total:.0f}%" if f.used else "unused"
            ttk.Label(left, text=use, foreground=MUTED, width=7, anchor="e").grid(row=row, column=2)
            var = tk.StringVar(value=str(t))
            combo = ttk.Combobox(left, textvariable=var, values=heads, width=4, state="readonly")
            combo.grid(row=row, column=3, padx=(8, 0))
            typeahead.attach(combo)
            if target.toolheads == 1:
                combo.configure(state="disabled")
            var.trace_add("write", lambda *_: self.refresh_heads())
            self.merge_vars.append(var)
        ttk.Label(left, text=f"→ {word.lower()}", foreground=MUTED).grid(row=0, column=3, sticky="w", padx=(8, 0))

        ttk.Separator(box, orient="vertical").pack(side="left", fill="y", padx=14)

        self.heads_frame = ttk.Frame(box)
        self.heads_frame.pack(side="left", fill="both", expand=True, anchor="n")
        # the preset boxes take whatever room there is, so a narrow window (or the
        # mac's bigger fonts) doesn't push them off the edge
        self.heads_frame.columnconfigure(2, weight=1)
        options = sorted(plan.candidates, key=lambda p: (not p.get("_user"), p["name"].lower()))
        self.preset_names = {_display(p): p["name"] for p in options}
        self.head_vars: dict[int, tk.StringVar] = {}
        self.cogs: dict[int, tk.Label] = {}
        self.preset_costs = {p["name"]: _preset_cost(p) for p in plan.candidates}
        self.head_user_set: set[int] = set()
        self.head_colours: dict[int, str] = {}
        # filaments the user picked for each toolhead of this printer before
        self.usual = dict(self.settings.get("filaments_by_printer", {}).get(target.key, {}))
        self.head_usual: set[int] = set()
        self.preset_types = {p["name"]: _material(p) for p in plan.candidates}
        self.refresh_heads()

        buttons = ttk.Frame(self.plan_frame, padding=(0, 6, 0, 0))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Convert", command=self.convert_plan).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.close_plan).pack(side="right", padx=6)
        note = target.note or ("Colours sharing a toolhead print in that toolhead's filament."
                               if target.toolheads > 1 else "")
        ttk.Label(buttons, text=note, foreground=MUTED, wraplength=520).pack(side="left")
        check = (f"Check the {target.slot_word.lower()}s and their filaments, then Convert."
                 if target.toolheads > 1 else "Check the filament, then Convert.")
        self.status.configure(text=check, fg=MUTED, font=ui_font(9))

    def show_thumbnail(self, box, source: Path, pictures):
        """the plate picture the project carries, so you can see it's the right file"""
        images = []
        for data in pictures:
            try:
                images.append(tk.PhotoImage(data=base64.b64encode(data).decode("ascii")))
            except tk.TclError:
                continue
        self.plate_images = images  # tk drops images nobody holds
        if not images:
            return
        first = images[0]
        small = first.subsample(max(1, math.ceil(max(first.width(), first.height()) / 110)))
        self.plate_images = images + [small]
        frame = ttk.Frame(box)
        frame.pack(anchor="n")
        pic = tk.Label(frame, image=small, cursor="hand2", bd=1, relief="solid", bg="#ffffff")
        pic.pack()
        pic.bind("<Button-1>", lambda e: self.show_plates(source, images))
        more = f"Plate 1 of {len(images)}\nclick to enlarge" if len(images) > 1 else "click to enlarge"
        ttk.Label(frame, text=more, foreground=MUTED, font=ui_font(8), justify="center").pack(pady=(3, 0))

    def show_plates(self, source: Path, images):
        # only ever one: clicking again brings it forward, another file's replaces it
        old = getattr(self, "plates_window", None)
        if old is not None and old.winfo_exists():
            if getattr(self, "plates_source", None) == source:
                old.deiconify()
                old.lift()
                old.focus_set()
                return
            old.destroy()
        self.plates_source = source
        win = tk.Toplevel(self.root)
        win.title(source.name)
        win.transient(self.root)
        shown = [0]
        pic = tk.Label(win, image=images[0], bg="#ffffff")
        pic.pack(padx=12, pady=(12, 6))
        bar = ttk.Frame(win, padding=(12, 0, 12, 12))
        bar.pack(fill="x")
        caption = ttk.Label(bar, foreground=MUTED)

        labels = getattr(self, "plate_labels", [])
        ticks = getattr(self, "plate_vars", [])
        # one tick per picture only when each plate has its picture
        tick = ttk.Checkbutton(bar, text="Convert this plate") if len(ticks) == len(images) > 1 else None

        def go(step: int):
            shown[0] = (shown[0] + step) % len(images)
            pic.configure(image=images[shown[0]])
            name = labels[shown[0]].partition(": ")[2] if shown[0] < len(labels) else ""
            caption.configure(text=f"Plate {shown[0] + 1} of {len(images)}" + (f": {name}" if name else ""))
            if tick is not None:
                tick.configure(variable=ticks[shown[0]])
        if len(images) > 1:
            ttk.Button(bar, text="<", width=3, command=lambda: go(-1)).pack(side="left")
            caption.pack(side="left", padx=10)
            ttk.Button(bar, text=">", width=3, command=lambda: go(1)).pack(side="left")
            if tick is not None:
                tick.pack(side="left", padx=(14, 0))
            go(0)
            win.bind("<Left>", lambda e: go(-1))
            win.bind("<Right>", lambda e: go(1))
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())
        self.plates_window = win

    def show_plate_choice(self, side):
        """a tick per plate, and whether each plate gets its own file"""
        if len(self.plate_vars) < 2:
            return
        frame = ttk.Frame(side)
        frame.pack(anchor="w", pady=(8, 0))
        ttk.Label(frame, text="Plates", font=ui_font(9, "bold")).grid(row=0, column=0, columnspan=4, sticky="w")
        for i, var in enumerate(self.plate_vars):
            row, col = divmod(i, 4)
            ttk.Checkbutton(frame, text=str(i + 1), variable=var, width=3).grid(row=row + 1, column=col, sticky="w")
            var.trace_add("write", lambda *_: self.plates_changed())
        rows = (len(self.plate_vars) + 3) // 4
        ttk.Checkbutton(frame, text="One file per plate", variable=self.split_plates).grid(
            row=rows + 1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.plates_note = ttk.Label(frame, foreground=MUTED, font=ui_font(8))
        self.plates_note.grid(row=rows + 2, column=0, columnspan=4, sticky="w")
        self.plates_changed()

    def chosen_plates(self) -> list[int]:
        return [i + 1 for i, v in enumerate(getattr(self, "plate_vars", [])) if v.get()]

    def plates_changed(self):
        chosen, total = len(self.chosen_plates()), len(self.plate_vars)
        text = "all plates" if chosen == total else f"{chosen} of {total} plates" if chosen else "no plates ticked"
        self.plates_note.configure(text=text, foreground=WARN_COLOUR if not chosen else MUTED)

    def current_merge(self) -> list[int]:
        return [int(v.get()) for v in self.merge_vars]

    def refresh_heads(self):
        plan, merge = self.plan, self.current_merge()
        word = self.plan_target.slot_word
        for child in self.heads_frame.winfo_children():
            child.destroy()
        ttk.Label(self.heads_frame, text=f"{word} presets", font=ui_font(9, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        ttk.Label(self.heads_frame, text="click a colour to change it", foreground=MUTED).grid(
            row=0, column=2, sticky="e")
        by_name = {v: k for k, v in self.preset_names.items()}
        row = 0
        for t in sorted(set(merge)):
            row += 1
            colour = self.head_colours.get(t, lead_colour(plan, merge, t))
            swatch = tk.Label(self.heads_frame, width=2, bg=colour, cursor="hand2",
                              relief="raised" if t in self.head_colours else "solid", bd=1)
            swatch.grid(row=row, column=0, padx=(0, 6), pady=1)
            swatch.bind("<Button-1>", lambda e, t=t: self.colour_menu(t, e.widget))
            ttk.Label(self.heads_frame, text=f"{word} {t}").grid(row=row, column=1, sticky="w")
            var = self.head_vars.get(t)
            if var is None:
                var = self.head_vars[t] = tk.StringVar()
                var.trace_add("write", lambda *_, t=t: self._quiet or self.head_user_set.add(t))
                var.trace_add("write", lambda *_, t=t: self.update_cog(t))
            note = ""
            if t not in self.head_user_set:
                name, note = suggest(plan, merge, t)
                # the filament last picked for this toolhead, when it's the same material
                usual = self.usual.get(str(t))
                if (usual and usual != name and usual in by_name
                        and self.preset_types.get(usual) == self.preset_types.get(name)):
                    name, note = usual, ""
                    self.head_usual.add(t)
                else:
                    self.head_usual.discard(t)
                self._quiet = True
                var.set(by_name.get(name, ""))
                self._quiet = False
            presets = ttk.Combobox(self.heads_frame, textvariable=var, values=list(self.preset_names),
                                   width=30, state="readonly")
            presets.grid(row=row, column=2, sticky="we", padx=(8, 0), pady=1)
            typeahead.attach(presets)
            cog = tk.Label(self.heads_frame, text="⚙", cursor="hand2", font=ui_font(12),
                           **({"bg": self.bg} if self.bg else {}))
            cog.grid(row=row, column=3, padx=(4, 0))
            cog.bind("<Button-1>", lambda e, t=t: self.price_dialog(t))
            self.cogs[t] = cog
            self.update_cog(t)
            if note:  # e.g. no TPU preset, so PLA was used
                row += 1
                ttk.Label(self.heads_frame, text=f"⚠ {note}", foreground=WARN_COLOUR).grid(
                    row=row, column=2, sticky="w", padx=(8, 0))

    # --- filament prices ---------------------------------------------------

    def head_preset(self, t: int) -> str | None:
        return self.preset_names.get(self.head_vars[t].get())

    def price_of(self, preset: str | None) -> tuple[float | None, bool]:
        """(price per kg, whether it's yours rather than the preset's)"""
        if preset in self.prices:
            return self.prices[preset], True
        return self.preset_costs.get(preset or ""), False

    def update_cog(self, t: int):
        cog = self.cogs.get(t)
        if cog is None or not cog.winfo_exists():
            return
        price, yours = self.price_of(self.head_preset(t))
        cog.configure(fg=BRAND_BLUE if yours else MUTED)
        where = "your price" if yours else "from the filament preset"
        cog.tooltip = (f"{money(price, self.currency_symbol())}/kg, {where}. Click to change it."
                       if price else "No price. Click to set one.")
        tooltip(cog)

    def currency_symbol(self) -> str:
        return self.settings.get("currency") or regional_currency()

    def price_dialog(self, t: int):
        """change the price of the filament on toolhead t, for every project"""
        preset = self.head_preset(t)
        if not preset:
            return
        win = tk.Toplevel(self.root)
        win.title("Filament price")
        win.resizable(False, False)
        win.transient(self.root)
        self.price_window = win
        box = ttk.Frame(win, padding=16)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=preset, font=ui_font(10, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        own = self.preset_costs.get(preset)
        currency = tk.StringVar(value=self.currency_symbol())
        ttk.Label(box, foreground=MUTED, text=(f"The preset's price: {money(own, currency.get())} per kg" if own
                                               else "The preset has no price.")).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(2, 10))
        ttk.Label(box, text="Price per kg").grid(row=2, column=0, sticky="w")
        current, yours = self.price_of(preset)
        value = tk.StringVar(value=f"{current:g}" if current else "")
        entry = ttk.Entry(box, textvariable=value, width=10, justify="right")
        entry.grid(row=2, column=1, sticky="w", padx=(8, 0))
        entry.focus_set()
        entry.select_range(0, "end")
        ttk.Label(box, text="Currency").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(box, textvariable=currency, width=6, justify="center").grid(
            row=3, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        problem = ttk.Label(box, foreground=FAIL_COLOUR)
        problem.grid(row=4, column=0, columnspan=3, sticky="w")
        ttk.Label(box, foreground=MUTED, font=ui_font(8), wraplength=300, justify="left",
                  text="PlateShift only reads the price from your slicer's filament profile. A price "
                       "changed here is used by PlateShift and saved in converted files, but the "
                       "profile itself isn't changed.").grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))

        def done(price: float | None, reset: bool = False):
            if reset:
                self.prices.pop(preset, None)
            elif price is not None:
                self.prices[preset] = price
            symbol = currency.get().strip()
            if symbol:
                self.settings["currency"] = symbol
            self.settings["filament_prices"] = dict(sorted(self.prices.items()))
            save_settings(self.settings)
            for other in self.cogs:
                self.update_cog(other)
            win.destroy()

        def ok(_event=None):
            text = value.get().strip()
            if not text:
                return done(None, reset=True)
            price = parse_price(text)
            if price is None:
                problem.configure(text="That isn't a price; type a number like 350.")
                return
            done(price)
        buttons = ttk.Frame(box)
        buttons.grid(row=6, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="OK", command=ok).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=6)
        if yours:
            ttk.Button(buttons, text="Use the preset's price", command=lambda: done(None, reset=True)).pack(
                side="right")
        win.bind("<Return>", ok)
        win.bind("<Escape>", lambda e: win.destroy())

    # --- toolhead colours --------------------------------------------------

    def _swatch_image(self, colour: str) -> tk.PhotoImage:
        img = tk.PhotoImage(width=18, height=12)
        img.put("#666666", to=(0, 0, 18, 12))
        img.put(colour, to=(1, 1, 17, 11))
        self._menu_images.append(img)  # tk drops images nobody holds
        return img

    def colour_menu(self, t: int, widget):
        original = lead_colour(self.plan, self.current_merge(), t)
        current = self.head_colours.get(t, original)
        self._menu_images = []
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"  Designer's colour  {original}", image=self._swatch_image(original),
                         compound="left", command=lambda: self.set_head_colour(t, None))
        recent = [c for c in self.settings.get("recent_colours", []) if c != original]
        if recent:
            menu.add_separator()
            for c in recent:
                menu.add_command(label=f"  {c}", image=self._swatch_image(c), compound="left",
                                 command=lambda c=c: self.set_head_colour(t, c))
        menu.add_separator()
        menu.add_command(label="  Choose…", command=lambda: self.pick_colour(t, current))
        menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())

    def pick_colour(self, t: int, current: str):
        word = self.plan_target.slot_word.lower()
        _, chosen = colorchooser.askcolor(color=current, parent=self.root, title=f"Colour for {word} {t}")
        if chosen:
            self.set_head_colour(t, chosen.upper())

    def set_head_colour(self, t: int, colour: str | None):
        original = lead_colour(self.plan, self.current_merge(), t)
        if colour is None or colour == original:
            self.head_colours.pop(t, None)
        else:
            self.head_colours[t] = colour
            recent = [colour] + [c for c in self.settings.get("recent_colours", []) if c != colour]
            self.settings["recent_colours"] = recent[:10]
            save_settings(self.settings)
        self.refresh_heads()

    def header(self, root):
        """a slim dark band with the logo, like the website's"""
        band = tk.Frame(root, bg=BRAND_DARK, padx=12, pady=8)
        band.pack(fill="x")
        tk.Label(band, image=self.icons[32], bg=BRAND_DARK).pack(side="left")
        tk.Label(band, text="PlateShift", font=ui_font(13, "bold"), fg=BRAND_TEXT, bg=BRAND_DARK).pack(
            side="left", padx=(8, 0))
        tk.Label(band, text="Bambu projects for your own printer · everything stays on this computer",
                 fg=BRAND_MUTED, bg=BRAND_DARK, font=ui_font(9)).pack(side="left", padx=(12, 0))
        tk.Frame(root, bg=BRAND_BLUE, height=2).pack(fill="x")  # a thin blue rule under it

    def about(self):
        if getattr(self, "about_window", None) is not None and self.about_window.winfo_exists():
            self.about_window.lift()
            return
        win = self.about_window = tk.Toplevel(self.root)
        win.title("About PlateShift")
        win.resizable(False, False)
        win.transient(self.root)
        band = tk.Frame(win, bg=BRAND_DARK, padx=24, pady=16)
        band.pack(fill="x")
        tk.Label(band, image=self.icons[48], bg=BRAND_DARK).pack(side="left")
        names = tk.Frame(band, bg=BRAND_DARK)
        names.pack(side="left", padx=(12, 0))
        tk.Label(names, text="PlateShift", font=ui_font(16, "bold"), fg=BRAND_TEXT, bg=BRAND_DARK).pack(anchor="w")
        tk.Label(names, text=f"Version {__version__}", fg=BRAND_MUTED, bg=BRAND_DARK).pack(anchor="w")
        box = ttk.Frame(win, padding=(24, 16))
        box.pack(fill="both", expand=True)
        ttk.Label(box, wraplength=380, justify="left",
                  text="Converts Bambu Studio and MakerWorld 3MF projects so they open ready to print on "
                       "another printer. Everything happens on this computer; nothing is sent anywhere.").pack(
            anchor="w")
        link = tk.Label(box, text=REPO_URL, fg="#0b5cad", cursor="hand2", font=ui_font(10, "underline"),
                        **({"bg": self.bg} if self.bg else {}))
        link.pack(anchor="w", pady=(10, 0))
        link.bind("<Button-1>", lambda e: webbrowser.open(REPO_URL))
        ttk.Label(box, wraplength=380, justify="left", foreground=MUTED,
                  text="Source code, help and new versions are on GitHub. Free software under the GNU AGPL v3 or later. "
                       "Not affiliated with Bambu Lab, Flashforge, Creality or MakerWorld.").pack(
            anchor="w", pady=(10, 0))
        buttons = ttk.Frame(box)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Close", command=win.destroy).pack(side="right")
        ttk.Button(buttons, text="Licences…", command=self.licences).pack(side="right", padx=(0, 6))
        ttk.Button(buttons, text="Report a problem…", command=self.report_problem).pack(side="left")
        win.bind("<Escape>", lambda e: win.destroy())

    # --- options: open with, the downloads folder ---------------------------

    def options(self):
        if getattr(self, "options_window", None) is not None and self.options_window.winfo_exists():
            self.options_window.lift()
            return
        win = self.options_window = tk.Toplevel(self.root)
        win.title("PlateShift options")
        win.resizable(False, False)
        win.transient(self.root)
        box = ttk.Frame(win, padding=16)
        box.pack(fill="both", expand=True)

        ttk.Label(box, text="Open 3MF files with PlateShift", font=ui_font(10, "bold")).pack(anchor="w")
        state = ttk.Label(box, foreground=MUTED, wraplength=440, justify="left")
        state.pack(anchor="w", pady=(2, 6))
        row = ttk.Frame(box)
        row.pack(anchor="w")

        def refresh():
            if sys.platform == "darwin":
                state.configure(text="On a Mac, PlateShift is already in Finder's Open With menu for 3MF files.")
                return
            on = integration.has_open_with()
            state.configure(text=("PlateShift is in the Open with list for 3MF files. Your default app for them "
                                  "hasn't changed." if on else
                                  "Adds PlateShift to the Open with list when you right-click a 3MF file. "
                                  "Your default app for 3MF files doesn't change."))
            add.configure(state="disabled" if on else "normal")
            remove.configure(state="normal" if on else "disabled")

        def change(fn):
            try:
                fn()
            except OSError as exc:
                messagebox.showerror("PlateShift options", f"That didn't work: {exc}", parent=win)
            refresh()
        add = ttk.Button(row, text="Add to Open with", command=lambda: change(integration.add_open_with))
        remove = ttk.Button(row, text="Remove", command=lambda: change(integration.remove_open_with))
        if sys.platform != "darwin":
            add.pack(side="left")
            remove.pack(side="left", padx=(6, 0))
        refresh()

        ttk.Separator(box).pack(fill="x", pady=12)
        ttk.Label(box, text="New downloads", font=ui_font(10, "bold")).pack(anchor="w")
        watch = tk.BooleanVar(value=bool(self.settings.get("watch_downloads")))
        ttk.Checkbutton(box, variable=watch, text="Open new 3MF downloads in PlateShift while it's open").pack(
            anchor="w", pady=(4, 0))
        ttk.Label(box, foreground=MUTED, wraplength=440, justify="left",
                  text=f"Watches {integration.downloads_folder()}. When a 3MF file finishes downloading, "
                       "for example from MakerWorld, PlateShift comes forward with it ready to convert.").pack(
            anchor="w", pady=(2, 0))

        def watch_changed(*_):
            self.settings["watch_downloads"] = watch.get()
            save_settings(self.settings)
            self.start_watching() if watch.get() else self.stop_watching()
        watch.trace_add("write", watch_changed)
        ttk.Button(box, text="Close", command=win.destroy).pack(anchor="e", pady=(14, 0))
        win.bind("<Escape>", lambda e: win.destroy())
        self.options_vars = {"watch": watch}  # for tests

    def start_watching(self, folder: Path | None = None):
        self.watcher = integration.DownloadWatcher(folder or integration.downloads_folder(),
                                                   [t.suffix for t in self.targets])
        self.root.after(2000, self.watch_tick)

    def stop_watching(self):
        self.watcher = None

    def watch_tick(self):
        watcher = self.watcher
        if watcher is None:
            return
        for path in watcher.check():
            self.write(f"New download: {path.name}")
            self.bring_forward()
            if not self.busy:
                self.handle_files([path])
                break  # one at a time; the next check offers the rest
            watcher.done.discard(path)   # busy: offer it again later
        if self.watcher is watcher:
            self.root.after(2000, self.watch_tick)

    def bring_forward(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    # --- sending to the printer -------------------------------------------

    def send_dialog(self, item: dict):
        """send a converted file's g-code to the printer, and start it if asked"""
        target, output = item["target"], Path(item["output"])
        kind = sendprint.kind_for(target.vendor)
        old = getattr(self, "send_window", None)
        if old is not None and old.winfo_exists():
            old.destroy()
        win = self.send_window = tk.Toplevel(self.root)
        win.title("Send to printer")
        win.resizable(False, False)
        win.transient(self.root)
        box = ttk.Frame(win, padding=16)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=f"{output.name} → {target.label}", font=ui_font(10, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        saved = dict(self.settings.get("printer_hosts", {}).get(target.key, {}))
        from_flash_studio = False
        if kind == "flashforge" and not (saved.get("serial") and saved.get("check_code")):
            # flash studio already knows the printer: its serial and check code, and maybe its address
            known = sendprint.flash_studio_match(target.label, sendprint.flash_studio_printers())
            if known:
                saved.setdefault("host", known["host"])
                saved["host"] = saved["host"] or known["host"]
                saved["serial"], saved["check_code"] = known["serial"], known["check_code"]
                from_flash_studio = True
        host = tk.StringVar(value=saved.get("host", ""))
        serial = tk.StringVar(value=saved.get("serial", ""))
        check = tk.StringVar(value=saved.get("check_code", ""))
        row = 1
        ttk.Label(box, text="Printer address").grid(row=row, column=0, sticky="w")
        ttk.Entry(box, textvariable=host, width=24).grid(row=row, column=1, sticky="w", padx=(8, 0))
        row += 1
        ttk.Label(box, foreground=MUTED, text="Its IP address, on the printer's screen under network settings").grid(
            row=row, column=1, sticky="w", padx=(8, 0))
        if kind == "flashforge":
            row += 1
            ttk.Label(box, text="Serial number").grid(row=row, column=0, sticky="w", pady=(6, 0))
            ttk.Entry(box, textvariable=serial, width=24).grid(row=row, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
            row += 1
            ttk.Label(box, text="Check code").grid(row=row, column=0, sticky="w", pady=(6, 0))
            ttk.Entry(box, textvariable=check, width=24).grid(row=row, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
            row += 1
            ttk.Label(box, foreground=MUTED, wraplength=320, justify="left",
                      text=("Filled in from Flash Studio, which has connected to this printer." if from_flash_studio
                            else "Both are on the printer's screen: the serial number under About, the check code "
                                 "where LAN mode is switched on.")).grid(row=row, column=1, sticky="w", padx=(8, 0))
        gcode = list(item.get("gcode") or [])
        plate = tk.StringVar(value="1")
        if len(gcode) > 1:
            row += 1
            ttk.Label(box, text="Plate").grid(row=row, column=0, sticky="w", pady=(6, 0))
            plates = ttk.Combobox(box, textvariable=plate, values=[str(i) for i in range(1, len(gcode) + 1)],
                                  state="readonly", width=5)
            plates.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        start = tk.BooleanVar(value=False)
        row += 1
        ttk.Checkbutton(box, variable=start, text="Start printing now").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))
        row += 1
        ttk.Label(box, foreground=MUTED, wraplength=380, justify="left",
                  text="Check the bed is clear and the right filament is loaded first. Otherwise the file is "
                       "only sent, and you start it from the printer's screen.").grid(
            row=row, column=0, columnspan=2, sticky="w")
        row += 1
        status = tk.Label(box, text="", wraplength=380, justify="left", font=ui_font(9),
                          **({"bg": self.bg} if self.bg else {}))
        status.grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(box)
        buttons.grid(row=row + 1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        send = ttk.Button(buttons, text="Send")
        send.pack(side="right")
        ttk.Button(buttons, text="Close", command=win.destroy).pack(side="right", padx=6)

        def say(text: str, colour: str = MUTED):
            self.later(lambda: status.winfo_exists() and status.configure(text=text, fg=colour))

        def go():
            address = host.get().strip()
            if not address:
                status.configure(text="Type the printer's address first.", fg=FAIL_COLOUR)
                return
            entry = {"host": address}
            if kind == "flashforge":
                entry.update(serial=serial.get().strip(), check_code=check.get().strip())
            self.settings.setdefault("printer_hosts", {})[target.key] = entry
            save_settings(self.settings)
            conn = sendprint.Connection(kind, address, entry.get("serial", ""), entry.get("check_code", ""))
            starting = start.get()
            send.configure(state="disabled")
            number = int(plate.get() or 1)

            def work():
                try:
                    files = gcode
                    if not files:
                        slicer = self.slicer_for_testing(target)
                        if slicer is None:
                            raise sendprint.SendError("Sending needs a slicer that can slice from the command line "
                                                      "(Flash Studio or OrcaSlicer).")
                        say(f"Slicing in {slicer.label}…")
                        result = test_slice(slicer, output, keep=Path(tempfile.mkdtemp(prefix="plateshift-gcode-")))
                        if not result.ok:
                            raise sendprint.SendError(f"It didn't slice: {result.error}")
                        files = result.gcode
                        item["gcode"] = files
                    chosen = files[min(number, len(files)) - 1]
                    name = output.stem + (f" - plate {number}" if len(files) > 1 else "") + ".gcode"
                    upload = Path(tempfile.mkdtemp(prefix="plateshift-send-")) / name
                    shutil.copyfile(chosen, upload)
                    colours, materials = sendprint.gcode_filaments(upload)
                    say("Starting the print…" if starting else "Sending…")
                    sent = sendprint.send(conn, upload, starting, colours, materials)
                    text = (f"✓ Printing {sent.file} on {target.label}." if sent.started else
                            f"✓ Sent {sent.file} to {target.label}. Start it from the printer's screen.")
                    if sent.notes:
                        text += "\n⚠ " + "\n⚠ ".join(sent.notes)
                    say(text, OK_COLOUR if not sent.notes else WARN_COLOUR)
                    self.write(text.replace("\n", "\n  "))
                except sendprint.SendError as exc:
                    say(f"✗ {exc}", FAIL_COLOUR)
                except Exception as exc:  # anything else: say so, never leave the window stuck
                    say(f"✗ {_explain(exc)}", FAIL_COLOUR)
                finally:
                    self.later(lambda: send.winfo_exists() and send.configure(state="normal"))
            threading.Thread(target=work, daemon=True).start()
        send.configure(command=go)
        win.bind("<Escape>", lambda e: win.destroy())
        self.send_vars = {"host": host, "serial": serial, "check": check, "start": start, "plate": plate,
                          "send": go, "status": status}  # for tests

    # --- problem reports ---------------------------------------------------

    def report_problem(self):
        """zip the last run's files with the log, then open a new GitHub issue to attach it to"""
        files = [f for f in getattr(self, "last_run", []) if Path(f).is_file()]
        what = (f"the {len(files)} project file{'s' if len(files) != 1 else ''} from the last conversion, "
                if files else "")
        if not messagebox.askokcancel(
                "Report a problem",
                f"PlateShift will make a zip with {what}the log, version details and your PlateShift "
                "settings, so the problem can be looked into.\n\nNothing is sent anywhere. Your browser then "
                "opens a new GitHub issue, and you choose whether to attach the zip.", parent=self.root):
            return
        about = bugreport.details(self.target().label, {s.label: str(s.exe) for s in self.slicers.values()})
        folder = Path(files[0]).parent if files else Path.home()
        try:
            # printer addresses and check codes stay private
            shared = {k: v for k, v in self.settings.items() if k != "printer_hosts"}
            path = bugreport.make(folder, files, self.log.get("1.0", "end"), about, shared)
        except OSError as exc:
            messagebox.showerror("Report a problem", f"Couldn't make the zip: {exc}", parent=self.root)
            return
        self.write(f"Problem report saved: {path}\n")
        reveal(path)
        webbrowser.open(bugreport.issue_url("Problem: ", about))

    # --- adding printers ---------------------------------------------------

    def printer_wizard(self):
        """pick a printer from the profiles your slicers have installed"""
        if getattr(self, "wizard_window", None) is not None and self.wizard_window.winfo_exists():
            self.wizard_window.lift()
            return
        win = self.wizard_window = tk.Toplevel(self.root)
        win.title("Add a printer")
        win.resizable(False, False)
        win.transient(self.root)
        box = ttk.Frame(win, padding=16)
        box.pack(fill="both", expand=True)
        ttk.Label(box, wraplength=440, justify="left",
                  text="Choose a printer your slicers have profiles for. It's added to the brand and model "
                       "boxes, and PlateShift converts for it with those profiles.").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        found = catalogue.vendors()
        ttk.Label(box, text="Brand").grid(row=1, column=0, sticky="w")
        brand = tk.StringVar()
        brands = ttk.Combobox(box, textvariable=brand, values=list(found), state="readonly", width=30)
        brands.grid(row=1, column=1, sticky="w", padx=(8, 0))
        typeahead.attach(brands)
        ttk.Label(box, text="Printer").grid(row=2, column=0, sticky="w", pady=(6, 0))
        model = tk.StringVar()
        models = ttk.Combobox(box, textvariable=model, state="readonly", width=40)
        models.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        typeahead.attach(models)
        about = ttk.Label(box, foreground=MUTED, wraplength=440, justify="left")
        about.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(4, 0))
        slots_label = ttk.Label(box, text="Colours at once")
        slots = tk.StringVar(value="1")
        slots_box = ttk.Spinbox(box, from_=1, to=16, textvariable=slots, width=5)
        slots_hint = ttk.Label(box, foreground=MUTED, wraplength=440, justify="left",
                               text="More than 1 if a colour unit (AMS, CFS, MMU…) feeds its nozzle.")
        listed: dict[str, catalogue.Machine] = {}
        if not found:
            about.configure(text="No printer profiles found. Install OrcaSlicer, Flash Studio or Creality Print.")

        def brand_picked(*_):
            win.configure(cursor="watch")
            win.update_idletasks()
            try:
                listed.clear()
                listed.update({m.label: m for m in catalogue.machines(brand.get(), found[brand.get()])})
            finally:
                win.configure(cursor="")
            models.configure(values=list(listed))
            model.set(next(iter(listed), ""))

        def model_picked(*_):
            machine = listed.get(model.get())
            for w in (slots_label, slots_box, slots_hint):
                w.grid_remove()
            if machine is None:
                about.configure(text="")
                return
            heads = f"{machine.nozzles} toolheads" if machine.nozzles > 1 else "one nozzle"
            about.configure(text=f"{heads} · {machine.nozzle} mm · profiles from {machine.slicer}")
            if machine.nozzles == 1:
                slots_label.grid(row=4, column=0, sticky="w", pady=(8, 0))
                slots_box.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
                slots_hint.grid(row=5, column=1, sticky="w", padx=(8, 0))
        brand.trace_add("write", brand_picked)
        model.trace_add("write", model_picked)

        def add():
            machine = listed.get(model.get())
            if machine is None:
                return
            try:
                colours = int(slots.get()) if machine.nozzles == 1 else machine.nozzles
            except ValueError:
                colours = 1
            target = catalogue.make_target(machine, max(1, min(colours, 16)))
            # a printer PlateShift lists already: just choose it
            same = next((t for t in self.targets if t.toolheads == target.toolheads and target.machine in
                         {t.machine, *(m for m, _ in t.alternatives)}), None)
            if same is None:
                saved = self.settings.setdefault("my_printers", [])
                saved[:] = [d for d in saved if d.get("key") != target.key] + [catalogue.to_dict(target)]
                save_settings(self.settings)
                self.targets = list(TARGETS) + self.my_printers()
                same = target
            win.destroy()
            self.printer.set(same.label)
            self.show_printer()

        mine = self.my_printers()
        if mine:
            ttk.Separator(box).grid(row=7, column=0, columnspan=3, sticky="we", pady=12)
            ttk.Label(box, text="Printers you added", font=ui_font(9, "bold")).grid(row=8, column=0, columnspan=3,
                                                                                    sticky="w")
            for i, t in enumerate(mine):
                ttk.Label(box, text=t.label).grid(row=9 + i, column=0, columnspan=2, sticky="w", pady=1)
                ttk.Button(box, text="Remove", command=lambda t=t: self.remove_printer(t)).grid(
                    row=9 + i, column=2, sticky="e", pady=1)
        buttons = ttk.Frame(box)
        buttons.grid(row=50, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Add", command=add).pack(side="right")
        ttk.Button(buttons, text="Close", command=win.destroy).pack(side="right", padx=6)
        win.bind("<Escape>", lambda e: win.destroy())
        self.wizard = {"brand": brand, "model": model, "slots": slots, "add": add}  # for tests

    def remove_printer(self, target):
        saved = self.settings.get("my_printers", [])
        saved[:] = [d for d in saved if d.get("key") != target.key]
        save_settings(self.settings)
        self.targets = list(TARGETS) + self.my_printers()
        if self.printer.get() == target.label:
            self.printer.set(self.targets[0].label)
        self.show_printer()
        if getattr(self, "wizard_window", None) is not None and self.wizard_window.winfo_exists():
            self.wizard_window.destroy()
            self.printer_wizard()

    def licences(self):
        """PlateShift's licence and those of what the app carries with it"""
        if getattr(self, "licences_window", None) is not None and self.licences_window.winfo_exists():
            self.licences_window.lift()
            return
        from .licence_texts import LICENCES
        win = self.licences_window = tk.Toplevel(self.root)
        win.title("PlateShift licences")
        win.geometry("820x560")
        win.minsize(560, 360)
        box = ttk.Frame(win, padding=12)
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=1)
        box.rowconfigure(0, weight=1)
        names = tk.Listbox(box, width=16, exportselection=False, activestyle="none", font=ui_font(10))
        for name, _, _ in LICENCES:
            names.insert("end", name)
        names.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        right = ttk.Frame(box)
        right.grid(row=0, column=1, sticky="nsew")
        summary = ttk.Label(right, font=ui_font(9, "bold"), wraplength=600, justify="left")
        summary.pack(anchor="w", pady=(0, 6))
        text = tk.Text(right, wrap="word", font=mono_font(9), relief="solid", bd=1, padx=8, pady=6)
        scroll = ttk.Scrollbar(right, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        notice = (f"PlateShift {__version__}\nCopyright (C) 2026 PlateShift contributors\n{REPO_URL}\n\n"
                  "This program is free software: you can redistribute it and/or modify it under the terms "
                  "of the GNU Affero General Public License as published by the Free Software Foundation, "
                  "either version 3 of the License, or (at your option) any later version.\n\n"
                  "This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; "
                  "without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. "
                  "See the GNU Affero General Public License below for more details.\n\n"
                  "The source code of this version is at the address above.\n\n" + "─" * 40 + "\n\n")

        def show(_event=None):
            picked = names.curselection()
            index = picked[0] if picked else 0
            name, about, body = LICENCES[index]
            summary.configure(text=f"{name}: {about}")
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("1.0", (notice if index == 0 else "") + body)
            text.configure(state="disabled")
            text.yview_moveto(0)
        names.bind("<<ListboxSelect>>", show)
        names.selection_set(0)
        show()
        ttk.Button(box, text="Close", command=win.destroy).grid(row=1, column=1, sticky="e", pady=(10, 0))
        win.bind("<Escape>", lambda e: win.destroy())

    def slicer_for_testing(self, target):
        """the slicer you'll open the file in, when it can slice from the command line
        and has the printer; else the first of the printer's slicers that can"""
        chosen = self.chosen_slicer() if hasattr(self, "open_choice") else None
        if chosen is not None and chosen.label in CAN_TEST and chosen.label in target.slicers:
            return chosen
        return test_slicer(self.slicers, target)

    def update_tester(self):
        # the test slice needs a slicer with this printer's profiles
        target = self.target()
        self.tester = self.slicer_for_testing(target)
        if self.tester:
            self.checker.configure(text=f"Test slice and price with {self.tester.label} afterwards",
                                   state="disabled" if self.busy else "normal")
        else:
            needs = " or ".join(n for n in target.slicers if n in CAN_TEST)
            self.checker.configure(text=f"Test slice (needs {needs})" if needs
                                   else "Test slice (not possible with Creality Print)", state="disabled")

    def chosen_process(self) -> str | None:
        """the print profile picked for this printer; None for automatic"""
        return self.process_names.get(self.process_choice.get())

    def update_keeper(self):
        if hasattr(self, "keeper"):
            # the designer's settings always go over an automatic choice
            self.keeper.configure(state="normal" if self.chosen_process() and not self.busy else "disabled")

    def update_processes(self):
        """fill the print profile list for the printer, loading its profiles in the background"""
        target = self.target()
        quiet, self._quiet = self._quiet, True
        self.process_names = {}
        self.process_picker.configure(values=[AUTO_PROCESS])
        self.process_choice.set(AUTO_PROCESS)
        self._quiet = quiet
        self.update_keeper()

        def load():
            try:
                located, lib = self.library(target)
                choices = process_choices(lib, located)
            except Exception:  # no profiles: automatic is all there is, and converting says why
                return
            self.later(lambda: self.show_processes(target, choices))
        threading.Thread(target=load, daemon=True).start()

    def show_processes(self, target, choices: list[dict]):
        if target.key != self.target().key:
            return  # the printer changed while loading
        self.process_names = {_display(p): p["name"] for p in choices}
        self.process_picker.configure(values=[AUTO_PROCESS, *self.process_names])
        saved = self.settings.get("process_by_printer", {}).get(target.key)
        shown = next((text for text, name in self.process_names.items() if name == saved), AUTO_PROCESS)
        quiet, self._quiet = self._quiet, True
        self.process_choice.set(shown)
        self._quiet = quiet
        self.update_keeper()

    def show_printer(self):
        """set the brand and model boxes to the chosen printer"""
        target = self.target()
        self._syncing = True
        try:
            self.brand_picker.configure(values=self.brands())
            self.brand.set(target.vendor)
            self.picker.configure(values=[_model_name(t) for t in self.targets if t.vendor == target.vendor])
            self.model.set(_model_name(target))
        finally:
            self._syncing = False

    def brand_changed(self):
        if self._syncing:
            return
        models = [t for t in self.targets if t.vendor == self.brand.get()]
        if not models:
            return
        leaving = self.target()  # so coming back to its brand returns to it
        self.settings.setdefault("printer_by_brand", {})[leaving.vendor] = leaving.label
        # the model last used for this brand, else its first
        last = self.settings.get("printer_by_brand", {}).get(self.brand.get()) or self.settings.get("printer")
        self.printer.set(next((t.label for t in models if t.label == last), models[0].label))

    def model_changed(self):
        if self._syncing:
            return
        target = next((t for t in self.targets if t.vendor == self.brand.get() and _model_name(t) == self.model.get()),
                      None)
        if target and target.label != self.printer.get():
            self.printer.set(target.label)

    def printer_changed(self):
        self.show_printer()
        self.update_slicer_choices()
        self.update_tester()
        self.update_processes()
        # a panel built for the old printer has the wrong toolheads and presets
        if getattr(self, "plan", None) is not None and not self.busy:
            source = self.plan_source
            self.close_plan()
            self.prepare(source)

    def close_plan(self):
        for child in self.plan_frame.winfo_children():
            child.destroy()
        self.plan_frame.pack_forget()
        self.plan = None

    def convert_plan(self):
        plan, source = self.plan, self.plan_source
        merge = self.current_merge()
        # only presets the user picked are passed; the rest are chosen for the
        # final layout by convert, the same way the panel suggested them
        overrides = {}
        target = self.plan_target
        usual = self.settings.setdefault("filaments_by_printer", {}).setdefault(target.key, {})
        for t in sorted(set(merge)):
            chosen = self.preset_names.get(self.head_vars[t].get())
            if chosen and (t in self.head_user_set or t in self.head_usual):
                overrides[t] = chosen
            if chosen and t in self.head_user_set:
                usual[str(t)] = chosen  # offered first next time
        save_settings(self.settings)
        colours = {t: c for t, c in self.head_colours.items() if t in set(merge)}
        plates = self.chosen_plates()
        if not plates:
            messagebox.showinfo("No plates ticked", "Tick at least one plate to convert.", parent=self.root)
            return
        split = self.split_plates.get() and len(self.plate_vars) > 1
        output = None
        if not split:  # a file per plate never replaces anything; it keeps both
            output = self.choose_output(default_output(source, target))
            if output is None:
                return  # cancelled; the panel stays open
        self.close_plan()
        self.run([source], plan=plan, merge=merge, overrides=overrides, colours=colours,
                 target=target, outputs={source: output} if output else None,
                 plates=None if len(plates) == len(self.plate_vars) and not split else plates, split=split)

    def choose_output(self, output: Path) -> Path | None:
        """ask before replacing a file that's already there"""
        if not output.exists():
            return output
        spare = unique_output(output)
        answer = messagebox.askyesnocancel(
            "File already exists",
            f"{output.name} already exists.\n\nYes: replace it\nNo: keep both, saving as {spare.name}\n"
            "Cancel: don't convert", parent=self.root)
        if answer is None:
            return None
        return output if answer else spare

    # --- converting -------------------------------------------------------

    def run(self, files: list[Path], plan=None, merge=None, overrides=None, colours=None, target=None,
            outputs: dict[Path, Path] | None = None, plates: list[int] | None = None, split: bool = False):
        # plates: the ones to keep (all when None); split: a file for each of them
        # a plan is only valid for the printer it was made for
        target = target or self.target()
        testing = self.check.get() and self.tester is not None
        process = self.chosen_process() if target.key == self.target().key else None
        keep_design = self.keep_design.get()
        prices, currency = dict(self.prices), self.currency_symbol()
        self.run_files = list(files)
        self.set_busy(True)
        self.show.pack_forget()
        self.open_button.pack_forget()
        count = len(plates) if split and plates else len(files)
        self.progress.configure(maximum=count * (2 if testing else 1), value=0)
        self.set_status(f"Converting {len(files)} file(s)…")

        def work():
            nonlocal target
            converted, failed, slice_failed = [], [], []
            self.results = []   # one summary per file, for the results window
            step = 0
            started = 0  # files begun, so a crash outside one knows which are left
            try:
                target, lib = self.library(target)
                for number, source in enumerate(files, start=1):
                    started = number
                    self.set_status(f"Converting {number} of {len(files)}: {source.name}…")
                    # batches never replace an earlier conversion; they keep both
                    if split and plates:
                        jobs = [([n], unique_output(plate_output(default_output(source, target), n)))
                                for n in plates]
                    else:
                        jobs = [(plates, (outputs or {}).get(source)
                                 or unique_output(default_output(source, target)))]
                    for chosen, output in jobs:
                        try:
                            report = convert(source, output, target, lib, overrides or {},
                                             merge=merge, plan=plan if len(files) == 1 else None,
                                             colours=colours, process=process, keep_design=keep_design,
                                             plates=chosen, prices=prices)
                        except Exception as exc:  # one bad file mustn't stop the rest
                            failed.append(source)
                            self.results.append({"name": source.name, "error": _explain(exc)})
                            self.write(f"✗ {source.name}: not converted", "fail")
                            self.write(f"  {_explain(exc)}\n")
                            step += (2 if testing else 1) * len(jobs)
                            self.later(lambda n=step: self.progress.configure(value=n))
                            break  # the same problem would stop every plate
                        converted.append(report.output)
                        summary = {"name": report.output.name, "warnings": list(report.warnings)}
                        summary["output"] = report.output
                        summary["target"] = target
                        self.results.append(summary)
                        lines = report.lines()
                        self.write(f"✓ {lines[0]}", "ok")
                        self.write("\n".join(lines[1:]))
                        step += 1
                        self.later(lambda n=step: self.progress.configure(value=n))
                        if testing:
                            self.set_status(f"Test slicing {output.name} in {self.tester.label}…")
                            # the g-code is kept, so sending it to the printer needn't slice again
                            result = test_slice(self.tester, output,
                                                keep=Path(tempfile.mkdtemp(prefix="plateshift-gcode-")))
                            summary["gcode"] = list(result.gcode)
                            self.write(f"  {'✓' if result.ok else '✗'} {result.line()}",
                                       "ok" if result.ok else "fail")
                            if not result.ok:
                                slice_failed.append(output)
                            summary["slice"] = result
                            if result.ok:
                                summary["cost"] = filament_total(result.grams_each, report.prices)
                                summary["currency"] = currency
                            for line in cost_lines(result.grams_each, report.slots, report.prices, currency,
                                                   report.slot_word):
                                self.write(f"  {line}")
                            step += 1
                            self.later(lambda n=step: self.progress.configure(value=n))
                        self.write("")
            except Exception as exc:  # no profiles, or something unexpected outside a file
                failed.extend(f for f in files[max(started - 1, 0):] if f not in failed)
                self.write(f"✗ {_explain(exc)}\n", "fail")
            finally:
                self.later(lambda: self.finish(converted, failed, slice_failed))

        threading.Thread(target=work, daemon=True).start()

    def finish(self, converted: list[Path], failed: list[Path], slice_failed: list[Path]):
        self.set_busy(False)
        # what a problem report would need
        self.last_run = list(dict.fromkeys([*getattr(self, "run_files", []), *converted]))
        self.progress.configure(value=self.progress["maximum"])
        ok = converted and not failed and not slice_failed
        if ok:
            text = (f"✓ Done: {converted[0].name} is ready" if len(converted) == 1
                    else f"✓ Done: all {len(converted)} files converted")
        elif converted and slice_failed and not failed:
            text = f"Done, but {len(slice_failed)} file(s) failed the test slice (see above)"
        elif converted:
            text = f"Done: {len(converted)} converted, {len(failed)} not converted (see above)"
        else:
            text = "✗ Nothing was converted (see above)"
        colour = OK_COLOUR if ok else FAIL_COLOUR
        if converted:
            self.last_output = converted[-1]
            self.show.pack(side="right")
            self.update_open_button()
            slicer = self.chosen_slicer()
            if slicer and len(converted) == 1:
                if self.open_last():
                    text += f", opening in {slicer.label}"
            elif slicer:
                text += f". Use Open to load {self.last_output.name}"
        self.status.configure(text=text, fg=colour, font=ui_font(10, "bold"))
        self.write(text.lstrip("✓✗ ").join(("── ", " ──")) + "\n", "done" if ok else "fail")
        self.root.bell()
        self.show_results(text, ok)

    def show_results(self, headline: str, ok: bool):
        """a small window with each file's outcome, so it isn't missed in the log"""
        results = getattr(self, "results", [])
        if not results:
            return
        old = getattr(self, "results_window", None)
        if old is not None and old.winfo_exists():
            old.destroy()
        win = self.results_window = tk.Toplevel(self.root)
        win.title("Conversion results")
        win.resizable(False, False)
        win.transient(self.root)
        box = ttk.Frame(win, padding=16)
        box.pack(fill="both", expand=True)
        tk.Label(box, text=headline.split(", opening")[0], fg=OK_COLOUR if ok else FAIL_COLOUR,
                 font=ui_font(11, "bold"), wraplength=460, justify="left",
                 **({"bg": self.bg} if self.bg else {})).pack(anchor="w", pady=(0, 8))
        shown = results[:8]
        for item in shown:
            row = ttk.Frame(box)
            row.pack(fill="x", anchor="w", pady=(4, 0))
            failed = "error" in item or (item.get("slice") is not None and not item["slice"].ok)
            mark = tk.Label(row, text="✗" if failed else "✓", fg=FAIL_COLOUR if failed else OK_COLOUR,
                            font=ui_font(10, "bold"), **({"bg": self.bg} if self.bg else {}))
            mark.pack(side="left", anchor="n")
            text = ttk.Frame(row)
            text.pack(side="left", padx=(6, 0), fill="x")
            ttk.Label(text, text=item["name"], font=ui_font(10, "bold")).pack(anchor="w")
            for line, colour in self.result_lines(item):
                ttk.Label(text, text=line, foreground=colour, wraplength=430, justify="left").pack(anchor="w")
        if len(results) > len(shown):
            ttk.Label(box, text=f"…and {len(results) - len(shown)} more; see the log.",
                      foreground=MUTED).pack(anchor="w", pady=(6, 0))
        buttons = ttk.Frame(box)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Close", command=win.destroy).pack(side="right")
        ttk.Button(buttons, text="Report a problem…", command=self.report_problem).pack(side="left")
        sendable = [r for r in results if "output" in r and sendprint.kind_for(r["target"].vendor)]
        if len(sendable) == 1 and len(results) == 1:
            ttk.Button(buttons, text="Send to printer…",
                       command=lambda: self.send_dialog(sendable[0])).pack(side="right", padx=(6, 0))
        if self.last_output is not None and any("error" not in r for r in results):
            ttk.Button(buttons, text="Show in folder", command=self.show_in_folder).pack(side="right", padx=6)
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Return>", lambda e: win.destroy())
        win.focus_set()

    @staticmethod
    def result_lines(item: dict) -> list[tuple[str, str]]:
        """the short lines under a file's name: what came out, and what to look at"""
        if "error" in item:
            return [(f"Not converted: {item['error']}", FAIL_COLOUR)]
        lines = []
        result = item.get("slice")
        if result is not None and not result.ok:
            lines.append((f"Test slice failed: {result.error}", FAIL_COLOUR))
        elif result is not None:
            h, m = divmod(result.seconds // 60, 60)
            parts = [f"{h} h {m} min" if h else f"{m} min", f"{result.grams:.0f} g"]
            if result.plates > 1:
                parts.append(f"{result.plates} plates")
            total, missing = item.get("cost", (0.0, 0))
            if total:
                parts.append(money(total, item.get("currency", "$")) + (" so far" if missing else ""))
            lines.append(("  ·  ".join(parts), "#1f2328"))
            if missing:
                lines.append((f"{missing} filament{'s' if missing > 1 else ''} without a price; "
                              "set one with the ⚙ beside it", WARN_COLOUR))
        else:
            lines.append(("Converted (not test sliced)", MUTED))
        for warning in item.get("warnings", [])[:3]:
            lines.append((f"⚠ {warning}", WARN_COLOUR))
        extra = len(item.get("warnings", [])) - 3
        if extra > 0:
            lines.append((f"⚠ and {extra} more in the log", WARN_COLOUR))
        return lines


def main(files: list[str] | None = None) -> int:
    if files is None:
        files = sys.argv[1:]
    root = tk.Tk()
    App(root, [Path(f) for f in files or [] if f.lower().endswith(".3mf")])
    root.mainloop()
    return 0
