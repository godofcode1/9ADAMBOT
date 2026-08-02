import datetime
import re
import shutil
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from cogs.command_helpers import get_data_dir


class WarningsCog(commands.Cog, name="Warnings"):
    """Manage infractions, warning history, and automatic escalation."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.base_dir = get_data_dir()
        self.db_path = self.base_dir / "moderation.db"
        self.legacy_db_path = self.base_dir / "warnings.db"
        self._connection: Optional[aiosqlite.Connection] = None

    async def cog_load(self) -> None:
        """Create the database and ensure the moderation log channel exists."""
        await self._initialize_db()
        await self._ensure_channels_for_all_guilds()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Create the moderation channels once the bot is fully ready."""
        await self._ensure_channels_for_all_guilds()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Create moderation channels whenever the bot joins a new guild."""
        await self._ensure_log_channel(guild)

    async def _initialize_db(self) -> None:
        """Create the SQLite table used to store warning records."""
        if not self.db_path.exists() and self.legacy_db_path.exists():
            shutil.copy2(self.legacy_db_path, self.db_path)

        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                timestamp DATETIME NOT NULL
            )
            """
        )
        await self._connection.commit()

    async def _get_connection(self) -> aiosqlite.Connection:
        """Return the active SQLite connection, creating it if needed."""
        if self._connection is None:
            await self._initialize_db()
        return self._connection

    async def _ensure_channels_for_all_guilds(self) -> None:
        """Create the moderation log channel for every guild the bot is in."""
        for guild in self.bot.guilds:
            await self._ensure_log_channel(guild)

    async def _ensure_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Create a mod-logs text channel if one does not already exist."""
        channel = discord.utils.get(guild.text_channels, name="mod-logs")
        if channel is not None:
            return channel
        perms = guild.me.guild_permissions
        print(f"[warnings] Trying mod-logs in {guild.name} | manage_channels={perms.manage_channels} | administrator={perms.administrator}")
        if not perms.manage_channels and not perms.administrator:
            print(f"[warnings] Missing manage_channels permission in {guild.name}; skipped mod-logs creation.")
            return None
        try:
            created = await guild.create_text_channel("mod-logs", reason="Auto-created moderation log channel")
            print(f"[warnings] Created mod-logs in {guild.name}")
            return created
        except discord.Forbidden as exc:
            print(f"[warnings] Forbidden creating mod-logs in {guild.name}: {exc}")
            return None
        except Exception as exc:
            print(f"[warnings] Failed creating mod-logs in {guild.name}: {exc}")
            return None

    async def _resolve_member(self, guild: discord.Guild, value: str) -> Optional[discord.Member]:
        """Resolve a member from a mention, username, nickname, or ID string."""
        if not value:
            return None

        text = value.strip()
        mention_match = re.match(r"<@!?([0-9]+)>", text)
        if mention_match:
            member_id = int(mention_match.group(1))
            member = guild.get_member(member_id)
            if member is not None:
                return member
            try:
                return await guild.fetch_member(member_id)
            except Exception:
                return None

        if text.isdigit():
            member_id = int(text)
            member = guild.get_member(member_id)
            if member is not None:
                return member
            try:
                return await guild.fetch_member(member_id)
            except Exception:
                return None

        lowered = text.lower()
        for member in guild.members:
            if (
                member.name.lower() == lowered
                or member.display_name.lower() == lowered
                or f"{member.name}#{member.discriminator}".lower() == lowered
            ):
                return member

        matches = [
            member
            for member in guild.members
            if lowered in member.name.lower() or lowered in member.display_name.lower()
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    async def _get_warning_count(self, guild_id: int, user_id: int) -> int:
        """Return the current number of warnings for a user in a guild."""
        conn = await self._get_connection()
        row = await conn.execute(
            "SELECT COUNT(*) AS count FROM warnings WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        result = await row.fetchone()
        return int(result["count"]) if result else 0

    async def _delete_recent_messages_in_channel(
        self,
        channel: discord.TextChannel,
        target: discord.Member,
        *,
        limit: int = 50,
    ) -> int:
        """Delete the target user's recent messages from a channel when possible."""
        permissions = channel.permissions_for(channel.guild.me)
        if not permissions.manage_messages:
            return 0

        try:
            deleted = await channel.purge(
                limit=limit,
                check=lambda message: message.author.id == target.id and not message.pinned,
            )
            return len(deleted)
        except discord.Forbidden:
            return 0
        except Exception:
            return 0

    async def _log_escalation(self, guild: discord.Guild, target: discord.Member, moderator: discord.Member, reason: str, count: int) -> None:
        """Send a summary of automatic moderation action to the log channel."""
        channel = await self._ensure_log_channel(guild)
        if channel is None:
            return
        embed = discord.Embed(title="Automatic Escalation", color=discord.Color.red())
        embed.add_field(name="Member", value=target.mention, inline=False)
        embed.add_field(name="Moderator", value=moderator.mention, inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Total warnings", value=str(count), inline=False)
        await channel.send(embed=embed)

    async def _auto_escalate(self, guild: discord.Guild, member: discord.Member, moderator: discord.Member, count: int) -> None:
        """Apply a timeout at 3 warnings and a ban at 5 warnings."""
        if count >= 5:
            try:
                await member.ban(reason="Automatic ban after 5 warnings")
                await self._log_escalation(guild, member, moderator, "Automatic ban after 5 warnings", count)
            except discord.Forbidden:
                pass
            except Exception:
                pass
        elif count >= 3:
            try:
                timeout_until = discord.utils.utcnow() + datetime.timedelta(hours=1)
                await member.timeout(timeout_until, reason="Automatic timeout after 3 warnings")
                await self._log_escalation(guild, member, moderator, "Automatic timeout after 3 warnings", count)
            except discord.Forbidden:
                pass
            except Exception:
                pass

    @app_commands.command(name="warn", description="Issue a warning to a member.")
    @app_commands.describe(member="The member to warn", reason="Reason for the warning")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str) -> None:
        """Insert a warning record and trigger escalation if needed."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        resolved_member = member

        try:
            conn = await self._get_connection()
            timestamp = discord.utils.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            await conn.execute(
                "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?, ?)",
                (guild.id, resolved_member.id, interaction.user.id, reason, timestamp),
            )
            await conn.commit()
            total_warnings = await self._get_warning_count(guild.id, resolved_member.id)
            await self._auto_escalate(guild, resolved_member, interaction.user, total_warnings)

            deleted_messages = 0
            try:
                deleted_messages = await self._delete_recent_messages_in_channel(
                    interaction.channel,
                    resolved_member,
                    limit=50,
                )
            except Exception:
                deleted_messages = 0

            dm_sent = False
            try:
                await resolved_member.send(
                    f"You were warned in {guild.name}.\nReason: {reason}\nTotal warnings: {total_warnings}"
                )
                dm_sent = True
            except Exception:
                pass

            embed = discord.Embed(title="Warning Issued", color=discord.Color.orange())
            embed.add_field(name="Member", value=resolved_member.mention, inline=False)
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Total warnings", value=str(total_warnings), inline=False)
            embed.add_field(name="Deleted recent messages", value=str(deleted_messages), inline=False)
            embed.add_field(name="Member notified", value="Yes" if dm_sent else "No (DMs may be disabled)", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)

            try:
                logs_channel = discord.utils.get(guild.text_channels, name='mod-logs')
                if logs_channel is not None:
                    log_embed = discord.Embed(title='Warning Logged', color=discord.Color.orange())
                    log_embed.add_field(name='Member', value=resolved_member.mention, inline=False)
                    log_embed.add_field(name='Moderator', value=interaction.user.mention, inline=False)
                    log_embed.add_field(name='Reason', value=reason, inline=False)
                    log_embed.add_field(name='Total warnings', value=str(total_warnings), inline=False)
                    log_embed.add_field(name='Deleted recent messages', value=str(deleted_messages), inline=False)
                    await logs_channel.send(embed=log_embed)
            except Exception:
                pass
        except discord.Forbidden:
            await interaction.followup.send("I do not have permission to perform that moderation action.", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"An unexpected error occurred: {exc}", ephemeral=True)

    @app_commands.command(name="warnings", description="Show a member's warning history.")
    @app_commands.describe(member="The member whose warnings should be reviewed")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member) -> None:
        """Retrieve and display every stored warning for the supplied member."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        resolved_member = member

        try:
            conn = await self._get_connection()
            rows = await conn.execute(
                "SELECT reason, moderator_id, timestamp FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC",
                (guild.id, resolved_member.id),
            )
            records = await rows.fetchall()

            embed = discord.Embed(title=f"Warnings for {resolved_member.display_name}", color=discord.Color.blue())
            if not records:
                embed.description = "No warnings found."
            else:
                for record in records:
                    timestamp = record["timestamp"]
                    moderator_id = record["moderator_id"]
                    embed.add_field(
                        name=f"{timestamp}",
                        value=f"Reason: {record['reason']}\nModerator ID: {moderator_id}",
                        inline=False,
                    )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"An unexpected error occurred: {exc}", ephemeral=True)

    @app_commands.command(name="clearwarns", description="Delete all stored warnings for a member.")
    @app_commands.describe(member="The member whose warnings should be cleared")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def clearwarns(self, interaction: discord.Interaction, member: discord.Member) -> None:
        """Remove all warning records for a user from the database."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        resolved_member = member

        try:
            conn = await self._get_connection()
            await conn.execute("DELETE FROM warnings WHERE guild_id=? AND user_id=?", (guild.id, resolved_member.id))
            await conn.commit()
            await interaction.followup.send(f"All warnings for {resolved_member.mention} have been cleared.", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"An unexpected error occurred: {exc}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WarningsCog(bot))
