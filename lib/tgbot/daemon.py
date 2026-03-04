"""
Telegram bot daemon (telegramd) for CCB.

Version 1: Long-polling bot with explicit prefix commands
- Routes messages to ASK system via handler
- Heartbeat status messages while AI works
- Provider health check via /pulse
- Replies via ccb-completion-hook with CCB_CALLER=telegram
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, CommandHandler, filters

from .config import TelegramConfig, load_config, get_config_dir, ensure_config_dir
from .handler import handle_message as process_message, HandleResult
from .heartbeat import Heartbeat
from .ask_bridge import (
    generate_request_id,
    save_pending_context,
    submit_to_ask,
)

# State file names
STATE_FILE = "telegramd.json"
PID_FILE = "telegramd.pid"
LOG_FILE = "telegramd.log"


@dataclass
class DaemonState:
    """Daemon state for discovery."""
    pid: int
    started_at: float
    status: str = "running"
    version: int = 1
    bot_username: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DaemonState":
        return cls(
            pid=data.get("pid", 0),
            started_at=data.get("started_at", 0),
            status=data.get("status", "unknown"),
            version=data.get("version", 1),
            bot_username=data.get("bot_username", ""),
        )


def get_state_path() -> Path:
    return get_config_dir() / STATE_FILE

def get_pid_path() -> Path:
    return get_config_dir() / PID_FILE

def get_log_path() -> Path:
    return get_config_dir() / LOG_FILE

def read_daemon_state() -> Optional[DaemonState]:
    state_path = get_state_path()
    if not state_path.exists():
        return None
    try:
        with open(state_path, "r") as f:
            data = json.load(f)
        return DaemonState.from_dict(data)
    except Exception:
        return None

def write_daemon_state(state: DaemonState) -> None:
    state_path = get_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(state_path, "w") as f:
        json.dump(state.to_dict(), f, indent=2)
    if sys.platform != "win32":
        state_path.chmod(0o600)

def remove_daemon_state() -> None:
    for name in (STATE_FILE, PID_FILE):
        p = get_config_dir() / name
        if p.exists():
            p.unlink()

def _read_pid_file() -> Optional[int]:
    pid_path = get_pid_path()
    if not pid_path.exists():
        return None
    try:
        pid = int(pid_path.read_text().strip())
        return pid if pid > 0 else None
    except Exception:
        return None

def _is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) == 0:
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, SystemError):
        return False

def _get_running_pid(state: Optional[DaemonState]) -> Optional[int]:
    candidates = []
    if state and state.pid:
        candidates.append(state.pid)
    pid_file = _read_pid_file()
    if pid_file and pid_file not in candidates:
        candidates.append(pid_file)
    for pid in candidates:
        if _is_process_alive(pid):
            return pid
    return None

def is_daemon_running() -> bool:
    state = read_daemon_state()
    running_pid = _get_running_pid(state)
    if running_pid:
        if state and state.pid != running_pid:
            state.pid = running_pid
            write_daemon_state(state)
        return True
    remove_daemon_state()
    return False

def get_daemon_status() -> dict:
    state = read_daemon_state()
    if not state:
        return {"running": False}
    running_pid = _get_running_pid(state)
    if running_pid:
        return {
            "running": True,
            "pid": running_pid,
            "started_at": state.started_at,
            "uptime": time.time() - state.started_at,
            "version": state.version,
            "bot_username": state.bot_username,
        }
    else:
        remove_daemon_state()
        return {"running": False}

def stop_daemon() -> bool:
    state = read_daemon_state()
    pid = state.pid if state else None
    if not pid:
        pid = _read_pid_file()
    if not pid:
        print("Telegram daemon is not running")
        return False
    if not _is_process_alive(pid):
        print("Telegram daemon is not running")
        remove_daemon_state()
        return False
    try:
        if os.name == "nt":
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to telegramd (PID: {pid})")
        for _ in range(20):
            if not _is_process_alive(pid):
                print("Telegram daemon stopped")
                remove_daemon_state()
                return True
            time.sleep(0.25)
        print("Warning: Daemon did not stop gracefully")
        return False
    except (OSError, ProcessLookupError, SystemError):
        print("Telegram daemon is not running")
        remove_daemon_state()
        return False


class TelegramDaemon:
    """Telegram bot daemon service."""

    def __init__(self, config: Optional[TelegramConfig] = None):
        self.config = config or load_config()
        self._app: Optional[Application] = None
        self._active_heartbeats: dict = {}  # chat_id -> Heartbeat

    async def _on_message(self, update: Update, context) -> None:
        """Handle incoming Telegram messages."""
        if not update.message or not update.message.text:
            return

        user_id = update.effective_user.id if update.effective_user else 0
        chat_id = update.effective_chat.id if update.effective_chat else 0
        message_id = update.message.message_id
        username = update.effective_user.username or "" if update.effective_user else ""
        text = update.message.text

        # Process through handler
        result = process_message(text, user_id, self.config)

        # Silent reject for unauthorized users
        if not result.success and not result.reply:
            return

        # Send error/help replies
        if result.reply and not result.route_to_ask:
            await update.message.reply_text(result.reply)
            return

        # Route to ASK system
        if result.route_to_ask and result.provider and result.message:
            await self._route_to_ask(
                update, result.provider, result.message,
                chat_id, message_id, user_id, username,
            )

    async def _route_to_ask(
        self, update: Update, provider: str, message: str,
        chat_id: int, message_id: int, user_id: int, username: str,
    ) -> None:
        """Route a message to the ASK system with heartbeat."""
        req_id = generate_request_id()

        # Save pending context
        save_pending_context(
            request_id=req_id,
            chat_id=chat_id,
            message_id=message_id,
            provider=provider,
            user_id=user_id,
            username=username,
        )

        # Start heartbeat
        bot = self._app.bot
        heartbeat = Heartbeat(bot, chat_id, provider, self.config)
        self._active_heartbeats[chat_id] = heartbeat
        await heartbeat.start()

        # Submit to ASK in a thread (it's a subprocess call)
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: submit_to_ask(
                    provider=provider,
                    message=message,
                    request_id=req_id,
                    chat_id=chat_id,
                    message_id=message_id,
                ),
            )

            if not result.success:
                await heartbeat.stop()
                self._active_heartbeats.pop(chat_id, None)
                await update.message.reply_text(
                    f"Failed to submit to {provider}: {result.message}"
                )
        except Exception as e:
            await heartbeat.stop()
            self._active_heartbeats.pop(chat_id, None)
            await update.message.reply_text(f"Error: {e}")

    def start(self) -> None:
        """Start the Telegram bot daemon (blocking)."""
        ensure_config_dir()

        # Write state
        state = DaemonState(
            pid=os.getpid(),
            started_at=time.time(),
            status="running",
        )
        write_daemon_state(state)

        # Write PID file
        pid_path = get_pid_path()
        pid_path.write_text(str(os.getpid()))

        # Set up signal handlers
        signal.signal(signal.SIGTERM, self._handle_signal)
        if hasattr(signal, "SIGINT"):
            signal.signal(signal.SIGINT, self._handle_signal)

        print(f"[telegramd] Starting Telegram bot daemon (PID: {os.getpid()})")

        # Build application
        self._app = Application.builder().token(self.config.bot_token).build()

        # Register message handler (catches all text messages)
        self._app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._on_message,
        ))
        # Register command handlers
        self._app.add_handler(CommandHandler("start", self._on_message))
        self._app.add_handler(CommandHandler("help", self._on_message))
        self._app.add_handler(CommandHandler("pulse", self._on_message))

        # Get bot info for state
        print("[telegramd] Connecting to Telegram Bot API...")

        # Run polling (blocking)
        self._app.run_polling(
            drop_pending_updates=False,  # Process buffered messages
            close_loop=False,
        )

    def _handle_signal(self, signum, frame) -> None:
        """Handle shutdown signal."""
        print(f"\n[telegramd] Received signal {signum}, shutting down...")
        remove_daemon_state()
        if self._app:
            self._app.stop_running()

    def stop(self) -> None:
        """Stop the daemon."""
        remove_daemon_state()
        if self._app:
            self._app.stop_running()


def start_daemon(foreground: bool = False) -> None:
    """Start the Telegram bot daemon."""
    config = load_config()
    if not config.enabled:
        print("Telegram bot is not enabled. Run 'telegramd setup' first.")
        sys.exit(1)

    daemon = TelegramDaemon(config)

    if foreground:
        daemon.start()
    else:
        if os.name == "nt":
            import shutil
            found = shutil.which("telegramd")
            if found:
                launcher = Path(found)
            else:
                launcher = Path(__file__).resolve().parents[2] / "bin" / "telegramd"
            work_dir = Path.cwd()
            log_path = get_log_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)

            creationflags = 0
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)

            # Explicitly pass environment so detached process inherits
            # CCB_RUN_DIR and other session-specific vars needed by ask/askd
            daemon_env = os.environ.copy()

            log_file = open(log_path, "a", buffering=1)
            proc = subprocess.Popen(
                [sys.executable, str(launcher), "start", "--foreground"],
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=log_file,
                cwd=str(work_dir),
                env=daemon_env,
                creationflags=creationflags,
            )

            print(f"[telegramd] Started in background (PID: {proc.pid})")
            return

        # POSIX daemonize
        pid = os.fork()
        if pid > 0:
            print(f"[telegramd] Started in background (PID: {pid})")
            sys.exit(0)

        os.setsid()
        os.umask(0)

        log_path = get_log_path()
        log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(log_fd, 1)
        os.dup2(log_fd, 2)
        os.close(log_fd)
        sys.stdout = os.fdopen(1, "w", buffering=1)
        sys.stderr = sys.stdout

        daemon.start()
