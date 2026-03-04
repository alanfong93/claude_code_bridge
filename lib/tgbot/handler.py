"""
Telegram message handler for CCB.

Parses incoming Telegram messages, validates users, and routes commands
to the ASK system or built-in bot commands (/help, /pulse).

Message format: "provider: message"
Examples:
  claude: fix the bug in auth.py
  gemini: explain this architecture
  /help
  /pulse
"""

import re
import subprocess
import os
import shutil
from dataclasses import dataclass
from typing import Optional, List

from .config import TelegramConfig, SUPPORTED_PROVIDERS


@dataclass
class ParsedMessage:
    """Result of parsing a Telegram message."""
    provider: Optional[str]  # Extracted provider name (lowercase) or None
    message: str             # Message body with prefix removed
    is_command: bool = False  # True if this is a /command
    command: Optional[str] = None  # Command name without slash


@dataclass
class HandleResult:
    """Result of handling a Telegram message."""
    success: bool
    reply: str
    route_to_ask: bool = False  # True if this should be sent to ASK system
    provider: Optional[str] = None
    message: Optional[str] = None


# Regex: captures provider name, then strips colon and whitespace
PREFIX_PATTERN = re.compile(r"^(\w+)\s*:\s*", re.IGNORECASE)

# Telegram bot commands
COMMAND_PATTERN = re.compile(r"^/(\w+)(?:\s+(.*))?$", re.DOTALL)


def parse_message(text: str, config: TelegramConfig) -> ParsedMessage:
    """Parse a Telegram message into provider + message.

    Supports:
    - "claude: fix the bug" -> provider=claude, message="fix the bug"
    - "/help" -> is_command=True, command="help"
    - "/pulse" -> is_command=True, command="pulse"
    - bare text -> provider=None, message=text
    """
    text = text.strip()
    if not text:
        return ParsedMessage(provider=None, message="")

    # Check for /commands first
    cmd_match = COMMAND_PATTERN.match(text)
    if cmd_match:
        command = cmd_match.group(1).lower()
        args = (cmd_match.group(2) or "").strip()
        return ParsedMessage(
            provider=None,
            message=args,
            is_command=True,
            command=command,
        )

    # Parse provider prefix
    prefix_match = PREFIX_PATTERN.match(text)
    if prefix_match:
        provider_raw = prefix_match.group(1)
        provider = provider_raw.lower() if config.case_insensitive else provider_raw
        message = text[prefix_match.end():].strip()

        if provider in SUPPORTED_PROVIDERS:
            return ParsedMessage(provider=provider, message=message)
        # Unknown provider — treat as bare text
        return ParsedMessage(provider=None, message=text)

    # No prefix — bare text
    return ParsedMessage(provider=None, message=text)


def handle_message(
    text: str,
    user_id: int,
    config: TelegramConfig,
) -> HandleResult:
    """Handle an incoming Telegram message.

    Returns a HandleResult indicating what action to take.
    """
    # Check whitelist
    if not config.is_user_allowed(user_id):
        # Silently ignore unauthorized users (no reply)
        return HandleResult(success=False, reply="")

    parsed = parse_message(text, config)

    # Handle empty messages
    if not parsed.message and not parsed.is_command:
        return HandleResult(success=False, reply="Empty message. Send 'provider: message' (e.g. 'claude: hello').")

    # Handle commands
    if parsed.is_command:
        return _handle_command(parsed, config)

    # Handle provider-prefixed messages
    if parsed.provider:
        if not parsed.message:
            return HandleResult(
                success=False,
                reply=f"Empty message for {parsed.provider}. Usage: {parsed.provider}: your message here",
            )
        return HandleResult(
            success=True,
            reply="",
            route_to_ask=True,
            provider=parsed.provider,
            message=parsed.message,
        )

    # No provider prefix — show usage hint
    return HandleResult(
        success=False,
        reply=(
            "Please specify a provider prefix.\n"
            f"Format: provider: message\n"
            f"Providers: {', '.join(SUPPORTED_PROVIDERS)}\n"
            f"Example: claude: explain this code"
        ),
    )


def _handle_command(parsed: ParsedMessage, config: TelegramConfig) -> HandleResult:
    """Handle a /command."""
    if parsed.command == "help":
        return _cmd_help(config)
    elif parsed.command == "pulse":
        return _cmd_pulse()
    elif parsed.command == "start":
        # Telegram sends /start when user first opens bot
        return _cmd_help(config)
    else:
        return HandleResult(
            success=False,
            reply=f"Unknown command: /{parsed.command}\nAvailable: /help, /pulse",
        )


def _cmd_help(config: TelegramConfig) -> HandleResult:
    """Handle /help command."""
    providers_list = ", ".join(SUPPORTED_PROVIDERS)
    reply = (
        "CCB Telegram Bot\n"
        "─────────────────\n"
        "Send commands to AI providers:\n"
        f"  provider: message\n"
        "\n"
        f"Providers: {providers_list}\n"
        "\n"
        "Examples:\n"
        "  claude: fix the login bug\n"
        "  gemini: review this PR\n"
        "  codex: analyze performance\n"
        "\n"
        "Commands:\n"
        "  /help   — Show this help\n"
        "  /pulse  — Check provider health\n"
    )
    return HandleResult(success=True, reply=reply)


def _cmd_pulse() -> HandleResult:
    """Handle /pulse command — check provider health."""
    lines = ["CCB Provider Status\n─────────────────"]

    # Find ccb-ping command
    ping_cmd = _find_command("ccb-ping")

    for provider in SUPPORTED_PROVIDERS:
        if ping_cmd:
            try:
                result = subprocess.run(
                    [ping_cmd, provider],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    lines.append(f"  {provider}: online")
                else:
                    lines.append(f"  {provider}: offline")
            except (subprocess.TimeoutExpired, OSError):
                lines.append(f"  {provider}: timeout")
        else:
            lines.append(f"  {provider}: unknown (ccb-ping not found)")

    return HandleResult(success=True, reply="\n".join(lines))


def _find_command(name: str) -> Optional[str]:
    """Find a CCB command in PATH or known locations."""
    # Check PATH first
    found = shutil.which(name)
    if found:
        return found

    # Check common CCB bin locations
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    bin_path = os.path.join(project_root, "bin", name)
    if os.path.isfile(bin_path):
        return bin_path

    return None
