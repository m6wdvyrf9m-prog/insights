#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from insights_app.config import Config
from insights_app.db import ensure_admin_user, init_db, transaction


def main() -> int:
    config = Config.from_env()
    init_db(config.database_path)
    with transaction(config.database_path) as conn:
        ensure_admin_user(conn, config.admin_password_hash)
    print(f"Database ready at {config.database_path}")
    if not config.admin_password_hash:
        print("ADMIN_PASSWORD_HASH is not set; admin login remains disabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
