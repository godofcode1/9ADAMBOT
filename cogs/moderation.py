import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands


class ModerationCog(commands.Cog, name="Moderation"):
    """Core moderation commands for staff members."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _get_mod_logs_channel(self, guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
        for channel in guild.text_channels:
            if channel.name.lower() in {"mod-logs", "moderation-logs", "audit-logs"}:
                return channel
        return None

    async def _log_action(
        self,
        guild: discord.Guild,
        action: str,
        target: discord.Member,
        moderator: discord.Member,
        reason: str,
    ) -> None:
        channel = await self._get_mod_logs_channel(guild)
        if channel is None:
            return
        embed = discord.Embed(title=f"{action} executed", color=discord.Color.orange())
        embed.add_field(name="Target", value=target.mention, inline=False)
        embed.add_field(name="Moderator", value=moderator.mention, inline=False)
        embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
        await channel.send(embed=embed)

    async def _attempt_appeal_dm(self, member: discord.Member, action: str, reason: str) -> None:
        appeals_cog = self.bot.get_cog("Appeals")
        if appeals_cog is not None:
            await appeals_cog.send_penalty_dm(member, action, reason)

    @app_commands.command(name="kick", description="Kick a member from the server.")
    @app_commands.describe(member="The member to kick", reason="Reason for the kick")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided") -> None:
        if member == interaction.user:
            await interaction.response.send_message("You cannot kick yourself.", ephemeral=True)
            return
        try:
            await self._attempt_appeal_dm(member, "kicked", reason)
            await member.kick(reason=reason)
            await self._log_action(interaction.guild, "Kick", member, interaction.user, reason)
            await interaction.response.send_message(f"{member.mention} was kicked.", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(f"Failed to kick member: {exc}", ephemeral=True)

    @app_commands.command(name="ban", description="Ban a member from the server.")
    @app_commands.describe(member="The member to ban", reason="Reason for the ban", delete_days="How many days of recent messages to purge")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided",
        delete_days: int = 0,
    ) -> None:
        if delete_days < 0 or delete_days > 7:
            await interaction.response.send_message("Delete days must be between 0 and 7.", ephemeral=True)
            return
        try:
            await self._attempt_appeal_dm(member, "banned", reason)
            await interaction.guild.ban(member, reason=reason, delete_message_seconds=delete_days * 86400)
            await self._log_action(interaction.guild, "Ban", member, interaction.user, reason)
            await interaction.response.send_message(f"{member.mention} was banned.", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(f"Failed to ban member: {exc}", ephemeral=True)

    @app_commands.command(name="unban", description="Unban a member from the server.")
    @app_commands.describe(user_id="The user ID to unban", reason="Reason for the unban")
    @app_commands.checks.has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str = "No reason provided") -> None:
        try:
            user_id_int = int(user_id)
            await interaction.guild.unban(discord.Object(user_id_int), reason=reason)
            await self._log_action(interaction.guild, "Unban", interaction.guild.get_member(user_id_int) or discord.Object(user_id_int), interaction.user, reason)
            await interaction.response.send_message(f"User <@{user_id_int}> was unbanned.", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(f"Failed to unban member: {exc}", ephemeral=True)

    @app_commands.command(name="softban", description="Ban and immediately unban to remove recent messages.")
    @app_commands.describe(member="The member to softban", reason="Reason for the softban")
    @app_commands.checks.has_permissions(ban_members=True)
    async def softban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided") -> None:
        try:
            await self._attempt_appeal_dm(member, "softbanned", reason)
            await interaction.guild.ban(member, reason=reason, delete_message_seconds=86400)
            await interaction.guild.unban(member, reason="Softban cleanup")
            await self._log_action(interaction.guild, "Softban", member, interaction.user, reason)
            await interaction.response.send_message(f"{member.mention} was softbanned.", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(f"Failed to softban member: {exc}", ephemeral=True)

    @app_commands.command(name="timeout", description="Place a member in timeout.")
    @app_commands.describe(member="The member to timeout", duration_minutes="How long the timeout should last", reason="Reason for the timeout")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration_minutes: int,
        reason: str = "No reason provided",
    ) -> None:
        if duration_minutes < 1 or duration_minutes > 10080:
            await interaction.response.send_message("Timeout duration must be between 1 and 10080 minutes.", ephemeral=True)
            return
        try:
            await self._attempt_appeal_dm(member, "timed out", reason)
            until = discord.utils.utcnow() + datetime.timedelta(minutes=duration_minutes)
            await member.timeout(until, reason=reason)
            await self._log_action(interaction.guild, "Timeout", member, interaction.user, reason)
            await interaction.response.send_message(f"{member.mention} was timed out for {duration_minutes} minutes.", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(f"Failed to timeout member: {exc}", ephemeral=True)

    @app_commands.command(name="unmute", description="Remove an active timeout from a member.")
    @app_commands.describe(member="The member to remove the timeout from")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def unmute(self, interaction: discord.Interaction, member: discord.Member) -> None:
        try:
            await member.timeout(None, reason="Timeout cleared by moderator")
            await interaction.response.send_message(f"Timeout removed from {member.mention}.", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(f"Failed to remove timeout: {exc}", ephemeral=True)

    @app_commands.command(name="purge", description="Delete recent messages in a channel.")
    @app_commands.describe(amount="How many messages to remove", member_optional="Optional member filter")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(
        self,
        interaction: discord.Interaction,
        amount: int,
        member_optional: Optional[discord.Member] = None,
    ) -> None:
        if amount < 1 or amount > 100:
            await interaction.response.send_message("Amount must be between 1 and 100.", ephemeral=True)
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("This command only works in text channels.", ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True)
            if member_optional is None:
                deleted = await channel.purge(limit=amount)
            else:
                deleted = await channel.purge(limit=amount, check=lambda message: message.author == member_optional)
            await interaction.followup.send(f"Deleted {len(deleted)} message(s).", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"Failed to purge messages: {exc}", ephemeral=True)

    @app_commands.command(name="slowmode", description="Set slowmode in a channel.")
    @app_commands.describe(seconds="Slowmode delay in seconds", channel_optional="Target channel")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slowmode(
        self,
        interaction: discord.Interaction,
        seconds: int,
        channel_optional: Optional[discord.TextChannel] = None,
    ) -> None:
        if seconds < 0 or seconds > 21600:
            await interaction.response.send_message("Slowmode seconds must be between 0 and 21600.", ephemeral=True)
            return
        target_channel = channel_optional or interaction.channel
        try:
            await target_channel.edit(slowmode_delay=seconds)
            await interaction.response.send_message(f"Slowmode set to {seconds} seconds in {target_channel.mention}.", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(f"Failed to set slowmode: {exc}", ephemeral=True)

    @app_commands.command(name="lockdown", description="Restrict send messages for @everyone in a channel.")
    @app_commands.describe(channel_optional="Channel to lockdown")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lockdown(self, interaction: discord.Interaction, channel_optional: Optional[discord.TextChannel] = None) -> None:
        target_channel = channel_optional or interaction.channel
        try:
            overwrite = target_channel.overwrites_for(interaction.guild.default_role)
            overwrite.send_messages = False
            await target_channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
            await interaction.response.send_message(f"{target_channel.mention} is now locked down.", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(f"Failed to lockdown channel: {exc}", ephemeral=True)

    @app_commands.command(name="unlock", description="Restore send messages for @everyone in a channel.")
    @app_commands.describe(channel_optional="Channel to unlock")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction, channel_optional: Optional[discord.TextChannel] = None) -> None:
        target_channel = channel_optional or interaction.channel
        try:
            overwrite = target_channel.overwrites_for(interaction.guild.default_role)
            overwrite.send_messages = None
            await target_channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
            await interaction.response.send_message(f"{target_channel.mention} has been unlocked.", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(f"Failed to unlock channel: {exc}", ephemeral=True)

    async def cog_load(self) -> None:
        return None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationCog(bot))
