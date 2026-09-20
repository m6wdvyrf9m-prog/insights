from pathlib import Path
import sys
from io import BytesIO
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from insights_app import create_app

app = create_app()


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._serve()

    def do_POST(self):
        self._serve()

    def do_PUT(self):
        self._serve()

    def do_PATCH(self):
        self._serve()

    def do_DELETE(self):
        self._serve()

    def _serve(self):
        parsed = urlsplit(self.path)
        app_path = parsed.path
        if app_path in {"/api", "/api/", "/api/index", "/api/index.py"}:
            app_path = "/"
        elif app_path.startswith("/api/index/"):
            app_path = app_path.removeprefix("/api/index")
        elif app_path.startswith("/api/index.py/"):
            app_path = app_path.removeprefix("/api/index.py")

        body = b""
        length = int(self.headers.get("content-length") or 0)
        if length:
            body = self.rfile.read(length)

        environ = {
            "REQUEST_METHOD": self.command,
            "SCRIPT_NAME": "",
            "PATH_INFO": app_path or "/",
            "QUERY_STRING": parsed.query,
            "SERVER_NAME": self.headers.get("host", "localhost").split(":", 1)[0],
            "SERVER_PORT": "443" if self.headers.get("x-forwarded-proto") == "https" else "80",
            "SERVER_PROTOCOL": self.protocol_version,
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": self.headers.get("x-forwarded-proto", "https"),
            "wsgi.input": BytesIO(body),
            "wsgi.errors": sys.stderr,
            "wsgi.multithread": False,
            "wsgi.multiprocess": True,
            "wsgi.run_once": False,
            "CONTENT_LENGTH": str(len(body)) if body else "",
            "CONTENT_TYPE": self.headers.get("content-type", ""),
            "REMOTE_ADDR": self.headers.get("x-forwarded-for", "").split(",", 1)[0].strip(),
        }
        for name, value in self.headers.items():
            key = "HTTP_" + name.upper().replace("-", "_")
            if key not in {"HTTP_CONTENT_TYPE", "HTTP_CONTENT_LENGTH"}:
                environ[key] = value

        status_headers = {"status": "500 Internal Server Error", "headers": []}

        def start_response(status, headers, exc_info=None):
            status_headers["status"] = status
            status_headers["headers"] = headers

        response_body = b"".join(app(environ, start_response))
        status_code = int(status_headers["status"].split(" ", 1)[0])
        self.send_response(status_code)
        for key, value in status_headers["headers"]:
            if key.lower() not in {"connection", "transfer-encoding"}:
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(response_body)
