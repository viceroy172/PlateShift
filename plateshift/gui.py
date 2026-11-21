"""Tk front end."""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

from . import REPO_URL, __version__, dnd
from .icon_data import ICONS
from .convert import (ConversionError, analyse, convert, default_output, lead_colour, process_choices,
                      suggest, unique_output)
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


AUTO_PROCESS = "Automatic (closest to the project's layer height)"


def _display(profile: dict) -> str:
    return f"{profile['name']}  (yours, {profile['_user']})" if profile.get("_user") else profile["name"]


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
        labels = [t.label for t in TARGETS]
        saved_printer = self.settings.get("printer")
        self.printer = tk.StringVar(value=saved_printer if saved_printer in labels else labels[0])
        self.picker = ttk.Combobox(top, textvariable=self.printer, state="readonly", width=34,
                                   values=labels)
        self.picker.grid(row=0, column=1, sticky="w", padx=8)
        self.button = ttk.Button(top, text="Choose 3MF files…", command=self.choose)
        self.button.grid(row=0, column=2, sticky="e")

        ttk.Label(top, text="Then open in").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.open_choice = tk.StringVar(value=DONT_OPEN)
        self.opener = ttk.Combobox(top, textvariable=self.open_choice, state="readonly", width=34)
        self.opener.grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))
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
        if sys.platform == "darwin":
            root.createcommand("tkAboutDialog", self.about)  # the app menu's "About PlateShift"

        self.open_choice.trace_add("write", lambda *_: self.remember(slicer=True))
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
        return next(t for t in TARGETS if t.label == self.printer.get())

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
            result = (None, None, None)
            try:
                located, lib = self.library(target)
                result = (source, located, analyse(source, located, lib))
            except Exception as exc:  # anything at all: the window must never stay busy
                self.write(f"✗ {source.name}: not converted", "fail")
                self.write(f"  {_explain(exc)}\n")
            finally:
                self.later(lambda: self.after_prepare(*result))

        threading.Thread(target=work, daemon=True).start()

    def after_prepare(self, source, target, plan):
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.set_busy(False)
        if plan is None:
            self.status.configure(text="✗ Nothing was converted (see below)", fg=FAIL_COLOUR,
                                  font=ui_font(10, "bold"))
            return
        self.show_plan(source, target, plan)

    def show_plan(self, source: Path, target, plan):
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
        self.head_user_set: set[int] = set()
        self.head_colours: dict[int, str] = {}
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
            note = ""
            if t not in self.head_user_set:
                name, note = suggest(plan, merge, t)
                self._quiet = True
                var.set(by_name.get(name, ""))
                self._quiet = False
            ttk.Combobox(self.heads_frame, textvariable=var, values=list(self.preset_names),
                         width=30, state="readonly").grid(row=row, column=2, sticky="we", padx=(8, 0), pady=1)
            if note:  # e.g. no TPU preset, so PLA was used
                row += 1
                ttk.Label(self.heads_frame, text=f"⚠ {note}", foreground=WARN_COLOUR).grid(
                    row=row, column=2, sticky="w", padx=(8, 0))

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
                  text="Source code, help and new versions are on GitHub. Open source under the MIT licence. "
                       "Not affiliated with Bambu Lab, Flashforge, Creality or MakerWorld.").pack(
            anchor="w", pady=(10, 0))
        ttk.Button(box, text="Close", command=win.destroy).pack(anchor="e", pady=(14, 0))
        win.bind("<Escape>", lambda e: win.destroy())

    def update_tester(self):
        # the test slice needs a slicer with this printer's profiles
        target = self.target()
        self.tester = test_slicer(self.slicers, target)
        if self.tester:
            self.checker.configure(text=f"Test slice with {self.tester.label} afterwards",
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

    def printer_changed(self):
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
        for t in sorted(set(merge)):
            chosen = self.preset_names.get(self.head_vars[t].get())
            if chosen and t in self.head_user_set:
                overrides[t] = chosen
        colours = {t: c for t, c in self.head_colours.items() if t in set(merge)}
        target = self.plan_target
        output = self.choose_output(default_output(source, target))
        if output is None:
            return  # cancelled; the panel stays open
        self.close_plan()
        self.run([source], plan=plan, merge=merge, overrides=overrides, colours=colours,
                 target=target, outputs={source: output})

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
            outputs: dict[Path, Path] | None = None):
        # a plan is only valid for the printer it was made for
        target = target or self.target()
        testing = self.check.get() and self.tester is not None
        process = self.chosen_process() if target.key == self.target().key else None
        keep_design = self.keep_design.get()
        self.set_busy(True)
        self.show.pack_forget()
        self.open_button.pack_forget()
        self.progress.configure(maximum=len(files) * (2 if testing else 1), value=0)
        self.set_status(f"Converting {len(files)} file(s)…")

        def work():
            nonlocal target
            converted, failed, slice_failed = [], [], []
            step = 0
            try:
                target, lib = self.library(target)
                for number, source in enumerate(files, start=1):
                    self.set_status(f"Converting {number} of {len(files)}: {source.name}…")
                    # batches never replace an earlier conversion; they keep both
                    output = (outputs or {}).get(source) or unique_output(default_output(source, target))
                    try:
                        report = convert(source, output, target, lib, overrides or {},
                                         merge=merge, plan=plan if len(files) == 1 else None,
                                         colours=colours, process=process, keep_design=keep_design)
                    except Exception as exc:  # one bad file mustn't stop the rest
                        failed.append(source)
                        self.write(f"✗ {source.name}: not converted", "fail")
                        self.write(f"  {_explain(exc)}\n")
                        step += 2 if testing else 1
                        self.later(lambda n=step: self.progress.configure(value=n))
                        continue
                    converted.append(report.output)
                    lines = report.lines()
                    self.write(f"✓ {lines[0]}", "ok")
                    self.write("\n".join(lines[1:]))
                    step += 1
                    self.later(lambda n=step: self.progress.configure(value=n))
                    if testing:
                        self.set_status(f"Test slicing {output.name} in {self.tester.label}…")
                        result = test_slice(self.tester, output)
                        self.write(f"  {'✓' if result.ok else '✗'} {result.line()}", "ok" if result.ok else "fail")
                        if not result.ok:
                            slice_failed.append(output)
                        step += 1
                        self.later(lambda n=step: self.progress.configure(value=n))
                    self.write("")
            except Exception as exc:  # no profiles, or something unexpected outside a file
                failed.extend(files[len(converted) + len(failed):])
                self.write(f"✗ {_explain(exc)}\n", "fail")
            finally:
                self.later(lambda: self.finish(converted, failed, slice_failed))

        threading.Thread(target=work, daemon=True).start()

    def finish(self, converted: list[Path], failed: list[Path], slice_failed: list[Path]):
        self.set_busy(False)
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


def main(files: list[str] | None = None) -> int:
    if files is None:
        files = sys.argv[1:]
    root = tk.Tk()
    App(root, [Path(f) for f in files or [] if f.lower().endswith(".3mf")])
    root.mainloop()
    return 0
