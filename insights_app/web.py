from __future__ import annotations

import cgi
import json
import re
import traceback
from email.utils import formatdate
from html import escape
from http import HTTPStatus
from http.cookies import SimpleCookie
from pathlib import Path
from types import TracebackType
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlencode

from .config import Config
from .db import connect, ensure_admin_user, init_db, loads, transaction
from .security import (
    check_rate_limit,
    create_session,
    destroy_session,
    participant_action_token,
    verify_participant_action_token,
    verify_password,
)
from .services import (
    AppError,
    Forbidden,
    can_access_experience,
    card_state_for_participant,
    create_learning_experience,
    deploy_card_game,
    get_active_card_deployment,
    get_participant_by_token,
    give_card,
    keep_card,
    parse_and_store_upload,
    profile_from_row,
    save_pdf_upload,
)


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "insights_app" / "static"
SESSION_COOKIE = "insights_session"


class Request:
    def __init__(self, environ: dict[str, Any]):
        self.environ = environ
        self.method = environ.get("REQUEST_METHOD", "GET").upper()
        self.path = environ.get("PATH_INFO", "/")
        self.query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        self.cookies = SimpleCookie(environ.get("HTTP_COOKIE", ""))
        self._form: cgi.FieldStorage | dict[str, list[str]] | None = None
        self._json: dict[str, Any] | None = None

    @property
    def ip(self) -> str:
        forwarded = self.environ.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
        return self.environ.get("REMOTE_ADDR", "")

    @property
    def user_agent(self) -> str:
        return self.environ.get("HTTP_USER_AGENT", "")

    @property
    def session_cookie(self) -> str | None:
        morsel = self.cookies.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def header(self, name: str) -> str:
        key = "HTTP_" + name.upper().replace("-", "_")
        return self.environ.get(key, "")

    def form(self) -> cgi.FieldStorage | dict[str, list[str]]:
        if self._form is not None:
            return self._form
        content_type = self.environ.get("CONTENT_TYPE", "")
        if self.method not in {"POST", "PUT", "PATCH"}:
            self._form = {}
            return self._form
        if content_type.startswith("multipart/form-data"):
            self._form = cgi.FieldStorage(
                fp=self.environ["wsgi.input"],
                environ=self.environ,
                keep_blank_values=True,
            )
            return self._form
        length = int(self.environ.get("CONTENT_LENGTH") or 0)
        body = self.environ["wsgi.input"].read(length).decode("utf-8") if length else ""
        self._form = parse_qs(body, keep_blank_values=True)
        return self._form

    def form_value(self, name: str, default: str = "") -> str:
        form = self.form()
        if isinstance(form, cgi.FieldStorage):
            item = form[name] if name in form else None
            if item is None:
                return default
            if isinstance(item, list):
                item = item[0]
            return item.value if not item.filename else default
        values = form.get(name)
        return values[0] if values else default

    def files(self, name: str) -> list[cgi.FieldStorage]:
        form = self.form()
        if not isinstance(form, cgi.FieldStorage) or name not in form:
            return []
        item = form[name]
        items = item if isinstance(item, list) else [item]
        return [entry for entry in items if getattr(entry, "filename", None)]

    def json(self) -> dict[str, Any]:
        if self._json is not None:
            return self._json
        length = int(self.environ.get("CONTENT_LENGTH") or 0)
        if length <= 0:
            self._json = {}
        else:
            self._json = json.loads(self.environ["wsgi.input"].read(length).decode("utf-8"))
        return self._json


class Response:
    def __init__(
        self,
        body: str | bytes,
        status: int = 200,
        headers: list[tuple[str, str]] | None = None,
        content_type: str = "text/html; charset=utf-8",
    ):
        self.status = status
        self.headers = headers or []
        self.content_type = content_type
        self.body = body.encode("utf-8") if isinstance(body, str) else body

    def as_wsgi(self, start_response: Callable) -> list[bytes]:
        status_line = f"{self.status} {HTTPStatus(self.status).phrase}"
        headers = [("Content-Type", self.content_type), ("Content-Length", str(len(self.body)))] + self.headers
        start_response(status_line, headers)
        return [self.body]


