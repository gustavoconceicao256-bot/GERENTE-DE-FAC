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
