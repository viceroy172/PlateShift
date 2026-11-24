# SPDX-License-Identifier: AGPL-3.0-or-later
"""Send sliced G-code to a printer on your network, and optionally start it.

Flashforge (Creator 5, AD5X, Adventurer 5M): the printer's local HTTP API on port
8898, with its serial number and check code. Creality on stock firmware (K1, K2,
SparkX…): an HTTP upload, then a start command over its websocket on port 9999.
Both follow what OrcaSlicer does (src/slic3r/Utils/Flashforge.cpp, CrealityPrint.cpp).
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import struct
import uuid
from dataclasses import dataclass, field
from pathlib import Path

TIMEOUT = 10          # seconds to wait for a reply
UPLOAD_TIMEOUT = 300  # big files on slow wifi


class SendError(Exception):
    """a problem talking to the printer, said plainly"""


@dataclass
class Connection:
    kind: str              # "flashforge" or "creality"
    host: str
    serial: str = ""       # flashforge only
    check_code: str = ""   # flashforge only


def kind_for(vendor: str) -> str | None:
    """which protocol a printer brand speaks, or None if sending isn't supported"""
    return {"Flashforge": "flashforge", "Creality": "creality"}.get(vendor)


def _host(host: str) -> str:
    host = host.strip()
    host = re.sub(r"^[a-z]+://", "", host, flags=re.IGNORECASE)
    return host.split("/")[0].split(":")[0]


# --- plain http, without extra libraries ------------------------------------

def _request(host: str, port: int, method: str, path: str, headers: dict[str, str], body: bytes,
             timeout: float) -> tuple[int, bytes]:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            lines = [f"{method} {path} HTTP/1.1", f"Host: {host}:{port}", "Connection: close",
                     f"Content-Length: {len(body)}", *(f"{k}: {v}" for k, v in headers.items()), "", ""]
            sock.sendall("\r\n".join(lines).encode("utf-8") + body)
            reply = b""
            while chunk := sock.recv(65536):
                reply += chunk
    except socket.timeout:
        raise SendError(f"The printer at {host} didn't answer in time. Is it switched on?") from None
    except OSError as exc:
        raise SendError(f"Couldn't reach the printer at {host} ({exc.strerror or exc}). "
                        "Check its address and that it's on the same network.") from None
    head, _, data = reply.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].split()
    code = int(status[1]) if len(status) > 1 and status[1].isdigit() else 0
    if b"transfer-encoding: chunked" in head.lower():
        data = _unchunk(data)
    return code, data


def _unchunk(data: bytes) -> bytes:
    out = b""
    while data:
        size_line, _, rest = data.partition(b"\r\n")
        try:
            size = int(size_line.split(b";")[0], 16)
        except ValueError:
            break
        if size == 0:
            break
        out += rest[:size]
        data = rest[size + 2:]
    return out


def _multipart(field_name: str, filename: str, content: bytes, fields: dict[str, str] | None = None):
    boundary = f"----PlateShift{uuid.uuid4().hex}"
    parts = []
    for name, value in (fields or {}).items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                 "Content-Type: application/octet-stream\r\n\r\n".encode("utf-8") + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# --- flashforge --------------------------------------------------------------

FLASHFORGE_PORT = 8898


def safe_name(name: str, local_api: bool = True) -> str:
    """the name the printer stores: no <>[]:/\\|?*\" or control characters"""
    base = Path(name).name or "print.gcode"
    return "".join(c if (c.isalnum() and c.isascii()) or c in "._-" or
                   (local_api and ord(c) >= 0x20 and c not in '<>[]:/\\|?*"') else "_" for c in base)


def _flashforge_reply(code: int, data: bytes) -> dict:
    try:
        reply = json.loads(data.decode("utf-8", "replace") or "{}")
    except ValueError:
        raise SendError(f"The printer sent a reply PlateShift can't read (HTTP {code}).") from None
    if not isinstance(reply, dict):
        raise SendError("The printer sent a reply PlateShift can't read.")
    result = reply.get("code", reply.get("err", 0))
    try:
        result = int(result)
    except (TypeError, ValueError):
        result = 0
    if result != 0:
        message = reply.get("message") or reply.get("msg") or f"error {result}"
        if "param" in str(message).lower():
            message = f"{message}. Check the serial number and check code"
        raise SendError(f"The printer said: {message}")
    return reply


def flashforge_detail(conn: Connection) -> dict:
    """the printer's status, including what's loaded in each slot"""
    body = json.dumps({"serialNumber": conn.serial, "checkCode": conn.check_code}).encode()
    code, data = _request(_host(conn.host), FLASHFORGE_PORT, "POST", "/detail",
                          {"Content-Type": "application/json"}, body, TIMEOUT)
    reply = _flashforge_reply(code, data)
    return reply.get("detail", reply)


@dataclass
class Slot:
    slot: int
    material: str = ""
    colour: str = ""
    loaded: bool = True


