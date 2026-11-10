"""Testes da aplicação automática de tag da hierarquia após WL."""
from __future__ import annotations

import ast
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def function_node(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"Função não encontrada: {name}")


def load_function(name: str, namespace: dict):
    node = function_node(name)
    module = ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            node,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), f"<{name}-test>", "exec"), namespace)
    return namespace[name]


class FakeRole:
    def __init__(self, role_id: int, name: str):
        self.id = role_id
        self.name = name


class FakeGuild:
    def __init__(self, roles):
        self.roles_by_id = {role.id: role for role in roles}

    def get_role(self, role_id):
        return self.roles_by_id.get(role_id)


class FakeMember:
    def __init__(self, roles, guild):
        self.roles = roles
        self.guild = guild


class WhitelistNicknameTests(unittest.TestCase):
    def test_nickname_matches_requested_format_and_keeps_name_case(self):
        build = load_function("build_member_nickname", {})
        self.assertEqual(build("gustavo", "2908", "M"), "[M] gustavo | 2908")
        self.assertEqual(build("gustavo", "2908", "[GERENTE]"), "[GERENTE] gustavo | 2908")

    def test_long_nickname_is_limited_to_discord_32_character_limit_and_keeps_id(self):
        build = load_function("build_member_nickname", {})
        nickname = build("Nome muito comprido para o apelido", "12345678", "GERENTE GERAL")
        self.assertLessEqual(len(nickname), 32)
        self.assertTrue(nickname.startswith("[GERENTE GERA"))
        self.assertTrue(nickname.endswith(" | 12345678"))

    def test_newly_assigned_wl_role_is_included_when_selecting_hierarchy_tag(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_path = str(Path(temp.name) / "test.sqlite3")

        @contextmanager
        def db():
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            try:
                yield con
                con.commit()
            except Exception:
                con.rollback()
                raise
            finally:
                con.close()

        with db() as con:
            con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            con.executemany(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                [("rank_role_10", "MEMBRO|1"), ("rank_role_20", "GERENTE|2")],
            )

        member_role = FakeRole(10, "Membro")
        manager_role = FakeRole(20, "Gerente")
        guild = FakeGuild([member_role, manager_role])
        member = FakeMember([member_role], guild)
        highest = load_function(
            "highest_configured_rank",
            {"db": db, "MEMBER_ROLE_ID": 10},
        )

        role, tag = highest(member, extra_role_ids={20})
        self.assertEqual(role.id, 20)
        self.assertEqual(tag, "GERENTE")

    def test_whitelist_saves_active_member_before_syncing_nickname(self):
        source = ast.get_source_segment(SOURCE, function_node("finish_registration"))
        self.assertIsNotNone(source)
        insert_position = source.index("INSERT INTO members")
        sync_position = source.index("await sync_configured_member_nickname(")
        self.assertLess(insert_position, sync_position)
        self.assertIn("extra_role_ids=assigned_role_ids", source)
        self.assertIn("assigned_role_ids.add(approved_role.id)", source)

    def test_hierarchy_guide_shows_the_same_nickname_format(self):
        guide = ast.get_source_segment(SOURCE, next(
            node for node in TREE.body
            if isinstance(node, ast.ClassDef) and node.name == "HierarchyGuideV2"
        ))
        self.assertIn("[TAG] Nome RP | ID", guide)

    def test_config_panel_explains_automatic_nickname_and_role_tag(self):
        node = function_node("config_embed")
        source = ast.get_source_segment(SOURCE, node)
        self.assertIn("[TAG] Nome RP | ID", source)
        self.assertIn("Tag do cargo pós-WL", source)
        self.assertIn("Defina a tag desse cargo em Hierarquia", SOURCE)

    def test_role_change_listener_continues_to_resync_nickname(self):
        source = ast.get_source_segment(SOURCE, function_node("on_member_update"))
        self.assertIn("before_roles != after_roles", source)
        self.assertIn("sync_configured_member_nickname(after", source)


if __name__ == "__main__":
    unittest.main()
