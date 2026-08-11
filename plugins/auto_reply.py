"""Reply to configured group members through a Responses-compatible API."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment


load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logger = logging.getLogger(__name__)


def _csv_ints(name: str) -> set[int]:
    """Parse a comma-separated list of QQ IDs, ignoring malformed values."""
    values: set[int] = set()
    for item in os.getenv(name, "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.add(int(item))
        except ValueError:
            logger.warning("Ignoring invalid integer in %s", name)
    return values


TARGET_USER_IDS = _csv_ints("TARGET_USER_IDS")
TARGET_GROUP_IDS = _csv_ints("TARGET_GROUP_IDS")
ALL_USERS_GROUP_IDS = _csv_ints("ALL_USERS_GROUP_IDS")
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.openai.com/v1").rstrip("/")
API_KEY = os.getenv("API_KEY", "")
API_MODEL = os.getenv("API_MODEL", "gpt-4o-mini")
API_REASONING_EFFORT = os.getenv("API_REASONING_EFFORT", "").strip()
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "你是一个友善、简洁的 QQ 群助手。请直接回答用户的问题。",
)
PUBLIC_SYSTEM_PROMPT = os.getenv("PUBLIC_SYSTEM_PROMPT", "").strip() or SYSTEM_PROMPT
RATING_SYSTEM_PROMPT = os.getenv("RATING_SYSTEM_PROMPT", "").strip()
PREFILL_USER = os.getenv("PREFILL_USER", "").strip()
PREFILL_ASSISTANT = os.getenv("PREFILL_ASSISTANT", "").strip()
PUBLIC_PREFILL_USER = os.getenv("PUBLIC_PREFILL_USER", "").strip()
PUBLIC_PREFILL_ASSISTANT = os.getenv("PUBLIC_PREFILL_ASSISTANT", "").strip()
API_TIMEOUT = float(os.getenv("API_TIMEOUT_SECONDS", "60"))
MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "2000"))
MAX_CONVERSATION_TURNS = max(
    1,
    int(os.getenv("MAX_CONVERSATION_TURNS", "20")),
)
MAX_PUBLIC_CONVERSATION_MESSAGES = 500

MessageItem = dict[str, str]
ConversationKey = tuple[int, int]
_conversation_histories: dict[ConversationKey, deque[MessageItem]] = {}
_conversation_locks: dict[ConversationKey, asyncio.Lock] = {}
_public_histories: dict[int, deque[MessageItem]] = {}
_public_locks: dict[int, asyncio.Lock] = {}

auto_reply = on_message(priority=20, block=False)


def _api_url() -> str:
    """Support either a base URL or a URL that already ends in the endpoint."""
    if API_BASE_URL.endswith("/responses"):
        return API_BASE_URL
    return f"{API_BASE_URL}/responses"


def _extract_output_text(data: Any) -> str:
    """Extract all output_text parts from a Responses API response."""
    if not isinstance(data, dict):
        raise RuntimeError("API response was not a JSON object")

    # Some compatible providers expose the SDK convenience field in raw JSON.
    direct_text = data.get("output_text")
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text.strip()

    output = data.get("output")
    if not isinstance(output, list):
        raise RuntimeError("API response did not contain output items")

    text_parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())

    if not text_parts:
        raise RuntimeError("API response did not contain output text")
    return "\n".join(text_parts)


def _build_input(
    prompt: str,
    history: Sequence[MessageItem] = (),
    prefill_user: str = PREFILL_USER,
    prefill_assistant: str = PREFILL_ASSISTANT,
) -> list[MessageItem]:
    """Build an input transcript from prefill, bounded history, and new input."""
    messages: list[MessageItem] = []
    if prefill_user and prefill_assistant:
        messages.extend(
            [
                {"role": "user", "content": prefill_user},
                {"role": "assistant", "content": prefill_assistant},
            ]
        )
    messages.extend(history)
    messages.append({"role": "user", "content": prompt})
    return messages


def _is_explicitly_at_bot(event: GroupMessageEvent) -> bool:
    """Return whether the group message contains an explicit @ for this bot."""
    bot_id = str(event.self_id)
    return any(
        segment.type == "at" and str(segment.data.get("qq")) == bot_id
        for segment in event.original_message
    )


def _should_listen_to_user(event: GroupMessageEvent) -> bool:
    """Apply per-user filters plus optional groups that listen to everyone."""
    if str(event.user_id) == str(event.self_id):
        return False
    if event.group_id in ALL_USERS_GROUP_IDS:
        return True
    if event.user_id not in TARGET_USER_IDS:
        return False
    return not TARGET_GROUP_IDS or event.group_id in TARGET_GROUP_IDS


def _parse_public_prompt(text: str) -> str | None:
    """Return text after an exact `公共` + newline marker, if present."""
    text = text.lstrip(" \t")
    marker = "公共"
    if not text.startswith(marker):
        return None

    remainder = text[len(marker) :]
    if remainder.startswith("\r\n"):
        return remainder[2:].strip()
    if remainder.startswith("\n"):
        return remainder[1:].strip()
    return None


def _format_public_prompt(event: GroupMessageEvent, prompt: str) -> str:
    """Prefix a public message with the sender's group display name."""
    card = str(event.sender.card or "").strip()
    nickname = str(event.sender.nickname or "").strip()
    display_name = card or nickname or "未知成员"
    return f"[{display_name}]\n{prompt}"


