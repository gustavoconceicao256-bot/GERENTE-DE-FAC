"""Regressões para paginação universal de seletores de cargos e canais."""
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


class RoleChannelPaginationTests(unittest.TestCase):
    def test_shared_picker_uses_discord_safe_25_option_pages(self):
        picker = get_class("PaginatedGuildObjectPickerView")
        page_size = next(
            node.value.value for node in picker.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "PAGE_SIZE" for target in node.targets)
            and isinstance(node.value, ast.Constant)
        )
        self.assertEqual(page_size, 25)
        source = ast.unparse(picker)
        self.assertIn("self.previous_button", source)
        self.assertIn("self.next_button", source)
        self.assertIn("self.page = max(0, self.page - 1)", source)
        self.assertIn("self.page = min(self.page_count - 1, self.page + 1)", source)

    def test_picker_supports_roles_text_channels_and_categories(self):
        source = ast.unparse(get_class("PaginatedGuildObjectPickerView"))
        self.assertIn("'role'", source)
        self.assertIn("'text_channel'", source)
        self.assertIn("'category'", source)
        self.assertIn("self.guild.roles", source)
        self.assertIn("self.guild.text_channels", source)
        self.assertIn("self.guild.categories", source)

    def test_no_native_role_or_channel_entity_select_is_instantiated(self):
        calls = []
        for node in ast.walk(TREE):
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                if name in {"discord.ui.RoleSelect", "discord.ui.ChannelSelect"}:
                    calls.append((name, node.lineno))
        self.assertEqual(calls, [], f"Seletores sem paginação encontrados: {calls}")

    def test_old_configuration_buttons_open_the_shared_picker(self):
        for class_name in ("ConfigTextChannelSelect", "ConfigSingleRoleSelect"):
            node = get_class(class_name)
            bases = [ast.unparse(base) for base in node.bases]
            self.assertIn("discord.ui.Button", bases)
            source = ast.unparse(node)
            self.assertIn("PaginatedGuildObjectPickerView", source)
            self.assertIn("owner_id=interaction.user.id", source)

    def test_ticket_text_channel_selector_uses_shared_pagination(self):
        node = get_class("TicketTextChannelSelectV2")
        self.assertIn("PaginatedGuildObjectPickerView", [ast.unparse(base) for base in node.bases])
        source = ast.unparse(node)
        self.assertIn("kind='text_channel'", source)
        self.assertIn("TicketConfigV2", source)

    def test_ticket_category_and_staff_role_each_open_paginated_picker(self):
        source = ast.unparse(get_class("TicketCategorySelectV2"))
        self.assertIn("kind='category'", source)
        self.assertIn("kind='role'", source)
        self.assertIn("_open_category_picker", source)
        self.assertIn("_open_role_picker", source)

    def test_existing_wl_farm_and_hierarchy_wizards_keep_their_arrows(self):
        required_fragments = (
            "class WLPageButton",
            "class FarmConfigPageButton",
            "class HierarchyFinalChannelView",
            "class HierarchyRoleConfigView",
            "class ConfigHierarchySetupView",
            "label=\"◀ Anterior\"",
            "label=\"Próxima página ▶\"",
        )
        for fragment in required_fragments:
            self.assertIn(fragment, SOURCE)

    def test_saved_selection_is_kept_when_picker_reopens(self):
        source = ast.unparse(get_class("PaginatedGuildObjectPickerView"))
        self.assertIn("if self.selected_id:", source)
        self.assertIn("self.page = index // self.PAGE_SIZE", source)
        self.assertIn("default=(obj.id == parent_view.selected_id)", SOURCE)

    def test_hierarchy_channel_selectors_open_shared_picker(self):
        for class_name in ("ConfigHierarchyChannelSelect", "HierarchyPublishChannelSelect"):
            source = ast.unparse(get_class(class_name))
            self.assertIn("PaginatedGuildObjectPickerView", source)
            self.assertIn("kind='text_channel'", source)

    def test_saved_value_must_belong_to_the_current_picker_type(self):
        source = ast.unparse(get_class("PaginatedGuildObjectPickerView"))
        self.assertIn("return next((obj for obj in self.objects if obj.id == self.selected_id), None)", source)


if __name__ == "__main__":
    unittest.main()
