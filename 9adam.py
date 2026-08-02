"""Entry point for the moderation bot.

Run this file with the virtual environment Python interpreter to start the bot.
"""

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from main import ModerationBot


env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(env_path, override=True)

values = dotenv_values(env_path)
if values.get("DISCORD_TOKEN"):
    os.environ["DISCORD_TOKEN"] = values["DISCORD_TOKEN"]
if values.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = values["OPENAI_API_KEY"]


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN not set. Add it to your .env file.")
    bot = ModerationBot()
    bot.run(token)


if __name__ == "__main__":
    main()
