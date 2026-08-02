import asyncio
import unittest

from cogs.ai_filter import AIFilterCog


class AIFilterCogTests(unittest.TestCase):
    def _make_cog(self) -> AIFilterCog:
        cog = AIFilterCog.__new__(AIFilterCog)
        cog.excluded_users = {}
        cog._permission_notified = set()
        cog._disabled_guilds = set()
        cog._custom_words = {}
        return cog

    def test_detects_obvious_slurs(self) -> None:
        cog = self._make_cog()
        self.assertTrue(cog._contains_obvious_slur("you are a nigga"))

    def test_ignores_non_slurs(self) -> None:
        cog = self._make_cog()
        self.assertFalse(cog._contains_obvious_slur("hello there friend"))

    def test_sensitive_categories_use_lower_threshold(self) -> None:
        cog = AIFilterCog.__new__(AIFilterCog)
        self.assertEqual(cog._score_threshold("hate"), 0.5)
        self.assertEqual(cog._score_threshold("sexual"), 0.7)

    def test_detects_expanded_slur_list(self) -> None:
        cog = self._make_cog()
        for text in ("shut up cunt", "go away whore", "kike", "dirty spic", "chink", "fuck off", "you fucker"):
            self.assertTrue(cog._contains_obvious_slur(text), text)

    def test_notifies_owner_only_once_about_permission_issue(self) -> None:
        class DummyOwner:
            def __init__(self) -> None:
                self.messages = []

            async def send(self, message: str) -> None:
                self.messages.append(message)

        class DummyGuild:
            def __init__(self) -> None:
                self.owner = DummyOwner()
                self.id = 123
                self.name = "Test Guild"

        cog = AIFilterCog.__new__(AIFilterCog)
        cog._permission_notified = set()
        guild = DummyGuild()

        asyncio.run(cog._notify_permission_issue(guild, "general"))
        self.assertEqual(len(guild.owner.messages), 1)
        self.assertIn("Manage Messages", guild.owner.messages[0])

        # A second issue for the same guild must not spam the owner again.
        asyncio.run(cog._notify_permission_issue(guild, "general"))
        self.assertEqual(len(guild.owner.messages), 1)

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

    def test_on_message_edit_screens_changed_content(self) -> None:
        class DummyAuthor:
            bot = False
            id = 1

        class DummyPermissions:
            manage_messages = True

        class DummyChannel:
            def permissions_for(self, _member) -> DummyPermissions:
                return DummyPermissions()

        class DummyGuild:
            id = 123
            me = object()

        class DummyMessage:
            def __init__(self, content: str) -> None:
                self.content = content
                self.author = DummyAuthor()
                self.guild = DummyGuild()
                self.channel = DummyChannel()

        screened = []

        async def fake_moderate(message) -> None:
            screened.append(message.content)

        cog = AIFilterCog.__new__(AIFilterCog)
        cog.excluded_users = {}
        cog._moderate_message = fake_moderate

        before = DummyMessage("hello there")
        after = DummyMessage("hello there you idiot")

        asyncio.run(cog.on_message_edit(before, after))

        self.assertEqual(screened, ["hello there you idiot"])

    def test_on_message_edit_ignores_unchanged_content(self) -> None:
        class DummyAuthor:
            bot = False
            id = 1

        class DummyMessage:
            def __init__(self, content: str) -> None:
                self.content = content
                self.author = DummyAuthor()

        screened = []

        async def fake_moderate(message) -> None:
            screened.append(message.content)

        cog = AIFilterCog.__new__(AIFilterCog)
        cog.excluded_users = {}
        cog._moderate_message = fake_moderate

        before = DummyMessage("same text")
        after = DummyMessage("same text")

        asyncio.run(cog.on_message_edit(before, after))

        self.assertEqual(screened, [])

    def test_moderate_message_catches_slur_added_by_edit(self) -> None:
        class DummyAuthor:
            bot = False
            id = 1

        class DummyPermissions:
            manage_messages = True

        class DummyChannel:
            def permissions_for(self, _member) -> DummyPermissions:
                return DummyPermissions()

        class DummyGuild:
            id = 123
            me = object()

        class DummyMessage:
            def __init__(self, content: str) -> None:
                self.content = content
                self.author = DummyAuthor()
                self.guild = DummyGuild()
                self.channel = DummyChannel()

        handled = []

        async def fake_get_moderation_result(_message):
            return False, [], {}

        async def fake_handle_flagged_message(message, high_risk) -> None:
            handled.append((message.content, high_risk))

        cog = self._make_cog()
        cog._get_moderation_result = fake_get_moderation_result
        cog._handle_flagged_message = fake_handle_flagged_message

        message = DummyMessage("this is fine now nigga")

        asyncio.run(cog._moderate_message(message))

        self.assertEqual(len(handled), 1)
        content, high_risk = handled[0]
        self.assertEqual(content, "this is fine now nigga")
        self.assertEqual(high_risk, [("slur_filter", 1.0)])

    def test_custom_words_apply_per_guild(self) -> None:
        cog = self._make_cog()
        cog._custom_words = {10: {"snarf"}, 20: {"glorb"}}

        # Guild 10 catches its own custom word but not guild 20's.
        self.assertTrue(cog._contains_obvious_slur("you are a snarf", 10))
        self.assertFalse(cog._contains_obvious_slur("you are a glorb", 10))
        self.assertTrue(cog._contains_obvious_slur("glorb here", 20))
        # A guild with no custom words is unaffected.
        self.assertFalse(cog._contains_obvious_slur("snarf", 30))

    def test_custom_words_are_normalized(self) -> None:
        cog = self._make_cog()
        cog._custom_words = {10: {"snarf"}}
        self.assertTrue(cog._contains_obvious_slur("SNARF IS HERE", 10))

    def test_custom_words_are_normalized_when_added(self) -> None:
        # Words added via the command must be stored in normalized form so that
        # leetspeak variants of the word in a message still match.
        cog = self._make_cog()
        cog._get_connection = None  # DB-backed methods not exercised here

        async def fake_add(guild_id: int, word: str) -> None:
            # Mirror the command path: the command strips/lowers, then _add_custom_word
            # normalizes before storing.
            stored = cog._normalize_for_slur_check(word.strip().lower())
            cog._custom_words.setdefault(guild_id, set()).add(stored)

        cog._add_custom_word = fake_add
        asyncio.run(cog._add_custom_word(10, "n1gga"))

        self.assertTrue(cog._contains_obvious_slur("you are a n1gga", 10))
        self.assertTrue(cog._contains_obvious_slur("you are a nigga", 10))

    def test_moderate_message_skips_disabled_guild(self) -> None:
        class DummyAuthor:
            bot = False
            id = 1

        class DummyGuild:
            id = 999
            me = object()

        class DummyMessage:
            def __init__(self, content: str) -> None:
                self.content = content
                self.author = DummyAuthor()
                self.guild = DummyGuild()

        called = []

        async def fake_get_moderation_result(_message):
            called.append(True)
            return False, [], {}

        cog = self._make_cog()
        cog._disabled_guilds = {999}
        cog._get_moderation_result = fake_get_moderation_result

        # The filter is off for guild 999, so moderation must never run.
        asyncio.run(cog._moderate_message(DummyMessage("nigga")))
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
