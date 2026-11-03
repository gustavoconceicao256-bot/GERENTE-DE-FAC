"""Auditoria estática da migração para Discord Components V2.

Estes testes não substituem um teste de integração em um servidor Discord real,
mas impedem que interfaces legadas sejam reintroduzidas acidentalmente.
"""
from __future__ import annotations

import ast
import copy
import unittest
from types import SimpleNamespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_FILE = ROOT / "bot.py"


class ComponentsV2MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = BOT_FILE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_no_legacy_message_views(self):
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [ast.unparse(base) for base in node.bases]
            self.assertNotIn("View", bases, f"View legado em {node.name}")
            self.assertNotIn("discord.ui.View", bases, f"View legado em {node.name}")

    def test_all_message_view_classes_are_v2(self):
        view_classes = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                bases = [ast.unparse(base) for base in node.bases]
                if any(base in {"View", "discord.ui.View", "ComponentsV2View", "ComponentsV2LayoutView", "discord.ui.LayoutView"} for base in bases):
                    view_classes.append((node.name, bases))
        self.assertTrue(view_classes)
        for name, bases in view_classes:
            if name == "ComponentsV2LayoutView":
                self.assertIn("discord.ui.LayoutView", bases)
            else:
                self.assertTrue(
                    any(base in {"ComponentsV2View", "ComponentsV2LayoutView"} for base in bases),
                    f"{name} não usa LayoutView/ComponentsV2View: {bases}",
                )

    def test_all_modals_use_v2_labels(self):
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [ast.unparse(base) for base in node.bases]
            if any(base in {"Modal", "discord.ui.Modal", "ComponentsV2Modal"} for base in bases):
                if node.name == "ComponentsV2Modal":
                    self.assertIn("Modal", bases)
                else:
                    self.assertIn("ComponentsV2Modal", bases, f"Modal legado em {node.name}")

    def test_panel_message_renders_text_without_embed(self):
        node = next(
            item for item in self.tree.body
            if isinstance(item, ast.ClassDef) and item.name == "PanelMessage"
        )
        isolated = ast.Module(body=[node], type_ignores=[])
        namespace = {"SimpleNamespace": SimpleNamespace, "copy": copy}
        exec(compile(ast.fix_missing_locations(isolated), "<PanelMessage-test>", "exec"), namespace)
        message = namespace["PanelMessage"](
            title="Título", description="Descrição", color=123
        )
        message.set_author(name="Admin")
        message.add_field("Campo", "Valor", inline=False)
        message.set_footer(text="Rodapé")
        rendered = message.to_text()
        self.assertIn("# Título", rendered)
        self.assertIn("**Admin**", rendered)
        self.assertIn("**Campo**\nValor", rendered)
        self.assertNotIn(r"\\n", rendered)
        self.assertIn("\n\n", rendered)
        self.assertIn("-# Rodapé", rendered)
        self.assertLessEqual(len(rendered), 4000)
        copied = message.copy()
        copied.set_image(url="https://example.com/banner.png")
        self.assertIsNone(message.image.url)
        self.assertEqual(copied.image.url, "https://example.com/banner.png")

    def test_no_discord_embed_objects_remain(self):
        self.assertNotIn("discord.Embed", self.source)
        self.assertIn("class PanelMessage", self.source)
        self.assertIn("def set_panel_message", self.source)
        self.assertIn("view.set_panel_message(panel_message)", self.source)

    def test_message_operations_always_send_a_layout(self):
        checked = 0
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = ast.unparse(node.func)
            is_message_edit = (
                func.endswith(".edit") and not any(
                    keyword.arg == "nick" for keyword in node.keywords
                )
            ) or func.endswith("edit_original_response")
            if not any(func.endswith(suffix) for suffix in ("send_message", ".send", "edit_message")) and not is_message_edit:
                continue
            checked += 1
            kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            self.assertIn("view", kwargs, f"Mensagem sem LayoutView na linha {node.lineno}: {func}")
            self.assertNotIn("embed", kwargs, f"Embed legado na linha {node.lineno}: {func}")
            if "embeds" in kwargs:
                self.assertIsInstance(kwargs["embeds"], ast.List)
                self.assertEqual(kwargs["embeds"].elts, [], f"Embed não vazio na linha {node.lineno}")
        self.assertGreater(checked, 0)

    def test_decorated_button_callbacks_keep_expected_signature(self):
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(
                isinstance(decorator, ast.Call)
                and ast.unparse(decorator.func) == "discord.ui.button"
                for decorator in node.decorator_list
            ):
                continue
            args = [arg.arg for arg in node.args.args]
            self.assertEqual(len(args), 3, f"Assinatura inválida em {node.name}: {args}")

    def test_v2_button_adapter_dispatches_component_argument(self):
        self.assertIn("async def _dispatch(interaction, callback=bound_callback, component=item):", self.source)
        self.assertIn("await callback(interaction, component)", self.source)
        self.assertIn("item.callback = _dispatch", self.source)

    def test_modal_inputs_are_wrapped_in_label_components(self):
        self.assertIn("discord.ui.Label(text=str(label_text)[:45], component=item)", self.source)
        self.assertIn("if isinstance(item, TextInput):", self.source)

    def test_native_discord_select_types_are_supported(self):
        for select_type in ("Select", "ChannelSelect", "RoleSelect", "UserSelect", "MentionableSelect"):
            self.assertIn(f"discord.ui.{select_type}", self.source)
        self.assertIn("isinstance(item, V2_SELECT_TYPES)", self.source)

    def test_action_rows_are_reordered_after_dynamic_additions(self):
        self.assertIn("def _sync_action_row_order", self.source)
        self.assertIn("self._sync_action_row_order()", self.source)
        self.assertIn("for _key, row in sorted(self._v2_rows.items()", self.source)

    def test_long_running_interactions_defer_before_followups(self):
        for function_name in (
            "finish_registration",
            "publish_configured_panel",
            "promover",
            "rebaixar",
            "nickname",
            "adv",
            "pd",
        ):
            start = self.source.find(f"async def {function_name}(")
            self.assertGreaterEqual(start, 0, function_name)
            next_def = self.source.find("\nasync def ", start + 10)
            next_class = self.source.find("\nclass ", start + 10)
            ends = [position for position in (next_def, next_class) if position >= 0]
            end = min(ends) if ends else len(self.source)
            body = self.source[start:end]
            self.assertIn("interaction.response.defer", body, function_name)

    def test_wl_processing_removes_temporary_role_on_all_outcomes(self):
        self.assertIn('await member.remove_roles(pending_role, reason=f"WL #{request_id} processada")', self.source)
        self.assertIn('await expired_member.remove_roles(pending_role, reason=f"WL #{row[\'id\']} expirada")', self.source)

    def test_wl_expiration_updates_v2_messages(self):
        self.assertIn("if msg.embeds or msg.components:", self.source)

    def test_incomplete_hierarchy_setup_cannot_be_published(self):
        self.assertIn('save_config_value("hierarchy_setup_in_progress", "1")', self.source)
        self.assertIn('setting("hierarchy_setup_in_progress", "0") == "1"', self.source)
        self.assertIn("hierarchy_setup_in_progress", self.source)

    def test_hierarchy_publish_checks_readiness_and_keeps_old_posts_until_success(self):
        helper_start = self.source.find("async def publish_configured_panel")
        hierarchy_branch = self.source.find('elif kind == "hierarchy":', helper_start)
        send_start = self.source.find("await target.send(", hierarchy_branch)
        delete_start = self.source.find("await old_message.delete()", hierarchy_branch)
        self.assertGreaterEqual(hierarchy_branch, 0)
        self.assertGreater(send_start, hierarchy_branch)
        self.assertGreater(delete_start, send_start)

        final_view_start = self.source.find("class HierarchyFinalChannelView")
        publish_start = self.source.find("async def publish(", final_view_start)
        readiness_start = self.source.find("hierarchy_configuration_issue(self.guild)", publish_start)
        defer_start = self.source.find("interaction.response.defer", publish_start)
        helper_call = self.source.find('publish_configured_panel(interaction, "hierarchy"', publish_start)
        self.assertGreater(readiness_start, publish_start)
        self.assertGreater(defer_start, readiness_start)
        self.assertGreater(helper_call, defer_start)

    def test_hierarchy_publication_requires_all_roles_and_tags(self):
        self.assertIn("def hierarchy_configuration_issue", self.source)
        self.assertIn("if expected_count and len(rows) != expected_count:", self.source)
        self.assertIn("missing_tags = [str(role_id) for _priority, role_id, abbreviation in rows", self.source)
        self.assertIn("selectable_ids = {role.id for role in selectable_hierarchy_roles(guild)}", self.source)

    def test_hierarchy_only_offers_assignable_roles(self):
        self.assertIn("def selectable_hierarchy_roles", self.source)
        self.assertIn("and not role.managed", self.source)
        self.assertIn("role < bot_member.top_role", self.source)
        self.assertIn("selectable_ids = {role.id for role in selectable_hierarchy_roles(self.guild)}", self.source)

    def test_hierarchy_persistence_removes_stale_roles(self):
        self.assertIn("DELETE FROM settings WHERE key LIKE 'rank_role_%'", self.source)
        self.assertIn("DELETE FROM settings WHERE key LIKE 'hierarchy_draft_tag_%'", self.source)

    def test_hierarchy_and_tag_pagination_limits(self):
        self.assertIn("HIERARCHY_MAX_ROLES = 100", self.source)
        self.assertIn("HIERARCHY_ROLES_PER_PAGE = 25", self.source)
        self.assertIn("return max(1, (len(self.role_ids) + 4) // 5)", self.source)
        self.assertIn("Progresso: {done}/{len(self.tags)}", self.source)

    def test_container_helper_obeys_discord_limit(self):
        self.assertIn("def add_v2_containers", self.source)
        self.assertIn("if limit < 1 or limit > 10:", self.source)
        self.assertIn("view.add_item(discord.ui.Container(*components[offset:offset + limit]))", self.source)

    def test_setup_hook_is_installed_as_lifecycle_method_not_event(self):
        setup_start = self.source.find("async def setup_hook(")
        self.assertGreaterEqual(setup_start, 0)
        prefix = self.source[max(0, setup_start - 40):setup_start]
        self.assertNotIn("@bot.event", prefix)
        self.assertIn("bot.setup_hook = setup_hook", self.source[setup_start:])


if __name__ == "__main__":
    unittest.main()
