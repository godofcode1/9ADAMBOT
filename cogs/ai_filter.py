import re
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from cogs.command_helpers import get_data_dir

# Categories where a lower score threshold is enough to act.
_SENSITIVE_CATEGORIES = frozenset(
    {
        "harassment",
        "harassment/threatening",
        "hate",
        "hate/threatening",
        "violence",
        "violence/graphic",
    }
)
_DEFAULT_THRESHOLD = 0.7
_SENSITIVE_THRESHOLD = 0.5

# Compiled once at module load; matching runs on every message in every guild.
_SLUR_PATTERN = re.compile(
    r"\b(?:"
    r"salop|salope|connard|connasse|bitch|putain|asshole|shit|damn|cunt|whore|"
    r"faggot|fagot|fag|twink|fuck(?:er|ing|ed)?|"
    r"n+i+g+[ae]+r?|n+igg+[ae]+r?"
    r"|k+i+k+e+|s+p+i+c+|c+h+i+n+k+"
    r")\b",
    re.IGNORECASE,
)


class AIFilterCog(commands.Cog, name="AI Filter"):
    """Use the OpenAI Moderation endpoint to screen incoming messages."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.excluded_users: dict[int, set[int]] = {}
        self._permission_notified: set[int] = set()
        self._disabled_guilds: set[int] = set()
        self._custom_words: dict[int, set[str]] = {}
        self._connection: Optional[aiosqlite.Connection] = None
        self._db_path = get_data_dir() / "filter.db"

    async def _get_mod_logs_channel(self, guild: discord.Guild):
        for channel in guild.text_channels:
            if channel.name.lower() in {"mod-logs", "moderation-logs", "audit-logs", "staff-logs"}:
                return channel
        return None

    def _normalize_for_slur_check(self, text: str) -> str:
        normalized = text.lower()
        normalized = re.sub(r"(.)\1+", r"\1", normalized)
        for source, target in (("0", "o"), ("1", "i"), ("3", "e"), ("4", "a"), ("5", "s"), ("7", "t"), ("@", "a"), ("$", "s")):
            normalized = normalized.replace(source, target)
        return normalized

    def _contains_obvious_slur(self, text: str, guild_id: Optional[int] = None) -> bool:
        normalized = self._normalize_for_slur_check(text)
        if _SLUR_PATTERN.search(normalized) or _SLUR_PATTERN.search(text):
            return True
        # Per-guild custom words are stored normalized, so matching them against
        # the normalized text keeps leetspeak/repeated-char words working.
        guild_words = self._custom_words.get(guild_id if guild_id is not None else 0, set())
        for word in guild_words:
            if re.search(rf"\b{re.escape(word)}\b", normalized):
                return True
        return False

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

    def _score_threshold(self, category: str) -> float:
        return _SENSITIVE_THRESHOLD if category in _SENSITIVE_CATEGORIES else _DEFAULT_THRESHOLD

    async def _initialize_db(self) -> None:
        """Create the SQLite tables used to store per-guild filter settings."""
        self._connection = await aiosqlite.connect(self._db_path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS filter_settings (
                guild_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        await self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS filter_words (
                guild_id INTEGER NOT NULL,
                word TEXT NOT NULL,
                PRIMARY KEY (guild_id, word)
            )
            """
        )
        await self._connection.commit()

    async def _get_connection(self) -> aiosqlite.Connection:
        """Return the active SQLite connection, creating it if needed."""
        if self._connection is None:
            await self._initialize_db()
        return self._connection

    async def _load_settings(self) -> None:
        """Load per-guild enabled flags and custom words into memory."""
        conn = await self._get_connection()
        async with conn.execute("SELECT guild_id, enabled FROM filter_settings") as cursor:
            settings_rows = await cursor.fetchall()
        self._disabled_guilds = {
            int(row["guild_id"]) for row in settings_rows if not int(row["enabled"])
        }

        self._custom_words = {}
        async with conn.execute("SELECT guild_id, word FROM filter_words") as cursor:
            word_rows = await cursor.fetchall()
        for row in word_rows:
            self._custom_words.setdefault(int(row["guild_id"]), set()).add(str(row["word"]))

    async def _is_filter_enabled(self, guild_id: int) -> bool:
        return guild_id not in self._disabled_guilds

    async def _set_filter_enabled(self, guild_id: int, enabled: bool) -> None:
        conn = await self._get_connection()
        await conn.execute(
            "INSERT INTO filter_settings (guild_id, enabled) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET enabled = excluded.enabled",
            (guild_id, int(enabled)),
        )
        await conn.commit()
        if enabled:
            self._disabled_guilds.discard(guild_id)
        else:
            self._disabled_guilds.add(guild_id)

    async def _add_custom_word(self, guild_id: int, word: str) -> None:
        # Store the normalized form so it matches the normalized message text
        # (e.g. "n1gga" is stored as "nigga").
        word = self._normalize_for_slur_check(word.strip().lower())
        if not word:
            return
        conn = await self._get_connection()
        await conn.execute(
            "INSERT OR IGNORE INTO filter_words (guild_id, word) VALUES (?, ?)",
            (guild_id, word),
        )
        await conn.commit()
        self._custom_words.setdefault(guild_id, set()).add(word)

    async def _remove_custom_word(self, guild_id: int, word: str) -> None:
        # Words are stored normalized, so normalize before looking them up.
        word = self._normalize_for_slur_check(word.strip().lower())
        conn = await self._get_connection()
        await conn.execute(
            "DELETE FROM filter_words WHERE guild_id = ? AND word = ?",
            (guild_id, word),
        )
        await conn.commit()
        self._custom_words.get(guild_id, set()).discard(word)

    async def _get_moderation_result(
        self, message: discord.Message
    ) -> tuple[bool, list[tuple[str, float]], dict[str, float]]:
        if not self.bot.ai_client:
            return False, [], {}

        try:
            response = await self.bot.ai_client.moderations.create(
                model="omni-moderation-latest",
                input=message.content,
            )
            result = response.results[0]
            categories = (
                result.categories.model_dump()
                if hasattr(result.categories, "model_dump")
                else dict(result.categories)
            )
            scores = (
                result.category_scores.model_dump()
                if hasattr(result.category_scores, "model_dump")
                else dict(result.category_scores)
            )

            high_risk: list[tuple[str, float]] = []
            for name, score in scores.items():
                threshold = self._score_threshold(name)
                if score >= threshold or categories.get(name, False):
                    high_risk.append((name, float(score)))

            flagged = bool(result.flagged) or bool(high_risk)
            return flagged, high_risk, scores
        except Exception as exc:
            print(f"[ai_filter] moderation API failed: {exc}")
            return False, [], {}

    async def _notify_owner(
        self,
        guild: discord.Guild,
        author_name: str,
        channel_name: str,
        categories: list[str],
        content: str,
    ) -> None:
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

    async def _notify_permission_issue(self, guild: discord.Guild, channel_name: str) -> None:
        """Tell the guild owner once that the filter cannot act without Manage Messages."""
        if guild.id in self._permission_notified:
            return
        self._permission_notified.add(guild.id)
        owner = getattr(guild, "owner", None)
        if owner is None:
            return
        try:
            await owner.send(
                f"⚠️ The AI moderation filter in **{guild.name}** cannot delete messages.\n"
                f"I need the **Manage Messages** permission to remove slurs and flagged content, "
                f"but I don't have it in #{channel_name}. Grant my role that permission and "
                "the filter will start working automatically."
            )
        except Exception:
            return

    async def _handle_flagged_message(
        self,
        message: discord.Message,
        high_risk: list[tuple[str, float]],
    ) -> None:
        try:
            await message.delete()
            print(f"[ai_filter] Deleted flagged message from {message.author} in {message.guild.name}#{message.channel.name}")
        except discord.Forbidden as exc:
            print(f"[ai_filter] Could not delete message: {exc}")
            await self._notify_permission_issue(message.guild, message.channel.name)
            return
        except Exception as exc:
            print(f"[ai_filter] Delete failed: {exc}")
            return

        warning_embed = discord.Embed(title="Auto-Moderation Flagged Message", color=discord.Color.red())
        warning_embed.add_field(name="Author", value=message.author.mention, inline=False)
        warning_embed.add_field(name="Channel", value=message.channel.mention, inline=False)
        warning_embed.add_field(
            name="Categories",
            value=", ".join(name for name, _ in high_risk) or "flagged",
            inline=False,
        )
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
            log_embed.add_field(
                name="Categories",
                value=", ".join(name for name, _ in high_risk) or "flagged",
                inline=False,
            )
            if self.bot.ai_client and high_risk:
                log_embed.add_field(
                    name="Scores",
                    value=", ".join(f"{name}={score:.2f}" for name, score in high_risk),
                    inline=False,
                )
            await logs_channel.send(embed=log_embed)

    async def cog_load(self) -> None:
        """Load persisted filter settings when the cog starts."""
        try:
            await self._initialize_db()
            await self._load_settings()
        except Exception as exc:
            print(f"[ai_filter] Failed to load filter settings: {exc}")

    async def _moderate_message(self, message: discord.Message) -> None:
        """Screen a message's content and act on it if it is flagged.

        Shared by ``on_message`` and ``on_message_edit`` so that users cannot
        dodge the filter by editing a message to add a slur after the fact.
        """
        if message.author.bot:
            return
        if message.guild is None:
            return
        if not message.content:
            return
        if not await self._is_filter_enabled(message.guild.id):
            return
        if await self._is_excluded(message.guild.id, message.author.id):
            return

        permissions = message.channel.permissions_for(message.guild.me)
        if not permissions.manage_messages:
            print(f"[ai_filter] Missing manage_messages permission in {message.guild.name}#{message.channel.name}")
            await self._notify_permission_issue(message.guild, message.channel.name)
            return

        try:
            flagged, high_risk, _scores = await self._get_moderation_result(message)
            slur_detected = self._contains_obvious_slur(message.content, message.guild.id)

            if slur_detected and not high_risk:
                high_risk = [("slur_filter", 1.0)]

            if flagged and not high_risk:
                high_risk = [("moderation_api", 1.0)]

            if not high_risk:
                return

            await self._handle_flagged_message(message, high_risk)
        except Exception as exc:
            print(f"[ai_filter] Unexpected error while moderating message: {exc}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self._moderate_message(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        # Only re-screen if the text actually changed (edits also fire for
        # embed/attachment changes, which we should not act on). Whitespace-only
        # tweaks are ignored to avoid needless API calls.
        if before.content == after.content:
            return
        if (before.content or "").strip() == (after.content or "").strip():
            return
        await self._moderate_message(after)

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

    @app_commands.command(name="filter", description="Turn the AI moderation filter on or off for this server.")
    @app_commands.describe(state="Whether the filter should be active")
    @app_commands.choices(
        state=[
            app_commands.Choice(name="On", value="on"),
            app_commands.Choice(name="Off", value="off"),
        ]
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def filter(self, interaction: discord.Interaction, state: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        enabled = state == "on"
        await self._set_filter_enabled(interaction.guild.id, enabled)
        await interaction.response.send_message(
            f"AI filter is now **{'on' if enabled else 'off'}** for this server.",
            ephemeral=True,
        )

    @app_commands.command(name="filterword", description="Add or remove a custom word this server's filter should catch.")
    @app_commands.describe(action="Add or remove", word="The word to add or remove")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Add", value="add"),
            app_commands.Choice(name="Remove", value="remove"),
        ]
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def filterword(self, interaction: discord.Interaction, action: str, word: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        word = word.strip().lower()
        if not word:
            await interaction.response.send_message("Please provide a word.", ephemeral=True)
            return
        if action == "add":
            await self._add_custom_word(interaction.guild.id, word)
            await interaction.response.send_message(f"Added **{word}** to this server's filter.", ephemeral=True)
        else:
            await self._remove_custom_word(interaction.guild.id, word)
            await interaction.response.send_message(f"Removed **{word}** from this server's filter (if present).", ephemeral=True)

    @app_commands.command(name="filterwords", description="List this server's custom filter words.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def filterwords(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        words = sorted(self._custom_words.get(interaction.guild.id, set()))
        if not words:
            await interaction.response.send_message("No custom filter words are set for this server.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Custom filter words: " + ", ".join(f"`{word}`" for word in words),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIFilterCog(bot))
