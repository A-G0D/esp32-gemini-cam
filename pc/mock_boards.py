#!/usr/bin/env python3
# mock_boards.py - stand-ins for the S3 cam and C3 HUD, so gemini_relay.py's
# real HTTP client code (http_get_bytes / http_post_text, timeouts, status
# codes) can be exercised over a real loopback socket without any hardware.
#
# S3 mock:  GET /capture  -> serves a JPEG (503 until one is "ready")
#           GET /status   -> tiny JSON status blob, like the real sketch
# C3 mock:  POST /msg     -> records the text it was sent, 200 OK
#
# Run standalone to point config.json at a fake rig:
#   python pc/mock_boards.py
#   -> s3 mock on http://127.0.0.1:8091 (use .../capture as s3_capture_url)
#   -> c3 mock on http://127.0.0.1:8092 (use .../msg as c3_msg_url, GET /last to see what arrived)

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def _solid_jpeg(w=64, h=64, color=(30, 120, 60)):
    """A tiny real JPEG so maybe_resize()/Pillow have something to chew on."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color=color).save(buf, "JPEG")
    return buf.getvalue()


class S3CamHandler(BaseHTTPRequestHandler):
    # class-level state shared by every request this server handles
    ready = True     # flip False to simulate "no sharp frame caught yet" (503)
    jpeg = None

    def do_GET(self):
        if self.path.startswith("/capture"):
            if not self.ready:
                self.send_response(503)
                self.end_headers()
                return
            body = self.jpeg or _solid_jpeg()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/status"):
            body = json.dumps({"scan_len": 4200, "sharp_min": 3000}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass  # quiet - the real board doesn't log to your test output either


class C3HudHandler(BaseHTTPRequestHandler):
    last_text = None   # last body POSTed to /msg, for assertions

    def do_POST(self):
        if self.path.startswith("/msg"):
            length = int(self.headers.get("Content-Length", 0))
            C3HudHandler.last_text = self.rfile.read(length).decode("utf-8")
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path.startswith("/last"):
            body = (C3HudHandler.last_text or "").encode("utf-8")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


def start_server(handler_cls, host="127.0.0.1", port=0):
    """Starts an HTTPServer for handler_cls on a background thread.
    Returns (server, thread). Pass port=0 to grab any free port
    (server.server_address[1] gives you the real one)."""
    server = HTTPServer((host, port), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t


def main():
    s3_srv, _ = start_server(S3CamHandler, port=8091)
    c3_srv, _ = start_server(C3HudHandler, port=8092)
    print("s3 mock:  http://127.0.0.1:%d/capture  (and /status)" % s3_srv.server_address[1])
    print("c3 mock:  http://127.0.0.1:%d/msg       (POST; GET /last to peek)" % c3_srv.server_address[1])
    print("point config.json's s3_capture_url / c3_msg_url at these, then run gemini_relay.py --once")
    print("Ctrl+C to stop")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
