"""Bot consolidado para gerenciamento de facções FiveM RP via Discord.
Configure as variáveis de ambiente no arquivo .env antes de iniciar.
"""
from __future__ import annotations

import os
import sqlite3
import logging
import asyncio
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
FACTION_NAME = os.getenv("FACTION_NAME", "Facção RP")
DB_PATH = os.getenv("DATABASE_PATH", "fac_control.sqlite3")

MEMBER_ROLE_ID = int(os.getenv("MEMBER_ROLE_ID", "0") or 0)
RECRUIT_ROLE_ID = int(os.getenv("RECRUIT_ROLE_ID", "0") or 0)
LEADER_ROLE_ID = int(os.getenv("LEADER_ROLE_ID", "0") or 0)
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
            active INTEGER NOT NULL DEFAULT 1
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
            created_at TEXT NOT NULL
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
            review_message_id TEXT DEFAULT ''
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


def is_active_member(discord_id: int) -> bool:
    with db() as con:
        row = con.execute("SELECT status FROM members WHERE discord_id=?", (str(discord_id),)).fetchone()
    return bool(row and row["status"] == "ACTIVE")

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
        embed.description = "O recrutador selecionado tem **5 minutos** para aprovar ou recusar. Ninguém mais pode decidir esta solicitação."
        embed.add_field(name="Solicitante", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
        embed.add_field(name="Nome no RP", value=name, inline=True)
        embed.add_field(name="ID FiveM", value=gid, inline=True)
        embed.add_field(name="Recrutador escolhido", value=f"{recruiter.mention} (`{recruiter.id}`)", inline=False)
        embed.add_field(name="Prazo", value=f"Termina <t:{int(deadline.timestamp())}:R>", inline=False)
        embed.set_footer(text=f"request_id={request_id}")
        try:
            msg = await channel.send(content=recruiter.mention, embed=embed, view=RegistrationApprovalView())
            with db() as con:
                con.execute("UPDATE registration_requests SET message_id=? WHERE id=?", (str(msg.id), request_id))
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
        await interaction.response.send_message("Primeiro, selecione o recrutador que deseja escolher para sua WL. Somente ele poderá aprovar ou recusar.", view=RecruiterSelectView(interaction.guild), ephemeral=True)

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
        if str(interaction.user.id) != str(request["selected_recruiter_id"] or request["recruiter"]):
            await interaction.response.send_message("Somente o recrutador selecionado nesta WL pode aprovar ou recusar.", ephemeral=True); return
        # Atualização condicional impede que dois cliques processem a mesma WL.
        cur = con.execute("UPDATE registration_requests SET status=?,processed_by=?,processed_at=?,reason=? WHERE id=? AND status='PENDING'",
                          ("APPROVED" if approve else "REJECTED", str(interaction.user.id), now_iso(), reason, request_id))
        if cur.rowcount != 1:
            await interaction.response.send_message("Essa solicitação já foi processada.", ephemeral=True); return
    member = await fetch_member(interaction.guild, int(request["discord_id"]))
    if approve and member:
        try:
            role_id = int(setting("member_role_id", str(MEMBER_ROLE_ID)) or 0)
            role = interaction.guild.get_role(role_id) if role_id else None
            if role:
                await member.add_roles(role, reason="WL aprovada")
            # Patente mais alta e abreviação configurável por cargo; fallback seguro para M.
            rank_role, abbreviation = highest_configured_rank(member)
            prefix = abbreviation or "M"
            nickname = f"[ {prefix} ] {request['character_name'].upper()} - {request['game_id']}"[:32]
            await member.edit(nick=nickname, reason=f"WL aprovada por recrutador {interaction.user}")
        except discord.Forbidden:
            log.warning("Sem permissão para atualizar cargo/nickname do membro %s", member.id)
        with db() as con:
            con.execute("""INSERT INTO members(discord_id,discord_name,character_name,game_id,recruiter,rank_name,joined_at,status)
                VALUES(?,?,?,?,?,?,?,'ACTIVE') ON CONFLICT(discord_id) DO UPDATE SET discord_name=excluded.discord_name,
                character_name=excluded.character_name,game_id=excluded.game_id,recruiter=excluded.recruiter,rank_name=excluded.rank_name,status='ACTIVE'""",
                (request["discord_id"], request["discord_name"], request["character_name"], request["game_id"], str(request["selected_recruiter_id"]), rank_role.name if rank_role else "Membro", now_iso()))
        log_action(interaction.user.id, "WL_APROVADA", int(request["discord_id"]), "Aprovada pelo recrutador selecionado", f"Solicitação #{request_id}")
        result_title = "✅ WHITELIST APROVADA!"
        result_text = f"Parabéns! Sua entrada em **{FACTION_NAME}** foi aprovada.\n**Nome:** {request['character_name']}\n**ID:** {request['game_id']}\n**Recrutador:** {interaction.user.mention}"
        color = discord.Color.green()
    else:
        log_action(interaction.user.id, "WL_REJEITADA", int(request["discord_id"]), reason, f"Solicitação #{request_id}")
        result_title = "❌ WHITELIST REPROVADA"
        result_text = f"Sua WL para **{FACTION_NAME}** foi recusada.\n**Motivo:** {reason or 'O prazo de 5 minutos expirou sem aprovação.'}"
        color = discord.Color.red()
    await send_channel_log(interaction.guild, result_title, f"Solicitante: <@{request['discord_id']}>\nDecisão: {interaction.user.mention}\nMotivo: {reason or ('Aprovada' if approve else 'Recusa')}", color=color)
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
    if original_message and original_message.embeds:
        embed = original_message.embeds[0]
        embed.title = f"{'✅ APROVADA' if approve else '❌ REPROVADA'} · WL #{request_id}"
        embed.color = color
        embed.add_field(name="Decisão por", value=interaction.user.mention, inline=True)
        if reason:
            embed.add_field(name="Motivo", value=reason[:1024], inline=False)
        try:
            await original_message.edit(embed=embed, view=None)
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
        if str(interaction.user.id) != str(row["selected_recruiter_id"]):
            await interaction.response.send_message("Somente o recrutador selecionado pode recusar esta WL.", ephemeral=True); return
        await interaction.response.send_modal(RegistrationRejectModal(request_id))


def highest_configured_rank(member: discord.Member):
    """Lê ranks do painel: rank_role_<role_id>=abreviação|prioridade."""
    candidates = []
    for role in member.roles:
        raw = setting(f"rank_role_{role.id}", "")
        if not raw:
            continue
        try:
            abbreviation, priority = raw.split("|", 1)
            candidates.append((int(priority), role, abbreviation.strip()))
        except (ValueError, TypeError):
            continue
    if candidates:
        _, role, abbreviation = max(candidates, key=lambda item: item[0])
        return role, abbreviation
    fallback = member.guild.get_role(MEMBER_ROLE_ID) if MEMBER_ROLE_ID else None
    return fallback, "M"

async def expire_pending_wls() -> None:
    """Reprova solicitações após cinco minutos, inclusive após reinicialização."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            with db() as con:
                rows = con.execute("SELECT id,discord_id,character_name,game_id,deadline_at,message_id FROM registration_requests WHERE status='PENDING'").fetchall()
                for row in rows:
                    try: deadline = datetime.fromisoformat(row["deadline_at"])
                    except (ValueError, TypeError): deadline = datetime.now(timezone.utc)
                    if datetime.now(timezone.utc) >= deadline:
                        cur = con.execute("UPDATE registration_requests SET status='REJECTED',processed_by='SYSTEM',processed_at=?,reason=? WHERE id=? AND status='PENDING'", (now_iso(), "Prazo de 5 minutos expirado sem aprovação", row["id"]))
                        if cur.rowcount:
                            try:
                                user = await bot.fetch_user(int(row["discord_id"]))
                                dm_embed = discord.Embed(title="⏱️ WHITELIST REPROVADA", description=f"Sua WL para **{FACTION_NAME}** foi reprovada automaticamente porque o recrutador selecionado não aprovou dentro de 5 minutos.\n**Nome:** {row['character_name']}\n**ID:** {row['game_id']}", color=discord.Color.red())
                                view = View(timeout=None)
                                view.add_item(discord.ui.Button(label="IR PARA O SERVIDOR", style=discord.ButtonStyle.link, url=invite_url()))
                                await user.send(embed=dm_embed, view=view)
                            except discord.HTTPException:
                                pass
                            guild = bot.get_guild(GUILD_ID) if GUILD_ID else None
                            if guild:
                                await send_channel_log(guild, "⏱️ WL reprovada automaticamente", f"Solicitante: <@{row['discord_id']}>\nMotivo: prazo de 5 minutos expirado.", color=discord.Color.red())
                            channel_id = int(setting("wl_channel_id", str(APPROVAL_CHANNEL_ID)) or 0)
                            channel = guild.get_channel(channel_id) if guild and channel_id else None
                            if isinstance(channel, discord.TextChannel) and row["message_id"]:
                                try:
                                    msg = await channel.fetch_message(int(row["message_id"]))
                                    if msg.embeds:
                                        embed = msg.embeds[0]; embed.title = f"⏱️ REPROVADA AUTOMATICAMENTE · WL #{row['id']}"; embed.color = discord.Color.red()
                                        await msg.edit(embed=embed, view=None)
                                except discord.HTTPException:
                                    pass
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

# ---------------- REGISTRO DE FARM COM COMPROVANTE ----------------
class FarmStartModal(Modal, title="Registrar farm"):
    character_name = TextInput(label="Seu nome no RP", placeholder="Nome cadastrado na facção", max_length=32)
    game_id = TextInput(label="Seu ID no FiveM", placeholder="Somente números", max_length=8)
    async def on_submit(self, interaction: discord.Interaction) -> None:
        gid = self.game_id.value.strip()
        if not gid.isdigit():
            await interaction.response.send_message("O ID do FiveM precisa conter somente números.", ephemeral=True); return
        with db() as con:
            con.execute("INSERT INTO farm_proof_pending(discord_id,character_name,game_id,created_at) VALUES(?,?,?,?) ON CONFLICT(discord_id) DO UPDATE SET character_name=excluded.character_name,game_id=excluded.game_id,created_at=excluded.created_at", (str(interaction.user.id), self.character_name.value.strip(), gid, now_iso()))
        try:
            await interaction.user.send("🌾 **Registro de farm iniciado**\nVocê informou seus dados. Agora envie **uma captura de tela como anexo** ou cole um **link direto para a imagem** nesta conversa privada. O comprovante será salvo no banco de dados. Limite: 8 MB; formatos PNG, JPG, JPEG, GIF ou WEBP.")
            await interaction.response.send_message("Quase pronto! Enviei uma mensagem privada para você anexar a captura ou colar o link da imagem. O registro só será concluído quando o comprovante for recebido.", ephemeral=True)
        except discord.Forbidden:
            with db() as con: con.execute("DELETE FROM farm_proof_pending WHERE discord_id=?", (str(interaction.user.id),))
            await interaction.response.send_message("Não consegui abrir sua DM. Ative mensagens privadas do servidor e tente novamente.", ephemeral=True)

class FarmPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="INICIAR REGISTRO", style=discord.ButtonStyle.success, emoji="🌾", custom_id="fac:farm:start")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(FarmStartModal())

async def store_farm_proof(message: discord.Message) -> bool:
    with db() as con:
        pending = con.execute("SELECT * FROM farm_proof_pending WHERE discord_id=?", (str(message.author.id),)).fetchone()
    if not pending:
        return False
    image_bytes = None; filename = "comprovante"; mime = ""; source_url = ""
    if message.attachments:
        attachment = next((a for a in message.attachments if (a.content_type or "").startswith("image/")), None)
        if attachment:
            if attachment.size > 8 * 1024 * 1024:
                await message.channel.send("A imagem excede o limite de 8 MB. Envie uma imagem menor."); return True
            try:
                image_bytes = await attachment.read(use_cached=False)
                filename = attachment.filename[:200]
                mime = attachment.content_type or "application/octet-stream"
                source_url = attachment.url
            except discord.HTTPException:
                await message.channel.send("Não consegui baixar o anexo. Tente enviá-lo novamente."); return True
    if image_bytes is None:
        match = re.search(r"https?://\S+", message.content or "")
        if match:
            url = match.group(0).strip('<>()[]')
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                await message.channel.send("Esse link não parece válido. Envie uma imagem como anexo ou um link HTTP/HTTPS direto."); return True
            try:
                import aiohttp
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url, allow_redirects=False) as response:
                        content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
                        if response.status != 200 or not content_type.startswith("image/"):
                            await message.channel.send("O link precisa abrir diretamente uma imagem pública (PNG/JPG/WEBP/GIF). Tente anexar o arquivo."); return True
                        if int(response.headers.get("Content-Length", "0") or 0) > 8 * 1024 * 1024:
                            await message.channel.send("A imagem excede o limite de 8 MB."); return True
                        image_bytes = await response.content.read(8 * 1024 * 1024 + 1)
                        if len(image_bytes) > 8 * 1024 * 1024:
                            await message.channel.send("A imagem excede o limite de 8 MB."); return True
                        mime = content_type; filename = os.path.basename(parsed.path)[:200] or "comprovante-link"; source_url = url
            except Exception as exc:
                log.warning("Falha ao obter imagem do link de farm: %s", exc)
                await message.channel.send("Não consegui baixar essa imagem. Envie o arquivo como anexo ou tente outro link."); return True
        else:
            await message.channel.send("Envie uma captura como anexo ou cole um link direto para a imagem."); return True
    # Checagem simples do conteúdo real, além do Content-Type.
    signatures = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")
    if not image_bytes or not image_bytes.startswith(signatures):
        await message.channel.send("O arquivo recebido não parece ser uma imagem compatível. Envie PNG, JPG, GIF ou WEBP."); return True
    require_approval = setting("farm_require_approval", "false").strip().lower() in {"1", "true", "yes", "sim"}
    proof_status = "PENDING" if require_approval else "APPROVED"
    with db() as con:
        cur = con.execute(
            "INSERT INTO farm_proofs(discord_id,character_name,game_id,image_data,image_filename,image_mime,source_url,created_at,status) VALUES(?,?,?,?,?,?,?,?,?)",
            (str(message.author.id), pending["character_name"], pending["game_id"], sqlite3.Binary(image_bytes), filename, mime, source_url, now_iso(), proof_status),
        )
        proof_id = cur.lastrowid
        con.execute("DELETE FROM farm_proof_pending WHERE discord_id=?", (str(message.author.id),))
    log_action(message.author.id, "FARM_COMPROVANTE_ENVIADO", message.author.id, "", f"Comprovante #{proof_id}; {filename}; {len(image_bytes)} bytes; status={proof_status}")

    if require_approval:
        guild = bot.get_guild(GUILD_ID) if GUILD_ID else (bot.guilds[0] if len(bot.guilds) == 1 else None)
        review_channel_id = int(setting("farm_approval_channel_id", "0") or 0)
        review_channel = guild.get_channel(review_channel_id) if guild and review_channel_id else None
        if not isinstance(review_channel, discord.TextChannel):
            await message.channel.send(
                f"✅ Comprovante **#{proof_id}** foi salvo no banco, mas está **aguardando aprovação**. "
                "A administração precisa configurar um canal de aprovação em `/config` > Farm."
            )
            log.error("Comprovante de farm #%s está pendente sem canal de aprovação válido.", proof_id)
            return True
        embed = discord.Embed(
            title=f"🌾 Comprovante de farm #{proof_id} — aguardando aprovação",
            description=(f"Membro: <@{message.author.id}> (`{message.author.id}`)\n"
                         f"Nome RP: **{pending['character_name']}**\nID FiveM: **{pending['game_id']}**\n"
                         f"Arquivo: `{filename[:150]}`\nTamanho: **{len(image_bytes) / 1024:.1f} KB**"),
            color=discord.Color.gold(), timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"farm_proof_id={proof_id}")
        safe_filename = os.path.basename(filename).replace("\n", "_")[:100] or f"farm-{proof_id}.png"
        embed.set_image(url=f"attachment://{safe_filename}")
        try:
            import io
            review_message = await review_channel.send(
                embed=embed,
                file=discord.File(io.BytesIO(image_bytes), filename=safe_filename),
                view=FarmProofReviewView(),
            )
            with db() as con:
                con.execute("UPDATE farm_proofs SET review_channel_id=?,review_message_id=? WHERE id=?",
                            (str(review_channel.id), str(review_message.id), proof_id))
            await message.channel.send(f"📨 Comprovante **#{proof_id}** salvo e enviado para aprovação da liderança. Você receberá o resultado após a análise.")
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.exception("Não foi possível publicar comprovante #%s para aprovação: %s", proof_id, exc)
            await message.channel.send(f"⚠️ Comprovante **#{proof_id}** foi salvo e continua pendente, mas não consegui publicar o pedido de aprovação. Avise a liderança.")
    else:
        await message.channel.send(
            f"✅ Comprovante **#{proof_id}** salvo permanentemente no banco e registrado como aprovado automaticamente. "
            f"Nome: **{pending['character_name']}** · ID FiveM: **{pending['game_id']}**."
        )
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
    entries.sort(key=lambda item: item[0], reverse=True)
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
            "Somente o recrutador selecionado pode aprovar ou recusar; ele tem 5 minutos para decidir.",
        )
        return apply_panel_media(discord.Embed(title=title[:256], description=description[:4096], color=discord.Color.blurple()), "wl")
    if kind == "farm":
        title = panel_setting("farm_panel_title", f"🌾 REGISTRO DE FARM — {FACTION_NAME}")
        description = panel_setting(
            "farm_panel_description",
            "1. Deposite o farm no baú da facção.\n2. Tire uma captura de tela.\n"
            "3. Clique em **INICIAR REGISTRO** e informe nome e ID.\n"
            "4. Envie a imagem ou um link direto na DM do bot. O comprovante é salvo no banco.",
        )
        return apply_panel_media(discord.Embed(title=title[:256], description=description[:4096], color=discord.Color.green()), "farm")
    if kind == "hierarchy":
        title = panel_setting("hierarchy_panel_title", f"🏛️ HIERARQUIA — {FACTION_NAME}")
        description = panel_setting("hierarchy_panel_description", "Cargos e membros da organização, em ordem de prioridade.")
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
    embed = discord.Embed(
        title=f"⚙️ Painel de configuração — {FACTION_NAME}",
        description=(
            "Configure somente os módulos usados pela facção. Selecione cargos e canais nas listas do Discord; "
            "WL e Farm também aceitam foto e banner por link HTTPS. As alterações ficam salvas no banco."
        ),
        color=discord.Color.blurple(),
    )

    def channel_label(key: str, fallback: str = "0") -> str:
        raw = panel_setting(key, fallback)
        if not raw.isdigit() or not int(raw):
            return "não selecionado"
        channel = guild.get_channel(int(raw)) if guild else None
        return channel.mention if channel else f"canal configurado ({raw})"

    def role_label(role_id: int) -> str:
        if not role_id:
            return "não selecionado"
        role = guild.get_role(role_id) if guild else None
        return role.mention if role else "cargo configurado"

    embed.add_field(
        name="Painéis",
        value=(
            f"**WL:** {panel_setting('wl_panel_title', 'WL — ' + FACTION_NAME)[:80]} · {channel_label('wl_panel_channel_id', str(REGISTRATION_CHANNEL_ID))}\n"
            f"**Hierarquia:** {panel_setting('hierarchy_panel_title', 'Hierarquia')[:80]} · {channel_label('hierarchy_panel_channel_id')}\n"
            f"**Farm:** {panel_setting('farm_panel_title', 'Farm')[:80]} · {channel_label('farm_panel_channel_id')}\n"
            f"**Canal de aprovação do farm:** {channel_label('farm_approval_channel_id')}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Cargos selecionados",
        value=(f"Membro: {role_label(MEMBER_ROLE_ID)}\nRecrutador: {role_label(recruiter_role_id())}\n"
               f"Liderança: {role_label(LEADER_ROLE_ID)}\nCargos na hierarquia: {len([x for x in existing_rank_config().split(';') if x.strip()])}"),
        inline=False,
    )
    embed.add_field(
        name="Mídia dos painéis WL e Farm",
        value=(f"WL — foto: {'configurada' if panel_setting('wl_panel_photo_url','') else 'não configurada'} · banner: {'configurado' if panel_setting('wl_panel_banner_url','') else 'não configurado'}\n"
               f"Farm — foto: {'configurada' if panel_setting('farm_panel_photo_url','') else 'não configurada'} · banner: {'configurado' if panel_setting('farm_panel_banner_url','') else 'não configurado'}"),
        inline=False,
    )
    embed.add_field(
        name="Logs",
        value=f"Admin: {channel_label('admin_log_channel_id', str(ADMIN_LOG_CHANNEL_ID))} · Público: {channel_label('public_log_channel_id', str(PUBLIC_LOG_CHANNEL_ID))} · Farm: {channel_label('farm_log_channel_id', str(FARM_LOG_CHANNEL_ID))}",
        inline=False,
    )
    embed.set_footer(text="/config • Somente liderança ou administradores podem alterar configurações")
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


class ConfigWLChannelsView(View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(ConfigTextChannelSelect("wl_panel_channel_id", "Canal onde o painel WL será publicado", row=0))
        self.add_item(ConfigTextChannelSelect("wl_channel_id", "Canal que receberá os pedidos de WL", row=1))


class ConfigFarmChannelsView(View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(ConfigTextChannelSelect("farm_panel_channel_id", "Canal onde o painel Farm será publicado", row=0))
        self.add_item(ConfigTextChannelSelect("farm_approval_channel_id", "Canal que receberá comprovantes para aprovação", row=1))


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


class ConfigHierarchyChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent_view: "ConfigHierarchySetupView"):
        super().__init__(placeholder="Canal onde será publicada a hierarquia", channel_types=[discord.ChannelType.text], min_values=1, max_values=1, row=0)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        self.parent_view.channel_id = channel.id
        await interaction.response.send_message(f"Canal da hierarquia selecionado: {channel.mention}. Clique em **Salvar hierarquia** para confirmar os cargos.", ephemeral=True)


class ConfigHierarchyRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent_view: "ConfigHierarchySetupView"):
        super().__init__(placeholder="Selecione os cargos que devem aparecer na hierarquia", min_values=0, max_values=25, row=1)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.selected_roles = list(self.values)
        self.parent_view.roles_touched = True
        await interaction.response.send_message(
            f"Seleção recebida: {len(self.values)} cargo(s). Clique em **Salvar hierarquia** para confirmar.",
            ephemeral=True,
        )


class ConfigHierarchySetupView(View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=600)
        raw_channel = panel_setting("hierarchy_panel_channel_id", "0")
        self.channel_id = int(raw_channel) if raw_channel.isdigit() else 0
        self.selected_roles = []
        with db() as con:
            rows = con.execute("SELECT key FROM settings WHERE key LIKE 'rank_role_%'").fetchall()
        for row in rows:
            try:
                role_id = int(row["key"].replace("rank_role_", "", 1))
                role = guild.get_role(role_id)
                if role:
                    self.selected_roles.append(role)
            except (ValueError, TypeError):
                pass
        self.roles_touched = False
        self.add_item(ConfigHierarchyChannelSelect(self))
        self.add_item(ConfigHierarchyRoleSelect(self))

    @discord.ui.button(label="Salvar hierarquia", style=discord.ButtonStyle.success, emoji="💾", row=2)
    async def save_hierarchy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.channel_id:
            save_config_value("hierarchy_panel_channel_id", str(self.channel_id))
        selected_roles = self.selected_roles
        with db() as con:
            con.execute("DELETE FROM settings WHERE key LIKE 'rank_role_%'")
            for role in selected_roles:
                words = [part for part in re.split(r"[^A-Za-z0-9À-ÿ]+", role.name.upper()) if part]
                abbreviation = ("".join(word[0] for word in words) if len(words) > 1 else (words[0][:3] if words else "RANK"))[:12]
                abbreviation = abbreviation or "RANK"
                priority = max(0, int(role.position))
                con.execute("INSERT INTO settings(key,value) VALUES(?,?)", (f"rank_role_{role.id}", f"{abbreviation}|{priority}"))
        await interaction.response.send_message(
            f"✅ Hierarquia salva: {len(selected_roles)} cargo(s) selecionado(s). A prioridade segue a posição do cargo no Discord; a sigla é gerada automaticamente pelo nome.",
            ephemeral=True,
        )


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
            "✅ Título, descrição, foto e banner da WL salvos. Agora selecione os dois canais abaixo.",
            view=ConfigWLChannelsView(), ephemeral=True,
        )


class ConfigHierarchyModal(Modal, title="Configurar painel de hierarquia"):
    title_input = TextInput(label="Título da hierarquia", max_length=256, required=True)
    description_input = TextInput(label="Descrição da hierarquia", style=discord.TextStyle.paragraph, max_length=3500, required=True)

    def __init__(self):
        super().__init__(timeout=300)
        _set_modal_defaults(self, [
            (self.title_input, panel_setting("hierarchy_panel_title", f"🏛️ HIERARQUIA — {FACTION_NAME}")),
            (self.description_input, panel_setting("hierarchy_panel_description", "Cargos e membros da organização, em ordem de prioridade.")),
        ])

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use este formulário dentro do servidor.", ephemeral=True)
            return
        save_config_value("hierarchy_panel_title", self.title_input.value.strip() or f"HIERARQUIA — {FACTION_NAME}")
        save_config_value("hierarchy_panel_description", self.description_input.value.strip())
        await interaction.response.send_message(
            "✅ Título e descrição salvos. Selecione o canal e os cargos que devem aparecer na hierarquia.",
            view=ConfigHierarchySetupView(interaction.guild), ephemeral=True,
        )


class ConfigFarmPanelModal(Modal, title="Configurar painel e aprovação de farm"):
    title_input = TextInput(label="Título do painel farm", max_length=256, required=True)
    description_input = TextInput(label="Descrição/instruções do farm", style=discord.TextStyle.paragraph, max_length=2500, required=True)
    photo_url = TextInput(label="URL da foto / miniatura (opcional)", max_length=500, required=False, placeholder="https://.../foto.png")
    banner_url = TextInput(label="URL do banner (opcional)", max_length=500, required=False, placeholder="https://.../banner.png")
    require_approval = TextInput(label="Exigir aprovação? (sim/não)", max_length=5, required=True)

    def __init__(self):
        super().__init__(timeout=300)
        _set_modal_defaults(self, [
            (self.title_input, panel_setting("farm_panel_title", f"🌾 REGISTRO DE FARM — {FACTION_NAME}")),
            (self.description_input, panel_setting("farm_panel_description", "Deposite no baú, clique em iniciar registro e envie o comprovante na DM do bot.")),
            (self.photo_url, panel_setting("farm_panel_photo_url", "")),
            (self.banner_url, panel_setting("farm_panel_banner_url", "")),
            (self.require_approval, "sim" if panel_setting("farm_require_approval", "false").lower() in {"1", "true", "yes", "sim"} else "não"),
        ])

    async def on_submit(self, interaction: discord.Interaction) -> None:
        photo, banner = self.photo_url.value.strip(), self.banner_url.value.strip()
        if not valid_asset_url(photo) or not valid_asset_url(banner):
            await interaction.response.send_message("Foto e banner precisam ser URLs públicas HTTPS, ou deixe o campo vazio para remover a imagem.", ephemeral=True)
            return
        raw_approval = self.require_approval.value.strip().lower()
        if raw_approval not in {"sim", "não", "nao", "yes", "no", "true", "false", "1", "0"}:
            await interaction.response.send_message("No campo de aprovação, informe sim ou não.", ephemeral=True)
            return
        enabled = raw_approval in {"sim", "yes", "true", "1"}
        save_config_value("farm_panel_title", self.title_input.value.strip() or f"FARM — {FACTION_NAME}")
        save_config_value("farm_panel_description", self.description_input.value.strip())
        save_config_value("farm_panel_photo_url", photo)
        save_config_value("farm_panel_banner_url", banner)
        save_config_value("farm_require_approval", "true" if enabled else "false")
        await interaction.response.send_message(
            f"✅ Título, descrição, foto, banner e opção de aprovação salvos ({'aprovação obrigatória' if enabled else 'aprovação automática'}). Agora selecione os canais abaixo.",
            view=ConfigFarmChannelsView(), ephemeral=True,
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
        if not has_leadership(interaction):
            await interaction.response.send_message("Somente liderança ou administrador pode recusar comprovantes.", ephemeral=True); return
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
        if not has_leadership(interaction):
            await interaction.response.send_message("Somente liderança ou administrador pode aprovar comprovantes.", ephemeral=True); return
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
        log_action(interaction.user.id, "FARM_COMPROVANTE_APROVADO", int(row["discord_id"]), "", f"Comprovante #{proof_id}")
        await update_farm_review_message(row, status="APPROVED", reviewer=interaction.user.mention)
        try:
            user = await bot.fetch_user(int(row["discord_id"]))
            await user.send(f"✅ Seu comprovante de farm **#{proof_id}** foi aprovado pela liderança.")
        except discord.HTTPException:
            pass
        await interaction.response.send_message(f"Comprovante #{proof_id} aprovado.", ephemeral=True)

    @discord.ui.button(label="Recusar comprovante", style=discord.ButtonStyle.danger, emoji="❌", custom_id="fac:farm:reject-proof")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not has_leadership(interaction):
            await interaction.response.send_message("Somente liderança ou administrador pode recusar comprovantes.", ephemeral=True); return
        proof_id = self.proof_id_from(interaction)
        if not proof_id:
            await interaction.response.send_message("Não consegui identificar esse comprovante.", ephemeral=True); return
        expected_channel = int(panel_setting("farm_approval_channel_id", "0") or 0)
        if expected_channel and interaction.channel_id != expected_channel:
            await interaction.response.send_message("A recusa precisa ser feita no canal de análise configurado.", ephemeral=True); return
        await interaction.response.send_modal(FarmProofRejectModal(proof_id))


class ConfigPanelView(View):
    def __init__(self):
        super().__init__(timeout=900)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not has_leadership(interaction):
            await interaction.response.send_message("Somente a liderança ou administradores podem alterar a configuração.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="WL", style=discord.ButtonStyle.primary, emoji="📋", row=0)
    async def wl(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ConfigWLPanelModal())

    @discord.ui.button(label="Hierarquia", style=discord.ButtonStyle.primary, emoji="🏛️", row=0)
    async def hierarchy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ConfigHierarchyModal())

    @discord.ui.button(label="Farm", style=discord.ButtonStyle.primary, emoji="🌾", row=0)
    async def farm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ConfigFarmPanelModal())

    @discord.ui.button(label="Selecionar cargos", style=discord.ButtonStyle.secondary, emoji="🎭", row=0)
    async def roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "Selecione os cargos nos menus abaixo. Não precisa digitar IDs.",
            view=ConfigRolesSelectView(), ephemeral=True,
        )

    @discord.ui.button(label="Logs", style=discord.ButtonStyle.secondary, emoji="🧾", row=0)
    async def logs(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ConfigLogsModal())

    @discord.ui.button(label="Publicar WL", style=discord.ButtonStyle.success, emoji="📨", row=1)
    async def publish_wl(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await publish_configured_panel(interaction, "wl")

    @discord.ui.button(label="Publicar hierarquia", style=discord.ButtonStyle.success, emoji="🏛️", row=1)
    async def publish_hierarchy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await publish_configured_panel(interaction, "hierarchy")

    @discord.ui.button(label="Publicar Farm", style=discord.ButtonStyle.success, emoji="🌾", row=1)
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


async def publish_configured_panel(interaction: discord.Interaction, kind: str) -> None:
    if not interaction.guild:
        await interaction.response.send_message("Use este recurso dentro de um servidor Discord.", ephemeral=True); return
    defaults = {
        "wl": ("wl_panel_channel_id", str(REGISTRATION_CHANNEL_ID)),
        "hierarchy": ("hierarchy_panel_channel_id", "0"),
        "farm": ("farm_panel_channel_id", "0"),
        "main": ("main_panel_channel_id", "0"),
        "members": ("members_panel_channel_id", "0"),
        "leader": ("leader_panel_channel_id", "0"),
    }
    key, fallback = defaults[kind]
    try: channel_id = int(panel_setting(key, fallback) or 0)
    except ValueError: channel_id = 0
    if not channel_id:
        await interaction.response.send_message(f"Configure o ID do canal de publicação em `/config` antes de publicar o painel {kind}.", ephemeral=True); return
    target = await resolve_text_channel(interaction.guild, channel_id)
    if not target:
        await interaction.response.send_message(f"Não encontrei o canal `{channel_id}` ou o bot não tem acesso a ele. Confira o ID e as permissões.", ephemeral=True); return
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
            rank_rows = []
            with db() as con:
                rank_rows = con.execute("SELECT key,value FROM settings WHERE key LIKE 'rank_role_%'").fetchall()
            ranks = []
            for row in rank_rows:
                try:
                    role_id = int(row["key"].replace("rank_role_", "", 1))
                    abbreviation, priority = row["value"].split("|", 1)
                    ranks.append((int(priority), role_id, abbreviation))
                except (ValueError, TypeError):
                    continue
            ranks.sort(key=lambda item: item[0], reverse=True)
            if not ranks:
                await interaction.response.send_message("Nenhum cargo está configurado para exibição. Configure-os em `/config` > Hierarquia.", ephemeral=True); return
            for _, role_id, abbreviation in ranks[:25]:
                role = interaction.guild.get_role(role_id)
                if not role: continue
                members = sorted((m for m in interaction.guild.members if not m.bot and role in m.roles), key=lambda m: m.display_name.casefold())
                lines = [m.mention for m in members[:25]]
                value = "\n".join(lines) if lines else "Nenhum membro com este cargo."
                if len(members) > 25: value += f"\n… e mais {len(members)-25} membro(s)."
                embed.add_field(name=f"{abbreviation} · {role.name} ({len(members)})", value=value[:1024], inline=False)
            await target.send(embed=embed)
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
        await interaction.response.send_message(f"✅ Painel **{kind}** publicado em {target.mention}.", ephemeral=True)
    except (discord.Forbidden, discord.HTTPException) as exc:
        log.exception("Não foi possível publicar painel %s", kind)
        await interaction.response.send_message(f"Não consegui publicar o painel. Confira as permissões do bot. Detalhe: {str(exc)[:180]}", ephemeral=True)


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


@bot.event
async def setup_hook() -> None:  # type: ignore[override]
    init_db()
    await start_render_health_server()
    apply_saved_settings()
    bot.add_view(RegistrationPanelView())
    bot.add_view(RegistrationApprovalView())
    bot.add_view(FarmPanelView())
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
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    if isinstance(message.channel, discord.DMChannel):
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

@bot.tree.command(name="config", description="Abre o painel interativo de configuração da facção")
async def config(interaction: discord.Interaction) -> None:
    if not await deny_unless_leader(interaction):
        return
    await interaction.response.send_message(embed=config_embed(interaction.guild), view=ConfigPanelView(), ephemeral=True)


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

@bot.tree.command(name="farm-item-desativar", description="Desativa um item de farm")
async def farm_item_desativar(interaction: discord.Interaction, nome: str) -> None:
    if not await deny_unless_leader(interaction): return
    with db() as con:
        cur = con.execute("UPDATE farm_items SET active=0 WHERE name=? COLLATE NOCASE", (nome.strip(),))
    if cur.rowcount == 0:
        await interaction.response.send_message("Não encontrei esse item de farm.", ephemeral=True); return
    log_action(interaction.user.id, "FARM_ITEM_DESATIVADO", None, "", nome)
    await interaction.response.send_message(f"Item **{nome}** desativado.", ephemeral=True)

@bot.tree.command(name="farm", description="Registra a quantidade farmada de um item")
@app_commands.describe(item="Nome exato do item cadastrado", quantidade="Quantidade farmada")
async def farm(interaction: discord.Interaction, item: str, quantidade: app_commands.Range[int, 1, 100000]) -> None:
    if not is_active_member(interaction.user.id):
        await interaction.response.send_message("Somente membros com cadastro aprovado podem registrar farm.", ephemeral=True); return
    with db() as con:
        farm_item_row = con.execute("SELECT * FROM farm_items WHERE name=? COLLATE NOCASE AND active=1", (item.strip(),)).fetchone()
        if not farm_item_row:
            available = con.execute("SELECT name FROM farm_items WHERE active=1 ORDER BY name").fetchall()
            names = ", ".join(r["name"] for r in available) or "nenhum item configurado"
            await interaction.response.send_message(f"Item não encontrado. Itens ativos: {names}", ephemeral=True); return
        day_prefix = local_day_prefix()
        daily = con.execute("SELECT COUNT(*) AS n FROM farm_entries WHERE discord_id=? AND substr(created_at,1,10)=?", (str(interaction.user.id), day_prefix)).fetchone()["n"]
        last = con.execute("SELECT created_at FROM farm_entries WHERE discord_id=? ORDER BY id DESC LIMIT 1", (str(interaction.user.id),)).fetchone()
        if daily >= MAX_FARM_ENTRIES_PER_DAY:
            await interaction.response.send_message(f"Você atingiu o limite de {MAX_FARM_ENTRIES_PER_DAY} registros de farm hoje.", ephemeral=True); return
        if last and FARM_COOLDOWN_SECONDS:
            try:
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last["created_at"])).total_seconds()
                if elapsed < FARM_COOLDOWN_SECONDS:
                    await interaction.response.send_message(f"Aguarde {int(FARM_COOLDOWN_SECONDS-elapsed)+1}s antes de registrar outro farm.", ephemeral=True); return
            except ValueError:
                pass
        points = quantidade * int(farm_item_row["points"])
        con.execute("INSERT INTO farm_entries(discord_id,item_name,quantity,points,created_at) VALUES(?,?,?,?,?)", (str(interaction.user.id), farm_item_row["name"], quantidade, points, now_iso()))
    log_action(interaction.user.id, "FARM_REGISTRADO", interaction.user.id, "", f"{quantidade}x {farm_item_row['name']} = {points} pontos")
    await interaction.response.send_message(f"🌾 Farm registrado: **{quantidade}x {farm_item_row['name']}** · **{points} pontos**.", ephemeral=True)
    if interaction.guild:
        await send_channel_log(interaction.guild, "🌾 Farm registrado", f"Membro: {interaction.user.mention}\nItem: **{farm_item_row['name']}**\nQuantidade: **{quantidade}**\nPontos: **{points}**", public=False, color=discord.Color.green())

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
    embed = discord.Embed(title=f"🌾 Comprovante de farm #{registro}", description=f"Membro: <@{row['discord_id']}>\nNome RP: **{row['character_name']}**\nID FiveM: **{row['game_id']}**\nStatus: **{row['status']}**\nData: {row['created_at']}", color=discord.Color.green() if row['status'] == 'APPROVED' else discord.Color.gold())
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

if __name__ == "__main__":
    if not TOKEN or TOKEN == "SEU_TOKEN_AQUI":
        raise SystemExit("Configure DISCORD_TOKEN no arquivo .env antes de iniciar o bot.")
    init_db()
    bot.run(TOKEN)
