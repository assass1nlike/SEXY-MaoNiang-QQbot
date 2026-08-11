from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import nonebot


nonebot.init()

from plugins import status_notify  # noqa: E402


class StatusNotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        status_notify._connected_bots.clear()
        status_notify._online_announced.clear()
        status_notify._offline_attempted = False
        status_notify._shutdown_task = None
        status_notify._previous_signal_handlers.clear()
        status_notify._signal_handlers_installed = False

    @staticmethod
    def _bot(self_id: str = "3440049649") -> SimpleNamespace:
        return SimpleNamespace(self_id=self_id, send_group_msg=AsyncMock())

    async def test_status_message_is_sent_to_every_configured_group(self) -> None:
        bot = self._bot()
        with patch.object(status_notify, "STATUS_GROUP_IDS", {200, 100}):
            sent = await status_notify._send_status_message(bot, "上线", "online")

        self.assertEqual(sent, 2)
        self.assertEqual(
            bot.send_group_msg.await_args_list,
            [
                unittest.mock.call(group_id=100, message="上线"),
                unittest.mock.call(group_id=200, message="上线"),
            ],
        )

    async def test_empty_configuration_sends_nothing(self) -> None:
        bot = self._bot()
        with patch.object(status_notify, "STATUS_GROUP_IDS", set()):
            sent = await status_notify._send_status_message(bot, "上线", "online")

        self.assertEqual(sent, 0)
        bot.send_group_msg.assert_not_awaited()

    async def test_online_notification_is_only_sent_once_per_process(self) -> None:
        bot = self._bot()
        with (
            patch.object(status_notify, "STATUS_GROUP_IDS", {100}),
            patch.object(status_notify, "ONLINE_MESSAGE", "机器人上线"),
        ):
            await status_notify._handle_bot_connect(bot)
            await status_notify._handle_bot_connect(bot)

        bot.send_group_msg.assert_awaited_once_with(
            group_id=100,
            message="机器人上线",
        )

    async def test_shutdown_sends_offline_notification_only_once(self) -> None:
        bot = self._bot()
        status_notify._connected_bots[str(bot.self_id)] = bot
        with (
            patch.object(status_notify, "STATUS_GROUP_IDS", {100}),
            patch.object(status_notify, "OFFLINE_MESSAGE", "机器人下线"),
        ):
            await status_notify._send_offline_once()
            await status_notify._send_offline_once()

        bot.send_group_msg.assert_awaited_once_with(
            group_id=100,
            message="机器人下线",
        )

    async def test_startup_wraps_existing_signal_handlers(self) -> None:
        previous_handler = Mock()
        with (
            patch.object(
                status_notify.signal,
                "getsignal",
                return_value=previous_handler,
            ),
            patch.object(status_notify.signal, "signal") as install,
        ):
            await status_notify._install_signal_handlers()

        self.assertTrue(status_notify._signal_handlers_installed)
        self.assertGreaterEqual(install.call_count, 2)
        self.assertTrue(status_notify._previous_signal_handlers)


if __name__ == "__main__":
    unittest.main()
