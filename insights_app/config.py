from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    database_path: Path
    upload_dir: Path
    secret_key: str
    admin_password_hash: str
    app_base_url: str
    secure_cookies: bool
    max_upload_bytes: int = 20 * 1024 * 1024
    session_hours: int = 12

    @classmethod
    def from_env(cls) -> "Config":
        default_data_dir = "/tmp/insights" if os.environ.get("VERCEL") else "instance"
        base_dir = Path(os.environ.get("INSIGHTS_DATA_DIR", default_data_dir)).resolve()
        secret_key = os.environ.get("SECRET_KEY", "")
        admin_password_hash = os.environ.get("ADMIN_PASSWORD_HASH", "")
        return cls(
            database_path=Path(os.environ.get("DATABASE_PATH", base_dir / "insights.sqlite3")).resolve(),
            upload_dir=Path(os.environ.get("UPLOAD_DIR", base_dir / "uploads")).resolve(),
            secret_key=secret_key,
            admin_password_hash=admin_password_hash,
            app_base_url=os.environ.get("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
            secure_cookies=os.environ.get("APP_SECURE_COOKIES", "false").lower() in {"1", "true", "yes"},
            max_upload_bytes=int(os.environ.get("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))),
            session_hours=int(os.environ.get("SESSION_HOURS", "12")),
        )

    def validate_runtime(self) -> list[str]:
        issues: list[str] = []
        if len(self.secret_key) < 32:
            issues.append("SECRET_KEY must be set to at least 32 random characters.")
        if not self.admin_password_hash:
            issues.append("ADMIN_PASSWORD_HASH is not set, so admin login is disabled.")
        return issues
