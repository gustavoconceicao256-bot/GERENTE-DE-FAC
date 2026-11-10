from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


class AppearanceConfigLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        wanted_assignments = {
            "PANEL_BUTTON_STYLE_LABELS",
            "PANEL_BUTTON_STYLE_DEFAULTS",
            "APPEARANCE_PANEL_LABELS",
        }
        wanted_functions = {
            "appearance_style_setting_key",
            "normalize_panel_button_style",
            "panel_button_style_value",
        }
        nodes = []
        for node in TREE.body:
            if isinstance(node, ast.Assign):
                names = {ast.unparse(target) for target in node.targets}
                if names & wanted_assignments:
                    nodes.append(node)
            elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
                nodes.append(node)

        cls.saved_values = {}
        namespace = {
            "panel_setting": lambda key, default: cls.saved_values.get(key, default),
        }
        module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        exec(compile(module, "appearance-logic-tests", "exec"), namespace)
        cls.style_key = staticmethod(namespace["appearance_style_setting_key"])
        cls.normalize = staticmethod(namespace["normalize_panel_button_style"])
        cls.style_value = staticmethod(namespace["panel_button_style_value"])
        cls.style_labels = namespace["PANEL_BUTTON_STYLE_LABELS"]
        cls.style_defaults = namespace["PANEL_BUTTON_STYLE_DEFAULTS"]
        cls.panel_labels = namespace["APPEARANCE_PANEL_LABELS"]

    def test_appearance_menu_contains_only_wl_farm_and_tickets(self):
        self.assertEqual(set(self.panel_labels), {"wl", "farm", "ticket"})
        self.assertNotIn("hierarchy", self.panel_labels)

    def test_discord_native_button_colors_are_available(self):
        self.assertEqual(
            self.style_labels,
            {"primary": "Azul", "secondary": "Cinza", "success": "Verde", "danger": "Vermelho"},
        )

    def test_panel_style_keys_are_separate(self):
        self.assertEqual(self.style_key("wl"), "wl_panel_button_style")
        self.assertEqual(self.style_key("farm"), "farm_panel_button_style")
        self.assertEqual(self.style_key("ticket", "support"), "ticket_support_button_style")
        self.assertEqual(self.style_key("ticket", "denuncia"), "ticket_denuncia_button_style")
        self.assertEqual(self.style_key("ticket", "outros"), "ticket_outros_button_style")

    def test_ticket_style_requires_a_valid_action(self):
        with self.assertRaises(ValueError):
            self.style_key("ticket")
        with self.assertRaises(ValueError):
            self.style_key("ticket", "hierarchy")

    def test_invalid_color_falls_back_to_the_panel_default(self):
        self.assertEqual(self.normalize("#ff00ff", "success"), "success")
        self.assertEqual(self.normalize("RED", "danger"), "danger")
        self.assertEqual(self.normalize("primary"), "primary")

    def test_default_button_colors_preserve_existing_panel_semantics(self):
        self.assertEqual(self.style_defaults["wl"], "primary")
        self.assertEqual(self.style_defaults["farm"], "success")
        self.assertEqual(self.style_defaults["ticket_support"], "primary")
        self.assertEqual(self.style_defaults["ticket_denuncia"], "danger")
        self.assertEqual(self.style_defaults["ticket_outros"], "secondary")

    def test_saved_ticket_button_colors_are_independent(self):
        self.saved_values.update({
            "ticket_support_button_style": "success",
            "ticket_denuncia_button_style": "secondary",
            "ticket_outros_button_style": "primary",
        })
        self.assertEqual(self.style_value("ticket", "support"), "success")
        self.assertEqual(self.style_value("ticket", "denuncia"), "secondary")
        self.assertEqual(self.style_value("ticket", "outros"), "primary")

    def test_config_root_exposes_appearance_button_and_route(self):
        self.assertIn('custom_id="fac:configv2:appearance"', SOURCE)
        self.assertIn('elif action == "fac:configv2:appearance":', SOURCE)
        self.assertIn("AppearancePanelSelectV2(interaction.guild, interaction.user.id)", SOURCE)

    def test_appearance_editor_offers_only_requested_controls(self):
        start = SOURCE.index("class AppearanceEditorV2(")
        end = SOURCE.index("class AppearanceDescriptionModal(", start)
        editor = SOURCE[start:end]
        for required in (
            "Editar descrição",
            "Alterar banner",
            "Atualizar painel",
            "AppearanceButtonStyleSelect",
        ):
            self.assertIn(required, editor)
        self.assertNotIn("Editar título", editor)
        self.assertNotIn("foto / miniatura", editor)

    def test_selection_screen_does_not_offer_hierarchy(self):
        start = SOURCE.index("class AppearancePanelSelectV2(")
        end = SOURCE.index("class AppearanceButtonStyleSelect(", start)
        screen = SOURCE[start:end]
        self.assertIn('("WL", "📋", "wl")', screen)
        self.assertIn('("Farme", "🌾", "farm")', screen)
        self.assertIn('("Tickets", "🎫", "ticket")', screen)
        self.assertNotIn('"Hierarquia"', screen)

    def test_wl_and_farm_buttons_read_saved_appearance(self):
        self.assertIn('style=panel_button_style("wl")', SOURCE)
        self.assertIn('style=panel_button_style("farm")', SOURCE)

    def test_legacy_small_panel_thumbnail_is_not_applied(self):
        start = SOURCE.index("def apply_panel_media(")
        end = SOURCE.index("def panel_embed(", start)
        media_helper = SOURCE[start:end]
        self.assertIn('f"{kind}_panel_banner_url"', media_helper)
        self.assertIn("embed.set_image", media_helper)
        self.assertNotIn("set_thumbnail", media_helper)

    def test_farm_setup_sends_user_to_channel_setup_not_old_appearance_modal(self):
        start = SOURCE.index("class FarmItemSetupV2(")
        end = SOURCE.index("FARM_CONFIG_FIELDS =", start)
        setup = SOURCE[start:end]
        self.assertIn("ConfigFarmChannelsV2", setup)
        self.assertNotIn("ConfigFarmPanelModal", setup)

    def test_farm_description_is_not_overwritten_by_old_legacy_phrase_guard(self):
        start = SOURCE.index("def panel_embed(")
        end = SOURCE.index("def config_embed(", start)
        panel_builder = SOURCE[start:end]
        self.assertNotIn('save_config_value("farm_panel_description"', panel_builder)
        self.assertNotIn('"informe nome e ID"', panel_builder)

    def test_ticket_buttons_have_independent_saved_styles(self):
        ticket_view_start = SOURCE.index("class TicketPanelView(")
        ticket_view_end = SOURCE.index("class TicketControlsView(", ticket_view_start)
        ticket_view = SOURCE[ticket_view_start:ticket_view_end]
        self.assertIn('style=panel_button_style("ticket", ticket_type)', ticket_view)
        self.assertIn('"ticket_panel_description"', ticket_view)
        self.assertIn('"ticket_panel_banner_url"', ticket_view)

    def test_wl_farm_publisher_updates_existing_messages(self):
        self.assertIn("async def publish_or_update_simple_panel(", SOURCE)
        self.assertIn("await existing.edit(view=v2_view(panel_message, view, None))", SOURCE)
        self.assertIn('message_key = f"{kind}_panel_message_id"', SOURCE)
        self.assertIn('published_channel_key = f"{kind}_panel_published_channel_id"', SOURCE)
        self.assertIn('save_config_value(message_key, str(message.id))', SOURCE)

    def test_opening_appearance_reuses_the_existing_config_message(self):
        start = SOURCE.index('elif action == "fac:configv2:appearance":')
        end = SOURCE.index('elif action == "fac:configv2:publish-wl":', start)
        route = SOURCE[start:end]
        self.assertIn("interaction.response.edit_message(", route)
        self.assertIn("AppearancePanelSelectV2(interaction.guild, interaction.user.id)", route)

    def test_appearance_modals_do_not_spawn_duplicate_editor_panels(self):
        start = SOURCE.index("class AppearanceDescriptionModal(")
        end = SOURCE.index("# ---------------- MÓDULO DE TICKETS", start)
        modals = SOURCE[start:end]
        self.assertIn("Descrição salva", modals)
        self.assertIn("Banner salvo", modals)
        self.assertNotIn("view=AppearanceEditorV2(", modals)

    def test_old_wl_wizard_no_longer_has_a_separate_appearance_button(self):
        self.assertNotIn('custom_id="fac:wl-v2:appearance"', SOURCE)


if __name__ == "__main__":
    unittest.main()
