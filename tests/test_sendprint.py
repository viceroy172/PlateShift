from __future__ import annotations

import base64
import json
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plateshift import sendprint  # noqa: E402


class FakePrinter:
    """answers http requests (and a websocket) on a local port, recording them"""

    def __init__(self, reply_for):
        self.reply_for = reply_for
        self.requests: list[tuple[str, dict, bytes]] = []
        self.ws_messages: list[str] = []
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen()
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn):
        with conn:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                data += chunk
            head, _, body = data.partition(b"\r\n\r\n")
            lines = head.decode().split("\r\n")
            headers = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in lines[1:])}
            if headers.get("upgrade", "").lower() == "websocket":
                conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
                frame = conn.recv(65536)
                n = frame[1] & 0x7F
                offset = 2 if n < 126 else 4
                mask, payload = frame[offset:offset + 4], frame[offset + 4:]
                self.ws_messages.append(bytes(b ^ mask[i % 4] for i, b in enumerate(payload)).decode())
                return
            length = int(headers.get("content-length", 0))
            while len(body) < length:
                body += conn.recv(65536)
            method, path, _ = lines[0].split(" ", 2)
            self.requests.append((f"{method} {path}", headers, body))
            reply = json.dumps(self.reply_for(method, path, headers)).encode()
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                         + str(len(reply)).encode() + b"\r\n\r\n" + reply)

    def close(self):
        self.server.close()


class SendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.gcode = self.tmp / "Robot - C5P.gcode"
        self.gcode.write_text("G28\n; filament_colour = #FF0000;#00FF00\n; filament_type = PLA;PETG\n")

    def test_flashforge_upload_with_slots(self):
        def reply(method, path, headers):
            if path == "/detail":
                return {"code": 0, "detail": {"matlStationInfo": {"slotInfos": [
                    {"slotId": 1, "hasFilament": True, "materialName": "PLA", "materialColor": "#FF0000"},
                    {"slotId": 2, "hasFilament": True, "materialName": "PLA", "materialColor": "#FFFFFF"}]}}}
            return {"code": 0, "message": "Success"}
        printer = FakePrinter(reply)
        self.addCleanup(printer.close)
        sendprint.FLASHFORGE_PORT, saved = printer.port, sendprint.FLASHFORGE_PORT
        self.addCleanup(setattr, sendprint, "FLASHFORGE_PORT", saved)
        conn = sendprint.Connection("flashforge", "http://127.0.0.1/", "SN123", "abcd1234")
        colours, materials = sendprint.gcode_filaments(self.gcode)
        self.assertEqual((colours, materials), (["#FF0000", "#00FF00"], ["PLA", "PETG"]))
        sent = sendprint.send(conn, self.gcode, start=False, tool_colours=colours, tool_materials=materials)
        self.assertFalse(sent.started)
        self.assertEqual(sent.notes, ["slot 2 has PLA, the print wants PETG"])
        detail, upload = printer.requests
        self.assertEqual(detail[0], "POST /detail")
        self.assertEqual(json.loads(detail[2]), {"serialNumber": "SN123", "checkCode": "abcd1234"})
        self.assertEqual(upload[0], "POST /uploadGcode")
        headers = upload[1]
        self.assertEqual((headers["serialnumber"], headers["checkcode"], headers["printnow"]),
                         ("SN123", "abcd1234", "false"))
        self.assertEqual(headers["gcodetoolcnt"], "2")
        mappings = json.loads(base64.b64decode(headers["materialmappings"]))
        self.assertEqual(mappings[0], {"toolId": 0, "slotId": 1, "materialName": "PLA",
                                       "toolMaterialColor": "#FF0000", "slotMaterialColor": "#FF0000"})
        self.assertIn(b'filename="Robot - C5P.gcode"', upload[2])
        self.assertIn(b"G28", upload[2])

    def test_flashforge_refusal_is_explained(self):
        printer = FakePrinter(lambda m, p, h: {"code": 1, "message": "Parameters is error"})
        self.addCleanup(printer.close)
        sendprint.FLASHFORGE_PORT, saved = printer.port, sendprint.FLASHFORGE_PORT
        self.addCleanup(setattr, sendprint, "FLASHFORGE_PORT", saved)
        conn = sendprint.Connection("flashforge", "127.0.0.1", "SN", "bad")
        with self.assertRaisesRegex(sendprint.SendError, "Check the serial number and check code"):
            sendprint.send(conn, self.gcode, start=True, tool_colours=["#FF0000"])
        with self.assertRaisesRegex(sendprint.SendError, "serial number and check code"):
            sendprint.send(sendprint.Connection("flashforge", "127.0.0.1"), self.gcode, start=False)

    def test_creality_upload_and_start(self):
        printer = FakePrinter(lambda m, p, h: {"model": "K1 Max"} if p == "/info" else {"code": 200})
        self.addCleanup(printer.close)
        real = sendprint._request

        def to_fake(host, port, *args, **kw):
            return real(host, printer.port, *args, **kw)
        sendprint._request = to_fake
        self.addCleanup(setattr, sendprint, "_request", real)
        sendprint.CREALITY_WS_PORT, saved = printer.port, sendprint.CREALITY_WS_PORT
        self.addCleanup(setattr, sendprint, "CREALITY_WS_PORT", saved)
        sent = sendprint.send(sendprint.Connection("creality", "127.0.0.1"), self.gcode, start=True)
        self.assertTrue(sent.started)
        self.assertEqual(sent.file, "Robot_-_C5P.gcode")
        paths = [r[0] for r in printer.requests]
        self.assertEqual(paths, ["GET /info", "POST /upload/Robot_-_C5P.gcode"])
        self.assertEqual(json.loads(printer.ws_messages[0]), {
            "method": "set", "params": {"opGcodeFile": "printprt:/usr/data/printer_data/gcodes/Robot_-_C5P.gcode"}})

    def test_unreachable_printer(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]   # nothing listens here once closed
        sendprint.FLASHFORGE_PORT, saved = port, sendprint.FLASHFORGE_PORT
        self.addCleanup(setattr, sendprint, "FLASHFORGE_PORT", saved)
        with self.assertRaisesRegex(sendprint.SendError, "Couldn't reach the printer"):
            sendprint.flashforge_detail(sendprint.Connection("flashforge", "127.0.0.1", "a", "b"))

    def test_flash_studio_printers(self):
        folder = self.tmp / "Orca-Flashforge"
        folder.mkdir()
        (folder / "Orca-Flashforge.conf").write_text(json.dumps({
            "header": "Flash Studio 1.7.23",
            "local_machines": {"SN1": {"dev_ip": "", "dev_name": "Creator 5 Pro"},
                               "SN2": {"dev_ip": "10.0.0.9", "dev_name": "AD5X"}},
            "user_access_code": {"SN1": "abcd1234", "SN2": "wxyz9876"}}, indent=1)
            + "\n# MD5 checksum 7BAFF0761D45DA218836AE5186734A76\n")
        printers = sendprint.flash_studio_printers([self.tmp])
        self.assertEqual(len(printers), 2)
        c5p = sendprint.flash_studio_match("Flashforge Creator 5 Pro", printers)
        self.assertEqual((c5p["serial"], c5p["check_code"], c5p["host"]), ("SN1", "abcd1234", ""))
        self.assertEqual(sendprint.flash_studio_match("Flashforge AD5X", printers)["host"], "10.0.0.9")
        self.assertIsNone(sendprint.flash_studio_match("Flashforge Adventurer 5M", printers))
        self.assertEqual(sendprint.flash_studio_printers([self.tmp / "none"]), [])

    def test_names_and_brands(self):
        self.assertEqual(sendprint.safe_name("a<b>:c?.gcode"), "a_b__c_.gcode")
        self.assertEqual(sendprint.kind_for("Flashforge"), "flashforge")
        self.assertEqual(sendprint.kind_for("Creality"), "creality")
        self.assertIsNone(sendprint.kind_for("Prusa"))


if __name__ == "__main__":
    unittest.main()
