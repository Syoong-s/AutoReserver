"""Loopback-only mock reservation server; never contacts the real site."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import time


# ==========================================
# Function: Serve the local UI and mock order responses.
# Method: Keep every request local, with optional failure scenarios in the payload.
# ==========================================
class Handler(BaseHTTPRequestHandler):
    # ==========================================
    # Function: Serve the fixture page.
    # Method: Return only the bundled HTML for all non-API GET paths.
    # ==========================================
    def do_GET(self):
        data = (Path(__file__).parent / "index.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ==========================================
    # Function: Respond to mock calendar reads and orders without any purchase.
    # Method: Delay the mock calendar chain or return configurable order outcomes.
    # ==========================================
    def do_POST(self):
        if self.path == "/manage/fieldDetail/queryFieldReserveSituationIsFull":
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            time.sleep(min(2, max(0, float(payload.get("delay", 0)))))
            body = b'{"code":0,"data":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/manage/fieldDetail/queryFieldSituation":
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = b'{"code":0,"data":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/venue/personal/ConfirmOrder":
            self.send_error(403, "Payments are disabled in the mock server")
            return
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        scenario = payload.get("scenario", "success")
        if scenario == "captcha" and not payload.get("verified"):
            result = {"code": 1002, "msg": "需要安全验证"}
        elif scenario == "rejected":
            result = {"code": 500, "msg": "该场地已被预约"}
        elif scenario == "missing-id":
            result = {"code": 0, "data": None}
        else:
            result = {"code": 0, "data": "LOCAL-DEMO-ORDER", "msg": "成功"}
        data = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ==========================================
    # Function: Keep test output focused on assertions.
    # Method: Silence routine HTTP access logging.
    # ==========================================
    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    print("本地模拟页：http://127.0.0.1:8765；按 Ctrl+C 停止")
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
