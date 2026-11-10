from __future__ import annotations

import ast
import sqlite3
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")


class DatabaseSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp_dir.name) / "test.sqlite3")
        tree = ast.parse(SOURCE)
        selected = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in {
                "db", "init_db", "setting", "hierarchy_rows",
                "clear_hierarchy_settings", "selectable_hierarchy_roles",
                "hierarchy_configuration_issue",
            }
        ]
        module = ast.Module(body=selected, type_ignores=[])
        namespace = {
            "sqlite3": sqlite3,
            "os": os,
            "contextmanager": contextmanager,
            "DB_PATH": self.database_path,
        }
        exec(compile(ast.fix_missing_locations(module), "<database-schema-test>", "exec"), namespace)
        self.db = namespace["db"]
        self.init_db = namespace["init_db"]
        self.hierarchy_rows = namespace["hierarchy_rows"]
        self.clear_hierarchy_settings = namespace["clear_hierarchy_settings"]
        self.hierarchy_configuration_issue = namespace["hierarchy_configuration_issue"]
        self.namespace = namespace

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_hierarchy_publish_readiness_gate(self):
        class Role:
            def __init__(self, role_id, name, position, managed=False):
                self.id = role_id
                self.name = name
                self.position = position
                self.managed = managed

            def __lt__(self, other):
                return self.position < other.position

        class Guild:
            def __init__(self):
                self.default_role = Role(1, "@everyone", 0)
                self.top_role = Role(99, "FAC Bot", 100)
                self.me = type("BotMember", (), {"top_role": self.top_role})()
                self.roles = [
                    self.default_role,
                    Role(10, "Alpha", 50),
                    Role(20, "Beta", 40),
                ]
                self._roles = {role.id: role for role in self.roles}

            def get_role(self, role_id):
                return self._roles.get(role_id)

        self.init_db()
        guild = Guild()
        with self.db() as connection:
            connection.executemany(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                [
                    ("hierarchy_role_count", "2"),
                    ("hierarchy_setup_in_progress", "0"),
                    ("rank_role_10", "ALPHA|2"),
                    ("rank_role_20", "BETA|1"),
                ],
            )
        self.assertIsNone(self.hierarchy_configuration_issue(guild))

        with self.db() as connection:
            connection.execute(
                "UPDATE settings SET value='1' WHERE key='hierarchy_setup_in_progress'"
            )
        self.assertIn("em andamento", self.hierarchy_configuration_issue(guild))

        with self.db() as connection:
            connection.execute(
                "UPDATE settings SET value='0' WHERE key='hierarchy_setup_in_progress'"
            )
            connection.execute(
                "UPDATE settings SET value='2' WHERE key='hierarchy_role_count'"
            )
            connection.execute(
                "UPDATE settings SET value='|1' WHERE key='rank_role_20'"
            )
        self.assertIn("faltam tags", self.hierarchy_configuration_issue(guild))

    def test_hierarchy_rows_are_highest_to_lowest(self):
        self.init_db()
        with self.db() as connection:
            connection.executemany(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                [
                    ("rank_role_10", "LOW|1"),
                    ("rank_role_20", "HIGH|3"),
                    ("rank_role_30", "MID|2"),
                ],
            )
        self.assertEqual(
            self.hierarchy_rows(),
            [(3, 20, "HIGH"), (2, 30, "MID"), (1, 10, "LOW")],
        )
        self.clear_hierarchy_settings()
        self.assertEqual(self.hierarchy_rows(), [])

    def test_schema_initializes_and_is_idempotent(self):
        self.init_db()
        self.init_db()
        with self.db() as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertTrue({
                "settings", "members", "registration_requests",
                "farm_items", "farm_entries", "farm_proofs",
                "farm_proof_pending", "farm_receipt_pending",
                "warnings", "actions", "blacklist", "tickets", "ticket_transcripts",
            }.issubset(tables))
            proof_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(farm_proofs)")
            }
            self.assertIn("receipt_data", proof_columns)
            self.assertIn("total_cents", proof_columns)
            ticket_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(tickets)")
            }
            self.assertTrue({"guild_id", "channel_id", "user_id", "ticket_type", "status", "claimed_by"}.issubset(ticket_columns))
            transcript_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(ticket_transcripts)")
            }
            self.assertTrue({"ticket_id", "file_name", "html_blob"}.issubset(transcript_columns))


if __name__ == "__main__":
    unittest.main()
