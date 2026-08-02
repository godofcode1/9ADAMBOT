import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import dotenv_values, load_dotenv
from openai import AsyncOpenAI


env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(env_path, override=True)

values = dotenv_values(env_path)
if values.get("DISCORD_TOKEN"):
    os.environ["DISCORD_TOKEN"] = values["DISCORD_TOKEN"]
if values.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = values["OPENAI_API_KEY"]


class ModerationBot(commands.Bot):
    """Production-grade Discord moderation bot with modular cogs and async persistence."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.ai_client = None

    async def setup_hook(self) -> None:
        token = os.getenv("DISCORD_TOKEN")
        openai_key = os.getenv("OPENAI_API_KEY")
        if token:
            self.ai_client = AsyncOpenAI(api_key=openai_key) if openai_key else None
        await self.load_all_cogs()

    async def load_all_cogs(self) -> None:
        cogs_dir = Path(__file__).resolve().parent / "cogs"
        for cog_file in sorted(cogs_dir.glob("*.py")):
            if cog_file.name.startswith("__"):
                continue
            cog_name = f"cogs.{cog_file.stem}"
            try:
                await self.load_extension(cog_name)
            except Exception as exc:
                print(f"Failed to load {cog_name}: {exc}")

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Connected to {len(self.guilds)} guild(s).")
        if not self.guilds:
            print("No guilds found. Invite the bot to a server before channel auto-creation can happen.")
            return

        for guild in self.guilds:
            try:
                await self.tree.sync(guild=discord.Object(guild.id))
            except Exception as exc:
                print(f"Failed to sync commands for {guild.name}: {exc}")

        print("Slash commands synced for the current guilds.")


if __name__ == "__main__":
    bot = ModerationBot()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN not set. Add it to your .env file.")
    bot.run(token)
