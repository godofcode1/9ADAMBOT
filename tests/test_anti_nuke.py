import asyncio
import unittest
from types import SimpleNamespace

from cogs.anti_nuke import AntiNukeCog


class AntiNukeCogTests(unittest.TestCase):
    def test_trigger_sends_appeal_dm(self) -> None:
        class DummyAppealsCog:
            def __init__(self) -> None:
                self.sent = []

            async def send_penalty_dm(self, member, action: str, reason: str) -> None:
                self.sent.append((member, action, reason))

        class DummyOwner:
            def __init__(self) -> None:
                self.messages = []
                self.mention = "<@owner>"

            async def send(self, message: str) -> None:
                self.messages.append(message)

        class DummySystemChannel:
            def __init__(self) -> None:
                self.messages = []

            async def send(self, message: str) -> None:
                self.messages.append(message)

        class DummyModerator:
            def __init__(self) -> None:
                self.id = 123
                self.roles_cleared = False
                self.mention = "<@123>"

            async def edit(self, *, roles) -> None:
                self.roles_cleared = True

        class DummyGuild:
            def __init__(self) -> None:
                self.owner = DummyOwner()
                self.system_channel = DummySystemChannel()
                self.name = "Test Guild"

            def get_member(self, member_id):
                return moderator if member_id == moderator.id else None

        appeals_cog = DummyAppealsCog()
        cog = AntiNukeCog.__new__(AntiNukeCog)
        cog.bot = SimpleNamespace(get_cog=lambda name: appeals_cog if name == "Appeals" else None)

        guild = DummyGuild()
        moderator = DummyModerator()

        asyncio.run(cog._trigger_anti_nuke(guild, moderator))

        # Roles stripped, owner + system channel alerted.
        self.assertTrue(moderator.roles_cleared)
        self.assertEqual(len(guild.owner.messages), 1)
        self.assertEqual(len(guild.system_channel.messages), 1)

        # The anti-nuke action is wired into the appeals flow.
        self.assertEqual(len(appeals_cog.sent), 1)
        member, action, reason = appeals_cog.sent[0]
        self.assertIs(member, moderator)
        self.assertEqual(action, "anti-nuked")

    def test_trigger_tolerates_missing_appeals_cog(self) -> None:
        class DummyModerator:
            def __init__(self) -> None:
                self.id = 123
                self.mention = "<@123>"

            async def edit(self, *, roles) -> None:
                pass

        class DummyGuild:
            owner = None
            system_channel = None
            name = "Test Guild"

            def get_member(self, member_id):
                return moderator if member_id == moderator.id else None

        cog = AntiNukeCog.__new__(AntiNukeCog)
        cog.bot = SimpleNamespace(get_cog=lambda name: None)

        # Should not raise when the Appeals cog is unavailable.
        moderator = DummyModerator()
        asyncio.run(cog._trigger_anti_nuke(DummyGuild(), moderator))


if __name__ == "__main__":
    unittest.main()
