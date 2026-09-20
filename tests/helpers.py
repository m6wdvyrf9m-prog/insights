from __future__ import annotations

import io
import tempfile
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlencode

from reportlab.pdfgen import canvas

from insights_app.config import Config
from insights_app.security import hash_password


def make_config(tmp: tempfile.TemporaryDirectory[str]) -> Config:
    root = Path(tmp.name)
    return Config(
        database_path=root / "insights.sqlite3",
        upload_dir=root / "uploads",
        secret_key="test-secret-key-with-more-than-32-characters",
        admin_password_hash=hash_password("admin-password-123"),
        app_base_url="http://testserver",
        secure_cookies=False,
    )


def make_sample_pdf(path: Path, *, name: str = "Alex Sample", email: str = "alex@example.com") -> Path:
    pdf = canvas.Canvas(str(path))
    pages = [
        [name, "23 January 2026", "Foundation Chapter"],
        [name, "Personal Details", name, email, "Date Completed: 23 January 2026"],
        [name, "Contents", "The Insights Discovery® 72 Type Wheel", "The Insights Discovery® Colour Dynamics"],
        [name, "Key Strengths & Weaknesses", "Strengths", f"{name}'s key strengths:", "- Takes advantage of opportunities.", "- Becomes involved in many activities.", "- Conceptual thinker, sees the big picture.", "- Can make impossible dreams possible.", "- Creative decision maker."],
        [name, "Key Strengths & Weaknesses", "Possible Weaknesses", f"{name}'s possible weaknesses:", "- Some ideas may be perceived as unrealistic.", "- Will set unrealistic deadlines.", "- Can be perceived as manipulative.", "- May miss others' reactions.", "- Makes decisions hastily."],
        [name, "Value to the Team", f"As a team member, {name}:", "- Has a can do attitude.", "- Shows ingenuity and imagination.", "- Brings a fresh outlook.", "- Generates a prolific number of ideas.", "- Believes life should be fun."],
        [name, "Communication", "Effective Communications", f"Strategies for communicating with {name}:", "- Keep returning to the realities.", "- Add to the challenge regularly.", "- Support their goals.", "- Agree exactly what needs to be done.", "- Allow and bolster self esteem."],
        [name, "Communication", "Barriers to Effective Communication", f"When communicating with {name}, DO NOT:", "- Stick rigidly to business issues.", "- Be curt-lipped or abrasive.", "- Isolate them.", "- Approach with foregone conclusions.", "- Prevent them moving on."],
        [name, "Possible Blind Spots", f"{name}'s possible Blind Spots:", "They can appear argumentative when they do not see the logic in others' feelings. They may oversell new ideas and miss quieter reactions. They benefit from slowing down and digesting all available information before acting."],
        [name, "Opposite Type", "Recognising your Opposite Type:", f"{name}'s opposite Insights type is the Coordinator, Jung's Introverted Sensing type. The Coordinator is careful, cautious, conventional, diplomatic and sincere. They prefer structure, facts and predictable ways of working."],
        [name, "Suggestions for Development", f"{name} may benefit from:", "- Being less indiscreet and more formal.", "- Remembering that image is not reality.", "- Looking for inconsistencies in reports.", "- Relating to quiet, thoughtful people.", "- Accepting analysis before intuition."],
        [name, "The Insights Discovery® 72 Type Wheel", "Conscious Wheel Position", "24: Directing Motivator (Classic)", "Less Conscious Wheel Position", "12: Example Type (Classic)"],
        [name, "The Insights Discovery® Colour Dynamics", "Persona (Conscious) Preference Flow Persona (Less Conscious)", "BLUE GREEN YELLOW RED BLUE GREEN YELLOW RED", "6 100 6", "3 0 3", "0.20 2.80 4.60 5.73 0.20 0.40 0.60 0.80", "3% 47% 77% 95% 3% 7% 10% 13%", "Conscious", "Less Conscious"],
    ]
    for lines in pages:
        y = 790
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 18
        pdf.showPage()
    pdf.save()
    return path


class WSGIClient:
    def __init__(self, app):
        self.app = app
        self.cookies = SimpleCookie()

    def get(self, path: str):
        return self.request("GET", path)

    def post_form(self, path: str, data: dict[str, str]):
        body = urlencode(data).encode("utf-8")
        return self.request(
            "POST",
            path,
            body=body,
            headers={"CONTENT_TYPE": "application/x-www-form-urlencoded"},
        )

    def post_multipart(self, path: str, fields: dict[str, str], files: list[tuple[str, str, bytes]]):
        boundary = "----insights-test-boundary"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.append(f"--{boundary}\r\n".encode())
            chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            chunks.append(value.encode())
            chunks.append(b"\r\n")
        for name, filename, payload in files:
            chunks.append(f"--{boundary}\r\n".encode())
            chunks.append(
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                "Content-Type: application/pdf\r\n\r\n".encode()
            )
            chunks.append(payload)
            chunks.append(b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode())
        body = b"".join(chunks)
        return self.request("POST", path, body=body, headers={"CONTENT_TYPE": f"multipart/form-data; boundary={boundary}"})

    def request(self, method: str, path: str, *, body: bytes = b"", headers: dict[str, str] | None = None):
        headers = headers or {}
        if "?" in path:
            path_info, query_string = path.split("?", 1)
        else:
            path_info, query_string = path, ""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path_info,
            "QUERY_STRING": query_string,
            "SERVER_NAME": "testserver",
            "SERVER_PORT": "80",
            "wsgi.version": (1, 0),
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
            "wsgi.url_scheme": "http",
            "REMOTE_ADDR": "127.0.0.1",
            "CONTENT_LENGTH": str(len(body)),
        }
        environ.update(headers)
        if self.cookies:
            environ["HTTP_COOKIE"] = "; ".join(f"{key}={morsel.value}" for key, morsel in self.cookies.items())
        captured: dict[str, object] = {}

        def start_response(status, response_headers):
            captured["status"] = status
            captured["headers"] = response_headers

        chunks = self.app(environ, start_response)
        response_body = b"".join(chunks)
        for key, value in captured["headers"]:
            if key.lower() == "set-cookie":
                self.cookies.load(value)
        return int(str(captured["status"]).split()[0]), dict(captured["headers"]), response_body
