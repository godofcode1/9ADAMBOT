import datetime
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.command_helpers import banned_user_autocomplete


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
        target,
        moderator,
        reason: str,
    ) -> None:
        channel = await self._get_mod_logs_channel(guild)
        if channel is None:
            return
        embed = discord.Embed(title=f"{action} executed", color=discord.Color.orange())
        embed.add_field(name="Target", value=getattr(target, "mention", str(target)), inline=False)
        embed.add_field(name="Moderator", value=getattr(moderator, "mention", str(moderator)), inline=False)
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
    @app_commands.describe(user_id="The user to unban", reason="Reason for the unban")
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.autocomplete(user_id=banned_user_autocomplete)
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str = "No reason provided") -> None:
        try:
            user_id_int = int(user_id)
        except ValueError:
            await interaction.response.send_message("That doesn't look like a valid user ID.", ephemeral=True)
            return
        try:
            await interaction.guild.unban(discord.Object(user_id_int), reason=reason)
            await self._log_action(interaction.guild, "Unban", f"<@{user_id_int}>", interaction.user, reason)
            await interaction.response.send_message(f"User <@{user_id_int}> was unbanned.", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("That user is not banned, or the ID is invalid.", ephemeral=True)
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

    async def _clear_timeout(self, interaction: discord.Interaction, member: discord.Member) -> None:
        try:
            await member.timeout(None, reason="Timeout cleared by moderator")
            await self._log_action(interaction.guild, "Untimeout", member, interaction.user, "Timeout cleared by moderator")
            await interaction.response.send_message(f"Timeout removed from {member.mention}.", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(f"Failed to remove timeout: {exc}", ephemeral=True)

    @app_commands.command(name="untimeout", description="Remove an active timeout from a member.")
    @app_commands.describe(member="The member to remove the timeout from")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member) -> None:
        await self._clear_timeout(interaction, member)

    @app_commands.command(name="unmute", description="Alias for /untimeout — remove an active timeout from a member.")
    @app_commands.describe(member="The member to remove the timeout from")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def unmute(self, interaction: discord.Interaction, member: discord.Member) -> None:
        await self._clear_timeout(interaction, member)

    async def _purge_messages(
        self,
        channel: discord.TextChannel,
        *,
        limit: int,
        check=None,  # Optional[Callable[[discord.Message], bool]]
    ) -> int:
        """Delete up to ``limit`` messages, tolerating messages that vanish mid-purge.

        Discord's bulk-delete endpoint fails with 404 "Unknown Message" if any
        message in the batch is already gone (e.g. the AI filter deleted it while
        this purge was running). Rather than letting one bad batch abort the whole
        purge, we fall back to deleting each message individually and keep going.
        Messages older than 14 days cannot be bulk-deleted, so those are always
        deleted one by one.
        """
        deleted = 0
        batch: list[discord.Message] = []

        # Bulk delete only works for messages younger than 14 days. Older messages
        # must be deleted individually (mirrors discord.py's own purge logic).
        minimum_time = int((time.time() - 14 * 24 * 60 * 60) * 1000.0 - 1420070400000) << 22
        use_bulk = True

        async for message in channel.history(limit=limit):
            if check is not None and not check(message):
                continue
            if not message.type.is_deletable():
                continue

            if message.id < minimum_time:
                use_bulk = False

            if use_bulk:
                batch.append(message)
                if len(batch) >= 100:
                    deleted += await self._delete_batch(channel, batch)
                    batch = []
            else:
                deleted += await self._delete_single(message)

        deleted += await self._delete_batch(channel, batch)
        return deleted

    async def _delete_batch(self, channel: discord.TextChannel, messages: list) -> int:
        """Bulk-delete a batch; on failure, fall back to deleting one by one.

        ``discord.Forbidden`` is deliberately not caught here so it bubbles up to
        the command's permission-specific handler; other HTTP errors (e.g. a
        message vanishing mid-purge) fall back to individual deletes.
        """
        if not messages:
            return 0
        try:
            await channel.delete_messages(messages, reason="Purge command")
            return len(messages)
        except discord.Forbidden:
            raise
        except discord.HTTPException:
            count = 0
            for message in messages:
                count += await self._delete_single(message)
            return count

    async def _delete_single(self, message) -> int:
        """Delete one message, counting it only if it still exists."""
        try:
            await message.delete()
            return 1
        except discord.NotFound:
            return 0

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
                deleted = await self._purge_messages(channel, limit=amount)
            else:
                deleted = await self._purge_messages(channel, limit=amount, check=lambda message: message.author == member_optional)
            await interaction.followup.send(f"Deleted {deleted} message(s).", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("I need the Manage Messages permission to purge messages.", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"Failed to purge messages: {exc}", ephemeral=True)

    async def _purge_member_across_channels(
        self,
        guild: discord.Guild,
        member: discord.Member,
        *,
        limit: int,
    ) -> int:
        """Delete up to ``limit`` messages from ``member`` across every text channel.

        Includes threads so the purge is genuinely server-wide. Channels the bot
        cannot act in are skipped rather than aborting the whole operation.
        Reuses the resilient per-channel purge (bulk delete with a single-delete
        fallback) so a message vanishing mid-purge never errors out.
        """
        total = 0
        channels = list(guild.text_channels) + list(guild.threads)
        for channel in channels:
            permissions = channel.permissions_for(guild.me)
            if not permissions.manage_messages or not permissions.read_message_history:
                continue
            try:
                total += await self._purge_messages(
                    channel,
                    limit=limit,
                    check=lambda message: message.author == member,
                )
            except discord.HTTPException:
                continue
        return total

    @app_commands.command(name="purgeuser", description="Delete a specific user's messages across the whole server.")
    @app_commands.describe(member="The member whose messages to delete", amount="How many messages to search per channel")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purgeuser(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: int = 100,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if amount < 1 or amount > 100:
            await interaction.response.send_message("Amount must be between 1 and 100.", ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True)
            total = await self._purge_member_across_channels(interaction.guild, member, limit=amount)
            await interaction.followup.send(
                f"Deleted {total} message(s) from {member.mention} across the server.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send("I need the Manage Messages permission to purge messages.", ephemeral=True)
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
