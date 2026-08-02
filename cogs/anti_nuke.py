import collections
import datetime
import discord
from discord.ext import commands


class AntiNukeCog(commands.Cog, name="Anti Nuke"):
    """Protect the server from rapid destructive actions by staff members."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.channel_deletions = collections.defaultdict(list)
        self.role_deletions = collections.defaultdict(list)
        self.member_bans = collections.defaultdict(list)

    def _trim_window(self, bucket: list[datetime.datetime], now: datetime.datetime) -> None:
        cutoff = now - datetime.timedelta(seconds=10)
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)

    def _check_window(self, bucket: list[datetime.datetime], limit: int) -> bool:
        return len(bucket) >= limit

    async def _trigger_anti_nuke(self, guild: discord.Guild, moderator: discord.Member) -> None:
        try:
            await moderator.edit(roles=[])
        except Exception:
            pass
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

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if channel.guild is None:
            return
        moderator = channel.guild.me
        if moderator is None:
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        bucket = self.channel_deletions[moderator.id]
        bucket.append(now)
        self._trim_window(bucket, now)
        if self._check_window(bucket, 3):
            await self._trigger_anti_nuke(channel.guild, moderator)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        if role.guild is None:
            return
        if role.name == "@everyone":
            return
        moderator = role.guild.me
        if moderator is None:
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        bucket = self.role_deletions[moderator.id]
        bucket.append(now)
        self._trim_window(bucket, now)
        if self._check_window(bucket, 3):
            await self._trigger_anti_nuke(role.guild, moderator)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        if not guild.me:
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        bucket = self.member_bans[guild.me.id]
        bucket.append(now)
        self._trim_window(bucket, now)
        if self._check_window(bucket, 3):
            await self._trigger_anti_nuke(guild, guild.me)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiNukeCog(bot))
