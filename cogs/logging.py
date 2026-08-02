import discord
from discord.ext import commands


class LoggingCog(commands.Cog, name="Logging"):
    """Audit logging for edits, deletions, and member joins."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _get_mod_logs_channel(self, guild: discord.Guild):
        for channel in guild.text_channels:
            if channel.name.lower() in {"mod-logs", "moderation-logs", "audit-logs"}:
                return channel
        return None

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot:
            return
        if before.content == after.content:
            return
        logs_channel = await self._get_mod_logs_channel(before.guild)
        if logs_channel is None:
            return
        embed = discord.Embed(title="Message Edited", color=discord.Color.orange())
        embed.add_field(name="Author", value=before.author.mention, inline=False)
        embed.add_field(name="Channel", value=before.channel.mention, inline=False)
        embed.add_field(name="Before", value=before.content[:1000] or "No content", inline=False)
        embed.add_field(name="After", value=after.content[:1000] or "No content", inline=False)
        await logs_channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        logs_channel = await self._get_mod_logs_channel(message.guild)
        if logs_channel is None:
            return
        embed = discord.Embed(title="Message Deleted", color=discord.Color.dark_orange())
        embed.add_field(name="Author", value=message.author.mention, inline=False)
        embed.add_field(name="Channel", value=message.channel.mention, inline=False)
        embed.add_field(name="Content", value=message.content[:1000] or "No content", inline=False)
        await logs_channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild is None:
            return
        logs_channel = await self._get_mod_logs_channel(member.guild)
        if logs_channel is None:
            return
        created = discord.utils.utcnow() - member.created_at
        age_days = created.days
        if age_days < 7:
            embed = discord.Embed(title="New Member Joined", color=discord.Color.red())
            embed.add_field(name="Member", value=member.mention, inline=False)
            embed.add_field(name="Account Age", value=f"{age_days} days", inline=False)
            await logs_channel.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LoggingCog(bot))
