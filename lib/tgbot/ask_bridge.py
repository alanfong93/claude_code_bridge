"""
ASK system bridge for CCB Telegram bot.

Routes parsed Telegram messages to the CCB ASK system and manages
pending request context files for the completion hook.

Flow:
1. Save request context to ~/.ccb/telegram/pending/<req_id>.json
2. Call `ask <provider>` subprocess with CCB_CALLER=telegram
3. Completion hook reads context and sends reply (handled in S5)
"""

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import get_pending_dir

def _log(msg: str) -> None:
    """Write diagnostic log to file (stdout may not be captured)."""
    try:
        log_path = Path.home() / ".ccb" / "telegram" / "telegramd.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{msg}\n")
    except Exception:
        pass
    print(msg)


@dataclass
class AskResult:
    """Result of submitting a request to the ASK system."""
    success: bool
    message: str
    request_id: Optional[str] = None


def generate_request_id() -> str:
    """Generate a unique request ID for tracking."""
    return f"tg-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"


def save_pending_context(
    request_id: str,
    chat_id: int,
    message_id: int,
    provider: str,
    user_id: int,
    username: str = "",
) -> bool:
    """Save request context for the completion hook to use later.

    The completion hook reads this file to know where to send the reply.

    Args:
        request_id: Unique request ID
        chat_id: Telegram chat ID for sending reply
        message_id: Original message ID for reply threading
        provider: AI provider name
        user_id: Telegram user ID
        username: Telegram username (optional)

    Returns:
        True if saved successfully
    """
    try:
        pending_dir = get_pending_dir()
        context_file = pending_dir / f"{request_id}.json"

        context = {
            "request_id": request_id,
            "chat_id": chat_id,
            "message_id": message_id,
            "provider": provider,
            "user_id": user_id,
            "username": username,
            "timestamp": time.time(),
        }

        with open(context_file, "w", encoding="utf-8") as f:
            json.dump(context, f, indent=2)
        if sys.platform != "win32":
            context_file.chmod(0o600)
        return True
    except Exception as e:
        _log(f"[telegramd] Failed to save pending context: {e}")
        return False


