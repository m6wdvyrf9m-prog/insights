from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from insights_app import create_app
from insights_app.db import connect, transaction
from insights_app.security import participant_action_token
from insights_app.services import (
    Forbidden,
    create_learning_experience,
    deploy_card_game,
    get_participant_by_token,
    give_card,
    parse_and_store_upload,
    save_pdf_upload,
)

from .helpers import WSGIClient, make_config, make_sample_pdf


class AppFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = make_config(self.tmp)
        self.app = create_app(self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def test_token_authorization(self):
        pdf_path = make_sample_pdf(Path(self.tmp.name) / "token.pdf", name="Taylor Token")
        with transaction(self.config.database_path) as conn:
            stored_path, file_hash = save_pdf_upload(self.config.upload_dir, "token.pdf", pdf_path.read_bytes(), max_bytes=20_000_000)
            result = parse_and_store_upload(conn, stored_path=stored_path, original_filename="token.pdf", file_sha256=file_hash, created_by_user_id=None)
            participant = conn.execute("SELECT * FROM participants WHERE id = ?", (result.participant_id,)).fetchone()
        client = WSGIClient(self.app)
        status, _headers, body = client.get(f"/p/{participant['token']}")
        self.assertEqual(status, 200)
        self.assertIn(b"Taylor Token", body)
        bad_status, _headers, _body = client.get("/p/not-a-real-token")
        self.assertEqual(bad_status, 404)

    def test_admin_and_facilitator_role_boundaries(self):
        with transaction(self.config.database_path) as conn:
            admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()["id"]
            experience_id = create_learning_experience(
                conn,
                name="Boundary Cohort",
                facilitator_username="facilitator-one",
                facilitator_password="facilitator-password-123",
                created_by_user_id=admin_id,
            )
            other_experience_id = create_learning_experience(
                conn,
                name="Other Cohort",
                facilitator_username="facilitator-two",
                facilitator_password="facilitator-password-123",
                created_by_user_id=admin_id,
            )
        client = WSGIClient(self.app)
        status, _headers, _body = client.post_form("/facilitator/login", {"username": "facilitator-one", "password": "facilitator-password-123"})
        self.assertEqual(status, 302)
        status, _headers, _body = client.get("/admin")
        self.assertEqual(status, 403)
        status, _headers, body = client.get(f"/facilitator/experiences/{experience_id}")
        self.assertEqual(status, 200)
        self.assertIn(b"Boundary Cohort", body)
        status, _headers, _body = client.get(f"/facilitator/experiences/{other_experience_id}")
        self.assertEqual(status, 403)

    def test_bulk_upload_creates_learning_experience_and_participants(self):
        client = WSGIClient(self.app)
        status, _headers, _body = client.post_form("/admin/login", {"username": "admin", "password": "admin-password-123"})
        self.assertEqual(status, 302)
        csrf = self.current_csrf()
        first = make_sample_pdf(Path(self.tmp.name) / "first.pdf", name="First Person").read_bytes()
        second = make_sample_pdf(Path(self.tmp.name) / "second.pdf", name="Second Person").read_bytes()
        status, headers, _body = client.post_multipart(
            "/admin/experiences/new",
            {
                "csrf_token": csrf,
                "name": "Bulk Cohort",
                "facilitator_username": "bulk-facilitator",
                "facilitator_password": "facilitator-password-123",
            },
            [("pdfs", "first.pdf", first), ("pdfs", "second.pdf", second)],
        )
        self.assertEqual(status, 302)
        self.assertRegex(headers["Location"], r"/admin/experiences/\d+")
        with connect(self.config.database_path) as conn:
            count = conn.execute("SELECT COUNT(*) AS total FROM participants").fetchone()["total"]
            members = conn.execute("SELECT COUNT(*) AS total FROM learning_experience_members").fetchone()["total"]
        self.assertEqual(count, 2)
        self.assertEqual(members, 2)

    def test_card_deployment_is_idempotent_and_assigns_unique_cards_per_user(self):
        experience_id, participant_ids = self.create_experience_with_participants()
        with transaction(self.config.database_path) as conn:
            facilitator = conn.execute("SELECT facilitator_user_id FROM learning_experiences WHERE id = ?", (experience_id,)).fetchone()
            first_deployment = deploy_card_game(conn, experience_id=experience_id, actor_user_id=facilitator["facilitator_user_id"])
            second_deployment = deploy_card_game(conn, experience_id=experience_id, actor_user_id=facilitator["facilitator_user_id"])
            self.assertEqual(first_deployment, second_deployment)
            for participant_id in participant_ids:
                rows = conn.execute(
                    "SELECT card_id FROM card_assignments WHERE deployment_id = ? AND participant_id = ?",
                    (first_deployment, participant_id),
                ).fetchall()
                self.assertEqual(len(rows), 16)
                self.assertEqual(len({row["card_id"] for row in rows}), 16)

    def test_card_transfer_rules(self):
        experience_id, participant_ids = self.create_experience_with_participants()
        giver, receiver = participant_ids
        with transaction(self.config.database_path) as conn:
            facilitator = conn.execute("SELECT facilitator_user_id FROM learning_experiences WHERE id = ?", (experience_id,)).fetchone()
            deployment_id = deploy_card_game(conn, experience_id=experience_id, actor_user_id=facilitator["facilitator_user_id"])
            assignment_id = conn.execute(
                "SELECT id FROM card_assignments WHERE deployment_id = ? AND participant_id = ? LIMIT 1",
                (deployment_id, giver),
            ).fetchone()["id"]
            with self.assertRaises(Exception):
                give_card(conn, participant_id=giver, assignment_id=assignment_id, recipient_id=giver)
            give_card(conn, participant_id=giver, assignment_id=assignment_id, recipient_id=receiver)
            transferred = conn.execute("SELECT * FROM card_assignments WHERE id = ?", (assignment_id,)).fetchone()
            self.assertEqual(transferred["current_owner_participant_id"], receiver)
            self.assertEqual(transferred["status"], "given")
            with self.assertRaises(Forbidden):
                give_card(conn, participant_id=giver, assignment_id=assignment_id, recipient_id=receiver)

    def test_participant_card_api_requires_action_token(self):
        experience_id, participant_ids = self.create_experience_with_participants()
        with transaction(self.config.database_path) as conn:
            facilitator = conn.execute("SELECT facilitator_user_id FROM learning_experiences WHERE id = ?", (experience_id,)).fetchone()
            deployment_id = deploy_card_game(conn, experience_id=experience_id, actor_user_id=facilitator["facilitator_user_id"])
            participant = conn.execute("SELECT * FROM participants WHERE id = ?", (participant_ids[0],)).fetchone()
            assignment_id = conn.execute(
                "SELECT id FROM card_assignments WHERE deployment_id = ? AND participant_id = ? LIMIT 1",
                (deployment_id, participant_ids[0]),
            ).fetchone()["id"]
        client = WSGIClient(self.app)
        status, _headers, body = client.request("POST", f"/api/p/{participant['token']}/cards/{assignment_id}/keep", body=b"{}")
        self.assertEqual(status, 403)
        csrf = participant_action_token(self.config.secret_key, participant["id"], participant["token_hash"])
        status, _headers, body = client.request(
            "POST",
            f"/api/p/{participant['token']}/cards/{assignment_id}/keep",
            body=b"{}",
            headers={"HTTP_X_CSRF_TOKEN": csrf, "CONTENT_TYPE": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body.decode())["ok"])

    def create_experience_with_participants(self):
        paths = [
            make_sample_pdf(Path(self.tmp.name) / "one.pdf", name="One Participant"),
            make_sample_pdf(Path(self.tmp.name) / "two.pdf", name="Two Participant"),
        ]
        with transaction(self.config.database_path) as conn:
            admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()["id"]
            experience_id = create_learning_experience(
                conn,
                name="Card Cohort",
                facilitator_username=f"cards-{id(self)}",
                facilitator_password="facilitator-password-123",
                created_by_user_id=admin_id,
            )
            participant_ids = []
            for path in paths:
                stored_path, file_hash = save_pdf_upload(self.config.upload_dir, path.name, path.read_bytes(), max_bytes=20_000_000)
                result = parse_and_store_upload(
                    conn,
                    stored_path=stored_path,
                    original_filename=path.name,
                    file_sha256=file_hash,
                    created_by_user_id=admin_id,
                    learning_experience_id=experience_id,
                )
                participant_ids.append(result.participant_id)
        return experience_id, participant_ids

    def current_csrf(self) -> str:
        with connect(self.config.database_path) as conn:
            row = conn.execute("SELECT csrf_token FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
        return row["csrf_token"]


if __name__ == "__main__":
    unittest.main()
