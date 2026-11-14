"""Tk front end."""

from __future__ import annotations

import json
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, ttk

from . import dnd
from .convert import ConversionError, analyse, convert, default_output, lead_colour, suggest_preset
from .printers import TARGETS
from .profiles import ProfileLibrary, locate, user_preset_dirs
from .slicers import Slicer, find_slicers, open_in, test_slicer
from .slicers import show_in_folder as reveal
from .testslice import run as test_slice

OK_COLOUR = "#1a7f37"
FAIL_COLOUR = "#b42318"
MUTED = "#555555"
DONT_OPEN = "Don't open"


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


def _display(profile: dict) -> str:
    return f"{profile['name']}  (yours, {profile['_user']})" if profile.get("_user") else profile["name"]


class App:
    def __init__(self, root: tk.Tk, initial_files: list[Path]):
        self.root = root
        self.libraries: dict[str, ProfileLibrary] = {}
        self.last_output: Path | None = None
        self.busy = False
        self._quiet = False
        self.settings = load_settings()
        self.settings.pop("open_in", None)  # old single choice, now per printer
        self.slicers: dict[str, Slicer] = {s.label: s for s in find_slicers()}
        self.tester = test_slicer(self.slicers)
        root.title("PlateShift")
        root.minsize(680, 460)

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
        self.printer.trace_add("write", lambda *_: self.update_slicer_choices())

        self.check = tk.BooleanVar(value=bool(self.settings.get("test_slice", True)) and bool(self.tester))
        self.checker = ttk.Checkbutton(top, variable=self.check,
                                       text=f"Test slice with {self.tester.label} afterwards" if self.tester
                                       else "Test slice (needs Flash Studio or OrcaSlicer)")
        self.checker.grid(row=2, column=1, sticky="w", padx=8, pady=(6, 0))
        if not self.tester:
            self.checker.configure(state="disabled")

        self.open_choice.trace_add("write", lambda *_: self.remember(slicer=True))
        for var in (self.printer, self.check):
            var.trace_add("write", lambda *_: self.remember())

        self.hint = ttk.Label(root, padding=(12, 2), foreground=MUTED,
                              text="Choose files, or drop .3mf files on this window. Converted copies are "
                                   "saved next to the originals; open them with File → Open Project.")
        self.hint.pack(fill="x")

        self.plan_frame = ttk.Frame(root, padding=(12, 6, 12, 0))

        status = ttk.Frame(root, padding=(12, 4, 12, 12))
        status.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(status, mode="determinate", length=160)
        self.progress.pack(side="left")
        self.status = tk.Label(status, text="Ready. Choose files to convert.", anchor="w", fg=MUTED)
        self.status.pack(side="left", fill="x", expand=True, padx=10)
        self.show = ttk.Button(status, text="Show in folder", command=self.show_in_folder)
        self.open_button = ttk.Button(status, text="Open", command=self.open_last)

        self.log_frame = ttk.Frame(root, padding=(12, 12, 12, 4))
        self.log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(self.log_frame, wrap="word", height=12, state="disabled", font=("Consolas", 9))
        self.log.tag_configure("ok", foreground=OK_COLOUR)
        self.log.tag_configure("fail", foreground=FAIL_COLOUR)
        self.log.tag_configure("done", foreground=OK_COLOUR, font=("Segoe UI", 10, "bold"))
        scroll = ttk.Scrollbar(self.log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        if not dnd.enable(root, self.handle_files):
            self.hint.configure(text="Choose files to convert. Converted copies are saved next to the "
                                     "originals; open them with File → Open Project.")
        if initial_files:
            root.after(100, lambda: self.handle_files(initial_files))

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

    def remember(self, slicer: bool = False):
        if self._quiet:
            return
        if slicer:
            by_printer = self.settings.setdefault("open_in_by_printer", {})
            by_printer[self.target().key] = self.open_choice.get()
        self.settings.update(printer=self.printer.get(), test_slice=self.check.get())
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
        self.root.after(0, append)

    def set_status(self, text: str, colour: str = MUTED):
        self.root.after(0, lambda: self.status.configure(text=text, fg=colour, font=("Segoe UI", 9)))

    def set_busy(self, busy: bool):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.button.configure(state=state)
        self.picker.configure(state="disabled" if busy else "readonly")
        self.opener.configure(state="disabled" if busy else "readonly")
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
            try:
                located, lib = self.library(target)
                plan = analyse(source, located, lib)
            except (ConversionError, FileNotFoundError, OSError) as exc:
                self.write(f"✗ {source.name}: not converted", "fail")
                self.write(f"  {exc}\n")
                self.root.after(0, lambda: self.after_prepare(None, None, None))
                return
            self.root.after(0, lambda: self.after_prepare(source, located, plan))

        threading.Thread(target=work, daemon=True).start()

    def after_prepare(self, source, target, plan):
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.set_busy(False)
        if plan is None:
            self.status.configure(text="✗ Nothing was converted (see below)", fg=FAIL_COLOUR,
                                  font=("Segoe UI", 10, "bold"))
            return
        self.show_plan(source, target, plan)

    def show_plan(self, source: Path, target, plan):
        self.plan = plan
        self.plan_source = source
        self.plan_target = target
        for child in self.plan_frame.winfo_children():
            child.destroy()
        self.plan_frame.pack(fill="x", before=self.log_frame)

        word = "Toolhead" if target.toolheads > 1 else "Filament"
        heads = [str(i) for i in range(1, target.toolheads + 1)]
        box = ttk.LabelFrame(self.plan_frame, text=f" {source.name} ", padding=10)
        box.pack(fill="x")

        left = ttk.Frame(box)
        left.pack(side="left", fill="y", anchor="n")
        ttk.Label(left, text="Project colours", font=("Segoe UI", 9, "bold")).grid(
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
        self.status.configure(text="Check the toolheads and filaments, then Convert.", fg=MUTED,
                              font=("Segoe UI", 9))

    def current_merge(self) -> list[int]:
        return [int(v.get()) for v in self.merge_vars]

    def refresh_heads(self):
        plan, merge = self.plan, self.current_merge()
        word = "Toolhead" if self.plan_target.toolheads > 1 else "Filament"
        for child in self.heads_frame.winfo_children():
            child.destroy()
        ttk.Label(self.heads_frame, text=f"{word} presets", font=("Segoe UI", 9, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        ttk.Label(self.heads_frame, text="click a colour to change it", foreground=MUTED).grid(
            row=0, column=2, sticky="e")
        by_name = {v: k for k, v in self.preset_names.items()}
        for row, t in enumerate(sorted(set(merge)), start=1):
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
            if t not in self.head_user_set:
                self._quiet = True
                var.set(by_name.get(suggest_preset(plan, merge, t), ""))
                self._quiet = False
            ttk.Combobox(self.heads_frame, textvariable=var, values=list(self.preset_names),
                         width=48, state="readonly").grid(row=row, column=2, sticky="we", padx=(8, 0), pady=1)

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
        word = "toolhead" if self.plan_target.toolheads > 1 else "filament"
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

    def close_plan(self):
        for child in self.plan_frame.winfo_children():
            child.destroy()
        self.plan_frame.pack_forget()
        self.plan = None

    def convert_plan(self):
        plan, source = self.plan, self.plan_source
        merge = self.current_merge()
        overrides = {}
        for t in sorted(set(merge)):
            chosen = self.preset_names.get(self.head_vars[t].get())
            if chosen and t in self.head_user_set:
                overrides[t] = chosen
            elif chosen:
                plan.presets[t] = chosen
        plan.notes.clear()
        colours = {t: c for t, c in self.head_colours.items() if t in set(merge)}
        self.close_plan()
        self.run([source], plan=plan, merge=merge, overrides=overrides, colours=colours)

    # --- converting -------------------------------------------------------

    def run(self, files: list[Path], plan=None, merge=None, overrides=None, colours=None):
        target = self.target()
        testing = self.check.get() and self.tester is not None
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
                    output = default_output(source, target)
                    try:
                        report = convert(source, output, target, lib, overrides or {},
                                         merge=merge, plan=plan if len(files) == 1 else None,
                                         colours=colours)
                    except (ConversionError, KeyError, OSError) as exc:
                        failed.append(source)
                        self.write(f"✗ {source.name}: not converted", "fail")
                        self.write(f"  {exc}\n")
                        step += 2 if testing else 1
                        self.root.after(0, lambda n=step: self.progress.configure(value=n))
                        continue
                    converted.append(report.output)
                    lines = report.lines()
                    self.write(f"✓ {lines[0]}", "ok")
                    self.write("\n".join(lines[1:]))
                    step += 1
                    self.root.after(0, lambda n=step: self.progress.configure(value=n))
                    if testing:
                        self.set_status(f"Test slicing {output.name} in {self.tester.label}…")
                        result = test_slice(self.tester, output)
                        self.write(f"  {'✓' if result.ok else '✗'} {result.line()}", "ok" if result.ok else "fail")
                        if not result.ok:
                            slice_failed.append(output)
                        step += 1
                        self.root.after(0, lambda n=step: self.progress.configure(value=n))
                    self.write("")
            except FileNotFoundError as exc:
                failed.extend(files[len(converted) + len(failed):])
                self.write(f"✗ {exc}\n", "fail")
            finally:
                self.root.after(0, lambda: self.finish(converted, failed, slice_failed))

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
        self.status.configure(text=text, fg=colour, font=("Segoe UI", 10, "bold"))
        self.write(text.lstrip("✓✗ ").join(("── ", " ──")) + "\n", "done" if ok else "fail")
        self.root.bell()


def main(files: list[str] | None = None) -> int:
    root = tk.Tk()
    App(root, [Path(f) for f in files or [] if f.lower().endswith(".3mf")])
    root.mainloop()
    return 0
