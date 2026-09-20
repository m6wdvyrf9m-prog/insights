from __future__ import annotations

import hashlib
import json
import mimetypes
import random
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cards import CARD_COLOURS
from .db import dumps, get_activity_id, loads, seed_reference_data
from .pdf_parser import parse_insights_pdf
from .security import hash_password, secure_token, stable_hash


class AppError(Exception):
    status_code = 400


class Forbidden(AppError):
    status_code = 403


class NotFound(AppError):
    status_code = 404


class ValidationError(AppError):
    status_code = 400


@dataclass
class UploadResult:
    upload_id: int
    participant_id: int | None
    status: str
    issues: list[str]


def save_pdf_upload(upload_dir: Path, filename: str, payload: bytes, *, max_bytes: int) -> tuple[Path, str]:
    if not filename.lower().endswith(".pdf"):
        raise ValidationError("Please upload a PDF file.")
    if len(payload) > max_bytes:
        raise ValidationError("The PDF is too large for this service.")
    if not payload.startswith(b"%PDF"):
        guessed, _ = mimetypes.guess_type(filename)
        raise ValidationError(f"The uploaded file does not look like a PDF ({guessed or 'unknown type'}).")
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_hash = hashlib.sha256(payload).hexdigest()
    stored_path = upload_dir / f"{uuid.uuid4().hex}.pdf"
    stored_path.write_bytes(payload)
    return stored_path, file_hash


def parse_and_store_upload(
    conn: sqlite3.Connection,
    *,
    stored_path: Path,
    original_filename: str,
    file_sha256: str,
    created_by_user_id: int | None,
    learning_experience_id: int | None = None,
) -> UploadResult:
    try:
        parsed = parse_insights_pdf(stored_path)
        critical_ok = (
            parsed["full_name"] != "Unknown participant"
            and all(parsed["colour_dynamics"][colour]["score"] is not None for colour in ("Blue", "Green", "Yellow", "Red"))
            and parsed["wheel"].get("conscious_position") is not None
        )
        status = "parsed" if critical_ok else "failed"
    except Exception as exc:
        parsed = {
            "full_name": "Unknown participant",
            "colour_dynamics": {},
            "wheel": {},
            "sections": {},
            "raw_excerpts": {},
            "confidence": {"overall": 0.0},
            "issues": [f"Parser error: {exc}"],
        }
        status = "failed"

    participant_id: int | None = None
    if status == "parsed":
        token = secure_token(32)
        cursor = conn.execute(
            """
            INSERT INTO participants(
              full_name, email, token, token_hash, profile_json, raw_excerpts_json,
              parse_confidence_json, parse_issues_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parsed["full_name"],
                extract_email(parsed["raw_excerpts"]),
                token,
                stable_hash(token),
                dumps(
                    {
                        "colour_dynamics": parsed["colour_dynamics"],
                        "wheel": parsed["wheel"],
                        "sections": parsed["sections"],
                    }
                ),
                dumps(parsed["raw_excerpts"]),
                dumps(parsed["confidence"]),
                dumps(parsed["issues"]),
            ),
        )
        participant_id = int(cursor.lastrowid)
        if learning_experience_id:
            conn.execute(
                """
                INSERT OR IGNORE INTO learning_experience_members(learning_experience_id, participant_id)
                VALUES (?, ?)
                """,
                (learning_experience_id, participant_id),
            )

    cursor = conn.execute(
        """
        INSERT INTO uploads(
          participant_id, learning_experience_id, original_filename, stored_path, file_sha256,
          parse_status, parse_confidence_json, parse_issues_json, created_by_user_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            participant_id,
            learning_experience_id,
            original_filename,
            str(stored_path),
            file_sha256,
            status,
            dumps(parsed["confidence"]),
            dumps(parsed["issues"]),
            created_by_user_id,
        ),
    )
    return UploadResult(int(cursor.lastrowid), participant_id, status, parsed["issues"])


