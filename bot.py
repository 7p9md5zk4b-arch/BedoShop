import os
import sqlite3
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks


# =========================
# USTAWIENIA
# =========================

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("Brak DISCORD_TOKEN w zmiennych środowiskowych.")

DB_FILE = "bedoshop.db"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# =========================
# BAZA DANYCH
# =========================

db = sqlite3.connect(DB_FILE)
db.row_factory = sqlite3.Row

db.execute("""
CREATE TABLE IF NOT EXISTS config (
    guild_id INTEGER PRIMARY KEY,
    welcome_channel INTEGER,
    log_channel INTEGER,
    auto_role INTEGER,
    warn_limit INTEGER DEFAULT 3
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    user_id INTEGER,
    moderator_id INTEGER,
    reason TEXT,
    created_at TEXT
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    event_type TEXT,
    user_id INTEGER,
    created_at TEXT
)
""")

db.commit()


# =========================
# POMOCNICZE
# =========================

def now():
    return datetime.now(timezone.utc)


def get_config(guild_id):
    row = db.execute(
        "SELECT * FROM config WHERE guild_id = ?",
        (guild_id,)
    ).fetchone()

    if row:
        return row

    db.execute(
        """
        INSERT INTO config
        (guild_id, welcome_channel, log_channel, auto_role, warn_limit)
        VALUES (?, NULL, NULL, NULL, 3)
        """,
        (guild_id,)
    )
    db.commit()

    return db.execute(
        "SELECT * FROM config WHERE guild_id = ?",
        (guild_id,)
    ).fetchone()


async def send_log(guild, message):
    config = get_config(guild.id)

    channel_id = config["log_channel"]

    if not channel_id:
        return

    channel = guild.get_channel(channel_id)

    if channel:
        try:
            await channel.send(message)
        except discord.HTTPException:
            pass


