"""Bot consolidado para gerenciamento de facções FiveM RP via Discord.
Configure as variáveis de ambiente no arquivo .env antes de iniciar.
"""
from __future__ import annotations

import os
import copy
import sqlite3
import logging
import asyncio
import math
import re
import io
import html as html_escape
from urllib.parse import urlparse
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional
from types import SimpleNamespace

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Modal, TextInput
from dotenv import load_dotenv


class ComponentsV2Modal(Modal):
    """Modal com campos TextInput sempre embrulhados em Label (modal Components V2).

    Preserva os atributos TextInput já usados pelos handlers existentes, mas
    serializa cada campo como Label + TextInput, padrão atual da API do Discord.
    """
    @staticmethod
    def _wrap_text_input(item: TextInput):
        # O label pertence ao componente Label em modais V2; TextInput.label
        # é legado/depreciado e deve ser omitido no payload.
        label_text = getattr(getattr(item, "_underlying", None), "label", None) or "Campo"
        item._underlying.label = None
        return discord.ui.Label(text=str(label_text)[:45], component=item)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Modal._init_children instancia os campos declarados na classe sem
        # passar por add_item; convertemos esses campos após a inicialização.
        for child in list(self.children):
            if isinstance(child, TextInput):
                self.remove_item(child)
                super().add_item(self._wrap_text_input(child))

    def add_item(self, item):
        # Também cobre TextInputs criados dinamicamente em __init__.
        if isinstance(item, TextInput):
            item = self._wrap_text_input(item)
        return super().add_item(item)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("Erro em formulário Components V2", exc_info=error)
        try:
            message_view = v2_view(None, None, "O formulário encontrou um erro. Revise os dados e tente novamente.")
            if interaction.response.is_done():
                await interaction.followup.send(None, ephemeral=True, view=message_view)
            else:
                await interaction.response.send_message(None, ephemeral=True, view=message_view)
        except discord.HTTPException:
            log.exception("Não foi possível responder ao erro do formulário")


class PanelMessage:
    """Modelo interno de conteúdo para TextDisplay/MediaGallery (não é um Embed)."""

    def __init__(self, title=None, description=None, *, color=None, timestamp=None):
        self.title = str(title)[:256] if title is not None else None
        self.description = str(description)[:4096] if description is not None else None
        self.color = color
        self.timestamp = timestamp
        self.fields = []
        self.author = SimpleNamespace(name=None, icon_url=None)
        self.footer = SimpleNamespace(text=None, icon_url=None)
        self.image = SimpleNamespace(url=None)
        self.thumbnail = SimpleNamespace(url=None)

    def add_field(self, name, value, inline=True):
        self.fields.append(SimpleNamespace(name=str(name), value=str(value), inline=bool(inline)))
        return self

    def set_author(self, *, name=None, icon_url=None, url=None):
        self.author = SimpleNamespace(name=name, icon_url=icon_url, url=url)
        return self

    def set_footer(self, text=None, *, icon_url=None):
        self.footer = SimpleNamespace(text=text, icon_url=icon_url)
        return self

    def set_image(self, *, url=None):
        self.image = SimpleNamespace(url=url)
        return self

    def set_thumbnail(self, *, url=None):
        self.thumbnail = SimpleNamespace(url=url)
        return self

    def copy(self):
        return copy.deepcopy(self)

    def to_text(self) -> str:
        parts = []
        if self.author and self.author.name:
            parts.append(f"**{self.author.name}**")
        if self.title:
            parts.append(f"# {self.title}")
        if self.description:
            parts.append(self.description)
        for field in self.fields:
            parts.append(f"**{field.name}**\n{field.value}")
        if self.footer and self.footer.text:
            parts.append(f"-# {self.footer.text}")
        if self.timestamp:
            parts.append(f"-# {self.timestamp.strftime('%d/%m/%Y %H:%M UTC')}")
        return "\n\n".join(parts)[:4000] or "‎"


# ---------------- DISCORD COMPONENTS V2 ----------------
V2_SELECT_TYPES = (
    discord.ui.Select,
    discord.ui.ChannelSelect,
    discord.ui.RoleSelect,
    discord.ui.UserSelect,
    discord.ui.MentionableSelect,
)


class ComponentsV2LayoutView(discord.ui.LayoutView):
    """Base comum de LayoutView com tratamento de erro para todas as telas V2."""

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        log.exception(
            "Erro em interação Components V2 (%s)",
            type(item).__name__ if item is not None else "componente desconhecido",
            exc_info=error,
        )
        try:
            message_view = v2_view(
                None, None,
                "Essa ação encontrou um erro. Tente novamente; se persistir, avise a administração.",
            )
            if interaction.response.is_done():
                await interaction.followup.send(None, ephemeral=True, view=message_view)
            else:
                await interaction.response.send_message(None, ephemeral=True, view=message_view)
        except discord.HTTPException:
            log.exception("Não foi possível responder ao erro da interação")


class ComponentsV2View(ComponentsV2LayoutView):
    """LayoutView compatível com a API antiga do projeto, mas publica somente componentes V2.

    Botões e menus são agrupados em ActionRows dentro de um Container. O texto
    substitui embeds e é renderizado como TextDisplay; assim o Discord recebe
    exclusivamente componentes V2 (flag IS_COMPONENTS_V2).
    """
    def __init__(self, *, timeout: Optional[float] = 180.0):
        super().__init__(timeout=timeout)
        self._v2_text = discord.ui.TextDisplay("‎")
        self._v2_container = discord.ui.Container(self._v2_text)
        self._v2_rows: dict[int, discord.ui.ActionRow] = {}
        self._v2_media_gallery = None
        super().add_item(self._v2_container)
        self._install_decorated_callbacks()

    def _install_decorated_callbacks(self):
        """Adapta @discord.ui.button/@select tradicionais para ActionRows V2."""
        installed = set()
        for base in type(self).__mro__:
            for name, member in base.__dict__.items():
                model_type = getattr(member, "__discord_ui_model_type__", None)
                model_kwargs = getattr(member, "__discord_ui_model_kwargs__", None)
                if model_type is None or model_kwargs is None or name in installed:
                    continue
                item = model_type(**model_kwargs)
                bound_callback = getattr(self, name)

                # LayoutView não cria o wrapper de callback que View tradicional
                # instalava para métodos @discord.ui.button. O wrapper abaixo
                # encaminha explicitamente (interaction, componente).
                async def _dispatch(interaction, callback=bound_callback, component=item):
                    await callback(interaction, component)

                item.callback = _dispatch

                # Preserva a API usada pelo restante do projeto:
                # self.next.disabled, self.approve.label etc.
                setattr(self, name, item)
                self.add_item(item)
                installed.add(name)

    @staticmethod
    def _row_key(item) -> int:
        row = getattr(item, "row", None)
        return int(row) if row is not None else 99

    def _sync_action_row_order(self):
        # Métodos @button são instalados durante super().__init__, antes de
        # alguns menus adicionados no __init__ da subclasse. Reordenamos os
        # ActionRows pelo índice lógico para não mostrar botões antes dos menus.
        for row in self._v2_rows.values():
            self._v2_container.remove_item(row)
        for _key, row in sorted(self._v2_rows.items(), key=lambda pair: pair[0]):
            self._v2_container.add_item(row)

    def add_item(self, item):
        # Layout primitives and containers are already valid V2 children.
        if isinstance(item, (discord.ui.Container, discord.ui.TextDisplay,
                             discord.ui.Separator, discord.ui.Section,
                             discord.ui.MediaGallery, discord.ui.File)):
            self._v2_container.add_item(item)
            return self

        # Interactive components must be placed in ActionRows.
        if isinstance(item, discord.ui.Button) or isinstance(item, V2_SELECT_TYPES):
            key = self._row_key(item)
            row = self._v2_rows.get(key)
            item_is_select = isinstance(item, V2_SELECT_TYPES)
            row_has_select = bool(row and row.children and isinstance(row.children[0], V2_SELECT_TYPES))
            incompatible = row is not None and (
                len(row.children) >= 5
                or (item_is_select and len(row.children) > 0)
                or row_has_select
            )
            if row is None or incompatible:
                key = max(self._v2_rows.keys(), default=-1) + 1
                row = discord.ui.ActionRow()
                self._v2_rows[key] = row
                self._v2_container.add_item(row)
            row.add_item(item)
            self._sync_action_row_order()
            return self

        # Preserve support for existing code that adds ActionRows directly.
        if isinstance(item, discord.ui.ActionRow):
            key = max(self._v2_rows.keys(), default=-1) + 1
            self._v2_rows[key] = item
            self._sync_action_row_order()
            return self
        raise TypeError(f"Componente não suportado em Components V2: {type(item).__name__}")

    def remove_item(self, item):
        for key, row in list(self._v2_rows.items()):
            if item in row.children:
                row.remove_item(item)
                if not row.children:
                    self._v2_container.remove_item(row)
                    self._v2_rows.pop(key, None)
                break
        return self

    def clear_items(self):
        for row in list(self._v2_rows.values()):
            self._v2_container.remove_item(row)
        self._v2_rows.clear()
        return self

    def set_text(self, text: str):
        text = str(text or "").strip() or "‎"
        # TextDisplay content can be updated in place, preserving component order.
        if self._v2_text is None:
            self._v2_text = discord.ui.TextDisplay(text[:4000])
            self._v2_container.add_item(self._v2_text)
        else:
            self._v2_text.content = text[:4000]
        return self

    def set_panel_message(self, panel_message: Optional[PanelMessage]):
        if panel_message is None:
            return self
        self.set_text(panel_message.to_text())
        if panel_message.color:
            self._v2_container.accent_colour = panel_message.color
        media_urls = []
        if panel_message.image and panel_message.image.url:
            media_urls.append(panel_message.image.url)
        if panel_message.thumbnail and panel_message.thumbnail.url:
            media_urls.append(panel_message.thumbnail.url)
        if self._v2_media_gallery is not None:
            self._v2_container.remove_item(self._v2_media_gallery)
            self._v2_media_gallery = None
        if media_urls:
            self._v2_media_gallery = discord.ui.MediaGallery(
                *(discord.MediaGalleryItem(url) for url in media_urls[:10])
            )
            self._v2_container.add_item(self._v2_media_gallery)
        self._sync_action_row_order()
        return self

    def set_message_content(self, content: Optional[str]):
        if content:
            current = getattr(self._v2_text, "content", "") if self._v2_text else ""
            return self.set_text((str(content) + ("\n\n" + current if current else ""))[:4000])
        return self




def v2_view(panel_message=None, view=None, content=None):
    """Monta uma mensagem Components V2 a partir do conteúdo textual e mídia."""
    if isinstance(view, ComponentsV2View):
        view.set_panel_message(panel_message)
        view.set_message_content(content)
        return view
    if isinstance(view, discord.ui.LayoutView):
        # LayoutViews já construídas permanecem intactas; o conteúdo adicional
        # é anexado em um Container independente, sem payload de embed/content.
        if panel_message is not None or content:
            text_parts = []
            if content:
                text_parts.append(str(content))
            if panel_message is not None:
                text_parts.append(panel_message.to_text())
            block = discord.ui.Container(
                discord.ui.TextDisplay("\n\n".join(text_parts)[:4000] or "‎")
            )
            media_urls = []
            if panel_message is not None:
                if panel_message.image and panel_message.image.url:
                    media_urls.append(panel_message.image.url)
                if panel_message.thumbnail and panel_message.thumbnail.url:
                    media_urls.append(panel_message.thumbnail.url)
                if panel_message.color:
                    block.accent_colour = panel_message.color
            if media_urls:
                block.add_item(discord.ui.MediaGallery(
                    *(discord.MediaGalleryItem(url) for url in media_urls[:10])
                ))
            view.add_item(block)
        return view
    result = ComponentsV2View(timeout=getattr(view, "timeout", 180.0))
    result.set_panel_message(panel_message)
    result.set_message_content(content)
    if view is not None:
        for child in list(getattr(view, "children", [])):
            result.add_item(child)
    return result


def add_v2_containers(view: discord.ui.LayoutView, components: list, *, limit: int = 10):
    """Adiciona os blocos em containers válidos (máximo de 10 filhos por container)."""
    if limit < 1 or limit > 10:
        raise ValueError("O limite de filhos por Container deve ficar entre 1 e 10.")
    for offset in range(0, len(components), limit):
        view.add_item(discord.ui.Container(*components[offset:offset + limit]))
    return view


def message_component_text(message: Optional[discord.Message]) -> str:
    """Extrai textos de TextDisplay/Container V2 e mantém compatibilidade com mensagens antigas."""
    if message is None:
        return ""
    parts: list[str] = []

    def walk(component):
        content = getattr(component, "content", None)
        if isinstance(content, str) and content:
            parts.append(content)
        for child in getattr(component, "children", ()) or ():
            walk(child)

    try:
        for component in getattr(message, "components", ()) or ():
            walk(component)
    except (AttributeError, TypeError):
        pass
    # Compatibilidade com mensagens legadas que ainda estejam no histórico.
    for embed in getattr(message, "embeds", ()) or ():
        if embed.title:
            parts.append(embed.title)
        if embed.description:
            parts.append(embed.description)
        for field in embed.fields:
            parts.append(f"{field.name}\n{field.value}")
        if embed.footer and embed.footer.text:
            parts.append(embed.footer.text)
    return "\n".join(parts)


def message_marker_id(message: Optional[discord.Message], marker: str) -> Optional[int]:
    text = message_component_text(message)
    match = re.search(re.escape(marker) + r"(\d+)", text)
    return int(match.group(1)) if match else None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ---------------- CONFIGURAÇÃO ----------------
TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
FACTION_NAME = re.sub(r"\bGTT\b", "", os.getenv("FACTION_NAME", "Facção RP"), flags=re.IGNORECASE).strip(" -—|") or "Facção RP"
DB_PATH = os.getenv("DATABASE_PATH", "").strip() or os.path.join(BASE_DIR, "fac_control.sqlite3")
if not os.path.isabs(DB_PATH):
    DB_PATH = os.path.join(BASE_DIR, DB_PATH)

MEMBER_ROLE_ID = int(os.getenv("MEMBER_ROLE_ID", "1523371849487220828") or 1523371849487220828)
RECRUIT_ROLE_ID = int(os.getenv("RECRUIT_ROLE_ID", "1461194746235195525") or 1461194746235195525)
LEADER_ROLE_ID = int(os.getenv("LEADER_ROLE_ID", "0") or 0)
DEVELOPER_ROLE_ID = int(os.getenv("DEVELOPER_ROLE_ID", "1460722337937887382") or 1460722337937887382)
ADV1_ROLE_ID = int(os.getenv("ADV1_ROLE_ID", "0") or 0)
ADV2_ROLE_ID = int(os.getenv("ADV2_ROLE_ID", "0") or 0)
ADV3_ROLE_ID = int(os.getenv("ADV3_ROLE_ID", "0") or 0)

REGISTRATION_CHANNEL_ID = int(os.getenv("REGISTRATION_CHANNEL_ID", "0") or 0)
APPROVAL_CHANNEL_ID = int(os.getenv("APPROVAL_CHANNEL_ID", "0") or 0)
ADMIN_LOG_CHANNEL_ID = int(os.getenv("ADMIN_LOG_CHANNEL_ID", "0") or 0)
PUBLIC_LOG_CHANNEL_ID = int(os.getenv("PUBLIC_LOG_CHANNEL_ID", "0") or 0)
FARM_LOG_CHANNEL_ID = int(os.getenv("FARM_LOG_CHANNEL_ID", "0") or 0)