def _instructions_for(is_public: bool) -> str:
    """Select separate system instructions for public and private chats."""
    return PUBLIC_SYSTEM_PROMPT if is_public else SYSTEM_PROMPT


def _prefill_for(is_public: bool) -> tuple[str, str]:
    """Select separate fixed example turns for public and private chats."""
    if is_public:
        return PUBLIC_PREFILL_USER, PUBLIC_PREFILL_ASSISTANT
    return PREFILL_USER, PREFILL_ASSISTANT


def _private_command_for(prompt: str, is_public: bool) -> str | None:
    """Recognize exact private-only commands after the bot mention is removed."""
    if is_public:
        return None
    if prompt == "清空":
        return "clear"
    if prompt == "评分":
        return "rating"
    return None


def _history_for(key: ConversationKey) -> deque[MessageItem]:
    history = _conversation_histories.get(key)
    if history is None:
        history = deque(maxlen=MAX_CONVERSATION_TURNS * 2)
        _conversation_histories[key] = history
    return history


def _remember_turn(key: ConversationKey, prompt: str, answer: str) -> None:
    history = _history_for(key)
    history.extend(
        [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": answer},
        ]
    )


def _clear_private_history(key: ConversationKey) -> None:
    _conversation_histories.pop(key, None)


def _public_history_for(group_id: int) -> deque[MessageItem]:
    history = _public_histories.get(group_id)
    if history is None:
        history = deque(maxlen=MAX_PUBLIC_CONVERSATION_MESSAGES)
        _public_histories[group_id] = history
    return history


def _remember_public_turn(group_id: int, prompt: str, answer: str) -> None:
    history = _public_history_for(group_id)
    history.extend(
        [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": answer},
        ]
    )


async def _ask_api(
    prompt: str,
    history: Sequence[MessageItem] = (),
    instructions: str = SYSTEM_PROMPT,
    prefill_user: str = PREFILL_USER,
    prefill_assistant: str = PREFILL_ASSISTANT,
) -> str:
    if not API_KEY:
        raise RuntimeError("API_KEY is not configured")

    payload = {
        "model": API_MODEL,
        "instructions": instructions,
        "input": _build_input(
            prompt,
            history,
            prefill_user=prefill_user,
            prefill_assistant=prefill_assistant,
        ),
        "store": False,
    }
    if API_REASONING_EFFORT:
        payload["reasoning"] = {"effort": API_REASONING_EFFORT}

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        response = await client.post(_api_url(), headers=headers, json=payload)
        response.raise_for_status()
        data: Any = response.json()

    return _extract_output_text(data)


@auto_reply.handle()
async def handle_group_message(event: GroupMessageEvent) -> None:
    if not _should_listen_to_user(event):
        return
    if not _is_explicitly_at_bot(event):
        return

    raw_prompt = event.get_plaintext()
    public_prompt = _parse_public_prompt(raw_prompt)
    is_public = public_prompt is not None
    prompt = public_prompt if is_public else raw_prompt.strip()
    if not prompt:
        return
    command = _private_command_for(prompt, is_public)
    prompt = prompt[:MAX_MESSAGE_LENGTH]
    if is_public:
        prompt = _format_public_prompt(event, prompt)

    key: ConversationKey | None = None
    if is_public:
        lock = _public_locks.setdefault(event.group_id, asyncio.Lock())
    else:
        key = (event.group_id, event.user_id)
        lock = _conversation_locks.setdefault(key, asyncio.Lock())

    if command == "clear":
        assert key is not None
        async with lock:
            _clear_private_history(key)
        await auto_reply.finish(
            MessageSegment.reply(event.message_id) + "上下文已清空。"
        )
        return

    is_rating = command == "rating"
    async with lock:
        # Re-read after acquiring the lock because another message may have
        # completed while this one was waiting.
        if is_public:
            history = list(_public_history_for(event.group_id))
        else:
            assert key is not None
            history = list(_history_for(key))
        try:
            prefill_user, prefill_assistant = _prefill_for(is_public)
            instructions = (
                RATING_SYSTEM_PROMPT
                if is_rating
                else _instructions_for(is_public)
            )
            if is_rating and not instructions:
                raise RuntimeError("RATING_SYSTEM_PROMPT is not configured")
            answer = await _ask_api(
                prompt,
                history,
                instructions=instructions,
                prefill_user=prefill_user,
                prefill_assistant=prefill_assistant,
            )
        except httpx.HTTPStatusError as exc:
            logger.warning("Upstream API returned HTTP %s", exc.response.status_code)
            answer = "上游服务暂时不可用，请稍后再试。"
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.warning(
                "Could not obtain upstream reply (%s): %s",
                type(exc).__name__,
                exc,
            )
            answer = "我暂时无法处理这条消息，请检查机器人配置或稍后再试。"
        else:
            if is_rating:
                pass
            elif is_public:
                _remember_public_turn(event.group_id, prompt, answer)
            else:
                assert key is not None
                _remember_turn(key, prompt, answer)
        finally:
            if is_rating:
                assert key is not None
                _clear_private_history(key)

    await auto_reply.finish(MessageSegment.reply(event.message_id) + answer)
