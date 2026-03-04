"""
Telegram bot configuration management for CCB.

Configuration is stored in ~/.ccb/telegram/config.json
Bot token is stored in the config file with restricted permissions.

Version 1: Long-polling bot with explicit prefix commands
- bot_token: Telegram Bot API token from @BotFather
- allowed_user_ids: Whitelist of authorized Telegram user IDs
- Provider prefix in message: "claude: message" -> ask claude message
- Replies via ccb-completion-hook with CCB_CALLER=telegram
"""

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Any, List, Optional

# Default configuration directory
DEFAULT_CONFIG_DIR = Path.home() / ".ccb" / "telegram"
CONFIG_FILE = "config.json"
PENDING_DIR = "pending"

# Current config version
CURRENT_CONFIG_VERSION = 1

# Supported AI providers
SUPPORTED_PROVIDERS = ["claude", "codex", "gemini", "opencode", "droid"]


@dataclass
class HeartbeatConfig:
    """Heartbeat status message configuration."""
    interval_seconds: int = 30
    typing_interval_seconds: int = 4
    indicators: str = "\u25d0\u25d3\u25d1\u25d2"  # cycling chars

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HeartbeatConfig":
        return cls(
            interval_seconds=data.get("interval_seconds", 30),
            typing_interval_seconds=data.get("typing_interval_seconds", 4),
            indicators=data.get("indicators", "\u25d0\u25d3\u25d1\u25d2"),
        )


@dataclass
class FormattingConfig:
    """Message formatting configuration."""
    max_message_length: int = 4096
    send_code_as_file: bool = True
    code_file_threshold_lines: int = 30
    use_spoiler_for_errors: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FormattingConfig":
        return cls(
            max_message_length=data.get("max_message_length", 4096),
            send_code_as_file=data.get("send_code_as_file", True),
            code_file_threshold_lines=data.get("code_file_threshold_lines", 30),
            use_spoiler_for_errors=data.get("use_spoiler_for_errors", True),
        )


@dataclass
class TelegramConfig:
    """Main Telegram bot configuration."""
    version: int = CURRENT_CONFIG_VERSION
    enabled: bool = False
    bot_token: str = ""
    allowed_user_ids: List[int] = field(default_factory=list)
    default_provider: str = "claude"
    case_insensitive: bool = True
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    formatting: FormattingConfig = field(default_factory=FormattingConfig)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "enabled": self.enabled,
            "bot_token": self.bot_token,
            "allowed_user_ids": self.allowed_user_ids,
            "default_provider": self.default_provider,
            "case_insensitive": self.case_insensitive,
            "heartbeat": self.heartbeat.to_dict(),
            "formatting": self.formatting.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TelegramConfig":
        return cls(
            version=data.get("version", CURRENT_CONFIG_VERSION),
            enabled=data.get("enabled", False),
            bot_token=data.get("bot_token", ""),
            allowed_user_ids=data.get("allowed_user_ids", []),
            default_provider=data.get("default_provider", "claude"),
            case_insensitive=data.get("case_insensitive", True),
            heartbeat=HeartbeatConfig.from_dict(data.get("heartbeat", {})),
            formatting=FormattingConfig.from_dict(data.get("formatting", {})),
        )

    def is_user_allowed(self, user_id: int) -> bool:
        """Check if a Telegram user ID is in the whitelist."""
        if not self.allowed_user_ids:
            return False
        return user_id in self.allowed_user_ids


def get_config_dir() -> Path:
    """Get the Telegram configuration directory."""
    config_dir = Path(os.environ.get("CCB_TELEGRAM_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    return config_dir


def ensure_config_dir() -> Path:
    """Ensure the configuration directory exists with proper permissions."""
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        config_dir.chmod(0o700)
    return config_dir


def get_config_path() -> Path:
    """Get the path to the configuration file."""
    return get_config_dir() / CONFIG_FILE


def get_pending_dir() -> Path:
    """Get the path to the pending requests directory."""
    pending = get_config_dir() / PENDING_DIR
    pending.mkdir(parents=True, exist_ok=True)
    return pending


def load_config() -> TelegramConfig:
    """Load Telegram configuration from file."""
    config_path = get_config_path()
    if not config_path.exists():
        return TelegramConfig()
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return TelegramConfig.from_dict(data)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Failed to load Telegram config: {e}")
        return TelegramConfig()


def save_config(config: TelegramConfig) -> None:
    """Save Telegram configuration to file."""
    ensure_config_dir()
    config_path = get_config_path()
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)
    if sys.platform != "win32":
        config_path.chmod(0o600)


def validate_config(config: TelegramConfig) -> List[str]:
    """Validate configuration and return list of errors."""
    errors = []

    if config.enabled:
        if not config.bot_token:
            errors.append("Bot token is required")
        elif not config.bot_token.count(":") == 1:
            errors.append("Bot token format invalid (expected 'ID:TOKEN')")
        if not config.allowed_user_ids:
            errors.append("At least one allowed user ID is required")
        for uid in config.allowed_user_ids:
            if not isinstance(uid, int) or uid <= 0:
                errors.append(f"Invalid user ID: {uid} (must be positive integer)")

    return errors


def is_configured() -> bool:
    """Check if Telegram bot is properly configured."""
    config = load_config()
    return bool(config.bot_token and config.allowed_user_ids)
