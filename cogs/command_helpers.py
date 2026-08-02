from __future__ import annotations

import discord
from discord import app_commands


async def member_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Searchable member list for slash command parameters."""
    if interaction.guild is None:
        return []

    current_lower = current.lower()
    choices: list[app_commands.Choice[str]] = []
    for member in interaction.guild.members:
        label = f"{member.display_name} ({member.name})"
        haystack = f"{label} {member.id}".lower()
        if not current or current_lower in haystack or current.strip() == str(member.id):
            choices.append(app_commands.Choice(name=label[:100], value=str(member.id)))
        if len(choices) >= 25:
            break
    return choices


async def banned_user_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Searchable ban list for /unban."""
    if interaction.guild is None:
        return []

    current_lower = current.lower()
    choices: list[app_commands.Choice[str]] = []
    try:
        async for ban_entry in interaction.guild.bans(limit=100):
            user = ban_entry.user
            label = f"{user.name} ({user.id})"
            haystack = f"{label} {ban_entry.reason or ''}".lower()
            if not current or current_lower in haystack or current.strip() == str(user.id):
                choices.append(app_commands.Choice(name=label[:100], value=str(user.id)))
            if len(choices) >= 25:
                break
    except discord.Forbidden:
        return []
    return choices


async def resolve_member(guild: discord.Guild, member_id: str) -> discord.Member:
    member = guild.get_member(int(member_id))
    if member is not None:
        return member
    return await guild.fetch_member(int(member_id))