def extract_email(raw_excerpts: dict[str, str]) -> str | None:
    joined = "\n".join(raw_excerpts.values())
    import re

    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", joined, flags=re.IGNORECASE)
    return match.group(0).lower() if match else None


def create_learning_experience(
    conn: sqlite3.Connection,
    *,
    name: str,
    facilitator_username: str,
    facilitator_password: str,
    created_by_user_id: int,
) -> int:
    name = name.strip()
    facilitator_username = facilitator_username.strip().lower()
    if not name:
        raise ValidationError("Learning Experience name is required.")
    if not facilitator_username:
        raise ValidationError("Facilitator username is required.")
    if len(facilitator_password) < 12:
        raise ValidationError("Facilitator password must be at least 12 characters.")
    cursor = conn.execute(
        "INSERT INTO learning_experiences(name, created_by_user_id) VALUES (?, ?)",
        (name, created_by_user_id),
    )
    experience_id = int(cursor.lastrowid)
    user_cursor = conn.execute(
        """
        INSERT INTO users(username, password_hash, role, learning_experience_id, active)
        VALUES (?, ?, 'facilitator', ?, 1)
        """,
        (facilitator_username, hash_password(facilitator_password), experience_id),
    )
    facilitator_user_id = int(user_cursor.lastrowid)
    conn.execute(
        "UPDATE learning_experiences SET facilitator_user_id = ? WHERE id = ?",
        (facilitator_user_id, experience_id),
    )
    return experience_id


def can_access_experience(user: dict[str, Any], experience_id: int) -> bool:
    if user["role"] == "admin":
        return True
    return user["role"] == "facilitator" and int(user["learning_experience_id"] or 0) == int(experience_id)


def deploy_card_game(conn: sqlite3.Connection, *, experience_id: int, actor_user_id: int) -> int:
    seed_reference_data(conn)
    activity_id = get_activity_id(conn, "colour-card-game")
    idempotency_key = f"experience:{experience_id}:activity:colour-card-game"
    existing = conn.execute(
        """
        SELECT id FROM activity_deployments
        WHERE learning_experience_id = ? AND activity_id = ?
        """,
        (experience_id, activity_id),
    ).fetchone()
    if existing:
        deployment_id = int(existing["id"])
        conn.execute(
            """
            INSERT INTO deployment_events(deployment_id, event_type, actor_user_id, details_json)
            VALUES (?, 'redeploy_requested', ?, ?)
            """,
            (deployment_id, actor_user_id, dumps({"safe": True, "effect": "existing deployment reused"})),
        )
        ensure_card_assignments(conn, deployment_id=deployment_id, experience_id=experience_id)
        return deployment_id

    cursor = conn.execute(
        """
        INSERT INTO activity_deployments(
          learning_experience_id, activity_id, status, idempotency_key, config_json, audit_json, deployed_by_user_id
        )
        VALUES (?, ?, 'deployed', ?, ?, ?, ?)
        """,
        (
            experience_id,
            activity_id,
            idempotency_key,
            dumps({"cards_per_colour": 4, "seed_content_replaceable": True}),
            dumps({"created_by": actor_user_id, "idempotency_key": idempotency_key}),
            actor_user_id,
        ),
    )
    deployment_id = int(cursor.lastrowid)
    conn.execute(
        """
        INSERT INTO deployment_events(deployment_id, event_type, actor_user_id, details_json)
        VALUES (?, 'deployed', ?, ?)
        """,
        (deployment_id, actor_user_id, dumps({"activity": "colour-card-game"})),
    )
    ensure_card_assignments(conn, deployment_id=deployment_id, experience_id=experience_id)
    return deployment_id