def add_event(guild_id, event_type, user_id=None):
    db.execute(
        """
        INSERT INTO events
        (guild_id, event_type, user_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            guild_id,
            event_type,
            user_id,
            now().isoformat()
        )
    )
    db.commit()


# =========================
# START BOTA
# =========================

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Zalogowano jako {bot.user}")
        print(f"Zsynchronizowano {len(synced)} komend.")
    except Exception as e:
        print(f"Błąd synchronizacji: {e}")

    if not daily_statistics.is_running():
        daily_statistics.start()


# =========================
# POWITANIE
# =========================

@bot.event
async def on_member_join(member):
    guild = member.guild

    add_event(guild.id, "join", member.id)

    config = get_config(guild.id)

    # Automatyczna rola
    role_id = config["auto_role"]

    if role_id:
        role = guild.get_role(role_id)

        if role:
            try:
                await member.add_roles(role, reason="BedoShop - automatyczna rola")
            except discord.HTTPException:
                pass

    # Wiadomość powitalna
    channel_id = config["welcome_channel"]

    if channel_id:
        channel = guild.get_channel(channel_id)

        if channel:
            embed = discord.Embed(
                title="👋 Witamy!",
                description=f"Witaj {member.mention} na **{guild.name}**!",
                color=discord.Color.green()
            )

            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

    await send_log(
        guild,
        f"📥 **Dołączył:** {member.mention} (`{member.id}`)"
    )


# =========================
# WYJŚCIE
# =========================

@bot.event
async def on_member_remove(member):
    add_event(member.guild.id, "leave", member.id)

    await send_log(
        member.guild,
        f"📤 **Opuścił serwer:** {member} (`{member.id}`)"
    )


# =========================
# BAN
# =========================

@bot.tree.command(name="ban", description="Banuje użytkownika.")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "Brak powodu"):
    try:
        await member.ban(reason=reason)

        add_event(interaction.guild.id, "ban", member.id)

        await interaction.response.send_message(
            f"🔨 Zbanowano **{member}**.\nPowód: {reason}"
        )

        await send_log(
            interaction.guild,
            f"🔨 **BAN:** {member} przez {interaction.user}\nPowód: {reason}"
        )

    except discord.HTTPException:
        await interaction.response.send_message(
            "❌ Nie udało się zbanować użytkownika.",
            ephemeral=True
        )


# =========================
# KICK
# =========================

@bot.tree.command(name="kick", description="Wyrzuca użytkownika.")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "Brak powodu"):
    try:
        await member.kick(reason=reason)

        add_event(interaction.guild.id, "kick", member.id)

        await interaction.response.send_message(
            f"👢 Wyrzucono **{member}**.\nPowód: {reason}"
        )

        await send_log(
            interaction.guild,
            f"👢 **KICK:** {member} przez {interaction.user}\nPowód: {reason}"
        )

    except discord.HTTPException:
        await interaction.response.send_message(
            "❌ Nie udało się wyrzucić użytkownika.",
            ephemeral=True
        )


# =========================
# TIMEOUT
# =========================

@bot.tree.command(name="timeout", description="Nadaje użytkownikowi timeout.")
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(
    interaction: discord.Interaction,
    member: discord.Member,
    minutes: int,
    reason: str = "Brak powodu"
):
    if minutes < 1 or minutes > 40320:
        await interaction.response.send_message(
            "❌ Czas musi wynosić od 1 do 40320 minut.",
            ephemeral=True
        )
        return

    until = now() + timedelta(minutes=minutes)

    try:
        await member.timeout(until, reason=reason)

        add_event(interaction.guild.id, "timeout", member.id)

        await interaction.response.send_message(
            f"⏱️ Nadano timeout użytkownikowi **{member}** na **{minutes} min.**"
        )

        await send_log(
            interaction.guild,
            f"⏱️ **TIMEOUT:** {member} przez {interaction.user}\n"
            f"Czas: {minutes} min.\nPowód: {reason}"
        )

    except discord.HTTPException:
        await interaction.response.send_message(
            "❌ Nie udało się nadać timeoutu.",
            ephemeral=True
        )


# =========================
# UNBAN
# =========================

@bot.tree.command(name="unban", description="Odbanowuje użytkownika po ID.")
@app_commands.checks.has_permissions(ban_members=True)
async def unban(interaction: discord.Interaction, user_id: str):
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user)

        await interaction.response.send_message(
            f"🔓 Odbanowano **{user}**."
        )

        await send_log(
            interaction.guild,
            f"🔓 **UNBAN:** {user} przez {interaction.user}"
        )

    except (ValueError, discord.NotFound, discord.HTTPException):
        await interaction.response.send_message(
            "❌ Nie znaleziono bana dla tego ID.",
            ephemeral=True
        )


# =========================
# CLEAR
# =========================

@bot.tree.command(name="clear", description="Usuwa wiadomości z kanału.")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int):
    if amount < 1 or amount > 100:
        await interaction.response.send_message(
            "❌ Możesz usunąć od 1 do 100 wiadomości.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    deleted = await interaction.channel.purge(limit=amount)

    await interaction.followup.send(
        f"🧹 Usunięto **{len(deleted)}** wiadomości.",
        ephemeral=True
    )

    await send_log(
        interaction.guild,
        f"🧹 **CLEAR:** {interaction.user} usunął {len(deleted)} wiadomości "
        f"na kanale {interaction.channel.mention}"
    )


# =========================
# WARN
# =========================

@bot.tree.command(name="warn", description="Nadaje ostrzeżenie użytkownikowi.")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "Brak powodu"
):
    created = now().isoformat()

    db.execute(
        """
        INSERT INTO warnings
        (guild_id, user_id, moderator_id, reason, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            interaction.guild.id,
            member.id,
            interaction.user.id,
            reason,
            created
        )
    )

    db.commit()

    count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM warnings
        WHERE guild_id = ? AND user_id = ?
        """,
        (interaction.guild.id, member.id)
    ).fetchone()["count"]

    config = get_config(interaction.guild.id)
    limit = config["warn_limit"]

    message = (
        f"⚠️ {member.mention} otrzymał ostrzeżenie.\n"
        f"Powód: **{reason}**\n"
        f"Ostrzeżenia: **{count}/{limit}**"
    )

    # Automatyczny timeout
    if count >= limit:
        until = now() + timedelta(minutes=60)

        try:
            await member.timeout(
                until,
                reason=f"Automatyczny timeout po {limit} ostrzeżeniach"
            )

            message += (
                f"\n\n⏱️ Osiągnięto limit ostrzeżeń — "
                f"nadano timeout na **60 minut**."
            )

        except discord.HTTPException:
            message += "\n\n❌ Nie udało się nadać automatycznego timeoutu."

    await interaction.response.send_message(message)

    await send_log(
        interaction.guild,
        f"⚠️ **WARN:** {member} przez {interaction.user}\n"
        f"Powód: {reason}\n"
        f"Liczba ostrzeżeń: {count}/{limit}"
    )


# =========================
# WARNINGS
# =========================

@bot.tree.command(name="warnings", description="Pokazuje ostrzeżenia użytkownika.")
@app_commands.checks.has_permissions(moderate_members=True)
async def warnings(interaction: discord.Interaction, member: discord.Member):
    rows = db.execute(
        """
        SELECT *
        FROM warnings
        WHERE guild_id = ? AND user_id = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (interaction.guild.id, member.id)
    ).fetchall()

    if not rows:
        await interaction.response.send_message(
            f"✅ {member.mention} nie ma żadnych ostrzeżeń."
        )
        return

    embed = discord.Embed(
        title=f"⚠️ Ostrzeżenia — {member}",
        color=discord.Color.orange()
    )

    for row in rows:
        moderator = interaction.guild.get_member(row["moderator_id"])

        moderator_name = (
            moderator.mention
            if moderator
            else f"`{row['moderator_id']}`"
        )

        embed.add_field(
            name=f"Ostrzeżenie #{row['id']}",
            value=(
                f"**Powód:** {row['reason']}\n"
                f"**Moderator:** {moderator_name}"
            ),
            inline=False
        )

    await interaction.response.send_message(embed=embed)