def load_pending_context(request_id: str) -> Optional[dict]:
    """Load saved pending context by request ID.

    Used by the completion hook to retrieve chat_id, message_id, etc.
    """
    try:
        context_file = get_pending_dir() / f"{request_id}.json"
        if not context_file.exists():
            return None
        with open(context_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        _log(f"[telegramd] Failed to load pending context: {e}")
        return None


def cleanup_pending_context(request_id: str) -> bool:
    """Delete pending context file after reply is sent."""
    try:
        context_file = get_pending_dir() / f"{request_id}.json"
        if context_file.exists():
            context_file.unlink()
        return True
    except OSError as e:
        _log(f"[telegramd] Failed to cleanup pending context: {e}")
        return False


def find_ask_command() -> tuple[str, ...]:
    """Find the `ask` command, returning a tuple of command args.

    On Windows, .BAT wrappers don't work well with subprocess capture,
    so we resolve to the underlying Python script and call it directly.
    """
    import shutil

    # Look for the Python script directly (bypass .BAT wrapper on Windows)
    script_dir = Path(__file__).resolve().parent.parent.parent / "bin"
    ask_script_paths = [
        script_dir / "ask",
        Path.home() / "AppData" / "Local" / "codex-dual" / "bin" / "ask",
        Path.home() / ".local" / "bin" / "ask",
    ]
    for p in ask_script_paths:
        if p.exists() and p.is_file():
            return (sys.executable, str(p))

    # Fallback: use shutil.which (may find .BAT on Windows)
    found = shutil.which("ask")
    if found:
        return (found,)
    return ()


def submit_to_ask(
    provider: str,
    message: str,
    request_id: str,
    chat_id: int,
    message_id: int,
    work_dir: Optional[str] = None,
) -> AskResult:
    """Submit a message to the ASK system.

    Calls `ask <provider>` with CCB_CALLER=telegram and associated
    environment variables. The actual AI processing happens asynchronously;
    the completion hook handles sending the reply back to Telegram.

    Args:
        provider: AI provider name (claude, gemini, etc.)
        message: The user's message to send
        request_id: Unique request ID for tracking
        chat_id: Telegram chat ID (passed via env)
        message_id: Telegram message ID (passed via env)
        work_dir: Working directory for the ask command

    Returns:
        AskResult indicating submission success/failure
    """
    _log(f"[telegramd] submit_to_ask provider={provider} req={request_id}")
    ask_cmd = find_ask_command()
    _log(f"[telegramd] ask_cmd={ask_cmd}")
    if not ask_cmd:
        return AskResult(
            success=False,
            message="ask command not found. Is CCB installed?",
        )
    # ask_cmd is a tuple: (python, script) or (bat_path,)

    if not work_dir:
        work_dir = os.getcwd()

    # Prepare environment
    env = os.environ.copy()
    env["CCB_CALLER"] = "telegram"
    env["CCB_TELEGRAM_REQ_ID"] = request_id
    env["CCB_TELEGRAM_CHAT_ID"] = str(chat_id)
    env["CCB_TELEGRAM_MSG_ID"] = str(message_id)
    env["CCB_WORK_DIR"] = work_dir
    if "CCB_RUN_DIR" not in env and work_dir:
        env["CCB_RUN_DIR"] = work_dir

    # Ensure WezTerm socket is discoverable (needed for pane liveness checks).
    # When daemon starts from PowerShell or a non-WezTerm terminal, these
    # env vars won't exist. Auto-detect from the known socket directory.
    if sys.platform == "win32":
        # On Windows, WezTerm uses named pipes. The wezterm CLI discovers its
        # server automatically on Windows, but we ensure the CLI is findable.
        # No WEZTERM_UNIX_SOCKET needed — wezterm cli uses Windows IPC natively.
        import shutil
        if not shutil.which("wezterm"):
            # Try common install locations so the CLI is available to subprocesses
            for wez_path in [
                Path(os.environ.get("PROGRAMFILES", "")) / "WezTerm",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "WezTerm",
                Path.home() / "scoop" / "apps" / "wezterm" / "current",
            ]:
                if (wez_path / "wezterm.exe").exists():
                    env["PATH"] = str(wez_path) + os.pathsep + env.get("PATH", "")
                    _log(f"[telegramd] Added WezTerm to PATH: {wez_path}")
                    break
    elif "WEZTERM_UNIX_SOCKET" not in env:
        import glob
        sock_dir = Path.home() / ".local" / "share" / "wezterm"
        socks = sorted(glob.glob(str(sock_dir / "gui-sock-*")))
        if socks:
            env["WEZTERM_UNIX_SOCKET"] = socks[-1]  # most recent
            _log(f"[telegramd] Auto-detected WEZTERM_UNIX_SOCKET: {socks[-1]}")

    try:
        # Fire-and-forget: ask blocks until AI responds (can take minutes).
        # We don't wait — the completion hook sends the reply to Telegram.
        # Use a temp file for stderr to avoid pipe buffer deadlocks on
        # long-running processes (pipes fill up if nobody drains them).
        import tempfile
        stderr_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
        try:
            proc = subprocess.Popen(
                [*ask_cmd, provider, "-t", "3600", message],
                cwd=work_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr_file,
                text=True,
            )
        except Exception:
            stderr_file.close()
            raise

        # Brief wait to catch immediate failures (bad config, pane not alive)
        try:
            proc.wait(timeout=5)
            # If wait returns within 5s, ask exited (likely an error)
            stderr_file.seek(0)
            errs = stderr_file.read()
            stderr_file.close()
            if proc.returncode != 0:
                err = (errs or "").strip()
                if not err:
                    err = f"ask exited with code {proc.returncode}"
                _log(f"[telegramd] ask failed immediately (code={proc.returncode}): {err}")
                return AskResult(
                    success=False,
                    message=err,
                    request_id=request_id,
                )
            # Exited with 0 quickly — unusual but fine
            _log(f"[telegramd] ask returned immediately for {provider} (req={request_id})")
            return AskResult(
                success=True,
                message=f"Submitted to {provider}",
                request_id=request_id,
            )
        except subprocess.TimeoutExpired:
            # This is the SUCCESS case — ask is still running (processing AI request).
            # Close our handle to stderr — the subprocess keeps its own fd open.
            stderr_file.close()
            _log(f"[telegramd] Submitted to {provider} (req={request_id}, pid={proc.pid})")
            return AskResult(
                success=True,
                message=f"Submitted to {provider}",
                request_id=request_id,
            )
    except Exception as e:
        return AskResult(
            success=False,
            message=f"Failed to call ask: {e}",
            request_id=request_id,
        )