def flashforge_slots(detail: dict) -> list[Slot]:
    """the slots (or toolheads) the printer reports, read leniently as orca does"""
    station = detail.get("matlStationInfo") or detail.get("MatlStationInfo") or {}
    infos = station.get("slotInfos") or station.get("SlotInfos") or []
    slots = []
    for i, info in enumerate(infos, start=1):
        if not isinstance(info, dict):
            continue
        try:
            number = int(info.get("slotId", i))
        except (TypeError, ValueError):
            number = i
        loaded = str(info.get("hasFilament", 1)) not in ("0", "False", "false")
        slots.append(Slot(number, str(info.get("materialName") or ""), str(info.get("materialColor") or ""), loaded))
    return slots


@dataclass
class Mapping:
    tool: int          # the g-code's filament, from 0
    slot: int          # the printer's slot, from 1
    material: str
    tool_colour: str
    slot_colour: str


def flashforge_upload(conn: Connection, gcode: Path, start: bool, mappings: list[Mapping] = (),
                      leveling: bool = False, progress=None) -> None:
    gcode = Path(gcode)
    content = gcode.read_bytes()
    listed = [{"toolId": m.tool, "slotId": m.slot, "materialName": m.material,
               "toolMaterialColor": m.tool_colour, "slotMaterialColor": m.slot_colour} for m in mappings]
    body, content_type = _multipart("gcodeFile", safe_name(gcode.name), content)
    headers = {
        "Content-Type": content_type,
        "serialNumber": conn.serial, "checkCode": conn.check_code, "fileSize": str(len(content)),
        "printNow": "true" if start else "false", "levelingBeforePrint": "true" if leveling else "false",
        "flowCalibration": "false", "firstLayerInspection": "false", "timeLapseVideo": "false",
        "useMatlStation": "true" if listed else "false", "gcodeToolCnt": str(len(listed)),
        "materialMappings": base64.b64encode(json.dumps(listed).encode()).decode(),
    }
    code, data = _request(_host(conn.host), FLASHFORGE_PORT, "POST", "/uploadGcode", headers, body, UPLOAD_TIMEOUT)
    _flashforge_reply(code, data)


# --- creality (stock firmware) -----------------------------------------------

CREALITY_WS_PORT = 9999
# where stock k1-family firmware keeps g-code; others use /mnt/UDISK
_USR_DATA_MODELS = {"K1", "K1 Max", "K1C", "K1 SE", "K1_CFS-C", "K1 Max_CFS-C", "K1C_CFS-C", "K1 SE_CFS-C",
                    "F001", "F002", "F003", "F004", "F005", "F016"}


def creality_info(conn: Connection) -> dict:
    code, data = _request(_host(conn.host), 80, "GET", "/info", {}, b"", TIMEOUT)
    try:
        return json.loads(data.decode("utf-8", "replace") or "{}")
    except ValueError:
        return {}


def creality_upload(conn: Connection, gcode: Path) -> str:
    """upload, and return the name the printer stores it under"""
    gcode = Path(gcode)
    name = safe_name(gcode.name, local_api=False)
    body, content_type = _multipart("file", name, gcode.read_bytes())
    code, data = _request(_host(conn.host), 80, "POST", f"/upload/{_url_quote(name)}",
                          {"Content-Type": content_type}, body, UPLOAD_TIMEOUT)
    if code >= 300 or code == 0:
        raise SendError(f"The printer refused the file (HTTP {code}).")
    return name


def _url_quote(name: str) -> str:
    from urllib.parse import quote
    return quote(name)


def creality_start(conn: Connection, name: str, model: str = "") -> None:
    folder = "/usr/data" if model in _USR_DATA_MODELS or not model else "/mnt/UDISK"
    path = f"{folder}/printer_data/gcodes/{name}"
    ws = _WebSocket(_host(conn.host), CREALITY_WS_PORT)
    try:
        ws.send(json.dumps({"method": "set", "params": {"opGcodeFile": f"printprt:{path}"}}))
        ws.read_quietly()   # k1 firmware closes right after accepting; a read keeps the close apart
    finally:
        ws.close()


class _WebSocket:
    """just enough of a websocket client to send a command (RFC 6455, text frames)"""

    def __init__(self, host: str, port: int):
        try:
            self.sock = socket.create_connection((host, port), timeout=TIMEOUT)
        except OSError as exc:
            raise SendError(f"Couldn't reach the printer at {host} to start the print ({exc.strerror or exc}).") \
                from None
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        reply = b""
        try:
            while b"\r\n\r\n" not in reply:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                reply += chunk
        except OSError:
            pass
        if b" 101 " not in reply.split(b"\r\n", 1)[0]:
            self.sock.close()
            raise SendError("The printer didn't accept the start command connection.")

    def send(self, text: str) -> None:
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else
                                bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else
                                bytes([0x80 | 127]) + struct.pack(">Q", n))
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(head + mask + masked)

    def read_quietly(self) -> bytes:
        try:
            self.sock.settimeout(3)
            return self.sock.recv(65536)
        except OSError:
            return b""

    def close(self) -> None:
        try:
            self.sock.sendall(bytes([0x88, 0x80]) + os.urandom(4))
        except OSError:
            pass
        self.sock.close()