class Application:
    def __init__(self, config: Config):
        self.config = config
        init_db(config.database_path)
        with transaction(config.database_path) as conn:
            ensure_admin_user(conn, config.admin_password_hash)

    def __call__(self, environ: dict[str, Any], start_response: Callable) -> list[bytes]:
        request = Request(environ)
        try:
            response = self.dispatch(request)
        except RedirectRequired as exc:
            response = redirect(exc.location)
        except AppError as exc:
            if request.path.startswith("/api/"):
                response = json_response({"ok": False, "error": str(exc)}, status=exc.status_code)
            else:
                response = self.error_page(str(exc), exc.status_code)
        except Exception:
            traceback.print_exc()
            if request.path.startswith("/api/"):
                response = json_response({"ok": False, "error": "Something went wrong. Please try again."}, status=500)
            else:
                response = self.error_page("Something went wrong. Please try again or contact the administrator.", 500)
        return response.as_wsgi(start_response)

    def dispatch(self, request: Request) -> Response:
        if request.path.startswith("/static/"):
            return self.static_response(request.path.removeprefix("/static/"))
        if request.path == "/healthz":
            return json_response({"ok": True})

        with transaction(self.config.database_path) as conn:
            session = self.current_session(conn, request)

            if request.path == "/":
                return redirect("/admin")
            if request.path == "/admin/login":
                return self.login(request, conn, session, role="admin")
            if request.path == "/facilitator/login":
                return self.login(request, conn, session, role="facilitator")
            if request.path == "/logout" and request.method == "POST":
                self.validate_csrf(request, session)
                destroy_session(conn, request.session_cookie)
                return redirect("/admin/login", clear_cookie=True, config=self.config)
            if request.path == "/admin":
                user = self.require_role(session, "admin")
                return self.admin_dashboard(conn, user)
            if request.path == "/admin/upload":
                user = self.require_role(session, "admin")
                if request.method == "POST":
                    self.validate_csrf(request, session)
                    return self.admin_upload_post(request, conn, user)
                return self.admin_upload_form(session)
            if request.path == "/admin/experiences/new":
                user = self.require_role(session, "admin")
                if request.method == "POST":
                    self.validate_csrf(request, session)
                    return self.admin_experience_post(request, conn, user)
                return self.admin_experience_form(session)
            match = re.fullmatch(r"/admin/experiences/(\d+)", request.path)
            if match:
                user = self.require_role(session, "admin")
                return self.experience_page(conn, int(match.group(1)), user, admin=True)
            if request.path == "/facilitator":
                user = self.require_role(session, "facilitator")
                return redirect(f"/facilitator/experiences/{int(user['learning_experience_id'])}")
            match = re.fullmatch(r"/facilitator/experiences/(\d+)", request.path)
            if match:
                user = self.require_role(session, "facilitator")
                experience_id = int(match.group(1))
                if not can_access_experience(user, experience_id):
                    raise Forbidden("You do not have access to that Learning Experience.")
                return self.experience_page(conn, experience_id, user, admin=False)
            match = re.fullmatch(r"/facilitator/experiences/(\d+)/deploy/card-game", request.path)
            if match and request.method == "POST":
                user = self.require_role(session, "facilitator")
                self.validate_csrf(request, session)
                experience_id = int(match.group(1))
                if not can_access_experience(user, experience_id):
                    raise Forbidden("You do not have access to that Learning Experience.")
                deploy_card_game(conn, experience_id=experience_id, actor_user_id=int(user["user_id"]))
                return redirect(f"/facilitator/experiences/{experience_id}?message=activity-deployed")
            match = re.fullmatch(r"/p/([A-Za-z0-9_-]+)", request.path)
            if match:
                return self.participant_page(conn, match.group(1))
            match = re.fullmatch(r"/api/p/([A-Za-z0-9_-]+)/state", request.path)
            if match:
                return self.participant_state(conn, match.group(1))
            match = re.fullmatch(r"/api/p/([A-Za-z0-9_-]+)/cards/(\d+)/(keep|give)", request.path)
            if match and request.method == "POST":
                return self.participant_card_action(conn, request, match.group(1), int(match.group(2)), match.group(3))

        return self.error_page("Page not found.", 404)

    def current_session(self, conn: Any, request: Request) -> dict[str, Any] | None:
        from .security import load_session

        return load_session(conn, request.session_cookie)

    def require_role(self, session: dict[str, Any] | None, role: str) -> dict[str, Any]:
        if not session:
            raise RedirectRequired("/admin/login" if role == "admin" else "/facilitator/login")
        if session["role"] != role:
            raise Forbidden("You do not have permission to view this area.")
        return session

    def validate_csrf(self, request: Request, session: dict[str, Any] | None) -> None:
        if not session:
            raise Forbidden("Your session has expired.")
        submitted = request.form_value("csrf_token") or request.header("X-CSRF-Token")
        if not submitted or submitted != session["csrf_token"]:
            raise Forbidden("Security check failed. Please refresh and try again.")

    def login(self, request: Request, conn: Any, session: dict[str, Any] | None, *, role: str) -> Response:
        if session and session["role"] == role:
            return redirect("/admin" if role == "admin" else "/facilitator")
        title = "Admin sign in" if role == "admin" else "Facilitator sign in"
        action = "/admin/login" if role == "admin" else "/facilitator/login"
        if request.method == "GET":
            return html_response(self.login_page(title, action, role, ""))
        if len(self.config.secret_key) < 32:
            return html_response(self.login_page(title, action, role, "SECRET_KEY is not configured."), status=503)
        if not check_rate_limit(conn, bucket=f"{role}:login", identity=request.ip, limit=12, window_seconds=600):
            return html_response(self.login_page(title, action, role, "Too many attempts. Please try later."), status=429)
        username = request.form_value("username").strip().lower()
        password = request.form_value("password")
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? AND role = ? AND active = 1",
            (username, role),
        ).fetchone()
        if not user or not verify_password(password, user["password_hash"]):
            return html_response(self.login_page(title, action, role, "Those details do not match."), status=401)
        token, _csrf = create_session(
            conn,
            int(user["id"]),
            role,
            ip=request.ip,
            user_agent=request.user_agent,
            hours=self.config.session_hours,
        )
        conn.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (user["id"],))
        target = "/admin" if role == "admin" else "/facilitator"
        return redirect(target, cookie=token, config=self.config)

    def admin_dashboard(self, conn: Any, user: dict[str, Any]) -> Response:
        participants = conn.execute(
            """
            SELECT participants.*, learning_experiences.name AS experience_name
            FROM participants
            LEFT JOIN learning_experience_members ON learning_experience_members.participant_id = participants.id
            LEFT JOIN learning_experiences ON learning_experiences.id = learning_experience_members.learning_experience_id
            ORDER BY participants.created_at DESC
            LIMIT 50
            """
        ).fetchall()
        uploads = conn.execute("SELECT * FROM uploads ORDER BY created_at DESC LIMIT 20").fetchall()
        experiences = conn.execute(
            """
            SELECT learning_experiences.*,
                   users.username AS facilitator_username,
                   COUNT(learning_experience_members.participant_id) AS participant_count
            FROM learning_experiences
            LEFT JOIN users ON users.id = learning_experiences.facilitator_user_id
            LEFT JOIN learning_experience_members ON learning_experience_members.learning_experience_id = learning_experiences.id
            GROUP BY learning_experiences.id
            ORDER BY learning_experiences.created_at DESC
            """
        ).fetchall()
        body = f"""
        <section class="page-head">
          <div>
            <p class="eyebrow">Admin</p>
            <h1>Insights Learning Experiences</h1>
            <p class="lead">Upload profiles, inspect parse confidence, and prepare facilitator-led activities.</p>
          </div>
          <div class="actions">
            <a class="button" href="/admin/upload">Upload PDF</a>
            <a class="button primary" href="/admin/experiences/new">New Learning Experience</a>
          </div>
        </section>
        {runtime_notice(self.config)}
        <section class="grid two">
          <div class="panel">
            <h2>Learning Experiences</h2>
            {experience_table(experiences, admin=True)}
          </div>
          <div class="panel">
            <h2>Recent Uploads</h2>
            {upload_table(uploads)}
          </div>
        </section>
        <section class="panel">
          <h2>Participants</h2>
          {participant_table(participants, self.config)}
        </section>
        """
        return html_response(layout("Admin", body, user=user, csrf=user["csrf_token"]))

    def admin_upload_form(self, session: dict[str, Any]) -> Response:
        body = f"""
        <section class="page-head">
          <div>
            <p class="eyebrow">Admin</p>
            <h1>Upload one profile</h1>
            <p class="lead">The parser stores raw excerpts, normalized fields, confidence scores, and a secure participant link.</p>
          </div>
        </section>
        <form class="panel form" action="/admin/upload" method="post" enctype="multipart/form-data">
          {csrf_input(session)}
          <label>Insights Discovery PDF <input required type="file" name="pdf" accept="application/pdf,.pdf"></label>
          <button class="button primary" type="submit">Extract Profile</button>
        </form>
        """
        return html_response(layout("Upload PDF", body, user=session, csrf=session["csrf_token"]))

    def admin_upload_post(self, request: Request, conn: Any, user: dict[str, Any]) -> Response:
        files = request.files("pdf")
        if not files:
            raise AppError("Choose a PDF to upload.")
        file_item = files[0]
        payload = file_item.file.read(self.config.max_upload_bytes + 1)
        stored_path, file_hash = save_pdf_upload(
            self.config.upload_dir,
            file_item.filename or "profile.pdf",
            payload,
            max_bytes=self.config.max_upload_bytes,
        )
        result = parse_and_store_upload(
            conn,
            stored_path=stored_path,
            original_filename=file_item.filename or "profile.pdf",
            file_sha256=file_hash,
            created_by_user_id=int(user["user_id"]),
        )
        if result.status == "parsed" and result.participant_id:
            return redirect(f"/admin?message=uploaded&participant_id={result.participant_id}")
        issue = quote("; ".join(result.issues) or "The PDF could not be parsed.")
        return redirect(f"/admin?message=parse-failed&issue={issue}")

    def admin_experience_form(self, session: dict[str, Any]) -> Response:
        body = f"""
        <section class="page-head">
          <div>
            <p class="eyebrow">Admin</p>
            <h1>New Learning Experience</h1>
            <p class="lead">Create a facilitator login and optionally bulk-upload the cohort PDFs in one pass.</p>
          </div>
        </section>
        <form class="panel form" action="/admin/experiences/new" method="post" enctype="multipart/form-data">
          {csrf_input(session)}
          <label>Learning Experience name <input required name="name" maxlength="120" autocomplete="off"></label>
          <label>Facilitator username <input required name="facilitator_username" maxlength="80" autocomplete="off"></label>
          <label>Facilitator password <input required name="facilitator_password" type="password" minlength="12" autocomplete="new-password"></label>
          <label>Cohort PDFs <input type="file" name="pdfs" accept="application/pdf,.pdf" multiple></label>
          <p class="hint">Use a strong facilitator password. It is stored only as a one-way hash.</p>
          <button class="button primary" type="submit">Create Experience</button>
        </form>
        """
        return html_response(layout("New Learning Experience", body, user=session, csrf=session["csrf_token"]))

    def admin_experience_post(self, request: Request, conn: Any, user: dict[str, Any]) -> Response:
        if not check_rate_limit(conn, bucket="bulk_upload", identity=request.ip, limit=30, window_seconds=3600):
            raise AppError("Too many bulk upload attempts. Please try later.")
        experience_id = create_learning_experience(
            conn,
            name=request.form_value("name"),
            facilitator_username=request.form_value("facilitator_username"),
            facilitator_password=request.form_value("facilitator_password"),
            created_by_user_id=int(user["user_id"]),
        )
        for file_item in request.files("pdfs"):
            payload = file_item.file.read(self.config.max_upload_bytes + 1)
            stored_path, file_hash = save_pdf_upload(
                self.config.upload_dir,
                file_item.filename or "profile.pdf",
                payload,
                max_bytes=self.config.max_upload_bytes,
            )
            parse_and_store_upload(
                conn,
                stored_path=stored_path,
                original_filename=file_item.filename or "profile.pdf",
                file_sha256=file_hash,
                created_by_user_id=int(user["user_id"]),
                learning_experience_id=experience_id,
            )
        return redirect(f"/admin/experiences/{experience_id}?message=created")

    def experience_page(self, conn: Any, experience_id: int, user: dict[str, Any], *, admin: bool) -> Response:
        experience = conn.execute(
            """
            SELECT learning_experiences.*, users.username AS facilitator_username
            FROM learning_experiences
            LEFT JOIN users ON users.id = learning_experiences.facilitator_user_id
            WHERE learning_experiences.id = ?
            """,
            (experience_id,),
        ).fetchone()
        if not experience:
            raise AppError("Learning Experience not found.")
        if not can_access_experience(user, experience_id):
            raise Forbidden("You do not have access to that Learning Experience.")
        participants = conn.execute(
            """
            SELECT participants.*
            FROM participants
            JOIN learning_experience_members ON learning_experience_members.participant_id = participants.id
            WHERE learning_experience_members.learning_experience_id = ?
            ORDER BY participants.full_name
            """,
            (experience_id,),
        ).fetchall()
        deployment = get_active_card_deployment(conn, experience_id)
        deploy_form = ""
        if not admin:
            deploy_form = f"""
            <form method="post" action="/facilitator/experiences/{experience_id}/deploy/card-game">
              {csrf_input(user)}
              <button class="button primary" type="submit">{'Redeploy safely' if deployment else 'Deploy card game'}</button>
            </form>
            """
        body = f"""
        <section class="page-head">
          <div>
            <p class="eyebrow">{'Admin' if admin else 'Facilitator'}</p>
            <h1>{escape(experience['name'])}</h1>
            <p class="lead">Facilitator: {escape(experience['facilitator_username'] or 'Not assigned')} - Participants: {len(participants)} - Card game: {'deployed' if deployment else 'not deployed'}</p>
          </div>
          <div class="actions">{deploy_form}</div>
        </section>
        <section class="panel">
          <h2>Participants</h2>
          {participant_table(participants, self.config)}
        </section>
        """
        return html_response(layout(experience["name"], body, user=user, csrf=user["csrf_token"]))

    def participant_page(self, conn: Any, token: str) -> Response:
        row = get_participant_by_token(conn, token)
        if not row:
            return self.error_page("This participant link is not valid.", 404, public=True)
        profile = profile_from_row(row)
        action_token = participant_action_token(self.config.secret_key, int(row["id"]), row["token_hash"])
        body = participant_profile_html(profile, action_token)
        return html_response(layout(profile["full_name"], body, public=True))

    def participant_state(self, conn: Any, token: str) -> Response:
        row = get_participant_by_token(conn, token)
        if not row:
            return json_response({"error": "not found"}, status=404)
        return json_response(card_state_for_participant(conn, int(row["id"])))

    def participant_card_action(self, conn: Any, request: Request, token: str, assignment_id: int, action: str) -> Response:
        row = get_participant_by_token(conn, token)
        if not row:
            return json_response({"error": "not found"}, status=404)
        submitted = request.header("X-CSRF-Token") or request.form_value("csrf_token")
        if not verify_participant_action_token(self.config.secret_key, submitted, int(row["id"]), row["token_hash"]):
            raise Forbidden("Security check failed. Please refresh and try again.")
        if action == "keep":
            keep_card(conn, participant_id=int(row["id"]), assignment_id=assignment_id)
        else:
            payload = request.json() if request.environ.get("CONTENT_TYPE", "").startswith("application/json") else {}
            recipient_id = int(payload.get("recipient_id") or request.form_value("recipient_id") or 0)
            give_card(conn, participant_id=int(row["id"]), assignment_id=assignment_id, recipient_id=recipient_id)
        return json_response({"ok": True, "state": card_state_for_participant(conn, int(row["id"]))})

    def static_response(self, rel_path: str) -> Response:
        safe = Path(rel_path)
        if safe.is_absolute() or ".." in safe.parts:
            return self.error_page("Static asset not found.", 404)
        path = STATIC_DIR / safe
        if not path.exists() or not path.is_file():
            return self.error_page("Static asset not found.", 404)
        content_type = "text/plain"
        if path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        return Response(path.read_bytes(), content_type=content_type, headers=[("Last-Modified", formatdate(path.stat().st_mtime, usegmt=True))])

    def error_page(self, message: str, status: int = 400, *, public: bool = False) -> Response:
        if isinstance(message, RedirectRequired):
            return redirect(message.location)
        body = f"""
        <section class="page-head compact">
          <div>
            <p class="eyebrow">Notice</p>
            <h1>{escape(HTTPStatus(status).phrase)}</h1>
            <p class="lead">{escape(message)}</p>
          </div>
        </section>
        """
        return html_response(layout(HTTPStatus(status).phrase, body, public=public), status=status)

    def login_page(self, title: str, action: str, role: str, error: str) -> str:
        body = f"""
        <main class="login-shell">
          <section class="login-panel">
            <div class="logo-lockup">
              <span>The Colour Works</span>
              <strong>Insights Learning Experience</strong>
            </div>
            <p class="eyebrow">{escape(role)}</p>
            <h1>{escape(title)}</h1>
            {'<p class="error">' + escape(error) + '</p>' if error else ''}
            <form class="form" action="{escape(action)}" method="post">
              <label>Username <input required name="username" autocomplete="username"></label>
              <label>Password <input required name="password" type="password" autocomplete="current-password"></label>
              <button class="button primary" type="submit">Sign in</button>
            </form>
          </section>
        </main>
        """
        return bare_page(title, body)


