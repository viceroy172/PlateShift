"""Slice a converted project with Flash Studio's command line to prove it works."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SliceResult:
    ok: bool
    plates: int = 0
    seconds: int = 0
    grams: float = 0.0
    error: str = ""

    def line(self) -> str:
        if not self.ok:
            return f"Test slice failed: {self.error}"
        h, m = divmod(self.seconds // 60, 60)
        time = f"{h} h {m} min" if h else f"{m} min"
        plates = f", {self.plates} plates" if self.plates > 1 else ""
        return f"Test slice OK: {time}, {self.grams:.0f} g{plates}"


_TIME = re.compile(r"^; estimated printing time \(normal mode\) = (.+)$")
_GRAMS = re.compile(r"^; total filament used \[g\] = ([\d.]+)")


def _seconds(text: str) -> int:
    total = 0
    for value, unit in re.findall(r"(\d+)([dhms])", text):
        total += int(value) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total


def run(slicer, project: Path, timeout: int = 900) -> SliceResult:
    """slicer: a slicers.Slicer, or the path of a slicer's program"""
    command = slicer.command if hasattr(slicer, "command") else (lambda *a: [str(slicer), *a])
    # flatpak apps can't see the system /tmp, so on linux work under ~/.cache
    base = None
    if sys.platform != "win32":
        base = Path.home() / ".cache" / "plateshift"
        base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="plateshift-", dir=base) as out:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            # cwd: the slicer drops a log file wherever it's started
            proc = subprocess.run(command("--slice", "0", "--outputdir", out, str(Path(project).resolve())),
                                  capture_output=True, text=True, errors="replace",
                                  timeout=timeout, creationflags=flags, cwd=out)
        except subprocess.TimeoutExpired:
            return SliceResult(False, error=f"gave up after {timeout // 60} minutes")
        except OSError as exc:
            return SliceResult(False, error=str(exc))
        files = sorted(Path(out).glob("*.gcode"))
        if proc.returncode != 0 or not files:
            return SliceResult(False, error=_why(proc.stdout + proc.stderr))
        result = SliceResult(True, plates=len(files))
        for f in files:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if (m := _TIME.match(line)):
                        result.seconds += _seconds(m.group(1))
                    elif (m := _GRAMS.match(line)):
                        result.grams += float(m.group(1))
        return result


def _why(log: str) -> str:
    lines = [l.strip() for l in log.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        if "error" in line.lower() and "opengl" not in line.lower() and "glfw" not in line.lower() \
                and "glad" not in line.lower() and "Placeable" not in line:
            text = re.sub(r"^\[[^\]]*\]\s*\[[^\]]*\]\s*\[\w+\]\s*", "", line)
            if text.endswith(":") and i + 1 < len(lines):
                text += " " + lines[i + 1]
            return text[:300]
    return lines[-1][:300] if lines else "no output"