# --- the whole job -----------------------------------------------------------

@dataclass
class Sent:
    printer: str
    file: str
    started: bool
    notes: list[str] = field(default_factory=list)


def send(conn: Connection, gcode: Path, start: bool, tool_colours: list[str] | None = None,
         tool_materials: list[str] | None = None) -> Sent:
    """upload gcode and start it if asked. tool_colours/tool_materials: the g-code's
    filaments, for the flashforge slot mapping (each tool to the same-numbered slot)"""
    if conn.kind == "flashforge":
        if not conn.serial or not conn.check_code:
            raise SendError("Flashforge printers need their serial number and check code, from the printer's "
                            "screen (Settings → Network, LAN mode).")
        notes, mappings = [], []
        tools = tool_colours or []
        used = gcode_used(gcode)
        if len(tools) > 1 or start:
            slots = {s.slot: s for s in flashforge_slots(flashforge_detail(conn))}
            for tool, colour in enumerate(tools):
                if used and tool < len(used) and not used[tool]:
                    continue   # a filament the print never uses needs no slot
                slot = slots.get(tool + 1)
                if slot is None:
                    continue
                mappings.append(Mapping(tool, slot.slot, slot.material, colour, slot.colour))
                wanted = (tool_materials or [""] * len(tools))[tool] if tool_materials else ""
                if not slot.loaded:
                    notes.append(f"slot {slot.slot} has no filament loaded")
                elif wanted and slot.material and wanted.upper() != slot.material.upper():
                    notes.append(f"slot {slot.slot} has {slot.material}, the print wants {wanted}")
        flashforge_upload(conn, gcode, start, mappings)
        return Sent(_host(conn.host), safe_name(Path(gcode).name), start, notes)
    if conn.kind == "creality":
        info = creality_info(conn) if start else {}
        name = creality_upload(conn, gcode)
        if start:
            creality_start(conn, name, str(info.get("model", "")))
        return Sent(_host(conn.host), name, start)
    raise SendError("PlateShift can't send to this printer yet.")


def gcode_used(gcode: Path) -> list[bool]:
    """which of the g-code's filaments it actually prints with (from '; filament used [g]')"""
    try:
        with open(gcode, "rb") as fh:
            head = fh.read(200_000)              # flash studio puts its summary first
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 400_000))
            text = (head + b"\n" + fh.read()).decode("utf-8", "replace")   # orca at the end
    except OSError:
        return []
    m = re.search(r"^; filament used \[g\] = (.+)$", text, re.MULTILINE)
    if not m:
        return []
    used = []
    for part in m.group(1).split(","):
        try:
            used.append(float(part) > 0)
        except ValueError:
            used.append(True)
    return used


def gcode_filaments(gcode: Path) -> tuple[list[str], list[str]]:
    """(colours, materials) of the g-code's filaments, from the settings the slicer
    writes into it (at the end for orca and flash studio)"""
    colours, materials = [], []
    try:
        with open(gcode, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 400_000))
            tail = fh.read().decode("utf-8", "replace")
            fh.seek(0)
            head = fh.read(200_000).decode("utf-8", "replace")
    except OSError:
        return [], []
    for text in (tail, head):
        if not colours and (m := re.search(r"^; filament_colour = (.+)$", text, re.MULTILINE)):
            colours = [c.strip() for c in m.group(1).split(";") if c.strip()]
        if not materials and (m := re.search(r"^; filament_type = (.+)$", text, re.MULTILINE)):
            materials = [c.strip() for c in m.group(1).split(";") if c.strip()]
    return colours, materials


# --- what flash studio already knows -------------------------------------------

def flash_studio_printers(config_dirs: list[Path] | None = None) -> list[dict]:
    """the flashforge printers flash studio has connected to: serial, check code,
    name and address (often empty), read from its settings file on this computer"""
    from .profiles import _config_dirs
    found = []
    for base in config_dirs if config_dirs is not None else _config_dirs():
        path = Path(base) / "Orca-Flashforge" / "Orca-Flashforge.conf"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            conf, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])  # an md5 line follows the json
        except (OSError, ValueError):
            continue
        machines = conf.get("local_machines") or {}
        codes = conf.get("user_access_code") or {}
        for serial, code in codes.items():
            info = machines.get(serial) if isinstance(machines.get(serial), dict) else {}
            found.append({"serial": serial, "check_code": str(code), "name": str(info.get("dev_name") or ""),
                          "host": str(info.get("dev_ip") or "")})
    return found


def flash_studio_match(label: str, printers: list[dict]) -> dict | None:
    """the saved printer for a target, by name ("Creator 5 Pro" in "Flashforge Creator 5 Pro");
    the only one when there's just one"""
    named = [p for p in printers if p["name"] and p["name"].lower() in label.lower()]
    if len(named) == 1:
        return named[0]
    return printers[0] if len(printers) == 1 else None
