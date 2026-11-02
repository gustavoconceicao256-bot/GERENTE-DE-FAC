"""Bot consolidado para gerenciamento de facções FiveM RP via Discord.
Configure as variáveis de ambiente no arquivo .env antes de iniciar.
"""
from __future__ import annotations

import os
import sqlite3
import logging
import asyncio
import math
import re
from urllib.parse import urlparse
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Modal, TextInput, View
from dotenv import load_dotenv

load_dotenv()

# ---------------- CONFIGURAÇÃO ----------------
TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
FACTION_NAME = re.sub(r"\bGTT\b", "", os.getenv("FACTION_NAME", "Facção RP"), flags=re.IGNORECASE).strip(" -—|") or "Facção RP"
DB_PATH = os.getenv("DATABASE_PATH", "fac_control.sqlite3")

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
        await interaction.response.send_message("Use este painel dentro do servidor.", ephemeral=True)
        return False
    member = interaction.user
    allowed = member.guild_permissions.administrator or has_leadership(interaction) or any(
        role.id == DEVELOPER_ROLE_ID for role in member.roles
    )
    if not allowed:
        await interaction.response.send_message(
            "Você precisa do cargo de desenvolvedor, liderança ou permissão de administrador para acessar o painel.",
            ephemeral=True,
        )
        return False
    return True


async def deny_unless_leader(interaction: discord.Interaction) -> bool:
    if not has_leadership(interaction):
        msg = "Você precisa do cargo de liderança ou permissão de administrador para usar isso."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
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
    embed = discord.Embed(title=title, description=description[:4000], color=color, timestamp=datetime.now(timezone.utc))
    try:
        await channel.send(embed=embed)
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


class RegistrationModal(Modal, title="Whitelist da facção"):
    character_name = TextInput(label="Seu nome no RP", placeholder="Ex.: Gustavo", max_length=32)
    game_id = TextInput(label="Seu ID no FiveM", placeholder="Somente números", max_length=8)

    def __init__(self, recruiter_id: int):
        super().__init__(timeout=300)
        self.recruiter_id = recruiter_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Use este formulário dentro do servidor.", ephemeral=True)
            return
        gid = self.game_id.value.strip()
        name = self.character_name.value.strip()
        if not gid.isdigit():
            await interaction.response.send_message("O ID do FiveM precisa conter somente números.", ephemeral=True)
            return
        role_id = recruiter_role_id()
        recruiter = interaction.guild.get_member(self.recruiter_id)
        if not recruiter or not role_id or not any(role.id == role_id for role in recruiter.roles):
            await interaction.response.send_message("Esse recrutador não possui mais o cargo autorizado. Inicie a WL novamente.", ephemeral=True)
            return
        with db() as con:
            pending = con.execute("SELECT id FROM registration_requests WHERE discord_id=? AND status='PENDING'", (str(interaction.user.id),)).fetchone()
            existing = con.execute("SELECT discord_id FROM members WHERE discord_id=? AND status='ACTIVE'", (str(interaction.user.id),)).fetchone()
            blacklisted = con.execute("SELECT game_id FROM blacklist WHERE game_id=?", (gid,)).fetchone()
            duplicate_game_id = con.execute("SELECT discord_id FROM members WHERE game_id=? AND status='ACTIVE'", (gid,)).fetchone()
            if pending:
                await interaction.response.send_message("Você já tem uma WL aguardando decisão.", ephemeral=True); return
            if existing:
                await interaction.response.send_message("Você já está cadastrado como membro.", ephemeral=True); return
            if blacklisted:
                await interaction.response.send_message("Esse ID está na blacklist da organização.", ephemeral=True); return
            if duplicate_game_id:
                await interaction.response.send_message("Esse ID do FiveM já está vinculado a outro membro.", ephemeral=True); return
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
            await interaction.response.send_message("O canal de WL não está configurado no painel/arquivo. Avise a administração.", ephemeral=True)
            return
        embed = discord.Embed(title=f"📋 Solicitação de WL #{request_id}", color=discord.Color.gold(), timestamp=datetime.now(timezone.utc))
        embed.description = "A solicitação poderá ser aprovada ou recusada por membros com o cargo autorizado configurado em **/config → WL**. Prazo: **5 minutos**."
        embed.add_field(name="Solicitante", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
        embed.add_field(name="Nome no RP", value=name, inline=True)
        embed.add_field(name="ID FiveM", value=gid, inline=True)
        embed.add_field(name="Recrutador escolhido", value=f"{recruiter.mention} (`{recruiter.id}`)", inline=False)
        embed.add_field(name="Prazo", value=f"Termina <t:{int(deadline.timestamp())}:R>", inline=False)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"request_id={request_id}")
        try:
            msg = await channel.send(content=recruiter.mention, embed=embed, view=RegistrationApprovalView())
            with db() as con:
                con.execute("UPDATE registration_requests SET message_id=? WHERE id=?", (str(msg.id), request_id))
            pending_role_id = int(setting("wl_pending_role_id", "0") or 0)
            pending_role = interaction.guild.get_role(pending_role_id) if pending_role_id else None
            if pending_role:
                try:
                    await interaction.user.add_roles(pending_role, reason=f"WL #{request_id} enviada; cargo temporário")
                except discord.Forbidden:
                    log.warning("Sem permissão para aplicar o cargo temporário de WL ao membro %s", interaction.user.id)
            await interaction.response.send_message("Sua solicitação foi enviada. Você receberá o resultado no privado.", ephemeral=True)
        except discord.HTTPException:
            with db() as con:
                con.execute("UPDATE registration_requests SET status='ERROR' WHERE id=?", (request_id,))
            await interaction.response.send_message("Não consegui publicar sua WL. Confira as permissões do bot e tente novamente.", ephemeral=True)

class RecruiterSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild):
        rid = recruiter_role_id()
        role = guild.get_role(rid) if rid else None
        members = sorted((m for m in (role.members if role else []) if not m.bot), key=lambda m: m.display_name.casefold())
        options = [discord.SelectOption(label=m.display_name[:100], value=str(m.id), description=f"Recrutador autorizado · {m.name}"[:100]) for m in members[:25]]
        if not options:
            options = [discord.SelectOption(label="Nenhum recrutador configurado", value="none", description="A administração precisa configurar o cargo")]
        super().__init__(placeholder="Escolha seu recrutador...", min_values=1, max_values=1, options=options, custom_id="fac:wl:recruiter")

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "none":
            await interaction.response.send_message("Não há recrutadores disponíveis. Avise a administração.", ephemeral=True)
            return
        await interaction.response.send_modal(RegistrationModal(int(self.values[0])))

class RecruiterSelectView(View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=180)
        self.add_item(RecruiterSelect(guild))

class RegistrationPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fazer WL", style=discord.ButtonStyle.primary, emoji="📋", custom_id="fac:register")
    async def register(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use este botão no servidor.", ephemeral=True); return
        await interaction.response.send_message("Primeiro, selecione um recrutador disponível e depois preencha seus dados. A aprovação ou recusa será feita por quem possuir o cargo autorizado em /config → WL.", view=RecruiterSelectView(interaction.guild), ephemeral=True)

class RegistrationRejectModal(Modal, title="Motivo da recusa"):
    reason = TextInput(label="Motivo obrigatório", placeholder="Explique por que a WL foi recusada", style=discord.TextStyle.paragraph, min_length=3, max_length=500)
    def __init__(self, request_id: int):
        super().__init__(timeout=180)
        self.request_id = request_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await finish_registration(interaction, self.request_id, approve=False, reason=self.reason.value.strip())

async def finish_registration(interaction: discord.Interaction, request_id: int, *, approve: bool, reason: str = "") -> None:
    if not interaction.guild:
        await interaction.response.send_message("Essa ação só pode ser feita no servidor.", ephemeral=True); return
    with db() as con:
        request = con.execute("SELECT * FROM registration_requests WHERE id=?", (request_id,)).fetchone()
        if not request or request["status"] != "PENDING":
            await interaction.response.send_message("Essa solicitação já foi encerrada.", ephemeral=True); return
        deadline_text = request["deadline_at"] or ""
        try:
            deadline = datetime.fromisoformat(deadline_text)
        except ValueError:
            deadline = datetime.now(timezone.utc)
        if datetime.now(timezone.utc) > deadline:
            await interaction.response.send_message("O prazo de 5 minutos já expirou; a WL será reprovada automaticamente.", ephemeral=True); return
        if not can_decide_wl(interaction):
            await interaction.response.send_message("Você não possui o cargo configurado para aprovar ou recusar WL.", ephemeral=True); return
        # Atualização condicional impede que dois cliques processem a mesma WL.
        cur = con.execute("UPDATE registration_requests SET status=?,processed_by=?,processed_at=?,reason=? WHERE id=? AND status='PENDING'",
                          ("APPROVED" if approve else "REJECTED", str(interaction.user.id), now_iso(), reason, request_id))
        if cur.rowcount != 1:
            await interaction.response.send_message("Essa solicitação já foi processada.", ephemeral=True); return
    member = await fetch_member(interaction.guild, int(request["discord_id"]))
    if member:
        pending_role_id = int(setting("wl_pending_role_id", "0") or 0)
        pending_role = interaction.guild.get_role(pending_role_id) if pending_role_id else None
        approved_role_id = int(setting("member_role_id", str(MEMBER_ROLE_ID)) or 0)
        approved_role = interaction.guild.get_role(approved_role_id) if approved_role_id else None
        try:
            if approve and pending_role and pending_role in member.roles:
                await member.remove_roles(pending_role, reason=f"WL #{request_id} aprovada")
            if approve and approved_role:
                await member.add_roles(approved_role, reason="WL aprovada")
        except discord.Forbidden:
            log.warning("Sem permissão para atualizar os cargos da WL do membro %s", member.id)
    if approve and member:
        try:
            # Patente mais alta e abreviação configurável por cargo; fallback seguro para M.
            rank_role, abbreviation = highest_configured_rank(member)
            prefix = abbreviation or "M"
            nickname = build_member_nickname(request["character_name"], request["game_id"], prefix)
            await member.edit(nick=nickname, reason=f"WL aprovada por recrutador {interaction.user}")
        except discord.Forbidden:
            log.warning("Sem permissão para atualizar cargo/nickname do membro %s", member.id)
        with db() as con:
            con.execute("""INSERT INTO members(discord_id,discord_name,character_name,game_id,recruiter,rank_name,joined_at,status)
                VALUES(?,?,?,?,?,?,?,'ACTIVE') ON CONFLICT(discord_id) DO UPDATE SET discord_name=excluded.discord_name,
                character_name=excluded.character_name,game_id=excluded.game_id,recruiter=excluded.recruiter,rank_name=excluded.rank_name,status='ACTIVE'""",
                (request["discord_id"], request["discord_name"], request["character_name"], request["game_id"], str(request["selected_recruiter_id"]), rank_role.name if rank_role else "Membro", now_iso()))
        log_action(interaction.user.id, "WL_APROVADA", int(request["discord_id"]), "Aprovada por usuário com cargo autorizado", f"Solicitação #{request_id}")
        result_title = "✅ WHITELIST APROVADA!"
        result_text = f"Parabéns! Sua entrada em **{FACTION_NAME}** foi aprovada.\n**Nome:** {request['character_name']}\n**ID:** {request['game_id']}\n**Recrutador:** <@{request['selected_recruiter_id'] or request['recruiter']}>\n**Decisão por:** {interaction.user.mention}"
        color = discord.Color.green()
    else:
        log_action(interaction.user.id, "WL_REJEITADA", int(request["discord_id"]), reason, f"Solicitação #{request_id}")
        result_title = "❌ WHITELIST REPROVADA"
        result_text = f"Sua WL para **{FACTION_NAME}** foi recusada.\n**Motivo:** {reason or 'O prazo de 5 minutos expirou sem aprovação.'}"
        color = discord.Color.red()
    if member:
        try:
            invite = invite_url()
            dm_embed = discord.Embed(title=result_title, description=result_text, color=color, timestamp=datetime.now(timezone.utc))
            dm_embed.set_footer(text="Resultado automático da whitelist")
            dm_view = View(timeout=None)
            dm_view.add_item(discord.ui.Button(label="IR PARA O SERVIDOR", style=discord.ButtonStyle.link, url=invite))
            await member.send(embed=dm_embed, view=dm_view)
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
        compact_result = discord.Embed(
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
            await original_message.edit(embed=compact_result, view=None, content=None)
        except discord.HTTPException:
            pass
    if not interaction.response.is_done():
        await interaction.response.send_message("WL processada e registrada.", ephemeral=True)

class RegistrationApprovalView(View):
    def __init__(self):
        super().__init__(timeout=None)

    def request_id_from(self, interaction: discord.Interaction) -> Optional[int]:
        if not interaction.message or not interaction.message.embeds:
            return None
        footer = interaction.message.embeds[0].footer.text or ""
        try: return int(footer.split("request_id=")[-1])
        except (ValueError, IndexError): return None

    @discord.ui.button(label="Aprovar", style=discord.ButtonStyle.success, emoji="✅", custom_id="fac:approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        request_id = self.request_id_from(interaction)
        if request_id is None:
            await interaction.response.send_message("ID da WL inválido.", ephemeral=True); return
        await finish_registration(interaction, request_id, approve=True)

    @discord.ui.button(label="Recusar", style=discord.ButtonStyle.danger, emoji="❌", custom_id="fac:reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        request_id = self.request_id_from(interaction)
        if request_id is None:
            await interaction.response.send_message("ID da WL inválido.", ephemeral=True); return
        with db() as con:
            row = con.execute("SELECT status,selected_recruiter_id FROM registration_requests WHERE id=?", (request_id,)).fetchone()
        if not row or row["status"] != "PENDING":
            await interaction.response.send_message("Essa WL já foi encerrada.", ephemeral=True); return
        if not can_decide_wl(interaction):
            await interaction.response.send_message("Você não possui o cargo configurado para aprovar ou recusar WL.", ephemeral=True); return
        await interaction.response.send_modal(RegistrationRejectModal(request_id))


def build_member_nickname(character_name: str, game_id: str, abbreviation: str) -> str:
    """Monta [TAG] NOME - ID sem cortar o ID no limite de 32 caracteres do Discord."""
    prefix = f"[ {abbreviation or 'M'} ] "
    suffix = f" - {game_id}"
    available = max(1, 32 - len(prefix) - len(suffix))
    safe_name = str(character_name or "Membro").upper()[:available].rstrip()
    return f"{prefix}{safe_name}{suffix}"[:32]


def highest_configured_rank(member: discord.Member):
    """Retorna o cargo de maior prioridade segundo a ordem configurada no painel."""
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
    candidates = [(priorities[r.id][0], r, priorities[r.id][1]) for r in member.roles if r.id in priorities]
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
                            try:
                                user = await bot.fetch_user(int(row["discord_id"]))
                                dm_embed = discord.Embed(title="⏱️ WHITELIST REPROVADA", description=f"Sua WL para **{FACTION_NAME}** foi reprovada automaticamente porque ninguém com o cargo autorizado decidiu dentro de 5 minutos.\n**Nome:** {row['character_name']}\n**ID:** {row['game_id']}", color=discord.Color.red())
                                view = View(timeout=None)
                                view.add_item(discord.ui.Button(label="IR PARA O SERVIDOR", style=discord.ButtonStyle.link, url=invite_url()))
                                await user.send(embed=dm_embed, view=view)
                            except discord.HTTPException:
                                pass
                            guild = bot.get_guild(GUILD_ID) if GUILD_ID else None
                            channel_id = int(setting("wl_channel_id", str(APPROVAL_CHANNEL_ID)) or 0)
                            channel = guild.get_channel(channel_id) if guild and channel_id else None
                            if isinstance(channel, discord.TextChannel) and row["message_id"]:
                                try:
                                    msg = await channel.fetch_message(int(row["message_id"]))
                                    if msg.embeds:
                                        embed = discord.Embed(
                                            title=f"⏱️ WL REPROVADA AUTOMATICAMENTE · #{row['id']}",
                                            description=(f"**Solicitante:** <@{row['discord_id']}>\n"
                                                         f"**Nome no RP:** {row['character_name']}\n"
                                                         f"**Recrutador escolhido:** <@{row['selected_recruiter_id'] or row['recruiter']}>\n"
                                                         f"**Status:** **REPROVADA**\n"
                                                         f"**Motivo:** prazo de 5 minutos expirado"),
                                            color=discord.Color.red(), timestamp=datetime.now(timezone.utc),
                                        )
                                        embed.set_footer(text=f"Registro de WL • request_id={row['id']}")
                                        await msg.edit(embed=embed, view=None, content=None)
                                except discord.HTTPException:
                                    pass
                            if guild:
                                await send_channel_log(guild, "⏱️ WL reprovada automaticamente", f"Solicitante: <@{row['discord_id']}>\nRecrutador escolhido: <@{row['selected_recruiter_id'] or row['recruiter']}>\nStatus: REPROVADA.", color=discord.Color.red())
        except Exception:
            log.exception("Erro ao verificar expiração das WLs")
        await asyncio.sleep(10)

class ActionModal(Modal, title="Registrar ação da liderança"):
    target_id = TextInput(label="ID do membro no Discord", placeholder="Ex.: 123456789012345678", max_length=20)
    action_type = TextInput(label="Tipo de ação", placeholder="Advertência, suspensão, observação...", max_length=40)
    reason = TextInput(label="Motivo / detalhes", style=discord.TextStyle.paragraph, max_length=500)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not has_leadership(interaction):
            await interaction.response.send_message("Você precisa de permissão de liderança.", ephemeral=True)
            return
        raw_id = self.target_id.value.strip().replace("<@", "").replace("!", "").replace(">", "")
        if not raw_id.isdigit():
            await interaction.response.send_message("Informe um ID numérico válido do Discord.", ephemeral=True)
            return
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
        await interaction.response.send_message(f"Ação **{action_name}** registrada para `{target_id}`.", ephemeral=True)

class LeadershipPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Registrar ação", style=discord.ButtonStyle.primary, emoji="📌", custom_id="fac:leadership:register-action")
    async def register_action(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not has_leadership(interaction):
            await interaction.response.send_message("Você precisa de permissão de liderança.", ephemeral=True)
            return
        await interaction.response.send_modal(ActionModal())

    @discord.ui.button(label="Ver logs recentes", style=discord.ButtonStyle.secondary, emoji="🧾", custom_id="fac:leadership:recent-logs")
    async def recent_logs(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not has_leadership(interaction):
            await interaction.response.send_message("Você precisa de permissão de liderança.", ephemeral=True)
            return
        with db() as con:
            rows = con.execute("SELECT * FROM actions ORDER BY id DESC LIMIT 10").fetchall()
        if not rows:
            await interaction.response.send_message("Ainda não há ações registradas.", ephemeral=True)
            return
        lines = []
        for row in rows:
            target = f" · alvo <@{row['target_id']}>" if row["target_id"] else ""
            reason = f" · {row['reason']}" if row["reason"] else ""
            lines.append(f"`#{row['id']}` **{row['action']}** por <@{row['actor_id']}>{target}{reason}")
        embed = discord.Embed(title="🧾 Últimas ações da liderança", description="\n".join(lines), color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------- REGISTRO DE FARM COM IDENTIFICAÇÃO PELA WL ----------------
class FarmQuantityModal(Modal, title="Quantidade do farm"):
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
            await interaction.response.send_message("Informe uma quantidade numérica entre 1 e 100.000.", ephemeral=True)
            return
        if not is_active_member(interaction.user.id):
            await interaction.response.send_message("Seu cadastro ativo da WL não foi encontrado. Não é possível registrar farm.", ephemeral=True)
            return
        with db() as con:
            member = con.execute("SELECT discord_id, character_name, game_id FROM members WHERE discord_id=? AND status='ACTIVE'", (str(interaction.user.id),)).fetchone()
            item = con.execute("SELECT name, points, unit_price_cents FROM farm_items WHERE id=? AND active=1", (self.item_id,)).fetchone()
            if not member:
                await interaction.response.send_message("Não encontrei seu cadastro ativo da WL. Procure a liderança.", ephemeral=True)
                return
            if not item:
                await interaction.response.send_message("Esse item de farm foi desativado. Inicie um novo registro.", ephemeral=True)
                return
            if int(item["unit_price_cents"] or 0) <= 0:
                await interaction.response.send_message("A liderança ainda não definiu o valor por unidade desse item. Avise a liderança para configurar `/farm-preco`.", ephemeral=True)
                return
            quantity = int(raw)
            day_prefix = local_day_prefix()
            daily_entries = con.execute("SELECT COUNT(*) AS n FROM farm_entries WHERE discord_id=? AND substr(created_at,1,10)=?", (str(interaction.user.id), day_prefix)).fetchone()["n"]
            daily_pending = con.execute("SELECT COUNT(*) AS n FROM farm_proofs WHERE discord_id=? AND status='PENDING' AND substr(created_at,1,10)=?", (str(interaction.user.id), day_prefix)).fetchone()["n"]
            daily = int(daily_entries) + int(daily_pending)
            if daily >= MAX_FARM_ENTRIES_PER_DAY:
                await interaction.response.send_message(f"Você atingiu o limite de {MAX_FARM_ENTRIES_PER_DAY} registros de farm hoje.", ephemeral=True)
                return
            last_entry = con.execute("SELECT created_at FROM farm_entries WHERE discord_id=? ORDER BY id DESC LIMIT 1", (str(interaction.user.id),)).fetchone()
            last_pending = con.execute("SELECT created_at FROM farm_proofs WHERE discord_id=? AND status='PENDING' ORDER BY id DESC LIMIT 1", (str(interaction.user.id),)).fetchone()
            timestamps = [row["created_at"] for row in (last_entry, last_pending) if row and row["created_at"]]
            if timestamps and FARM_COOLDOWN_SECONDS:
                try:
                    latest = max(datetime.fromisoformat(value) for value in timestamps)
                    elapsed = (datetime.now(timezone.utc) - latest).total_seconds()
                    if elapsed < FARM_COOLDOWN_SECONDS:
                        await interaction.response.send_message(f"Aguarde {int(FARM_COOLDOWN_SECONDS-elapsed)+1}s antes de iniciar outro registro.", ephemeral=True)
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
            (f"🌾 **Farm selecionado:** {item['name']}\n**Quantidade:** {quantity}\n" + (f"**Valor estimado:** R$ {(quantity * int(item['unit_price_cents'] or 0))/100:,.2f}\n\n" if int(item['unit_price_cents'] or 0) else "**Valor:** ainda não definido pela liderança\n\n") + "Agora envie a **imagem de prova como anexo neste mesmo canal**. O bot já identificou seu nome e ID pela WL; não precisa informar novamente."),
            ephemeral=True)


class FarmItemSelect(discord.ui.Select):
    def __init__(self, interaction: discord.Interaction):
        self.requester_id = interaction.user.id
        self.channel_id = interaction.channel_id
        with db() as con:
            items = con.execute("SELECT id,name,points,unit_price_cents FROM farm_items WHERE active=1 ORDER BY name LIMIT 25").fetchall()
        options = [discord.SelectOption(label=row["name"][:100], value=str(row["id"]), description=(f"R$ {int(row['unit_price_cents'] or 0)/100:.2f} por unidade · {row['points']} pts" if int(row['unit_price_cents'] or 0) else f"{row['points']} ponto(s) por unidade")[:100]) for row in items]
        super().__init__(placeholder="Selecione o farm configurado...", min_values=1, max_values=1, options=options, disabled=not options)
        self.items = {int(row["id"]): (row["name"], int(row["points"])) for row in items}

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Somente a pessoa que iniciou o registro pode selecionar o farm.", ephemeral=True)
            return
        if not is_active_member(interaction.user.id):
            await interaction.response.send_message("Seu cadastro ativo da WL não foi encontrado.", ephemeral=True)
            return
        item_id = int(self.values[0])
        item = self.items.get(item_id)
        if not item:
            await interaction.response.send_message("Item inválido. Abra um novo registro.", ephemeral=True)
            return
        await interaction.response.send_modal(FarmQuantityModal(item_id, item[0], item[1], self.channel_id))


class FarmItemSelectView(View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=180)
        self.add_item(FarmItemSelect(interaction))


class FarmPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="REGISTRAR FARM", style=discord.ButtonStyle.success, emoji="🌾", custom_id="fac:farm:start")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Faça o registro pelo servidor.", ephemeral=True)
            return
        with db() as con:
            member = con.execute("SELECT character_name,game_id FROM members WHERE discord_id=? AND status='ACTIVE'", (str(interaction.user.id),)).fetchone()
            items = con.execute("SELECT COUNT(*) AS n FROM farm_items WHERE active=1").fetchone()["n"]
        if not member:
            await interaction.response.send_message("Seu cadastro ativo da WL não foi encontrado. Faça/regularize sua WL antes de registrar farm.", ephemeral=True)
            return
        if not items:
            await interaction.response.send_message("Nenhum tipo de farm está configurado/ativo no painel. Avise a liderança.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Cadastro identificado: **{member['character_name']}** · ID **{member['game_id']}**.\nSelecione o farm que deseja registrar:",
            view=FarmItemSelectView(interaction), ephemeral=True)


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
            await message.reply("O prazo de 15 minutos para enviar a prova expirou. Inicie um novo registro pelo painel.")
            return True
    except (TypeError, ValueError):
        pass
    if str(pending["channel_id"] or "") != str(message.channel.id):
        return False
    if not is_active_member(message.author.id):
        with db() as con:
            con.execute("DELETE FROM farm_proof_pending WHERE discord_id=?", (str(message.author.id),))
        await message.reply("Seu cadastro ativo da WL não foi localizado. O registro de farm foi cancelado.")
        return True
    image_attachment = next((a for a in message.attachments if (a.content_type or "").startswith("image/") or a.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))), None)
    if not image_attachment:
        await message.reply("Envie a imagem de prova como anexo nesta mensagem. Links e mensagens sem imagem não concluem o registro.")
        return True
    if image_attachment.size > 8 * 1024 * 1024:
        await message.reply("A imagem excede o limite de 8 MB. Envie uma imagem menor.")
        return True
    try:
        image_bytes = await image_attachment.read(use_cached=False)
    except discord.HTTPException:
        await message.reply("Não consegui baixar a imagem. Envie novamente.")
        return True
    filename = image_attachment.filename[:200]
    mime = image_attachment.content_type or "application/octet-stream"
    signatures = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")
    if not image_bytes.startswith(signatures):
        await message.reply("O arquivo não parece ser uma imagem PNG, JPG, GIF ou WEBP válida.")
        return True
    guild = message.guild
    private_channel_id = int(setting("farm_private_channel_id", "0") or 0)
    review_channel = guild.get_channel(private_channel_id) if guild and private_channel_id else None
    if not isinstance(review_channel, discord.TextChannel):
        await message.reply("O canal privado de cobranças ainda não foi configurado. A prova não foi registrada. Avise a liderança em `/config` → Farm.")
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
    embed = discord.Embed(
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
        invoice_message = await review_channel.send(embed=embed, file=discord.File(io.BytesIO(image_bytes), filename=safe_filename), view=FarmInvoiceView())
        with db() as con:
            con.execute("UPDATE farm_proofs SET review_channel_id=?,review_message_id=? WHERE id=?", (str(review_channel.id), str(invoice_message.id), proof_id))
        try:
            await message.delete()
        except discord.HTTPException:
            log.warning("Não consegui apagar a mensagem de prova do membro %s", message.author.id)
        try:
            await message.channel.send(f"📨 Sua solicitação de farm **#{proof_id}** foi enviada para a liderança. A decisão chegará por mensagem privada.", delete_after=20)
        except discord.HTTPException:
            pass
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Não foi possível publicar cobrança #%s", proof_id)
        await message.reply(f"⚠️ A prova #{proof_id} foi salva, mas a cobrança não pôde ser publicada. Avise a liderança; não envie outra prova.")
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


def apply_panel_media(embed: discord.Embed, kind: str) -> discord.Embed:
    photo_url = panel_setting(f"{kind}_panel_photo_url", "").strip()
    banner_url = panel_setting(f"{kind}_panel_banner_url", "").strip()
    if photo_url and valid_asset_url(photo_url):
        embed.set_thumbnail(url=photo_url)
    if banner_url and valid_asset_url(banner_url):
        embed.set_image(url=banner_url)
    return embed


def panel_embed(kind: str) -> discord.Embed:
    if kind == "wl":
        title = panel_setting("wl_panel_title", f"📋 WHITELIST — {FACTION_NAME}")
        description = panel_setting(
            "wl_panel_description",
            "Clique em **Fazer WL**, escolha o recrutador e informe seu nome no RP e ID do FiveM. "
            "A aprovação ou recusa é feita por quem possuir o cargo autorizado configurado em /config → WL.",
        )
        return apply_panel_media(discord.Embed(title=title[:256], description=description[:4096], color=discord.Color.blurple()), "wl")
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
        return apply_panel_media(discord.Embed(title=title[:256], description=description[:4096], color=discord.Color.green()), "farm")
    if kind == "hierarchy":
        title = panel_setting("hierarchy_panel_title", f"🏛️ HIERARQUIA — {FACTION_NAME}")
        description = panel_setting("hierarchy_panel_description", "Cargos e membros da organização, do cargo mais baixo ao mais alto.")
        return discord.Embed(title=title[:256], description=description[:4096], color=discord.Color.blurple())
    # Os demais painéis continuam disponíveis pelos comandos existentes, mas não são apresentados no /config simplificado.
    if kind == "members":
        title = panel_setting("members_panel_title", f"👥 MEMBROS — {FACTION_NAME}")
        description = panel_setting("members_panel_description", "Lista de membros cadastrados e ativos da organização.")
        return discord.Embed(title=title[:256], description=description[:4096], color=discord.Color.blurple())
    if kind == "leader":
        title = panel_setting("leader_panel_title", f"👑 PAINEL DA LIDERANÇA — {FACTION_NAME}")
        description = panel_setting("leader_panel_description", "Use os botões para registrar uma ação administrativa ou consultar os logs recentes.")
        return discord.Embed(title=title[:256], description=description[:4096], color=discord.Color.dark_gold())
    title = panel_setting("main_panel_title", f"🎛️ PAINEL PRINCIPAL — {FACTION_NAME}")
    description = panel_setting("main_panel_description", "Acesse os recursos da facção: perfil, farm, ranking, histórico e ações administrativas.")
    return discord.Embed(title=title[:256], description=description[:4096], color=discord.Color.blurple())


def config_embed(guild: Optional[discord.Guild] = None) -> discord.Embed:
    """Resumo visual das configurações, organizado por módulos (sem menu de logs)."""
    embed = discord.Embed(
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

    embed.add_field(
        name="📋 Whitelist",
        value=(
            f"**{panel_setting('wl_panel_title', 'Whitelist')[:90]}**\n"
            f"Publicação: {channel_label('wl_panel_channel_id', str(REGISTRATION_CHANNEL_ID))}\n"
            f"Solicitações: {channel_label('wl_channel_id', str(APPROVAL_CHANNEL_ID))}\n"
            f"Cargo temporário: {role_label(int(panel_setting('wl_pending_role_id', '0') or 0))}\n"
            f"Cargo após aprovação: {role_label(int(panel_setting('member_role_id', str(MEMBER_ROLE_ID)) or 0))}\n"
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
            "Ordem: sequência selecionada, da base ao topo"
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


class ConfigTextChannelSelect(discord.ui.ChannelSelect):
    """Canal de texto selecionado com o próprio menu do Discord, sem digitar IDs."""
    def __init__(self, setting_key: str, placeholder: str, *, row: int = 0):
        super().__init__(
            placeholder=placeholder,
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=row,
        )
        self.setting_key = setting_key

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        save_config_value(self.setting_key, str(channel.id))
        await interaction.response.send_message(f"✅ Canal salvo: {channel.mention}", ephemeral=True)


class ConfigManualIdModal(Modal, title="Configurar ID manualmente"):
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
            await interaction.response.send_message("Chave inválida ou ID não numérico. Confira a chave e cole somente o ID.", ephemeral=True)
            return
        object_id = int(raw)
        if key.endswith("channel_id"):
            obj = interaction.guild.get_channel(object_id) if interaction.guild else None
            if obj is None or not isinstance(obj, (discord.TextChannel, discord.Thread, discord.ForumChannel)):
                await interaction.response.send_message("Esse ID não corresponde a um canal de texto válido deste servidor.", ephemeral=True)
                return
            label = f"canal {obj.mention}"
        else:
            obj = interaction.guild.get_role(object_id) if interaction.guild else None
            if obj is None:
                await interaction.response.send_message("Esse ID não corresponde a um cargo deste servidor.", ephemeral=True)
                return
            label = f"cargo {obj.mention}"
        if self.farm_return and key in FARM_CONFIG_FIELDS:
            field_kind = FARM_CONFIG_FIELDS[key][1]
            if field_kind == "channel" and not isinstance(obj, discord.TextChannel):
                await interaction.response.send_message(
                    "Para o Farm, informe um canal de texto deste servidor.", ephemeral=True
                )
                return
            if field_kind == "role" and obj.is_default():
                await interaction.response.send_message(
                    "O cargo @everyone não pode ser usado como cargo autorizador do Farm.", ephemeral=True
                )
                return
        save_config_value(key, str(object_id))
        if self.farm_return and key in FARM_CONFIG_FIELDS:
            if interaction.guild is None:
                await interaction.response.send_message(
                    "Use este formulário dentro do servidor.", ephemeral=True
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
        await interaction.response.send_message(f"✅ Configuração **{key}** salva usando {label}.", ephemeral=True)


class ConfigManualIdButton(discord.ui.Button):
    def __init__(self, *, row: int = 4, preset_key: str = "", farm_return: bool = False):
        super().__init__(label="Informar ID manualmente", style=discord.ButtonStyle.secondary, emoji="🔢", row=row)
        self.preset_key = preset_key
        self.farm_return = farm_return

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            ConfigManualIdModal(self.preset_key, farm_return=self.farm_return)
        )


class ConfigWLChannelsView(View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(ConfigTextChannelSelect("wl_panel_channel_id", "Canal onde o painel WL será publicado", row=0))
        self.add_item(ConfigTextChannelSelect("wl_channel_id", "Canal que receberá os pedidos de WL", row=1))
        self.add_item(ConfigManualIdButton(preset_key="wl_panel_channel_id"))


class ConfigWLRoleView(View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(ConfigSingleRoleSelect("wl_pending_role_id", "Cargo temporário antes da aprovação da WL", row=0))
        self.add_item(ConfigSingleRoleSelect("member_role_id", "Cargo adicionado quando a WL for aprovada", row=1))
        self.add_item(ConfigSingleRoleSelect("wl_approver_role_id", "Cargo autorizado a aprovar ou recusar WL", row=2))
        self.add_item(ConfigSingleRoleSelect("recruit_role_id", "Cargo cujos membros aparecem como recrutadores", row=3))
        self.add_item(ConfigManualIdButton(preset_key="member_role_id"))


class ConfigFarmChannelsView(View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(ConfigTextChannelSelect("farm_panel_channel_id", "Canal público do painel Farm", row=0))
        self.add_item(ConfigTextChannelSelect("farm_private_channel_id", "Canal privado de cobranças e pagamentos", row=1))
        self.add_item(ConfigSingleRoleSelect("farm_approver_role_id", "Cargo autorizado a decidir cobranças", row=2))
        self.add_item(ConfigTextChannelSelect("farm_log_channel_id", "Canal de histórico final do Farm", row=3))
        self.add_item(ConfigManualIdButton(preset_key="farm_private_channel_id"))


class ConfigSingleRoleSelect(discord.ui.RoleSelect):
    def __init__(self, setting_key: str, placeholder: str, *, row: int):
        super().__init__(placeholder=placeholder, min_values=0, max_values=1, row=row)
        self.setting_key = setting_key

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0] if self.values else None
        save_config_value(self.setting_key, str(role.id if role else 0))
        role_name = role.mention if role else "(desconfigurado)"
        await interaction.response.send_message(f"✅ Cargo salvo: {role_name}", ephemeral=True)


class ConfigRolesSelectView(View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(ConfigSingleRoleSelect("member_role_id", "Selecionar cargo padrão dos membros", row=0))
        self.add_item(ConfigSingleRoleSelect("recruit_role_id", "Selecionar cargo autorizado a recrutar WL", row=1))
        self.add_item(ConfigSingleRoleSelect("leader_role_id", "Selecionar cargo de liderança", row=2))
        self.add_item(ConfigManualIdButton(preset_key="leader_role_id"))


HIERARCHY_ROLES_PER_PAGE = 25


class ConfigHierarchyChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent_view: "ConfigHierarchySetupView"):
        super().__init__(
            placeholder="① Selecione o canal de publicação da hierarquia",
            channel_types=[discord.ChannelType.text], min_values=1, max_values=1, row=0,
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        self.parent_view.channel_id = channel.id
        await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)


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
            placeholder=f"② Cargos {start + 1}–{start + len(self.page_roles)} • lista crescente",
            min_values=0,
            max_values=max(1, len(options)),
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        visible_ids = {role.id for role in self.page_roles}
        # Mantém a ordem de seleção: primeiro é o cargo mais baixo; o último é o mais alto.
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
            )
        )
        self.parent_view._persist_selected_roles()
        self.parent_view._refresh_remove_selector()
        await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)


class ConfigHierarchyAddRoleModal(Modal, title="Adicionar cargo por ID"):
    role_id_input = TextInput(label="ID do cargo", max_length=25, required=True, placeholder="Cole o ID do cargo")
    def __init__(self, parent_view):
        super().__init__(timeout=300)
        self.parent_view = parent_view
    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.role_id_input.value.strip()
        role = self.parent_view.guild.get_role(int(raw)) if raw.isdigit() else None
        if role is None or role == self.parent_view.guild.default_role:
            await interaction.response.send_message("ID inválido: não encontrei esse cargo neste servidor.", ephemeral=True)
            return
        if role.id not in self.parent_view.selected_role_ids:
            self.parent_view.selected_role_ids.append(role.id)
        self.parent_view.selected_role_ids.sort(
            key=lambda rid: self.parent_view.guild.get_role(rid).position
            if self.parent_view.guild.get_role(rid) else -1
        )
        self.parent_view._persist_selected_roles()
        self.parent_view._refresh_role_page()
        await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)

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
        options = [discord.SelectOption(label=r.name[:100], value=str(r.id)) for r in roles[:25]]
        if not options:
            options = [discord.SelectOption(label="Nenhum cargo selecionado", value="none")]
        super().__init__(placeholder="❌ Remover cargo selecionado", min_values=0, max_values=1, options=options, row=2)
    async def callback(self, interaction: discord.Interaction) -> None:
        raw = self.values[0] if self.values else "none"
        if raw != "none":
            rid = int(raw)
            self.parent_view.selected_role_ids = [x for x in self.parent_view.selected_role_ids if x != rid]
        self.parent_view._persist_selected_roles()
        self.parent_view._refresh_remove_selector()
        await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)


class ConfigHierarchySetupView(View):
    """Configuração em lote: seleciona vários cargos por página, sem confirmar um a um."""
    def __init__(self, guild: discord.Guild, required_count: Optional[int] = None):
        super().__init__(timeout=900)
        self.guild = guild
        self.required_count = required_count
        # @everyone não é patente. Todos os demais cargos são incluídos, inclusive cargos gerenciados.
        self.roles = sorted(
            (role for role in guild.roles if role != guild.default_role),
            key=lambda role: (role.position, role.name.casefold()),
            reverse=False,
        )
        self.page_index = 0
        with db() as con:
            rows = con.execute("SELECT key,value FROM settings WHERE key LIKE 'rank_role_%'").fetchall()
        saved_roles = []
        for row in rows:
            try:
                role_id = int(row["key"].replace("rank_role_", "", 1))
                _abbr, priority = row["value"].split("|", 1)
                if guild.get_role(role_id):
                    saved_roles.append((int(priority), role_id))
            except (ValueError, TypeError):
                continue
        self.selected_role_ids: list[int] = [
            role_id for _priority, role_id in sorted(saved_roles)
        ]
        self.role_selector = ConfigHierarchyRolePageSelect(self)
        self.add_item(self.role_selector)
        self.remove_selector = ConfigHierarchyRemoveSelect(self)
        self.add_item(self.remove_selector)
        self._update_pager_buttons()

    @property
    def total_pages(self) -> int:
        return max(1, math.ceil(len(self.roles) / HIERARCHY_ROLES_PER_PAGE))

    def _update_pager_buttons(self) -> None:
        self.previous_page.disabled = self.page_index <= 0
        self.next_page.disabled = self.page_index >= self.total_pages - 1

    def _persist_selected_roles(self) -> None:
        """Salva a seleção atual para não perder cargos ao voltar ou fechar o painel."""
        selected_roles = [self.guild.get_role(rid) for rid in self.selected_role_ids]
        selected_roles = [role for role in selected_roles if role is not None]
        with db() as con:
            con.execute("DELETE FROM settings WHERE key LIKE 'rank_role_%'")
            for idx, role in enumerate(selected_roles):
                priority = idx + 1
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

    def build_embed(self, *, saved: bool = False) -> discord.Embed:
        selected_roles = [self.guild.get_role(rid) for rid in self.selected_role_ids]
        selected_roles = [role for role in selected_roles if role is not None]
        # Exibição visual de cima para baixo: topo primeiro e cargo-base por último.
        # A prioridade gravada continua sendo 1 = base, último = topo.
        display_roles = list(reversed(selected_roles))
        selected_text = "\n".join(
            f"`{len(selected_roles) - index}.` {role.mention}"
            for index, role in enumerate(display_roles[:18])
        )
        if len(selected_roles) > 18:
            selected_text += f"\n… e mais {len(selected_roles) - 18} cargo(s) selecionado(s)."
        if not selected_text:
            selected_text = "Nenhum cargo selecionado ainda."
        embed = discord.Embed(
            title="🏛️ Configurar hierarquia",
            description=(
                "Selecione os cargos no menu. Não existe limite de quantidade pré-definida. "
                "Use o menu **Remover cargo** para retirar uma seleção incorreta; o botão Reiniciar limpa tudo.\n\n"
                "A ordem de seleção define a hierarquia: selecione primeiro o cargo mais baixo e termine no cargo mais alto."
            ),
            color=discord.Color.from_rgb(76, 86, 180),
        )
        embed.add_field(name="📄 Página de cargos", value=f"**{self.page_index + 1}/{self.total_pages}** · {len(self.roles)} cargo(s) disponíveis", inline=True)
        embed.add_field(name="✅ Selecionados", value=f"**{len(selected_roles)} cargo(s)**", inline=True)
        embed.add_field(name="📋 Hierarquia (topo → base)", value=selected_text[:1024], inline=False)
        if saved:
            embed.set_footer(text="Configuração salva • nº 1 é o cargo mais alto; último é o mais baixo")
        else:
            embed.set_footer(text="A seleção é salva automaticamente • Use as setas para ver todos os cargos")
        return embed

    @discord.ui.button(label="◀ Anterior", style=discord.ButtonStyle.secondary, emoji="⬅️", row=3)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.page_index > 0:
            self.page_index -= 1
        self._refresh_role_page()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Próxima ▶", style=discord.ButtonStyle.secondary, emoji="➡️", row=3)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.page_index < self.total_pages - 1:
            self.page_index += 1
        self._refresh_role_page()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

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
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class ConfigWLPanelModal(Modal, title="Configurar painel de WL"):
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
            await interaction.response.send_message("Foto e banner precisam ser URLs públicas HTTPS, ou deixe o campo vazio para remover a imagem.", ephemeral=True)
            return
        save_config_value("wl_panel_title", self.title_input.value.strip() or f"WL — {FACTION_NAME}")
        save_config_value("wl_panel_description", self.description_input.value.strip())
        save_config_value("wl_panel_photo_url", photo)
        save_config_value("wl_panel_banner_url", banner)
        await interaction.response.send_message(
            "✅ Aparência da WL salva. Configure abaixo os cargos temporário, de aprovação e de decisão, além do cargo usado para listar recrutadores.",
            view=ConfigWLRoleView(), ephemeral=True,
        )
        await interaction.followup.send(
            "📣 Agora configure os canais de publicação e recebimento das solicitações de WL.",
            view=ConfigWLChannelsView(), ephemeral=True,
        )


class ConfigHierarchyModal(Modal, title="Aparência da hierarquia"):
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
            await interaction.response.send_message("Use este formulário dentro do servidor.", ephemeral=True)
            return
        save_config_value("hierarchy_panel_title", self.title_input.value.strip() or f"HIERARQUIA — {FACTION_NAME}")
        save_config_value("hierarchy_panel_description", self.description_input.value.strip())
        setup_view = ConfigHierarchySetupView(interaction.guild)
        await interaction.response.send_message(embed=setup_view.build_embed(), view=setup_view, ephemeral=True)


# O fluxo antigo de seleção cargo-por-cargo foi removido: agora a hierarquia é configurada em lote por páginas.

class ConfigFarmPanelModal(Modal, title="Configurar painel e aprovação de farm"):
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
                "Use este formulário dentro de um servidor.", ephemeral=True
            )
            return
        photo, banner = self.photo_url.value.strip(), self.banner_url.value.strip()
        if not valid_asset_url(photo) or not valid_asset_url(banner):
            await interaction.response.send_message("Foto e banner precisam ser URLs públicas HTTPS, ou deixe o campo vazio para remover a imagem.", ephemeral=True)
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




class ConfigLogsModal(Modal, title="Canais de logs"):
    admin_channel = TextInput(label="ID do canal de logs administrativos", max_length=22, required=True)
    public_channel = TextInput(label="ID do canal de logs públicos", max_length=22, required=True)
    farm_channel = TextInput(label="ID do canal de logs de farm", max_length=22, required=True)

    def __init__(self):
        super().__init__(timeout=300)
        _set_modal_defaults(self, [(self.admin_channel, ADMIN_LOG_CHANNEL_ID), (self.public_channel, PUBLIC_LOG_CHANNEL_ID), (self.farm_channel, FARM_LOG_CHANNEL_ID)])

    async def on_submit(self, interaction: discord.Interaction) -> None:
        vals = {"admin_log_channel_id": self.admin_channel.value.strip(), "public_log_channel_id": self.public_channel.value.strip(), "farm_log_channel_id": self.farm_channel.value.strip()}
        if any(not v.isdigit() for v in vals.values()):
            await interaction.response.send_message("Todos os IDs devem ser numéricos. Use 0 para desativar.", ephemeral=True); return
        for k, v in vals.items(): save_config_value(k, v)
        await interaction.response.send_message("✅ Canais de logs administrativos, públicos e de farm salvos.", ephemeral=True)




class FarmProofRejectModal(Modal, title="Recusar comprovante de farm"):
    reason = TextInput(label="Motivo obrigatório", style=discord.TextStyle.paragraph, min_length=3, max_length=500, required=True)
    def __init__(self, proof_id: int):
        super().__init__(timeout=300)
        self.proof_id = proof_id
    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not can_decide_farm(interaction):
            await interaction.response.send_message("Você não possui o cargo configurado para aprovar ou recusar comprovantes de farm.", ephemeral=True); return
        with db() as con:
            row = con.execute("SELECT * FROM farm_proofs WHERE id=?", (self.proof_id,)).fetchone()
            if not row or row["status"] != "PENDING":
                await interaction.response.send_message("Esse comprovante já foi analisado ou não existe.", ephemeral=True); return
            cur = con.execute("UPDATE farm_proofs SET status='REJECTED',reviewed_by=?,reviewed_at=?,review_reason=? WHERE id=? AND status='PENDING'", (str(interaction.user.id), now_iso(), self.reason.value.strip(), self.proof_id))
            if not cur.rowcount:
                await interaction.response.send_message("Esse comprovante já foi analisado.", ephemeral=True); return
        log_action(interaction.user.id, "FARM_COMPROVANTE_RECUSADO", int(row["discord_id"]), self.reason.value.strip(), f"Comprovante #{self.proof_id}")
        if interaction.guild:
            await send_channel_log(interaction.guild, "🌾 Comprovante de farm recusado", f"Comprovante: `#{self.proof_id}`\nMembro: <@{row['discord_id']}>\nMotivo: {self.reason.value.strip()}", color=discord.Color.red())
        await update_farm_review_message(row, status="REJECTED", reviewer=interaction.user.mention, reason=self.reason.value.strip())
        try:
            user = await bot.fetch_user(int(row["discord_id"]))
            await user.send(f"❌ Seu comprovante de farm **#{self.proof_id}** foi recusado. Motivo: {self.reason.value.strip()}")
        except discord.HTTPException:
            pass
        await interaction.response.send_message(f"Comprovante #{self.proof_id} recusado e registrado.", ephemeral=True)


async def update_farm_review_message(row, *, status: str, reviewer: str, reason: str = "") -> None:
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
        if msg.embeds:
            embed = msg.embeds[0]
            embed.title = f"{'✅ APROVADO' if status == 'APPROVED' else '❌ RECUSADO'} · COMPROVANTE DE FARM #{row['id']}"
            embed.color = discord.Color.green() if status == "APPROVED" else discord.Color.red()
            embed.add_field(name="Analisado por", value=reviewer, inline=True)
            if reason: embed.add_field(name="Motivo", value=reason[:1024], inline=False)
            await msg.edit(embed=embed, view=None)
    except discord.HTTPException:
        log.exception("Não foi possível atualizar a mensagem do comprovante #%s", row["id"])


class FarmInvoiceRejectModal(Modal, title="Recusar cobrança de farm"):
    reason = TextInput(label="Motivo da recusa", style=discord.TextStyle.paragraph, min_length=5, max_length=500, required=True)
    def __init__(self, proof_id: int):
        super().__init__(timeout=300)
        self.proof_id = proof_id
    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not can_decide_farm(interaction):
            await interaction.response.send_message("Você não possui o cargo autorizado para decidir cobranças de farm.", ephemeral=True); return
        with db() as con:
            row = con.execute("SELECT * FROM farm_proofs WHERE id=?", (self.proof_id,)).fetchone()
            if not row or row["status"] != "PENDING":
                await interaction.response.send_message("Essa cobrança já foi finalizada ou não existe.", ephemeral=True); return
            cur = con.execute("UPDATE farm_proofs SET status='REJECTED',reviewed_by=?,reviewed_at=?,review_reason=? WHERE id=? AND status='PENDING'", (str(interaction.user.id), now_iso(), self.reason.value.strip(), self.proof_id))
            if not cur.rowcount:
                await interaction.response.send_message("Essa cobrança já foi decidida por outra pessoa.", ephemeral=True); return
        log_action(interaction.user.id, "FARM_COBRANCA_RECUSADA", int(row["discord_id"]), self.reason.value.strip(), f"Cobrança #{self.proof_id}")
        await finalize_farm_invoice(row, "REJECTED", interaction.user, reason=self.reason.value.strip())
        await interaction.response.send_message(f"Cobrança #{self.proof_id} recusada e registrada.", ephemeral=True)


class FarmInvoiceConfirmView(View):
    def __init__(self, proof_id: int):
        super().__init__(timeout=300)
        self.proof_id = proof_id
    @discord.ui.button(label="SIM, pagar", style=discord.ButtonStyle.success, emoji="✅")
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_decide_farm(interaction):
            await interaction.response.send_message("Você não possui o cargo autorizado para decidir cobranças de farm.", ephemeral=True); return
        with db() as con:
            row = con.execute("SELECT * FROM farm_proofs WHERE id=? AND status='PENDING'", (self.proof_id,)).fetchone()
        if not row:
            await interaction.response.send_message("Essa cobrança já foi finalizada.", ephemeral=True); return
        with db() as con:
            con.execute("INSERT INTO farm_receipt_pending(reviewer_id,proof_id,channel_id,created_at) VALUES(?,?,?,?) ON CONFLICT(reviewer_id) DO UPDATE SET proof_id=excluded.proof_id,channel_id=excluded.channel_id,created_at=excluded.created_at", (str(interaction.user.id), self.proof_id, str(interaction.channel_id), now_iso()))
        await interaction.response.send_message(f"Envie agora, neste canal privado, a **foto do comprovante de pagamento** da cobrança #{self.proof_id}. A imagem será salva no banco e enviada ao membro por DM. Prazo: 10 minutos.", ephemeral=True)
    @discord.ui.button(label="NÃO, não pagar", style=discord.ButtonStyle.danger, emoji="✖️")
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_decide_farm(interaction):
            await interaction.response.send_message("Você não possui o cargo autorizado para decidir cobranças de farm.", ephemeral=True); return
        await interaction.response.send_modal(FarmInvoiceRejectModal(self.proof_id))


class FarmInvoiceView(View):
    def __init__(self):
        super().__init__(timeout=None)
    def proof_id_from(self, interaction: discord.Interaction) -> Optional[int]:
        if not interaction.message or not interaction.message.embeds: return None
        footer = interaction.message.embeds[0].footer.text or ""
        try: return int(footer.split("farm_proof_id=")[-1])
        except (ValueError, IndexError): return None
    @discord.ui.button(label="Registrar pagamento", style=discord.ButtonStyle.success, emoji="💸", custom_id="fac:farm:invoice-pay")
    async def pay(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_decide_farm(interaction):
            await interaction.response.send_message("Você não possui o cargo autorizado para decidir cobranças de farm.", ephemeral=True); return
        proof_id = self.proof_id_from(interaction)
        if not proof_id:
            await interaction.response.send_message("Não consegui identificar essa cobrança.", ephemeral=True); return
        await interaction.response.send_message(f"Confirma o pagamento da cobrança **#{proof_id}**?", view=FarmInvoiceConfirmView(proof_id), ephemeral=True)
    @discord.ui.button(label="Recusar cobrança", style=discord.ButtonStyle.danger, emoji="✖️", custom_id="fac:farm:invoice-reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_decide_farm(interaction):
            await interaction.response.send_message("Você não possui o cargo autorizado para decidir cobranças de farm.", ephemeral=True); return
        proof_id = self.proof_id_from(interaction)
        if not proof_id:
            await interaction.response.send_message("Não consegui identificar essa cobrança.", ephemeral=True); return
        await interaction.response.send_modal(FarmInvoiceRejectModal(proof_id))


async def finalize_farm_invoice(row, status: str, reviewer: discord.abc.User, *, reason: str = "", receipt: bytes = b"", receipt_filename: str = "", receipt_mime: str = "") -> None:
    proof_id = int(row["id"])
    with db() as con:
        expected_status = "PENDING" if status == "APPROVED" else "REJECTED"
        cur = con.execute("UPDATE farm_proofs SET status=?,reviewed_by=?,reviewed_at=?,review_reason=?,paid_by=?,paid_at=?,receipt_data=?,receipt_filename=?,receipt_mime=? WHERE id=? AND status=?", (status, str(reviewer.id), now_iso(), reason, str(reviewer.id) if status == "APPROVED" else "", now_iso() if status == "APPROVED" else "", sqlite3.Binary(receipt) if receipt else None, receipt_filename, receipt_mime, proof_id, expected_status))
        if not cur.rowcount:
            return
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
            final_embed = discord.Embed(title=f"{'✅ PAGAMENTO CONFIRMADO' if status == 'APPROVED' else '❌ COBRANÇA RECUSADA'} · FARM #{proof_id}", color=discord.Color.green() if status == 'APPROVED' else discord.Color.red(), timestamp=datetime.now(timezone.utc))
            final_embed.description = (
                f"**Membro:** <@{row['discord_id']}> · {row['character_name']}\n"
                f"**ID:** {row['game_id']}\n**Item:** {row['item_name']} × {row['quantity']}\n"
                f"**Total:** R$ {int(row['total_cents'] or 0)/100:.2f}\n**Decidido por:** {reviewer.mention}"
                + (f"\n**Motivo:** {reason}" if reason else "")
            )
            await msg.edit(embed=final_embed, view=None)
            history_channel_id = int(setting("farm_log_channel_id", str(FARM_LOG_CHANNEL_ID)) or 0)
            history_channel = bot.get_channel(history_channel_id) if history_channel_id else None
            if isinstance(history_channel, discord.TextChannel) and history_channel.id != channel.id:
                await history_channel.send(embed=final_embed)
        except discord.HTTPException: log.exception("Falha ao atualizar cobrança finalizada #%s", proof_id)
    try:
        user = await bot.fetch_user(int(row["discord_id"]))
        if status == "APPROVED" and receipt:
            import io
            await user.send(f"✅ Seu farm #{proof_id} foi pago! Valor: **R$ {int(row['total_cents'] or 0)/100:.2f}**. Comprovante anexado.", file=discord.File(io.BytesIO(receipt), filename=receipt_filename or f"pagamento-{proof_id}.png"))
        elif status == "REJECTED":
            await user.send(f"❌ Sua cobrança de farm #{proof_id} foi recusada. Motivo: **{reason}**")
    except discord.HTTPException:
        log.info("Não foi possível enviar DM para o membro da cobrança #%s", proof_id)
    log_action(reviewer.id, "FARM_PAGAMENTO_CONFIRMADO" if status == "APPROVED" else "FARM_COBRANCA_FINALIZADA_COM_RECUSA", int(row["discord_id"]), reason, f"Cobrança #{proof_id}; total={int(row['total_cents'] or 0)} centavos")


async def store_farm_payment_receipt(message: discord.Message) -> bool:
    with db() as con:
        pending = con.execute("SELECT * FROM farm_receipt_pending WHERE reviewer_id=?", (str(message.author.id),)).fetchone()
    if not pending: return False
    if str(pending["channel_id"]) != str(message.channel.id): return False
    try:
        if (datetime.now(timezone.utc) - datetime.fromisoformat(pending["created_at"])).total_seconds() > 600:
            with db() as con: con.execute("DELETE FROM farm_receipt_pending WHERE reviewer_id=?", (str(message.author.id),))
            await message.reply("O prazo para enviar o comprovante expirou. Inicie a confirmação novamente."); return True
    except (TypeError, ValueError): pass
    if not isinstance(message.author, discord.Member) or not can_decide_farm_for_member(message.author):
        await message.reply("Somente o cargo configurado para decidir Farm pode anexar esse comprovante."); return True
    attachment = next((a for a in message.attachments if (a.content_type or "").startswith("image/") or a.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))), None)
    if not attachment:
        await message.reply("Anexe a imagem do comprovante de pagamento nesta mensagem."); return True
    if attachment.size > 8 * 1024 * 1024:
        await message.reply("A imagem excede 8 MB. Envie uma menor."); return True
    try: data = await attachment.read(use_cached=False)
    except discord.HTTPException:
        await message.reply("Não consegui baixar o comprovante. Envie novamente."); return True
    if not data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")):
        await message.reply("O arquivo não parece ser uma imagem válida."); return True
    with db() as con:
        row = con.execute("SELECT * FROM farm_proofs WHERE id=? AND status='PENDING'", (int(pending["proof_id"]),)).fetchone()
        con.execute("DELETE FROM farm_receipt_pending WHERE reviewer_id=?", (str(message.author.id),))
    if not row:
        await message.reply("Essa cobrança já foi finalizada ou não existe."); return True
    await finalize_farm_invoice(row, "APPROVED", message.author, receipt=data, receipt_filename=attachment.filename[:150], receipt_mime=attachment.content_type or "application/octet-stream")
    try: await message.delete()
    except discord.HTTPException: pass
    try: await message.channel.send(f"✅ Pagamento da cobrança **#{row['id']}** registrado e comprovante salvo.", delete_after=15)
    except discord.HTTPException: pass
    return True


def can_decide_farm_for_member(member: discord.Member) -> bool:
    if member.guild_permissions.administrator: return True
    try: role_id = int(setting("farm_approver_role_id", str(LEADER_ROLE_ID)) or 0)
    except (TypeError, ValueError): role_id = 0
    return bool(role_id and any(role.id == role_id for role in member.roles))


class FarmProofReviewView(View):
    def __init__(self):
        super().__init__(timeout=None)

    def proof_id_from(self, interaction: discord.Interaction) -> Optional[int]:
        if not interaction.message or not interaction.message.embeds:
            return None
        footer = interaction.message.embeds[0].footer.text or ""
        try: return int(footer.split("farm_proof_id=")[-1])
        except (ValueError, IndexError): return None

    @discord.ui.button(label="Aprovar comprovante", style=discord.ButtonStyle.success, emoji="✅", custom_id="fac:farm:approve-proof")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not can_decide_farm(interaction):
            await interaction.response.send_message("Você não possui o cargo configurado para aprovar ou recusar comprovantes de farm.", ephemeral=True); return
        proof_id = self.proof_id_from(interaction)
        if not proof_id:
            await interaction.response.send_message("Não consegui identificar esse comprovante.", ephemeral=True); return
        expected_channel = int(panel_setting("farm_approval_channel_id", "0") or 0)
        if expected_channel and interaction.channel_id != expected_channel:
            await interaction.response.send_message("A aprovação precisa ser feita no canal de análise configurado.", ephemeral=True); return
        with db() as con:
            row = con.execute("SELECT * FROM farm_proofs WHERE id=?", (proof_id,)).fetchone()
            if not row or row["status"] != "PENDING":
                await interaction.response.send_message("Esse comprovante já foi analisado ou não existe.", ephemeral=True); return
            cur = con.execute("UPDATE farm_proofs SET status='APPROVED',reviewed_by=?,reviewed_at=?,review_reason='' WHERE id=? AND status='PENDING'", (str(interaction.user.id), now_iso(), proof_id))
            if not cur.rowcount:
                await interaction.response.send_message("Esse comprovante já foi analisado.", ephemeral=True); return
            con.execute("INSERT INTO farm_entries(discord_id,item_name,quantity,points,created_at) VALUES(?,?,?,?,?)",
                (str(row["discord_id"]), row["item_name"], int(row["quantity"] or 0), int(row["points"] or 0), now_iso()))
        log_action(interaction.user.id, "FARM_COMPROVANTE_APROVADO", int(row["discord_id"]), "", f"Comprovante #{proof_id}; {row['item_name']} x{row['quantity']}")
        await update_farm_review_message(row, status="APPROVED", reviewer=interaction.user.mention)
        guild = interaction.guild
        log_channel_id = int(setting("farm_log_channel_id", str(FARM_LOG_CHANNEL_ID)) or 0)
        log_channel = guild.get_channel(log_channel_id) if guild and log_channel_id else None
        if isinstance(log_channel, discord.TextChannel):
            approved_embed = discord.Embed(title=f"🌾 REGISTRO DE FARM #{proof_id}", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
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
            await log_channel.send(embed=approved_embed, file=discord.File(io.BytesIO(row["image_data"]), filename=safe_log_filename))
        try:
            user = await bot.fetch_user(int(row["discord_id"]))
            await user.send(f"✅ Seu comprovante de farm **#{proof_id}** foi aprovado pela liderança.")
        except discord.HTTPException:
            pass
        await interaction.response.send_message(f"Comprovante #{proof_id} aprovado.", ephemeral=True)

    @discord.ui.button(label="Recusar comprovante", style=discord.ButtonStyle.danger, emoji="❌", custom_id="fac:farm:reject-proof")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not can_decide_farm(interaction):
            await interaction.response.send_message("Você não possui o cargo configurado para aprovar ou recusar comprovantes de farm.", ephemeral=True); return
        proof_id = self.proof_id_from(interaction)
        if not proof_id:
            await interaction.response.send_message("Não consegui identificar esse comprovante.", ephemeral=True); return
        expected_channel = int(panel_setting("farm_approval_channel_id", "0") or 0)
        if expected_channel and interaction.channel_id != expected_channel:
            await interaction.response.send_message("A recusa precisa ser feita no canal de análise configurado.", ephemeral=True); return
        await interaction.response.send_modal(FarmProofRejectModal(proof_id))


class HierarchyPublishChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent_view: "HierarchyPublishChannelView"):
        self.parent_view = parent_view
        super().__init__(placeholder="Selecione o canal onde publicar a hierarquia", channel_types=[discord.ChannelType.text], min_values=1, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        self.parent_view.channel_id = channel.id
        self.parent_view.publish_now.disabled = False
        await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)


class HierarchyPublishChannelView(View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=600)
        self.guild = guild
        self.channel_id: Optional[int] = None
        self.add_item(HierarchyPublishChannelSelect(self))
        self.add_item(ConfigManualIdButton(preset_key="hierarchy_panel_channel_id", row=2))
        self.publish_now.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not has_leadership(interaction):
            await interaction.response.send_message("Somente liderança ou administradores podem publicar a hierarquia.", ephemeral=True)
            return False
        return True

    def build_embed(self) -> discord.Embed:
        title = panel_setting("hierarchy_panel_title", f"🏛️ HIERARQUIA — {FACTION_NAME}")
        description = panel_setting("hierarchy_panel_description", "Cargos e membros da organização, do mais alto ao mais baixo.")
        channel = self.guild.get_channel(self.channel_id) if self.channel_id else None
        embed = discord.Embed(
            title="📣 Publicar hierarquia",
            description=f"**{title[:180]}**\n{description[:700]}\n\nSelecione o canal de destino. A hierarquia será publicada em seções por cargo, do mais alto ao mais baixo.",
            color=discord.Color.from_rgb(76, 86, 180),
        )
        embed.add_field(name="Canal escolhido", value=channel.mention if channel else "`Selecione um canal abaixo`", inline=False)
        embed.set_footer(text="Cada membro aparece apenas na seção do maior cargo configurado que possui")
        return embed

    @discord.ui.button(label="Publicar neste canal", style=discord.ButtonStyle.success, emoji="🚀", row=1)
    async def publish_now(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.channel_id:
            raw = setting("hierarchy_panel_channel_id", "0")
            self.channel_id = int(raw) if raw.isdigit() and int(raw) else None
        if not self.channel_id:
            await interaction.response.send_message("Selecione o canal ou informe o ID manualmente.", ephemeral=True)
            return
        save_config_value("hierarchy_panel_channel_id", str(self.channel_id))
        await interaction.response.defer(ephemeral=True, thinking=True)
        await publish_configured_panel(interaction, "hierarchy", channel_override=self.channel_id)




# ---------------- HIERARQUIA CONFIGURÁVEL (FLUXO GUIADO) ----------------
HIERARCHY_MAX_ROLES = 25
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
        for key in ("hierarchy_panel_channel_id", "hierarchy_role_count"):
            con.execute("DELETE FROM settings WHERE key=?", (key,))


class HierarchyGuideView(View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=900)
        self.guild = guild

    @discord.ui.button(label="Configurar hierarquia do zero", style=discord.ButtonStyle.success, emoji="⚙️")
    async def configure(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HierarchyCountModal(self.guild))

    @discord.ui.button(label="Apagar configuração anterior", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "⚠️ **Confirma a exclusão?** Isso apagará os cargos, tags, canal salvo e as mensagens da hierarquia no canal anteriormente configurado. Essa ação não pode ser desfeita.",
            view=HierarchyResetConfirmView(self.guild), ephemeral=True
        )


class HierarchyResetConfirmView(View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=120)
        self.guild = guild

    @discord.ui.button(label="Sim, apagar tudo", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        raw = setting("hierarchy_panel_channel_id", "0")
        channel = self.guild.get_channel(int(raw)) if raw.isdigit() and int(raw) else None
        deleted = 0
        if isinstance(channel, discord.TextChannel):
            try:
                async for message in channel.history(limit=None):
                    try:
                        await message.delete()
                        deleted += 1
                    except discord.Forbidden:
                        raise
                    except discord.HTTPException:
                        continue
            except discord.Forbidden:
                await interaction.response.send_message("Não tenho permissão para limpar o canal anterior. Conceda Gerenciar mensagens e tente novamente.", ephemeral=True)
                return
        clear_hierarchy_settings()
        await interaction.response.edit_message(content=f"✅ Configuração apagada. {deleted} mensagem(ns) removida(s) do canal anterior. Você pode começar do zero.", view=None)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Operação cancelada. Nenhuma configuração foi alterada.", view=None)


class HierarchyCountModal(Modal, title="Etapa 1 — Quantidade de cargos"):
    count = TextInput(label="Quantos cargos terá a hierarquia? (1 a 25)", placeholder="Ex.: 10", max_length=2)

    def __init__(self, guild):
        super().__init__(timeout=300)
        self.guild = guild

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.count.value.strip()
        if not raw.isdigit() or not 1 <= int(raw) <= HIERARCHY_MAX_ROLES:
            await interaction.response.send_message("Informe uma quantidade entre 1 e 25.", ephemeral=True)
            return
        count = int(raw)
        save_config_value("hierarchy_role_count", str(count))
        # A nova configuração substitui a lista anterior, mas mantém o canal até a publicação.
        with db() as con:
            con.execute("DELETE FROM settings WHERE key LIKE 'rank_role_%'")
            con.execute("DELETE FROM settings WHERE key LIKE 'hierarchy_draft_tag_%'")
        view = HierarchyRoleConfigView(self.guild, count)
        view.owner_id = interaction.user.id
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)


class HierarchyRoleIdsModal(Modal):
    def __init__(self, parent, start: int, end: int):
        super().__init__(title=f"Etapa 2 — IDs dos cargos {start + 1} a {end}", timeout=300)
        self.parent_view = parent
        self.start = start
        self.end = end
        self.inputs = []
        for index in range(start, end):
            field = TextInput(
                label=f"Cargo {index + 1} — {'MAIS BAIXO' if index == 0 else ('MAIS ALTO' if index == len(parent.role_ids) - 1 else 'ID do cargo')}",
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
            if not role or role == self.parent_view.guild.default_role:
                await interaction.response.send_message(f"ID inválido no campo Cargo {self.start + offset + 1}. Confira se o cargo pertence a este servidor.", ephemeral=True)
                return
            if role.id in resolved or role.id in [x for i, x in enumerate(self.parent_view.role_ids) if x and not self.start <= i < self.end]:
                await interaction.response.send_message("Você informou o mesmo cargo mais de uma vez. Cada posição precisa ter um cargo diferente.", ephemeral=True)
                return
            resolved.append(role.id)
        for offset, rid in enumerate(resolved):
            self.parent_view.role_ids[self.start + offset] = rid
        await interaction.response.send_message(embed=self.parent_view.embed(), view=self.parent_view, ephemeral=True)



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
            await interaction.response.send_message("Somente quem iniciou a configuração pode selecionar os cargos.", ephemeral=True)
            return
        raw = self.values[0]
        role = self.parent_view.guild.get_role(int(raw)) if raw.isdigit() else None
        if role is None or role == self.parent_view.guild.default_role:
            await interaction.response.send_message("Cargo inválido. Selecione um cargo deste servidor.", ephemeral=True)
            return
        if role.id in [rid for rid in self.parent_view.role_ids if rid]:
            await interaction.response.send_message("Esse cargo já foi selecionado. Escolha outro.", ephemeral=True)
            return
        try:
            index = self.parent_view.role_ids.index(None)
        except ValueError:
            await interaction.response.send_message("Todos os cargos já foram preenchidos.", ephemeral=True)
            return
        self.parent_view.role_ids[index] = role.id
        await self.parent_view.refresh_after_role_change(interaction)


class HierarchyRoleIdModal(Modal, title="Adicionar cargo por ID"):
    role_id_input = TextInput(label="ID do cargo", max_length=25, required=True, placeholder="Cole o ID do cargo do servidor")

    def __init__(self, parent_view):
        super().__init__(timeout=300)
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.owner_id:
            await interaction.response.send_message("Somente quem iniciou a configuração pode informar os IDs.", ephemeral=True)
            return
        raw = self.role_id_input.value.strip()
        role = self.parent_view.guild.get_role(int(raw)) if raw.isdigit() else None
        if role is None or role == self.parent_view.guild.default_role:
            await interaction.response.send_message("ID inválido: não encontrei esse cargo neste servidor.", ephemeral=True)
            return
        if role.id in [rid for rid in self.parent_view.role_ids if rid]:
            await interaction.response.send_message("Esse cargo já foi selecionado. Informe outro ID.", ephemeral=True)
            return
        try:
            index = self.parent_view.role_ids.index(None)
        except ValueError:
            await interaction.response.send_message("Todos os cargos já foram preenchidos.", ephemeral=True)
            return
        self.parent_view.role_ids[index] = role.id
        if all(rid is not None for rid in self.parent_view.role_ids):
            next_view = HierarchyTagConfigView(self.parent_view.guild, self.parent_view.role_ids, self.parent_view.owner_id)
            await interaction.response.send_message(embed=next_view.embed(), view=next_view, ephemeral=True)
        else:
            self.parent_view._refresh_role_picker()
            await interaction.response.send_message(embed=self.parent_view.embed(), view=self.parent_view, ephemeral=True)


class HierarchyRoleConfigView(View):
    """Seleciona cargos do servidor em páginas, do cargo mais baixo ao mais alto."""
    def __init__(self, guild, count):
        super().__init__(timeout=900)
        self.guild, self.count = guild, count
        self.role_ids = [None] * count
        self.owner_id = 0
        # Ordem de seleção: primeiro o cargo mais baixo; último o mais alto.
        self.available_roles = sorted(
            (role for role in guild.roles if role != guild.default_role),
            key=lambda role: (role.position, role.name.casefold()),
            reverse=False,
        )
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

    async def refresh_after_role_change(self, interaction):
        if all(rid is not None for rid in self.role_ids):
            tags_view = HierarchyTagConfigView(self.guild, self.role_ids, self.owner_id)
            await interaction.response.edit_message(embed=tags_view.embed(), view=tags_view)
            return
        self._refresh_role_picker()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Somente quem iniciou a configuração pode alterar os cargos.", ephemeral=True)
            return False
        return True

    def embed(self):
        lines = []
        for idx, rid in enumerate(self.role_ids):
            role = self.guild.get_role(rid) if rid else None
            lines.append(f"**Cargo {idx + 1}:** {role.mention if role else '— não preenchido —'}")
        e = discord.Embed(
            title="Etapa 2 — Seleção de cargos",
            description=(
                "Use o menu para selecionar os cargos na ordem **do mais baixo para o mais alto**. "
                "As setas mudam a página do menu e percorrem todos os cargos do servidor.\n\n"
                + "\n".join(lines)
            ),
            color=discord.Color.blurple(),
        )
        e.set_footer(text=f"Página de cargos {self.role_page + 1}/{self.role_pages} • {sum(rid is not None for rid in self.role_ids)}/{self.count} cargos definidos")
        return e

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.role_page = max(0, self.role_page - 1)
        self._refresh_role_picker()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.role_page = min(self.role_pages - 1, self.role_page + 1)
        self._refresh_role_picker()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HierarchyCountModal(self.guild))

    @discord.ui.button(label="Por ID", style=discord.ButtonStyle.primary, emoji="🔢", row=2)
    async def by_id(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HierarchyRoleIdModal(self))

    @discord.ui.button(label="Recomeçar", style=discord.ButtonStyle.danger, emoji="🔄", row=2)
    async def restart(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.role_ids = [None] * self.count
        self.role_page = 0
        self._refresh_role_picker()
        await interaction.response.edit_message(embed=self.embed(), view=self)


class HierarchyTagsModal(Modal):
    def __init__(self, parent, start, end):
        super().__init__(title=f"Etapa 3 — Tags dos cargos {start + 1} a {end}", timeout=300)
        self.parent_view, self.start = parent, start
        self.inputs = []
        for idx in range(start, end):
            role = self.parent_view.guild.get_role(self.parent_view.role_ids[idx])
            field = TextInput(
                label=f"Tag para {role.name[:35] if role else f'Cargo {idx+1}'}",
                placeholder="Ex.: M, REC, LT",
                required=True, max_length=12
            )
            old = self.parent_view.tags[idx]
            if old:
                field.default = old
            self.inputs.append(field)
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        for offset, field in enumerate(self.inputs):
            tag = field.value.strip().replace("[", "").replace("]", "")
            if not tag or any(ch in tag for ch in "\n\r"):
                await interaction.response.send_message("Cada cargo precisa ter uma tag válida (até 12 caracteres).", ephemeral=True)
                return
            idx = self.start + offset
            self.parent_view.tags[idx] = tag
            rid = self.parent_view.role_ids[idx]
            save_config_value(f"hierarchy_draft_tag_{rid}", tag)
        # Ao concluir o bloco atual, avança automaticamente para o próximo.
        if self.parent_view.page < self.parent_view.pages - 1:
            self.parent_view.page += 1
        await interaction.response.send_message(embed=self.parent_view.embed(), view=self.parent_view, ephemeral=True)


class HierarchyTagConfigView(View):
    def __init__(self, guild, role_ids, owner_id=0):
        super().__init__(timeout=900)
        self.guild, self.role_ids = guild, list(role_ids)
        self.owner_id = owner_id
        with db() as con:
            self.tags = [
                (con.execute("SELECT value FROM settings WHERE key=?", (f"hierarchy_draft_tag_{rid}",)).fetchone() or {"value": ""})["value"]
                for rid in self.role_ids
            ]
        self.page = 0

    @property
    def pages(self):
        return max(1, (len(self.role_ids) + 4) // 5)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id and interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Somente quem iniciou a configuração pode alterar as tags.", ephemeral=True
            )
            return False
        return True

    def embed(self):
        start, end = self.page * 5, min(self.page * 5 + 5, len(self.role_ids))
        lines = []
        for idx in range(start, end):
            role = self.guild.get_role(self.role_ids[idx])
            tag = self.tags[idx] or "— não definida —"
            lines.append(f"{role.mention if role else f'Cargo {idx+1}'} → **[{tag}]**")
        e = discord.Embed(
            title="Etapa 3 — Tags / abreviações",
            description=("Defina a tag que ficará antes do nome do membro. Exemplo: **[ M ] GUSTAVO BOBO - 1908**.\n"
                         "A tag acompanha o cargo mais alto configurado que o membro possuir.\n\n" + "\n".join(lines)),
            color=discord.Color.blurple()
        )
        e.set_footer(text=f"Bloco {self.page + 1}/{self.pages} • {sum(bool(x) for x in self.tags)}/{len(self.tags)} tags definidas")
        return e

    @discord.ui.button(label="Definir tag", style=discord.ButtonStyle.primary, emoji="✏️", row=0)
    async def define_tag(self, interaction: discord.Interaction, button: discord.ui.Button):
        start = self.page * 5
        await interaction.response.send_modal(HierarchyTagsModal(self, start, min(start + 5, len(self.tags))))

    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=self.embed(), view=self)
            return
        view = HierarchyRoleConfigView(self.guild, len(self.role_ids))
        view.role_ids = list(self.role_ids)
        view.owner_id = self.owner_id or interaction.user.id
        view._refresh_role_picker()
        await interaction.response.edit_message(embed=view.embed(), view=view)

    @discord.ui.button(label="Recomeçar", style=discord.ButtonStyle.danger, emoji="🔄", row=1)
    async def restart(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.tags = [""] * len(self.tags)
        with db() as con:
            for rid in self.role_ids:
                con.execute("DELETE FROM settings WHERE key=?", (f"hierarchy_draft_tag_{rid}",))
        self.page = 0
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Próximo", style=discord.ButtonStyle.secondary, emoji="➡️", row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.pages - 1:
            self.page += 1
            await interaction.response.edit_message(embed=self.embed(), view=self)
            return
        if any(not x for x in self.tags):
            await interaction.response.send_message(
                "Defina a tag de todos os cargos antes de continuar.", ephemeral=True
            )
            return
        with db() as con:
            con.execute("DELETE FROM settings WHERE key LIKE 'rank_role_%'")
            total = len(self.role_ids)
            # A seleção começa no cargo mais baixo e termina no mais alto.
            for idx, (rid, tag) in enumerate(zip(self.role_ids, self.tags)):
                priority = idx + 1
                con.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?)",
                    (f"rank_role_{rid}", f"{tag}|{priority}")
                )
                con.execute("DELETE FROM settings WHERE key=?", (f"hierarchy_draft_tag_{rid}",))
        view = HierarchyFinalChannelView(self.guild)
        await interaction.response.edit_message(content=None, embed=view.embed(), view=view)


class HierarchyManualChannelModal(Modal, title="Canal da hierarquia"):
    channel_id = TextInput(label="ID do canal de texto", placeholder="Cole o ID do canal", max_length=25)

    def __init__(self, parent):
        super().__init__(timeout=300)
        self.parent_view = parent

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.channel_id.value.strip()
        channel = self.parent_view.guild.get_channel(int(raw)) if raw.isdigit() else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Não encontrei um canal de texto com esse ID neste servidor.", ephemeral=True)
            return
        self.parent_view.channel_id = channel.id
        save_config_value("hierarchy_panel_channel_id", str(channel.id))
        await interaction.response.send_message(embed=self.parent_view.embed(), view=self.parent_view, ephemeral=True)


class HierarchyFinalChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent):
        self.parent_view = parent
        super().__init__(placeholder="Selecione o canal de publicação", channel_types=[discord.ChannelType.text], min_values=1, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.channel_id = self.values[0].id
        save_config_value("hierarchy_panel_channel_id", str(self.parent_view.channel_id))
        await interaction.response.edit_message(embed=self.parent_view.embed(), view=self.parent_view)


class HierarchyFinalChannelView(View):
    def __init__(self, guild):
        super().__init__(timeout=900)
        self.guild = guild
        raw = setting("hierarchy_panel_channel_id", "0")
        self.channel_id = int(raw) if raw.isdigit() and int(raw) else None
        self.add_item(HierarchyFinalChannelSelect(self))

    def embed(self):
        channel = self.guild.get_channel(self.channel_id) if self.channel_id else None
        e = discord.Embed(
            title="Etapa 4 — Canal de publicação",
            description="Escolha o canal pelo menu ou informe o ID manualmente. Ao publicar, o bot limpará as mensagens do canal escolhido e enviará a hierarquia atualizada.",
            color=discord.Color.blurple()
        )
        e.add_field(name="Canal selecionado", value=channel.mention if channel else "Nenhum canal selecionado", inline=False)
        e.set_footer(text="O bot precisa de Ver canal, Enviar mensagens, Incorporar links e Gerenciar mensagens.")
        return e

    @discord.ui.button(label="Informar ID manualmente", style=discord.ButtonStyle.secondary, row=1)
    async def manual(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HierarchyManualChannelModal(self))

    @discord.ui.button(label="Voltar às tags", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = HierarchyTagConfigView(self.guild, [rid for _priority, rid, _tag in hierarchy_rows()], interaction.user.id)
        view.tags = [tag for _priority, _rid, tag in hierarchy_rows()]
        await interaction.response.edit_message(embed=view.embed(), view=view)

    @discord.ui.button(label="Reiniciar esta etapa", style=discord.ButtonStyle.danger, row=2)
    async def restart(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.channel_id = None
        save_config_value("hierarchy_panel_channel_id", "0")
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Limpar canal e publicar hierarquia", style=discord.ButtonStyle.success, emoji="🚀", row=3)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = self.guild.get_channel(self.channel_id) if self.channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Selecione um canal válido antes de publicar.", ephemeral=True)
            return
        if not hierarchy_rows():
            await interaction.response.send_message("A configuração dos cargos não foi encontrada. Refaça as etapas anteriores.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async for message in channel.history(limit=None):
                try:
                    await message.delete()
                except discord.Forbidden:
                    raise
                except discord.HTTPException:
                    continue
        except discord.Forbidden:
            await interaction.followup.send("Não tenho permissão para limpar esse canal. Conceda Gerenciar mensagens ao bot.", ephemeral=True)
            return
        await publish_configured_panel(interaction, "hierarchy", channel_override=channel.id)


class ConfigPanelView(View):
    def __init__(self):
        super().__init__(timeout=900)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not (interaction.guild and isinstance(interaction.user, discord.Member) and (
            interaction.user.guild_permissions.administrator or has_leadership(interaction)
            or any(role.id == DEVELOPER_ROLE_ID for role in interaction.user.roles)
        )):
            await interaction.response.send_message("Somente desenvolvedor, liderança ou administradores podem alterar a configuração.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="WL", style=discord.ButtonStyle.primary, emoji="📋", row=0)
    async def wl(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ConfigWLPanelModal())

    @discord.ui.button(label="Hierarquia", style=discord.ButtonStyle.primary, emoji="🏛️", row=0)
    async def hierarchy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use este painel dentro do servidor.", ephemeral=True)
            return
        guide = discord.Embed(
            title="🏛️ Guia de configuração da hierarquia",
            description=(
                "Configure a hierarquia em quatro etapas: **1)** quantidade de cargos; "
                "**2)** cargos, do mais alto ao mais baixo; **3)** tag/abreviação de cada cargo; "
                "**4)** canal de publicação.\n\n"
                "O bot identifica o maior cargo configurado do membro e atualiza o nickname para "
                "**[ TAG ] NOME - ID**. Os dados ficam salvos no SQLite.\n\n"
                "Você também pode apagar a configuração anterior e limpar as mensagens do canal "
                "da hierarquia para começar do zero."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=guide, view=HierarchyGuideV2(interaction.guild), ephemeral=True)

    @discord.ui.button(label="Farm", style=discord.ButtonStyle.primary, emoji="🌾", row=0)
    async def farm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ConfigFarmPanelModal())

    @discord.ui.button(label="Publicar WL", style=discord.ButtonStyle.success, emoji="📨", row=2)
    async def publish_wl(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await publish_configured_panel(interaction, "wl")

    @discord.ui.button(label="Publicar hierarquia", style=discord.ButtonStyle.success, emoji="🏛️", row=2)
    async def publish_hierarchy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use este recurso dentro de um servidor Discord.", ephemeral=True)
            return
        view = HierarchyPublishChannelView(interaction.guild)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

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
    async def reply(content: str, **kwargs) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, **kwargs)
        else:
            await interaction.response.send_message(content, **kwargs)
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
    try:
        embed = panel_embed(kind)
        if kind == "wl":
            await target.send(embed=embed, view=RegistrationPanelView())
        elif kind == "farm":
            await target.send(embed=embed, view=FarmPanelView())
        elif kind == "hierarchy":
            try:
                await interaction.guild.chunk(cache=True)
            except Exception as exc:
                log.warning("Não consegui atualizar o cache de membros ao publicar a hierarquia: %s", exc)

            configured = []
            for priority, role_id, abbreviation in hierarchy_rows():
                role = interaction.guild.get_role(role_id)
                if role and role != interaction.guild.default_role:
                    configured.append((priority, role, abbreviation or "RANK"))
            configured.sort(key=lambda item: item[0])
            if not configured:
                await reply("Nenhum cargo está configurado para exibição. Abra `/config` > Hierarquia e selecione os cargos.", ephemeral=True)
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
                # Higher priority number means higher rank. The member is listed in only one rank.
                highest_role_id = max(owned_rank_ids, key=lambda rid: rank_priorities[rid])
                members_by_highest_rank[highest_role_id].append(member)

            panel_title = panel_setting("hierarchy_panel_title", f"🏛️ HIERARQUIA — {FACTION_NAME}")
            panel_description = panel_setting("hierarchy_panel_description", "Cargos e membros da organização.")
            total_ranks = len(ranks)
            sent_messages = 0
            # Publish in configured order: highest rank first, lowest rank last.
            for priority, role_id, abbreviation, role in ranks:
                members = sorted(
                    members_by_highest_rank.get(role_id, []),
                    key=lambda m: m.display_name.casefold(),
                )
                member_lines = [m.mention for m in members] or ["Ninguém com esse cargo."]
                chunks, buf, size = [], [], 0
                for line in member_lines:
                    if buf and size + len(line) + 1 > 3500:
                        chunks.append(buf)
                        buf, size = [], 0
                    buf.append(line)
                    size += len(line) + 1
                if buf:
                    chunks.append(buf)
                for chunk_index, chunk in enumerate(chunks, start=1):
                    suffix = f" · continuação {chunk_index}/{len(chunks)}" if len(chunks) > 1 else ""
                    rank_title = f"📋 | {role.name.upper()} - [{len(members)}] membros{suffix}"
                    body = f"# {role.mention}\n" + "\n".join(chunk)
                    body += f"\n\n**Atualizado Automaticamente | Última alteração: {datetime.now().strftime('%d/%m/%Y, %H:%M')}**"
                    role_embed = discord.Embed(
                        title=rank_title[:256], description=body[:4096],
                        color=discord.Color.from_rgb(76, 86, 180), timestamp=datetime.now(timezone.utc),
                    )
                    await target.send(embed=role_embed)
                    sent_messages += 1
            # Aplica as tags recém-configuradas também aos membros que já estavam no servidor.
            for current_member in interaction.guild.members:
                if not current_member.bot:
                    await sync_configured_member_nickname(current_member, "Nova hierarquia publicada")
            await reply(
                f"✅ Hierarquia publicada em {target.mention}: **{total_ranks} cargos**, cada um em mensagem separada. "
                f"O cargo mais alto aparece primeiro; o nº 1 (mais baixo) aparece por último. ({sent_messages} mensagem(ns)).",
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
            await target.send(embed=embed)
        elif kind == "leader":
            await target.send(embed=embed, view=LeadershipPanelView())
        else:
            await target.send(embed=embed)
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
    if _render_http_runner is not None:
        return  # Evita abrir duas vezes a mesma porta durante uma recuperação.
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


@bot.event
async def setup_hook() -> None:  # type: ignore[override]
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


async def sync_configured_member_nickname(member: discord.Member, reason: str = "Sincronização automática da hierarquia") -> None:
    """Atualiza apenas membros com WL aprovada, preservando nome RP e ID cadastrados."""
    with db() as con:
        row = con.execute(
            "SELECT character_name,game_id FROM members WHERE discord_id=? AND status='ACTIVE'",
            (str(member.id),),
        ).fetchone()
    if not row:
        return
    rank_role, abbreviation = highest_configured_rank(member)
    if not rank_role:
        return
    with db() as con:
        con.execute("UPDATE members SET rank_name=? WHERE discord_id=?", (rank_role.name, str(member.id)))
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





class FarmItemConfigModal(discord.ui.Modal, title="Adicionar item ao Farm"):
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
            await interaction.response.send_message("Informe um valor em reais e uma quantidade de pontos válidos.", ephemeral=True)
            return
        if not name or cents <= 0 or points < 1 or cents > 100000000:
            await interaction.response.send_message("Confira o nome, o valor (maior que zero) e os pontos (mínimo 1).", ephemeral=True)
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


class FarmItemSetupV2(discord.ui.LayoutView):
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
            await interaction.response.send_message("Cadastre pelo menos um item antes de continuar.", ephemeral=True)
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
            )
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
                "Não há opções disponíveis. Confira os canais/cargos e as permissões do bot.",
                ephemeral=True,
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
            )
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
            )
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
                "Use esta configuração dentro do servidor.", ephemeral=True
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
                "Ainda faltam configurações obrigatórias válidas:\n"
                + "\n".join(f"• {label}" for label in missing)
                + "\nSelecione cada campo acima para definir ou corrigir.",
                ephemeral=True,
            )
            return

        await publish_configured_panel(interaction, "farm")


class ConfigFarmChannelsV2(discord.ui.LayoutView):
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
        self.add_item(discord.ui.Container(*parts))

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


class HierarchyGuideV2(discord.ui.LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=900)
        self.guild = guild
        container = discord.ui.Container(
            discord.ui.TextDisplay(
                "## 🏛️ Hierarquia\n"
                "A configuração é feita do **cargo mais baixo para o mais alto**.\n\n"
                "**1.** Informe a quantidade de cargos.\n"
                "**2.** Selecione cada cargo ou informe seu ID.\n"
                "**3.** Defina a abreviação de cada cargo.\n"
                "**4.** Escolha o canal e publique.\n\n"
                "O bot usa sempre o cargo mais alto que o membro possuir e atualiza o apelido no formato `[TAG] NOME - ID`."
            ),
            discord.ui.Separator(),
            discord.ui.ActionRow(
                discord.ui.Button(label="Configurar do zero", emoji="⚙️", style=discord.ButtonStyle.success, custom_id="fac:hierarchyv2:start"),
                discord.ui.Button(label="Apagar configuração", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="fac:hierarchyv2:reset"),
            ),
        )
        self.add_item(container)
        row = container.children[-1]
        row.children[0].callback = self.start
        row.children[1].callback = self.reset

    async def start(self, interaction: discord.Interaction):
        await interaction.response.send_modal(HierarchyCountModal(self.guild))

    async def reset(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "⚠️ **Confirma a exclusão?** Serão apagados os cargos, tags, canal salvo e as mensagens publicadas da hierarquia.",
            view=HierarchyResetConfirmView(self.guild), ephemeral=True
        )


class ConfigPanelV2(discord.ui.LayoutView):
    """Painel principal de configuração usando Discord Components V2."""
    def __init__(self):
        super().__init__(timeout=900)
        container = discord.ui.Container(
            discord.ui.TextDisplay("# ⚙️ GERENTE-DE-FAC\nConfigure os módulos da facção por etapas.\n\n**Escolha o que deseja configurar:**"),
            discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://painel_configuracao.png", description="Painel de configuração GERENTE-DE-FAC")),
            discord.ui.Separator(),
            discord.ui.TextDisplay("📋 **WL** — painel e cadastro de membros\n🏛️ **Hierarquia** — cargos, tags e publicação\n🌾 **Farm** — itens, valores, canais e cobranças\n🧾 **Publicação** — enviar os painéis configurados"),
            discord.ui.Separator(),
            discord.ui.ActionRow(
                discord.ui.Button(label="WL", emoji="📋", style=discord.ButtonStyle.primary, custom_id="fac:configv2:wl"),
                discord.ui.Button(label="Hierarquia", emoji="🏛️", style=discord.ButtonStyle.primary, custom_id="fac:configv2:hierarchy"),
                discord.ui.Button(label="Farm", emoji="🌾", style=discord.ButtonStyle.primary, custom_id="fac:configv2:farm"),
            ),
            discord.ui.ActionRow(
                discord.ui.Button(label="Publicar WL", emoji="📨", style=discord.ButtonStyle.success, custom_id="fac:configv2:publish-wl"),
                discord.ui.Button(label="Publicar hierarquia", emoji="🏛️", style=discord.ButtonStyle.success, custom_id="fac:configv2:publish-hierarchy"),
                discord.ui.Button(label="Publicar Farm", emoji="🌾", style=discord.ButtonStyle.success, custom_id="fac:configv2:publish-farm"),
            ),
        )
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
                await interaction.response.send_modal(ConfigWLPanelModal())
            elif action == "fac:configv2:hierarchy":
                if not interaction.guild:
                    await interaction.response.send_message("Use no servidor.", ephemeral=True)
                    return
                # Components V2 não aceita o campo legado "content".
                # Todo o texto da tela fica dentro do LayoutView/Container.
                await interaction.response.send_message(
                    view=HierarchyGuideV2(interaction.guild), ephemeral=True
                )
            elif action == "fac:configv2:farm":
                await interaction.response.send_message(view=FarmItemSetupV2(), ephemeral=True)
            elif action == "fac:configv2:publish-wl":
                await publish_configured_panel(interaction, "wl")
            elif action == "fac:configv2:publish-hierarchy":
                if not interaction.guild:
                    await interaction.response.send_message("Use no servidor.", ephemeral=True)
                    return
                view = HierarchyPublishChannelView(interaction.guild)
                await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)
            elif action == "fac:configv2:publish-farm":
                await publish_configured_panel(interaction, "farm")
        return callback


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
        await interaction.response.send_message("Esse usuário ainda não possui cadastro aprovado.", ephemeral=True)
        return
    embed = discord.Embed(title=f"👤 Perfil de {row['character_name']}", color=discord.Color.blurple())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Discord", value=target.mention, inline=True)
    embed.add_field(name="Passaporte", value=row["game_id"], inline=True)
    embed.add_field(name="Hierarquia registrada", value=row["rank_name"], inline=True)
    embed.add_field(name="Farm total", value=f"{total['qty']} unidades · {total['pts']} pontos", inline=True)
    embed.add_field(name="Advertências registradas", value=str(warnings), inline=True)
    embed.add_field(name="Entrada", value=row["joined_at"][:10], inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="membros", description="Lista os membros ativos cadastrados")
async def membros(interaction: discord.Interaction) -> None:
    with db() as con:
        rows = con.execute("SELECT character_name,game_id,rank_name,discord_id FROM members WHERE status='ACTIVE' ORDER BY character_name COLLATE NOCASE LIMIT 40").fetchall()
    if not rows:
        await interaction.response.send_message("Ainda não há membros cadastrados.", ephemeral=True)
        return
    lines = [f"• **{r['character_name']} | {r['game_id']}** — {r['rank_name']} — <@{r['discord_id']}>" for r in rows]
    embed = discord.Embed(title=f"👥 Membros ativos ({len(rows)})", description="\n".join(lines), color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="promover", description="Adiciona um cargo e registra a promoção")
@app_commands.describe(membro="Membro a promover", cargo="Cargo que será adicionado", motivo="Motivo da promoção")
async def promover(interaction: discord.Interaction, membro: discord.Member, cargo: discord.Role, motivo: str = "Reconhecimento") -> None:
    if not await deny_unless_leader(interaction): return
    if interaction.user.id != interaction.guild.owner_id and cargo >= interaction.user.top_role:
        await interaction.response.send_message("Você não pode atribuir um cargo igual ou superior ao seu.", ephemeral=True); return
    try:
        await membro.add_roles(cargo, reason=f"Promoção por {interaction.user}: {motivo}")
        with db() as con:
            con.execute("UPDATE members SET rank_name=? WHERE discord_id=?", (cargo.name, str(membro.id)))
        log_action(interaction.user.id, "PROMOCAO", membro.id, motivo, f"Cargo adicionado: {cargo.name}")
        await interaction.response.send_message(f"✅ {membro.mention} recebeu o cargo **{cargo.name}**.", ephemeral=True)
        await send_channel_log(interaction.guild, "📈 Promoção registrada", f"Liderança: {interaction.user.mention}\nMembro: {membro.mention}\nCargo: {cargo.mention}\nMotivo: {motivo}", color=discord.Color.green())
        await send_channel_log(interaction.guild, "🎉 Nova promoção", f"{membro.mention} foi promovido para {cargo.mention}.", public=True, color=discord.Color.green())
    except discord.Forbidden:
        await interaction.response.send_message("Não tenho permissão para atribuir esse cargo. Confira a hierarquia de cargos do bot.", ephemeral=True)

@bot.tree.command(name="rebaixar", description="Remove um cargo e opcionalmente atribui outro")
@app_commands.describe(membro="Membro a rebaixar", cargo_atual="Cargo que será removido", novo_cargo="Novo cargo", motivo="Motivo do rebaixamento")
async def rebaixar(interaction: discord.Interaction, membro: discord.Member, cargo_atual: discord.Role, novo_cargo: discord.Role, motivo: str = "Reajuste hierárquico") -> None:
    if not await deny_unless_leader(interaction): return
    if interaction.user.id != interaction.guild.owner_id and membro.top_role >= interaction.user.top_role:
        await interaction.response.send_message("Você não pode gerenciar alguém com cargo igual ou superior ao seu.", ephemeral=True); return
    try:
        await membro.remove_roles(cargo_atual, reason=f"Rebaixamento por {interaction.user}: {motivo}")
        await membro.add_roles(novo_cargo, reason=f"Rebaixamento por {interaction.user}: {motivo}")
        with db() as con:
            con.execute("UPDATE members SET rank_name=? WHERE discord_id=?", (novo_cargo.name, str(membro.id)))
        log_action(interaction.user.id, "REBAIXAMENTO", membro.id, motivo, f"{cargo_atual.name} -> {novo_cargo.name}")
        await interaction.response.send_message(f"✅ {membro.mention} agora possui o cargo **{novo_cargo.name}**.", ephemeral=True)
        await send_channel_log(interaction.guild, "📉 Rebaixamento registrado", f"Liderança: {interaction.user.mention}\nMembro: {membro.mention}\nCargo removido: {cargo_atual.mention}\nNovo cargo: {novo_cargo.mention}\nMotivo: {motivo}", color=discord.Color.orange())
        await send_channel_log(interaction.guild, "🔻 Atualização de hierarquia", f"O cargo de {membro.mention} foi atualizado para {novo_cargo.mention}.", public=True, color=discord.Color.orange())
    except discord.Forbidden:
        await interaction.response.send_message("Não tenho permissão para alterar esses cargos.", ephemeral=True)

@bot.tree.command(name="farm-item", description="Cria ou atualiza um item de farm (liderança)")
@app_commands.describe(nome="Nome do item", pontos="Pontos por unidade", meta="Meta em unidades para o ciclo")
async def farm_item(interaction: discord.Interaction, nome: str, pontos: app_commands.Range[int, 1, 100000], meta: app_commands.Range[int, 0, 1000000] = 0) -> None:
    if not await deny_unless_leader(interaction): return
    name = nome.strip()[:60]
    with db() as con:
        con.execute("INSERT INTO farm_items(name,points,target,active) VALUES(?,?,?,1) ON CONFLICT(name) DO UPDATE SET points=excluded.points,target=excluded.target,active=1", (name, pontos, meta))
    log_action(interaction.user.id, "FARM_ITEM_CONFIG", None, "", f"{name}: {pontos} ponto(s), meta {meta}")
    await interaction.response.send_message(f"Item **{name}** configurado: **{pontos}** ponto(s) por unidade; meta: **{meta}**.", ephemeral=True)

@bot.tree.command(name="farm-preco", description="Define o valor unitário em reais de um item de farm")
@app_commands.describe(nome="Nome exato do item cadastrado", valor="Valor em reais por unidade, por exemplo 2.50")
async def farm_preco(interaction: discord.Interaction, nome: str, valor: app_commands.Range[float, 0.01, 1000000.0]) -> None:
    if not await deny_unless_leader(interaction):
        return
    cents = int(round(float(valor) * 100))
    with db() as con:
        cur = con.execute("UPDATE farm_items SET unit_price_cents=? WHERE name=? COLLATE NOCASE AND active=1", (cents, nome.strip()))
    if not cur.rowcount:
        await interaction.response.send_message("Não encontrei esse item ativo. Cadastre primeiro com `/farm-item`.", ephemeral=True)
        return
    log_action(interaction.user.id, "FARM_PRECO_CONFIG", None, "", f"{nome.strip()}: R$ {cents/100:.2f} por unidade")
    await interaction.response.send_message(f"Valor de **{nome.strip()}** atualizado para **R$ {cents/100:.2f} por unidade**.", ephemeral=True)

@bot.tree.command(name="farm-item-desativar", description="Desativa um item de farm")
async def farm_item_desativar(interaction: discord.Interaction, nome: str) -> None:
    if not await deny_unless_leader(interaction): return
    with db() as con:
        cur = con.execute("UPDATE farm_items SET active=0 WHERE name=? COLLATE NOCASE", (nome.strip(),))
    if cur.rowcount == 0:
        await interaction.response.send_message("Não encontrei esse item de farm.", ephemeral=True); return
    log_action(interaction.user.id, "FARM_ITEM_DESATIVADO", None, "", nome)
    await interaction.response.send_message(f"Item **{nome}** desativado.", ephemeral=True)

@bot.tree.command(name="farm", description="Informa como registrar farm pelo painel com comprovante")
async def farm(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "Para manter a auditoria, o registro agora deve ser feito pelo **painel de Farm**: clique em **REGISTRAR FARM**, selecione o item, informe a quantidade e envie a imagem de prova no canal. Registros sem comprovante não são contabilizados.",
        ephemeral=True)

@bot.tree.command(name="farm-comprovante", description="Consulta um comprovante de farm salvo no banco")
@app_commands.describe(registro="Número do registro de comprovante")
async def farm_comprovante(interaction: discord.Interaction, registro: app_commands.Range[int, 1, 100000000]) -> None:
    if not await deny_unless_leader(interaction): return
    with db() as con:
        row = con.execute("SELECT * FROM farm_proofs WHERE id=?", (registro,)).fetchone()
    if not row:
        await interaction.response.send_message("Não encontrei esse comprovante.", ephemeral=True); return
    import io
    file = discord.File(io.BytesIO(row["image_data"]), filename=row["image_filename"] or f"comprovante-{registro}.png")
    embed = discord.Embed(title=f"🌾 Comprovante de farm #{registro}", description=f"Membro: <@{row['discord_id']}>\nNome RP: **{row['character_name']}**\nID FiveM: **{row['game_id']}**\nFarm: **{row['item_name'] or 'Não informado'}**\nQuantidade: **{row['quantity'] or 0}**\nPontos: **{row['points'] or 0}**\nStatus: **{row['status']}**\nData: {row['created_at']}", color=discord.Color.green() if row['status'] == 'APPROVED' else discord.Color.gold())
    await interaction.response.send_message(embed=embed, file=file, ephemeral=True)

@bot.tree.command(name="farm-resumo", description="Mostra o resumo de farm do ciclo atual")
@app_commands.describe(membro="Membro para consultar; vazio para você")
async def farm_resumo(interaction: discord.Interaction, membro: Optional[discord.Member] = None) -> None:
    target = membro or interaction.user
    with db() as con:
        rows = con.execute("SELECT item_name,SUM(quantity) qty,SUM(points) pts FROM farm_entries WHERE discord_id=? GROUP BY item_name ORDER BY pts DESC", (str(target.id),)).fetchall()
        total = con.execute("SELECT COALESCE(SUM(quantity),0) qty,COALESCE(SUM(points),0) pts FROM farm_entries WHERE discord_id=?", (str(target.id),)).fetchone()
    if not rows:
        await interaction.response.send_message("Ainda não há registros de farm para esse membro.", ephemeral=True); return
    embed = discord.Embed(title=f"📊 Resumo de farm — {target.display_name}", color=discord.Color.green())
    embed.description = "\n".join(f"• **{r['item_name']}**: {r['qty']} unidade(s) · {r['pts']} ponto(s)" for r in rows[:20])
    embed.add_field(name="Total", value=f"{total['qty']} unidades · {total['pts']} pontos", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="farm-ranking", description="Mostra o ranking de farm")
async def farm_ranking(interaction: discord.Interaction) -> None:
    with db() as con:
        rows = con.execute("SELECT discord_id,SUM(points) pts,SUM(quantity) qty FROM farm_entries GROUP BY discord_id ORDER BY pts DESC LIMIT 15").fetchall()
    if not rows:
        await interaction.response.send_message("Ainda não há registros de farm.", ephemeral=True); return
    lines = [f"**{i}.** <@{r['discord_id']}> — **{r['pts']} pts** ({r['qty']} unidades)" for i, r in enumerate(rows, 1)]
    await interaction.response.send_message(embed=discord.Embed(title="🏆 Ranking de farm", description="\n".join(lines), color=discord.Color.gold()), ephemeral=True)

@bot.tree.command(name="meta-farm", description="Define a meta global de farm em unidades")
async def meta_farm(interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 1000000]) -> None:
    if not await deny_unless_leader(interaction): return
    with db() as con:
        con.execute("INSERT INTO settings(key,value) VALUES('global_farm_goal',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(quantidade),))
    log_action(interaction.user.id, "META_FARM_CONFIG", None, "", str(quantidade))
    await interaction.response.send_message(f"Meta global de farm definida para **{quantidade} unidades**.", ephemeral=True)

@bot.tree.command(name="farm-meta-status", description="Compara o farm total com a meta global")
async def farm_meta_status(interaction: discord.Interaction) -> None:
    goal = int(setting("global_farm_goal", "0"))
    with db() as con:
        total = con.execute("SELECT COALESCE(SUM(quantity),0) qty,COALESCE(SUM(points),0) pts FROM farm_entries").fetchone()
    if goal <= 0:
        await interaction.response.send_message("A liderança ainda não configurou uma meta global com `/meta-farm`.", ephemeral=True); return
    percent = min(100, round(total["qty"] / goal * 100))
    filled = int(percent / 10)
    bar = "🟩" * filled + "⬜" * (10-filled)
    await interaction.response.send_message(f"**Meta de farm da facção**\n{bar} **{percent}%**\nQuantidade: **{total['qty']} / {goal}** · Pontos: **{total['pts']}**", ephemeral=True)

@bot.tree.command(name="slot", description="Atribui um número de slot do baú a um membro")
async def slot(interaction: discord.Interaction, membro: discord.Member, numero: app_commands.Range[int, 1, 9999]) -> None:
    if not await deny_unless_leader(interaction): return
    try:
        with db() as con:
            row = con.execute("SELECT discord_id FROM members WHERE discord_id=?", (str(membro.id),)).fetchone()
            if not row:
                await interaction.response.send_message("Esse membro ainda não tem cadastro aprovado.", ephemeral=True); return
            con.execute("UPDATE members SET slot_number=? WHERE discord_id=?", (numero, str(membro.id)))
        log_action(interaction.user.id, "SLOT_ATRIBUIDO", membro.id, "", f"Slot {numero}")
        await interaction.response.send_message(f"Slot **{numero}** atribuído a {membro.mention}.", ephemeral=True)
        if interaction.guild:
            await send_channel_log(interaction.guild, "📦 Slot de baú atualizado", f"Liderança: {interaction.user.mention}\nMembro: {membro.mention}\nSlot: **{numero}**", color=discord.Color.blurple())
    except sqlite3.IntegrityError:
        await interaction.response.send_message("Esse número de slot já está atribuído a outro membro.", ephemeral=True)

@bot.tree.command(name="slots", description="Lista os slots de baú atribuídos")
async def slots(interaction: discord.Interaction) -> None:
    if not await deny_unless_leader(interaction): return
    with db() as con:
        rows = con.execute("SELECT character_name,game_id,discord_id,slot_number FROM members WHERE status='ACTIVE' AND slot_number IS NOT NULL ORDER BY slot_number LIMIT 40").fetchall()
    if not rows:
        await interaction.response.send_message("Nenhum slot foi atribuído ainda.", ephemeral=True); return
    lines = [f"**Slot {r['slot_number']}** — {r['character_name']} | {r['game_id']} — <@{r['discord_id']}>" for r in rows]
    await interaction.response.send_message(embed=discord.Embed(title="📦 Slots do baú", description="\n".join(lines), color=discord.Color.blurple()), ephemeral=True)

@bot.tree.command(name="nickname", description="Atualiza o nickname RP de um membro")
async def nickname(interaction: discord.Interaction, membro: discord.Member, nome_rp: str, passaporte: str) -> None:
    if not await deny_unless_leader(interaction): return
    if not passaporte.isdigit():
        await interaction.response.send_message("O passaporte deve conter somente números.", ephemeral=True); return
    try:
        new_nick = f"[ M ] {nome_rp.strip().upper()} - {passaporte}"[:32]
        await membro.edit(nick=new_nick, reason=f"Nickname atualizado por {interaction.user}")
        with db() as con:
            con.execute("UPDATE members SET character_name=?,game_id=? WHERE discord_id=?", (nome_rp.strip(), passaporte, str(membro.id)))
        log_action(interaction.user.id, "NICKNAME_ATUALIZADO", membro.id, "", new_nick)
        await interaction.response.send_message(f"Nickname atualizado para **{new_nick}**.", ephemeral=True)
        await send_channel_log(interaction.guild, "🏷️ Nickname atualizado", f"Liderança: {interaction.user.mention}\nMembro: {membro.mention}\nNovo nickname: **{new_nick}**", color=discord.Color.blurple())
    except sqlite3.IntegrityError:
        await interaction.response.send_message("Esse passaporte já está vinculado a outro membro.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("Não tenho permissão para editar esse nickname.", ephemeral=True)

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
    await interaction.response.send_message(f"Ação **{tipo.value}** registrada para {membro.mention}.", ephemeral=True)
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
        await interaction.response.send_message("Você não pode aplicar advertência em alguém com cargo igual ou superior ao seu.", ephemeral=True); return
    level = await add_warning(interaction.guild, membro, interaction.user, motivo)
    if level == "ADV 3" and AUTO_PD_ON_ADV3:
        try:
            await membro.send(f"Você recebeu ADV 3 na organização **{FACTION_NAME}** e será removido conforme a configuração automática. Motivo: {motivo}")
        except discord.HTTPException:
            pass
        try:
            await membro.kick(reason=f"ADV 3 automática por {interaction.user}: {motivo}")
            with db() as con:
                con.execute("UPDATE members SET status='REMOVED' WHERE discord_id=?", (str(membro.id),))
            log_action(interaction.user.id, "PD_AUTOMATICA_ADV3", membro.id, motivo, "Removido automaticamente por configuração")
            await send_channel_log(interaction.guild, "💀 PD automático por ADV 3", f"Liderança: {interaction.user.mention}\nMembro: {membro.mention}\nMotivo: {motivo}", color=discord.Color.dark_red())
            await interaction.response.send_message("⚠️ ADV 3 registrada e PD automático aplicado conforme configuração.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("ADV 3 registrada, mas não consegui aplicar PD automático por falta de permissão.", ephemeral=True)
    elif level == "ADV 3":
        try:
            await membro.send(f"Você recebeu ADV 3 na organização **{FACTION_NAME}**. Motivo: {motivo}. A liderança deverá revisar seu caso.")
        except discord.HTTPException:
            pass
        await interaction.response.send_message("⚠️ ADV 3 registrada. O PD automático está desativado; a liderança pode revisar o caso e usar `/pd`.", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ Advertência aplicada: **{level}**.", ephemeral=True)

@bot.tree.command(name="pd", description="Aplica perda de direitos e remove o membro do Discord")
async def pd(interaction: discord.Interaction, membro: discord.Member, motivo: str) -> None:
    if not await deny_unless_leader(interaction): return
    if interaction.user.id != interaction.guild.owner_id and membro.top_role >= interaction.user.top_role:
        await interaction.response.send_message("Você não pode aplicar PD em alguém com cargo igual ou superior ao seu.", ephemeral=True); return
    try:
        try:
            await membro.send(f"Você recebeu PD na organização **{FACTION_NAME}**. Motivo: {motivo}")
        except discord.HTTPException:
            pass
        await membro.kick(reason=f"PD por {interaction.user}: {motivo}")
        with db() as con:
            con.execute("UPDATE members SET status='REMOVED' WHERE discord_id=?", (str(membro.id),))
        log_action(interaction.user.id, "PD", membro.id, motivo, "Membro removido do servidor")
        await interaction.response.send_message(f"PD aplicado em {membro.display_name}.", ephemeral=True)
        await send_channel_log(interaction.guild, "💀 PD aplicado", f"Liderança: {interaction.user.mention}\nMembro: {membro.mention}\nMotivo: {motivo}", color=discord.Color.dark_red())
        await send_channel_log(interaction.guild, "💀 Membro desligado", f"Um membro recebeu PD. Motivo registrado pela liderança: {motivo}", public=True, color=discord.Color.dark_grey())
    except discord.Forbidden:
        await interaction.response.send_message("Não tenho permissão para expulsar esse membro.", ephemeral=True)

@bot.tree.command(name="blacklist-add", description="Adiciona um passaporte à blacklist")
async def blacklist_add(interaction: discord.Interaction, passaporte: str, motivo: str) -> None:
    if not await deny_unless_leader(interaction): return
    if not passaporte.isdigit():
        await interaction.response.send_message("O passaporte deve conter somente números.", ephemeral=True); return
    with db() as con:
        con.execute("INSERT INTO blacklist(game_id,reason,added_by,created_at) VALUES(?,?,?,?) ON CONFLICT(game_id) DO UPDATE SET reason=excluded.reason,added_by=excluded.added_by,created_at=excluded.created_at", (passaporte, motivo, str(interaction.user.id), now_iso()))
    log_action(interaction.user.id, "BLACKLIST_ADD", None, motivo, f"Passaporte {passaporte}")
    if interaction.guild:
        await send_channel_log(interaction.guild, "⛔ BLACKLIST adicionada", f"Liderança: {interaction.user.mention}\nPassaporte: `{passaporte}`\nMotivo: {motivo}", color=discord.Color.dark_red())
    await interaction.response.send_message(f"Passaporte **{passaporte}** adicionado à blacklist.", ephemeral=True)

@bot.tree.command(name="blacklist-check", description="Consulta um passaporte na blacklist")
async def blacklist_check(interaction: discord.Interaction, passaporte: str) -> None:
    if not await deny_unless_leader(interaction): return
    with db() as con:
        row = con.execute("SELECT * FROM blacklist WHERE game_id=?", (passaporte.strip(),)).fetchone()
    if not row:
        await interaction.response.send_message(f"Passaporte **{passaporte}** não está na blacklist.", ephemeral=True); return
    await interaction.response.send_message(f"⛔ **Passaporte {passaporte} está na blacklist**\nMotivo: {row['reason']}\nAdicionado por: <@{row['added_by']}>\nData: {row['created_at']}", ephemeral=True)

@bot.tree.command(name="logs", description="Consulta as ações recentes da liderança")
@app_commands.describe(quantidade="Quantidade de registros, máximo 20")
async def logs(interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 20] = 10) -> None:
    if not await deny_unless_leader(interaction): return
    with db() as con:
        rows = con.execute("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (quantidade,)).fetchall()
    if not rows:
        await interaction.response.send_message("Ainda não há ações registradas.", ephemeral=True); return
    lines = []
    for r in rows:
        target = f" · alvo <@{r['target_id']}>" if r["target_id"] else ""
        reason = f" · motivo: {r['reason']}" if r["reason"] else ""
        details = f" · {r['details']}" if r["details"] else ""
        lines.append(f"`#{r['id']}` **{r['action']}** por <@{r['actor_id']}>{target}{reason}{details}\n< t:{int(datetime.fromisoformat(r['created_at']).timestamp())}:R >".replace("< t:", "<t:").replace(":R >", ":R>"))
    embed = discord.Embed(title="🧾 Logs administrativos", description="\n\n".join(lines)[:4000], color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, ephemeral=True)

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
    await interaction.response.send_message(f"**Marcos de {target.mention}**\n" + "\n".join(achievements), ephemeral=True)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    log.error("Erro em comando: %s", error, exc_info=error)
    message = "Ocorreu um erro ao executar o comando. Confira a configuração e as permissões do bot."
    if isinstance(error, app_commands.MissingPermissions):
        message = "Você não tem permissão para usar esse comando."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass

async def run_bot_with_recovery() -> None:
    """Mantém o processo vivo durante bloqueios 429 temporários no login.

    Um 429 global pode ocorrer antes de o gateway conectar. Sem este laço,
    bot.run() encerra o processo e o Render pode reiniciá-lo em sequência,
    repetindo a tentativa enquanto o bloqueio ainda está ativo.
    """
    retry_delay = 30
    max_retry_delay = 900  # 15 minutos; evita martelar a API durante bloqueios longos.

    # Inicia o healthcheck antes do login para o Render não interpretar a
    # espera por rate limit como um processo morto em Web Service.
    await start_render_health_server()

    while not bot.is_closed():
        try:
            await bot.start(TOKEN, reconnect=True)
            return  # Saída normal (por exemplo, encerramento solicitado).
        except discord.HTTPException as exc:
            if exc.status != 429:
                raise
            log.error(
                "Discord recusou o login com HTTP 429 (limite global). "
                "Nova tentativa em %s segundos. Não reinicie manualmente o serviço.",
                retry_delay,
            )
            # Descarta a sessão HTTP da tentativa anterior antes de tentar de novo.
            try:
                await bot.http.close()
            except Exception:
                log.debug("Não foi possível fechar a sessão HTTP anterior.", exc_info=True)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)
        except (OSError, asyncio.TimeoutError) as exc:
            log.warning(
                "Falha temporária de rede ao iniciar o bot (%s). Nova tentativa em %s segundos.",
                exc, retry_delay,
            )
            try:
                await bot.http.close()
            except Exception:
                log.debug("Não foi possível fechar a sessão HTTP anterior.", exc_info=True)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)


async def main() -> None:
    if not TOKEN or TOKEN == "SEU_TOKEN_AQUI":
        raise SystemExit("Configure DISCORD_TOKEN nas variáveis de ambiente do Render ou no arquivo .env.")
    init_db()
    remove_legacy_gtt_branding()
    await run_bot_with_recovery()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Encerramento solicitado pelo usuário.")
