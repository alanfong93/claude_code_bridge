"""
CCB Telegram System - Telegram bot-based AI provider interaction.

This module provides Telegram integration for CCB, allowing users to interact
with AI providers (Claude, Codex, Gemini, etc.) via a Telegram bot.

Version 1: Long-polling bot with explicit prefix commands
- Routes messages to ASK system via provider prefix: "claude: message"
- Replies via ccb-completion-hook with CCB_CALLER=telegram
- Self-editing heartbeat status messages while AI works
- User whitelisting for security

Key components:
- config: Configuration management
- handler: Message parsing, validation, and routing
"""

__version__ = "1.0.0"

from .handler import (
    ParsedMessage,
    HandleResult,
    parse_message,
    handle_message,
)

from .config import (
    TelegramConfig,
    HeartbeatConfig,
    FormattingConfig,
    CURRENT_CONFIG_VERSION,
    SUPPORTED_PROVIDERS,
    load_config,
    save_config,
    validate_config,
    is_configured,
    get_config_dir,
    ensure_config_dir,
    get_config_path,
    get_pending_dir,
)

__all__ = [
    # Handler
    "ParsedMessage",
    "HandleResult",
    "parse_message",
    "handle_message",
    # Config classes
    "TelegramConfig",
    "HeartbeatConfig",
    "FormattingConfig",
    # Constants
    "CURRENT_CONFIG_VERSION",
    "SUPPORTED_PROVIDERS",
    # Config functions
    "load_config",
    "save_config",
    "validate_config",
    "is_configured",
    "get_config_dir",
    "ensure_config_dir",
    "get_config_path",
    "get_pending_dir",
]