def ensure_card_assignments(conn: sqlite3.Connection, *, deployment_id: int, experience_id: int) -> None:
    participants = conn.execute(
        """
        SELECT participants.id
        FROM participants
        JOIN learning_experience_members ON learning_experience_members.participant_id = participants.id
        WHERE learning_experience_members.learning_experience_id = ?
        ORDER BY participants.full_name
        """,
        (experience_id,),
    ).fetchall()
    rng = random.SystemRandom()
    for participant in participants:
        participant_id = int(participant["id"])
        for colour in CARD_COLOURS:
            existing = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM card_assignments
                JOIN card_bank ON card_bank.id = card_assignments.card_id
                WHERE card_assignments.deployment_id = ?
                  AND card_assignments.participant_id = ?
                  AND card_bank.colour = ?
                """,
                (deployment_id, participant_id, colour),
            ).fetchone()["total"]
            missing = 4 - int(existing)
            if missing <= 0:
                continue
            cards = conn.execute(
                "SELECT id FROM card_bank WHERE colour = ? AND active = 1 ORDER BY id",
                (colour,),
            ).fetchall()
            already = {
                int(row["card_id"])
                for row in conn.execute(
                    "SELECT card_id FROM card_assignments WHERE deployment_id = ? AND participant_id = ?",
                    (deployment_id, participant_id),
                ).fetchall()
            }
            available = [int(card["id"]) for card in cards if int(card["id"]) not in already]
            chosen = rng.sample(available, missing)
            for card_id in chosen:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO card_assignments(
                      deployment_id, participant_id, card_id, origin_participant_id, current_owner_participant_id, status
                    )
                    VALUES (?, ?, ?, ?, ?, 'assigned')
                    """,
                    (deployment_id, participant_id, card_id, participant_id, participant_id),
                )


def get_participant_by_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM participants WHERE token_hash = ?", (stable_hash(token),)).fetchone()


def participant_experience(conn: sqlite3.Connection, participant_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT learning_experiences.*
        FROM learning_experiences
        JOIN learning_experience_members ON learning_experience_members.learning_experience_id = learning_experiences.id
        WHERE learning_experience_members.participant_id = ?
        ORDER BY learning_experiences.created_at DESC
        LIMIT 1
        """,
        (participant_id,),
    ).fetchone()


def get_active_card_deployment(conn: sqlite3.Connection, experience_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT activity_deployments.*
        FROM activity_deployments
        JOIN activities ON activities.id = activity_deployments.activity_id
        WHERE activity_deployments.learning_experience_id = ?
          AND activities.slug = 'colour-card-game'
          AND activity_deployments.status = 'deployed'
        """,
        (experience_id,),
    ).fetchone()


def card_state_for_participant(conn: sqlite3.Connection, participant_id: int) -> dict:
    experience = participant_experience(conn, participant_id)
    if not experience:
        return {"deployed": False, "assignments": [], "received": [], "participants": []}
    deployment = get_active_card_deployment(conn, int(experience["id"]))
    peers = [
        {"id": int(row["id"]), "full_name": row["full_name"]}
        for row in conn.execute(
            """
            SELECT participants.id, participants.full_name
            FROM participants
            JOIN learning_experience_members ON learning_experience_members.participant_id = participants.id
            WHERE learning_experience_members.learning_experience_id = ? AND participants.id <> ?
            ORDER BY participants.full_name
            """,
            (experience["id"], participant_id),
        ).fetchall()
    ]
    if not deployment:
        return {"deployed": False, "assignments": [], "received": [], "participants": peers}
    assignments = conn.execute(
        """
        SELECT card_assignments.id, card_assignments.participant_id, card_assignments.status, card_assignments.origin_participant_id,
               card_assignments.current_owner_participant_id, card_bank.colour, card_bank.content
        FROM card_assignments
        JOIN card_bank ON card_bank.id = card_assignments.card_id
        WHERE card_assignments.deployment_id = ?
          AND card_assignments.participant_id = ?
        ORDER BY card_bank.colour, card_assignments.id
        """,
        (deployment["id"], participant_id),
    ).fetchall()
    received = conn.execute(
        """
        SELECT card_assignments.id, card_assignments.status, card_bank.colour, card_bank.content,
               giver.full_name AS giver_name, card_transfers.created_at
        FROM card_assignments
        JOIN card_bank ON card_bank.id = card_assignments.card_id
        JOIN card_transfers ON card_transfers.assignment_id = card_assignments.id
        JOIN participants AS giver ON giver.id = card_transfers.from_participant_id
        WHERE card_assignments.deployment_id = ?
          AND card_assignments.current_owner_participant_id = ?
          AND card_assignments.origin_participant_id <> ?
        ORDER BY card_transfers.created_at DESC
        """,
        (deployment["id"], participant_id, participant_id),
    ).fetchall()
    kept = conn.execute(
        """
        SELECT card_assignments.id, card_bank.colour, card_bank.content
        FROM card_assignments
        JOIN card_bank ON card_bank.id = card_assignments.card_id
        WHERE card_assignments.deployment_id = ?
          AND card_assignments.participant_id = ?
          AND card_assignments.current_owner_participant_id = ?
          AND card_assignments.status = 'kept'
        ORDER BY card_bank.colour, card_assignments.id
        """,
        (deployment["id"], participant_id, participant_id),
    ).fetchall()
    return {
        "deployed": True,
        "deployment_id": int(deployment["id"]),
        "assignments": [dict(row) for row in assignments],
        "received": [dict(row) for row in received],
        "kept": [dict(row) for row in kept],
        "participants": peers,
    }