# =========================
# CLEAR WARNINGS
# =========================

@bot.tree.command(
    name="clear-warnings",
    description="Usuwa wszystkie ostrzeżenia użytkownika."
)
@app_commands.checks.has_permissions(moderate_members=True)
async def clear_warnings(
    interaction: discord.Interaction,
    member: discord.Member
):
    db.execute(
        """
        DELETE FROM warnings
        WHERE guild_id = ? AND user_id = ?
        """,
        (interaction.guild.id, member.id)
    )

    db.commit()

    await interaction.response.send_message(
        f"🧹 Usunięto wszystkie ostrzeżenia użytkownika **{member}**."
    )

    await send_log(
        interaction.guild,
        f"🧹 **CLEAR WARNINGS:** {member} przez {interaction.user}"
    )


# =========================
# SETUP
# =========================

@bot.tree.command(
    name="setup",
    description="Konfiguruje kanał powitalny, logi i automatyczną rolę."
)
@app_commands.checks.has_permissions(manage_guild=True)
async def setup(
    interaction: discord.Interaction,
    welcome_channel: discord.TextChannel,
    log_channel: discord.TextChannel,
    auto_role: discord.Role,
    warn_limit: int = 3
):
    if warn_limit < 1 or warn_limit > 20:
        await interaction.response.send_message(
            "❌ Limit ostrzeżeń musi być od 1 do 20.",
            ephemeral=True
        )
        return

    db.execute(
        """
        INSERT INTO config
        (guild_id, welcome_channel, log_channel, auto_role, warn_limit)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            welcome_channel = excluded.welcome_channel,
            log_channel = excluded.log_channel,
            auto_role = excluded.auto_role,
            warn_limit = excluded.warn_limit
        """,
        (
            interaction.guild.id,
            welcome_channel.id,
            log_channel.id,
            auto_role.id,
            warn_limit
        )
    )

    db.commit()

    embed = discord.Embed(
        title="⚙️ BedoShop — konfiguracja",
        description="Konfiguracja została zapisana.",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="👋 Powitania",
        value=welcome_channel.mention,
        inline=False
    )

    embed.add_field(
        name="📋 Logi",
        value=log_channel.mention,
        inline=False
    )

    embed.add_field(
        name="🎭 Automatyczna rola",
        value=auto_role.mention,
        inline=False
    )

    embed.add_field(
        name="⚠️ Limit ostrzeżeń",
        value=str(warn_limit),
        inline=False
    )

    await interaction.response.send_message(embed=embed)


# =========================
# STATYSTYKI
# =========================

@bot.tree.command(
    name="stats",
    description="Pokazuje statystyki serwera z ostatnich 24 godzin."
)
@app_commands.checks.has_permissions(manage_guild=True)
async def stats(interaction: discord.Interaction):
    since = (now() - timedelta(hours=24)).isoformat()

    rows = db.execute(
        """
        SELECT event_type, COUNT(*) AS count
        FROM events
        WHERE guild_id = ? AND created_at >= ?
        GROUP BY event_type
        """,
        (interaction.guild.id, since)
    ).fetchall()

    data = {
        "join": 0,
        "leave": 0,
        "ban": 0,
        "kick": 0,
        "timeout": 0
    }

    for row in rows:
        data[row["event_type"]] = row["count"]

    embed = discord.Embed(
        title="📊 Statystyki — ostatnie 24h",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="📥 Dołączenia",
        value=str(data["join"])
    )

    embed.add_field(
        name="📤 Wyjścia",
        value=str(data["leave"])
    )

    embed.add_field(
        name="🔨 Bany",
        value=str(data["ban"])
    )

    embed.add_field(
        name="👢 Kicki",
        value=str(data["kick"])
    )

    embed.add_field(
        name="⏱️ Timeouty",
        value=str(data["timeout"])
    )

    await interaction.response.send_message(embed=embed)


# =========================
# DZIENNE STATYSTYKI
# =========================

@tasks.loop(hours=24)
async def daily_statistics():
    await bot.wait_until_ready()

    for guild in bot.guilds:
        since = (now() - timedelta(hours=24)).isoformat()

        count = db.execute(
            """
            SELECT COUNT(*) AS count
            FROM events
            WHERE guild_id = ? AND created_at >= ?
            """,
            (guild.id, since)
        ).fetchone()["count"]

        await send_log(
            guild,
            f"📊 **Dzienne statystyki BedoShop**\n"
            f"Łączna liczba zarejestrowanych zdarzeń z ostatnich 24h: **{count}**"
        )


# =========================
# BŁĘDY KOMEND
# =========================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    if isinstance(error, app_commands.errors.MissingPermissions):
        message = "❌ Nie masz odpowiednich uprawnień do tej komendy."

    elif isinstance(error, app_commands.errors.BotMissingPermissions):
        message = "❌ BedoShop nie ma odpowiednich uprawnień."

    else:
        print(f"Błąd komendy: {error}")
        message = "❌ Wystąpił błąd podczas wykonywania komendy."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# =========================
# START
# =========================

bot.run(TOKEN)