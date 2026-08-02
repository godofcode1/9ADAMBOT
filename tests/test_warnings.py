import asyncio
import unittest
from types import SimpleNamespace

from cogs.warnings import WarningsCog


class DummyMessage:
    def __init__(self, author, pinned=False):
        self.author = author
        self.pinned = pinned


class DummyChannel:
    def __init__(self, messages):
        self.messages = messages
        self.guild = SimpleNamespace(
            me=SimpleNamespace(guild_permissions=SimpleNamespace(manage_messages=True))
        )
        self.purged = None

    def permissions_for(self, _member):
        return SimpleNamespace(manage_messages=True)

    async def purge(self, limit=100, check=None):
        self.purged = (limit, check)
        return [message for message in self.messages if check is None or check(message)]


class WarningsCogTests(unittest.TestCase):
    def test_deletes_matching_messages_from_channel(self) -> None:
        cog = WarningsCog.__new__(WarningsCog)
        target = SimpleNamespace(id=1)
        other = SimpleNamespace(id=2)
        channel = DummyChannel([DummyMessage(target), DummyMessage(other)])

        deleted_count = asyncio.run(cog._delete_recent_messages_in_channel(channel, target, limit=5))

        self.assertEqual(deleted_count, 1)
        self.assertEqual(channel.purged[0], 5)


if __name__ == "__main__":
    unittest.main()
