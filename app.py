from __future__ import annotations

from io import BytesIO

from flask import Flask, Response, request

from insights_app import create_app


insights_wsgi_app = create_app()
app = Flask(__name__)


@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def route_to_insights(path: str) -> Response:
    body = request.get_data()
    environ = request.environ.copy()
    environ["PATH_INFO"] = "/" + path
    environ["QUERY_STRING"] = request.query_string.decode("utf-8")
    environ["wsgi.input"] = BytesIO(body)
    environ["CONTENT_LENGTH"] = str(len(body)) if body else ""

    status_headers = {"status": "500 Internal Server Error", "headers": []}

    def start_response(status, headers, exc_info=None):
        status_headers["status"] = status
        status_headers["headers"] = headers

    response_body = b"".join(insights_wsgi_app(environ, start_response))
    status_code = int(status_headers["status"].split(" ", 1)[0])
    headers = [(key, value) for key, value in status_headers["headers"] if key.lower() != "content-length"]
    return Response(response_body, status=status_code, headers=headers)
