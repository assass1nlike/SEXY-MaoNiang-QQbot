from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot


nonebot.init()

from plugins import auto_reply  # noqa: E402


class PublicConversationTests(unittest.TestCase):
    def setUp(self) -> None:
        auto_reply._conversation_histories.clear()
        auto_reply._conversation_locks.clear()
        auto_reply._public_histories.clear()
        auto_reply._public_locks.clear()

    def test_parse_public_prompt_requires_newline(self) -> None:
        self.assertEqual(auto_reply._parse_public_prompt("公共\n实际内容"), "实际内容")
        self.assertEqual(
            auto_reply._parse_public_prompt("  公共\r\n实际内容"),
            "实际内容",
        )
        self.assertIsNone(auto_reply._parse_public_prompt("公共 实际内容"))
        self.assertIsNone(auto_reply._parse_public_prompt("普通消息"))
        self.assertEqual(auto_reply._parse_public_prompt("公共\n"), "")

    def test_public_prompt_contains_only_group_display_name_and_body(self) -> None:
        event = SimpleNamespace(
            sender=SimpleNamespace(card="群名片", nickname="QQ昵称")
        )
        self.assertEqual(
            auto_reply._format_public_prompt(event, "实际内容"),
            "[群名片]\n实际内容",
        )

    def test_public_prompt_uses_nickname_when_group_card_is_empty(self) -> None:
        event = SimpleNamespace(sender=SimpleNamespace(card="", nickname="QQ昵称"))
        self.assertEqual(
            auto_reply._format_public_prompt(event, "实际内容"),
            "[QQ昵称]\n实际内容",
        )

    def test_public_and_private_system_prompts_are_selected_separately(self) -> None:
        with (
            patch.object(auto_reply, "SYSTEM_PROMPT", "private instructions"),
            patch.object(auto_reply, "PUBLIC_SYSTEM_PROMPT", "public instructions"),
        ):
            self.assertEqual(
                auto_reply._instructions_for(is_public=False),
                "private instructions",
            )
            self.assertEqual(
                auto_reply._instructions_for(is_public=True),
                "public instructions",
            )

    def test_public_and_private_prefills_are_selected_separately(self) -> None:
        with (
            patch.object(auto_reply, "PREFILL_USER", "private user"),
            patch.object(auto_reply, "PREFILL_ASSISTANT", "private assistant"),
            patch.object(auto_reply, "PUBLIC_PREFILL_USER", "[Alice]\npublic user"),
            patch.object(auto_reply, "PUBLIC_PREFILL_ASSISTANT", "public assistant"),
        ):
            self.assertEqual(
                auto_reply._prefill_for(is_public=False),
                ("private user", "private assistant"),
            )
            self.assertEqual(
                auto_reply._prefill_for(is_public=True),
                ("[Alice]\npublic user", "public assistant"),
            )

    def test_public_prefill_is_used_as_the_complete_message_content(self) -> None:
        messages = auto_reply._build_input(
            "[Bob]\ncurrent message",
            prefill_user="[Alice]\nexample message",
            prefill_assistant="example answer",
        )
        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "[Alice]\nexample message"},
                {"role": "assistant", "content": "example answer"},
                {"role": "user", "content": "[Bob]\ncurrent message"},
            ],
        )

    def test_public_history_keeps_at_most_500_messages(self) -> None:
        for index in range(251):
            auto_reply._remember_public_turn(
                100,
                f"user-{index}",
                f"assistant-{index}",
            )

        history = list(auto_reply._public_history_for(100))
        self.assertEqual(len(history), 500)
        self.assertEqual(history[0], {"role": "user", "content": "user-1"})
        self.assertEqual(
            history[-1],
            {"role": "assistant", "content": "assistant-250"},
        )

    def test_public_histories_are_isolated_between_groups(self) -> None:
        auto_reply._remember_public_turn(100, "from user A", "answer A")
        auto_reply._remember_public_turn(100, "from user B", "answer B")
        auto_reply._remember_public_turn(200, "other group", "other answer")

        self.assertEqual(len(auto_reply._public_history_for(100)), 4)
        self.assertEqual(len(auto_reply._public_history_for(200)), 2)
        self.assertIsNot(
            auto_reply._public_history_for(100),
            auto_reply._public_history_for(200),
        )

    def test_public_and_private_histories_do_not_mix(self) -> None:
        private_key = (100, 12345)
        auto_reply._remember_turn(private_key, "private", "private answer")
        auto_reply._remember_public_turn(100, "public", "public answer")

        self.assertEqual(
            list(auto_reply._history_for(private_key)),
            [
                {"role": "user", "content": "private"},
                {"role": "assistant", "content": "private answer"},
            ],
        )
        self.assertEqual(
            list(auto_reply._public_history_for(100)),
            [
                {"role": "user", "content": "public"},
                {"role": "assistant", "content": "public answer"},
            ],
        )

    def test_private_commands_require_exact_non_public_messages(self) -> None:
        self.assertEqual(auto_reply._private_command_for("清空", False), "clear")
        self.assertEqual(auto_reply._private_command_for("评分", False), "rating")
        self.assertIsNone(auto_reply._private_command_for("清空一下", False))
        self.assertIsNone(auto_reply._private_command_for("评分 现在", False))
        self.assertIsNone(auto_reply._private_command_for("清空", True))
        self.assertIsNone(auto_reply._private_command_for("评分", True))

    def test_clearing_private_history_does_not_touch_public_history(self) -> None:
        private_key = (100, 12345)
        auto_reply._remember_turn(private_key, "private", "private answer")
        auto_reply._remember_public_turn(100, "public", "public answer")

        auto_reply._clear_private_history(private_key)

        self.assertNotIn(private_key, auto_reply._conversation_histories)
        self.assertEqual(len(auto_reply._public_history_for(100)), 2)


class PrivateCommandHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        auto_reply._conversation_histories.clear()
        auto_reply._conversation_locks.clear()
        auto_reply._public_histories.clear()
        auto_reply._public_locks.clear()

    @staticmethod
    def _event(text: str) -> SimpleNamespace:
        return SimpleNamespace(
            group_id=100,
            user_id=12345,
            self_id=3440049649,
            message_id=999,
            get_plaintext=lambda: text,
        )

    async def test_clear_command_clears_without_calling_api(self) -> None:
        key = (100, 12345)
        auto_reply._remember_turn(key, "old question", "old answer")
        ask_api = AsyncMock(return_value="unused")
        finish = AsyncMock()

        with (
            patch.object(auto_reply, "_should_listen_to_user", return_value=True),
            patch.object(auto_reply, "_is_explicitly_at_bot", return_value=True),
            patch.object(auto_reply, "_ask_api", ask_api),
            patch.object(auto_reply.auto_reply, "finish", finish),
        ):
            await auto_reply.handle_group_message(self._event("清空"))

        ask_api.assert_not_awaited()
        finish.assert_awaited_once()
        self.assertNotIn(key, auto_reply._conversation_histories)

    async def test_rating_uses_special_prompt_then_clears_history(self) -> None:
        key = (100, 12345)
        auto_reply._remember_turn(key, "old question", "old answer")
        ask_api = AsyncMock(return_value="rating result")
        finish = AsyncMock()

        with (
            patch.object(auto_reply, "_should_listen_to_user", return_value=True),
            patch.object(auto_reply, "_is_explicitly_at_bot", return_value=True),
            patch.object(auto_reply, "RATING_SYSTEM_PROMPT", "rating instructions"),
            patch.object(auto_reply, "_ask_api", ask_api),
            patch.object(auto_reply.auto_reply, "finish", finish),
        ):
            await auto_reply.handle_group_message(self._event("评分"))

        ask_api.assert_awaited_once()
        args, kwargs = ask_api.await_args
        self.assertEqual(args[0], "评分")
        self.assertEqual(
            args[1],
            [
                {"role": "user", "content": "old question"},
                {"role": "assistant", "content": "old answer"},
            ],
        )
        self.assertEqual(kwargs["instructions"], "rating instructions")
        finish.assert_awaited_once()
        self.assertNotIn(key, auto_reply._conversation_histories)

    async def test_rating_api_failure_still_clears_history(self) -> None:
        key = (100, 12345)
        auto_reply._remember_turn(key, "old question", "old answer")
        ask_api = AsyncMock(side_effect=RuntimeError("upstream failed"))
        finish = AsyncMock()

        with (
            patch.object(auto_reply, "_should_listen_to_user", return_value=True),
            patch.object(auto_reply, "_is_explicitly_at_bot", return_value=True),
            patch.object(auto_reply, "RATING_SYSTEM_PROMPT", "rating instructions"),
            patch.object(auto_reply, "_ask_api", ask_api),
            patch.object(auto_reply.auto_reply, "finish", finish),
        ):
            await auto_reply.handle_group_message(self._event("评分"))

        ask_api.assert_awaited_once()
        finish.assert_awaited_once()
        self.assertNotIn(key, auto_reply._conversation_histories)


if __name__ == "__main__":
    unittest.main()
