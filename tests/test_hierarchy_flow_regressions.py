"""Regressões do fluxo guiado de hierarquia (Components V2)."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def get_class(name: str) -> ast.ClassDef:
    for node in TREE.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"Classe não encontrada: {name}")


def button_labels(class_name: str) -> list[str]:
    node = get_class(class_name)
    labels = []
    for item in node.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in item.decorator_list:
            if isinstance(decorator, ast.Call) and ast.unparse(decorator.func) == "discord.ui.button":
                for keyword in decorator.keywords:
                    if keyword.arg == "label":
                        labels.append(ast.literal_eval(keyword.value))
    return labels


class HierarchyFlowRegressionTests(unittest.TestCase):
    def test_tag_screen_has_only_three_clear_actions(self):
        self.assertEqual(
            button_labels("HierarchyTagConfigView"),
            ["Definir tag dos cargos", "Voltar", "Recomeçar tags"],
        )

    def test_tag_modal_replaces_existing_message_after_save(self):
        modal = get_class("HierarchyTagsModal")
        submit = next(node for node in modal.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_submit")
        body = ast.unparse(submit)
        self.assertIn("update_hierarchy_modal_message", body)
        self.assertIn("self.parent_view.message", body)
        self.assertIn("next_view = self.parent_view", body)

    def test_tag_restart_clears_only_the_current_block(self):
        view = get_class("HierarchyTagConfigView")
        restart = next(node for node in view.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "restart")
        body = ast.unparse(restart)
        self.assertIn("self.page * 5", body)
        self.assertIn("range(start, end)", body)
        self.assertIn("self.tags[idx] = \'\'", body)

    def test_stage_two_has_single_role_removal_and_manual_reorder(self):
        labels = button_labels("HierarchyRoleConfigView")
        self.assertIn("Apagar 1", labels)
        self.assertIn("Mudar posição", labels)
        self.assertIn("HierarchyRemoveRoleModal", SOURCE)
        self.assertIn("HierarchyMoveRoleModal", SOURCE)
        self.assertIn("self.parent_view.role_ids.pop(current - 1)", SOURCE)
        self.assertIn("self.parent_view.role_ids.insert(new - 1, moved)", SOURCE)

    def test_role_order_is_not_sorted_by_discord_role_position(self):
        role_view = get_class("HierarchyRoleConfigView")
        body = "\n".join(ast.unparse(node) for node in role_view.body)
        self.assertNotIn("role_ids.sort", body)
        self.assertIn("Não ordenar por role.position", SOURCE)

    def test_guide_has_edit_and_start_fresh_actions(self):
        guide = get_class("HierarchyGuideV2")
        source = ast.unparse(guide)
        self.assertIn("Editar hierarquia", source)
        self.assertIn("Começar do zero", source)
        self.assertNotIn("Apagar configuração", source)

    def test_count_modal_updates_the_message_that_opened_it(self):
        modal = get_class("HierarchyCountModal")
        body = ast.unparse(modal)
        self.assertIn("self.source_message", body)
        self.assertIn("update_hierarchy_modal_message", body)


if __name__ == "__main__":
    unittest.main()
