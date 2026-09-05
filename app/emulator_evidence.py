from __future__ import annotations

import base64
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from .checksum import sha256_bytes


class EmulatorEvidenceError(RuntimeError):
    pass


def private_display_arguments(
    arguments: list[str], display: str, duration: int = 20,
) -> list[str]:
    """Replace xvfb-run with one explicitly owned display for evidence capture."""
    result = list(arguments)
    try:
        wrapper = result.index("xvfb-run")
    except ValueError as exc:
        raise EmulatorEvidenceError(
            "The configured emulator command does not use the managed headless display."
        ) from exc
    del result[wrapper:wrapper + (2 if result[wrapper + 1:wrapper + 2] == ["-a"] else 1)]
    try:
        environment = result.index("env") + 1
    except ValueError as exc:
        raise EmulatorEvidenceError("The emulator command has no managed environment.") from exc
    result.insert(environment, f"DISPLAY={display}")
    if result and result[0] == "timeout":
        for index, value in enumerate(result[1:environment], start=1):
            if value.isdigit():
                result[index] = str(duration)
                break
    return result


def _display_number() -> int:
    for number in range(120, 220):
        if not Path(f"/tmp/.X11-unix/X{number}").exists():
            return number
    raise EmulatorEvidenceError("No private X display is available for the emulator sandbox.")


def _capture(display: str, output: Path) -> None:
    completed = subprocess.run(
        ["import", "-display", display, "-window", "root", str(output)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if completed.returncode or not output.is_file():
        raise EmulatorEvidenceError(
            "The emulator screen could not be captured: "
            + (completed.stderr.strip() or "ImageMagick import produced no image.")
        )


def _changed_pixels(before: Path, after: Path) -> int | None:
    completed = subprocess.run(
        ["compare", "-colorspace", "Gray", "-metric", "AE", str(before), str(after), "null:"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    match = re.search(r"\d+", completed.stderr)
    return int(match.group()) if match else None


def _terminate(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def capture_emulator_evidence(
    arguments: list[str], cwd: str, *, input_key: str = "Down",
) -> dict:
    """Run an emulator against private media and retain two auditable frames."""
    number = _display_number()
    display = f":{number}"
    xvfb = emulator = None
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="aff-emulator-evidence-") as folder:
        before, after = Path(folder) / "before.png", Path(folder) / "after.png"
        try:
            xvfb = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", "1280x960x24", "-ac", "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
            )
            socket = Path(f"/tmp/.X11-unix/X{number}")
            for _ in range(40):
                if socket.exists():
                    break
                if xvfb.poll() is not None:
                    raise EmulatorEvidenceError(
                        "The private display exited before the emulator started."
                    )
                time.sleep(0.05)
            else:
                raise EmulatorEvidenceError("The private display did not become ready.")
            command = private_display_arguments(arguments, display)
            emulator = subprocess.Popen(
                command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            # FastFileSystem initialises the virtual SD card and scans the full HDF before
            # drawing its first menu. Give slower ARM hosts a bounded settling
            # period instead of capturing the boot prompt as menu evidence.
            time.sleep(8)
            if emulator.poll() is not None:
                stdout, stderr = emulator.communicate()
                raise EmulatorEvidenceError(
                    "The emulator exited before a menu screen could be captured: "
                    + (stderr or stdout or f"return code {emulator.returncode}")[-2000:]
                )
            _capture(display, before)
            key = subprocess.run(
                ["xdotool", "key", input_key],
                env={**os.environ, "DISPLAY": display},
                capture_output=True, text=True, timeout=5, check=False,
            )
            if key.returncode:
                raise EmulatorEvidenceError(
                    "The sandbox could not send its navigation key: "
                    + (key.stderr.strip() or "xdotool failed.")
                )
            time.sleep(3)
            _capture(display, after)
            frames = []
            for role, path in (("settled", before), ("after-input", after)):
                content = path.read_bytes()
                frames.append({
                    "role": role,
                    "sha256": sha256_bytes(content),
                    "dataUrl": "data:image/png;base64," + base64.b64encode(content).decode("ascii"),
                })
            changed = _changed_pixels(before, after)
            return {
                "schema": "amiga-file-forge/emulator-display-evidence/v1",
                "display": "1280x960x24",
                "bounded": True,
                "deterministicMedia": True,
                "input": input_key,
                "inputChangedDisplay": bool(changed),
                "changedPixels": changed,
                "elapsedSeconds": round(time.monotonic() - started, 2),
                "frames": frames,
            }
        finally:
            _terminate(emulator)
            _terminate(xvfb)
