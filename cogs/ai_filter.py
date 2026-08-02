import re

import discord
from discord import app_commands
from discord.ext import commands


class AIFilterCog(commands.Cog, name="AI Filter"):
    """Use the OpenAI Moderation endpoint to screen incoming messages."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.excluded_users: dict[int, set[int]] = {}

    async def _get_mod_logs_channel(self, guild: discord.Guild):
        for channel in guild.text_channels:
            if channel.name.lower() in {"mod-logs", "moderation-logs", "audit-logs", "staff-logs"}:
                return channel
        return None

    async def _contains_obvious_slur(self, text: str) -> bool:
        slur_pattern = re.compile(r"\b(?:salop|salope|connard|connasse|bitch|nigga|nigger|faggot|fag|ta\w+|putain|asshole|shit|damn)\b", re.IGNORECASE)
        return bool(slur_pattern.search(text))

    async def _is_excluded(self, guild_id: int, user_id: int) -> bool:
        return user_id in self.excluded_users.get(guild_id, set())

    async def _add_exclusion(self, guild_id: int, user_id: int) -> None:
        self.excluded_users.setdefault(guild_id, set()).add(user_id)

    async def _remove_exclusion(self, guild_id: int, user_id: int) -> None:
        exclusions = self.excluded_users.get(guild_id)
        if exclusions is None:
            return
        exclusions.discard(user_id)
        if not exclusions:
            self.excluded_users.pop(guild_id, None)

    async def _get_moderation_result(self, message: discord.Message) -> tuple[bool, list[tuple[str, float]], dict[str, float]]:
        if not self.bot.ai_client:
            return False, [], {}

        try:
            response = await self.bot.ai_client.moderations.create(
                model="omni-moderation-latest",
                input=message.content,
            )
            result = response.results[0]
            categories = result.categories.model_dump() if hasattr(result.categories, "model_dump") else result.categories
            scores = result.category_scores.model_dump() if hasattr(result.category_scores, "model_dump") else result.category_scores
            return bool(result.flagged), [
                (name, scores.get(name, 0))
                for name in ("harassment", "hate", "self_harm", "sexual", "violence")
                if scores.get(name, 0) >= 0.7
            ], scores
        except Exception as exc:
            print(f"[ai_filter] moderation API failed: {exc}")
            return False, [], {}

    async def _notify_owner(self, guild: discord.Guild, author_name: str, channel_name: str, categories: list[str], content: str) -> None:
        owner = getattr(guild, "owner", None)
        if owner is None:
            return
        try:
            categories_text = ", ".join(categories) if categories else "unknown"
            preview = content[:500]
            await owner.send(
                f"Moderation alert: {author_name} posted flagged content in {channel_name}.\n"
                f"Categories: {categories_text}\n"
                f"Preview: {preview}"
            )
        except Exception:
            return

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.guild is None:
            return
        if message.author.guild_permissions.manage_messages:
            return
        if not message.content:
            return
        if await self._is_excluded(message.guild.id, message.author.id):
            return

        permissions = message.channel.permissions_for(message.guild.me)
        if not permissions.manage_messages:
            print(f"[ai_filter] Missing manage_messages permission in {message.guild.name}#{message.channel.name}")
            return

        try:
            flagged, high_risk, scores = await self._get_moderation_result(message)
            slur_detected = self._contains_obvious_slur(message.content)
            if not flagged and not high_risk and not slur_detected:
                return

            if not high_risk and not slur_detected:
                return
            if flagged and not high_risk and not slur_detected:
                high_risk = [("harassment", 0.95)]
            elif not high_risk and slur_detected:
                high_risk = [("harassment", 0.95)]

            try:
                await message.delete()
            except discord.Forbidden as exc:
                print(f"[ai_filter] Could not delete message: {exc}")
                return
            except Exception as exc:
                print(f"[ai_filter] Delete failed: {exc}")
                return

            warning_embed = discord.Embed(title="Auto-Moderation Flagged Message", color=discord.Color.red())
            warning_embed.add_field(name="Author", value=message.author.mention, inline=False)
            warning_embed.add_field(name="Channel", value=message.channel.mention, inline=False)
            warning_embed.add_field(name="Categories", value=", ".join(name for name, _ in high_risk), inline=False)
            warning_message = await message.channel.send(embed=warning_embed)
            await warning_message.delete(delay=10)
            await self._notify_owner(
                message.guild,
                message.author.display_name,
                message.channel.name,
                [name for name, _ in high_risk],
                message.content,
            )
            logs_channel = await self._get_mod_logs_channel(message.guild)
            if logs_channel is not None:
                log_embed = discord.Embed(title="Flagged Message Logged", color=discord.Color.dark_red())
                log_embed.add_field(name="Author", value=message.author.mention, inline=False)
                log_embed.add_field(name="Channel", value=message.channel.mention, inline=False)
                log_embed.add_field(name="Message", value=message.content[:1000], inline=False)
                log_embed.add_field(name="Categories", value=", ".join(name for name, _ in high_risk), inline=False)
                if self.bot.ai_client:
                    log_embed.add_field(name="Scores", value=", ".join(f"{name}={score:.2f}" for name, score in high_risk), inline=False)
                await logs_channel.send(embed=log_embed)
        except Exception:
            return


    @app_commands.command(name="exclude", description="Exclude a member from the AI moderation filter.")
    @app_commands.describe(member="The member to exclude")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def exclude(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        await self._add_exclusion(interaction.guild.id, member.id)
        await interaction.response.send_message(f"{member.mention} is now excluded from the AI filter.", ephemeral=True)

    @app_commands.command(name="excluded", description="Show the members currently excluded from the AI moderation filter.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def excluded(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        excluded_ids = self.excluded_users.get(interaction.guild.id, set())
        if not excluded_ids:
            await interaction.response.send_message("No members are currently excluded.", ephemeral=True)
            return
        members = [f"<@{user_id}>" for user_id in sorted(excluded_ids)]
        await interaction.response.send_message("Excluded members: " + ", ".join(members), ephemeral=True)

    @app_commands.command(name="unexclude", description="Remove a member from the AI moderation filter exclusions.")
    @app_commands.describe(member="The member to remove from exclusions")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def unexclude(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        await self._remove_exclusion(interaction.guild.id, member.id)
        await interaction.response.send_message(f"{member.mention} has been removed from the AI filter exclusions.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIFilterCog(bot))
