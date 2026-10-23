"""Loopback-only HTTP service. The browser and SQLite workflow need no third-party packages."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from .storage import ConflictError

LOG = logging.getLogger(__name__)
WEB = Path(__file__).parent / "web"
SESSION_PATH = re.compile(r"^/api/sessions/([0-9a-f]{32})(/confirm|/export.csv)?$")


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store, live):
        if address[0] != "127.0.0.1":
            raise ValueError("本地终端服务仅监听127.0.0.1")
        self.store, self.live = store, live
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "ClassroomAttendance/0.1"

    def log_message(self, fmt, *args):
        LOG.info("%s %s", self.client_address[0], fmt % args)

    def send(self, status, body, content_type="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def check_host(self):
        host = self.headers.get("Host", "")
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if host not in allowed:
            self.send(403, {"error": "主机名不匹配"})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + host:
            self.send(403, {"error": "不接受跨站请求"})
            return False
        return True

    def do_GET(self):
        if not self.check_host():
            return
        try:
            target = urlsplit(self.path)
            path = target.path
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in assets:
                name, content_type = assets[path]
                return self.send(200, (WEB / name).read_bytes(), content_type)
            if path == "/api/live":
                return self.send(200, self.server.live.snapshot())
            if path == "/api/frame.jpg":
                frame = self.server.live.frame()
                return self.send(200, frame, "image/jpeg") if frame is not None else self.send(404, {"error": "当前没有有效画面"})
            if path == "/api/sessions":
                query = parse_qs(target.query).get("q", [""])[0]
                return self.send(200, self.server.store.list(query))
            match = SESSION_PATH.fullmatch(path)
            if match and match[2] is None:
                return self.send(200, self.server.store.get(match[1]))
            if match and match[2] == "/export.csv":
                return self.send(200, self.server.store.export(match[1]), "text/csv; charset=utf-8",
                                 {"Content-Disposition": f'attachment; filename="attendance-{match[1]}.csv"'})
            self.send(404, {"error": "接口不存在"})
        except KeyError as exc:
            self.send(404, {"error": str(exc).strip("'")})
        except ValueError as exc:
            self.send(400, {"error": str(exc)})
        except Exception:
            LOG.exception("读取请求失败")
            self.send(500, {"error": "读取失败，请查看服务日志"})

    def do_POST(self):
        if not self.check_host():
            return
        try:
            if self.headers.get_content_type() != "application/json":
                return self.send(415, {"error": "需要application/json"})
            if self.headers.get("Transfer-Encoding"):
                return self.send(400, {"error": "不支持分块请求体"})
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 256_000:
                return self.send(413, {"error": "请求大小必须在1到256000字节之间"})
            payload = json.loads(self.rfile.read(length), parse_constant=lambda v: (_ for _ in ()).throw(ValueError("JSON不允许NaN或Infinity")))
            path = urlsplit(self.path).path
            if path == "/api/sessions":
                return self.send(201, self.server.store.create(payload))
            match = SESSION_PATH.fullmatch(path)
            if match and match[2] == "/confirm":
                return self.send(200, self.server.store.confirm(match[1], payload, self.server.live.snapshot()))
            self.send(404, {"error": "接口不存在"})
        except ConflictError as exc:
            self.send(409, {"error": str(exc)})
        except KeyError as exc:
            self.send(404, {"error": str(exc).strip("'")})
        except (ValueError, UnicodeError) as exc:
            self.send(400, {"error": str(exc)})
        except Exception:
            LOG.exception("写入请求失败")
            self.send(500, {"error": "保存失败，请查看服务日志"})