FARM_COOLDOWN_SECONDS = max(0, int(os.getenv("FARM_COOLDOWN_SECONDS", "30") or 30))
MAX_FARM_ENTRIES_PER_DAY = max(1, int(os.getenv("MAX_FARM_ENTRIES_PER_DAY", "50") or 50))
TIMEZONE_OFFSET_HOURS = int(os.getenv("TIMEZONE_OFFSET_HOURS", "-3") or -3)
AUTO_PD_ON_ADV3 = os.getenv("AUTO_PD_ON_ADV3", "false").strip().lower() in {"1", "true", "yes", "sim"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("fac-control")

# ---------------- BANCO DE DADOS ----------------
@contextmanager
def db():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS registration_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT NOT NULL,
            discord_name TEXT NOT NULL,
            character_name TEXT NOT NULL,
            game_id TEXT NOT NULL,
            phone TEXT DEFAULT '',
            recruiter TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            processed_by TEXT DEFAULT '',
            processed_at TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            selected_recruiter_id TEXT DEFAULT '',
            deadline_at TEXT DEFAULT '',
            message_id TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS members (
            discord_id TEXT PRIMARY KEY,
            discord_name TEXT NOT NULL,
            character_name TEXT NOT NULL,
            game_id TEXT NOT NULL UNIQUE,
            phone TEXT DEFAULT '',
            recruiter TEXT DEFAULT '',
            rank_name TEXT DEFAULT 'Membro',
            slot_number INTEGER UNIQUE,
            joined_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE'
        );
        CREATE TABLE IF NOT EXISTS farm_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            points INTEGER NOT NULL DEFAULT 1,
            target INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            unit_price_cents INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS farm_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            points INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS farm_proof_pending (
            discord_id TEXT PRIMARY KEY,
            character_name TEXT NOT NULL,
            game_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            item_name TEXT DEFAULT '',
            quantity INTEGER DEFAULT 0,
            points INTEGER DEFAULT 0,
            channel_id TEXT DEFAULT '',
            unit_price_cents INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS farm_proofs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT NOT NULL,
            character_name TEXT NOT NULL,
            game_id TEXT NOT NULL,
            image_data BLOB NOT NULL,
            image_filename TEXT NOT NULL,
            image_mime TEXT NOT NULL,
            source_url TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            reviewed_by TEXT DEFAULT '',
            reviewed_at TEXT DEFAULT '',
            review_reason TEXT DEFAULT '',
            review_channel_id TEXT DEFAULT '',
            review_message_id TEXT DEFAULT '',
            item_name TEXT DEFAULT '',
            quantity INTEGER DEFAULT 0,
            points INTEGER DEFAULT 0,
            unit_price_cents INTEGER DEFAULT 0,
            total_cents INTEGER DEFAULT 0,
            paid_by TEXT DEFAULT '',
            paid_at TEXT DEFAULT '',
            receipt_data BLOB,
            receipt_filename TEXT DEFAULT '',
            receipt_mime TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS farm_receipt_pending (
            reviewer_id TEXT PRIMARY KEY,
            proof_id INTEGER NOT NULL,
            channel_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id TEXT NOT NULL,
            target_id TEXT DEFAULT '',
            action TEXT NOT NULL,
            reason TEXT DEFAULT '',
            details TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS blacklist (
            game_id TEXT PRIMARY KEY,
            reason TEXT NOT NULL,
            added_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT NOT NULL,
            level INTEGER NOT NULL,
            reason TEXT NOT NULL,
            issued_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            channel_id TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            ticket_type TEXT NOT NULL,
            subject TEXT NOT NULL,
            description TEXT NOT NULL,
            target_name TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'OPEN',
            claimed_by TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            closed_at TEXT DEFAULT '',
            closed_by TEXT DEFAULT '',
            close_reason TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS ticket_transcripts (
            ticket_id INTEGER PRIMARY KEY,
            file_name TEXT NOT NULL,
            html_blob BLOB NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_tickets_user_status ON tickets(guild_id, user_id, status);
        CREATE INDEX IF NOT EXISTS idx_farm_user_date ON farm_entries(discord_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_actions_created ON actions(created_at);
        """)
        # Migração segura para bancos criados por versões anteriores.
        columns = {row[1] for row in con.execute("PRAGMA table_info(registration_requests)").fetchall()}
        for name in ("selected_recruiter_id", "deadline_at", "message_id"):
            if name not in columns:
                con.execute(f"ALTER TABLE registration_requests ADD COLUMN {name} TEXT DEFAULT ''")
        proof_columns = {row[1] for row in con.execute("PRAGMA table_info(farm_proofs)").fetchall()}
        proof_migrations = {
            "status": "TEXT NOT NULL DEFAULT 'PENDING'",
            "reviewed_by": "TEXT DEFAULT ''",
            "reviewed_at": "TEXT DEFAULT ''",
            "review_reason": "TEXT DEFAULT ''",
            "review_channel_id": "TEXT DEFAULT ''",
            "review_message_id": "TEXT DEFAULT ''",
        }
        for name, definition in proof_migrations.items():
            if name not in proof_columns:
                con.execute(f"ALTER TABLE farm_proofs ADD COLUMN {name} {definition}")
        pending_columns = {row[1] for row in con.execute("PRAGMA table_info(farm_proof_pending)").fetchall()}
        pending_migrations = {
            "item_name": "TEXT DEFAULT ''", "quantity": "INTEGER DEFAULT 0",
            "points": "INTEGER DEFAULT 0", "channel_id": "TEXT DEFAULT ''",
        }
        for name, definition in pending_migrations.items():
            if name not in pending_columns:
                con.execute(f"ALTER TABLE farm_proof_pending ADD COLUMN {name} {definition}")
        if "unit_price_cents" not in pending_columns:
            con.execute("ALTER TABLE farm_proof_pending ADD COLUMN unit_price_cents INTEGER DEFAULT 0")
        item_columns = {row[1] for row in con.execute("PRAGMA table_info(farm_items)").fetchall()}
        if "unit_price_cents" not in item_columns:
            con.execute("ALTER TABLE farm_items ADD COLUMN unit_price_cents INTEGER NOT NULL DEFAULT 0")
        for name, definition in {"item_name": "TEXT DEFAULT ''", "quantity": "INTEGER DEFAULT 0", "points": "INTEGER DEFAULT 0", "unit_price_cents": "INTEGER DEFAULT 0", "total_cents": "INTEGER DEFAULT 0", "paid_by": "TEXT DEFAULT ''", "paid_at": "TEXT DEFAULT ''", "receipt_data": "BLOB", "receipt_filename": "TEXT DEFAULT ''", "receipt_mime": "TEXT DEFAULT ''"}.items():
            if name not in proof_columns:
                con.execute(f"ALTER TABLE farm_proofs ADD COLUMN {name} {definition}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_day_prefix() -> str:
    local = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET_HOURS)
    return local.strftime("%Y-%m-%d")


def setting(key: str, default: str = "0") -> str:
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


# Chaves que podem ser alteradas pelo painel /config. São persistidas no SQLite
# e reaplicadas na inicialização para não depender apenas do arquivo .env.
CONFIG_BINDINGS = {
    "faction_name": ("FACTION_NAME", str),
    "member_role_id": ("MEMBER_ROLE_ID", int),
    "recruit_role_id": ("RECRUIT_ROLE_ID", int),
    "leader_role_id": ("LEADER_ROLE_ID", int),
    "adv1_role_id": ("ADV1_ROLE_ID", int),
    "adv2_role_id": ("ADV2_ROLE_ID", int),
    "adv3_role_id": ("ADV3_ROLE_ID", int),
    "registration_channel_id": ("REGISTRATION_CHANNEL_ID", int),
    "wl_channel_id": ("APPROVAL_CHANNEL_ID", int),
    "admin_log_channel_id": ("ADMIN_LOG_CHANNEL_ID", int),
    "public_log_channel_id": ("PUBLIC_LOG_CHANNEL_ID", int),
    "farm_log_channel_id": ("FARM_LOG_CHANNEL_ID", int),
    "farm_cooldown_seconds": ("FARM_COOLDOWN_SECONDS", int),
    "max_farm_entries_per_day": ("MAX_FARM_ENTRIES_PER_DAY", int),
    "timezone_offset_hours": ("TIMEZONE_OFFSET_HOURS", int),
    "auto_pd_on_adv3": ("AUTO_PD_ON_ADV3", lambda v: str(v).strip().lower() in {"1", "true", "yes", "sim", "on"}),
}


def apply_config_value(key: str, value: str) -> None:
    """Aplica uma configuração salva também às variáveis usadas pelo bot em execução."""
    binding = CONFIG_BINDINGS.get(key)
    if not binding:
        return
    global_vars_name, converter = binding
    try:
        converted = converter(value)
        if key == "farm_cooldown_seconds":
            converted = max(0, converted)
        elif key == "max_farm_entries_per_day":
            converted = max(1, converted)
        elif key == "timezone_offset_hours":
            converted = max(-12, min(14, converted))
        elif key.endswith("_role_id") or key.endswith("_channel_id"):
            converted = max(0, converted)
        elif key == "faction_name":
            converted = str(value).strip() or "Facção RP"
        globals()[global_vars_name] = converted
    except (TypeError, ValueError):
        log.warning("Valor inválido ignorado para a configuração %s", key)


def save_config_value(key: str, value: str) -> None:
    with db() as con:
        con.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
    apply_config_value(key, str(value))


def apply_saved_settings() -> None:
    with db() as con:
        rows = con.execute("SELECT key,value FROM settings").fetchall()
    for row in rows:
        apply_config_value(row["key"], row["value"])


def display_id(raw: int) -> str:
    return f"<@&{raw}> (`{raw}`)" if raw else "Não configurado"


def log_action(actor_id: int, action: str, target_id: Optional[int] = None,
               reason: str = "", details: str = "") -> None:
    with db() as con:
        con.execute("INSERT INTO actions(actor_id,target_id,action,reason,details,created_at) VALUES(?,?,?,?,?,?)",
                    (str(actor_id), str(target_id or ""), action, reason, details, now_iso()))


def has_leadership(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    member = interaction.user
    return member.guild_permissions.administrator or (LEADER_ROLE_ID != 0 and any(r.id == LEADER_ROLE_ID for r in member.roles))


def member_has_configured_role(interaction: discord.Interaction, setting_key: str, default_role_id: int = 0) -> bool:
    """Verifica um cargo salvo no painel; administradores sempre podem agir."""
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    member = interaction.user
    if member.guild_permissions.administrator:
        return True
    try:
        role_id = int(setting(setting_key, str(default_role_id)) or 0)
    except (TypeError, ValueError):
        role_id = 0
    return bool(role_id and any(role.id == role_id for role in member.roles))


def can_decide_wl(interaction: discord.Interaction) -> bool:
    return member_has_configured_role(interaction, "wl_approver_role_id", RECRUIT_ROLE_ID)


def can_decide_farm(interaction: discord.Interaction) -> bool:
    return member_has_configured_role(interaction, "farm_approver_role_id", LEADER_ROLE_ID)


def is_active_member(discord_id: int) -> bool:
    with db() as con:
        row = con.execute("SELECT status FROM members WHERE discord_id=?", (str(discord_id),)).fetchone()
    return bool(row and row["status"] == "ACTIVE")

async def deny_unless_config_access(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use este painel dentro do servidor."))
        return False
    member = interaction.user
    allowed = member.guild_permissions.administrator or has_leadership(interaction) or any(
        role.id == DEVELOPER_ROLE_ID for role in member.roles
    )
    if not allowed:
        await interaction.response.send_message(
            None,
            ephemeral=True, view=v2_view(None, None, "Você precisa do cargo de desenvolvedor, liderança ou permissão de administrador para acessar o painel."),
        )
        return False
    return True


async def deny_unless_leader(interaction: discord.Interaction) -> bool:
    if not has_leadership(interaction):
        msg = "Você precisa do cargo de liderança ou permissão de administrador para usar isso."
        if interaction.response.is_done():
            await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, msg))
        else:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, msg))
        return False
    return True

async def send_channel_log(guild: discord.Guild, title: str, description: str,
                           *, public: bool = False, color: discord.Color = discord.Color.blurple()) -> None:
    title_upper = title.upper()
    if title.startswith("🌾") and int(setting("farm_log_channel_id", str(FARM_LOG_CHANNEL_ID)) or 0):
        channel_id = int(setting("farm_log_channel_id", str(FARM_LOG_CHANNEL_ID)) or 0)
    elif ("ADV" in title_upper or "ADVERTÊNCIA" in title_upper) and int(setting("adv_log_channel_id", "0") or 0):
        channel_id = int(setting("adv_log_channel_id", "0") or 0)
    elif ("PD " in title_upper or "PD APLICADO" in title_upper or "MEMBRO DESLIGADO" in title_upper) and int(setting("pd_log_channel_id", "0") or 0):
        channel_id = int(setting("pd_log_channel_id", "0") or 0)
    elif "BLACKLIST" in title_upper and int(setting("blacklist_log_channel_id", "0") or 0):
        channel_id = int(setting("blacklist_log_channel_id", "0") or 0)
    else:
        channel_id = PUBLIC_LOG_CHANNEL_ID if public else ADMIN_LOG_CHANNEL_ID
    channel = guild.get_channel(channel_id) if channel_id else None
    if not isinstance(channel, discord.TextChannel):
        return
    embed = PanelMessage(title=title, description=description[:4000], color=color, timestamp=datetime.now(timezone.utc))
    try:
        await channel.send(view=v2_view(embed, None, None))
    except discord.HTTPException as exc:
        log.warning("Não foi possível enviar log no canal %s: %s", channel_id, exc)

async def fetch_member(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    member = guild.get_member(user_id)
    if member:
        return member
    try:
        return await guild.fetch_member(user_id)
    except (discord.NotFound, discord.HTTPException):
        return None

# ---------------- WHITELIST / CADASTRO ----------------
def recruiter_role_id() -> int:
    """Cargo autorizado a recrutar; configurável por .env ou painel/settings."""
    try:
        return int(setting("recruit_role_id", str(RECRUIT_ROLE_ID)))
    except (TypeError, ValueError):
        return RECRUIT_ROLE_ID


def invite_url() -> str:
    return setting("server_invite_url", os.getenv("SERVER_INVITE_URL", "https://discord.com"))


class RegistrationModal(ComponentsV2Modal, title="Whitelist da facção"):
    character_name = TextInput(label="Seu nome no RP", placeholder="Ex.: Gustavo", max_length=32)
    game_id = TextInput(label="Seu ID no FiveM", placeholder="Somente números", max_length=8)

    def __init__(self, recruiter_id: int):
        super().__init__(timeout=300)
        self.recruiter_id = recruiter_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use este formulário dentro do servidor."))
            return
        gid = self.game_id.value.strip()
        name = self.character_name.value.strip()
        if not gid.isdigit():
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "O ID do FiveM precisa conter somente números."))
            return
        role_id = recruiter_role_id()
        recruiter = interaction.guild.get_member(self.recruiter_id)
        if not recruiter or not role_id or not any(role.id == role_id for role in recruiter.roles):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse recrutador não possui mais o cargo autorizado. Inicie a WL novamente."))
            return
        with db() as con:
            pending = con.execute("SELECT id FROM registration_requests WHERE discord_id=? AND status='PENDING'", (str(interaction.user.id),)).fetchone()
            existing = con.execute("SELECT discord_id FROM members WHERE discord_id=? AND status='ACTIVE'", (str(interaction.user.id),)).fetchone()
            blacklisted = con.execute("SELECT game_id FROM blacklist WHERE game_id=?", (gid,)).fetchone()
            duplicate_game_id = con.execute(
                "SELECT discord_id FROM members WHERE game_id=? AND discord_id<>?",
                (gid, str(interaction.user.id)),
            ).fetchone()
            pending_game_id = con.execute(
                "SELECT id FROM registration_requests WHERE game_id=? AND discord_id<>? AND status='PENDING'",
                (gid, str(interaction.user.id)),
            ).fetchone()
            if pending:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você já tem uma WL aguardando decisão.")); return
            if existing:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você já está cadastrado como membro.")); return
            if blacklisted:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse ID está na blacklist da organização.")); return
            if duplicate_game_id or pending_game_id:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse ID do FiveM já está vinculado a outro membro ou aguardando outra WL.")); return
            deadline = datetime.now(timezone.utc) + timedelta(minutes=5)
            cur = con.execute("""INSERT INTO registration_requests
                (discord_id,discord_name,character_name,game_id,recruiter,status,created_at,selected_recruiter_id,deadline_at)
                VALUES(?,?,?,?,?,'PENDING',?,?,?)""",
                (str(interaction.user.id), str(interaction.user), name, gid, str(recruiter.id), now_iso(), str(recruiter.id), deadline.isoformat(timespec="seconds")))
            request_id = cur.lastrowid
        channel_id = int(setting("wl_channel_id", str(APPROVAL_CHANNEL_ID)) or 0)
        channel = interaction.guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            with db() as con:
                con.execute("UPDATE registration_requests SET status='ERROR' WHERE id=?", (request_id,))
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "O canal de WL não está configurado no painel/arquivo. Avise a administração."))
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        embed = PanelMessage(title=f"📋 Solicitação de WL #{request_id}", color=discord.Color.gold(), timestamp=datetime.now(timezone.utc))
        embed.description = "A solicitação poderá ser aprovada ou recusada por membros com o cargo autorizado configurado em **/config → WL**. Prazo: **5 minutos**."
        embed.add_field(name="Solicitante", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
        embed.add_field(name="Nome no RP", value=name, inline=True)
        embed.add_field(name="ID FiveM", value=gid, inline=True)
        embed.add_field(name="Recrutador escolhido", value=f"{recruiter.mention} (`{recruiter.id}`)", inline=False)
        embed.add_field(name="Prazo", value=f"Termina <t:{int(deadline.timestamp())}:R>", inline=False)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"request_id={request_id}")
        try:
            msg = await channel.send(view=v2_view(embed, RegistrationApprovalView(), recruiter.mention))
        except discord.HTTPException:
            with db() as con:
                con.execute("UPDATE registration_requests SET status='ERROR' WHERE id=?", (request_id,))
            await interaction.followup.send(
                None, ephemeral=True,
                view=v2_view(None, None, "Não consegui publicar sua WL. Confira as permissões do bot e tente novamente."),
            )
            return

        with db() as con:
            con.execute("UPDATE registration_requests SET message_id=? WHERE id=?", (str(msg.id), request_id))
        pending_role_id = int(setting("wl_pending_role_id", "0") or 0)
        pending_role = interaction.guild.get_role(pending_role_id) if pending_role_id else None
        if pending_role:
            try:
                await interaction.user.add_roles(pending_role, reason=f"WL #{request_id} enviada; cargo temporário")
            except discord.HTTPException:
                log.warning("Não foi possível aplicar o cargo temporário de WL ao membro %s", interaction.user.id, exc_info=True)
        await interaction.followup.send(
            None, ephemeral=True,
            view=v2_view(None, None, "Sua solicitação foi enviada. Você receberá o resultado no privado."),
        )

class RecruiterSelect(discord.ui.Select):
    def __init__(self, parent_view: "RecruiterSelectView"):
        self.parent_view = parent_view
        start = parent_view.page * 25
        page_members = parent_view.members[start:start + 25]
        options = [
            discord.SelectOption(
                label=member.display_name[:100],
                value=str(member.id),
                description=f"Recrutador autorizado · {member.name}"[:100],
            )
            for member in page_members
        ]
        if not options:
            options = [discord.SelectOption(
                label="Nenhum recrutador configurado",
                value="none",
                description="A administração precisa configurar o cargo",
            )]
        super().__init__(
            placeholder=f"Escolha seu recrutador • página {parent_view.page + 1}/{parent_view.page_count}",
            min_values=1, max_values=1, options=options, custom_id="fac:wl:recruiter",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "none":
            await interaction.response.send_message(
                None, ephemeral=True,
                view=v2_view(None, None, "Não há recrutadores disponíveis. Avise a administração."),
            )
            return
        await interaction.response.send_modal(RegistrationModal(int(self.values[0])))


class RecruiterSelectView(ComponentsV2View):
    """Seleciona recrutadores em páginas, sem truncar servidores com mais de 25 pessoas."""
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=180)
        self.guild = guild
        rid = recruiter_role_id()
        role = guild.get_role(rid) if rid else None
        self.members = sorted(
            (member for member in (role.members if role else []) if not member.bot),
            key=lambda member: member.display_name.casefold(),
        )
        self.page = 0
        self.selector = RecruiterSelect(self)
        self.add_item(self.selector)
        self.previous_button = discord.ui.Button(label="◀ Anterior", style=discord.ButtonStyle.secondary, row=1, disabled=True)
        self.next_button = discord.ui.Button(label="Próxima ▶", style=discord.ButtonStyle.secondary, row=1)
        self.previous_button.callback = self.previous_page
        self.next_button.callback = self.next_page
        self.add_item(self.previous_button)
        self.add_item(self.next_button)
        self._sync()
        self.set_text(f"# Selecionar recrutador\nEscolha quem realizou seu recrutamento. Página {self.page + 1}/{self.page_count}.")

    @property
    def page_count(self):
        return max(1, math.ceil(len(self.members) / 25))

    def _sync(self):
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.page_count - 1

    def refresh(self):
        self.remove_item(self.selector)
        self.selector = RecruiterSelect(self)
        self.add_item(self.selector)
        self._sync()
        self.set_text(f"# Selecionar recrutador\nEscolha quem realizou seu recrutamento. Página {self.page + 1}/{self.page_count}.")

    async def previous_page(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self.refresh()
        await interaction.response.edit_message(
            view=self, content=None, embeds=[],
            attachments=(interaction.message.attachments if interaction.message else []),
        )

    async def next_page(self, interaction: discord.Interaction):
        self.page = min(self.page_count - 1, self.page + 1)
        self.refresh()
        await interaction.response.edit_message(
            view=self, content=None, embeds=[],
            attachments=(interaction.message.attachments if interaction.message else []),
        )


class RegistrationPanelView(ComponentsV2View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fazer WL", style=discord.ButtonStyle.primary, emoji="📋", custom_id="fac:register")
    async def register(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use este botão no servidor.")); return
        await interaction.response.send_message(None, view=v2_view(None, RecruiterSelectView(interaction.guild), "Primeiro, selecione um recrutador disponível e depois preencha seus dados. A aprovação ou recusa será feita por quem possuir o cargo autorizado em /config → WL."), ephemeral=True)

class RegistrationRejectModal(ComponentsV2Modal, title="Motivo da recusa"):
    reason = TextInput(label="Motivo obrigatório", placeholder="Explique por que a WL foi recusada", style=discord.TextStyle.paragraph, min_length=3, max_length=500)
    def __init__(self, request_id: int):
        super().__init__(timeout=180)
        self.request_id = request_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await finish_registration(interaction, self.request_id, approve=False, reason=self.reason.value.strip())

async def finish_registration(interaction: discord.Interaction, request_id: int, *, approve: bool, reason: str = "") -> None:
    if not interaction.guild:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Essa ação só pode ser feita no servidor.")); return
    with db() as con:
        request = con.execute("SELECT * FROM registration_requests WHERE id=?", (request_id,)).fetchone()
        if not request or request["status"] != "PENDING":
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Essa solicitação já foi encerrada.")); return
        deadline_text = request["deadline_at"] or ""
        try:
            deadline = datetime.fromisoformat(deadline_text)
        except ValueError:
            deadline = datetime.now(timezone.utc)
        if datetime.now(timezone.utc) > deadline:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "O prazo de 5 minutos já expirou; a WL será reprovada automaticamente.")); return
        if not can_decide_wl(interaction):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você não possui o cargo configurado para aprovar ou recusar WL.")); return
        # Atualização condicional impede que dois cliques processem a mesma WL.
        cur = con.execute("UPDATE registration_requests SET status=?,processed_by=?,processed_at=?,reason=? WHERE id=? AND status='PENDING'",
                          ("APPROVED" if approve else "REJECTED", str(interaction.user.id), now_iso(), reason, request_id))
        if cur.rowcount != 1:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Essa solicitação já foi processada.")); return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    member = await fetch_member(interaction.guild, int(request["discord_id"]))
    rank_role = None
    assigned_role_ids = set()
    if member:
        pending_role_id = int(setting("wl_pending_role_id", "0") or 0)
        pending_role = interaction.guild.get_role(pending_role_id) if pending_role_id else None
        approved_role_id = int(setting("member_role_id", str(MEMBER_ROLE_ID)) or 0)
        approved_role = interaction.guild.get_role(approved_role_id) if approved_role_id else None

        # Remover o cargo temporário não pode impedir a atribuição do cargo aprovado.
        if pending_role and pending_role in member.roles:
            try:
                await member.remove_roles(pending_role, reason=f"WL #{request_id} processada")
            except discord.HTTPException:
                log.exception("Não foi possível remover o cargo temporário do membro %s", member.id)

        if approve and approved_role:
            try:
                await member.add_roles(approved_role, reason="WL aprovada")
                # O objeto Member pode manter a lista de cargos em cache antigo até o próximo evento.
                # Guardamos o ID concedido para calcular a prioridade sem depender desse cache.
                assigned_role_ids.add(approved_role.id)
            except discord.HTTPException:
                log.exception("Não foi possível aplicar o cargo aprovado ao membro %s", member.id)

    if approve:
        if member:
            rank_role, _abbreviation = highest_configured_rank(member, extra_role_ids=assigned_role_ids)
        with db() as con:
            con.execute("""INSERT INTO members(discord_id,discord_name,character_name,game_id,recruiter,rank_name,joined_at,status)
                VALUES(?,?,?,?,?,?,?,'ACTIVE') ON CONFLICT(discord_id) DO UPDATE SET discord_name=excluded.discord_name,
                character_name=excluded.character_name,game_id=excluded.game_id,recruiter=excluded.recruiter,rank_name=excluded.rank_name,status='ACTIVE'""",
                (
                    request["discord_id"],
                    request["discord_name"],
                    request["character_name"],
                    request["game_id"],
                    str(request["selected_recruiter_id"] or request["recruiter"] or ""),
                    rank_role.name if rank_role else "Membro",
                    now_iso(),
                ))

        # O cadastro precisa estar ACTIVE antes da sincronização. Assim, tanto a
        # aprovação direta quanto o evento on_member_update encontram os dados da WL.
        if member:
            await sync_configured_member_nickname(
                member,
                f"WL #{request_id} aprovada por {interaction.user}",
                extra_role_ids=assigned_role_ids,
            )

        log_action(interaction.user.id, "WL_APROVADA", int(request["discord_id"]), "Aprovada por usuário com cargo autorizado", f"Solicitação #{request_id}")
        result_title = "✅ WHITELIST APROVADA!"
        result_text = f"Parabéns! Sua entrada em **{FACTION_NAME}** foi aprovada.\n**Nome:** {request['character_name']}\n**ID:** {request['game_id']}\n**Recrutador:** <@{request['selected_recruiter_id'] or request['recruiter']}>\n**Decisão por:** {interaction.user.mention}"
        color = discord.Color.green()
    else:
        log_action(interaction.user.id, "WL_REJEITADA", int(request["discord_id"]), reason, f"Solicitação #{request_id}")
        result_title = "❌ WHITELIST REPROVADA"
        result_text = f"Sua WL para **{FACTION_NAME}** foi recusada.\n**Motivo:** {reason or 'O prazo de 5 minutos expirou sem aprovação.'}"
        color = discord.Color.red()

    target_user = member
    if target_user is None:
        try:
            target_user = await bot.fetch_user(int(request["discord_id"]))
        except discord.HTTPException:
            target_user = None
    if target_user:
        try:
            invite = invite_url()
            dm_message = PanelMessage(title=result_title, description=result_text, color=color, timestamp=datetime.now(timezone.utc))
            dm_message.set_footer(text="Resultado automático da whitelist")
            dm_view = ComponentsV2View(timeout=None)
            dm_view.add_item(discord.ui.Button(label="IR PARA O SERVIDOR", style=discord.ButtonStyle.link, url=invite))
            await target_user.send(view=v2_view(dm_message, dm_view, None))
        except discord.HTTPException:
            log.info("Não foi possível enviar DM de WL para %s", request["discord_id"])
    # Atualiza a mensagem original também quando a decisão veio do modal de recusa.
    original_message = interaction.message
    if original_message is None and request["message_id"]:
        channel_id = int(setting("wl_channel_id", str(APPROVAL_CHANNEL_ID)) or 0)
        channel = interaction.guild.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            try:
                original_message = await channel.fetch_message(int(request["message_id"]))
            except discord.HTTPException:
                original_message = None
    if original_message:
        compact_result = PanelMessage(
            title=f"{'✅ WL APROVADA' if approve else '❌ WL REPROVADA'} · #{request_id}",
            description=(
                f"**Solicitante:** {request['discord_name'] or request['character_name']} (<@{request['discord_id']}>)\n"
                f"**Nome no RP:** {request['character_name']}\n"
                f"**Recrutador escolhido:** <@{request['selected_recruiter_id'] or request['recruiter']}>\n"
                f"**Decisão por:** {interaction.user.mention}\n"
                f"**Status:** **{'APROVADA' if approve else 'REPROVADA'}**"
                + (f"\n**Motivo:** {reason[:500]}" if reason else "")
            ),
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        compact_result.set_footer(text=f"Registro de WL • request_id={request_id}")
        try:
            await original_message.edit(view=v2_view(compact_result, None, None), content=None, embeds=[], attachments=original_message.attachments)
        except discord.HTTPException:
            pass
    await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, "WL processada e registrada."))

class RegistrationApprovalView(ComponentsV2View):
    def __init__(self):
        super().__init__(timeout=None)

    def request_id_from(self, interaction: discord.Interaction) -> Optional[int]:
        return message_marker_id(interaction.message, "request_id=")

    @discord.ui.button(label="Aprovar", style=discord.ButtonStyle.success, emoji="✅", custom_id="fac:approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        request_id = self.request_id_from(interaction)
        if request_id is None:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "ID da WL inválido.")); return
        await finish_registration(interaction, request_id, approve=True)

    @discord.ui.button(label="Recusar", style=discord.ButtonStyle.danger, emoji="❌", custom_id="fac:reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        request_id = self.request_id_from(interaction)
        if request_id is None:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "ID da WL inválido.")); return
        with db() as con:
            row = con.execute("SELECT status,selected_recruiter_id FROM registration_requests WHERE id=?", (request_id,)).fetchone()
        if not row or row["status"] != "PENDING":
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Essa WL já foi encerrada.")); return
        if not can_decide_wl(interaction):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você não possui o cargo configurado para aprovar ou recusar WL.")); return
        await interaction.response.send_modal(RegistrationRejectModal(request_id))


def build_member_nickname(character_name: str, game_id: str, abbreviation: str) -> str:
    """Monta [TAG] Nome RP | ID, preservando o ID no limite de 32 caracteres do Discord."""
    safe_tag = " ".join(str(abbreviation or "M").strip().strip("[]").split()) or "M"
    safe_id = str(game_id or "").strip()
    suffix = f" | {safe_id}"

    # Mantém espaço para pelo menos um caractere do nome, mesmo com uma tag longa.
    max_tag_length = max(1, 32 - len(suffix) - 4)
    safe_tag = safe_tag[:max_tag_length].rstrip() or "M"
    prefix = f"[{safe_tag}] "
    available_name = max(1, 32 - len(prefix) - len(suffix))
    safe_name = " ".join(str(character_name or "Membro").split())[:available_name].rstrip() or "M"
    return f"{prefix}{safe_name}{suffix}"[:32]


def highest_configured_rank(member: discord.Member, extra_role_ids=None):
    """Retorna a tag do cargo de maior prioridade, incluindo cargos concedidos nesta interação."""
    priorities = {}
    with db() as con:
        rows = con.execute("SELECT key,value FROM settings WHERE key LIKE 'rank_role_%'").fetchall()
    for row in rows:
        try:
            rid = int(row["key"].replace("rank_role_", "", 1))
            parts = row["value"].split("|")
            priorities[rid] = (int(parts[1]) if len(parts) > 1 else 0, parts[0].strip())
        except (ValueError, TypeError, IndexError):
            continue

    roles_by_id = {role.id: role for role in member.roles if role.id in priorities}
    for role_id in extra_role_ids or ():
        if role_id in priorities and role_id not in roles_by_id:
            role = member.guild.get_role(role_id)
            if role is not None:
                roles_by_id[role_id] = role

    candidates = [
        (priorities[role_id][0], role, priorities[role_id][1])
        for role_id, role in roles_by_id.items()
    ]
    if candidates:
        _, role, abbreviation = max(candidates, key=lambda item: (item[0], item[1].id))
        return role, abbreviation
    fallback = member.guild.get_role(MEMBER_ROLE_ID) if MEMBER_ROLE_ID else None
    return fallback, "M"

async def expire_pending_wls() -> None:
    """Reprova solicitações após cinco minutos, inclusive após reinicialização."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            with db() as con:
                rows = con.execute("SELECT id,discord_id,character_name,game_id,recruiter,selected_recruiter_id,deadline_at,message_id FROM registration_requests WHERE status='PENDING'").fetchall()
                for row in rows:
                    try: deadline = datetime.fromisoformat(row["deadline_at"])
                    except (ValueError, TypeError): deadline = datetime.now(timezone.utc)
                    if datetime.now(timezone.utc) >= deadline:
                        cur = con.execute("UPDATE registration_requests SET status='REJECTED',processed_by='SYSTEM',processed_at=?,reason=? WHERE id=? AND status='PENDING'", (now_iso(), "Prazo de 5 minutos expirado sem aprovação", row["id"]))
                        if cur.rowcount:
                            guild = bot.get_guild(GUILD_ID) if GUILD_ID else (bot.guilds[0] if len(bot.guilds) == 1 else None)
                            if guild:
                                expired_member = await fetch_member(guild, int(row["discord_id"]))
                                pending_role_id = int(setting("wl_pending_role_id", "0") or 0)
                                pending_role = guild.get_role(pending_role_id) if pending_role_id else None
                                if expired_member and pending_role and pending_role in expired_member.roles:
                                    try:
                                        await expired_member.remove_roles(pending_role, reason=f"WL #{row['id']} expirada")
                                    except discord.HTTPException:
                                        log.exception("Não foi possível remover o cargo temporário da WL expirada #%s", row["id"])
                            try:
                                user = await bot.fetch_user(int(row["discord_id"]))
                                dm_embed = PanelMessage(title="⏱️ WHITELIST REPROVADA", description=f"Sua WL para **{FACTION_NAME}** foi reprovada automaticamente porque ninguém com o cargo autorizado decidiu dentro de 5 minutos.\n**Nome:** {row['character_name']}\n**ID:** {row['game_id']}", color=discord.Color.red())
                                view = ComponentsV2View(timeout=None)
                                view.add_item(discord.ui.Button(label="IR PARA O SERVIDOR", style=discord.ButtonStyle.link, url=invite_url()))
                                await user.send(view=v2_view(dm_embed, view, None))
                            except discord.HTTPException:
                                pass
                            channel_id = int(setting("wl_channel_id", str(APPROVAL_CHANNEL_ID)) or 0)
                            channel = guild.get_channel(channel_id) if guild and channel_id else None
                            if isinstance(channel, discord.TextChannel) and row["message_id"]:
                                try:
                                    msg = await channel.fetch_message(int(row["message_id"]))
                                    if msg.embeds or msg.components:
                                        embed = PanelMessage(
                                            title=f"⏱️ WL REPROVADA AUTOMATICAMENTE · #{row['id']}",
                                            description=(f"**Solicitante:** <@{row['discord_id']}>\n"
                                                         f"**Nome no RP:** {row['character_name']}\n"
                                                         f"**Recrutador escolhido:** <@{row['selected_recruiter_id'] or row['recruiter']}>\n"
                                                         f"**Status:** **REPROVADA**\n"
                                                         f"**Motivo:** prazo de 5 minutos expirado"),
                                            color=discord.Color.red(), timestamp=datetime.now(timezone.utc),
                                        )
                                        embed.set_footer(text=f"Registro de WL • request_id={row['id']}")
                                        await msg.edit(view=v2_view(embed, None, None), content=None, embeds=[], attachments=msg.attachments)
                                except discord.HTTPException:
                                    pass
                            if guild:
                                await send_channel_log(guild, "⏱️ WL reprovada automaticamente", f"Solicitante: <@{row['discord_id']}>\nRecrutador escolhido: <@{row['selected_recruiter_id'] or row['recruiter']}>\nStatus: REPROVADA.", color=discord.Color.red())
        except Exception:
            log.exception("Erro ao verificar expiração das WLs")
        await asyncio.sleep(10)

class ActionModal(ComponentsV2Modal, title="Registrar ação da liderança"):
    target_id = TextInput(label="ID do membro no Discord", placeholder="Ex.: 123456789012345678", max_length=20)
    action_type = TextInput(label="Tipo de ação", placeholder="Advertência, suspensão, observação...", max_length=40)
    reason = TextInput(label="Motivo / detalhes", style=discord.TextStyle.paragraph, max_length=500)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not has_leadership(interaction):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você precisa de permissão de liderança."))
            return
        raw_id = self.target_id.value.strip().replace("<@", "").replace("!", "").replace(">", "")
        if not raw_id.isdigit():
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Informe um ID numérico válido do Discord."))
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        target_id = int(raw_id)
        member = await fetch_member(interaction.guild, target_id) if interaction.guild else None
        action_name = self.action_type.value.strip()[:40]
        reason_text = self.reason.value.strip()
        log_action(interaction.user.id, action_name.upper(), target_id, reason_text, "Registrado pelo painel da liderança")
        if interaction.guild:
            target_label = member.mention if member else f"ID Discord `{target_id}`"
            await send_channel_log(interaction.guild, f"📌 Ação administrativa: {action_name}",
                                   f"Aplicada por: {interaction.user.mention}\nMembro: {target_label}\nMotivo: {reason_text}",
                                   color=discord.Color.orange())
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, f"Ação **{action_name}** registrada para `{target_id}`."))

class LeadershipPanelView(ComponentsV2View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Registrar ação", style=discord.ButtonStyle.primary, emoji="📌", custom_id="fac:leadership:register-action")
    async def register_action(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not has_leadership(interaction):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você precisa de permissão de liderança."))
            return
        await interaction.response.send_modal(ActionModal())

    @discord.ui.button(label="Ver logs recentes", style=discord.ButtonStyle.secondary, emoji="🧾", custom_id="fac:leadership:recent-logs")
    async def recent_logs(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not has_leadership(interaction):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você precisa de permissão de liderança."))
            return
        with db() as con:
            rows = con.execute("SELECT * FROM actions ORDER BY id DESC LIMIT 10").fetchall()
        if not rows:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Ainda não há ações registradas."))
            return
        lines = []
        for row in rows:
            target = f" · alvo <@{row['target_id']}>" if row["target_id"] else ""
            reason = f" · {row['reason']}" if row["reason"] else ""
            lines.append(f"`#{row['id']}` **{row['action']}** por <@{row['actor_id']}>{target}{reason}")
        embed = PanelMessage(title="🧾 Últimas ações da liderança", description="\n".join(lines), color=discord.Color.blurple())
        await interaction.response.send_message(view=v2_view(embed, None, None), ephemeral=True)

# ---------------- REGISTRO DE FARM COM IDENTIFICAÇÃO PELA WL ----------------
class FarmQuantityModal(ComponentsV2Modal, title="Quantidade do farm"):
    quantity = TextInput(label="Quantidade farmada", placeholder="Digite somente números", max_length=9)

    def __init__(self, item_id: int, item_name: str, points_per_unit: int, channel_id: int):
        super().__init__(timeout=300)
        self.item_id = item_id
        self.item_name = item_name
        self.points_per_unit = points_per_unit
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.quantity.value.strip()
        if not raw.isdigit() or int(raw) < 1 or int(raw) > 100000:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Informe uma quantidade numérica entre 1 e 100.000."))
            return
        if not is_active_member(interaction.user.id):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Seu cadastro ativo da WL não foi encontrado. Não é possível registrar farm."))
            return
        with db() as con:
            member = con.execute("SELECT discord_id, character_name, game_id FROM members WHERE discord_id=? AND status='ACTIVE'", (str(interaction.user.id),)).fetchone()
            item = con.execute("SELECT name, points, unit_price_cents FROM farm_items WHERE id=? AND active=1", (self.item_id,)).fetchone()
            if not member:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Não encontrei seu cadastro ativo da WL. Procure a liderança."))
                return
            if not item:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse item de farm foi desativado. Inicie um novo registro."))
                return
            if int(item["unit_price_cents"] or 0) <= 0:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "A liderança ainda não definiu o valor por unidade desse item. Avise a liderança para configurar `/farm-preco`."))
                return
            quantity = int(raw)
            day_prefix = local_day_prefix()
            daily_entries = con.execute("SELECT COUNT(*) AS n FROM farm_entries WHERE discord_id=? AND substr(created_at,1,10)=?", (str(interaction.user.id), day_prefix)).fetchone()["n"]
            daily_pending = con.execute("SELECT COUNT(*) AS n FROM farm_proofs WHERE discord_id=? AND status='PENDING' AND substr(created_at,1,10)=?", (str(interaction.user.id), day_prefix)).fetchone()["n"]
            daily = int(daily_entries) + int(daily_pending)
            if daily >= MAX_FARM_ENTRIES_PER_DAY:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Você atingiu o limite de {MAX_FARM_ENTRIES_PER_DAY} registros de farm hoje."))
                return
            last_entry = con.execute("SELECT created_at FROM farm_entries WHERE discord_id=? ORDER BY id DESC LIMIT 1", (str(interaction.user.id),)).fetchone()
            last_pending = con.execute("SELECT created_at FROM farm_proofs WHERE discord_id=? AND status='PENDING' ORDER BY id DESC LIMIT 1", (str(interaction.user.id),)).fetchone()
            timestamps = [row["created_at"] for row in (last_entry, last_pending) if row and row["created_at"]]
            if timestamps and FARM_COOLDOWN_SECONDS:
                try:
                    latest = max(datetime.fromisoformat(value) for value in timestamps)
                    elapsed = (datetime.now(timezone.utc) - latest).total_seconds()
                    if elapsed < FARM_COOLDOWN_SECONDS:
                        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Aguarde {int(FARM_COOLDOWN_SECONDS-elapsed)+1}s antes de iniciar outro registro."))
                        return
                except ValueError:
                    pass
            con.execute("""INSERT INTO farm_proof_pending(discord_id,character_name,game_id,created_at,item_name,quantity,points,channel_id,unit_price_cents)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(discord_id) DO UPDATE SET
                character_name=excluded.character_name, game_id=excluded.game_id, created_at=excluded.created_at,
                item_name=excluded.item_name, quantity=excluded.quantity, points=excluded.points, channel_id=excluded.channel_id,
                unit_price_cents=excluded.unit_price_cents""",
                (str(interaction.user.id), member["character_name"], member["game_id"], now_iso(), item["name"], quantity,
                 quantity * int(item["points"]), str(self.channel_id), int(item["unit_price_cents"] or 0)))
        await interaction.response.send_message(
            (None),
            ephemeral=True, view=v2_view(None, None, f"🌾 **Farm selecionado:** {item['name']}\n**Quantidade:** {quantity}\n" + (f"**Valor estimado:** R$ {(quantity * int(item['unit_price_cents'] or 0))/100:,.2f}\n\n" if int(item['unit_price_cents'] or 0) else "**Valor:** ainda não definido pela liderança\n\n") + "Agora envie a **imagem de prova como anexo neste mesmo canal**. O bot já identificou seu nome e ID pela WL; não precisa informar novamente."))


class FarmItemSelect(discord.ui.Select):
    def __init__(self, parent_view: "FarmItemSelectView"):
        self.parent_view = parent_view
        start = parent_view.page * 25
        page_items = parent_view.items[start:start + 25]
        options = [
            discord.SelectOption(
                label=row["name"][:100],
                value=str(row["id"]),
                description=(
                    f"R$ {int(row['unit_price_cents'] or 0)/100:.2f} por unidade · {row['points']} pts"
                    if int(row['unit_price_cents'] or 0)
                    else f"{row['points']} ponto(s) por unidade"
                )[:100],
            )
            for row in page_items
        ]
        super().__init__(
            placeholder=f"Selecione o farm • página {parent_view.page + 1}/{parent_view.page_count}",
            min_values=1, max_values=1,
            options=options or [discord.SelectOption(label="Nenhum item ativo", value="none")],
            disabled=not options,
        )
        self.items_by_id = {
            int(row["id"]): (row["name"], int(row["points"]))
            for row in page_items
        }

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message(
                None, ephemeral=True,
                view=v2_view(None, None, "Somente a pessoa que iniciou o registro pode selecionar o farm."),
            )
            return
        if not is_active_member(interaction.user.id):
            await interaction.response.send_message(
                None, ephemeral=True,
                view=v2_view(None, None, "Seu cadastro ativo da WL não foi encontrado."),
            )
            return
        raw = self.values[0]
        item = self.items_by_id.get(int(raw)) if raw.isdigit() else None
        if not item:
            await interaction.response.send_message(
                None, ephemeral=True,
                view=v2_view(None, None, "Item inválido. Abra um novo registro."),
            )
            return
        await interaction.response.send_modal(
            FarmQuantityModal(int(raw), item[0], item[1], self.parent_view.channel_id)
        )


class FarmItemSelectView(ComponentsV2View):
    """Menu paginado para qualquer quantidade de itens de farm ativos."""
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=180)
        self.requester_id = interaction.user.id
        self.channel_id = interaction.channel_id
        with db() as con:
            self.items = con.execute(
                "SELECT id,name,points,unit_price_cents FROM farm_items WHERE active=1 ORDER BY name"
            ).fetchall()
        self.page = 0
        self.selector = FarmItemSelect(self)
        self.add_item(self.selector)
        self.previous_button = discord.ui.Button(label="◀ Anterior", style=discord.ButtonStyle.secondary, row=1, disabled=True)
        self.next_button = discord.ui.Button(label="Próxima ▶", style=discord.ButtonStyle.secondary, row=1)
        self.previous_button.callback = self.previous_page
        self.next_button.callback = self.next_page
        self.add_item(self.previous_button)
        self.add_item(self.next_button)
        self._sync()

    @property
    def page_count(self):
        return max(1, math.ceil(len(self.items) / 25))

    def _sync(self):
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.page_count - 1

    def refresh(self):
        self.remove_item(self.selector)
        self.selector = FarmItemSelect(self)
        self.add_item(self.selector)
        self._sync()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                None, ephemeral=True,
                view=v2_view(None, None, "Somente a pessoa que iniciou o registro pode navegar nesta lista."),
            )
            return False
        return True

    async def previous_page(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self.refresh()
        await interaction.response.edit_message(
            view=self, content=None, embeds=[],
            attachments=(interaction.message.attachments if interaction.message else []),
        )

    async def next_page(self, interaction: discord.Interaction):
        self.page = min(self.page_count - 1, self.page + 1)
        self.refresh()
        await interaction.response.edit_message(
            view=self, content=None, embeds=[],
            attachments=(interaction.message.attachments if interaction.message else []),
        )


class FarmPanelView(ComponentsV2View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="REGISTRAR FARM", style=discord.ButtonStyle.success, emoji="🌾", custom_id="fac:farm:start")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Faça o registro pelo servidor."))
            return
        with db() as con:
            member = con.execute("SELECT character_name,game_id FROM members WHERE discord_id=? AND status='ACTIVE'", (str(interaction.user.id),)).fetchone()
            items = con.execute("SELECT COUNT(*) AS n FROM farm_items WHERE active=1").fetchone()["n"]
        if not member:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Seu cadastro ativo da WL não foi encontrado. Faça/regularize sua WL antes de registrar farm."))
            return
        if not items:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Nenhum tipo de farm está configurado/ativo no painel. Avise a liderança."))
            return
        await interaction.response.send_message(
            None,
            view=v2_view(None, FarmItemSelectView(interaction), f"Cadastro identificado: **{member['character_name']}** · ID **{member['game_id']}**.\nSelecione o farm que deseja registrar:"), ephemeral=True)


async def store_farm_proof(message: discord.Message) -> bool:
    with db() as con:
        pending = con.execute("SELECT * FROM farm_proof_pending WHERE discord_id=?", (str(message.author.id),)).fetchone()
    if not pending:
        return False
    try:
        pending_age = (datetime.now(timezone.utc) - datetime.fromisoformat(pending["created_at"])).total_seconds()
        if pending_age > 900:
            with db() as con:
                con.execute("DELETE FROM farm_proof_pending WHERE discord_id=?", (str(message.author.id),))
            await message.reply(None, view=v2_view(None, None, "O prazo de 15 minutos para enviar a prova expirou. Inicie um novo registro pelo painel."))
            return True
    except (TypeError, ValueError):
        pass
    if str(pending["channel_id"] or "") != str(message.channel.id):
        return False
    if not is_active_member(message.author.id):
        with db() as con:
            con.execute("DELETE FROM farm_proof_pending WHERE discord_id=?", (str(message.author.id),))
        await message.reply(None, view=v2_view(None, None, "Seu cadastro ativo da WL não foi localizado. O registro de farm foi cancelado."))
        return True
    image_attachment = next((a for a in message.attachments if (a.content_type or "").startswith("image/") or a.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))), None)
    if not image_attachment:
        await message.reply(None, view=v2_view(None, None, "Envie a imagem de prova como anexo nesta mensagem. Links e mensagens sem imagem não concluem o registro."))
        return True
    if image_attachment.size > 8 * 1024 * 1024:
        await message.reply(None, view=v2_view(None, None, "A imagem excede o limite de 8 MB. Envie uma imagem menor."))
        return True
    try:
        image_bytes = await image_attachment.read(use_cached=False)
    except discord.HTTPException:
        await message.reply(None, view=v2_view(None, None, "Não consegui baixar a imagem. Envie novamente."))
        return True
    filename = image_attachment.filename[:200]
    mime = image_attachment.content_type or "application/octet-stream"
    signatures = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")
    if not image_bytes.startswith(signatures):
        await message.reply(None, view=v2_view(None, None, "O arquivo não parece ser uma imagem PNG, JPG, GIF ou WEBP válida."))
        return True
    guild = message.guild
    private_channel_id = int(setting("farm_private_channel_id", "0") or 0)
    review_channel = guild.get_channel(private_channel_id) if guild and private_channel_id else None
    if not isinstance(review_channel, discord.TextChannel):
        await message.reply(None, view=v2_view(None, None, "O canal privado de cobranças ainda não foi configurado. A prova não foi registrada. Avise a liderança em `/config` → Farm."))
        return True
    log_channel_id = int(setting("farm_log_channel_id", str(FARM_LOG_CHANNEL_ID)) or 0)
    log_channel = guild.get_channel(log_channel_id) if guild and log_channel_id else None
    proof_status = "PENDING"
    with db() as con:
        cur = con.execute("""INSERT INTO farm_proofs(discord_id,character_name,game_id,image_data,image_filename,image_mime,source_url,created_at,status,item_name,quantity,points,unit_price_cents,total_cents)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(message.author.id), pending["character_name"], pending["game_id"], sqlite3.Binary(image_bytes), filename, mime,
             image_attachment.url, now_iso(), proof_status, pending["item_name"], int(pending["quantity"]), int(pending["points"]),
             int(pending["unit_price_cents"] or 0), int(pending["unit_price_cents"] or 0) * int(pending["quantity"])))
        proof_id = cur.lastrowid
        con.execute("DELETE FROM farm_proof_pending WHERE discord_id=?", (str(message.author.id),))
    log_action(message.author.id, "FARM_COMPROVANTE_ENVIADO", message.author.id, "", f"Comprovante #{proof_id}; {pending['item_name']}; quantidade={pending['quantity']}; {filename}; status={proof_status}")
    total_cents = int(pending["unit_price_cents"] or 0) * int(pending["quantity"])
    embed = PanelMessage(
        title=f"🧾 COBRANÇA DE FARM #{proof_id} — AGUARDANDO DECISÃO",
        description=(f"**Membro:** {pending['character_name']} · <@{message.author.id}>\n"
                     f"**ID FiveM:** {pending['game_id']}\n**Item:** {pending['item_name']}\n"
                     f"**Quantidade:** {pending['quantity']}\n**Valor unitário:** R$ {int(pending['unit_price_cents'] or 0)/100:.2f}\n"
                     f"**Total:** R$ {total_cents/100:.2f}\n**Pontos:** {pending['points']}\n\n"
                     "A liderança deve confirmar o pagamento e anexar o comprovante, ou recusar informando o motivo."),
        color=discord.Color.gold(), timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=f"farm_proof_id={proof_id}")
    safe_filename = os.path.basename(filename).replace("\n", "_")[:100] or f"farm-{proof_id}.png"
    embed.set_image(url=f"attachment://{safe_filename}")
    try:
        import io
        invoice_message = await review_channel.send(file=discord.File(io.BytesIO(image_bytes), filename=safe_filename), view=v2_view(embed, FarmInvoiceView(), None))
        with db() as con:
            con.execute("UPDATE farm_proofs SET review_channel_id=?,review_message_id=? WHERE id=?", (str(review_channel.id), str(invoice_message.id), proof_id))
        try:
            await message.delete()
        except discord.HTTPException:
            log.warning("Não consegui apagar a mensagem de prova do membro %s", message.author.id)
        try:
            await message.channel.send(None, delete_after=20, view=v2_view(None, None, f"📨 Sua solicitação de farm **#{proof_id}** foi enviada para a liderança. A decisão chegará por mensagem privada."))
        except discord.HTTPException:
            pass
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Não foi possível publicar cobrança #%s", proof_id)
        await message.reply(None, view=v2_view(None, None, f"⚠️ A prova #{proof_id} foi salva, mas a cobrança não pôde ser publicada. Avise a liderança; não envie outra prova."))
    return True

# ---------------- PAINEL INTERATIVO DE CONFIGURAÇÃO ----------------
def existing_rank_config() -> str:
    with db() as con:
        rows = con.execute("SELECT key,value FROM settings WHERE key LIKE 'rank_role_%'").fetchall()
    entries = []
    for row in rows:
        try:
            role_id = int(row["key"].replace("rank_role_", "", 1))
            abbreviation, priority = row["value"].split("|", 1)
            entries.append((int(priority), role_id, abbreviation))
        except (ValueError, TypeError):
            continue
    entries.sort(key=lambda item: item[0])
    return "; ".join(f"{role_id}={abbr}={priority}" for priority, role_id, abbr in entries)


def panel_setting(key: str, default: str) -> str:
    value = setting(key, default)
    return value if value is not None else default


def valid_asset_url(raw: str) -> bool:
    """Valida URLs públicas HTTPS usadas como foto ou banner dos painéis."""
    if not raw.strip():
        return True
    parsed = urlparse(raw.strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


def apply_panel_media(embed: PanelMessage, kind: str) -> PanelMessage:
    photo_url = panel_setting(f"{kind}_panel_photo_url", "").strip()
    banner_url = panel_setting(f"{kind}_panel_banner_url", "").strip()
    if photo_url and valid_asset_url(photo_url):
        embed.set_thumbnail(url=photo_url)
    if banner_url and valid_asset_url(banner_url):
        embed.set_image(url=banner_url)
    return embed


def panel_embed(kind: str) -> PanelMessage:
    if kind == "wl":
        title = panel_setting("wl_panel_title", f"📋 WHITELIST — {FACTION_NAME}")
        description = panel_setting(
            "wl_panel_description",
            "Clique em **Fazer WL**, escolha o recrutador e informe seu nome no RP e ID do FiveM. "
            "A aprovação ou recusa é feita por quem possuir o cargo autorizado configurado em /config → WL.",
        )
        return apply_panel_media(PanelMessage(title=title[:256], description=description[:4096], color=discord.Color.blurple()), "wl")
    if kind == "farm":
        title = panel_setting("farm_panel_title", f"🌾 REGISTRO DE FARM — {FACTION_NAME}")
        description = panel_setting(
            "farm_panel_description",
            "1. Clique em **REGISTRAR FARM**.\n2. Selecione o farm configurado e informe a quantidade.\n"
            "3. Envie a imagem de prova como anexo neste canal. Nome e ID são identificados automaticamente pela WL.",
        )
        if "informe nome e ID" in description.lower() or "na dm do bot" in description.lower() or "informe seus dados" in description.lower():
            description = ("1. Clique em **REGISTRAR FARM**.\n2. Selecione o farm configurado e informe a quantidade.\n"
                           "3. Envie a imagem de prova como anexo neste canal. Nome e ID são identificados automaticamente pela WL.")
            save_config_value("farm_panel_description", description)
        return apply_panel_media(PanelMessage(title=title[:256], description=description[:4096], color=discord.Color.green()), "farm")
    if kind == "hierarchy":
        title = panel_setting("hierarchy_panel_title", f"🏛️ HIERARQUIA — {FACTION_NAME}")
        description = panel_setting("hierarchy_panel_description", "Cargos e membros da organização, do cargo mais alto ao mais baixo.")
        return PanelMessage(title=title[:256], description=description[:4096], color=discord.Color.blurple())
    # Os demais painéis continuam disponíveis pelos comandos existentes, mas não são apresentados no /config simplificado.
    if kind == "members":
        title = panel_setting("members_panel_title", f"👥 MEMBROS — {FACTION_NAME}")
        description = panel_setting("members_panel_description", "Lista de membros cadastrados e ativos da organização.")
        return PanelMessage(title=title[:256], description=description[:4096], color=discord.Color.blurple())
    if kind == "leader":
        title = panel_setting("leader_panel_title", f"👑 PAINEL DA LIDERANÇA — {FACTION_NAME}")
        description = panel_setting("leader_panel_description", "Use os botões para registrar uma ação administrativa ou consultar os logs recentes.")
        return PanelMessage(title=title[:256], description=description[:4096], color=discord.Color.dark_gold())
    title = panel_setting("main_panel_title", f"🎛️ PAINEL PRINCIPAL — {FACTION_NAME}")
    description = panel_setting("main_panel_description", "Acesse os recursos da facção: perfil, farm, ranking, histórico e ações administrativas.")
    return PanelMessage(title=title[:256], description=description[:4096], color=discord.Color.blurple())


def config_embed(guild: Optional[discord.Guild] = None) -> PanelMessage:
    """Resumo visual das configurações, organizado por módulos (sem menu de logs)."""
    embed = PanelMessage(
        title=f"⚙️ Central de configuração · {FACTION_NAME}",
        description=(
            "Personalize os painéis da facção pelos botões abaixo.\n"
            "Selecione cargos e canais diretamente nos menus do Discord — não precisa digitar IDs.\n\n"
            "**Fluxo recomendado:** configure cada módulo e depois use o botão de publicação correspondente."
        ),
        color=discord.Color.from_rgb(76, 86, 180),
    )
    embed.set_author(name="FAC-GERAL • Administração da facção")

    def channel_label(key: str, fallback: str = "0") -> str:
        raw = panel_setting(key, fallback)
        if not raw.isdigit() or not int(raw):
            return "`Não selecionado`"
        channel = guild.get_channel(int(raw)) if guild else None
        return channel.mention if channel else f"Canal configurado (`{raw}`)"

    def role_label(role_id: int) -> str:
        if not role_id:
            return "`Não selecionado`"
        role = guild.get_role(role_id) if guild else None
        return role.mention if role else f"Cargo configurado (`{role_id}`)"

    approved_wl_role_id = int(panel_setting("member_role_id", str(MEMBER_ROLE_ID)) or 0)
    with db() as con:
        approved_wl_tag_row = con.execute(
            "SELECT value FROM settings WHERE key=?",
            (f"rank_role_{approved_wl_role_id}",),
        ).fetchone() if approved_wl_role_id else None
    approved_wl_tag = (
        f"[{str(approved_wl_tag_row['value']).split('|', 1)[0].strip()}]"
        if approved_wl_tag_row and str(approved_wl_tag_row["value"]).split("|", 1)[0].strip()
        else "⚠️ Defina a tag desse cargo em Hierarquia"
    )

    embed.add_field(
        name="📋 Whitelist",
        value=(
            f"**{panel_setting('wl_panel_title', 'Whitelist')[:90]}**\n"
            f"Publicação: {channel_label('wl_panel_channel_id', str(REGISTRATION_CHANNEL_ID))}\n"
            f"Solicitações: {channel_label('wl_channel_id', str(APPROVAL_CHANNEL_ID))}\n"
            f"Cargo temporário: {role_label(int(panel_setting('wl_pending_role_id', '0') or 0))}\n"
            f"Cargo após aprovação: {role_label(int(panel_setting('member_role_id', str(MEMBER_ROLE_ID)) or 0))}\n"
            f"Apelido automático: **[TAG] Nome RP | ID**\n"
            f"Tag do cargo pós-WL: **{approved_wl_tag}**\n"
            f"Quem decide WL: {role_label(int(panel_setting('wl_approver_role_id', str(RECRUIT_ROLE_ID)) or 0))}"
        ),
        inline=True,
    )
    embed.add_field(
        name="🌾 Farm",
        value=(
            f"**{panel_setting('farm_panel_title', 'Registro de farm')[:90]}**\n"
            f"Publicação: {channel_label('farm_panel_channel_id')}\n"
            f"Cobranças: {channel_label('farm_private_channel_id')}\n"
            f"Registros finais: {channel_label('farm_log_channel_id', str(FARM_LOG_CHANNEL_ID))}\n"
            f"Quem decide: {role_label(int(panel_setting('farm_approver_role_id', str(LEADER_ROLE_ID)) or 0))}\n"
            f"Decisão: **Liderança** (pagamento com comprovante ou recusa justificada)"
        ),
        inline=True,
    )
    hierarchy_count = len([x for x in existing_rank_config().split(';') if x.strip()])
    embed.add_field(
        name="🏛️ Hierarquia",
        value=(
            f"**{panel_setting('hierarchy_panel_title', 'Hierarquia')[:90]}**\n"
            f"Publicação: {channel_label('hierarchy_panel_channel_id')}\n"
            f"Cargos em exibição: **{hierarchy_count}**\n"
            "Ordem de publicação: cargo mais alto primeiro; cargo mais baixo por último"
        ),
        inline=True,
    )
    embed.add_field(
        name="🎭 Cargos de acesso",
        value=(
            f"Membro: {role_label(MEMBER_ROLE_ID)}\n"
            f"Recrutador: {role_label(recruiter_role_id())}\n"
            f"Liderança: {role_label(LEADER_ROLE_ID)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="🖼️ Aparência dos painéis",
        value=(
            f"WL · foto {'✅' if panel_setting('wl_panel_photo_url', '') else '—'} · banner {'✅' if panel_setting('wl_panel_banner_url', '') else '—'}\n"
            f"Farm · foto {'✅' if panel_setting('farm_panel_photo_url', '') else '—'} · banner {'✅' if panel_setting('farm_panel_banner_url', '') else '—'}"
        ),
        inline=False,
    )
    embed.set_footer(text="/config • Acesso restrito à liderança e administradores")
    return embed


def _set_modal_defaults(modal: Modal, values: list[tuple[TextInput, str]]) -> None:
    for field, value in values:
        field.default = str(value)[:field.max_length]


class PaginatedGuildObjectSelect(discord.ui.Select):
    """Dropdown de até 25 cargos/canais por página para uso em Components V2."""
    def __init__(self, parent_view: "PaginatedGuildObjectPickerView"):
        self.parent_view = parent_view
        start = parent_view.page * parent_view.PAGE_SIZE
        self.page_objects = parent_view.objects[start:start + parent_view.PAGE_SIZE]
        options = []
        for obj in self.page_objects:
            if parent_view.kind == "role":
                label = obj.name
                description = f"Cargo · posição {obj.position}"
            elif parent_view.kind == "category":
                label = f"📁 {obj.name}"
                description = "Categoria de canais"
            else:
                label = f"#{obj.name}"
                category = getattr(getattr(obj, "category", None), "name", None)
                description = f"Canal de texto · {category}" if category else "Canal de texto"
            options.append(discord.SelectOption(
                label=label[:100],
                value=str(obj.id),
                description=description[:100],
                default=(obj.id == parent_view.selected_id),
            ))
        if not options:
            options = [discord.SelectOption(
                label="Nenhuma opção encontrada", value="__none__",
                description="Não há itens disponíveis neste servidor",
            )]
        total = len(parent_view.objects)
        first = start + 1 if total else 0
        last = min(start + len(self.page_objects), total)
        super().__init__(
            placeholder=f"{parent_view.kind_label} · {first}–{last} de {total}",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        raw = self.values[0]
        if raw == "__none__":
            await interaction.response.send_message(
                None, ephemeral=True,
                view=v2_view(None, None, "Não há cargos ou canais disponíveis nesta página."),
            )
            return
        self.parent_view.selected_id = int(raw)
        self.parent_view.refresh()
        await interaction.response.edit_message(view=self.parent_view)


class PaginatedGuildObjectPickerView(ComponentsV2View):
    """Seletor reutilizável com setas para todos os cargos e canais do servidor."""
    PAGE_SIZE = 25

    def __init__(
        self,
        guild: discord.Guild,
        *,
        kind: str,
        title: str,
        owner_id: int,
        on_confirm,
        on_back=None,
        selected_id: int = 0,
        allow_clear: bool = False,
        confirm_label: str = "Confirmar seleção",
    ):
        super().__init__(timeout=900)
        if kind not in {"role", "text_channel", "category"}:
            raise ValueError(f"Tipo de seletor não suportado: {kind}")
        self.guild = guild
        self.kind = kind
        self.title = title
        self.owner_id = int(owner_id or 0)
        self.on_confirm = on_confirm
        self.on_back = on_back
        self.selected_id = int(selected_id or 0)
        self.allow_clear = bool(allow_clear)
        self.objects = self._get_objects()
        self.page = 0
        if self.selected_id:
            for index, obj in enumerate(self.objects):
                if obj.id == self.selected_id:
                    self.page = index // self.PAGE_SIZE
                    break
        self.kind_label = {
            "role": "Cargos",
            "text_channel": "Canais de texto",
            "category": "Categorias",
        }[kind]

        self.selector = PaginatedGuildObjectSelect(self)
        self.add_item(self.selector)

        self.previous_button = discord.ui.Button(
            label="◀ Anterior", style=discord.ButtonStyle.secondary,
            row=1, disabled=True,
        )
        self.next_button = discord.ui.Button(
            label="Próxima página ▶", style=discord.ButtonStyle.secondary,
            row=1, disabled=self.page_count <= 1,
        )
        self.confirm_button = discord.ui.Button(
            label=confirm_label, emoji="✅", style=discord.ButtonStyle.success,
            row=2, disabled=not self._selected_object(),
        )
        self.back_button = discord.ui.Button(
            label="Voltar", emoji="⬅️", style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.previous_button.callback = self._previous
        self.next_button.callback = self._next
        self.confirm_button.callback = self._confirm
        self.back_button.callback = self._back
        self.add_item(self.previous_button)
        self.add_item(self.next_button)
        self.add_item(self.confirm_button)
        self.add_item(self.back_button)
        if self.allow_clear:
            self.clear_button = discord.ui.Button(
                label="Limpar seleção", emoji="🗑️", style=discord.ButtonStyle.danger,
                row=3,
            )
            self.clear_button.callback = self._clear
            self.add_item(self.clear_button)
        self._refresh_text_and_buttons()

    def _get_objects(self):
        if self.kind == "role":
            return sorted(
                (role for role in self.guild.roles if not role.is_default()),
                key=lambda role: (role.position, role.name.casefold()),
                reverse=True,
            )
        if self.kind == "category":
            return sorted(self.guild.categories, key=lambda channel: (channel.position, channel.name.casefold()))
        return sorted(
            self.guild.text_channels,
            key=lambda channel: (
                getattr(getattr(channel, "category", None), "position", -1),
                channel.position,
                channel.name.casefold(),
            ),
        )

    @property
    def page_count(self) -> int:
        return max(1, math.ceil(len(self.objects) / self.PAGE_SIZE))

    def _selected_object(self):
        if not self.selected_id:
            return None
        # Retorna somente um item que realmente pertence ao tipo deste seletor.
        # Isso evita aceitar um ID antigo de canal de voz/categoria num campo de texto.
        return next((obj for obj in self.objects if obj.id == self.selected_id), None)

    def _refresh_text_and_buttons(self):
        selected = self._selected_object()
        selected_text = selected.mention if selected and hasattr(selected, "mention") else (selected.name if selected else "Nenhum selecionado")
        self.set_text(
            f"# {self.title}\n"
            f"Selecione uma opção no menu abaixo. Quando houver mais de 25 opções, use as setas para avançar ou voltar.\n\n"
            f"**Selecionado:** {selected_text}\n"
            f"**Página:** {self.page + 1}/{self.page_count} · **Opções encontradas:** {len(self.objects)}"
        )
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.page_count - 1
        self.confirm_button.disabled = selected is None

    def refresh(self):
        if hasattr(self, "selector"):
            self.remove_item(self.selector)
        self.selector = PaginatedGuildObjectSelect(self)
        self.add_item(self.selector)
        self._refresh_text_and_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id and interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                None, ephemeral=True,
                view=v2_view(None, None, "Somente quem iniciou esta configuração pode selecionar cargos ou canais."),
            )
            return False
        return True

    async def _previous(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self.refresh()
        await interaction.response.edit_message(view=self)

    async def _next(self, interaction: discord.Interaction):
        self.page = min(self.page_count - 1, self.page + 1)
        self.refresh()
        await interaction.response.edit_message(view=self)

    async def _confirm(self, interaction: discord.Interaction):
        selected = self._selected_object()
        if selected is None:
            await interaction.response.send_message(
                None, ephemeral=True,
                view=v2_view(None, None, "Selecione um cargo ou canal antes de confirmar."),
            )
            return
        await self.on_confirm(interaction, selected)

    async def _back(self, interaction: discord.Interaction):
        if self.on_back:
            await self.on_back(interaction)
        else:
            await interaction.response.edit_message(
                view=v2_view(None, None, "Seleção cancelada. Nenhuma configuração foi alterada."),
            )

    async def _clear(self, interaction: discord.Interaction):
        self.selected_id = 0
        if self.on_confirm:
            await self.on_confirm(interaction, None)


class ConfigTextChannelSelect(discord.ui.Button):
    """Abre um seletor paginado de canais de texto em vez do menu limitado do Discord."""
    def __init__(self, setting_key: str, placeholder: str, *, row: int = 0):
        super().__init__(label=placeholder[:80], style=discord.ButtonStyle.secondary, emoji="📁", row=row)
        self.setting_key = setting_key
        self.placeholder = placeholder

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use este seletor dentro do servidor."))
            return
        async def confirm(picker_interaction, channel):
            save_config_value(self.setting_key, str(channel.id if channel else 0))
            shown = channel.mention if channel else "(desconfigurado)"
            await picker_interaction.response.edit_message(
                view=v2_view(None, None, f"✅ **{self.placeholder}** salvo: {shown}"),
            )
        async def back(picker_interaction):
            await picker_interaction.response.edit_message(
                view=v2_view(None, None, "Seleção cancelada. Nenhuma configuração foi alterada."),
            )
        raw_selected_id = panel_setting(self.setting_key, "0")
        try:
            selected_id = int(raw_selected_id or 0)
        except (TypeError, ValueError):
            selected_id = 0
        picker = PaginatedGuildObjectPickerView(
            interaction.guild, kind="text_channel", title=self.placeholder,
            owner_id=interaction.user.id, on_confirm=confirm, on_back=back,
            selected_id=selected_id,
        )
        await interaction.response.send_message(view=picker, ephemeral=True)


class ConfigManualIdModal(ComponentsV2Modal, title="Configurar ID manualmente"):
    setting_key = TextInput(label="Chave da configuração", max_length=80, required=True, placeholder="Ex.: farm_log_channel_id")
    object_id = TextInput(label="ID do cargo ou canal", max_length=25, required=True, placeholder="Cole o ID numérico")

    def __init__(self, preset_key: str = "", *, farm_return: bool = False):
        super().__init__(timeout=300)
        self.setting_key.default = preset_key[:80] if preset_key else None
        self.farm_return = farm_return

    async def on_submit(self, interaction: discord.Interaction) -> None:
        key = self.setting_key.value.strip()
        raw = self.object_id.value.strip()
        allowed = {
            "wl_panel_channel_id", "wl_channel_id", "farm_panel_channel_id",
            "farm_approval_channel_id", "farm_private_channel_id", "farm_log_channel_id", "hierarchy_panel_channel_id",
            "wl_pending_role_id", "member_role_id", "wl_approver_role_id", "recruit_role_id",
            "farm_approver_role_id", "leader_role_id", "adv1_role_id", "adv2_role_id", "adv3_role_id",
        }
        if key not in allowed or not raw.isdigit():
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Chave inválida ou ID não numérico. Confira a chave e cole somente o ID."))
            return
        object_id = int(raw)
        if key.endswith("channel_id"):
            obj = interaction.guild.get_channel(object_id) if interaction.guild else None
            if obj is None or not isinstance(obj, (discord.TextChannel, discord.Thread, discord.ForumChannel)):
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse ID não corresponde a um canal de texto válido deste servidor."))
                return
            label = f"canal {obj.mention}"
        else:
            obj = interaction.guild.get_role(object_id) if interaction.guild else None
            if obj is None:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse ID não corresponde a um cargo deste servidor."))
                return
            label = f"cargo {obj.mention}"
        if self.farm_return and key in FARM_CONFIG_FIELDS:
            field_kind = FARM_CONFIG_FIELDS[key][1]
            if field_kind == "channel" and not isinstance(obj, discord.TextChannel):
                await interaction.response.send_message(
                    None, ephemeral=True, view=v2_view(None, None, "Para o Farm, informe um canal de texto deste servidor.")
                )
                return
            if field_kind == "role" and obj.is_default():
                await interaction.response.send_message(
                    None, ephemeral=True, view=v2_view(None, None, "O cargo @everyone não pode ser usado como cargo autorizador do Farm.")
                )
                return
        save_config_value(key, str(object_id))
        if self.farm_return and key in FARM_CONFIG_FIELDS:
            if interaction.guild is None:
                await interaction.response.send_message(
                    None, ephemeral=True, view=v2_view(None, None, "Use este formulário dentro do servidor.")
                )
                return
            await interaction.response.send_message(
                view=ConfigFarmChannelsV2(
                    interaction.guild,
                    selected_key=key,
                    success_message=f"✅ **{FARM_CONFIG_FIELDS[key][0]}** salvo usando {label}.",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"✅ Configuração **{key}** salva usando {label}."))


class ConfigManualIdButton(discord.ui.Button):
    def __init__(self, *, row: int = 4, preset_key: str = "", farm_return: bool = False):
        super().__init__(label="Informar ID manualmente", style=discord.ButtonStyle.secondary, emoji="🔢", row=row)
        self.preset_key = preset_key
        self.farm_return = farm_return

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            ConfigManualIdModal(self.preset_key, farm_return=self.farm_return)
        )


class ConfigWLChannelsView(ComponentsV2View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(ConfigTextChannelSelect("wl_panel_channel_id", "Canal onde o painel WL será publicado", row=0))
        self.add_item(ConfigTextChannelSelect("wl_channel_id", "Canal que receberá os pedidos de WL", row=1))
        self.add_item(ConfigManualIdButton(preset_key="wl_panel_channel_id"))


class ConfigWLRoleView(ComponentsV2View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(ConfigSingleRoleSelect("wl_pending_role_id", "Cargo temporário antes da aprovação da WL", row=0))
        self.add_item(ConfigSingleRoleSelect("member_role_id", "Cargo adicionado quando a WL for aprovada", row=1))
        self.add_item(ConfigSingleRoleSelect("wl_approver_role_id", "Cargo autorizado a aprovar ou recusar WL", row=2))
        self.add_item(ConfigSingleRoleSelect("recruit_role_id", "Cargo cujos membros aparecem como recrutadores", row=3))
        self.add_item(ConfigManualIdButton(preset_key="member_role_id"))


class ConfigFarmChannelsView(ComponentsV2View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(ConfigTextChannelSelect("farm_panel_channel_id", "Canal público do painel Farm", row=0))
        self.add_item(ConfigTextChannelSelect("farm_private_channel_id", "Canal privado de cobranças e pagamentos", row=1))
        self.add_item(ConfigSingleRoleSelect("farm_approver_role_id", "Cargo autorizado a decidir cobranças", row=2))
        self.add_item(ConfigTextChannelSelect("farm_log_channel_id", "Canal de histórico final do Farm", row=3))
        self.add_item(ConfigManualIdButton(preset_key="farm_private_channel_id"))


class ConfigSingleRoleSelect(discord.ui.Button):
    def __init__(self, setting_key: str, placeholder: str, *, row: int):
        super().__init__(label=placeholder[:80], style=discord.ButtonStyle.secondary, emoji="🎭", row=row)
        self.setting_key = setting_key
        self.placeholder = placeholder

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use este seletor dentro do servidor."))
            return
        async def confirm(picker_interaction, role):
            save_config_value(self.setting_key, str(role.id if role else 0))
            shown = role.mention if role else "(desconfigurado)"
            await picker_interaction.response.edit_message(
                view=v2_view(None, None, f"✅ **{self.placeholder}** salvo: {shown}"),
            )
        async def back(picker_interaction):
            await picker_interaction.response.edit_message(
                view=v2_view(None, None, "Seleção cancelada. Nenhuma configuração foi alterada."),
            )
        raw_selected_id = panel_setting(self.setting_key, "0")
        try:
            selected_id = int(raw_selected_id or 0)
        except (TypeError, ValueError):
            selected_id = 0
        picker = PaginatedGuildObjectPickerView(
            interaction.guild, kind="role", title=self.placeholder,
            owner_id=interaction.user.id, on_confirm=confirm, on_back=back,
            selected_id=selected_id, allow_clear=True,
        )
        await interaction.response.send_message(view=picker, ephemeral=True)


class ConfigRolesSelectView(ComponentsV2View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(ConfigSingleRoleSelect("member_role_id", "Selecionar cargo padrão dos membros", row=0))
        self.add_item(ConfigSingleRoleSelect("recruit_role_id", "Selecionar cargo autorizado a recrutar WL", row=1))
        self.add_item(ConfigSingleRoleSelect("leader_role_id", "Selecionar cargo de liderança", row=2))
        self.add_item(ConfigManualIdButton(preset_key="leader_role_id"))


HIERARCHY_ROLES_PER_PAGE = 25


class ConfigHierarchyChannelSelect(discord.ui.Button):
    def __init__(self, parent_view: "ConfigHierarchySetupView"):
        super().__init__(
            label="Selecionar canal de publicação",
            style=discord.ButtonStyle.secondary, emoji="📣", row=0,
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction) -> None:
        async def confirm(picker_interaction, channel):
            self.parent_view.channel_id = channel.id
            await picker_interaction.response.edit_message(
                view=v2_view(self.parent_view.build_embed(), self.parent_view, None),
                content=None, embeds=[], attachments=(picker_interaction.message.attachments if picker_interaction.message else []),
            )
        async def back(picker_interaction):
            await picker_interaction.response.edit_message(
                view=v2_view(self.parent_view.build_embed(), self.parent_view, None),
                content=None, embeds=[], attachments=(picker_interaction.message.attachments if picker_interaction.message else []),
            )
        picker = PaginatedGuildObjectPickerView(
            interaction.guild, kind="text_channel", title="Canal de publicação da hierarquia",
            owner_id=interaction.user.id, on_confirm=confirm, on_back=back,
            selected_id=getattr(self.parent_view, "channel_id", 0) or 0,
        )
        await interaction.response.send_message(view=picker, ephemeral=True)


class ConfigHierarchyRolePageSelect(discord.ui.Select):
    """Lista 25 cargos por página; preserva seleção entre páginas para servidores grandes."""
    def __init__(self, parent_view: "ConfigHierarchySetupView"):
        self.parent_view = parent_view
        start = parent_view.page_index * HIERARCHY_ROLES_PER_PAGE
        self.page_roles = parent_view.roles[start:start + HIERARCHY_ROLES_PER_PAGE]
        options = [
            discord.SelectOption(
                label=role.name[:100],
                value=str(role.id),
                description=f"Posição na hierarquia: {role.position}"[:100],
                default=role.id in parent_view.selected_role_ids,
            )
            for role in self.page_roles
        ]
        if not options:
            options = [discord.SelectOption(label="Nenhum cargo disponível", value="none")]
        super().__init__(
            placeholder=f"② Cargos {start + 1}–{start + len(self.page_roles)} • maior → menor",
            min_values=0,
            max_values=max(1, len(options)),
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        visible_ids = {role.id for role in self.page_roles}
        # A ordem é sempre normalizada pela posição do Discord: maior primeiro.
        self.parent_view.selected_role_ids = [
            rid for rid in self.parent_view.selected_role_ids if rid not in visible_ids
        ]
        for raw_id in self.values:
            if raw_id != "none":
                try:
                    rid = int(raw_id)
                    if rid not in self.parent_view.selected_role_ids:
                        self.parent_view.selected_role_ids.append(rid)
                except ValueError:
                    pass
        self.parent_view.selected_role_ids.sort(
            key=lambda rid: (
                self.parent_view.guild.get_role(rid).position
                if self.parent_view.guild.get_role(rid) else -1,
                self.parent_view.guild.get_role(rid).name.casefold()
                if self.parent_view.guild.get_role(rid) else "",
            ),
            reverse=True,
        )
        self.parent_view.remove_page_index = min(
            self.parent_view.remove_page_index,
            max(0, math.ceil(len(self.parent_view.selected_role_ids) / 25) - 1),
        )
        self.parent_view._persist_selected_roles()
        self.parent_view._refresh_remove_selector()
        await interaction.response.edit_message(view=v2_view(self.parent_view.build_embed(), self.parent_view, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))


class ConfigHierarchyAddRoleModal(ComponentsV2Modal, title="Adicionar cargo por ID"):
    role_id_input = TextInput(label="ID do cargo", max_length=25, required=True, placeholder="Cole o ID do cargo")
    def __init__(self, parent_view):
        super().__init__(timeout=300)
        self.parent_view = parent_view
    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.role_id_input.value.strip()
        role = self.parent_view.guild.get_role(int(raw)) if raw.isdigit() else None
        if role is None or role.id not in {item.id for item in selectable_hierarchy_roles(self.parent_view.guild)}:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "ID inválido: use um cargo não gerenciado e abaixo do cargo do bot."))
            return
        if role.id not in self.parent_view.selected_role_ids:
            self.parent_view.selected_role_ids.append(role.id)
        self.parent_view.selected_role_ids.sort(
            key=lambda rid: self.parent_view.guild.get_role(rid).position
            if self.parent_view.guild.get_role(rid) else -1,
            reverse=True,
        )
        self.parent_view._persist_selected_roles()
        self.parent_view._refresh_role_page()
        await interaction.response.edit_message(view=v2_view(self.parent_view.build_embed(), self.parent_view, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))

class ConfigHierarchyAddRoleButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Adicionar cargo por ID", style=discord.ButtonStyle.secondary, emoji="🔢", row=4)
    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ConfigHierarchyAddRoleModal(self.view))

class ConfigHierarchyRemoveSelect(discord.ui.Select):
    def __init__(self, parent_view):
        self.parent_view = parent_view
        roles = [parent_view.guild.get_role(rid) for rid in parent_view.selected_role_ids]
        roles = [r for r in roles if r]
        start = parent_view.remove_page_index * 25
        self.page_roles = roles[start:start + 25]
        pages = max(1, math.ceil(len(roles) / 25))
        options = [discord.SelectOption(label=r.name[:100], value=str(r.id)) for r in self.page_roles]
        if not options:
            options = [discord.SelectOption(label="Nenhum cargo selecionado", value="none")]
        super().__init__(
            placeholder=f"❌ Remover cargo • página {parent_view.remove_page_index + 1}/{pages}",
            min_values=0, max_values=1, options=options, row=2,
        )
    async def callback(self, interaction: discord.Interaction) -> None:
        raw = self.values[0] if self.values else "none"
        if raw != "none":
            rid = int(raw)
            self.parent_view.selected_role_ids = [x for x in self.parent_view.selected_role_ids if x != rid]
        self.parent_view._persist_selected_roles()
        self.parent_view._refresh_remove_selector()
        await interaction.response.edit_message(view=v2_view(self.parent_view.build_embed(), self.parent_view, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))


class ConfigHierarchySetupView(ComponentsV2View):
    """Configuração em lote: seleciona vários cargos por página, sem confirmar um a um."""
    def __init__(self, guild: discord.Guild, required_count: Optional[int] = None):
        super().__init__(timeout=900)
        self.guild = guild
        self.required_count = required_count
        # Usa somente cargos que o bot pode administrar; @everyone e cargos
        # gerenciados/fora da hierarquia do bot não são opções válidas.
        self.roles = selectable_hierarchy_roles(guild)
        self.page_index = 0
        self.remove_page_index = 0
        with db() as con:
            rows = con.execute("SELECT key,value FROM settings WHERE key LIKE 'rank_role_%'").fetchall()
        saved_roles = []
        selectable_ids = {role.id for role in self.roles}
        for row in rows:
            try:
                role_id = int(row["key"].replace("rank_role_", "", 1))
                _abbr, priority = row["value"].split("|", 1)
                if role_id in selectable_ids:
                    saved_roles.append((int(priority), role_id))
            except (ValueError, TypeError):
                continue
        self.selected_role_ids: list[int] = [
            role_id for _priority, role_id in sorted(saved_roles, reverse=True)
        ]
        self.role_selector = ConfigHierarchyRolePageSelect(self)
        self.add_item(self.role_selector)
        self.remove_selector = ConfigHierarchyRemoveSelect(self)
        self.add_item(self.remove_selector)
        self._update_pager_buttons()

    @property
    def total_pages(self) -> int:
        return max(1, math.ceil(len(self.roles) / HIERARCHY_ROLES_PER_PAGE))

    @property
    def remove_total_pages(self) -> int:
        return max(1, math.ceil(len(self.selected_role_ids) / 25))

    def _update_pager_buttons(self) -> None:
        self.previous_page.disabled = self.page_index <= 0 and self.remove_page_index <= 0
        self.next_page.disabled = (
            self.page_index >= self.total_pages - 1
            and self.remove_page_index >= self.remove_total_pages - 1
        )

    def _persist_selected_roles(self) -> None:
        """Salva a seleção atual para não perder cargos ao voltar ou fechar o painel."""
        selected_roles = [self.guild.get_role(rid) for rid in self.selected_role_ids]
        selected_roles = [role for role in selected_roles if role is not None]
        with db() as con:
            con.execute("DELETE FROM settings WHERE key LIKE 'rank_role_%'")
            for idx, role in enumerate(selected_roles):
                priority = len(selected_roles) - idx
                words = [part for part in re.split(r"[^A-Za-z0-9À-ÿ]+", role.name.upper()) if part]
                abbreviation = ("".join(word[0] for word in words) if len(words) > 1 else (words[0][:3] if words else "RANK"))[:12]
                con.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?)",
                    (f"rank_role_{role.id}", f"{abbreviation or 'RANK'}|{priority}"),
                )

    def _refresh_role_page(self) -> None:
        self.remove_item(self.role_selector)
        self.role_selector = ConfigHierarchyRolePageSelect(self)
        self.add_item(self.role_selector)
        self._refresh_remove_selector()
        self._update_pager_buttons()

    def _refresh_remove_selector(self) -> None:
        if hasattr(self, "remove_selector"):
            self.remove_item(self.remove_selector)
        self.remove_selector = ConfigHierarchyRemoveSelect(self)
        self.add_item(self.remove_selector)

    def build_embed(self, *, saved: bool = False) -> PanelMessage:
        selected_roles = [self.guild.get_role(rid) for rid in self.selected_role_ids]
        selected_roles = [role for role in selected_roles if role is not None]
        # A lista selecionada e publicada usa sempre topo primeiro e base por último.
        display_roles = selected_roles
        selected_text = "\n".join(
            f"`{index + 1}.` {role.mention}"
            for index, role in enumerate(display_roles[:18])
        )
        if len(selected_roles) > 18:
            selected_text += f"\n… e mais {len(selected_roles) - 18} cargo(s) selecionado(s)."
        if not selected_text:
            selected_text = "Nenhum cargo selecionado ainda."
        embed = PanelMessage(
            title="🏛️ Configurar hierarquia",
            description=(
                "Selecione os cargos no menu. Não existe limite de quantidade pré-definida. "
                "Use o menu **Remover cargo** para retirar uma seleção incorreta; o botão Reiniciar limpa tudo.\n\n"
                "A ordem é automática pela posição real do Discord: o cargo mais alto fica primeiro e o mais baixo por último."
            ),
            color=discord.Color.from_rgb(76, 86, 180),
        )
        embed.add_field(name="📄 Página de cargos", value=f"**{self.page_index + 1}/{self.total_pages}** · {len(self.roles)} cargo(s) disponíveis", inline=True)
        embed.add_field(name="✅ Selecionados", value=f"**{len(selected_roles)} cargo(s)**", inline=True)
        embed.add_field(name="📋 Hierarquia (topo → base)", value=selected_text[:1024], inline=False)
        if saved:
            embed.set_footer(text="Configuração salva • maior cargo primeiro; menor cargo por último")
        else:
            embed.set_footer(text="A seleção é salva automaticamente • maior cargo primeiro; menor por último")
        return embed

    @discord.ui.button(label="◀ Anterior", style=discord.ButtonStyle.secondary, emoji="⬅️", row=3)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page_index = max(0, self.page_index - 1)
        self.remove_page_index = max(0, self.remove_page_index - 1)
        self._refresh_role_page()
        await interaction.response.edit_message(view=v2_view(self.build_embed(), self, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))

    @discord.ui.button(label="Próxima ▶", style=discord.ButtonStyle.secondary, emoji="➡️", row=3)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page_index = min(self.total_pages - 1, self.page_index + 1)
        self.remove_page_index = min(self.remove_total_pages - 1, self.remove_page_index + 1)
        self._refresh_role_page()
        await interaction.response.edit_message(view=v2_view(self.build_embed(), self, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))

    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.secondary, emoji="⬅️", row=4)
    async def back_to_hierarchy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._persist_selected_roles()
        await interaction.response.send_message(view=HierarchyGuideV2(self.guild), ephemeral=True)

    @discord.ui.button(label="Por ID", style=discord.ButtonStyle.primary, emoji="🔢", row=4)
    async def add_role_by_id(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ConfigHierarchyAddRoleModal(self))

    @discord.ui.button(label="Recomeçar", style=discord.ButtonStyle.danger, emoji="🔄", row=4)
    async def reset_hierarchy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.selected_role_ids.clear()
        with db() as con:
            con.execute("DELETE FROM settings WHERE key LIKE 'rank_role_%'")
        self._refresh_role_page()
        await interaction.response.edit_message(view=v2_view(self.build_embed(), self, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))


WL_CONFIG_FIELDS = {
    "wl_pending_role_id": ("Cargo temporário antes da aprovação", "role", True),
    "member_role_id": ("Cargo aplicado após aprovação", "role", False),
    "wl_approver_role_id": ("Cargo autorizado a aprovar/reprovar", "role", False),
    "recruit_role_id": ("Cargo cujos membros podem recrutar", "role", False),
    "wl_panel_channel_id": ("Canal onde o painel será publicado", "channel", False),
    "wl_channel_id": ("Canal que receberá as solicitações", "channel", False),
}


class WLFieldSelect(discord.ui.Select):
    def __init__(self, parent):
        self.parent_view = parent
        keys = parent.stage_keys
        options = [discord.SelectOption(label=WL_CONFIG_FIELDS[k][0][:100], value=k, default=(k == parent.selected_key)) for k in keys]
        super().__init__(placeholder="① Escolha o que deseja configurar", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_key = self.values[0]
        self.parent_view.page = 0
        self.parent_view.refresh()
        await interaction.response.edit_message(view=self.parent_view, content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))


class WLTargetSelect(discord.ui.Select):
    def __init__(self, parent):
        self.parent_view = parent
        objects = parent.get_objects()
        start = parent.page * 25
        page_objects = objects[start:start + 25]
        options = [discord.SelectOption(label=(obj.name if parent.kind == "role" else f"#{obj.name}")[:100], value=str(obj.id), description=(f"Cargo · posição {obj.position}" if parent.kind == "role" else "Canal de texto")[:100]) for obj in page_objects]
        if not options:
            options = [discord.SelectOption(label="Nenhuma opção encontrada", value="none")]
        super().__init__(placeholder=f"② Selecione a opção • página {parent.page + 1}/{parent.page_count}", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        raw = self.values[0]
        if raw == "none":
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Não há opções disponíveis nesta página."))
            return
        save_config_value(self.parent_view.selected_key, raw)
        obj = self.parent_view.guild.get_role(int(raw)) if self.parent_view.kind == "role" else self.parent_view.guild.get_channel(int(raw))
        shown = obj.mention if obj else f"`{raw}`"
        self.parent_view.success_message = f"✅ **{WL_CONFIG_FIELDS[self.parent_view.selected_key][0]}** salvo: {shown}"
        self.parent_view.refresh()
        await interaction.response.edit_message(view=self.parent_view, content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))


class WLPageButton(discord.ui.Button):
    def __init__(self, parent, delta):
        self.parent_view, self.delta = parent, delta
        super().__init__(label="◀ Anterior" if delta < 0 else "Próxima ▶", style=discord.ButtonStyle.secondary, disabled=(parent.page + delta < 0 or parent.page + delta >= parent.page_count), row=2)

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.page = max(0, min(self.parent_view.page_count - 1, self.parent_view.page + self.delta))
        self.parent_view.refresh()
        await interaction.response.edit_message(view=self.parent_view, content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))


class WLStageButton(discord.ui.Button):
    def __init__(self, parent, delta):
        self.parent_view, self.delta = parent, delta
        super().__init__(label="Voltar" if delta < 0 else ("Próximo" if parent.stage < 2 else "Publicar painel"), emoji="⬅️" if delta < 0 else ("➡️" if parent.stage < 2 else "📨"), style=discord.ButtonStyle.primary if delta > 0 else discord.ButtonStyle.secondary, row=3)

    async def callback(self, interaction: discord.Interaction):
        if self.parent_view.stage == 0 and self.delta > 0 and not self.parent_view.roles_ready():
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Defina os cargos obrigatórios antes de avançar. O cargo temporário é opcional."))
            return
        if self.parent_view.stage == 1 and self.delta > 0 and not self.parent_view.channels_ready():
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Defina os dois canais da WL antes de avançar."))
            return
        if self.parent_view.stage == 2 and self.delta > 0:
            await publish_configured_panel(interaction, "wl")
            return
        self.parent_view.stage = max(0, min(2, self.parent_view.stage + self.delta))
        self.parent_view.selected_key = self.parent_view.stage_keys[0]
        self.parent_view.page = 0
        self.parent_view.refresh()
        await interaction.response.edit_message(view=self.parent_view, content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))


class WLConfigV2(ComponentsV2LayoutView):
    """Assistente WL compacto em Components V2, com menus e paginação por setas."""
    def __init__(self, guild, success_message="", *, stage=0, selected_key=None, page=0):
        super().__init__(timeout=900)
        self.guild, self.stage, self.page = guild, stage, page
        self.success_message = success_message
        self.selected_key = selected_key or self.stage_keys[0]
        self.refresh()

    @property
    def stage_keys(self):
        return list(WL_CONFIG_FIELDS)[:4] if self.stage == 0 else (list(WL_CONFIG_FIELDS)[4:] if self.stage == 1 else list(WL_CONFIG_FIELDS))

    @property
    def kind(self):
        return WL_CONFIG_FIELDS[self.selected_key][1]

    def get_objects(self):
        if self.kind == "role":
            return sorted((r for r in self.guild.roles if not r.is_default()), key=lambda r: (r.position, r.name.casefold()), reverse=True)
        return sorted(self.guild.text_channels, key=lambda c: (getattr(c.category, "position", -1), c.position, c.name.casefold()))

    @property
    def page_count(self):
        return max(1, math.ceil(len(self.get_objects()) / 25))

    def _saved(self, key):
        fallbacks = {
            "wl_panel_channel_id": str(REGISTRATION_CHANNEL_ID),
            "wl_channel_id": str(APPROVAL_CHANNEL_ID),
            "member_role_id": str(MEMBER_ROLE_ID),
            "wl_approver_role_id": str(RECRUIT_ROLE_ID),
            "recruit_role_id": str(recruiter_role_id()),
            "wl_pending_role_id": "0",
        }
        raw = panel_setting(key, fallbacks.get(key, "0"))
        try: oid = int(raw or 0)
        except (TypeError, ValueError): return None
        return (self.guild.get_role(oid) if WL_CONFIG_FIELDS[key][1] == "role" else self.guild.get_channel(oid)) if oid else None

    def roles_ready(self):
        return all(self._saved(k) is not None for k in list(WL_CONFIG_FIELDS)[:4] if not WL_CONFIG_FIELDS[k][2])

    def channels_ready(self):
        return all(self._saved(k) is not None for k in list(WL_CONFIG_FIELDS)[4:])

    def refresh(self):
        self.clear_items()
        stage_titles = ["Etapa 1/3 — Cargos", "Etapa 2/3 — Canais", "Etapa 3/3 — Revisão"]
        header = "## 📋 Configuração da Whitelist\n" + stage_titles[self.stage]
        if self.success_message:
            header += "\n" + self.success_message
        parts = [discord.ui.TextDisplay(header)]
        if self.stage < 2:
            keys = self.stage_keys
            summary = []
            for k in keys:
                obj = self._saved(k)
                optional = " *(opcional)*" if WL_CONFIG_FIELDS[k][2] else ""
                summary.append(("✅" if obj else "▫️") + f" **{WL_CONFIG_FIELDS[k][0]}:** {obj.mention if obj else 'Não definido'}{optional}")
            objects = self.get_objects()
            parts.extend([
                discord.ui.TextDisplay("**Configurações desta etapa**\n" + "\n".join(summary)),
                discord.ui.Separator(),
                discord.ui.ActionRow(WLFieldSelect(self)),
                discord.ui.ActionRow(WLTargetSelect(self)),
                discord.ui.TextDisplay(f"Página **{self.page + 1}/{self.page_count}** · {len(objects)} opções disponíveis"),
                discord.ui.ActionRow(WLPageButton(self, -1), WLPageButton(self, 1)),
            ])
            can_advance = self.roles_ready() if self.stage == 0 else self.channels_ready()
            parts.append(discord.ui.TextDisplay("✅ Etapa concluída. Você já pode avançar." if can_advance else "🔒 Defina os campos obrigatórios para liberar **Próximo**."))
            nav = []
            if self.stage > 0:
                nav.append(WLStageButton(self, -1))
            if can_advance:
                nav.append(WLStageButton(self, 1))
            if nav:
                parts.append(discord.ui.ActionRow(*nav))
        else:
            lines = []
            for k, (label, _kind, optional) in WL_CONFIG_FIELDS.items():
                obj = self._saved(k)
                lines.append(("✅" if obj else ("▫️" if optional else "❌")) + f" **{label}:** {obj.mention if obj else ('Opcional' if optional else 'Não definido')}")
            parts.extend([
                discord.ui.TextDisplay("**Resumo final**\n" + "\n".join(lines)),
                discord.ui.Separator(),
                discord.ui.TextDisplay("Revise os dados. O painel só será publicado quando todos os campos obrigatórios estiverem configurados."),
                discord.ui.ActionRow(discord.ui.Button(label="Editar aparência", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="fac:wl-v2:appearance")),
            ])
            parts.append(discord.ui.ActionRow(WLStageButton(self, -1)))
            if self.roles_ready() and self.channels_ready():
                parts.append(discord.ui.ActionRow(WLStageButton(self, 1)))
            else:
                parts.append(discord.ui.TextDisplay("🔒 Complete as etapas 1 e 2 para liberar a publicação."))
        container = discord.ui.Container(*parts)
        self.add_item(container)
        for child in container.children:
            if isinstance(child, discord.ui.ActionRow):
                for button in child.children:
                    if getattr(button, "custom_id", "") == "fac:wl-v2:appearance":
                        button.callback = self.edit_appearance

    async def edit_appearance(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ConfigWLPanelModal())


class ConfigWLPanelModal(ComponentsV2Modal, title="Configurar painel de WL"):
    title_input = TextInput(label="Título do painel WL", max_length=256, required=True)
    description_input = TextInput(label="Descrição do painel WL", style=discord.TextStyle.paragraph, max_length=3500, required=True)
    photo_url = TextInput(label="URL da foto / miniatura (opcional)", max_length=500, required=False, placeholder="https://.../foto.png")
    banner_url = TextInput(label="URL do banner (opcional)", max_length=500, required=False, placeholder="https://.../banner.png")

    def __init__(self):
        super().__init__(timeout=300)
        _set_modal_defaults(self, [
            (self.title_input, panel_setting("wl_panel_title", f"📋 WHITELIST — {FACTION_NAME}")),
            (self.description_input, panel_setting("wl_panel_description", "Clique em Fazer WL, escolha o recrutador e informe nome RP e ID FiveM.")),
            (self.photo_url, panel_setting("wl_panel_photo_url", "")),
            (self.banner_url, panel_setting("wl_panel_banner_url", "")),
        ])

    async def on_submit(self, interaction: discord.Interaction) -> None:
        photo, banner = self.photo_url.value.strip(), self.banner_url.value.strip()
        if not valid_asset_url(photo) or not valid_asset_url(banner):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Foto e banner precisam ser URLs públicas HTTPS, ou deixe o campo vazio para remover a imagem."))
            return
        save_config_value("wl_panel_title", self.title_input.value.strip() or f"WL — {FACTION_NAME}")
        save_config_value("wl_panel_description", self.description_input.value.strip())
        save_config_value("wl_panel_photo_url", photo)
        save_config_value("wl_panel_banner_url", banner)
        if not interaction.guild:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use esta configuração dentro do servidor."))
            return
        await interaction.response.send_message(
            view=WLConfigV2(interaction.guild, success_message="✅ Aparência salva. Continue configurando a WL por etapas."),
            ephemeral=True,
        )


class ConfigHierarchyModal(ComponentsV2Modal, title="Aparência da hierarquia"):
    title_input = TextInput(label="Título da hierarquia", max_length=256, required=True)
    description_input = TextInput(label="Descrição da hierarquia", style=discord.TextStyle.paragraph, max_length=1500, required=True)

    def __init__(self):
        super().__init__(timeout=300)
        _set_modal_defaults(self, [
            (self.title_input, panel_setting("hierarchy_panel_title", f"🏛️ HIERARQUIA — {FACTION_NAME}")),
            (self.description_input, panel_setting("hierarchy_panel_description", "Cargos e membros da organização, do mais alto ao mais baixo.")),
        ])

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use este formulário dentro do servidor."))
            return
        save_config_value("hierarchy_panel_title", self.title_input.value.strip() or f"HIERARQUIA — {FACTION_NAME}")
        save_config_value("hierarchy_panel_description", self.description_input.value.strip())
        setup_view = ConfigHierarchySetupView(interaction.guild)
        await interaction.response.send_message(view=v2_view(setup_view.build_embed(), setup_view, None), ephemeral=True)


# O fluxo antigo de seleção cargo-por-cargo foi removido: agora a hierarquia é configurada em lote por páginas.

class ConfigFarmPanelModal(ComponentsV2Modal, title="Configurar painel e aprovação de farm"):
    title_input = TextInput(label="Título do painel farm", max_length=256, required=True)
    description_input = TextInput(label="Descrição/instruções do farm", style=discord.TextStyle.paragraph, max_length=2500, required=True)
    photo_url = TextInput(label="URL da foto / miniatura (opcional)", max_length=500, required=False, placeholder="https://.../foto.png")
    banner_url = TextInput(label="URL do banner (opcional)", max_length=500, required=False, placeholder="https://.../banner.png")

    def __init__(self):
        super().__init__(timeout=300)
        _set_modal_defaults(self, [
            (self.title_input, panel_setting("farm_panel_title", f"🌾 REGISTRO DE FARM — {FACTION_NAME}")),
            (self.description_input, panel_setting("farm_panel_description", "Clique em REGISTRAR FARM, selecione o item, informe a quantidade e envie a imagem de prova neste canal. Nome e ID são identificados automaticamente pela WL.")),
            (self.photo_url, panel_setting("farm_panel_photo_url", "")),
            (self.banner_url, panel_setting("farm_panel_banner_url", "")),
        ])

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                None, ephemeral=True, view=v2_view(None, None, "Use este formulário dentro de um servidor.")
            )
            return
        photo, banner = self.photo_url.value.strip(), self.banner_url.value.strip()
        if not valid_asset_url(photo) or not valid_asset_url(banner):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Foto e banner precisam ser URLs públicas HTTPS, ou deixe o campo vazio para remover a imagem."))
            return
        save_config_value("farm_panel_title", self.title_input.value.strip() or f"FARM — {FACTION_NAME}")
        save_config_value("farm_panel_description", self.description_input.value.strip())
        save_config_value("farm_panel_photo_url", photo)
        save_config_value("farm_panel_banner_url", banner)
        await interaction.response.send_message(
            view=ConfigFarmChannelsV2(
                interaction.guild,
                success_message="✅ Aparência do painel salva. Agora configure os canais e as permissões do Farm abaixo."
            ),
            ephemeral=True,
        )




class ConfigLogsModal(ComponentsV2Modal, title="Canais de logs"):
    admin_channel = TextInput(label="ID do canal de logs administrativos", max_length=22, required=True)
    public_channel = TextInput(label="ID do canal de logs públicos", max_length=22, required=True)
    farm_channel = TextInput(label="ID do canal de logs de farm", max_length=22, required=True)

    def __init__(self):
        super().__init__(timeout=300)
        _set_modal_defaults(self, [(self.admin_channel, ADMIN_LOG_CHANNEL_ID), (self.public_channel, PUBLIC_LOG_CHANNEL_ID), (self.farm_channel, FARM_LOG_CHANNEL_ID)])

    async def on_submit(self, interaction: discord.Interaction) -> None:
        vals = {"admin_log_channel_id": self.admin_channel.value.strip(), "public_log_channel_id": self.public_channel.value.strip(), "farm_log_channel_id": self.farm_channel.value.strip()}
        if any(not v.isdigit() for v in vals.values()):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Todos os IDs devem ser numéricos. Use 0 para desativar.")); return
        for k, v in vals.items(): save_config_value(k, v)
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "✅ Canais de logs administrativos, públicos e de farm salvos."))




class FarmProofRejectModal(ComponentsV2Modal, title="Recusar comprovante de farm"):
    reason = TextInput(label="Motivo obrigatório", style=discord.TextStyle.paragraph, min_length=3, max_length=500, required=True)
    def __init__(self, proof_id: int):
        super().__init__(timeout=300)
        self.proof_id = proof_id
    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not can_decide_farm(interaction):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você não possui o cargo configurado para aprovar ou recusar comprovantes de farm.")); return
        with db() as con:
            row = con.execute("SELECT * FROM farm_proofs WHERE id=?", (self.proof_id,)).fetchone()
            if not row or row["status"] != "PENDING":
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse comprovante já foi analisado ou não existe.")); return
            cur = con.execute("UPDATE farm_proofs SET status='REJECTED',reviewed_by=?,reviewed_at=?,review_reason=? WHERE id=? AND status='PENDING'", (str(interaction.user.id), now_iso(), self.reason.value.strip(), self.proof_id))
            if not cur.rowcount:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse comprovante já foi analisado.")); return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        log_action(interaction.user.id, "FARM_COMPROVANTE_RECUSADO", int(row["discord_id"]), self.reason.value.strip(), f"Comprovante #{self.proof_id}")
        if interaction.guild:
            await send_channel_log(interaction.guild, "🌾 Comprovante de farm recusado", f"Comprovante: `#{self.proof_id}`\nMembro: <@{row['discord_id']}>\nMotivo: {self.reason.value.strip()}", color=discord.Color.red())
        await update_farm_review_message(row, status="REJECTED", reviewer=interaction.user.mention, reason=self.reason.value.strip())
        try:
            user = await bot.fetch_user(int(row["discord_id"]))
            await user.send(None, view=v2_view(None, None, f"❌ Seu comprovante de farm **#{self.proof_id}** foi recusado. Motivo: {self.reason.value.strip()}"))
        except discord.HTTPException:
            pass
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, f"Comprovante #{self.proof_id} recusado e registrado."))


async def update_farm_review_message(row, *, status: str, reviewer: str, reason: str = "") -> None:
    """Atualiza o cartão V2 de análise e remove os botões após a decisão."""
    channel_id = int(row["review_channel_id"] or 0)
    message_id = int(row["review_message_id"] or 0)
    channel = bot.get_channel(channel_id) if channel_id else None
    if not isinstance(channel, discord.TextChannel) and channel_id:
        guild = bot.get_guild(GUILD_ID) if GUILD_ID else (bot.guilds[0] if len(bot.guilds) == 1 else None)
        channel = guild.get_channel(channel_id) if guild else None
        if channel is None and guild:
            try:
                channel = await guild.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None
    if not isinstance(channel, discord.TextChannel) or not message_id:
        return
    try:
        msg = await channel.fetch_message(message_id)
        approved = status == "APPROVED"
        embed = PanelMessage(
            title=f"{'✅ APROVADO' if approved else '❌ RECUSADO'} · COMPROVANTE DE FARM #{row['id']}",
            description=(
                f"**Membro:** <@{row['discord_id']}> · **{row['character_name']}**\n"
                f"**ID FiveM:** `{row['game_id']}`\n"
                f"**Item:** {row['item_name'] or 'Não informado'}\n"
                f"**Quantidade:** {row['quantity'] or 0}\n"
                f"**Pontos:** {row['points'] or 0}\n"
                f"**Valor total:** R$ {int(row['total_cents'] or 0) / 100:.2f}\n"
                f"**Analisado por:** {reviewer}"
                + (f"\n**Motivo:** {reason[:900]}" if reason else "")
            ),
            color=discord.Color.green() if approved else discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        if msg.attachments:
            embed.set_image(url=f"attachment://{msg.attachments[0].filename}")
        await msg.edit(
            content=None,
            embeds=[],
            attachments=msg.attachments,
            view=v2_view(embed, None, None),
        )
    except discord.HTTPException:
        log.exception("Não foi possível atualizar a mensagem do comprovante #%s", row["id"])


class FarmInvoiceRejectModal(ComponentsV2Modal, title="Recusar cobrança de farm"):
    reason = TextInput(label="Motivo da recusa", style=discord.TextStyle.paragraph, min_length=5, max_length=500, required=True)
    def __init__(self, proof_id: int):
        super().__init__(timeout=300)
        self.proof_id = proof_id
    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not can_decide_farm(interaction):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você não possui o cargo autorizado para decidir cobranças de farm.")); return
        with db() as con:
            row = con.execute("SELECT * FROM farm_proofs WHERE id=?", (self.proof_id,)).fetchone()
            if not row or row["status"] != "PENDING":
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Essa cobrança já foi finalizada ou não existe.")); return
            cur = con.execute("UPDATE farm_proofs SET status='REJECTED',reviewed_by=?,reviewed_at=?,review_reason=? WHERE id=? AND status='PENDING'", (str(interaction.user.id), now_iso(), self.reason.value.strip(), self.proof_id))
            if not cur.rowcount:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Essa cobrança já foi decidida por outra pessoa.")); return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        log_action(interaction.user.id, "FARM_COBRANCA_RECUSADA", int(row["discord_id"]), self.reason.value.strip(), f"Cobrança #{self.proof_id}")
        await finalize_farm_invoice(row, "REJECTED", interaction.user, reason=self.reason.value.strip())
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, f"Cobrança #{self.proof_id} recusada e registrada."))


class FarmInvoiceConfirmView(ComponentsV2View):
    def __init__(self, proof_id: int):
        super().__init__(timeout=300)
        self.proof_id = proof_id
    @discord.ui.button(label="SIM, pagar", style=discord.ButtonStyle.success, emoji="✅")
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_decide_farm(interaction):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você não possui o cargo autorizado para decidir cobranças de farm.")); return
        with db() as con:
            row = con.execute("SELECT * FROM farm_proofs WHERE id=? AND status='PENDING'", (self.proof_id,)).fetchone()
        if not row:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Essa cobrança já foi finalizada.")); return
        with db() as con:
            con.execute("INSERT INTO farm_receipt_pending(reviewer_id,proof_id,channel_id,created_at) VALUES(?,?,?,?) ON CONFLICT(reviewer_id) DO UPDATE SET proof_id=excluded.proof_id,channel_id=excluded.channel_id,created_at=excluded.created_at", (str(interaction.user.id), self.proof_id, str(interaction.channel_id), now_iso()))
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Envie agora, neste canal privado, a **foto do comprovante de pagamento** da cobrança #{self.proof_id}. A imagem será salva no banco e enviada ao membro por DM. Prazo: 10 minutos."))
    @discord.ui.button(label="NÃO, não pagar", style=discord.ButtonStyle.danger, emoji="✖️")
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_decide_farm(interaction):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você não possui o cargo autorizado para decidir cobranças de farm.")); return
        await interaction.response.send_modal(FarmInvoiceRejectModal(self.proof_id))


class FarmInvoiceView(ComponentsV2View):
    def __init__(self):
        super().__init__(timeout=None)
    def proof_id_from(self, interaction: discord.Interaction) -> Optional[int]:
        return message_marker_id(interaction.message, "farm_proof_id=")
    @discord.ui.button(label="Registrar pagamento", style=discord.ButtonStyle.success, emoji="💸", custom_id="fac:farm:invoice-pay")
    async def pay(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_decide_farm(interaction):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você não possui o cargo autorizado para decidir cobranças de farm.")); return
        proof_id = self.proof_id_from(interaction)
        if not proof_id:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Não consegui identificar essa cobrança.")); return
        await interaction.response.send_message(None, view=v2_view(None, FarmInvoiceConfirmView(proof_id), f"Confirma o pagamento da cobrança **#{proof_id}**?"), ephemeral=True)
    @discord.ui.button(label="Recusar cobrança", style=discord.ButtonStyle.danger, emoji="✖️", custom_id="fac:farm:invoice-reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_decide_farm(interaction):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você não possui o cargo autorizado para decidir cobranças de farm.")); return
        proof_id = self.proof_id_from(interaction)
        if not proof_id:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Não consegui identificar essa cobrança.")); return
        await interaction.response.send_modal(FarmInvoiceRejectModal(proof_id))


async def finalize_farm_invoice(row, status: str, reviewer: discord.abc.User, *, reason: str = "", receipt: bytes = b"", receipt_filename: str = "", receipt_mime: str = "") -> bool:
    proof_id = int(row["id"])
    with db() as con:
        expected_status = "PENDING" if status == "APPROVED" else "REJECTED"
        cur = con.execute("UPDATE farm_proofs SET status=?,reviewed_by=?,reviewed_at=?,review_reason=?,paid_by=?,paid_at=?,receipt_data=?,receipt_filename=?,receipt_mime=? WHERE id=? AND status=?", (status, str(reviewer.id), now_iso(), reason, str(reviewer.id) if status == "APPROVED" else "", now_iso() if status == "APPROVED" else "", sqlite3.Binary(receipt) if receipt else None, receipt_filename, receipt_mime, proof_id, expected_status))
        if not cur.rowcount:
            return False
        if status == "APPROVED":
            con.execute("INSERT INTO farm_entries(discord_id,item_name,quantity,points,created_at) VALUES(?,?,?,?,?)", (str(row["discord_id"]), row["item_name"], int(row["quantity"] or 0), int(row["points"] or 0), now_iso()))
    channel_id = int(row["review_channel_id"] or 0); message_id = int(row["review_message_id"] or 0)
    channel = bot.get_channel(channel_id) if channel_id else None
    if not isinstance(channel, discord.TextChannel) and channel_id:
        try: channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException): channel = None
    if isinstance(channel, discord.TextChannel) and message_id:
        try:
            msg = await channel.fetch_message(message_id)
            final_embed = PanelMessage(title=f"{'✅ PAGAMENTO CONFIRMADO' if status == 'APPROVED' else '❌ COBRANÇA RECUSADA'} · FARM #{proof_id}", color=discord.Color.green() if status == 'APPROVED' else discord.Color.red(), timestamp=datetime.now(timezone.utc))
            final_embed.description = (
                f"**Membro:** <@{row['discord_id']}> · {row['character_name']}\n"
                f"**ID:** {row['game_id']}\n**Item:** {row['item_name']} × {row['quantity']}\n"
                f"**Total:** R$ {int(row['total_cents'] or 0)/100:.2f}\n**Decidido por:** {reviewer.mention}"
                + (f"\n**Motivo:** {reason}" if reason else "")
            )
            if msg.attachments:
                final_embed.set_image(url=f"attachment://{msg.attachments[0].filename}")
            await msg.edit(
                view=v2_view(final_embed, None, None),
                content=None,
                embeds=[],
                attachments=msg.attachments,
            )
            history_channel_id = int(setting("farm_log_channel_id", str(FARM_LOG_CHANNEL_ID)) or 0)
            history_channel = bot.get_channel(history_channel_id) if history_channel_id else None
            if isinstance(history_channel, discord.TextChannel) and history_channel.id != channel.id:
                import io
                history_embed = final_embed.copy()
                safe_filename = os.path.basename(row["image_filename"] or f"farm-{proof_id}.png").replace("\n", "_")[:100]
                history_embed.set_image(url=f"attachment://{safe_filename}")
                await history_channel.send(
                    file=discord.File(io.BytesIO(row["image_data"]), filename=safe_filename),
                    view=v2_view(history_embed, None, None),
                )
        except discord.HTTPException: log.exception("Falha ao atualizar cobrança finalizada #%s", proof_id)
    try:
        user = await bot.fetch_user(int(row["discord_id"]))
        if status == "APPROVED" and receipt:
            import io
            await user.send(None, file=discord.File(io.BytesIO(receipt), filename=receipt_filename or f"pagamento-{proof_id}.png"), view=v2_view(None, None, f"✅ Seu farm #{proof_id} foi pago! Valor: **R$ {int(row['total_cents'] or 0)/100:.2f}**. Comprovante anexado."))
        elif status == "REJECTED":
            await user.send(None, view=v2_view(None, None, f"❌ Sua cobrança de farm #{proof_id} foi recusada. Motivo: **{reason}**"))
    except discord.HTTPException:
        log.info("Não foi possível enviar DM para o membro da cobrança #%s", proof_id)
    log_action(reviewer.id, "FARM_PAGAMENTO_CONFIRMADO" if status == "APPROVED" else "FARM_COBRANCA_FINALIZADA_COM_RECUSA", int(row["discord_id"]), reason, f"Cobrança #{proof_id}; total={int(row['total_cents'] or 0)} centavos")
    return True


async def store_farm_payment_receipt(message: discord.Message) -> bool:
    with db() as con:
        pending = con.execute("SELECT * FROM farm_receipt_pending WHERE reviewer_id=?", (str(message.author.id),)).fetchone()
    if not pending: return False
    if str(pending["channel_id"]) != str(message.channel.id): return False
    try:
        if (datetime.now(timezone.utc) - datetime.fromisoformat(pending["created_at"])).total_seconds() > 600:
            with db() as con: con.execute("DELETE FROM farm_receipt_pending WHERE reviewer_id=?", (str(message.author.id),))
            await message.reply(None, view=v2_view(None, None, "O prazo para enviar o comprovante expirou. Inicie a confirmação novamente.")); return True
    except (TypeError, ValueError): pass
    if not isinstance(message.author, discord.Member) or not can_decide_farm_for_member(message.author):
        await message.reply(None, view=v2_view(None, None, "Somente o cargo configurado para decidir Farm pode anexar esse comprovante.")); return True
    attachment = next((a for a in message.attachments if (a.content_type or "").startswith("image/") or a.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))), None)
    if not attachment:
        await message.reply(None, view=v2_view(None, None, "Anexe a imagem do comprovante de pagamento nesta mensagem.")); return True
    if attachment.size > 8 * 1024 * 1024:
        await message.reply(None, view=v2_view(None, None, "A imagem excede 8 MB. Envie uma menor.")); return True
    try: data = await attachment.read(use_cached=False)
    except discord.HTTPException:
        await message.reply(None, view=v2_view(None, None, "Não consegui baixar o comprovante. Envie novamente.")); return True
    if not data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")):
        await message.reply(None, view=v2_view(None, None, "O arquivo não parece ser uma imagem válida.")); return True
    with db() as con:
        row = con.execute("SELECT * FROM farm_proofs WHERE id=? AND status='PENDING'", (int(pending["proof_id"]),)).fetchone()
        con.execute("DELETE FROM farm_receipt_pending WHERE reviewer_id=?", (str(message.author.id),))
    if not row:
        await message.reply(None, view=v2_view(None, None, "Essa cobrança já foi finalizada ou não existe.")); return True
    finalized = await finalize_farm_invoice(
        row, "APPROVED", message.author,
        receipt=data,
        receipt_filename=attachment.filename[:150],
        receipt_mime=attachment.content_type or "application/octet-stream",
    )
    if not finalized:
        await message.reply(None, view=v2_view(None, None, "Essa cobrança foi decidida por outra pessoa antes do registro do pagamento."))
        return True
    try: await message.delete()
    except discord.HTTPException: pass
    try: await message.channel.send(None, delete_after=15, view=v2_view(None, None, f"✅ Pagamento da cobrança **#{row['id']}** registrado e comprovante salvo."))
    except discord.HTTPException: pass
    return True


def can_decide_farm_for_member(member: discord.Member) -> bool:
    if member.guild_permissions.administrator: return True
    try: role_id = int(setting("farm_approver_role_id", str(LEADER_ROLE_ID)) or 0)
    except (TypeError, ValueError): role_id = 0
    return bool(role_id and any(role.id == role_id for role in member.roles))


class FarmProofReviewView(ComponentsV2View):
    def __init__(self):
        super().__init__(timeout=None)

    def proof_id_from(self, interaction: discord.Interaction) -> Optional[int]:
        return message_marker_id(interaction.message, "farm_proof_id=")

    @discord.ui.button(label="Aprovar comprovante", style=discord.ButtonStyle.success, emoji="✅", custom_id="fac:farm:approve-proof")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not can_decide_farm(interaction):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você não possui o cargo configurado para aprovar ou recusar comprovantes de farm.")); return
        proof_id = self.proof_id_from(interaction)
        if not proof_id:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Não consegui identificar esse comprovante.")); return
        expected_channel = int(panel_setting("farm_approval_channel_id", "0") or 0)
        if expected_channel and interaction.channel_id != expected_channel:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "A aprovação precisa ser feita no canal de análise configurado.")); return
        with db() as con:
            row = con.execute("SELECT * FROM farm_proofs WHERE id=?", (proof_id,)).fetchone()
            if not row or row["status"] != "PENDING":
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse comprovante já foi analisado ou não existe.")); return
            cur = con.execute("UPDATE farm_proofs SET status='APPROVED',reviewed_by=?,reviewed_at=?,review_reason='' WHERE id=? AND status='PENDING'", (str(interaction.user.id), now_iso(), proof_id))
            if not cur.rowcount:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse comprovante já foi analisado.")); return
            con.execute("INSERT INTO farm_entries(discord_id,item_name,quantity,points,created_at) VALUES(?,?,?,?,?)",
                (str(row["discord_id"]), row["item_name"], int(row["quantity"] or 0), int(row["points"] or 0), now_iso()))
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        log_action(interaction.user.id, "FARM_COMPROVANTE_APROVADO", int(row["discord_id"]), "", f"Comprovante #{proof_id}; {row['item_name']} x{row['quantity']}")
        await update_farm_review_message(row, status="APPROVED", reviewer=interaction.user.mention)
        guild = interaction.guild
        log_channel_id = int(setting("farm_log_channel_id", str(FARM_LOG_CHANNEL_ID)) or 0)
        log_channel = guild.get_channel(log_channel_id) if guild and log_channel_id else None
        if isinstance(log_channel, discord.TextChannel):
            approved_embed = PanelMessage(title=f"🌾 REGISTRO DE FARM #{proof_id}", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
            approved_embed.add_field(name="Jogador", value=row["character_name"], inline=True)
            approved_embed.add_field(name="ID", value=row["game_id"], inline=True)
            approved_embed.add_field(name="Farm", value=row["item_name"] or "Não informado", inline=True)
            approved_embed.add_field(name="Quantidade", value=str(row["quantity"] or 0), inline=True)
            approved_embed.add_field(name="Pontos", value=str(row["points"] or 0), inline=True)
            approved_embed.add_field(name="Discord", value=f"<@{row['discord_id']}> (`{row['discord_id']}`)", inline=False)
            approved_embed.add_field(name="Aprovado por", value=interaction.user.mention, inline=True)
            import io
            safe_log_filename = os.path.basename(row["image_filename"] or f"farm-{proof_id}.png").replace("\n", "_")[:100]
            approved_embed.set_image(url=f"attachment://{safe_log_filename}")
            await log_channel.send(view=v2_view(approved_embed, None, None), file=discord.File(io.BytesIO(row["image_data"]), filename=safe_log_filename))
        try:
            user = await bot.fetch_user(int(row["discord_id"]))
            await user.send(None, view=v2_view(None, None, f"✅ Seu comprovante de farm **#{proof_id}** foi aprovado pela liderança."))
        except discord.HTTPException:
            pass
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, f"Comprovante #{proof_id} aprovado."))

    @discord.ui.button(label="Recusar comprovante", style=discord.ButtonStyle.danger, emoji="❌", custom_id="fac:farm:reject-proof")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not can_decide_farm(interaction):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você não possui o cargo configurado para aprovar ou recusar comprovantes de farm.")); return
        proof_id = self.proof_id_from(interaction)
        if not proof_id:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Não consegui identificar esse comprovante.")); return
        expected_channel = int(panel_setting("farm_approval_channel_id", "0") or 0)
        if expected_channel and interaction.channel_id != expected_channel:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "A recusa precisa ser feita no canal de análise configurado.")); return
        await interaction.response.send_modal(FarmProofRejectModal(proof_id))


class HierarchyPublishChannelSelect(discord.ui.Button):
    def __init__(self, parent_view: "HierarchyPublishChannelView"):
        super().__init__(
            label="Selecionar canal de publicação",
            style=discord.ButtonStyle.secondary, emoji="📣", row=0,
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction) -> None:
        async def confirm(picker_interaction, channel):
            self.parent_view.channel_id = channel.id
            save_config_value("hierarchy_panel_channel_id", str(channel.id))
            self.parent_view.publish_now.disabled = False
            await picker_interaction.response.edit_message(
                view=v2_view(self.parent_view.build_embed(), self.parent_view, None),
                content=None, embeds=[], attachments=(picker_interaction.message.attachments if picker_interaction.message else []),
            )
        async def back(picker_interaction):
            await picker_interaction.response.edit_message(
                view=v2_view(self.parent_view.build_embed(), self.parent_view, None),
                content=None, embeds=[], attachments=(picker_interaction.message.attachments if picker_interaction.message else []),
            )
        picker = PaginatedGuildObjectPickerView(
            interaction.guild, kind="text_channel", title="Canal onde publicar a hierarquia",
            owner_id=interaction.user.id, on_confirm=confirm, on_back=back,
            selected_id=self.parent_view.channel_id or 0,
        )
        await interaction.response.edit_message(view=picker, content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))


class HierarchyPublishChannelView(ComponentsV2View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=600)
        self.guild = guild
        self.channel_id: Optional[int] = None
        self.add_item(HierarchyPublishChannelSelect(self))
        self.add_item(ConfigManualIdButton(preset_key="hierarchy_panel_channel_id", row=2))
        self.publish_now.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not has_leadership(interaction):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Somente liderança ou administradores podem publicar a hierarquia."))
            return False
        return True

    def build_embed(self) -> PanelMessage:
        title = panel_setting("hierarchy_panel_title", f"🏛️ HIERARQUIA — {FACTION_NAME}")
        description = panel_setting("hierarchy_panel_description", "Cargos e membros da organização, do mais alto ao mais baixo.")
        channel = self.guild.get_channel(self.channel_id) if self.channel_id else None
        embed = PanelMessage(
            title="📣 Publicar hierarquia",
            description=f"**{title[:180]}**\n{description[:700]}\n\nSelecione o canal de destino. A hierarquia será publicada em seções por cargo, do mais alto ao mais baixo.",
            color=discord.Color.from_rgb(76, 86, 180),
        )
        embed.add_field(name="Canal escolhido", value=channel.mention if channel else "`Selecione um canal abaixo`", inline=False)
        embed.set_footer(text="Cada membro aparece apenas na seção do maior cargo configurado que possui")
        return embed

    @discord.ui.button(label="Publicar neste canal", style=discord.ButtonStyle.success, emoji="🚀", row=1)
    async def publish_now(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        issue = hierarchy_configuration_issue(interaction.guild) if interaction.guild else "Use este recurso dentro do servidor."
        if issue:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, issue))
            return
        if not self.channel_id:
            raw = setting("hierarchy_panel_channel_id", "0")
            self.channel_id = int(raw) if raw.isdigit() and int(raw) else None
        if not self.channel_id:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Selecione o canal ou informe o ID manualmente."))
            return
        save_config_value("hierarchy_panel_channel_id", str(self.channel_id))
        await interaction.response.defer(ephemeral=True, thinking=True)
        await publish_configured_panel(interaction, "hierarchy", channel_override=self.channel_id)




# ---------------- HIERARQUIA CONFIGURÁVEL (FLUXO GUIADO) ----------------
HIERARCHY_MAX_ROLES = 100
HIERARCHY_FIELDS_PER_MODAL = 5


def hierarchy_rows():
    """Retorna a hierarquia na ordem configurada: topo primeiro."""
    with db() as con:
        rows = con.execute("SELECT key,value FROM settings WHERE key LIKE 'rank_role_%'").fetchall()
    parsed = []
    for row in rows:
        try:
            rid = int(row["key"].replace("rank_role_", "", 1))
            parts = row["value"].split("|")
            abbr = parts[0].strip()
            priority = int(parts[1]) if len(parts) > 1 else 0
            parsed.append((priority, rid, abbr))
        except (ValueError, TypeError, IndexError):
            continue
    return sorted(parsed, reverse=True)


def clear_hierarchy_settings():
    with db() as con:
        con.execute("DELETE FROM settings WHERE key LIKE 'rank_role_%'")
        con.execute("DELETE FROM settings WHERE key LIKE 'hierarchy_draft_tag_%'")
        for key in ("hierarchy_panel_channel_id", "hierarchy_role_count", "hierarchy_setup_in_progress"):
            con.execute("DELETE FROM settings WHERE key=?", (key,))


class HierarchyGuideView(ComponentsV2View):
    """Compatibilidade com a entrada antiga: mantém as mesmas ações do guia V2 atual."""
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=900)
        self.guild = guild

    @discord.ui.button(label="Editar hierarquia", style=discord.ButtonStyle.primary, emoji="✏️")
    async def configure(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = HierarchyEditMenuView(self.guild, interaction.user.id)
        await interaction.response.edit_message(view=v2_view(view.embed(), view, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))

    @discord.ui.button(label="Começar do zero", style=discord.ButtonStyle.success, emoji="🔄")
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            None,
            view=v2_view(None, HierarchyResetConfirmView(self.guild), "⚠️ **Começar do zero?** Isso apagará os cargos, tags, canal salvo e as mensagens da hierarquia publicadas pelo bot."), ephemeral=True
        )


class HierarchyResetConfirmView(ComponentsV2View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=120)
        self.guild = guild

    @discord.ui.button(label="Sim, começar do zero", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        raw = setting("hierarchy_panel_channel_id", "0")
        channel = self.guild.get_channel(int(raw)) if raw.isdigit() and int(raw) else None
        deleted = 0
        if isinstance(channel, discord.TextChannel):
            try:
                async for message in channel.history(limit=None):
                    is_hierarchy_post = (
                        bot.user is not None
                        and message.author.id == bot.user.id
                        and any(
                            line.lstrip("# ").startswith("📋 | ")
                            for line in message_component_text(message).splitlines()
                        )
                    )
                    if not is_hierarchy_post:
                        continue
                    try:
                        await message.delete()
                        deleted += 1
                    except discord.Forbidden:
                        raise
                    except discord.HTTPException:
                        continue
            except discord.Forbidden:
                await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, "Não tenho permissão para limpar o canal anterior. Conceda Gerenciar mensagens e tente novamente."))
                return
        clear_hierarchy_settings()
        await interaction.edit_original_response(
            content=None,
            view=v2_view(
                PanelMessage(
                    title="✅ Configuração anterior removida",
                    description=f"{deleted} mensagem(ns) da hierarquia foram removidas. Agora defina a nova quantidade de cargos para começar a configuração do zero."
                ),
                HierarchyStartSetupView(self.guild),
                None,
            ),
            embeds=[],
            attachments=(interaction.message.attachments if interaction.message else []),
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=None, view=v2_view(None, None, "Operação cancelada. Nenhuma configuração foi alterada."), embeds=[], attachments=(interaction.message.attachments if interaction.message else []))


class HierarchyStartSetupView(ComponentsV2View):
    def __init__(self, guild):
        super().__init__(timeout=300)
        self.guild = guild

    @discord.ui.button(label="Definir quantidade de cargos", style=discord.ButtonStyle.success, emoji="➡️", row=0)
    async def continue_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HierarchyCountModal(self.guild, interaction.message))


class HierarchyCountModal(ComponentsV2Modal, title="Etapa 1 — Quantidade de cargos"):
    count = TextInput(label="Quantos cargos terá a hierarquia? (1 a 100)", placeholder="Ex.: 10", max_length=3)

    def __init__(self, guild, source_message=None):
        super().__init__(timeout=300)
        self.guild = guild
        self.source_message = source_message

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.count.value.strip()
        if not raw.isdigit() or not 1 <= int(raw) <= HIERARCHY_MAX_ROLES:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Informe uma quantidade entre 1 e 100."))
            return
        count = int(raw)
        available_role_count = len(selectable_hierarchy_roles(self.guild))
        if count > available_role_count:
            await interaction.response.send_message(
                None, ephemeral=True,
                view=v2_view(
                    None, None,
                    f"Você pediu {count} cargos, mas este servidor tem apenas {available_role_count} cargos selecionáveis. Ajuste a quantidade.",
                ),
            )
            return
        save_config_value("hierarchy_role_count", str(count))
        save_config_value("hierarchy_setup_in_progress", "1")
        # Reaproveita a ordem configurada (prioridade salva), não a posição
        # nativa do Discord. Assim, a ordem da hierarquia pode ser personalizada.
        selectable_ids = {role.id for role in selectable_hierarchy_roles(self.guild)}
        existing = []
        for _priority, role_id, _abbr in hierarchy_rows():
            role = self.guild.get_role(role_id)
            if role and role.id in selectable_ids:
                existing.append(role)
        view = HierarchyRoleConfigView(self.guild, count)
        view.owner_id = interaction.user.id
        view.role_ids = [role.id for role in existing[:count]] + [None] * max(0, count - len(existing))
        if all(role_id is not None for role_id in view.role_ids):
            tags_view = HierarchyTagConfigView(self.guild, view.role_ids, view.owner_id)
            if all(tags_view.tags):
                tags_view.persist_tags()
                next_view = HierarchyFinalChannelView(self.guild)
            else:
                next_view = tags_view
        else:
            view._refresh_role_picker()
            next_view = view
        await update_hierarchy_modal_message(interaction, self.source_message, next_view)


class HierarchyRoleIdsModal(ComponentsV2Modal):
    def __init__(self, parent, start: int, end: int):
        super().__init__(title=f"Etapa 2 — IDs dos cargos {start + 1} a {end}", timeout=300)
        self.parent_view = parent
        self.start = start
        self.end = end
        self.inputs = []
        for index in range(start, end):
            field = TextInput(
                label=f"Cargo {index + 1} — {'MAIS ALTO' if index == 0 else ('MAIS BAIXO' if index == len(parent.role_ids) - 1 else 'ID do cargo')}",
                placeholder="Cole o ID do cargo do Discord",
                required=True, max_length=25
            )
            current = parent.role_ids[index]
            if current:
                field.default = str(current)
            self.inputs.append(field)
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        resolved = []
        for offset, field in enumerate(self.inputs):
            raw = field.value.strip()
            role = self.parent_view.guild.get_role(int(raw)) if raw.isdigit() else None
            if not role or role.id not in {item.id for item in selectable_hierarchy_roles(self.parent_view.guild)}:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"ID inválido no campo Cargo {self.start + offset + 1}. Use um cargo não gerenciado e abaixo do cargo do bot."))
                return
            if role.id in resolved or role.id in [x for i, x in enumerate(self.parent_view.role_ids) if x and not self.start <= i < self.end]:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você informou o mesmo cargo mais de uma vez. Cada posição precisa ter um cargo diferente."))
                return
            resolved.append(role.id)
        for offset, rid in enumerate(resolved):
            self.parent_view.role_ids[self.start + offset] = rid
        await interaction.response.send_message(view=v2_view(self.parent_view.embed(), self.parent_view, None), ephemeral=True)



class HierarchyRolePicker(discord.ui.Select):
    """Menu próprio com 25 cargos por página; as setas percorrem todos os cargos do servidor."""
    def __init__(self, parent):
        self.parent_view = parent
        start = parent.role_page * HIERARCHY_ROLES_PER_PAGE
        self.page_roles = parent.available_roles[start:start + HIERARCHY_ROLES_PER_PAGE]
        options = [
            discord.SelectOption(
                label=role.name[:100],
                value=str(role.id),
                description=f"Posição: {role.position}"[:100],
            )
            for role in self.page_roles
        ]
        if not options:
            options = [discord.SelectOption(label="Nenhum cargo disponível", value="none")]
        super().__init__(
            placeholder=f"Selecione um cargo • página {parent.role_page + 1}/{parent.role_pages}",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.owner_id:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Somente quem iniciou a configuração pode selecionar os cargos."))
            return
        raw = self.values[0]
        role = self.parent_view.guild.get_role(int(raw)) if raw.isdigit() else None
        if role is None or role.id not in {item.id for item in selectable_hierarchy_roles(self.parent_view.guild)}:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Cargo inválido ou fora da hierarquia administrável pelo bot."))
            return
        if role.id in [rid for rid in self.parent_view.role_ids if rid]:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse cargo já foi selecionado. Escolha outro."))
            return
        try:
            index = self.parent_view.role_ids.index(None)
        except ValueError:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Todos os cargos já foram preenchidos."))
            return
        self.parent_view.role_ids[index] = role.id
        await self.parent_view.refresh_after_role_change(interaction)


class HierarchyRoleIdModal(ComponentsV2Modal, title="Adicionar cargo por ID"):
    role_id_input = TextInput(label="ID do cargo", max_length=25, required=True, placeholder="Cole o ID do cargo do servidor")

    def __init__(self, parent_view):
        super().__init__(timeout=300)
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.owner_id:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Somente quem iniciou a configuração pode informar os IDs."))
            return
        raw = self.role_id_input.value.strip()
        role = self.parent_view.guild.get_role(int(raw)) if raw.isdigit() else None
        if role is None or role.id not in {item.id for item in selectable_hierarchy_roles(self.parent_view.guild)}:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "ID inválido: escolha um cargo não gerenciado e abaixo do cargo do bot."))
            return
        if role.id in [rid for rid in self.parent_view.role_ids if rid]:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse cargo já foi selecionado. Informe outro ID."))
            return
        try:
            index = self.parent_view.role_ids.index(None)
        except ValueError:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Todos os cargos já foram preenchidos."))
            return
        self.parent_view.role_ids[index] = role.id
        if all(rid is not None for rid in self.parent_view.role_ids):
            tags_view = HierarchyTagConfigView(
                self.parent_view.guild, self.parent_view.role_ids,
                self.parent_view.owner_id, return_to_edit=self.parent_view.edit_mode,
            )
            if all(tags_view.tags):
                tags_view.persist_tags()
                next_view = HierarchyEditMenuView(self.parent_view.guild, self.parent_view.owner_id) if self.parent_view.edit_mode else HierarchyFinalChannelView(self.parent_view.guild)
            else:
                next_view = tags_view
        else:
            self.parent_view._refresh_role_picker()
            next_view = self.parent_view
        await update_hierarchy_modal_message(interaction, self.parent_view.message, next_view)


def selectable_hierarchy_roles(guild: discord.Guild) -> list[discord.Role]:
    """Cargos válidos para a hierarquia e administráveis pelo bot."""
    bot_member = guild.me
    roles = [
        role for role in guild.roles
        if role != guild.default_role
        and not role.managed
        and (bot_member is None or role < bot_member.top_role)
    ]
    return sorted(
        roles,
        key=lambda role: (role.position, role.name.casefold()),
        reverse=True,
    )


def hierarchy_configuration_issue(guild: discord.Guild) -> Optional[str]:
    """Retorna o motivo pelo qual a hierarquia ainda não pode ser publicada."""
    if setting("hierarchy_setup_in_progress", "0") == "1":
        return "A configuração da hierarquia ainda está em andamento. Termine de selecionar todos os cargos e definir todas as tags antes de publicar."

    rows = hierarchy_rows()
    if not rows:
        return "Nenhum cargo está configurado para exibição. Abra `/config` → Hierarquia e selecione os cargos."

    try:
        expected_count = int(setting("hierarchy_role_count", "0") or 0)
    except (TypeError, ValueError):
        expected_count = 0
    if expected_count and len(rows) != expected_count:
        return (
            f"A hierarquia ainda não está completa: existem **{len(rows)} de {expected_count} cargos** salvos. "
            "Conclua a seleção dos cargos e das tags em `/config` → Hierarquia antes de publicar."
        )

    missing_tags = [str(role_id) for _priority, role_id, abbreviation in rows if not (abbreviation or "").strip()]
    if missing_tags:
        return (
            "Ainda faltam tags para os cargos de ID " + ", ".join(f"`{role_id}`" for role_id in missing_tags[:10]) +
            ". Conclua a etapa de tags em `/config` → Hierarquia antes de publicar."
        )

    selectable_ids = {role.id for role in selectable_hierarchy_roles(guild)}
    invalid_roles = []
    for _priority, role_id, _abbreviation in rows:
        role = guild.get_role(role_id)
        if role is None or role_id not in selectable_ids:
            invalid_roles.append(role.name if role else str(role_id))
    if invalid_roles:
        return (
            "O cargo do bot precisa ficar acima destes cargos, e eles não podem ser gerenciados por integrações: " +
            ", ".join(f"**{name}**" for name in invalid_roles[:10]) + "."
        )
    return None


class HierarchyRoleConfigView(ComponentsV2View):
    """Seleciona e reorganiza cargos em ordem manual: topo primeiro, base por último."""
    def __init__(self, guild, count, *, edit_mode=False):
        super().__init__(timeout=900)
        self.guild, self.count = guild, count
        self.role_ids = [None] * count
        self.owner_id = 0
        self.edit_mode = edit_mode
        self.message = None
        self.available_roles = selectable_hierarchy_roles(guild)
        self.role_page = 0
        self.role_picker = HierarchyRolePicker(self)
        self.add_item(self.role_picker)
        self._update_role_page_buttons()

    @property
    def role_pages(self):
        return max(1, math.ceil(len(self.available_roles) / HIERARCHY_ROLES_PER_PAGE))

    def _update_role_page_buttons(self):
        self.previous.disabled = self.role_page <= 0
        self.next.disabled = self.role_page >= self.role_pages - 1

    def _refresh_role_picker(self):
        self.remove_item(self.role_picker)
        self.role_picker = HierarchyRolePicker(self)
        self.add_item(self.role_picker)
        self._update_role_page_buttons()

    async def _show_next_stage(self, interaction):
        # A ordem digitada/selecionada é preservada. Não ordenar por role.position.
        tags_view = HierarchyTagConfigView(
            self.guild, self.role_ids, self.owner_id, return_to_edit=self.edit_mode
        )
        if all(tags_view.tags):
            tags_view.persist_tags()
            next_view = HierarchyEditMenuView(self.guild, self.owner_id) if self.edit_mode else HierarchyFinalChannelView(self.guild)
        else:
            next_view = tags_view
        await interaction.response.edit_message(
            view=v2_view(next_view.embed(), next_view, None),
            content=None, embeds=[],
            attachments=(interaction.message.attachments if interaction.message else []),
        )

    async def refresh_after_role_change(self, interaction):
        if all(rid is not None for rid in self.role_ids):
            await self._show_next_stage(interaction)
            return
        self._refresh_role_picker()
        await interaction.response.edit_message(
            view=v2_view(self.embed(), self, None), content=None, embeds=[],
            attachments=(interaction.message.attachments if interaction.message else []),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id and interaction.user.id != self.owner_id:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Somente quem iniciou a configuração pode alterar os cargos."))
            return False
        return True

    def embed(self):
        lines = []
        for idx, rid in enumerate(self.role_ids):
            role = self.guild.get_role(rid) if rid else None
            position = "MAIS ALTO" if idx == 0 else ("MAIS BAIXO" if idx == len(self.role_ids) - 1 else f"POSIÇÃO {idx + 1}")
            lines.append(f"**{idx + 1}. {position}:** {role.mention if role else '— vaga livre —'}")
        e = PanelMessage(
            title="Etapa 2 — Cargos e posições",
            description=(
                "Defina a ordem manualmente: **Cargo 1 é o topo** e o último cargo é a base. "
                "O menu adiciona o cargo na primeira vaga livre. Use **Apagar 1** para corrigir uma escolha "
                "ou **Mudar posição** para reorganizar cargos já selecionados.\n\n"
                + "\n".join(lines)
            ),
            color=discord.Color.blurple(),
        )
        e.set_footer(text=f"Página de cargos {self.role_page + 1}/{self.role_pages} • {sum(rid is not None for rid in self.role_ids)}/{self.count} cargos definidos")
        return e

    @discord.ui.button(label="◀ Anterior", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.role_page = max(0, self.role_page - 1)
        self._refresh_role_picker()
        await interaction.response.edit_message(view=v2_view(self.embed(), self, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))

    @discord.ui.button(label="Próxima página ▶", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.role_page = min(self.role_pages - 1, self.role_page + 1)
        self._refresh_role_picker()
        await interaction.response.edit_message(view=v2_view(self.embed(), self, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))

    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.edit_mode:
            next_view = HierarchyEditMenuView(self.guild, self.owner_id or interaction.user.id)
            await interaction.response.edit_message(view=v2_view(next_view.embed(), next_view, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))
        else:
            self.message = interaction.message
            await interaction.response.send_modal(HierarchyCountModal(self.guild, interaction.message))

    @discord.ui.button(label="Por ID", style=discord.ButtonStyle.primary, emoji="🔢", row=2)
    async def by_id(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.message = interaction.message
        await interaction.response.send_modal(HierarchyRoleIdModal(self))

    @discord.ui.button(label="Apagar 1", style=discord.ButtonStyle.danger, emoji="🗑️", row=2)
    async def remove_one(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not any(self.role_ids):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Ainda não há cargos selecionados para apagar."))
            return
        self.message = interaction.message
        await interaction.response.send_modal(HierarchyRemoveRoleModal(self))

    @discord.ui.button(label="Mudar posição", style=discord.ButtonStyle.primary, emoji="↕️", row=3)
    async def move_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        if any(rid is None for rid in self.role_ids):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Preencha todas as posições antes de reorganizar a hierarquia."))
            return
        self.message = interaction.message
        await interaction.response.send_modal(HierarchyMoveRoleModal(self))


async def update_hierarchy_modal_message(interaction, source_message, next_view):
    """Atualiza a mensagem efêmera que abriu o modal, sem criar uma segunda tela."""
    await interaction.response.defer(ephemeral=True)
    rendered_view = v2_view(next_view.embed(), next_view, None)
    if source_message is not None:
        await source_message.edit(
            content=None, embeds=[], view=rendered_view,
            attachments=(source_message.attachments if getattr(source_message, "attachments", None) else []),
        )
    else:
        await interaction.followup.send(view=rendered_view, ephemeral=True)


class HierarchyRemoveRoleModal(ComponentsV2Modal, title="Apagar um cargo"):
    position = TextInput(label="Número da posição que deseja apagar", placeholder="Ex.: 3", max_length=3, required=True)

    def __init__(self, parent):
        super().__init__(timeout=300)
        self.parent_view = parent

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.position.value.strip()
        if not raw.isdigit() or not 1 <= int(raw) <= len(self.parent_view.role_ids):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Informe uma posição entre 1 e {len(self.parent_view.role_ids)}."))
            return
        index = int(raw) - 1
        role_id = self.parent_view.role_ids[index]
        if role_id is None:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Essa posição já está vazia. Escolha uma posição que tenha um cargo."))
            return
        self.parent_view.role_ids[index] = None
        with db() as con:
            con.execute("DELETE FROM settings WHERE key=?", (f"rank_role_{role_id}",))
            con.execute("DELETE FROM settings WHERE key=?", (f"hierarchy_draft_tag_{role_id}",))
            con.execute("INSERT INTO settings(key,value) VALUES('hierarchy_setup_in_progress','1') ON CONFLICT(key) DO UPDATE SET value='1'")
        self.parent_view._refresh_role_picker()
        await update_hierarchy_modal_message(interaction, self.parent_view.message, self.parent_view)


class HierarchyMoveRoleModal(ComponentsV2Modal, title="Mudar posição do cargo"):
    current_position = TextInput(label="Posição atual do cargo", placeholder="Ex.: 4", max_length=3, required=True)
    new_position = TextInput(label="Nova posição na hierarquia", placeholder="Ex.: 2", max_length=3, required=True)

    def __init__(self, parent):
        super().__init__(timeout=300)
        self.parent_view = parent

    async def on_submit(self, interaction: discord.Interaction):
        total = len(self.parent_view.role_ids)
        raw_current = self.current_position.value.strip()
        raw_new = self.new_position.value.strip()
        if not raw_current.isdigit() or not raw_new.isdigit():
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Informe números válidos para a posição atual e a nova posição."))
            return
        current, new = int(raw_current), int(raw_new)
        if not (1 <= current <= total and 1 <= new <= total):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"As posições precisam estar entre 1 e {total}."))
            return
        moved = self.parent_view.role_ids.pop(current - 1)
        self.parent_view.role_ids.insert(new - 1, moved)
        next_view = HierarchyTagConfigView(
            self.parent_view.guild, self.parent_view.role_ids,
            self.parent_view.owner_id, return_to_edit=self.parent_view.edit_mode,
        )
        if all(next_view.tags):
            next_view.persist_tags()
            next_view = HierarchyEditMenuView(self.parent_view.guild, self.parent_view.owner_id) if self.parent_view.edit_mode else HierarchyFinalChannelView(self.parent_view.guild)
        await update_hierarchy_modal_message(interaction, self.parent_view.message, next_view)


class HierarchyTagsModal(ComponentsV2Modal):
    """Preenche somente o bloco atual e substitui a mesma mensagem pelo próximo bloco."""
    def __init__(self, parent, indices: list[int]):
        start = min(indices) + 1 if indices else 1
        end = max(indices) + 1 if indices else start
        super().__init__(title=f"Etapa 3 — Tags dos cargos {start} a {end}", timeout=300)
        self.parent_view = parent
        self.indices = list(indices)
        self.inputs = []
        for idx in self.indices:
            role = self.parent_view.guild.get_role(self.parent_view.role_ids[idx])
            field = TextInput(
                label=f"Tag · {role.name[:35] if role else f'Cargo {idx + 1}'}",
                placeholder="Ex.: M, REC, LT",
                required=True,
                max_length=12,
            )
            self.inputs.append(field)
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        if self.parent_view.owner_id and interaction.user.id != self.parent_view.owner_id:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Somente quem iniciou a configuração pode definir as tags."))
            return

        new_tags = []
        for field in self.inputs:
            tag = field.value.strip().replace("[", "").replace("]", "")
            if not tag or any(ch in tag for ch in "\n\r"):
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Cada cargo precisa ter uma tag válida (até 12 caracteres)."))
                return
            new_tags.append(tag)

        # Valida o bloco inteiro antes de gravar qualquer alteração.
        for idx, tag in zip(self.indices, new_tags):
            self.parent_view.tags[idx] = tag
            rid = self.parent_view.role_ids[idx]
            save_config_value(f"hierarchy_draft_tag_{rid}", tag)

        next_page = self.parent_view.find_next_incomplete_page(0)
        if next_page is None:
            self.parent_view.persist_tags()
            next_view = HierarchyEditMenuView(self.parent_view.guild, self.parent_view.owner_id) if self.parent_view.return_to_edit else HierarchyFinalChannelView(self.parent_view.guild)
        else:
            self.parent_view.page = next_page
            self.parent_view._sync_buttons()
            next_view = self.parent_view
        # A tela antiga é editada; não é enviada uma nova mensagem efêmera abaixo dela.
        await update_hierarchy_modal_message(interaction, self.parent_view.message, next_view)


class HierarchyTagConfigView(ComponentsV2View):
    """Tags em blocos de até cinco cargos; cada bloco substitui o anterior ao ser salvo."""
    def __init__(self, guild, role_ids, owner_id=0, *, return_to_edit=False):
        super().__init__(timeout=900)
        self.guild, self.role_ids = guild, list(role_ids)
        self.owner_id = owner_id
        self.return_to_edit = return_to_edit
        self.message = None
        self.tags = []
        with db() as con:
            for rid in self.role_ids:
                draft = con.execute("SELECT value FROM settings WHERE key=?", (f"hierarchy_draft_tag_{rid}",)).fetchone()
                saved = con.execute("SELECT value FROM settings WHERE key=?", (f"rank_role_{rid}",)).fetchone()
                tag = str(draft["value"] if draft else "").strip()
                if not tag and saved:
                    tag = str(saved["value"]).split("|", 1)[0].strip()
                self.tags.append(tag)
        self.page = self.find_next_incomplete_page(0)
        if self.page is None:
            self.page = 0
        self._sync_buttons()

    @property
    def pages(self):
        return max(1, (len(self.role_ids) + 4) // 5)

    def find_next_incomplete_page(self, start_page: int = 0):
        for page in range(max(0, start_page), self.pages):
            start = page * 5
            end = min(start + 5, len(self.tags))
            if any(not tag for tag in self.tags[start:end]):
                return page
        return None

    def persist_tags(self):
        total = len(self.role_ids)
        with db() as con:
            con.execute("DELETE FROM settings WHERE key LIKE 'rank_role_%'")
            con.execute("DELETE FROM settings WHERE key LIKE 'hierarchy_draft_tag_%'")
            for idx, (rid, tag) in enumerate(zip(self.role_ids, self.tags)):
                if not tag:
                    continue
                priority = total - idx
                con.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (f"rank_role_{rid}", f"{tag}|{priority}"),
                )
            con.execute("INSERT INTO settings(key,value) VALUES('hierarchy_setup_in_progress','0') ON CONFLICT(key) DO UPDATE SET value='0'")
            con.execute("INSERT INTO settings(key,value) VALUES('hierarchy_role_count',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(total),))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id and interaction.user.id != self.owner_id:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Somente quem iniciou a configuração pode alterar as tags."))
            return False
        return True

    def embed(self):
        start, end = self.page * 5, min(self.page * 5 + 5, len(self.role_ids))
        lines = []
        for idx in range(start, end):
            role = self.guild.get_role(self.role_ids[idx])
            tag = self.tags[idx] or "— pendente —"
            state = "✅" if self.tags[idx] else "⏳"
            lines.append(f"{state} **Cargo {idx + 1}** · {role.mention if role else 'Cargo removido'} → **[{tag}]**")
        done = sum(bool(tag) for tag in self.tags)
        e = PanelMessage(
            title="Etapa 3 — Tags / abreviações",
            description=(
                "Defina as tags dos cargos deste bloco. Depois de salvar, **este bloco será substituído aqui mesmo pelo próximo bloco pendente**.\n\n"
                + "\n".join(lines)
            ),
            color=discord.Color.blurple(),
        )
        e.set_footer(text=f"Progresso: {done}/{len(self.tags)} tags • Bloco {self.page + 1}/{self.pages}")
        return e

    def _sync_buttons(self):
        start, end = self.page * 5, min(self.page * 5 + 5, len(self.tags))
        self.define_tag.disabled = all(self.tags[start:end])
        self.restart.disabled = not any(self.tags[start:end])

    @discord.ui.button(label="Definir tag dos cargos", style=discord.ButtonStyle.primary, emoji="✏️", row=0)
    async def define_tag(self, interaction: discord.Interaction, button: discord.ui.Button):
        start = self.page * 5
        end = min(start + 5, len(self.tags))
        indices = [idx for idx in range(start, end) if not self.tags[idx]]
        if not indices:
            next_page = self.find_next_incomplete_page(0)
            if next_page is not None:
                self.page = next_page
                self._sync_buttons()
                await interaction.response.edit_message(view=v2_view(self.embed(), self, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))
                return
            next_view = HierarchyEditMenuView(self.guild, self.owner_id) if self.return_to_edit else HierarchyFinalChannelView(self.guild)
            self.persist_tags()
            await interaction.response.edit_message(view=v2_view(next_view.embed(), next_view, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))
            return
        self.message = interaction.message
        await interaction.response.send_modal(HierarchyTagsModal(self, indices))

    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.secondary, emoji="⬅️", row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = HierarchyRoleConfigView(self.guild, len(self.role_ids), edit_mode=self.return_to_edit)
        view.role_ids = list(self.role_ids)
        view.owner_id = self.owner_id or interaction.user.id
        view._refresh_role_picker()
        await interaction.response.edit_message(view=v2_view(view.embed(), view, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))

    @discord.ui.button(label="Recomeçar tags", style=discord.ButtonStyle.danger, emoji="🔄", row=0)
    async def restart(self, interaction: discord.Interaction, button: discord.ui.Button):
        start, end = self.page * 5, min(self.page * 5 + 5, len(self.tags))
        with db() as con:
            for idx in range(start, end):
                rid = self.role_ids[idx]
                self.tags[idx] = ""
                con.execute("DELETE FROM settings WHERE key=?", (f"hierarchy_draft_tag_{rid}",))
                con.execute("DELETE FROM settings WHERE key=?", (f"rank_role_{rid}",))
            con.execute("INSERT INTO settings(key,value) VALUES('hierarchy_setup_in_progress','1') ON CONFLICT(key) DO UPDATE SET value='1'")
        self._sync_buttons()
        await interaction.response.edit_message(view=v2_view(self.embed(), self, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))


class HierarchyManualChannelModal(ComponentsV2Modal, title="Canal da hierarquia"):
    channel_id = TextInput(label="ID do canal de texto", placeholder="Cole o ID do canal", max_length=25)

    def __init__(self, parent):
        super().__init__(timeout=300)
        self.parent_view = parent

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.channel_id.value.strip()
        channel = self.parent_view.guild.get_channel(int(raw)) if raw.isdigit() else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Não encontrei um canal de texto com esse ID neste servidor."))
            return
        self.parent_view.channel_id = channel.id
        save_config_value("hierarchy_panel_channel_id", str(channel.id))
        await interaction.response.send_message(view=v2_view(self.parent_view.embed(), self.parent_view, None), ephemeral=True)


class HierarchyFinalChannelSelect(discord.ui.Select):
    def __init__(self, parent):
        self.parent_view = parent
        start = parent.channel_page * 25
        self.page_channels = parent.channels[start:start + 25]
        options = [discord.SelectOption(label=f"#{ch.name}"[:100], value=str(ch.id), description=f"Canal de texto · posição {ch.position}"[:100]) for ch in self.page_channels]
        if not options:
            options = [discord.SelectOption(label="Nenhum canal de texto disponível", value="none")]
        super().__init__(placeholder=f"Selecione o canal • página {parent.channel_page + 1}/{parent.channel_pages}", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        raw = self.values[0]
        channel = self.parent_view.guild.get_channel(int(raw)) if raw.isdigit() else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Selecione um canal de texto válido."))
            return
        self.parent_view.channel_id = channel.id
        save_config_value("hierarchy_panel_channel_id", str(channel.id))
        self.parent_view.refresh()
        await interaction.response.edit_message(view=v2_view(self.parent_view.embed(), self.parent_view, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))


class HierarchyFinalChannelView(ComponentsV2View):
    def __init__(self, guild, *, return_to_edit=False, owner_id=0):
        super().__init__(timeout=900)
        self.guild = guild
        self.return_to_edit = return_to_edit
        self.owner_id = owner_id
        raw = setting("hierarchy_panel_channel_id", "0")
        self.channel_id = int(raw) if raw.isdigit() and int(raw) else None
        self.channels = sorted((c for c in guild.text_channels), key=lambda c: (getattr(c.category, "position", -1), c.position, c.name.casefold()))
        self.channel_page = 0
        self.channel_selector = HierarchyFinalChannelSelect(self)
        self.add_item(self.channel_selector)
        self._sync_buttons()

    @property
    def channel_pages(self):
        return max(1, math.ceil(len(self.channels) / 25))

    def _sync_buttons(self):
        self.previous_page.disabled = self.channel_page <= 0
        self.next_page.disabled = self.channel_page >= self.channel_pages - 1

    def refresh(self):
        self.remove_item(self.channel_selector)
        self.channel_selector = HierarchyFinalChannelSelect(self)
        self.add_item(self.channel_selector)
        self._sync_buttons()

    def embed(self):
        channel = self.guild.get_channel(self.channel_id) if self.channel_id else None
        e = PanelMessage(title="Etapa 4 — Canal de publicação", description="Escolha o canal no menu. Use as setas para ver os demais canais quando houver mais de 25. Ao atualizar, o bot substitui somente as mensagens antigas da hierarquia enviadas por ele; outras mensagens do canal são preservadas.", color=discord.Color.blurple())
        e.add_field(name="Canal selecionado", value=channel.mention if channel else "Nenhum canal selecionado", inline=False)
        e.set_footer(text=f"Página {self.channel_page + 1}/{self.channel_pages} • O bot precisa de Ver canal, Enviar mensagens, Incorporar links e Gerenciar mensagens.")
        return e

    @discord.ui.button(label="◀ Anterior", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.channel_page = max(0, self.channel_page - 1)
        self.refresh()
        await interaction.response.edit_message(view=v2_view(self.embed(), self, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))

    @discord.ui.button(label="Próxima página ▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.channel_page = min(self.channel_pages - 1, self.channel_page + 1)
        self.refresh()
        await interaction.response.edit_message(view=v2_view(self.embed(), self, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id and interaction.user.id != self.owner_id:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Somente quem iniciou a edição pode alterar o canal."))
            return False
        return True

    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.return_to_edit:
            view = HierarchyEditMenuView(self.guild, self.owner_id or interaction.user.id)
            await interaction.response.edit_message(view=v2_view(view.embed(), view, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))
            return
        rows = hierarchy_rows()
        view = HierarchyTagConfigView(self.guild, [rid for _priority, rid, _tag in rows], interaction.user.id)
        view.tags = [tag for _priority, _rid, tag in rows]
        view._sync_buttons()
        await interaction.response.edit_message(view=v2_view(view.embed(), view, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))

    @discord.ui.button(label="Atualizar hierarquia neste canal", style=discord.ButtonStyle.success, emoji="🚀", row=2)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        issue = hierarchy_configuration_issue(self.guild)
        if issue:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, issue))
            return
        channel = self.guild.get_channel(self.channel_id) if self.channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Selecione um canal válido antes de publicar."))
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await publish_configured_panel(interaction, "hierarchy", channel_override=channel.id)


class ConfigPanelView(ComponentsV2View):
    def __init__(self):
        super().__init__(timeout=900)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not (interaction.guild and isinstance(interaction.user, discord.Member) and (
            interaction.user.guild_permissions.administrator or has_leadership(interaction)
            or any(role.id == DEVELOPER_ROLE_ID for role in interaction.user.roles)
        )):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Somente desenvolvedor, liderança ou administradores podem alterar a configuração."))
            return False
        return True

    @discord.ui.button(label="WL", style=discord.ButtonStyle.primary, emoji="📋", row=0)
    async def wl(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ConfigWLPanelModal())

    @discord.ui.button(label="Hierarquia", style=discord.ButtonStyle.primary, emoji="🏛️", row=0)
    async def hierarchy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use este painel dentro do servidor."))
            return
        guide = PanelMessage(
            title="🏛️ Guia de configuração da hierarquia",
            description=(
                "Configure a hierarquia em quatro etapas: **1)** quantidade de cargos; "
                "**2)** cargos, do mais alto ao mais baixo; **3)** tag/abreviação de cada cargo; "
                "**4)** canal de publicação.\n\n"
                "O bot identifica o cargo de maior prioridade configurado e atualiza o apelido no formato "
                "**[TAG] Nome RP | ID**. Após a WL aprovada, usa a tag do cargo concedido. Os dados ficam no SQLite.\n\n"
                "Você também pode apagar a configuração anterior e remover somente as mensagens "
                "da hierarquia publicadas pelo bot, preservando o restante do canal."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(view=v2_view(guide, HierarchyGuideV2(interaction.guild), None), ephemeral=True)

    @discord.ui.button(label="Farm", style=discord.ButtonStyle.primary, emoji="🌾", row=0)
    async def farm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ConfigFarmPanelModal())

    @discord.ui.button(label="Publicar WL", style=discord.ButtonStyle.success, emoji="📨", row=2)
    async def publish_wl(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await publish_configured_panel(interaction, "wl")

    @discord.ui.button(label="Publicar hierarquia", style=discord.ButtonStyle.success, emoji="🏛️", row=2)
    async def publish_hierarchy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use este recurso dentro de um servidor Discord."))
            return
        view = HierarchyFinalChannelView(interaction.guild)
        await interaction.response.send_message(view=v2_view(view.embed(), view, None), ephemeral=True)

    @discord.ui.button(label="Publicar Farm", style=discord.ButtonStyle.success, emoji="🌾", row=2)
    async def publish_farm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await publish_configured_panel(interaction, "farm")


async def resolve_text_channel(guild: discord.Guild, channel_id: int) -> Optional[discord.TextChannel]:
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is None and channel_id:
        try:
            fetched = await guild.fetch_channel(channel_id)
            channel = fetched if isinstance(fetched, discord.TextChannel) else None
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
    return channel if isinstance(channel, discord.TextChannel) else None


async def publish_configured_panel(interaction: discord.Interaction, kind: str, *, channel_override: Optional[int] = None) -> None:
    # Publicações podem percorrer histórico, membros e vários canais; reconheça
    # a interação antes de iniciar qualquer operação de rede demorada.
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)

    async def reply(content: str, **kwargs) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(None, **kwargs, view=v2_view(None, None, content))
        else:
            await interaction.response.send_message(None, **kwargs, view=v2_view(None, None, content))
    if not interaction.guild:
        await reply("Use este recurso dentro de um servidor Discord.", ephemeral=True); return
    defaults = {
        "wl": ("wl_panel_channel_id", str(REGISTRATION_CHANNEL_ID)),
        "hierarchy": ("hierarchy_panel_channel_id", "0"),
        "farm": ("farm_panel_channel_id", "0"),
        "main": ("main_panel_channel_id", "0"),
        "members": ("members_panel_channel_id", "0"),
        "leader": ("leader_panel_channel_id", "0"),
    }
    key, fallback = defaults[kind]
    try:
        channel_id = int(channel_override if channel_override is not None else (panel_setting(key, fallback) or 0))
    except (TypeError, ValueError):
        channel_id = 0
    if not channel_id:
        await reply(f"Configure o canal de publicação em `/config` antes de publicar o painel {kind}.", ephemeral=True); return
    target = await resolve_text_channel(interaction.guild, channel_id)
    if not target:
        await reply(f"Não encontrei o canal `{channel_id}` ou o bot não tem acesso a ele. Confira o canal e as permissões.", ephemeral=True); return
    if kind == "wl":
        required_wl = (
            ("member_role_id", "Cargo aplicado após aprovação", str(MEMBER_ROLE_ID)),
            ("wl_approver_role_id", "Cargo autorizado a aprovar/reprovar", str(RECRUIT_ROLE_ID)),
            ("recruit_role_id", "Cargo de recrutadores", str(recruiter_role_id())),
            ("wl_panel_channel_id", "Canal de publicação", str(REGISTRATION_CHANNEL_ID)),
            ("wl_channel_id", "Canal de solicitações", str(APPROVAL_CHANNEL_ID)),
        )
        missing_wl = []
        for config_key, label, fallback in required_wl:
            raw_id = panel_setting(config_key, fallback)
            try: object_id = int(raw_id or 0)
            except (TypeError, ValueError): object_id = 0
            obj = interaction.guild.get_role(object_id) if config_key.endswith("role_id") else interaction.guild.get_channel(object_id)
            if obj is None:
                missing_wl.append(label)
        if missing_wl:
            await reply("A WL ainda não está pronta. Configure estes campos em `/config` → WL:\n" + "\n".join(f"• {x}" for x in missing_wl), ephemeral=True)
            return
    try:
        embed = panel_embed(kind)
        if kind == "wl":
            await target.send(view=v2_view(embed, RegistrationPanelView(), None))
        elif kind == "farm":
            await target.send(view=v2_view(embed, FarmPanelView(), None))
        elif kind == "hierarchy":
            issue = hierarchy_configuration_issue(interaction.guild)
            if issue:
                await reply(issue, ephemeral=True)
                return
            try:
                await interaction.guild.chunk(cache=True)
            except Exception:
                log.exception("Não consegui carregar todos os membros antes de publicar a hierarquia")
                await reply(
                    "Não consegui carregar a lista completa de membros. Ative **Server Members Intent** no Discord Developer Portal, confira as permissões e tente publicar novamente.",
                    ephemeral=True,
                )
                return

            configured = []
            for priority, role_id, abbreviation in hierarchy_rows():
                role = interaction.guild.get_role(role_id)
                if role and role != interaction.guild.default_role:
                    configured.append((priority, role, (abbreviation or "").strip()))

            # Publicação sempre do maior cargo (maior prioridade) para o menor.
            configured.sort(key=lambda item: item[0], reverse=True)
            if not configured:
                await reply("Nenhum cargo está configurado para exibição. Abra `/config` > Hierarquia e selecione os cargos.", ephemeral=True)
                return

            # Guarda as mensagens antigas até que a nova publicação esteja completa.
            # Se o envio falhar no meio, removemos somente as novas e mantemos as antigas.
            old_hierarchy_messages = []
            try:
                async for old_message in target.history(limit=None):
                    is_hierarchy_post = (
                        bot.user is not None
                        and old_message.author.id == bot.user.id
                        and any(
                            line.lstrip("# ").startswith("📋 | ")
                            for line in message_component_text(old_message).splitlines()
                        )
                    )
                    if is_hierarchy_post:
                        old_hierarchy_messages.append(old_message)
            except discord.HTTPException:
                log.exception("Não consegui ler o histórico do canal antes de substituir a hierarquia")
                await reply(
                    "Não consegui ler o histórico do canal para substituir a hierarquia com segurança. Confira a permissão **Ler histórico de mensagens**.",
                    ephemeral=True,
                )
                return

            ranks = [(priority, role.id, abbreviation, role) for priority, role, abbreviation in configured]
            rank_priorities = {role_id: priority for priority, role_id, _, _ in ranks}
            members_by_highest_rank: dict[int, list[discord.Member]] = {role_id: [] for _, role_id, _, _ in ranks}
            configured_role_ids = set(rank_priorities)
            for member in interaction.guild.members:
                if member.bot:
                    continue
                owned_rank_ids = [role.id for role in member.roles if role.id in configured_role_ids]
                if not owned_rank_ids:
                    continue
                highest_role_id = max(owned_rank_ids, key=lambda rid: rank_priorities[rid])
                members_by_highest_rank[highest_role_id].append(member)

            total_ranks = len(ranks)
            sent_messages = []
            try:
                # Cabeçalho do painel com a aparência configurada, marcado para
                # ser substituído junto com as mensagens de cargos na próxima publicação.
                header_message = embed.copy()
                header_message.title = f"📋 | {header_message.title or f'HIERARQUIA — {FACTION_NAME}'}"[:256]
                if not header_message.description:
                    header_message.description = "Cargos e membros da organização."
                sent_messages.append(await target.send(view=v2_view(header_message, None, None)))

                # Envia todos os cargos, sempre do maior para o menor.
                for priority, role_id, abbreviation, role in ranks:
                    members = sorted(
                        members_by_highest_rank.get(role_id, []),
                        key=lambda member: member.display_name.casefold(),
                    )
                    member_lines = [member.mention for member in members] or ["Ninguém com esse cargo."]
                    chunks, buffer, size = [], [], 0
                    for line in member_lines:
                        if buffer and size + len(line) + 1 > 3500:
                            chunks.append(buffer)
                            buffer, size = [], 0
                        buffer.append(line)
                        size += len(line) + 1
                    if buffer:
                        chunks.append(buffer)
                    for chunk_index, chunk in enumerate(chunks, start=1):
                        suffix = f" · continuação {chunk_index}/{len(chunks)}" if len(chunks) > 1 else ""
                        rank_title = f"📋 | {role.name.upper()} - [{len(members)}] membros{suffix}"
                        body = f"# {role.mention}\n" + "\n".join(chunk)
                        body += f"\n\n**Atualizado Automaticamente | Última alteração: {datetime.now().strftime('%d/%m/%Y, %H:%M')}**"
                        role_message = PanelMessage(
                            title=rank_title[:256],
                            description=body[:4096],
                            color=discord.Color.from_rgb(76, 86, 180),
                            timestamp=datetime.now(timezone.utc),
                        )
                        sent_messages.append(await target.send(view=v2_view(role_message, None, None)))
            except Exception:
                log.exception("Falha ao enviar a nova hierarquia; preservando a publicação anterior")
                for new_message in sent_messages:
                    try:
                        await new_message.delete()
                    except discord.HTTPException:
                        log.warning("Não consegui remover uma mensagem parcial da nova hierarquia", exc_info=True)
                raise

            # Só remove a versão anterior depois de confirmar que a nova foi enviada.
            delete_failures = 0
            for old_message in old_hierarchy_messages:
                try:
                    await old_message.delete()
                except discord.HTTPException:
                    delete_failures += 1
                    log.warning("Não consegui remover uma mensagem antiga da hierarquia (%s)", old_message.id, exc_info=True)

            # Aplica as tags recém-configuradas também aos membros que já estavam no servidor.
            for current_member in interaction.guild.members:
                if not current_member.bot:
                    await sync_configured_member_nickname(current_member, "Nova hierarquia publicada")

            warning = (
                f" Atenção: não consegui remover {delete_failures} mensagem(ns) antiga(s); confira o canal."
                if delete_failures else ""
            )
            await reply(
                f"✅ Hierarquia publicada em {target.mention}: **{total_ranks} cargos**, cada um em mensagem separada. "
                f"O cargo nº 1 (mais alto) aparece primeiro; o último cargo (mais baixo) aparece por último. "
                f"({len(sent_messages)} mensagem(ns), incluindo cabeçalho).{warning}",
                ephemeral=True,
            )
        elif kind == "members":
            with db() as con:
                rows = con.execute("SELECT discord_id,character_name,game_id,rank_name,slot_number FROM members WHERE status='ACTIVE' ORDER BY rank_name,character_name LIMIT 50").fetchall()
            if not rows:
                embed.add_field(name="Membros", value="Ainda não há membros ativos cadastrados.", inline=False)
            else:
                lines = [f"• <@{r['discord_id']}> — **{r['character_name']}** · ID `{r['game_id']}` · {r['rank_name']}" + (f" · Slot {r['slot_number']}" if r['slot_number'] is not None else "") for r in rows]
                embed.description = (embed.description + "\n\n" + "\n".join(lines))[:4096]
            await target.send(view=v2_view(embed, None, None))
        elif kind == "leader":
            await target.send(view=v2_view(embed, LeadershipPanelView(), None))
        else:
            await target.send(view=v2_view(embed, None, None))
        await reply(f"✅ Painel **{kind}** publicado em {target.mention}.", ephemeral=True)
    except (discord.Forbidden, discord.HTTPException) as exc:
        log.exception("Não foi possível publicar painel %s", kind)
        await reply(f"Não consegui publicar o painel. Confira as permissões do bot. Detalhe: {str(exc)[:180]}", ephemeral=True)


# ---------------- BOT / COMANDOS ----------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
_render_http_runner = None


async def start_render_health_server() -> None:
    """Abre uma rota HTTP mínima quando o host fornece PORT (ex.: Render Web Service)."""
    global _render_http_runner
    raw_port = os.getenv("PORT", "").strip()
    if not raw_port:
        return  # Background Worker: não precisa de uma porta HTTP.

    try:
        port = int(raw_port)
        if not 1 <= port <= 65535:
            raise ValueError("PORT fora do intervalo válido")
    except ValueError as exc:
        raise RuntimeError(f"A variável PORT do host não é válida: {raw_port!r}") from exc

    from aiohttp import web

    async def health(_: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "bot_ready": bot.is_ready(),
            "service": "fac-control-fivem",
        })

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    _render_http_runner = runner
    log.info("Servidor de healthcheck HTTP iniciado em 0.0.0.0:%s (/health).", port)



def remove_legacy_gtt_branding() -> None:
    """Limpa menções antigas a GTT de títulos e descrições salvos no banco."""
    try:
        with db() as con:
            rows = con.execute("SELECT key,value FROM settings").fetchall()
            for row in rows:
                value = re.sub(r"\bGTT\b", "", str(row["value"]), flags=re.IGNORECASE)
                value = re.sub(r"\s{2,}", " ", value).strip(" -—|")
                if value != row["value"]:
                    con.execute("UPDATE settings SET value=? WHERE key=?", (value, row["key"]))
    except sqlite3.Error:
        log.exception("Falha ao limpar nomes antigos da organização nas configurações")


async def setup_hook() -> None:
    init_db()
    remove_legacy_gtt_branding()
    await start_render_health_server()
    apply_saved_settings()
    bot.add_view(RegistrationPanelView())
    bot.add_view(RegistrationApprovalView())
    bot.add_view(FarmPanelView())
    bot.add_view(FarmInvoiceView())
    bot.add_view(FarmProofReviewView())
    bot.add_view(LeadershipPanelView())
    # Views persistentes do módulo nativo de tickets (Components V2).
    bot.add_view(TicketPanelView())
    bot.add_view(TicketControlsView())
    bot.loop.create_task(expire_pending_wls())

    # Sincronização resiliente: um DISCORD_GUILD_ID incorreto ou um servidor
    # onde o bot não foi instalado pode causar 403/50001. Isso não deve derrubar
    # o processo inteiro no Render; tentamos a sincronização global como fallback.
    if GUILD_ID:
        guild_obj = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild_obj)
        try:
            synced = await bot.tree.sync(guild=guild_obj)
            log.info("Sincronizados %s comandos slash no servidor %s.", len(synced), GUILD_ID)
            return
        except discord.Forbidden as exc:
            log.error(
                "Não foi possível sincronizar comandos no servidor %s (403 Missing Access: %s). "
                "Confira se DISCORD_GUILD_ID é o ID correto e se ESTE bot foi convidado para esse servidor "
                "com os escopos bot e applications.commands. Tentando sincronização global.",
                GUILD_ID, exc,
            )
        except discord.HTTPException as exc:
            log.error(
                "Falha HTTP ao sincronizar comandos no servidor %s (%s). Tentando sincronização global.",
                GUILD_ID, exc,
            )

    try:
        synced = await bot.tree.sync()
        log.info(
            "Sincronizados %s comandos slash globalmente. A publicação global pode levar algum tempo para aparecer.",
            len(synced),
        )
    except discord.Forbidden as exc:
        log.error(
            "O Discord também recusou a sincronização global (403: %s). Confira o token da aplicação e o convite "
            "do bot com o escopo applications.commands. O processo continuará ligado, mas os slash commands "
            "podem não aparecer até a sincronização ser corrigida.", exc
        )
    except discord.HTTPException as exc:
        log.error(
            "Falha HTTP ao sincronizar comandos slash globalmente (%s). O bot continuará ligado; "
            "veja os logs e tente novamente após corrigir a configuração.", exc
        )

# setup_hook é um método do ciclo de vida de discord.py, não um evento.
# Registrá-lo com @bot.event não o executa durante a inicialização.
bot.setup_hook = setup_hook  # type: ignore[method-assign]

@bot.event
async def on_member_join(member: discord.Member) -> None:
    pending_role_id = int(setting("wl_pending_role_id", "0") or 0)
    pending_role = member.guild.get_role(pending_role_id) if pending_role_id else None
    if pending_role:
        try:
            await member.add_roles(pending_role, reason="Cargo inicial antes da whitelist")
        except discord.Forbidden:
            log.warning("Sem permissão para aplicar o cargo pré-WL ao novo membro %s", member.id)
    await sync_configured_member_nickname(member, "Membro entrou no servidor")


async def sync_configured_member_nickname(
    member: discord.Member,
    reason: str = "Sincronização automática da hierarquia",
    *,
    extra_role_ids=None,
) -> None:
    """Atualiza [TAG] Nome RP | ID dos membros com WL aprovada."""
    with db() as con:
        row = con.execute(
            "SELECT character_name,game_id FROM members WHERE discord_id=? AND status='ACTIVE'",
            (str(member.id),),
        ).fetchone()
    if not row:
        return

    rank_role, abbreviation = highest_configured_rank(member, extra_role_ids=extra_role_ids)
    with db() as con:
        con.execute(
            "UPDATE members SET rank_name=? WHERE discord_id=?",
            (rank_role.name if rank_role else "Membro", str(member.id)),
        )

    nickname = build_member_nickname(row["character_name"], row["game_id"], abbreviation or "M")
    if member.nick == nickname:
        return
    try:
        await member.edit(nick=nickname, reason=reason)
    except discord.Forbidden:
        log.warning("Sem permissão para atualizar nickname do membro %s; confira a posição do cargo do bot.", member.id)
    except discord.HTTPException as exc:
        log.warning("Falha ao sincronizar nickname do membro %s: %s", member.id, exc)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member) -> None:
    before_roles = {role.id for role in before.roles}
    after_roles = {role.id for role in after.roles}
    if before_roles != after_roles:
        await sync_configured_member_nickname(after, "Cargo da hierarquia alterado")


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    receipt_handled = await store_farm_payment_receipt(message)
    if receipt_handled:
        return
    handled = await store_farm_proof(message)
    if handled:
        return
    await bot.process_commands(message)

@bot.event
async def on_ready() -> None:
    log.info("Bot conectado como %s (%s)", bot.user, bot.user.id if bot.user else "?")
    if GUILD_ID and bot.get_guild(GUILD_ID) is None:
        log.error(
            "DISCORD_GUILD_ID=%s não aparece na lista de servidores que este bot consegue acessar. "
            "Confirme o ID no Discord (Modo Desenvolvedor) e se o bot correto foi convidado para esse servidor.",
            GUILD_ID,
        )





class FarmItemConfigModal(ComponentsV2Modal, title="Adicionar item ao Farm"):
    nome = discord.ui.TextInput(label="Nome do item", placeholder="Ex.: Alumínio", max_length=60)
    valor = discord.ui.TextInput(label="Valor por unidade (R$)", placeholder="Ex.: 500,00", max_length=20)
    pontos = discord.ui.TextInput(label="Pontos por unidade", placeholder="Ex.: 1", default="1", max_length=7)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.nome.value.strip()
        raw_value = self.valor.value.strip().replace("R$", "").replace(" ", "")
        raw_value = raw_value.replace(".", "").replace(",", ".") if "," in raw_value else raw_value
        try:
            cents = int(round(float(raw_value) * 100))
            points = int(self.pontos.value.strip())
        except (ValueError, OverflowError):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Informe um valor em reais e uma quantidade de pontos válidos."))
            return
        if not name or cents <= 0 or points < 1 or cents > 100000000:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Confira o nome, o valor (maior que zero) e os pontos (mínimo 1)."))
            return
        with db() as con:
            con.execute(
                "INSERT INTO farm_items(name,points,target,active,unit_price_cents) VALUES(?,?,0,1,?) "
                "ON CONFLICT(name) DO UPDATE SET points=excluded.points,active=1,unit_price_cents=excluded.unit_price_cents",
                (name, points, cents),
            )
        log_action(interaction.user.id, "FARM_ITEM_CONFIG", None, "", f"{name}: R$ {cents/100:.2f}; {points} ponto(s)")
        # LayoutView usa Components V2: não envie o campo legado `content` junto.
        await interaction.response.send_message(
            view=FarmItemSetupV2(
                success_message=f"✅ **{name}** salvo — R$ {cents/100:.2f} por unidade; {points} ponto(s) por unidade."
            ),
            ephemeral=True,
        )


class FarmItemSetupV2(ComponentsV2LayoutView):
    def __init__(self, success_message: str = ""):
        super().__init__(timeout=900)
        with db() as con:
            rows = con.execute("SELECT name,unit_price_cents,points FROM farm_items WHERE active=1 ORDER BY name").fetchall()
        summary = "\n".join(f"• **{r['name']}** — R$ {int(r['unit_price_cents'] or 0)/100:.2f} / un. · {r['points']} pt(s)" for r in rows[:25])
        if len(rows) > 25:
            summary += f"\n… e mais {len(rows)-25} item(ns)."
        if not summary:
            summary = "Nenhum item cadastrado ainda."
        parts = []
        if success_message:
            parts.extend([discord.ui.TextDisplay(success_message), discord.ui.Separator()])
        parts.extend([
            discord.ui.TextDisplay("## 🌾 Etapa 1 — Itens e valores\nCadastre os itens que os membros poderão registrar. Você pode adicionar quantos precisar."),
            discord.ui.Separator(),
            discord.ui.TextDisplay(summary),
        ])
        buttons = [discord.ui.Button(label="Adicionar item", emoji="➕", style=discord.ButtonStyle.success, custom_id="fac:farmv2:add")]
        # O botão Próximo só aparece depois que existe pelo menos um item ativo.
        if rows:
            buttons.append(discord.ui.Button(label="Próximo", emoji="➡️", style=discord.ButtonStyle.primary, custom_id="fac:farmv2:next"))
        parts.append(discord.ui.ActionRow(*buttons))
        container = discord.ui.Container(*parts)
        self.add_item(container)
        row = container.children[-1]
        row.children[0].callback = self.add_item_modal
        if len(row.children) > 1:
            row.children[1].callback = self.next_step

    async def add_item_modal(self, interaction: discord.Interaction):
        await interaction.response.send_modal(FarmItemConfigModal())

    async def next_step(self, interaction: discord.Interaction):
        if not await deny_unless_config_access(interaction):
            return
        with db() as con:
            count = con.execute("SELECT COUNT(*) AS n FROM farm_items WHERE active=1").fetchone()["n"]
        if not count:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Cadastre pelo menos um item antes de continuar."))
            return
        await interaction.response.send_modal(ConfigFarmPanelModal())


FARM_CONFIG_FIELDS = {
    "farm_panel_channel_id": ("Canal público do Farm", "channel"),
    "farm_private_channel_id": ("Canal privado da liderança", "channel"),
    "farm_approver_role_id": ("Cargo autorizado a aprovar, pagar ou recusar", "role"),
    "farm_log_channel_id": ("Canal de histórico final (opcional)", "channel"),
}


class FarmConfigFieldSelect(discord.ui.Select):
    def __init__(self, parent: "ConfigFarmChannelsV2"):
        self.parent_view = parent
        options = [
            discord.SelectOption(label=label[:100], value=key, default=(key == parent.selected_key))
            for key, (label, _kind) in FARM_CONFIG_FIELDS.items()
        ]
        super().__init__(placeholder="Escolha qual configuração deseja editar",
                         min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            view=ConfigFarmChannelsV2(
                interaction.guild, selected_key=self.values[0], page=0,
                success_message="Agora escolha o canal ou cargo. Use as setas para percorrer todas as opções.",
            ), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else [])
        )


class FarmConfigTargetSelect(discord.ui.Select):
    def __init__(self, parent: "ConfigFarmChannelsV2", options: list[discord.SelectOption]):
        self.parent_view = parent
        super().__init__(placeholder=f"Opções — página {parent.page + 1}",
                         min_values=1, max_values=1, options=options[:25], row=1)

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        key = self.parent_view.selected_key
        if selected == "__none__":
            await interaction.response.send_message(
                None,
                ephemeral=True, view=v2_view(None, None, "Não há opções disponíveis. Confira os canais/cargos e as permissões do bot."),
            )
            return
        save_config_value(key, selected)
        label = FARM_CONFIG_FIELDS[key][0]
        if key == "farm_approver_role_id":
            obj = interaction.guild.get_role(int(selected)) if interaction.guild else None
        else:
            obj = interaction.guild.get_channel(int(selected)) if interaction.guild else None
        shown = obj.mention if obj else f"`{selected}`"
        await interaction.response.edit_message(
            view=ConfigFarmChannelsV2(
                interaction.guild, selected_key=key, page=self.parent_view.page,
                success_message=f"✅ **{label}** salvo: {shown}",
            ), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else [])
        )


class FarmConfigPageButton(discord.ui.Button):
    def __init__(self, parent: "ConfigFarmChannelsV2", delta: int, *, disabled: bool):
        self.parent_view = parent
        self.delta = delta
        super().__init__(label=("◀ Anterior" if delta < 0 else "Próxima ▶"),
                         style=discord.ButtonStyle.secondary, disabled=disabled, row=2)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            view=ConfigFarmChannelsV2(
                interaction.guild,
                selected_key=self.parent_view.selected_key,
                page=max(0, self.parent_view.page + self.delta),
            ), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else [])
        )


class FarmSendPanelButton(discord.ui.Button):
    def __init__(self, parent: "ConfigFarmChannelsV2"):
        super().__init__(
            label="ENVIAR PAINEL",
            emoji="📨",
            style=discord.ButtonStyle.success,
            row=3,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if not await deny_unless_config_access(interaction):
            return
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                None, ephemeral=True, view=v2_view(None, None, "Use esta configuração dentro do servidor.")
            )
            return

        required = (
            ("farm_panel_channel_id", "Canal público do Farm", "channel"),
            ("farm_private_channel_id", "Canal privado da liderança", "channel"),
            ("farm_approver_role_id", "Cargo autorizado a aprovar, pagar ou recusar", "role"),
        )
        missing = []
        for key, label, kind in required:
            raw_id = panel_setting(key, "0")
            try:
                object_id = int(raw_id or 0)
            except (TypeError, ValueError):
                object_id = 0
            obj = guild.get_role(object_id) if kind == "role" and object_id else (
                guild.get_channel(object_id) if object_id else None
            )
            if obj is None:
                missing.append(label)

        if missing:
            await interaction.response.send_message(
                None,
                ephemeral=True, view=v2_view(None, None, "Ainda faltam configurações obrigatórias válidas:\n"
                + "\n".join(f"• {label}" for label in missing)
                + "\nSelecione cada campo acima para definir ou corrigir."),
            )
            return

        await publish_configured_panel(interaction, "farm")


class ConfigFarmChannelsV2(ComponentsV2LayoutView):
    """Configuração paginada com resumo dos campos salvos e possibilidade de redefinir qualquer campo."""
    PAGE_SIZE = 25

    def __init__(self, guild: discord.Guild | None, success_message: str = "",
                 *, selected_key: str = "farm_panel_channel_id", page: int = 0):
        super().__init__(timeout=900)
        self.guild = guild
        self.selected_key = selected_key if selected_key in FARM_CONFIG_FIELDS else "farm_panel_channel_id"
        self.page = max(0, int(page))
        label, kind = FARM_CONFIG_FIELDS[self.selected_key]
        objects = self._get_objects(kind)
        page_count = max(1, (len(objects) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = min(self.page, page_count - 1)
        page_objects = objects[self.page * self.PAGE_SIZE:(self.page + 1) * self.PAGE_SIZE]

        options = []
        for obj in page_objects:
            if kind == "role":
                option_label = obj.name
                option_desc = f"Cargo • posição {obj.position}"
            else:
                option_label = f"#{obj.name}"
                category = getattr(getattr(obj, "category", None), "name", None)
                option_desc = f"Canal de texto • {category}" if category else "Canal de texto"
            options.append(discord.SelectOption(
                label=option_label[:100], value=str(obj.id), description=option_desc[:100]
            ))
        if not options:
            options = [discord.SelectOption(
                label="Nenhuma opção encontrada", value="__none__",
                description="Verifique as permissões e o servidor",
            )]

        summary_lines = self._build_current_summary()
        required_keys = (
            "farm_panel_channel_id",
            "farm_private_channel_id",
            "farm_approver_role_id",
        )
        complete_count = sum(1 for key in required_keys if self._resolve_saved_object(key) is not None)
        all_required_ready = complete_count == len(required_keys)

        parts = []
        if success_message:
            parts.extend([discord.ui.TextDisplay(success_message), discord.ui.Separator()])
        parts.extend([
            discord.ui.TextDisplay("## ✅ Configurações já definidas\n" + "\n".join(summary_lines)),
            discord.ui.TextDisplay(
                f"**Obrigatórias concluídas: {complete_count}/3** · "
                "O canal de histórico é opcional."
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                "## 🌾 Configurar canais do Farm\n"
                "Escolha abaixo qual campo deseja definir ou **redefinir**. "
                "O resumo acima é atualizado conforme você salva cada opção."
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"**Editando agora:** {label}"),
            discord.ui.ActionRow(FarmConfigFieldSelect(self)),
            discord.ui.ActionRow(FarmConfigTargetSelect(self, options)),
            discord.ui.TextDisplay(
                f"Página **{self.page + 1}/{page_count}** · "
                f"{len(objects)} opção(ões) encontradas"
            ),
            discord.ui.ActionRow(
                FarmConfigPageButton(self, -1, disabled=(self.page == 0)),
                FarmConfigPageButton(self, 1, disabled=(self.page >= page_count - 1)),
                ConfigManualIdButton(
                    row=2, preset_key=self.selected_key, farm_return=True
                ),
            ),
        ])
        if all_required_ready:
            parts.append(discord.ui.ActionRow(FarmSendPanelButton(self)))
        else:
            parts.append(discord.ui.TextDisplay(
                "🔒 **ENVIAR PAINEL** será liberado quando o canal público, "
                "o canal privado e o cargo autorizador estiverem definidos."
            ))
        # Containers aceitam no máximo 10 filhos. O assistente pode ter até
        # 13 blocos quando exibe resumo, paginação e botão de publicação.
        add_v2_containers(self, parts, limit=10)

    def _resolve_saved_object(self, key: str):
        if not self.guild:
            return None
        raw_id = panel_setting(key, "0")
        try:
            object_id = int(raw_id or 0)
        except (TypeError, ValueError):
            return None
        if object_id <= 0:
            return None
        _label, kind = FARM_CONFIG_FIELDS[key]
        if kind == "role":
            role = self.guild.get_role(object_id)
            return role if role and not role.is_default() else None
        channel = self.guild.get_channel(object_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    def _build_current_summary(self) -> list[str]:
        summary = []
        for key, (label, kind) in FARM_CONFIG_FIELDS.items():
            obj = self._resolve_saved_object(key)
            optional = " *(opcional)*" if key == "farm_log_channel_id" else ""
            if obj is None:
                summary.append(f"▫️ **{label}:** Não definido{optional}")
            else:
                display = obj.mention if hasattr(obj, "mention") else f"`{obj.name}`"
                summary.append(f"✅ **{label}:** {display}{optional}")
        return summary

    def _get_objects(self, kind: str):
        if not self.guild:
            return []
        if kind == "role":
            return sorted(
                (role for role in self.guild.roles if not role.is_default()),
                key=lambda role: (role.position, role.id),
            )
        channel_types = tuple(
            cls for cls in (getattr(discord, "TextChannel", None),
                            getattr(discord, "NewsChannel", None))
            if cls is not None
        )
        return sorted(
            (channel for channel in self.guild.channels if isinstance(channel, channel_types)),
            key=lambda channel: (
                getattr(getattr(channel, "category", None), "position", -1),
                channel.position,
                channel.name.casefold(),
            ),
        )


class HierarchyEditMenuView(ComponentsV2View):
    """Menu de edição sem apagar a hierarquia que já está configurada."""
    def __init__(self, guild, owner_id=0):
        super().__init__(timeout=900)
        self.guild = guild
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id and interaction.user.id != self.owner_id:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Somente quem iniciou a edição pode alterar a hierarquia."))
            return False
        return True

    def embed(self):
        rows = hierarchy_rows()
        lines = []
        for index, (_priority, role_id, tag) in enumerate(rows, 1):
            role = self.guild.get_role(role_id)
            lines.append(f"**{index}.** {role.mention if role else f'Cargo removido ({role_id})'} — **[{tag}]**")
        if not lines:
            lines = ["Nenhum cargo salvo ainda. Comece uma configuração do zero."]
        return PanelMessage(
            title="✏️ Editar hierarquia",
            description=(
                "A hierarquia atual será preservada. Escolha exatamente o que deseja alterar:\n\n"
                "**Editar cargos e posições:** trocar cargos ou mudar a ordem do topo até a base.\n"
                "**Editar uma tag:** alterar a abreviação de qualquer cargo sem refazer as outras.\n"
                "**Editar canal:** trocar o canal onde a hierarquia é publicada.\n\n"
                "**Configuração atual:**\n" + "\n".join(lines)
            ),
            color=discord.Color.blurple(),
        )

    @discord.ui.button(label="Editar cargos e posições", style=discord.ButtonStyle.primary, emoji="🏛️", row=0)
    async def edit_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = hierarchy_rows()
        if not rows:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Ainda não existe uma hierarquia para editar. Use Começar do zero."))
            return
        try:
            expected = int(setting("hierarchy_role_count", str(len(rows))) or len(rows))
        except (TypeError, ValueError):
            expected = len(rows)
        count = max(len(rows), expected, 1)
        view = HierarchyRoleConfigView(self.guild, count, edit_mode=True)
        view.owner_id = self.owner_id or interaction.user.id
        view.role_ids = [role_id for _priority, role_id, _tag in rows[:count]] + [None] * max(0, count - len(rows))
        view._refresh_role_picker()
        await interaction.response.edit_message(view=v2_view(view.embed(), view, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))

    @discord.ui.button(label="Editar tag de qualquer cargo", style=discord.ButtonStyle.primary, emoji="🏷️", row=0)
    async def edit_tag(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not hierarchy_rows():
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Ainda não existe nenhum cargo salvo para editar."))
            return
        await interaction.response.send_modal(HierarchyEditTagModal(self.guild, self.owner_id or interaction.user.id, interaction.message))

    @discord.ui.button(label="Editar canal", style=discord.ButtonStyle.secondary, emoji="📣", row=1)
    async def edit_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = HierarchyFinalChannelView(self.guild, return_to_edit=True, owner_id=self.owner_id or interaction.user.id)
        await interaction.response.edit_message(view=v2_view(view.embed(), view, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))

    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.secondary, emoji="⬅️", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=HierarchyGuideV2(self.guild), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))


class HierarchyEditTagModal(ComponentsV2Modal, title="Editar tag de um cargo"):
    position = TextInput(label="Número do cargo na lista", placeholder="Ex.: 3", max_length=3, required=True)
    new_tag = TextInput(label="Nova tag (até 12 caracteres)", placeholder="Ex.: GT.G", max_length=12, required=True)

    def __init__(self, guild, owner_id, source_message):
        super().__init__(timeout=300)
        self.guild = guild
        self.owner_id = owner_id
        self.source_message = source_message

    async def on_submit(self, interaction: discord.Interaction):
        if self.owner_id and interaction.user.id != self.owner_id:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Somente quem iniciou a edição pode alterar as tags."))
            return
        raw_position = self.position.value.strip()
        tag = self.new_tag.value.strip().replace("[", "").replace("]", "")
        rows = hierarchy_rows()
        if not raw_position.isdigit() or not 1 <= int(raw_position) <= len(rows):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Informe uma posição entre 1 e {len(rows)}."))
            return
        if not tag or any(ch in tag for ch in "\n\r"):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Informe uma tag válida de até 12 caracteres."))
            return
        priority, role_id, _old_tag = rows[int(raw_position) - 1]
        save_config_value(f"rank_role_{role_id}", f"{tag}|{priority}")
        with db() as con:
            con.execute("DELETE FROM settings WHERE key=?", (f"hierarchy_draft_tag_{role_id}",))
        next_view = HierarchyEditMenuView(self.guild, self.owner_id)
        await update_hierarchy_modal_message(interaction, self.source_message, next_view)


class HierarchyGuideV2(ComponentsV2LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=900)
        self.guild = guild
        container = discord.ui.Container(
            discord.ui.TextDisplay(
                "## 🏛️ Hierarquia\n"
                "A configuração é feita do **cargo mais alto para o mais baixo**.\n\n"
                "**Editar hierarquia:** mantém a configuração atual e permite trocar cargos, mudar posições, "
                "editar a tag de qualquer cargo e trocar o canal de publicação.\n\n"
                "**Começar do zero:** apaga a configuração atual e as mensagens da hierarquia publicadas pelo bot, "
                "para montar tudo novamente.\n\n"
                "O apelido dos membros segue o formato `[TAG] Nome RP | ID`, usando a tag configurada para o cargo de maior prioridade."
            ),
            discord.ui.Separator(),
            discord.ui.ActionRow(
                discord.ui.Button(label="Editar hierarquia", emoji="✏️", style=discord.ButtonStyle.primary, custom_id="fac:hierarchyv2:edit"),
                discord.ui.Button(label="Começar do zero", emoji="🔄", style=discord.ButtonStyle.success, custom_id="fac:hierarchyv2:start"),
            ),
        )
        self.add_item(container)
        row = container.children[-1]
        row.children[0].callback = self.edit
        row.children[1].callback = self.start

    async def edit(self, interaction: discord.Interaction):
        view = HierarchyEditMenuView(self.guild, interaction.user.id)
        await interaction.response.edit_message(view=v2_view(view.embed(), view, None), content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))

    async def start(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            None,
            view=v2_view(
                None,
                HierarchyResetConfirmView(self.guild),
                "⚠️ **Começar do zero?** Isso apagará os cargos, tags, canal salvo e as mensagens da hierarquia publicadas pelo bot. A configuração atual não poderá ser recuperada.",
            ),
            ephemeral=True,
        )


class ConfigPanelV2(ComponentsV2LayoutView):
    """Painel principal de configuração usando Discord Components V2."""
    def __init__(self, include_banner: bool = True):
        super().__init__(timeout=900)
        components = [
            discord.ui.TextDisplay("# ⚙️ GERENTE-DE-FAC\nConfigure os módulos da facção por etapas.\n\n**Escolha o que deseja configurar:**"),
        ]
        if include_banner:
            components.append(discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://painel_configuracao.png", description="Painel de configuração GERENTE-DE-FAC")))
        components.extend([
            discord.ui.Separator(),
            discord.ui.TextDisplay("📋 **WL** — painel e cadastro de membros\n🏛️ **Hierarquia** — cargos, tags e publicação\n🌾 **Farm** — itens, valores, canais e cobranças\n🎫 **Tickets** — suporte, denúncias, outros assuntos, equipe e transcripts\n🧾 **Publicação** — enviar os painéis configurados"),
            discord.ui.Separator(),
            discord.ui.ActionRow(
                discord.ui.Button(label="WL", emoji="📋", style=discord.ButtonStyle.primary, custom_id="fac:configv2:wl"),
                discord.ui.Button(label="Hierarquia", emoji="🏛️", style=discord.ButtonStyle.primary, custom_id="fac:configv2:hierarchy"),
                discord.ui.Button(label="Farm", emoji="🌾", style=discord.ButtonStyle.primary, custom_id="fac:configv2:farm"),
                discord.ui.Button(label="Tickets", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="fac:configv2:tickets"),
            ),
            discord.ui.ActionRow(
                discord.ui.Button(label="Publicar WL", emoji="📨", style=discord.ButtonStyle.success, custom_id="fac:configv2:publish-wl"),
                discord.ui.Button(label="Publicar hierarquia", emoji="🏛️", style=discord.ButtonStyle.success, custom_id="fac:configv2:publish-hierarchy"),
                discord.ui.Button(label="Publicar Farm", emoji="🌾", style=discord.ButtonStyle.success, custom_id="fac:configv2:publish-farm"),
                discord.ui.Button(label="Publicar Tickets", emoji="🎫", style=discord.ButtonStyle.success, custom_id="fac:configv2:publish-tickets"),
            ),
        ])
        container = discord.ui.Container(*components)
        self.add_item(container)
        for item in container.children:
            if isinstance(item, discord.ui.ActionRow):
                for button in item.children:
                    button.callback = self._make_callback(button.custom_id)

    def _make_callback(self, action):
        async def callback(interaction: discord.Interaction):
            if not await deny_unless_config_access(interaction):
                return
            if action == "fac:configv2:wl":
                if not interaction.guild:
                    await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use a configuração dentro do servidor."))
                    return
                await interaction.response.send_message(view=WLConfigV2(interaction.guild), ephemeral=True)
            elif action == "fac:configv2:hierarchy":
                if not interaction.guild:
                    await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use no servidor."))
                    return
                # Components V2 não aceita o campo legado "content".
                # Todo o texto da tela fica dentro do LayoutView/Container.
                await interaction.response.send_message(
                    view=HierarchyGuideV2(interaction.guild), ephemeral=True
                )
            elif action == "fac:configv2:farm":
                await interaction.response.send_message(view=FarmItemSetupV2(), ephemeral=True)
            elif action == "fac:configv2:tickets":
                if not interaction.guild:
                    await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use a configuração dentro do servidor."))
                    return
                await interaction.response.send_message(view=TicketConfigV2(interaction.guild), ephemeral=True)
            elif action == "fac:configv2:publish-wl":
                await publish_configured_panel(interaction, "wl")
            elif action == "fac:configv2:publish-hierarchy":
                if not interaction.guild:
                    await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use no servidor."))
                    return
                view = HierarchyFinalChannelView(interaction.guild)
                await interaction.response.send_message(view=v2_view(view.embed(), view, None), ephemeral=True)
            elif action == "fac:configv2:publish-farm":
                await publish_configured_panel(interaction, "farm")
            elif action == "fac:configv2:publish-tickets":
                if not interaction.guild:
                    await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use a publicação dentro do servidor."))
                    return
                await interaction.response.defer(ephemeral=True, thinking=True)
                ok, message = await publish_ticket_panel(interaction.guild)
                await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, message))
        return callback


# ---------------- MÓDULO DE TICKETS (NATIVO / COMPONENTS V2) ----------------
TICKET_TYPES = {
    "support": {"label": "Suporte", "emoji": "🛠️", "category_key": "ticket_support_category_id", "role_key": "ticket_support_role_id"},
    "denuncia": {"label": "Denúncia", "emoji": "🚨", "category_key": "ticket_denuncia_category_id", "role_key": "ticket_denuncia_role_id"},
    "outros": {"label": "Outros assuntos", "emoji": "📌", "category_key": "ticket_outros_category_id", "role_key": "ticket_outros_role_id"},
}


def ticket_setting_complete(values: dict[str, str]) -> list[str]:
    """Validação pura da configuração; usada também nos testes automatizados."""
    required = [
        ("ticket_panel_channel_id", "canal onde o painel será publicado"),
        ("ticket_log_channel_id", "canal privado de logs e transcripts"),
    ]
    for ticket_type, data in TICKET_TYPES.items():
        required.append((data["category_key"], f"categoria de tickets de {data['label'].lower()}"))
        required.append((data["role_key"], f"cargo da equipe de {data['label'].lower()}"))
    return [label for key, label in required if not str(values.get(key, "0") or "0").isdigit() or int(values.get(key, "0") or 0) <= 0]


def ticket_user_limit(raw: str = "2") -> int:
    try:
        return max(1, min(10, int(raw)))
    except (TypeError, ValueError):
        return 2


def ticket_type_data(ticket_type: str) -> dict:
    return TICKET_TYPES.get(ticket_type, TICKET_TYPES["outros"])


def ticket_staff_role_id(ticket_type: str) -> int:
    try:
        return int(setting(ticket_type_data(ticket_type)["role_key"], "0") or 0)
    except (TypeError, ValueError):
        return 0


def ticket_is_staff(member: discord.Member, ticket_type: str) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_channels:
        return True
    role_id = ticket_staff_role_id(ticket_type)
    return bool(role_id and any(role.id == role_id for role in member.roles))


def ticket_config_issue(guild: discord.Guild) -> Optional[str]:
    values = {
        key: setting(key, "0")
        for key in (
            "ticket_panel_channel_id", "ticket_log_channel_id",
            "ticket_support_category_id", "ticket_support_role_id",
            "ticket_denuncia_category_id", "ticket_denuncia_role_id",
            "ticket_outros_category_id", "ticket_outros_role_id",
        )
    }
    missing = ticket_setting_complete(values)
    if missing:
        return "Falta configurar: " + ", ".join(missing) + ". Abra `/config` → **Tickets** e conclua os campos."
    panel = guild.get_channel(int(values["ticket_panel_channel_id"]))
    logs = guild.get_channel(int(values["ticket_log_channel_id"]))
    if not isinstance(panel, discord.TextChannel):
        return "O canal de publicação do painel não existe mais ou não é um canal de texto."
    if not isinstance(logs, discord.TextChannel):
        return "O canal de logs/transcripts não existe mais ou não é um canal de texto."
    for ticket_type, data in TICKET_TYPES.items():
        category = guild.get_channel(int(values[data["category_key"]]))
        role = guild.get_role(int(values[data["role_key"]]))
        if not isinstance(category, discord.CategoryChannel):
            return f"A categoria de **{data['label']}** não existe mais. Configure novamente."
        if not role or role.is_default():
            return f"O cargo da equipe de **{data['label']}** não existe mais. Configure novamente."
    me = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
    if not me:
        return "Não consegui identificar o membro do bot no servidor."
    if not me.guild_permissions.manage_channels:
        return "O bot precisa da permissão **Gerenciar canais** para criar tickets privados."
    if not (me.guild_permissions.view_channel and me.guild_permissions.send_messages):
        return "O bot precisa conseguir visualizar canais e enviar mensagens."
    if not panel.permissions_for(me).view_channel or not panel.permissions_for(me).send_messages:
        return "O bot não consegue enviar mensagens no canal do painel. Revise as permissões desse canal."
    log_perms = logs.permissions_for(me)
    if not (log_perms.view_channel and log_perms.send_messages and log_perms.attach_files):
        return "O bot precisa visualizar o canal de logs, enviar mensagens e anexar arquivos (transcripts)."
    return None


def ticket_config_summary(guild: discord.Guild) -> str:
    def channel_name(key: str, expected_type):
        try:
            obj = guild.get_channel(int(setting(key, "0") or 0))
        except (TypeError, ValueError):
            obj = None
        return obj.mention if isinstance(obj, expected_type) else "`Não configurado`"

    lines = [
        f"**Canal do painel:** {channel_name('ticket_panel_channel_id', discord.TextChannel)}",
        f"**Logs e transcripts:** {channel_name('ticket_log_channel_id', discord.TextChannel)}",
    ]
    for ticket_type, data in TICKET_TYPES.items():
        try:
            category = guild.get_channel(int(setting(data["category_key"], "0") or 0))
        except (TypeError, ValueError):
            category = None
        try:
            role = guild.get_role(int(setting(data["role_key"], "0") or 0))
        except (TypeError, ValueError):
            role = None
        category_text = category.name if isinstance(category, discord.CategoryChannel) else "Não configurada"
        role_text = role.mention if role else "Não configurado"
        lines.append(f"{data['emoji']} **{data['label']}:** `{category_text}` · equipe {role_text}")
    return "\n".join(lines)


class TicketPanelView(ComponentsV2View):
    """Painel público persistente para abrir os três tipos de atendimento."""
    def __init__(self):
        super().__init__(timeout=None)
        self.set_text(
            f"# 🎫 CENTRAL DE ATENDIMENTO · {FACTION_NAME}\n"
            "Escolha o assunto. Um canal privado será criado e ficará visível somente para você e a equipe responsável.\n\n"
            "🛠️ **Suporte:** dúvidas, problemas e solicitações.\n"
            "🚨 **Denúncia:** reporte jogadores ou integrantes da equipe. Envie provas dentro do ticket.\n"
            "📌 **Outros:** demais assuntos que não se encaixam nas opções anteriores."
        )
        buttons = [
            ("support", "Suporte", "🛠️", discord.ButtonStyle.primary),
            ("denuncia", "Denúncia", "🚨", discord.ButtonStyle.danger),
            ("outros", "Outros", "📌", discord.ButtonStyle.secondary),
        ]
        for ticket_type, label, emoji, style in buttons:
            button = discord.ui.Button(
                label=label, emoji=emoji, style=style,
                custom_id=f"fac:ticket:open:{ticket_type}", row=0,
            )
            button.callback = self._open_callback(ticket_type)
            self.add_item(button)

    def _open_callback(self, ticket_type: str):
        async def callback(interaction: discord.Interaction):
            if not interaction.guild:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Abra um ticket dentro do servidor."))
                return
            issue = ticket_config_issue(interaction.guild)
            if issue:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "O sistema de tickets ainda não está configurado corretamente. Avise a liderança."))
                log.warning("Painel de tickets clicado sem configuração válida: %s", issue)
                return
            await interaction.response.send_modal(TicketOpenModal(ticket_type))
        return callback


class TicketControlsView(ComponentsV2View):
    """Controles persistentes usados dentro de cada canal privado de ticket."""
    def __init__(self, intro: str = ""):
        super().__init__(timeout=None)
        text = intro or "**CONTROLES DO ATENDIMENTO**\nA equipe pode assumir o ticket. O autor ou a equipe pode solicitar o encerramento."
        self.set_text(text)
        definitions = [
            ("Assumir / liberar", "fac:ticket:claim", discord.ButtonStyle.primary, self.claim),
            ("Fechar ticket", "fac:ticket:close", discord.ButtonStyle.danger, self.close),
            ("Reabrir ticket", "fac:ticket:reopen", discord.ButtonStyle.success, self.reopen),
        ]
        for label, custom_id, style, callback in definitions:
            button = discord.ui.Button(label=label, custom_id=custom_id, style=style, row=0)
            button.callback = callback
            self.add_item(button)

    async def _get_ticket(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.channel:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esta ação só funciona dentro do servidor."))
            return None
        with db() as con:
            row = con.execute("SELECT * FROM tickets WHERE channel_id=?", (str(interaction.channel.id),)).fetchone()
        if not row:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Não encontrei o registro deste ticket no banco de dados."))
            return None
        return row

    async def claim(self, interaction: discord.Interaction):
        row = await self._get_ticket(interaction)
        if not row:
            return
        if not isinstance(interaction.user, discord.Member) or not ticket_is_staff(interaction.user, row["ticket_type"]):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Somente a equipe configurada pode assumir ou liberar este ticket."))
            return
        if row["status"] == "CLOSED":
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Este ticket está fechado. Reabra-o antes de assumir."))
            return
        current = str(row["claimed_by"] or "")
        if current and current != str(interaction.user.id):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Este ticket já está assumido por <@{current}>."))
            return
        new_claim = "" if current == str(interaction.user.id) else str(interaction.user.id)
        with db() as con:
            if new_claim:
                changed = con.execute(
                    "UPDATE tickets SET claimed_by=? WHERE id=? AND (claimed_by='' OR claimed_by IS NULL)",
                    (new_claim, row["id"]),
                ).rowcount
            else:
                changed = con.execute(
                    "UPDATE tickets SET claimed_by='' WHERE id=? AND claimed_by=?",
                    (row["id"], str(interaction.user.id)),
                ).rowcount
            latest = con.execute("SELECT claimed_by FROM tickets WHERE id=?", (row["id"],)).fetchone()
        if not changed:
            current_holder = str(latest["claimed_by"] or "") if latest else ""
            message = f"Este ticket acabou de ser assumido por <@{current_holder}>." if current_holder else "A atribuição foi alterada por outra ação. Tente novamente."
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, message))
            return
        action_text = f"Ticket #{row['id']} liberado por <@{interaction.user.id}>." if not new_claim else f"Ticket #{row['id']} assumido por <@{interaction.user.id}>."
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, action_text))
        if isinstance(interaction.channel, discord.TextChannel):
            try:
                await interaction.channel.send(view=v2_view(None, None, action_text))
            except discord.HTTPException:
                log.warning("Não consegui publicar a alteração de responsável no ticket #%s", row["id"])
        await ticket_send_log(interaction.guild, action_text)

    async def close(self, interaction: discord.Interaction):
        row = await self._get_ticket(interaction)
        if not row:
            return
        is_owner = str(interaction.user.id) == str(row["user_id"])
        is_staff = isinstance(interaction.user, discord.Member) and ticket_is_staff(interaction.user, row["ticket_type"])
        if not (is_owner or is_staff):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Somente quem abriu o ticket ou a equipe responsável pode fechá-lo."))
            return
        if row["status"] == "CLOSED":
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Este ticket já está fechado."))
            return
        await interaction.response.send_modal(TicketCloseModal())

    async def reopen(self, interaction: discord.Interaction):
        row = await self._get_ticket(interaction)
        if not row:
            return
        if not isinstance(interaction.user, discord.Member) or not ticket_is_staff(interaction.user, row["ticket_type"]):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Somente a equipe responsável pode reabrir o ticket."))
            return
        if row["status"] != "CLOSED":
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Este ticket já está aberto."))
            return
        channel = interaction.channel
        owner = interaction.guild.get_member(int(row["user_id"]))
        try:
            if owner:
                await channel.set_permissions(owner, view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
            new_name = re.sub(r"^fechado-", "", channel.name)[:95]
            await channel.edit(name=new_name, reason=f"Ticket #{row['id']} reaberto por {interaction.user}")
            with db() as con:
                con.execute("UPDATE tickets SET status='OPEN',closed_at='',closed_by='',close_reason='' WHERE id=?", (row["id"],))
                con.execute("DELETE FROM ticket_transcripts WHERE ticket_id=?", (row["id"],))
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Ticket **#{row['id']}** reaberto. O autor voltou a poder enviar mensagens."))
            await ticket_send_log(interaction.guild, f"Ticket **#{row['id']}** reaberto por <@{interaction.user.id}>.")
        except discord.Forbidden:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Não tenho permissão para reabrir este canal. Confira Gerenciar canais e permissões."))
        except discord.HTTPException:
            log.exception("Falha ao reabrir ticket #%s", row["id"])
            if not interaction.response.is_done():
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "O Discord recusou a reabertura. Tente novamente."))


class TicketOpenModal(ComponentsV2Modal, title="Abrir atendimento"):
    assunto = discord.ui.TextInput(label="Assunto", placeholder="Resuma o motivo do atendimento", max_length=100, required=True)
    alvo = discord.ui.TextInput(label="Pessoa/ID envolvido (se houver)", placeholder="Opcional; informe na denúncia se souber", max_length=100, required=False)
    descricao = discord.ui.TextInput(label="Descreva o que aconteceu", placeholder="Explique com detalhes. Você poderá enviar imagens e arquivos no canal criado.", style=discord.TextStyle.paragraph, max_length=1800, required=True)

    def __init__(self, ticket_type: str):
        self.ticket_type = ticket_type if ticket_type in TICKET_TYPES else "outros"
        super().__init__()

    async def on_submit(self, interaction: discord.Interaction):
        await create_ticket_from_modal(interaction, self.ticket_type, self.assunto.value, self.alvo.value, self.descricao.value)


class TicketCloseModal(ComponentsV2Modal, title="Encerrar ticket"):
    motivo = discord.ui.TextInput(label="Motivo do encerramento (opcional)", placeholder="Informe um resumo ou deixe em branco", required=False, max_length=300, style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.channel:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Não consegui identificar o ticket."))
            return
        with db() as con:
            row = con.execute("SELECT * FROM tickets WHERE channel_id=?", (str(interaction.channel.id),)).fetchone()
        if not row:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Ticket não encontrado."))
            return
        is_owner = str(interaction.user.id) == str(row["user_id"])
        is_staff = isinstance(interaction.user, discord.Member) and ticket_is_staff(interaction.user, row["ticket_type"])
        if not (is_owner or is_staff):
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você não tem permissão para encerrar este ticket."))
            return
        await close_ticket(interaction, row, (self.motivo.value or "").strip())


async def create_ticket_from_modal(interaction: discord.Interaction, ticket_type: str, subject: str, target: str, description: str):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Abra o ticket dentro do servidor."))
        return
    guild = interaction.guild
    issue = ticket_config_issue(guild)
    if issue:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, issue))
        return
    limit = ticket_user_limit(setting("ticket_user_limit", "2"))
    with db() as con:
        active_count = con.execute(
            "SELECT COUNT(*) FROM tickets WHERE guild_id=? AND user_id=? AND status IN ('OPEN','CLAIMED')",
            (str(guild.id), str(interaction.user.id)),
        ).fetchone()[0]
    if active_count >= limit:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Você já possui **{active_count}** ticket(s) aberto(s). O limite atual é **{limit}**. Aguarde o encerramento de um deles."))
        return
    data = ticket_type_data(ticket_type)
    category = guild.get_channel(int(setting(data["category_key"], "0")))
    staff_role = guild.get_role(int(setting(data["role_key"], "0")))
    if not isinstance(category, discord.CategoryChannel) or not staff_role:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "A categoria ou o cargo da equipe foi removido. Avise a liderança para corrigir `/config` → Tickets."))
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    safe_user = re.sub(r"[^a-z0-9-]", "-", interaction.user.name.casefold()).strip("-")[:18] or "membro"
    channel_name = f"{ticket_type}-{safe_user}"[:95]
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True, add_reactions=True),
        staff_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
    }
    me = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
    if me:
        overwrites[me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_messages=True, attach_files=True, embed_links=True)
    channel = None
    ticket_id = None
    try:
        channel = await guild.create_text_channel(
            name=channel_name, category=category, overwrites=overwrites,
            topic=f"Ticket {data['label']} de {interaction.user} ({interaction.user.id}) · assunto: {subject[:150]}",
            reason=f"Abertura de ticket por {interaction.user} ({interaction.user.id})",
        )
        with db() as con:
            cursor = con.execute(
                "INSERT INTO tickets(guild_id,channel_id,user_id,ticket_type,subject,description,target_name,status,created_at) VALUES(?,?,?,?,?,?,?,'OPEN',?)",
                (str(guild.id), str(channel.id), str(interaction.user.id), ticket_type, subject.strip(), description.strip(), target.strip(), now_iso()),
            )
            ticket_id = int(cursor.lastrowid)
        intro = (
            f"# {data['emoji']} TICKET {data['label'].upper()} · #{ticket_id}\n"
            f"**Autor:** <@{interaction.user.id}>\n**Equipe:** {staff_role.mention}\n"
            f"**Assunto:** {subject.strip()}\n"
            + (f"**Pessoa/ID citado:** {target.strip()}\n" if target.strip() else "")
            + f"\n**Descrição inicial**\n{description.strip()}\n\n"
            "Envie aqui as provas, imagens, vídeos ou arquivos necessários. Somente você e a equipe responsável conseguem visualizar este canal."
        )
        await channel.send(view=TicketControlsView(intro))
        await ticket_send_log(guild, f"🎫 Ticket **#{ticket_id}** aberto: **{data['label']}** por <@{interaction.user.id}> em {channel.mention}. Assunto: **{subject.strip()}**")
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, f"✅ Seu ticket **#{ticket_id}** foi criado: {channel.mention}. A equipe responsável poderá responder por lá."))
    except (discord.Forbidden, discord.HTTPException, sqlite3.Error):
        log.exception("Falha ao criar ticket para %s", interaction.user.id)
        if ticket_id:
            try:
                with db() as con:
                    con.execute("DELETE FROM ticket_transcripts WHERE ticket_id=?", (ticket_id,))
                    con.execute("DELETE FROM tickets WHERE id=?", (ticket_id,))
            except sqlite3.Error:
                log.exception("Falha ao limpar o registro parcial do ticket #%s", ticket_id)
        if channel:
            try:
                await channel.delete(reason="Falha ao finalizar a criação do ticket")
            except discord.HTTPException:
                pass
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, "Não foi possível criar o ticket. Confira as permissões do bot e tente novamente; a equipe foi avisada."))


async def ticket_send_log(guild: discord.Guild, text: str, file: Optional[discord.File] = None) -> bool:
    try:
        log_channel = guild.get_channel(int(setting("ticket_log_channel_id", "0") or 0))
    except (TypeError, ValueError):
        log_channel = None
    if not isinstance(log_channel, discord.TextChannel):
        if file:
            file.close()
        log.warning("Canal de logs dos tickets não encontrado. Evento: %s", text)
        return False
    try:
        message_view = v2_view(None, None, text)
        if file is None:
            await log_channel.send(view=message_view)
        else:
            await log_channel.send(file=file, view=message_view)
        return True
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Não foi possível enviar log de ticket para o canal %s", log_channel.id)
        return False


async def render_ticket_transcript(channel: discord.TextChannel, row, reason: str) -> bytes:
    items = []
    async for message in channel.history(limit=800, oldest_first=True):
        author = html_escape.escape(getattr(message.author, "display_name", str(message.author)))
        stamp = message.created_at.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC")
        content = html_escape.escape(message.content or "").replace("\n", "<br>")
        attachments = []
        for attachment in message.attachments:
            url = html_escape.escape(attachment.url, quote=True)
            filename = html_escape.escape(attachment.filename)
            attachments.append(f'<div class="attachment">📎 <a href="{url}">{filename}</a></div>')
        items.append(
            f'<article><header><b>{author}</b><time>{stamp}</time></header>'
            f'<div class="message">{content or "<i>(sem texto)</i>"}</div>{"".join(attachments)}</article>'
        )
    details = (
        f"Ticket #{row['id']} · {html_escape.escape(ticket_type_data(row['ticket_type'])['label'])}<br>"
        f"Autor: {html_escape.escape(str(row['user_id']))}<br>"
        f"Assunto: {html_escape.escape(str(row['subject']))}<br>"
        f"Encerrado por: {html_escape.escape(str(row['closed_by'] or ''))}<br>"
        f"Motivo: {html_escape.escape(reason or 'Não informado')}"
    )
    body = "".join(items) or "<p>Nenhuma mensagem foi registrada.</p>"
    max_bytes = 7 * 1024 * 1024
    style = (
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        f"<title>Transcript ticket #{row['id']}</title><style>"
        "body{margin:0;background:#101116;color:#e7e8ee;font:14px Arial,sans-serif}"
        ".wrap{max-width:1000px;margin:32px auto;padding:24px;background:#191b23;border-radius:14px}"
        "h1{margin:0 0 12px;color:#fff}.meta{padding:14px;background:#222530;border-radius:8px;color:#c4c8d5}"
        "article{padding:14px 4px;border-bottom:1px solid #30333e}header{display:flex;gap:18px;justify-content:space-between}"
        "time{color:#969cac;font-size:12px}.message{margin-top:9px;line-height:1.5;overflow-wrap:anywhere}"
        ".attachment{margin-top:8px}.attachment a{color:#8bb8ff}footer{margin-top:22px;color:#8d93a1;font-size:12px}"
        "</style></head><body><main class='wrap'><h1>Transcript de atendimento</h1>"
        f"<div class='meta'>{details}</div>"
    )
    max_bytes = 7 * 1024 * 1024
    omitted = False
    while True:
        body = ("<p>As mensagens mais antigas foram omitidas para manter o arquivo dentro do limite.</p>" if omitted else "") + "".join(items)
        document = style + body + "<footer>Gerado pelo GERENTE-DE-FAC · conteúdo privado do atendimento.</footer></main></body></html>"
        encoded = document.encode("utf-8")
        if len(encoded) <= max_bytes or not items:
            return encoded[:max_bytes]
        items.pop(0)
        omitted = True


async def close_ticket(interaction: discord.Interaction, row, reason: str):
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel) or not interaction.guild:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Este ticket não está em um canal de texto válido."))
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    closer_id = str(interaction.user.id)
    try:
        html_data = await render_ticket_transcript(channel, row, reason)
        filename = f"ticket-{int(row['id']):05d}-transcript.html"
        with db() as con:
            con.execute(
                "UPDATE tickets SET status='CLOSED',closed_at=?,closed_by=?,close_reason=? WHERE id=?",
                (now_iso(), closer_id, reason, row["id"]),
            )
            con.execute(
                "INSERT INTO ticket_transcripts(ticket_id,file_name,html_blob,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(ticket_id) DO UPDATE SET file_name=excluded.file_name,html_blob=excluded.html_blob,created_at=excluded.created_at",
                (row["id"], filename, sqlite3.Binary(html_data), now_iso()),
            )
        owner = interaction.guild.get_member(int(row["user_id"]))
        if owner:
            try:
                await channel.set_permissions(owner, view_channel=True, send_messages=False, read_message_history=True, attach_files=False)
            except discord.HTTPException:
                log.warning("Não consegui bloquear o autor do ticket #%s", row["id"])
        if not channel.name.startswith("fechado-"):
            try:
                await channel.edit(name=f"fechado-{channel.name}"[:100], reason=f"Ticket #{row['id']} fechado")
            except discord.HTTPException:
                log.warning("Não consegui renomear o ticket #%s", row["id"])
        file_for_log = discord.File(io.BytesIO(html_data), filename=filename)
        log_text = (
            f"🔒 **Ticket #{row['id']} encerrado** · Tipo: **{ticket_type_data(row['ticket_type'])['label']}**\n"
            f"**Autor:** <@{row['user_id']}> · **Fechado por:** <@{closer_id}>\n"
            f"**Canal:** #{channel.name} · **Motivo:** {reason or 'Não informado'}\n"
            "O arquivo HTML anexado é a cópia administrativa do transcript."
        )
        log_sent = await ticket_send_log(interaction.guild, log_text, file_for_log)
        dm_sent = False
        if owner:
            try:
                await owner.send(file=discord.File(io.BytesIO(html_data), filename=filename), view=v2_view(None, None, f"O ticket **#{row['id']}** foi encerrado. Este é o seu transcript privado."))
                dm_sent = True
            except (discord.Forbidden, discord.HTTPException):
                dm_sent = False
        result = f"✅ Ticket **#{row['id']}** encerrado. Transcript salvo no banco de dados.\n"
        result += "✅ Transcript enviado ao canal administrativo." if log_sent else "⚠️ Não consegui enviar o transcript ao canal de logs; ele continua salvo no banco."
        result += "\n✅ Cópia privada enviada por DM." if dm_sent else "\nℹ️ Não foi possível enviar DM ao autor. Ele poderá usar `/ticket-transcript` para baixar a própria cópia."
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, result))
        await ticket_send_log(interaction.guild, f"Registro: ticket **#{row['id']}** encerrado em {channel.mention}.")
    except (discord.Forbidden, discord.HTTPException, sqlite3.Error):
        log.exception("Falha ao encerrar ticket #%s", row["id"])
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, "Houve uma falha ao gerar o transcript ou atualizar o ticket. O registro foi mantido; avise a liderança antes de tentar novamente."))


class TicketConfigV2(ComponentsV2View):
    """Assistente de configuração do ticket, sem digitar IDs manualmente."""
    def __init__(self, guild: discord.Guild, owner_id: int = 0):
        super().__init__(timeout=900)
        self.guild = guild
        self.owner_id = owner_id
        self.set_text(
            "# 🎫 CONFIGURAÇÃO DO SISTEMA DE TICKETS\n"
            "Configure os canais, categorias e cargos responsáveis. Depois publique o painel uma única vez.\n\n"
            + ticket_config_summary(guild)
            + "\n\n**Ordem recomendada:** 1) canal do painel; 2) canal privado de logs; 3) categoria e equipe de cada tipo; 4) publicar.\n"
            "⚠️ Deixe o canal de logs visível somente à liderança e à equipe autorizada."
        )
        actions = [
            ("Canal do painel", "📨", self._open_text_channel("ticket_panel_channel_id", "Canal do painel"), 0),
            ("Canal de logs", "🧾", self._open_text_channel("ticket_log_channel_id", "Canal privado de logs e transcripts"), 0),
            ("Suporte", "🛠️", self._open_category_config("support"), 1),
            ("Denúncia", "🚨", self._open_category_config("denuncia"), 1),
            ("Outros", "📌", self._open_category_config("outros"), 1),
            ("Publicar painel", "🚀", self._publish, 2),
            ("Voltar ao painel principal", "↩️", self._back, 2),
        ]
        for label, emoji, callback, row in actions:
            button = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.success if label == "Publicar painel" else discord.ButtonStyle.secondary, row=row)
            button.callback = callback
            self.add_item(button)

    def _open_text_channel(self, key: str, label: str):
        async def callback(interaction: discord.Interaction):
            view = TicketTextChannelSelectV2(self.guild, key, label, self.owner_id or interaction.user.id)
            await interaction.response.edit_message(view=view, content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))
        return callback

    def _open_category_config(self, ticket_type: str):
        async def callback(interaction: discord.Interaction):
            view = TicketCategorySelectV2(self.guild, ticket_type, self.owner_id or interaction.user.id)
            await interaction.response.edit_message(view=view, content=None, embeds=[], attachments=(interaction.message.attachments if interaction.message else []))
        return callback

    async def _publish(self, interaction: discord.Interaction):
        if self.owner_id and interaction.user.id != self.owner_id:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Somente quem abriu esta configuração pode alterá-la."))
            return
        issue = ticket_config_issue(self.guild)
        if issue:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, issue))
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, message = await publish_ticket_panel(self.guild)
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, message))

    async def _back(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=ConfigPanelV2(include_banner=False), content=None, embeds=[], attachments=[])


class TicketTextChannelSelectV2(PaginatedGuildObjectPickerView):
    def __init__(self, guild: discord.Guild, key: str, label: str, owner_id: int):
        async def confirm(picker_interaction, channel):
            save_config_value(key, str(channel.id))
            await picker_interaction.response.edit_message(
                view=TicketConfigV2(guild, owner_id), content=None, embeds=[], attachments=[],
            )
        async def back(picker_interaction):
            await picker_interaction.response.edit_message(
                view=TicketConfigV2(guild, owner_id), content=None, embeds=[], attachments=[],
            )
        raw = panel_setting(key, "0")
        try:
            selected_id = int(raw or 0)
        except (TypeError, ValueError):
            selected_id = 0
        super().__init__(
            guild, kind="text_channel", title=label,
            owner_id=owner_id, on_confirm=confirm, on_back=back,
            selected_id=selected_id, confirm_label="Salvar canal",
        )


class TicketCategorySelectV2(ComponentsV2View):
    """Configuração de categoria/equipe com paginadores separados de 25 itens."""
    def __init__(self, guild: discord.Guild, ticket_type: str, owner_id: int):
        super().__init__(timeout=900)
        self.guild, self.ticket_type, self.owner_id = guild, ticket_type, owner_id
        self.data = ticket_type_data(ticket_type)
        try:
            self.selected_category_id = int(panel_setting(self.data["category_key"], "0") or 0)
        except (TypeError, ValueError):
            self.selected_category_id = 0
        try:
            self.selected_role_id = int(panel_setting(self.data["role_key"], "0") or 0)
        except (TypeError, ValueError):
            self.selected_role_id = 0
        self._refresh_selection_text()

        category_button = discord.ui.Button(
            label="Selecionar categoria", emoji="📁", style=discord.ButtonStyle.secondary, row=0,
        )
        role_button = discord.ui.Button(
            label="Selecionar cargo da equipe", emoji="🎭", style=discord.ButtonStyle.secondary, row=1,
        )
        save = discord.ui.Button(label="Salvar configuração", emoji="💾", style=discord.ButtonStyle.success, row=2)
        back = discord.ui.Button(label="Voltar", emoji="⬅️", style=discord.ButtonStyle.secondary, row=2)
        category_button.callback = self._open_category_picker
        role_button.callback = self._open_role_picker
        save.callback = self._save
        back.callback = self._back
        self.add_item(category_button)
        self.add_item(role_button)
        self.add_item(save)
        self.add_item(back)

    def _refresh_selection_text(self):
        category = self.guild.get_channel(self.selected_category_id) if self.selected_category_id else None
        role = self.guild.get_role(self.selected_role_id) if self.selected_role_id else None
        category_text = category.name if isinstance(category, discord.CategoryChannel) else "Ainda não selecionada"
        role_text = role.mention if role else "Ainda não selecionado"
        self.set_text(
            f"# {self.data['emoji']} CONFIGURAR {self.data['label'].upper()}\n"
            "Escolha os dois campos. Em cada seletor você terá setas para percorrer todas as opções do servidor.\n\n"
            f"**Categoria:** {category_text}\n**Equipe responsável:** {role_text}\n\n"
            "Depois de selecionar os dois, clique em **Salvar configuração**."
        )

    async def _open_category_picker(self, interaction: discord.Interaction):
        async def confirm(picker_interaction, category):
            self.selected_category_id = category.id
            self._refresh_selection_text()
            await picker_interaction.response.edit_message(view=self)
        async def back(picker_interaction):
            await picker_interaction.response.edit_message(view=self)
        picker = PaginatedGuildObjectPickerView(
            self.guild, kind="category", title=f"Categoria · {self.data['label']}",
            owner_id=self.owner_id, on_confirm=confirm, on_back=back,
            selected_id=self.selected_category_id, confirm_label="Usar esta categoria",
        )
        await interaction.response.edit_message(view=picker, content=None, embeds=[], attachments=[])

    async def _open_role_picker(self, interaction: discord.Interaction):
        async def confirm(picker_interaction, role):
            self.selected_role_id = role.id
            self._refresh_selection_text()
            await picker_interaction.response.edit_message(view=self)
        async def back(picker_interaction):
            await picker_interaction.response.edit_message(view=self)
        picker = PaginatedGuildObjectPickerView(
            self.guild, kind="role", title=f"Cargo da equipe · {self.data['label']}",
            owner_id=self.owner_id, on_confirm=confirm, on_back=back,
            selected_id=self.selected_role_id, confirm_label="Usar este cargo",
        )
        await interaction.response.edit_message(view=picker, content=None, embeds=[], attachments=[])

    async def _save(self, interaction: discord.Interaction):
        if not self.selected_category_id or not self.selected_role_id:
            await interaction.response.send_message(
                None, ephemeral=True,
                view=v2_view(None, None, "Selecione a categoria e o cargo da equipe antes de salvar."),
            )
            return
        category = self.guild.get_channel(self.selected_category_id)
        role = self.guild.get_role(self.selected_role_id)
        if not isinstance(category, discord.CategoryChannel) or not role or role.is_default():
            await interaction.response.send_message(
                None, ephemeral=True,
                view=v2_view(None, None, "A seleção não é mais válida. Selecione novamente."),
            )
            return
        save_config_value(self.data["category_key"], str(category.id))
        save_config_value(self.data["role_key"], str(role.id))
        await interaction.response.edit_message(
            view=TicketConfigV2(self.guild, self.owner_id), content=None, embeds=[], attachments=[],
        )

    async def _back(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            view=TicketConfigV2(self.guild, self.owner_id), content=None, embeds=[], attachments=[],
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Somente quem iniciou esta configuração pode alterá-la."))
            return False
        return True


async def publish_ticket_panel(guild: discord.Guild) -> tuple[bool, str]:
    issue = ticket_config_issue(guild)
    if issue:
        return False, issue
    channel = guild.get_channel(int(setting("ticket_panel_channel_id", "0")))
    try:
        existing_id = int(setting("ticket_panel_message_id", "0") or 0)
        previous_channel_id = int(setting("ticket_panel_published_channel_id", str(channel.id)) or channel.id)
    except (TypeError, ValueError):
        existing_id = 0
        previous_channel_id = 0
    try:
        if existing_id and previous_channel_id == channel.id:
            try:
                message = await channel.fetch_message(existing_id)
                await message.edit(view=TicketPanelView())
                return True, f"✅ Painel de tickets atualizado em {channel.mention}. Os botões antigos foram substituídos."
            except discord.NotFound:
                pass
        elif existing_id and previous_channel_id and previous_channel_id != channel.id:
            previous_channel = guild.get_channel(previous_channel_id)
            if isinstance(previous_channel, discord.TextChannel):
                try:
                    previous_message = await previous_channel.fetch_message(existing_id)
                    await previous_message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    log.warning("O painel antigo de tickets não pôde ser removido do canal %s", previous_channel_id)
        message = await channel.send(view=TicketPanelView())
        save_config_value("ticket_panel_message_id", str(message.id))
        save_config_value("ticket_panel_published_channel_id", str(channel.id))
        return True, f"✅ Painel de tickets publicado em {channel.mention}."
    except discord.Forbidden:
        return False, "O bot não tem permissão para enviar mensagens e visualizar o canal do painel."
    except discord.HTTPException:
        log.exception("Falha ao publicar painel de tickets")
        return False, "O Discord recusou a publicação do painel. Verifique as permissões e tente novamente."


@bot.tree.command(name="painel-tickets", description="Publica ou atualiza o painel de Suporte, Denúncia e Outros")
async def painel_tickets(interaction: discord.Interaction):
    if not await deny_unless_leader(interaction):
        return
    if not interaction.guild:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use no servidor.")); return
    await interaction.response.defer(ephemeral=True, thinking=True)
    _ok, message = await publish_ticket_panel(interaction.guild)
    await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, message))


@bot.tree.command(name="ticket-transcript", description="Baixa o transcript de um ticket que você abriu ou que sua equipe atende")
@app_commands.describe(numero="Número do ticket (ex.: 15)")
async def ticket_transcript(interaction: discord.Interaction, numero: app_commands.Range[int, 1, 999999999]):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Use este comando dentro do servidor.")); return
    with db() as con:
        row = con.execute(
            "SELECT t.*,tr.file_name,tr.html_blob FROM tickets t JOIN ticket_transcripts tr ON tr.ticket_id=t.id WHERE t.id=? AND t.guild_id=?",
            (str(numero), str(interaction.guild.id)),
        ).fetchone()
    if not row:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Não encontrei transcript salvo para esse número de ticket.")); return
    is_owner = str(interaction.user.id) == str(row["user_id"])
    is_staff = ticket_is_staff(interaction.user, row["ticket_type"])
    if not (is_owner or is_staff):
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Acesso negado. Somente o autor do ticket ou a equipe responsável pode baixar esse transcript.")); return
    transcript = bytes(row["html_blob"])
    await interaction.response.send_message(
        content=None,
        view=v2_view(None, None, f"Transcript privado do ticket **#{row['id']}**. Somente você recebeu este arquivo."),
        file=discord.File(io.BytesIO(transcript), filename=row["file_name"]),
        ephemeral=True,
    )


@bot.tree.command(name="config", description="Abre o painel interativo de configuração da facção")
async def config(interaction: discord.Interaction) -> None:
    if not await deny_unless_config_access(interaction):
        return
    image_path = os.path.join(os.path.dirname(__file__), "assets", "painel_configuracao.png")
    panel_image = discord.File(image_path, filename="painel_configuracao.png")
    await interaction.response.send_message(view=ConfigPanelV2(), file=panel_image, ephemeral=True)


@bot.tree.command(name="painel-wl", description="Publica o painel WL no canal configurado")
async def painel_wl(interaction: discord.Interaction) -> None:
    if not await deny_unless_leader(interaction): return
    await publish_configured_panel(interaction, "wl")

@bot.tree.command(name="painel-farm", description="Publica o painel de farm no canal configurado")
async def painel_farm(interaction: discord.Interaction) -> None:
    if not await deny_unless_leader(interaction): return
    await publish_configured_panel(interaction, "farm")

@bot.tree.command(name="painel-hierarquia", description="Publica a hierarquia nos cargos e canal configurados")
async def painel_hierarquia(interaction: discord.Interaction) -> None:
    if not await deny_unless_leader(interaction): return
    await publish_configured_panel(interaction, "hierarchy")

@bot.tree.command(name="painel-membros", description="Publica a lista de membros ativos no canal configurado")
async def painel_membros(interaction: discord.Interaction) -> None:
    if not await deny_unless_leader(interaction): return
    await publish_configured_panel(interaction, "members")

@bot.tree.command(name="painel-lideranca", description="Publica o painel da liderança no canal configurado")
async def painel_lideranca(interaction: discord.Interaction) -> None:
    if not await deny_unless_leader(interaction): return
    await publish_configured_panel(interaction, "leader")

@bot.tree.command(name="painel", description="Publica o painel principal no canal configurado")
async def painel(interaction: discord.Interaction) -> None:
    if not await deny_unless_leader(interaction): return
    await publish_configured_panel(interaction, "main")

@bot.tree.command(name="perfil", description="Consulta seu perfil ou o de um membro")
@app_commands.describe(membro="Membro que deseja consultar; vazio para seu próprio perfil")
async def perfil(interaction: discord.Interaction, membro: Optional[discord.Member] = None) -> None:
    target = membro or interaction.user
    with db() as con:
        row = con.execute("SELECT * FROM members WHERE discord_id=?", (str(target.id),)).fetchone()
        total = con.execute("SELECT COALESCE(SUM(points),0) AS pts, COALESCE(SUM(quantity),0) AS qty FROM farm_entries WHERE discord_id=?", (str(target.id),)).fetchone()
        warnings = con.execute("SELECT COUNT(*) AS n FROM warnings WHERE discord_id=?", (str(target.id),)).fetchone()["n"]
    if not row:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse usuário ainda não possui cadastro aprovado."))
        return
    embed = PanelMessage(title=f"👤 Perfil de {row['character_name']}", color=discord.Color.blurple())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Discord", value=target.mention, inline=True)
    embed.add_field(name="Passaporte", value=row["game_id"], inline=True)
    embed.add_field(name="Hierarquia registrada", value=row["rank_name"], inline=True)
    embed.add_field(name="Farm total", value=f"{total['qty']} unidades · {total['pts']} pontos", inline=True)
    embed.add_field(name="Advertências registradas", value=str(warnings), inline=True)
    embed.add_field(name="Entrada", value=row["joined_at"][:10], inline=True)
    await interaction.response.send_message(view=v2_view(embed, None, None), ephemeral=True)

@bot.tree.command(name="membros", description="Lista os membros ativos cadastrados")
async def membros(interaction: discord.Interaction) -> None:
    with db() as con:
        rows = con.execute("SELECT character_name,game_id,rank_name,discord_id FROM members WHERE status='ACTIVE' ORDER BY character_name COLLATE NOCASE LIMIT 40").fetchall()
    if not rows:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Ainda não há membros cadastrados."))
        return
    lines = [f"• **{r['character_name']} | {r['game_id']}** — {r['rank_name']} — <@{r['discord_id']}>" for r in rows]
    embed = PanelMessage(title=f"👥 Membros ativos ({len(rows)})", description="\n".join(lines), color=discord.Color.blurple())
    await interaction.response.send_message(view=v2_view(embed, None, None), ephemeral=True)

@bot.tree.command(name="promover", description="Adiciona um cargo e registra a promoção")
@app_commands.describe(membro="Membro a promover", cargo="Cargo que será adicionado", motivo="Motivo da promoção")
async def promover(interaction: discord.Interaction, membro: discord.Member, cargo: discord.Role, motivo: str = "Reconhecimento") -> None:
    if not await deny_unless_leader(interaction): return
    if interaction.user.id != interaction.guild.owner_id and cargo >= interaction.user.top_role:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você não pode atribuir um cargo igual ou superior ao seu.")); return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await membro.add_roles(cargo, reason=f"Promoção por {interaction.user}: {motivo}")
        with db() as con:
            con.execute("UPDATE members SET rank_name=? WHERE discord_id=?", (cargo.name, str(membro.id)))
        log_action(interaction.user.id, "PROMOCAO", membro.id, motivo, f"Cargo adicionado: {cargo.name}")
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, f"✅ {membro.mention} recebeu o cargo **{cargo.name}**."))
        await send_channel_log(interaction.guild, "📈 Promoção registrada", f"Liderança: {interaction.user.mention}\nMembro: {membro.mention}\nCargo: {cargo.mention}\nMotivo: {motivo}", color=discord.Color.green())
        await send_channel_log(interaction.guild, "🎉 Nova promoção", f"{membro.mention} foi promovido para {cargo.mention}.", public=True, color=discord.Color.green())
    except discord.Forbidden:
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, "Não tenho permissão para atribuir esse cargo. Confira a hierarquia de cargos do bot."))

@bot.tree.command(name="rebaixar", description="Remove um cargo e opcionalmente atribui outro")
@app_commands.describe(membro="Membro a rebaixar", cargo_atual="Cargo que será removido", novo_cargo="Novo cargo", motivo="Motivo do rebaixamento")
async def rebaixar(interaction: discord.Interaction, membro: discord.Member, cargo_atual: discord.Role, novo_cargo: discord.Role, motivo: str = "Reajuste hierárquico") -> None:
    if not await deny_unless_leader(interaction): return
    if interaction.user.id != interaction.guild.owner_id and membro.top_role >= interaction.user.top_role:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você não pode gerenciar alguém com cargo igual ou superior ao seu.")); return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await membro.remove_roles(cargo_atual, reason=f"Rebaixamento por {interaction.user}: {motivo}")
        await membro.add_roles(novo_cargo, reason=f"Rebaixamento por {interaction.user}: {motivo}")
        with db() as con:
            con.execute("UPDATE members SET rank_name=? WHERE discord_id=?", (novo_cargo.name, str(membro.id)))
        log_action(interaction.user.id, "REBAIXAMENTO", membro.id, motivo, f"{cargo_atual.name} -> {novo_cargo.name}")
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, f"✅ {membro.mention} agora possui o cargo **{novo_cargo.name}**."))
        await send_channel_log(interaction.guild, "📉 Rebaixamento registrado", f"Liderança: {interaction.user.mention}\nMembro: {membro.mention}\nCargo removido: {cargo_atual.mention}\nNovo cargo: {novo_cargo.mention}\nMotivo: {motivo}", color=discord.Color.orange())
        await send_channel_log(interaction.guild, "🔻 Atualização de hierarquia", f"O cargo de {membro.mention} foi atualizado para {novo_cargo.mention}.", public=True, color=discord.Color.orange())
    except discord.Forbidden:
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, "Não tenho permissão para alterar esses cargos."))

@bot.tree.command(name="farm-item", description="Cria ou atualiza um item de farm (liderança)")
@app_commands.describe(nome="Nome do item", pontos="Pontos por unidade", meta="Meta em unidades para o ciclo")
async def farm_item(interaction: discord.Interaction, nome: str, pontos: app_commands.Range[int, 1, 100000], meta: app_commands.Range[int, 0, 1000000] = 0) -> None:
    if not await deny_unless_leader(interaction): return
    name = nome.strip()[:60]
    with db() as con:
        con.execute("INSERT INTO farm_items(name,points,target,active) VALUES(?,?,?,1) ON CONFLICT(name) DO UPDATE SET points=excluded.points,target=excluded.target,active=1", (name, pontos, meta))
    log_action(interaction.user.id, "FARM_ITEM_CONFIG", None, "", f"{name}: {pontos} ponto(s), meta {meta}")
    await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Item **{name}** configurado: **{pontos}** ponto(s) por unidade; meta: **{meta}**."))

@bot.tree.command(name="farm-preco", description="Define o valor unitário em reais de um item de farm")
@app_commands.describe(nome="Nome exato do item cadastrado", valor="Valor em reais por unidade, por exemplo 2.50")
async def farm_preco(interaction: discord.Interaction, nome: str, valor: app_commands.Range[float, 0.01, 1000000.0]) -> None:
    if not await deny_unless_leader(interaction):
        return
    cents = int(round(float(valor) * 100))
    with db() as con:
        cur = con.execute("UPDATE farm_items SET unit_price_cents=? WHERE name=? COLLATE NOCASE AND active=1", (cents, nome.strip()))
    if not cur.rowcount:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Não encontrei esse item ativo. Cadastre primeiro com `/farm-item`."))
        return
    log_action(interaction.user.id, "FARM_PRECO_CONFIG", None, "", f"{nome.strip()}: R$ {cents/100:.2f} por unidade")
    await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Valor de **{nome.strip()}** atualizado para **R$ {cents/100:.2f} por unidade**."))

@bot.tree.command(name="farm-item-desativar", description="Desativa um item de farm")
async def farm_item_desativar(interaction: discord.Interaction, nome: str) -> None:
    if not await deny_unless_leader(interaction): return
    with db() as con:
        cur = con.execute("UPDATE farm_items SET active=0 WHERE name=? COLLATE NOCASE", (nome.strip(),))
    if cur.rowcount == 0:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Não encontrei esse item de farm.")); return
    log_action(interaction.user.id, "FARM_ITEM_DESATIVADO", None, "", nome)
    await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Item **{nome}** desativado."))

@bot.tree.command(name="farm", description="Informa como registrar farm pelo painel com comprovante")
async def farm(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        None,
        ephemeral=True, view=v2_view(None, None, "Para manter a auditoria, o registro agora deve ser feito pelo **painel de Farm**: clique em **REGISTRAR FARM**, selecione o item, informe a quantidade e envie a imagem de prova no canal. Registros sem comprovante não são contabilizados."))

@bot.tree.command(name="farm-comprovante", description="Consulta um comprovante de farm salvo no banco")
@app_commands.describe(registro="Número do registro de comprovante")
async def farm_comprovante(interaction: discord.Interaction, registro: app_commands.Range[int, 1, 100000000]) -> None:
    if not await deny_unless_leader(interaction): return
    with db() as con:
        row = con.execute("SELECT * FROM farm_proofs WHERE id=?", (registro,)).fetchone()
    if not row:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Não encontrei esse comprovante.")); return
    import io
    file = discord.File(io.BytesIO(row["image_data"]), filename=row["image_filename"] or f"comprovante-{registro}.png")
    embed = PanelMessage(title=f"🌾 Comprovante de farm #{registro}", description=f"Membro: <@{row['discord_id']}>\nNome RP: **{row['character_name']}**\nID FiveM: **{row['game_id']}**\nFarm: **{row['item_name'] or 'Não informado'}**\nQuantidade: **{row['quantity'] or 0}**\nPontos: **{row['points'] or 0}**\nStatus: **{row['status']}**\nData: {row['created_at']}", color=discord.Color.green() if row['status'] == 'APPROVED' else discord.Color.gold())
    await interaction.response.send_message(view=v2_view(embed, None, None), file=file, ephemeral=True)

@bot.tree.command(name="farm-resumo", description="Mostra o resumo de farm do ciclo atual")
@app_commands.describe(membro="Membro para consultar; vazio para você")
async def farm_resumo(interaction: discord.Interaction, membro: Optional[discord.Member] = None) -> None:
    target = membro or interaction.user
    with db() as con:
        rows = con.execute("SELECT item_name,SUM(quantity) qty,SUM(points) pts FROM farm_entries WHERE discord_id=? GROUP BY item_name ORDER BY pts DESC", (str(target.id),)).fetchall()
        total = con.execute("SELECT COALESCE(SUM(quantity),0) qty,COALESCE(SUM(points),0) pts FROM farm_entries WHERE discord_id=?", (str(target.id),)).fetchone()
    if not rows:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Ainda não há registros de farm para esse membro.")); return
    embed = PanelMessage(title=f"📊 Resumo de farm — {target.display_name}", color=discord.Color.green())
    embed.description = "\n".join(f"• **{r['item_name']}**: {r['qty']} unidade(s) · {r['pts']} ponto(s)" for r in rows[:20])
    embed.add_field(name="Total", value=f"{total['qty']} unidades · {total['pts']} pontos", inline=False)
    await interaction.response.send_message(view=v2_view(embed, None, None), ephemeral=True)

@bot.tree.command(name="farm-ranking", description="Mostra o ranking de farm")
async def farm_ranking(interaction: discord.Interaction) -> None:
    with db() as con:
        rows = con.execute("SELECT discord_id,SUM(points) pts,SUM(quantity) qty FROM farm_entries GROUP BY discord_id ORDER BY pts DESC LIMIT 15").fetchall()
    if not rows:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Ainda não há registros de farm.")); return
    lines = [f"**{i}.** <@{r['discord_id']}> — **{r['pts']} pts** ({r['qty']} unidades)" for i, r in enumerate(rows, 1)]
    await interaction.response.send_message(view=v2_view(PanelMessage(title="🏆 Ranking de farm", description="\n".join(lines), color=discord.Color.gold()), None, None), ephemeral=True)

@bot.tree.command(name="meta-farm", description="Define a meta global de farm em unidades")
async def meta_farm(interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 1000000]) -> None:
    if not await deny_unless_leader(interaction): return
    with db() as con:
        con.execute("INSERT INTO settings(key,value) VALUES('global_farm_goal',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(quantidade),))
    log_action(interaction.user.id, "META_FARM_CONFIG", None, "", str(quantidade))
    await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Meta global de farm definida para **{quantidade} unidades**."))

@bot.tree.command(name="farm-meta-status", description="Compara o farm total com a meta global")
async def farm_meta_status(interaction: discord.Interaction) -> None:
    goal = int(setting("global_farm_goal", "0"))
    with db() as con:
        total = con.execute("SELECT COALESCE(SUM(quantity),0) qty,COALESCE(SUM(points),0) pts FROM farm_entries").fetchone()
    if goal <= 0:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "A liderança ainda não configurou uma meta global com `/meta-farm`.")); return
    percent = min(100, round(total["qty"] / goal * 100))
    filled = int(percent / 10)
    bar = "🟩" * filled + "⬜" * (10-filled)
    await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"**Meta de farm da facção**\n{bar} **{percent}%**\nQuantidade: **{total['qty']} / {goal}** · Pontos: **{total['pts']}**"))

@bot.tree.command(name="slot", description="Atribui um número de slot do baú a um membro")
async def slot(interaction: discord.Interaction, membro: discord.Member, numero: app_commands.Range[int, 1, 9999]) -> None:
    if not await deny_unless_leader(interaction): return
    try:
        with db() as con:
            row = con.execute("SELECT discord_id FROM members WHERE discord_id=?", (str(membro.id),)).fetchone()
            if not row:
                await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse membro ainda não tem cadastro aprovado.")); return
            con.execute("UPDATE members SET slot_number=? WHERE discord_id=?", (numero, str(membro.id)))
        log_action(interaction.user.id, "SLOT_ATRIBUIDO", membro.id, "", f"Slot {numero}")
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Slot **{numero}** atribuído a {membro.mention}."))
        if interaction.guild:
            await send_channel_log(interaction.guild, "📦 Slot de baú atualizado", f"Liderança: {interaction.user.mention}\nMembro: {membro.mention}\nSlot: **{numero}**", color=discord.Color.blurple())
    except sqlite3.IntegrityError:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse número de slot já está atribuído a outro membro."))

@bot.tree.command(name="slots", description="Lista os slots de baú atribuídos")
async def slots(interaction: discord.Interaction) -> None:
    if not await deny_unless_leader(interaction): return
    with db() as con:
        rows = con.execute("SELECT character_name,game_id,discord_id,slot_number FROM members WHERE status='ACTIVE' AND slot_number IS NOT NULL ORDER BY slot_number LIMIT 40").fetchall()
    if not rows:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Nenhum slot foi atribuído ainda.")); return
    lines = [f"**Slot {r['slot_number']}** — {r['character_name']} | {r['game_id']} — <@{r['discord_id']}>" for r in rows]
    await interaction.response.send_message(view=v2_view(PanelMessage(title="📦 Slots do baú", description="\n".join(lines), color=discord.Color.blurple()), None, None), ephemeral=True)

@bot.tree.command(name="nickname", description="Atualiza o nickname RP de um membro")
async def nickname(interaction: discord.Interaction, membro: discord.Member, nome_rp: str, passaporte: str) -> None:
    if not await deny_unless_leader(interaction): return
    nome_rp = nome_rp.strip()
    if not nome_rp:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Informe um nome RP válido.")); return
    if not passaporte.isdigit():
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "O passaporte deve conter somente números.")); return
    with db() as con:
        duplicate = con.execute(
            "SELECT discord_id FROM members WHERE game_id=? AND discord_id<>?",
            (passaporte, str(membro.id)),
        ).fetchone()
    if duplicate:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Esse passaporte já está vinculado a outro membro.")); return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        _rank_role, abbreviation = highest_configured_rank(membro)
        new_nick = build_member_nickname(nome_rp, passaporte, abbreviation or "M")
        await membro.edit(nick=new_nick, reason=f"Nickname atualizado por {interaction.user}")
        with db() as con:
            con.execute("UPDATE members SET character_name=?,game_id=? WHERE discord_id=?", (nome_rp.strip(), passaporte, str(membro.id)))
        log_action(interaction.user.id, "NICKNAME_ATUALIZADO", membro.id, "", new_nick)
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, f"Nickname atualizado para **{new_nick}**."))
        await send_channel_log(interaction.guild, "🏷️ Nickname atualizado", f"Liderança: {interaction.user.mention}\nMembro: {membro.mention}\nNovo nickname: **{new_nick}**", color=discord.Color.blurple())
    except sqlite3.IntegrityError:
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, "Esse passaporte já está vinculado a outro membro."))
    except discord.Forbidden:
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, "Não tenho permissão para editar esse nickname."))

@bot.tree.command(name="acao", description="Registra uma ação administrativa e envia log")
@app_commands.choices(tipo=[
    app_commands.Choice(name="Advertência", value="Advertência"),
    app_commands.Choice(name="Suspensão", value="Suspensão"),
    app_commands.Choice(name="Observação", value="Observação"),
    app_commands.Choice(name="Afastamento", value="Afastamento"),
    app_commands.Choice(name="Outro", value="Outro"),
])
async def acao(interaction: discord.Interaction, membro: discord.Member, tipo: app_commands.Choice[str], motivo: str) -> None:
    if not await deny_unless_leader(interaction): return
    log_action(interaction.user.id, tipo.value.upper(), membro.id, motivo, "Ação administrativa")
    await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Ação **{tipo.value}** registrada para {membro.mention}."))
    if interaction.guild:
        await send_channel_log(interaction.guild, f"📌 Ação administrativa: {tipo.value}", f"Aplicada por: {interaction.user.mention}\nMembro: {membro.mention}\nMotivo: {motivo}", color=discord.Color.orange())

async def add_warning(guild: discord.Guild, member: discord.Member, actor: discord.Member, reason: str) -> str:
    with db() as con:
        prior = con.execute("SELECT MAX(level) AS lvl FROM warnings WHERE discord_id=?", (str(member.id),)).fetchone()["lvl"] or 0
        level = min(int(prior) + 1, 3)
        con.execute("INSERT INTO warnings(discord_id,level,reason,issued_by,created_at) VALUES(?,?,?,?,?)", (str(member.id), level, reason, str(actor.id), now_iso()))
    role_ids = {1: ADV1_ROLE_ID, 2: ADV2_ROLE_ID, 3: ADV3_ROLE_ID}
    current_role = guild.get_role(role_ids[level]) if role_ids[level] else None
    previous_roles = [guild.get_role(rid) for rid in (ADV1_ROLE_ID, ADV2_ROLE_ID, ADV3_ROLE_ID) if rid]
    previous_roles = [r for r in previous_roles if r and r in member.roles]
    try:
        if previous_roles:
            await member.remove_roles(*previous_roles, reason="Atualização de nível de advertência")
        if current_role:
            await member.add_roles(current_role, reason=f"ADV {level}: {reason}")
    except discord.Forbidden:
        log.warning("Não foi possível atualizar os cargos de ADV para %s", member.id)
    log_action(actor.id, f"ADV_{level}", member.id, reason, "Advertência progressiva")
    await send_channel_log(guild, f"⚠️ Advertência ADV {level}", f"Aplicada por: {actor.mention}\nMembro: {member.mention}\nMotivo: {reason}", color=discord.Color.orange())
    return f"ADV {level}"

@bot.tree.command(name="adv", description="Aplica advertência progressiva; a terceira pode resultar em PD")
async def adv(interaction: discord.Interaction, membro: discord.Member, motivo: str) -> None:
    if not await deny_unless_leader(interaction): return
    if interaction.user.id != interaction.guild.owner_id and membro.top_role >= interaction.user.top_role:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você não pode aplicar advertência em alguém com cargo igual ou superior ao seu.")); return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    level = await add_warning(interaction.guild, membro, interaction.user, motivo)
    if level == "ADV 3" and AUTO_PD_ON_ADV3:
        try:
            await membro.send(None, view=v2_view(None, None, f"Você recebeu ADV 3 na organização **{FACTION_NAME}** e será removido conforme a configuração automática. Motivo: {motivo}"))
        except discord.HTTPException:
            pass
        try:
            await membro.kick(reason=f"ADV 3 automática por {interaction.user}: {motivo}")
            with db() as con:
                con.execute("UPDATE members SET status='REMOVED' WHERE discord_id=?", (str(membro.id),))
            log_action(interaction.user.id, "PD_AUTOMATICA_ADV3", membro.id, motivo, "Removido automaticamente por configuração")
            await send_channel_log(interaction.guild, "💀 PD automático por ADV 3", f"Liderança: {interaction.user.mention}\nMembro: {membro.mention}\nMotivo: {motivo}", color=discord.Color.dark_red())
            await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, "⚠️ ADV 3 registrada e PD automático aplicado conforme configuração."))
        except discord.Forbidden:
            await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, "ADV 3 registrada, mas não consegui aplicar PD automático por falta de permissão."))
    elif level == "ADV 3":
        try:
            await membro.send(None, view=v2_view(None, None, f"Você recebeu ADV 3 na organização **{FACTION_NAME}**. Motivo: {motivo}. A liderança deverá revisar seu caso."))
        except discord.HTTPException:
            pass
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, "⚠️ ADV 3 registrada. O PD automático está desativado; a liderança pode revisar o caso e usar `/pd`."))
    else:
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, f"⚠️ Advertência aplicada: **{level}**."))

@bot.tree.command(name="pd", description="Aplica perda de direitos e remove o membro do Discord")
async def pd(interaction: discord.Interaction, membro: discord.Member, motivo: str) -> None:
    if not await deny_unless_leader(interaction): return
    if interaction.user.id != interaction.guild.owner_id and membro.top_role >= interaction.user.top_role:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Você não pode aplicar PD em alguém com cargo igual ou superior ao seu.")); return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        try:
            await membro.send(None, view=v2_view(None, None, f"Você recebeu PD na organização **{FACTION_NAME}**. Motivo: {motivo}"))
        except discord.HTTPException:
            pass
        await membro.kick(reason=f"PD por {interaction.user}: {motivo}")
        with db() as con:
            con.execute("UPDATE members SET status='REMOVED' WHERE discord_id=?", (str(membro.id),))
        log_action(interaction.user.id, "PD", membro.id, motivo, "Membro removido do servidor")
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, f"PD aplicado em {membro.display_name}."))
        await send_channel_log(interaction.guild, "💀 PD aplicado", f"Liderança: {interaction.user.mention}\nMembro: {membro.mention}\nMotivo: {motivo}", color=discord.Color.dark_red())
        await send_channel_log(interaction.guild, "💀 Membro desligado", f"Um membro recebeu PD. Motivo registrado pela liderança: {motivo}", public=True, color=discord.Color.dark_grey())
    except discord.Forbidden:
        await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, "Não tenho permissão para expulsar esse membro."))

@bot.tree.command(name="blacklist-add", description="Adiciona um passaporte à blacklist")
async def blacklist_add(interaction: discord.Interaction, passaporte: str, motivo: str) -> None:
    if not await deny_unless_leader(interaction): return
    if not passaporte.isdigit():
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "O passaporte deve conter somente números.")); return
    with db() as con:
        con.execute("INSERT INTO blacklist(game_id,reason,added_by,created_at) VALUES(?,?,?,?) ON CONFLICT(game_id) DO UPDATE SET reason=excluded.reason,added_by=excluded.added_by,created_at=excluded.created_at", (passaporte, motivo, str(interaction.user.id), now_iso()))
    log_action(interaction.user.id, "BLACKLIST_ADD", None, motivo, f"Passaporte {passaporte}")
    await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Passaporte **{passaporte}** adicionado à blacklist."))
    if interaction.guild:
        await send_channel_log(interaction.guild, "⛔ BLACKLIST adicionada", f"Liderança: {interaction.user.mention}\nPassaporte: `{passaporte}`\nMotivo: {motivo}", color=discord.Color.dark_red())

@bot.tree.command(name="blacklist-check", description="Consulta um passaporte na blacklist")
async def blacklist_check(interaction: discord.Interaction, passaporte: str) -> None:
    if not await deny_unless_leader(interaction): return
    with db() as con:
        row = con.execute("SELECT * FROM blacklist WHERE game_id=?", (passaporte.strip(),)).fetchone()
    if not row:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"Passaporte **{passaporte}** não está na blacklist.")); return
    await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"⛔ **Passaporte {passaporte} está na blacklist**\nMotivo: {row['reason']}\nAdicionado por: <@{row['added_by']}>\nData: {row['created_at']}"))

@bot.tree.command(name="logs", description="Consulta as ações recentes da liderança")
@app_commands.describe(quantidade="Quantidade de registros, máximo 20")
async def logs(interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 20] = 10) -> None:
    if not await deny_unless_leader(interaction): return
    with db() as con:
        rows = con.execute("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (quantidade,)).fetchall()
    if not rows:
        await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, "Ainda não há ações registradas.")); return
    lines = []
    for r in rows:
        target = f" · alvo <@{r['target_id']}>" if r["target_id"] else ""
        reason = f" · motivo: {r['reason']}" if r["reason"] else ""
        details = f" · {r['details']}" if r["details"] else ""
        lines.append(f"`#{r['id']}` **{r['action']}** por <@{r['actor_id']}>{target}{reason}{details}\n< t:{int(datetime.fromisoformat(r['created_at']).timestamp())}:R >".replace("< t:", "<t:").replace(":R >", ":R>"))
    embed = PanelMessage(title="🧾 Logs administrativos", description="\n\n".join(lines)[:4000], color=discord.Color.blurple())
    await interaction.response.send_message(view=v2_view(embed, None, None), ephemeral=True)

@bot.tree.command(name="conquistas", description="Mostra marcos simples de atividade do membro")
@app_commands.describe(membro="Membro para consultar; vazio para você")
async def conquistas(interaction: discord.Interaction, membro: Optional[discord.Member] = None) -> None:
    target = membro or interaction.user
    with db() as con:
        total = con.execute("SELECT COALESCE(SUM(quantity),0) qty,COALESCE(SUM(points),0) pts,COUNT(*) n FROM farm_entries WHERE discord_id=?", (str(target.id),)).fetchone()
    achievements = []
    if total["n"] >= 1: achievements.append("🌱 Primeiro registro de farm")
    if total["n"] >= 10: achievements.append("🧱 10 registros de farm")
    if total["qty"] >= 100: achievements.append("📦 100 unidades farmadas")
    if total["pts"] >= 1000: achievements.append("🏆 1.000 pontos acumulados")
    if not achievements: achievements = ["Ainda não desbloqueou marcos. Registre seu farm para começar!"]
    await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, f"**Marcos de {target.mention}**\n" + "\n".join(achievements)))

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    log.error("Erro em comando: %s", error, exc_info=error)
    message = "Ocorreu um erro ao executar o comando. Confira a configuração e as permissões do bot."
    if isinstance(error, app_commands.MissingPermissions):
        message = "Você não tem permissão para usar esse comando."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(None, ephemeral=True, view=v2_view(None, None, message))
        else:
            await interaction.response.send_message(None, ephemeral=True, view=v2_view(None, None, message))
    except discord.HTTPException:
        pass

def _retry_after_seconds(exc: discord.HTTPException) -> float | None:
    """Extrai Retry-After do cabeçalho ou do corpo, se o Discord o enviar."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw:
        try:
            return max(1.0, float(raw))
        except (TypeError, ValueError):
            pass
    # discord.py guarda o corpo textual em HTTPException.text.
    try:
        import json
        payload = json.loads(getattr(exc, "text", "") or "{}")
        value = payload.get("retry_after")
        if value is not None:
            return max(1.0, float(value))
    except (ValueError, TypeError, AttributeError):
        pass
    return None


async def run_bot_with_login_backoff() -> None:
    """Evita martelar a API quando o Discord/Cloudflare bloqueia o login."""
    delay_without_header = 600.0  # 10 min na primeira falha sem Retry-After
    maximum_delay = 6 * 60 * 60.0
    while True:
        try:
            await bot.start(TOKEN, reconnect=True)
            return
        except discord.LoginFailure:
            log.critical(
                "Token recusado pelo Discord (401). Confira DISCORD_TOKEN no Render; "
                "o bot não fará novas tentativas automáticas com credencial inválida."
            )
            raise
        except discord.HTTPException as exc:
            if exc.status != 429:
                raise
            server_wait = _retry_after_seconds(exc)
            wait = server_wait if server_wait is not None else delay_without_header
            # Margem curta para evitar nova tentativa exatamente no limite.
            wait = min(maximum_delay, max(60.0, wait + 5.0))
            log.error(
                "Discord retornou HTTP 429 durante o login. Nova tentativa em %.0f segundos. "
                "O processo permanecerá ativo; não reinicie o serviço. Resposta: %s",
                wait, str(exc)[:300],
            )
            try:
                await bot.close()
            except Exception:
                log.debug("Falha ao fechar sessão HTTP após 429", exc_info=True)
            await asyncio.sleep(wait)
            delay_without_header = min(maximum_delay, delay_without_header * 2)


if __name__ == "__main__":
    if not TOKEN or TOKEN in {"SEU_TOKEN_AQUI", "COLOQUE_SEU_TOKEN_NOVO_AQUI"}:
        raise SystemExit("Configure DISCORD_TOKEN como variável de ambiente no Render (ou no .env local).")
    init_db()
    remove_legacy_gtt_branding()
    try:
        asyncio.run(run_bot_with_login_backoff())
    except KeyboardInterrupt:
        log.info("Encerramento solicitado pelo sistema.")
