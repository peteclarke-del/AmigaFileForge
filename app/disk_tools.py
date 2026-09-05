from __future__ import annotations

import json
import re
import subprocess
import sys

from .errors import DiskError


def friendly_engine_error(message: str) -> str:
    if "outside this volume" in message and "Block" in message:
        return (
            "The hard-drive image geometry is incomplete or invalid. "
            "For an RDB-less hardfile, reopen the original HDA with its matching GEO file."
        )
    if "Traceback (most recent call last)" in message:
        lines = [line.strip() for line in message.splitlines() if line.strip()]
        message = lines[-1] if lines else ""
        message = re.sub(
            r"^(?:[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):\s*",
            "",
            message,
        )
    return re.sub(r"^Error:\s*", "", message).strip() or "Disk operation failed."


def run_disc(args: list[str], binary: bool = False) -> bytes | str:
    arguments = [argument for argument in args if argument != ""]
    try:
        result = subprocess.run(
            [sys.executable, "-m", "amiganut", *arguments],
            capture_output=True,
            check=False,
            timeout=240,
        )
    except FileNotFoundError as exc:
        raise DiskError("The Amiganut disk engine is not installed.") from exc
    except subprocess.TimeoutExpired as exc:
        raise DiskError("The disk operation timed out.") from exc
    if result.returncode:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise DiskError(friendly_engine_error(message))
    return result.stdout if binary else result.stdout.decode("utf-8", "replace")


def decode_disc_json(output: str) -> dict:
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise DiskError("The disk engine returned an unreadable response.") from exc


def run_hxcfe(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["hxcfe", *args], capture_output=True, check=False, timeout=240
        )
    except FileNotFoundError as exc:
        raise DiskError("The HFE conversion engine is not installed.") from exc
    except subprocess.TimeoutExpired as exc:
        raise DiskError("The HFE conversion timed out.") from exc
    output = (result.stdout + result.stderr).decode("utf-8", "replace")
    if result.returncode:
        raise DiskError(friendly_engine_error(output))
    return output
