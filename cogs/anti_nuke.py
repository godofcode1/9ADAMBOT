import collections
import datetime
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from cogs.command_helpers import get_data_dir

_DEFAULT_THRESHOLD = 3
_DEFAULT_WINDOW_SECONDS = 10
_MIN_THRESHOLD = 1
_MAX_THRESHOLD = 20
_MIN_WINDOW = 5
_MAX_WINDOW = 300


class AntiNukeCog(commands.Cog, name="Anti Nuke"):
    """Protect the server from rapid destructive actions by staff members."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.channel_deletions = collections.defaultdict(list)
        self.role_deletions = collections.defaultdict(list)
        self.member_bans = collections.defaultdict(list)
        self._permission_notified: set[int] = set()
        self._thresholds: dict[int, int] = {}
        self._windows: dict[int, int] = {}
        self._connection: Optional[aiosqlite.Connection] = None
        self._db_path = get_data_dir() / "anti_nuke.db"

    def _trim_window(
        self,
        bucket: list[datetime.datetime],
        now: datetime.datetime,
        window_seconds: int,
    ) -> None:
        cutoff = now - datetime.timedelta(seconds=window_seconds)
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)

    def _check_window(self, bucket: list[datetime.datetime], limit: int) -> bool:
        return len(bucket) >= limit

    async def _initialize_db(self) -> None:
        """Create the SQLite table used to store per-guild anti-nuke thresholds."""
        self._connection = await aiosqlite.connect(self._db_path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS anti_nuke_settings (
                guild_id INTEGER PRIMARY KEY,
                threshold INTEGER NOT NULL,
                window_seconds INTEGER NOT NULL
            )
            """
        )
        await self._connection.commit()

    async def _get_connection(self) -> aiosqlite.Connection:
        """Return the active SQLite connection, creating it if needed."""
        if self._connection is None:
            await self._initialize_db()
        return self._connection

    async def cog_load(self) -> None:
        """Load persisted per-guild thresholds when the cog starts."""
        try:
            await self._initialize_db()
            await self._load_settings()
        except Exception as exc:
            print(f"[anti_nuke] Failed to load settings: {exc}")

    async def _load_settings(self) -> None:
        """Load per-guild thresholds and windows into memory."""
        conn = await self._get_connection()
        async with conn.execute("SELECT guild_id, threshold, window_seconds FROM anti_nuke_settings") as cursor:
            rows = await cursor.fetchall()
        self._thresholds = {int(row["guild_id"]): int(row["threshold"]) for row in rows}
        self._windows = {int(row["guild_id"]): int(row["window_seconds"]) for row in rows}

    def _threshold_for(self, guild_id: int) -> int:
        return self._thresholds.get(guild_id, _DEFAULT_THRESHOLD)

    def _window_for(self, guild_id: int) -> int:
        return self._windows.get(guild_id, _DEFAULT_WINDOW_SECONDS)

    async def _set_settings(self, guild_id: int, threshold: int, window_seconds: int) -> None:
        conn = await self._get_connection()
        await conn.execute(
            "INSERT INTO anti_nuke_settings (guild_id, threshold, window_seconds) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET threshold = excluded.threshold, "
            "window_seconds = excluded.window_seconds",
            (guild_id, threshold, window_seconds),
        )
        await conn.commit()
        self._thresholds[guild_id] = threshold
        self._windows[guild_id] = window_seconds

    async def _reset_settings(self, guild_id: int) -> None:
        conn = await self._get_connection()
        await conn.execute(
            "DELETE FROM anti_nuke_settings WHERE guild_id = ?",
            (guild_id,),
        )
        await conn.commit()
        self._thresholds.pop(guild_id, None)
        self._windows.pop(guild_id, None)

    async def _get_actor(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
    ) -> Optional[discord.abc.User]:
        """Return the member who most recently performed ``action``, if discoverable.

        Falls back to ``None`` when audit logs are unavailable, so the anti-nuke
        can't misattribute a destructive action to the bot itself.
        """
        try:
            async for entry in guild.audit_logs(limit=5):
                if entry.action == action and entry.user is not None:
                    return guild.get_member(entry.user.id) or entry.user
        except discord.Forbidden:
            await self._notify_audit_log_permission(guild)
        except discord.HTTPException:
            pass
        return None

    async def _notify_audit_log_permission(self, guild: discord.Guild) -> None:
        """Tell the guild owner once that anti-nuke needs View Audit Log."""
        if guild.id in self._permission_notified:
            return
        self._permission_notified.add(guild.id)
        owner = getattr(guild, "owner", None)
        if owner is None:
            return
        try:
            await owner.send(
                f"⚠️ Anti-nuke protection in **{guild.name}** cannot identify who performs "
                "destructive actions because I'm missing the **View Audit Log** permission.\n"
                "Grant my role that permission so anti-nuke can protect the server properly."
            )
        except Exception:
            return

    async def _trigger_anti_nuke(self, guild: discord.Guild, moderator: discord.abc.User) -> None:
        # Only Members can have roles stripped or receive a penalty DM; a plain
        # User (actor no longer in the guild) can only be reported.
        member = guild.get_member(moderator.id)
        if member is not None:
            try:
                await member.edit(roles=[])
            except Exception:
                pass
        print(f"[anti_nuke] Triggered in {guild.name} against {moderator}.")
        owner = guild.owner
        if owner is not None:
            try:
                await owner.send(f"URGENT: {moderator.mention} triggered anti-nuke protection in {guild.name}.")
            except Exception:
                pass
            try:
                await guild.system_channel.send(f"@everyone {owner.mention} urgent anti-nuke action triggered for {moderator.mention}.")
            except Exception:
                pass

        # Wire the anti-nuke action into the appeals flow so the affected member
        # can submit an appeal with the same DM flow used for kicks/bans/timeouts.
        if member is not None:
            appeals_cog = self.bot.get_cog("Appeals")
            if appeals_cog is not None:
                try:
                    await appeals_cog.send_penalty_dm(
                        member,
                        "anti-nuked",
                        "Automatic anti-nuke protection triggered after rapid destructive actions.",
                    )
                except Exception:
                    pass

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if channel.guild is None:
            return
        actor = await self._get_actor(channel.guild, discord.AuditLogAction.channel_delete)
        if actor is None:
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        bucket = self.channel_deletions[actor.id]
        bucket.append(now)
        self._trim_window(bucket, now, self._window_for(channel.guild.id))
        if self._check_window(bucket, self._threshold_for(channel.guild.id)):
            await self._trigger_anti_nuke(channel.guild, actor)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        if role.guild is None:
            return
        if role.name == "@everyone":
            return
        actor = await self._get_actor(role.guild, discord.AuditLogAction.role_delete)
        if actor is None:
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        bucket = self.role_deletions[actor.id]
        bucket.append(now)
        self._trim_window(bucket, now, self._window_for(role.guild.id))
        if self._check_window(bucket, self._threshold_for(role.guild.id)):
            await self._trigger_anti_nuke(role.guild, actor)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        actor = await self._get_actor(guild, discord.AuditLogAction.ban)
        if actor is None:
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        bucket = self.member_bans[actor.id]
        bucket.append(now)
        self._trim_window(bucket, now, self._window_for(guild.id))
        if self._check_window(bucket, self._threshold_for(guild.id)):
            await self._trigger_anti_nuke(guild, actor)

    @app_commands.command(name="antinuke", description="View or configure anti-nuke thresholds for this server.")
    @app_commands.describe(
        threshold="Number of destructive actions allowed in the window before anti-nuke triggers (1-20)",
        window_seconds="Time window in seconds (5-300). Omit to keep the current value.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def antinuke(
        self,
        interaction: discord.Interaction,
        threshold: Optional[int] = None,
        window_seconds: Optional[int] = None,
    ) -> None:
        """Show or update the anti-nuke thresholds for this server."""
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if threshold is None and window_seconds is None:
            await interaction.response.send_message(
                f"Current anti-nuke settings: **{self._threshold_for(interaction.guild.id)}** destructive actions "
                f"within **{self._window_for(interaction.guild.id)}s**.",
                ephemeral=True,
            )
            return

        if threshold is not None:
            threshold = max(_MIN_THRESHOLD, min(_MAX_THRESHOLD, threshold))
        if window_seconds is not None:
            window_seconds = max(_MIN_WINDOW, min(_MAX_WINDOW, window_seconds))

        new_threshold = threshold if threshold is not None else self._threshold_for(interaction.guild.id)
        new_window = window_seconds if window_seconds is not None else self._window_for(interaction.guild.id)
        await self._set_settings(interaction.guild.id, new_threshold, new_window)
        await interaction.response.send_message(
            f"Anti-nuke now triggers after **{new_threshold}** destructive actions within **{new_window}s**. "
            "These settings are saved and survive restarts.",
            ephemeral=True,
        )

    @app_commands.command(name="antinukereset", description="Restore the default anti-nuke thresholds for this server.")
    @app_commands.checks.has_permissions(administrator=True)
    async def antinukereset(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        await self._reset_settings(interaction.guild.id)
        await interaction.response.send_message(
            f"Anti-nuke restored to defaults: **{_DEFAULT_THRESHOLD}** destructive actions within "
            f"**{_DEFAULT_WINDOW_SECONDS}s**.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiNukeCog(bot))
