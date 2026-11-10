from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


class TicketConfigLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        selected = []
        for node in TREE.body:
            if isinstance(node, ast.Assign):
                targets = [ast.unparse(target) for target in node.targets]
                if "TICKET_TYPES" in targets:
                    selected.append(node)
            if isinstance(node, ast.FunctionDef) and node.name in {
                "ticket_setting_complete", "ticket_user_limit"
            }:
                selected.append(node)
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), "ticket-tests", "exec"), namespace)
        cls.ticket_setting_complete = staticmethod(namespace["ticket_setting_complete"])
        cls.ticket_user_limit = staticmethod(namespace["ticket_user_limit"])
        cls.ticket_types = namespace["TICKET_TYPES"]

    def test_all_required_ticket_settings_are_accepted(self):
        values = {
            "ticket_panel_channel_id": "100",
            "ticket_log_channel_id": "101",
        }
        for data in self.ticket_types.values():
            values[data["category_key"]] = "200"
            values[data["role_key"]] = "300"
        self.assertEqual(self.ticket_setting_complete(values), [])

    def test_each_missing_setting_is_reported(self):
        self.assertGreaterEqual(len(self.ticket_setting_complete({})), 8)

    def test_invalid_ids_are_rejected(self):
        values = {
            "ticket_panel_channel_id": "abc",
            "ticket_log_channel_id": "101",
        }
        for data in self.ticket_types.values():
            values[data["category_key"]] = "200"
            values[data["role_key"]] = "300"
        self.assertIn("canal onde o painel será publicado", self.ticket_setting_complete(values))

    def test_per_user_limit_is_clamped_safely(self):
        self.assertEqual(self.ticket_user_limit("2"), 2)
        self.assertEqual(self.ticket_user_limit("0"), 1)
        self.assertEqual(self.ticket_user_limit("99"), 10)
        self.assertEqual(self.ticket_user_limit("invalid"), 2)

    def test_config_panel_exposes_ticket_configuration_and_publication(self):
        self.assertIn('custom_id="fac:configv2:tickets"', SOURCE)
        self.assertIn('custom_id="fac:configv2:publish-tickets"', SOURCE)
        self.assertIn('name="painel-tickets"', SOURCE)

    def test_ticket_actions_use_persistent_custom_ids(self):
        self.assertIn('custom_id=f"fac:ticket:open:{ticket_type}"', SOURCE)
        for custom_id in (
            "fac:ticket:claim",
            "fac:ticket:close",
            "fac:ticket:reopen",
        ):
            self.assertIn(custom_id, SOURCE)

    def test_category_picker_owns_its_selection_summary_method(self):
        tree = ast.parse(SOURCE)
        category_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "TicketCategorySelectV2"
        )
        methods = {node.name for node in category_class.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertIn("_refresh_selection_text", methods)
        text_channel_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "TicketTextChannelSelectV2"
        )
        text_methods = {node.name for node in text_channel_class.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertNotIn("_refresh_selection_text", text_methods)

    def test_transcript_access_checks_owner_or_team(self):
        self.assertIn("is_owner or is_staff", SOURCE)
        self.assertIn("ticket-transcript", SOURCE)
        self.assertIn("ticket_transcripts", SOURCE)


if __name__ == "__main__":
    unittest.main()
