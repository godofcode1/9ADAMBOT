import asyncio
import os
import tempfile
import time
import unittest
from types import SimpleNamespace

import discord

from cogs.command_helpers import get_data_dir
from cogs.moderation import ModerationCog


def _snowflake(days_ago: float = 0) -> int:
    """Return a plausible Discord snowflake, optionally from N days ago."""
    ms = (time.time() - days_ago * 86400) * 1000.0
    return (int(ms) - 1420070400000) << 22


def _not_found() -> discord.NotFound:
    response = SimpleNamespace(status=404, reason="Not Found", text="404 Not Found")
    return discord.NotFound(response, {"code": 10008, "message": "Unknown Message"})


class DummyMessage:
    def __init__(self, message_id: int, already_gone: bool = False, author=None) -> None:
        self.id = message_id
        self.type = SimpleNamespace(is_deletable=lambda: True)
        self._already_gone = already_gone
        self.author = author
        self.delete_calls = 0

    async def delete(self) -> None:
        self.delete_calls += 1
        if self._already_gone:
            raise _not_found()


class DummyChannel:
    def __init__(self, messages: list, bulk_fails: bool = False, can_act: bool = True) -> None:
        self.messages = messages
        self.bulk_fails = bulk_fails
        self.can_act = can_act
        self.bulk_calls = []
        self.deleted_messages = []

    def history(self, limit: int):
        async def gen():
            for message in self.messages[:limit]:
                yield message

        return gen()

    def permissions_for(self, _member):
        return SimpleNamespace(
            manage_messages=self.can_act,
            read_message_history=self.can_act,
        )

    async def delete_messages(self, messages, *, reason=None) -> None:
        self.bulk_calls.append(messages)
        if self.bulk_fails:
            raise _not_found()
        self.deleted_messages.extend(messages)


class CommandHelpersTests(unittest.TestCase):
    def test_get_data_dir_uses_data_dir_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("DATA_DIR")
            os.environ["DATA_DIR"] = tmp
            try:
                data_dir = get_data_dir()
            finally:
                if old is None:
                    os.environ.pop("DATA_DIR", None)
                else:
                    os.environ["DATA_DIR"] = old

            self.assertEqual(str(data_dir), tmp)
            self.assertTrue(data_dir.exists())

    def test_get_data_dir_creates_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "nested", "data")
            old = os.environ.get("DATA_DIR")
            os.environ["DATA_DIR"] = target
            try:
                data_dir = get_data_dir()
            finally:
                if old is None:
                    os.environ.pop("DATA_DIR", None)
                else:
                    os.environ["DATA_DIR"] = old

            self.assertTrue(data_dir.exists())


class ModerationCogTests(unittest.TestCase):
    def test_log_action_handles_plain_string_targets(self) -> None:
        class DummyChannel:
            def __init__(self) -> None:
                self.name = "mod-logs"
                self.embeds = []

            async def send(self, embed) -> None:
                self.embeds.append(embed)

        class DummyGuild:
            def __init__(self) -> None:
                self.text_channels = [DummyChannel()]

        class DummyModerator:
            @property
            def mention(self) -> str:
                return "<@moderator>"

        cog = ModerationCog.__new__(ModerationCog)
        guild = DummyGuild()

        # Unban passes a plain "<@id>" string as the target (the user is no
        # longer in the guild, so there is no Member object with .mention).
        asyncio.run(cog._log_action(guild, "Unban", "<@12345>", DummyModerator(), "appealed"))

        self.assertEqual(len(guild.text_channels[0].embeds), 1)
        embed = guild.text_channels[0].embeds[0]
        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields["Target"], "<@12345>")

    def test_purge_bulk_delete_succeeds_normally(self) -> None:
        cog = ModerationCog.__new__(ModerationCog)
        messages = [DummyMessage(_snowflake()), DummyMessage(_snowflake())]
        channel = DummyChannel(messages)

        deleted = asyncio.run(cog._purge_messages(channel, limit=100))

        self.assertEqual(deleted, 2)
        self.assertEqual(len(channel.bulk_calls), 1)
        self.assertEqual(len(channel.deleted_messages), 2)

    def test_purge_falls_back_to_single_delete_when_bulk_404s(self) -> None:
        cog = ModerationCog.__new__(ModerationCog)
        messages = [DummyMessage(_snowflake()), DummyMessage(_snowflake()), DummyMessage(_snowflake())]
        channel = DummyChannel(messages, bulk_fails=True)

        deleted = asyncio.run(cog._purge_messages(channel, limit=100))

        self.assertEqual(deleted, 3)
        # Bulk delete failed with 404, so every message was deleted individually.
        self.assertEqual(len(channel.bulk_calls), 1)
        for message in messages:
            self.assertEqual(message.delete_calls, 1)

    def test_purge_skips_messages_already_deleted_mid_purge(self) -> None:
        cog = ModerationCog.__new__(ModerationCog)
        gone = DummyMessage(_snowflake(), already_gone=True)
        alive = DummyMessage(_snowflake())
        channel = DummyChannel([gone, alive], bulk_fails=True)

        deleted = asyncio.run(cog._purge_messages(channel, limit=100))

        self.assertEqual(deleted, 1)
        self.assertEqual(gone.delete_calls, 1)  # attempted but raised 404
        self.assertEqual(alive.delete_calls, 1)

    def test_purge_deletes_old_messages_individually(self) -> None:
        cog = ModerationCog.__new__(ModerationCog)
        old = DummyMessage(_snowflake(days_ago=20))
        fresh = DummyMessage(_snowflake())
        channel = DummyChannel([fresh, old])

        deleted = asyncio.run(cog._purge_messages(channel, limit=100))

        self.assertEqual(deleted, 2)
        # The fresh message went through the bulk path; the old one did not.
        self.assertEqual(len(channel.bulk_calls), 1)
        self.assertEqual(len(channel.deleted_messages), 1)
        self.assertEqual(old.delete_calls, 1)
        self.assertEqual(fresh.delete_calls, 0)

    def test_purge_member_across_channels(self) -> None:
        target = SimpleNamespace(id=1)
        other = SimpleNamespace(id=2)

        channel_a = DummyChannel([
            DummyMessage(_snowflake(), author=target),
            DummyMessage(_snowflake(), author=other),
        ])
        channel_b = DummyChannel([
            DummyMessage(_snowflake(), author=target),
        ])
        guild = SimpleNamespace(text_channels=[channel_a, channel_b], threads=[], me=object())
        cog = ModerationCog.__new__(ModerationCog)

        deleted = asyncio.run(cog._purge_member_across_channels(guild, target, limit=100))

        self.assertEqual(deleted, 2)
        self.assertEqual(len(channel_a.deleted_messages), 1)
        self.assertEqual(len(channel_b.deleted_messages), 1)

    def test_purge_member_across_channels_skips_unreachable_channels(self) -> None:
        target = SimpleNamespace(id=1)
        blocked = DummyChannel([DummyMessage(_snowflake(), author=target)], can_act=False)
        accessible = DummyChannel([DummyMessage(_snowflake(), author=target)])
        guild = SimpleNamespace(text_channels=[blocked, accessible], threads=[], me=object())
        cog = ModerationCog.__new__(ModerationCog)

        deleted = asyncio.run(cog._purge_member_across_channels(guild, target, limit=100))

        # The channel without Manage Messages/read history is skipped entirely.
        self.assertEqual(deleted, 1)
        self.assertEqual(len(blocked.bulk_calls), 0)
        self.assertEqual(len(accessible.deleted_messages), 1)


if __name__ == "__main__":
    unittest.main()