def keep_card(conn: sqlite3.Connection, *, participant_id: int, assignment_id: int) -> None:
    row = conn.execute(
        """
        SELECT * FROM card_assignments
        WHERE id = ? AND participant_id = ? AND current_owner_participant_id = ?
        """,
        (assignment_id, participant_id, participant_id),
    ).fetchone()
    if not row:
        raise Forbidden("That card is not currently available to this participant.")
    if row["status"] == "given":
        raise ValidationError("This card has already been given away.")
    conn.execute(
        """
        UPDATE card_assignments
        SET status = 'kept', decided_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (assignment_id,),
    )


def give_card(conn: sqlite3.Connection, *, participant_id: int, assignment_id: int, recipient_id: int) -> None:
    if participant_id == recipient_id:
        raise ValidationError("Cards cannot be given to yourself.")
    row = conn.execute(
        """
        SELECT card_assignments.*, activity_deployments.learning_experience_id
        FROM card_assignments
        JOIN activity_deployments ON activity_deployments.id = card_assignments.deployment_id
        WHERE card_assignments.id = ?
          AND card_assignments.participant_id = ?
          AND card_assignments.current_owner_participant_id = ?
        """,
        (assignment_id, participant_id, participant_id),
    ).fetchone()
    if not row:
        raise Forbidden("That card is not currently available to this participant.")
    if row["status"] == "given":
        raise ValidationError("This card has already been given away.")
    recipient_member = conn.execute(
        """
        SELECT 1
        FROM learning_experience_members
        WHERE learning_experience_id = ? AND participant_id = ?
        """,
        (row["learning_experience_id"], recipient_id),
    ).fetchone()
    if not recipient_member:
        raise Forbidden("Cards can only be given to someone in the same Learning Experience.")
    conn.execute(
        """
        UPDATE card_assignments
        SET status = 'given', current_owner_participant_id = ?, decided_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (recipient_id, assignment_id),
    )
    conn.execute(
        """
        INSERT INTO card_transfers(assignment_id, deployment_id, from_participant_id, to_participant_id)
        VALUES (?, ?, ?, ?)
        """,
        (assignment_id, row["deployment_id"], participant_id, recipient_id),
    )


def profile_from_row(row: sqlite3.Row) -> dict:
    profile = loads(row["profile_json"], {})
    profile["full_name"] = row["full_name"]
    profile["token"] = row["token"]
    profile["confidence"] = loads(row["parse_confidence_json"], {})
    profile["issues"] = loads(row["parse_issues_json"], [])
    return profile
