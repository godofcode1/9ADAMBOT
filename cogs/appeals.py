import discord
from discord.ext import commands


class SubmitAppealButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot, member: discord.Member, guild_id: int, action: str, reason: str) -> None:
        super().__init__(label="Submit Appeal", style=discord.ButtonStyle.primary)
        self.bot = bot
        self.member = member
        self.guild_id = guild_id
        self.action = action
        self.reason = reason

    async def callback(self, interaction: discord.Interaction) -> None:
        modal = AppealModal(self.bot, self.member, self.guild_id, self.action, self.reason)
        await interaction.response.send_modal(modal)


class AppealModal(discord.ui.Modal, title="Appeal Submission"):
    def __init__(
        self,
        bot: commands.Bot,
        member: discord.Member,
        guild_id: int,
        action: str,
        reason: str,
    ) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.member = member
        self.guild_id = guild_id
        self.action = action
        self.reason = reason
        self.penalty_reason = discord.ui.TextInput(label="Why were you penalized?", style=discord.TextStyle.paragraph)
        self.lift_reason = discord.ui.TextInput(label="Why should your penalty be lifted?", style=discord.TextStyle.paragraph)
        self.add_item(self.penalty_reason)
        self.add_item(self.lift_reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            try:
                guild = await self.bot.fetch_guild(self.guild_id)
            except Exception:
                guild = None

        if guild is None:
            await interaction.response.send_message(
                "I could not find the server for this appeal. Contact staff directly.",
                ephemeral=True,
            )
            return

        appeals_channel = await self._get_appeals_channel(guild)
        if appeals_channel is None:
            await interaction.response.send_message(
                "No appeals channel is configured and I cannot create one without channel permissions.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="New Appeal Submitted", color=discord.Color.blurple())
        embed.add_field(name="User", value=self.member.mention, inline=False)
        embed.add_field(name="Penalty", value=self.action, inline=False)
        embed.add_field(name="Original Reason", value=self.reason, inline=False)
        embed.add_field(name="Why penalized", value=self.penalty_reason.value, inline=False)
        embed.add_field(name="Why should it be lifted", value=self.lift_reason.value, inline=False)
        view = AppealResolutionView(self.member)
        await appeals_channel.send(embed=embed, view=view)
        await interaction.response.send_message("Your appeal has been submitted to staff.", ephemeral=True)

    async def _get_appeals_channel(self, guild: discord.Guild) -> discord.abc.GuildChannel | None:
        channel = discord.utils.get(guild.text_channels, name="mod-appeals")
        if channel is not None:
            return channel
        if not guild.me.guild_permissions.manage_channels:
            return None
        return await guild.create_text_channel("mod-appeals", reason="Auto-created moderation appeals channel")


class AppealResolutionView(discord.ui.View):
    def __init__(self, member: discord.Member) -> None:
        super().__init__(timeout=None)
        self.member = member
        self.add_item(AppealDecisionButton("Approve", discord.ButtonStyle.green, member))
        self.add_item(AppealDecisionButton("Deny", discord.ButtonStyle.red, member))


class AppealDecisionButton(discord.ui.Button):
    def __init__(self, label: str, style: discord.ButtonStyle, member: discord.Member) -> None:
        super().__init__(label=label, style=style)
        self.member = member

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("You need moderator permissions to resolve appeals.", ephemeral=True)
            return
        await interaction.response.send_message(f"Appeal {self.label}d by {interaction.user.mention}", ephemeral=True)


class AppealsCog(commands.Cog, name="Appeals"):
    """Provides DM-based appeals with a modal and resolution buttons."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """Create the appeals channel automatically when the cog loads."""
        await self._ensure_appeals_channels_for_all_guilds()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Create the appeals channel once the bot is fully ready."""
        await self._ensure_appeals_channels_for_all_guilds()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Create the appeals channel when the bot joins a new guild."""
        await self._ensure_appeals_channel(guild)

    async def _ensure_appeals_channels_for_all_guilds(self) -> None:
        for guild in self.bot.guilds:
            await self._ensure_appeals_channel(guild)

    async def _ensure_appeals_channel(self, guild: discord.Guild) -> None:
        channel = discord.utils.get(guild.text_channels, name="mod-appeals")
        if channel is not None:
            return
        perms = guild.me.guild_permissions
        print(f"[appeals] Trying mod-appeals in {guild.name} | manage_channels={perms.manage_channels} | administrator={perms.administrator}")
        if not perms.manage_channels and not perms.administrator:
            print(f"[appeals] Missing manage_channels permission in {guild.name}; skipped mod-appeals creation.")
            return
        try:
            await guild.create_text_channel("mod-appeals", reason="Auto-created moderation appeals channel")
            print(f"[appeals] Created mod-appeals in {guild.name}")
        except discord.Forbidden as exc:
            print(f"[appeals] Forbidden creating mod-appeals in {guild.name}: {exc}")
        except Exception as exc:
            print(f"[appeals] Failed creating mod-appeals in {guild.name}: {exc}")

    async def send_penalty_dm(self, member: discord.Member, action: str, reason: str) -> None:
        try:
            view = discord.ui.View(timeout=1800)
            view.add_item(SubmitAppealButton(self.bot, member, member.guild.id, action, reason))
            await member.send(
                f"You were {action} in {member.guild.name}. If you believe this was incorrect, you can submit an appeal.",
                view=view,
            )
        except Exception:
            return


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AppealsCog(bot))
