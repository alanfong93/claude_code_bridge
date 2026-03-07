"""
Message formatter for CCB Telegram bot.

Handles:
- Splitting long messages at Telegram's 4096 char limit
- Sending code blocks as file attachments when they exceed threshold
- Converting LLM markdown to Telegram-compatible format
- Wrapping long error logs in Telegram spoiler tags
"""

import re
from typing import List, Tuple

from .config import TelegramConfig

# Telegram message limit
MAX_MESSAGE_LENGTH = 4096


def split_message(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> List[str]:
    """Split a long message into chunks that fit Telegram's limit.

    Tries to split at natural boundaries (newlines, then spaces).
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Try to split at a newline
        split_pos = remaining.rfind("\n", 0, max_length)
        if split_pos <= 0:
            # Try to split at a space
            split_pos = remaining.rfind(" ", 0, max_length)
        if split_pos <= 0:
            # Hard split at max_length
            split_pos = max_length

        chunks.append(remaining[:split_pos])
        remaining = remaining[split_pos:].lstrip("\n")

    return chunks


def extract_code_blocks(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Extract fenced code blocks from text.

    Returns:
        (text_without_code, [(language, code_content), ...])
    """
    pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    blocks = []
    clean_text = text

    for match in pattern.finditer(text):
        lang = match.group(1) or "txt"
        code = match.group(2).strip()
        blocks.append((lang, code))

    if blocks:
        clean_text = pattern.sub("[code block — see attachment]", text)

    return clean_text, blocks


def should_send_as_file(text: str, config: TelegramConfig) -> bool:
    """Check if the response should be sent as a file attachment.

    Returns True if:
    - Text exceeds max message length AND contains code blocks with many lines
    - Code blocks exceed the configured line threshold
    """
    if not config.formatting.send_code_as_file:
        return False

    _, blocks = extract_code_blocks(text)
    for _, code in blocks:
        if code.count("\n") >= config.formatting.code_file_threshold_lines:
            return True

    return False


def wrap_spoiler(text: str) -> str:
    """Wrap text in Telegram spoiler tags for collapsible content."""
    return f"||{text}||"


def format_error_reply(error_text: str, config: TelegramConfig) -> str:
    """Format an error reply, using spoiler tags for long errors."""
    if not config.formatting.use_spoiler_for_errors:
        return error_text

    lines = error_text.split("\n")
    if len(lines) > 10:
        summary = "\n".join(lines[:5])
        detail = "\n".join(lines[5:])
        return f"{summary}\n\n(tap to reveal full error)\n{wrap_spoiler(detail)}"

    return error_text


_ERROR_KEYWORDS = (
    "pane died during request",
    "pane died",
    "interrupted",
    "Failed to",
    "Error:",
    "timed out",
    "timeout",
)


def format_reply(
    provider: str,
    reply_text: str,
    config: TelegramConfig,
    is_error: bool = False,
) -> List[str]:
    """Format a provider reply for Telegram delivery.

    Returns a list of message strings to send (may be multiple if splitting needed).

    Args:
        is_error: Explicitly mark as error. Also auto-detected from reply content.
    """
    if not reply_text:
        return [f"[{provider.capitalize()}] (empty response)"]

    # Auto-detect errors from reply content
    if not is_error and any(kw in reply_text for kw in _ERROR_KEYWORDS):
        is_error = True

    # Prefix with provider name and error indicator
    if is_error:
        formatted = f"\u26a0\ufe0f [{provider.capitalize()}] Error\n{reply_text}"
    else:
        formatted = f"[{provider.capitalize()}]\n{reply_text}"

    # Split if needed
    return split_message(formatted)
