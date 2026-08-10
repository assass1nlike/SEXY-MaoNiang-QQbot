from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