class RedirectRequired(AppError):
    status_code = 302

    def __init__(self, location: str):
        super().__init__(location)
        self.location = location


def create_app(config: Config | None = None) -> Application:
    return Application(config or Config.from_env())


def html_response(body: str, status: int = 200) -> Response:
    return Response(body, status=status)


def json_response(data: Any, status: int = 200) -> Response:
    return Response(json.dumps(data, ensure_ascii=False), status=status, content_type="application/json; charset=utf-8")


def redirect(location: str, *, cookie: str | None = None, clear_cookie: bool = False, config: Config | None = None) -> Response:
    headers = [("Location", location)]
    if cookie and config:
        secure = "; Secure" if config.secure_cookies else ""
        headers.append(
            (
                "Set-Cookie",
                f"{SESSION_COOKIE}={cookie}; Max-Age={config.session_hours * 3600}; Path=/; HttpOnly; SameSite=Lax{secure}",
            )
        )
    if clear_cookie and config:
        secure = "; Secure" if config.secure_cookies else ""
        headers.append(("Set-Cookie", f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax{secure}"))
    return Response("", status=302, headers=headers)


def layout(
    title: str,
    body: str,
    *,
    user: dict[str, Any] | None = None,
    csrf: str | None = None,
    public: bool = False,
) -> str:
    nav = ""
    if not public:
        nav = f"""
        <header class="topbar">
          <a class="logo-lockup" href="/admin">
            <span>The Colour Works</span>
            <strong>Insights</strong>
          </a>
          <nav>
            {('<a href="/admin">Admin</a><a href="/admin/experiences/new">New Experience</a>' if user and user['role'] == 'admin' else '<a href="/facilitator">Experience</a>')}
            <form method="post" action="/logout">{csrf_input({'csrf_token': csrf or ''})}<button type="submit">Sign out</button></form>
          </nav>
        </header>
        """
    return bare_page(title, f"{nav}<main class='shell'>{body}</main>")


def bare_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} - The Colour Works Insights</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
{body}
<script src="/static/app.js"></script>
</body>
</html>"""


def csrf_input(session: dict[str, Any]) -> str:
    return f'<input type="hidden" name="csrf_token" value="{escape(session.get("csrf_token", ""))}">'


def runtime_notice(config: Config) -> str:
    issues = config.validate_runtime()
    if not issues:
        return ""
    return "<section class='notice'>" + "".join(f"<p>{escape(issue)}</p>" for issue in issues) + "</section>"


def experience_table(rows: list[Any], *, admin: bool) -> str:
    if not rows:
        return "<p class='muted'>No Learning Experiences yet.</p>"
    body = "".join(
        f"""
        <tr>
          <td><a href="/admin/experiences/{row['id']}">{escape(row['name'])}</a></td>
          <td>{escape(row['facilitator_username'] or '')}</td>
          <td>{int(row['participant_count'])}</td>
        </tr>
        """
        for row in rows
    )
    return f"<table><thead><tr><th>Name</th><th>Facilitator</th><th>Participants</th></tr></thead><tbody>{body}</tbody></table>"


def upload_table(rows: list[Any]) -> str:
    if not rows:
        return "<p class='muted'>No uploads yet.</p>"
    body = "".join(
        f"""
        <tr>
          <td>{escape(row['original_filename'])}</td>
          <td><span class="pill {escape(row['parse_status'])}">{escape(row['parse_status'])}</span></td>
          <td>{escape(', '.join(loads(row['parse_issues_json'], [])[:2]))}</td>
        </tr>
        """
        for row in rows
    )
    return f"<table><thead><tr><th>File</th><th>Status</th><th>Issues</th></tr></thead><tbody>{body}</tbody></table>"


def participant_table(rows: list[Any], config: Config) -> str:
    if not rows:
        return "<p class='muted'>No participants yet.</p>"
    body = ""
    for row in rows:
        issues = loads(row["parse_issues_json"], [])
        confidence = loads(row["parse_confidence_json"], {})
        link = f"{config.app_base_url}/p/{row['token']}"
        experience = row["experience_name"] if "experience_name" in row.keys() else ""
        body += f"""
        <tr>
          <td>{escape(row['full_name'])}</td>
          <td>{escape(experience or '')}</td>
          <td>{escape(str(round(float(confidence.get('overall', 0)) * 100)))}%</td>
          <td>{escape('; '.join(issues[:2]))}</td>
          <td><a href="/p/{escape(row['token'])}">Open</a><br><small>{escape(link)}</small></td>
        </tr>
        """
    return f"<table><thead><tr><th>Name</th><th>Experience</th><th>Confidence</th><th>Parse issues</th><th>Token page</th></tr></thead><tbody>{body}</tbody></table>"


def participant_profile_html(profile: dict, action_token: str) -> str:
    colours = profile.get("colour_dynamics", {})
    wheel = profile.get("wheel", {})
    sections = profile.get("sections", {})
    bars = "".join(
        colour_bar(colour, colours.get(colour, {}).get("score"), colours.get(colour, {}).get("percentage"))
        for colour in ("Blue", "Green", "Yellow", "Red")
    )
    body = f"""
    <section class="participant-hero">
      <div>
        <div class="logo-lockup inverse"><span>The Colour Works</span><strong>Insights Discovery</strong></div>
        <p class="eyebrow">Personal profile</p>
        <h1>{escape(profile['full_name'])}</h1>
        <p class="lead">Your extracted Insights Discovery results and Learning Experience activities live on this private link.</p>
      </div>
      <div class="hero-ribbon" aria-hidden="true"><span></span><span></span><span></span><span></span></div>
    </section>
    <section class="profile-grid">
      <div class="panel colour-panel">
        <div class="section-title"><p class="eyebrow">Conscious Colour Dynamics</p><h2>BLUE - GREEN - YELLOW - RED</h2></div>
        <div class="bars">{bars}</div>
      </div>
      <div class="panel wheel-panel">
        <div class="section-title"><p class="eyebrow">72 Type Wheel</p><h2>{escape(wheel.get('conscious_text') or 'Wheel position unavailable')}</h2></div>
        {wheel_svg(wheel.get('conscious_position'))}
      </div>
    </section>
    <section class="accordion-grid">
      {details_block('Strengths', sections.get('strengths', []))}
      {details_block('Development Areas', sections.get('development_areas', []))}
      {details_block('Value to the Team', sections.get('value_to_team', []))}
      {details_block('Effective Communications', sections.get('effective_communications', []))}
      {details_block('Barriers to Effective Communication', sections.get('communication_barriers', []))}
      {details_block('Suggestions for Development', sections.get('suggestions_for_development', []))}
      {details_text('Possible Blind Spots', sections.get('possible_blind_spots_summary', ''))}
      {details_text('Opposite Type', sections.get('opposite_type_summary', ''))}
    </section>
    <section class="panel activity-panel" id="card-game" data-token="{escape(profile['token'])}" data-csrf="{escape(action_token)}">
      <div class="section-title"><p class="eyebrow">Learning Experience</p><h2>Card Game</h2></div>
      <div id="card-game-root" class="activity-empty">Waiting for your facilitator to deploy the activity.</div>
    </section>
    """
    return body


def colour_bar(colour: str, score: float | None, percentage: int | None) -> str:
    pct = max(0, min(100, int(percentage or 0)))
    score_text = "n/a" if score is None else f"{float(score):.2f}"
    return f"""
    <div class="bar-row {colour.lower()}">
      <div class="bar-label"><strong>{escape(colour.upper())}</strong><span>{score_text} - {pct}%</span></div>
      <div class="bar-track"><span style="width: {pct}%"></span></div>
    </div>
    """


def wheel_svg(position: Any) -> str:
    try:
        pos = int(position)
    except (TypeError, ValueError):
        pos = 0
    ticks = []
    for index in range(72):
        angle = (index / 72) * 360 - 90
        ticks.append(f'<i style="--a:{angle}deg"></i>')
    marker = ""
    if 1 <= pos <= 72:
        angle = ((pos - 1) / 72) * 360 - 90
        marker = f'<b class="wheel-marker" style="--a:{angle}deg"><span>{pos}</span></b>'
    return f"""
    <div class="wheel" aria-label="Custom 72 position wheel visualization">
      <div class="wheel-core">{''.join(ticks)}{marker}<em>72</em></div>
    </div>
    <p class="hint">Original visualization: custom non-proprietary 72-position plot.</p>
    """


def details_block(title: str, values: list[str]) -> str:
    if not values:
        values = ["Not available from this upload."]
    items = "".join(f"<li>{escape(value)}</li>" for value in values[:5])
    return f"<details><summary>{escape(title)}</summary><ul>{items}</ul></details>"


def details_text(title: str, text: str) -> str:
    text = text or "Not available from this upload."
    return f"<details><summary>{escape(title)}</summary><p>{escape(text)}</p></details>"
