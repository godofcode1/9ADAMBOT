import asyncio
import unittest

from cogs.ai_filter import AIFilterCog


class AIFilterCogTests(unittest.TestCase):
    def test_detects_obvious_slurs(self) -> None:
        cog = AIFilterCog.__new__(AIFilterCog)
        self.assertTrue(asyncio.run(cog._contains_obvious_slur("you are a nigga")))

    def test_ignores_non_slurs(self) -> None:
        cog = AIFilterCog.__new__(AIFilterCog)
        self.assertFalse(asyncio.run(cog._contains_obvious_slur("hello there friend")))

    def test_notifies_owner_when_flagged_message_is_handled(self) -> None:
        class DummyOwner:
            def __init__(self) -> None:
                self.messages = []

            async def send(self, message: str) -> None:
                self.messages.append(message)

        class DummyGuild:
            def __init__(self) -> None:
                self.owner = DummyOwner()

        cog = AIFilterCog.__new__(AIFilterCog)
        guild = DummyGuild()

        asyncio.run(cog._notify_owner(guild, "Alice", "#general", ["harassment"], "bad message"))

        self.assertEqual(len(guild.owner.messages), 1)
        self.assertIn("Alice", guild.owner.messages[0])
        self.assertIn("#general", guild.owner.messages[0])


if __name__ == "__main__":
    import asyncio

    unittest.main()
