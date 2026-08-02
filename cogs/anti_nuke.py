import collections
import datetime
from typing import Optional

import discord
from discord.ext import commands


class AntiNukeCog(commands.Cog, name="Anti Nuke"):
    """Protect the server from rapid destructive actions by staff members."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.channel_deletions = collections.defaultdict(list)
        self.role_deletions = collections.defaultdict(list)
        self.member_bans = collections.defaultdict(list)
        self._permission_notified: set[int] = set()

    def _trim_window(self, bucket: list[datetime.datetime], now: datetime.datetime) -> None:
        cutoff = now - datetime.timedelta(seconds=10)
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)

    def _check_window(self, bucket: list[datetime.datetime], limit: int) -> bool:
        return len(bucket) >= limit

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
        self._trim_window(bucket, now)
        if self._check_window(bucket, 3):
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
        self._trim_window(bucket, now)
        if self._check_window(bucket, 3):
            await self._trigger_anti_nuke(role.guild, actor)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        actor = await self._get_actor(guild, discord.AuditLogAction.ban)
        if actor is None:
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        bucket = self.member_bans[actor.id]
        bucket.append(now)
        self._trim_window(bucket, now)
        if self._check_window(bucket, 3):
            await self._trigger_anti_nuke(guild, actor)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiNukeCog(bot))
