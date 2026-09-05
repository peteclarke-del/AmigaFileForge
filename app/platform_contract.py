"""The explicit compatibility contract shared by web and desktop hosts."""

from __future__ import annotations

from dataclasses import dataclass


PLATFORM_CONTRACT_FORMAT = "amiga-file-forge-platform-contract"
PLATFORM_CONTRACT_VERSION = 7
PLATFORM_KINDS = frozenset({"web", "desktop"})

# A capability belongs here only when both hosts expose the same implementation
# through the shared application factory and static client. Host adapters are
# intentionally listed separately so a new exception requires a reviewed edit.
SHARED_CAPABILITIES = (
    "image-sessions",
    "filesystem-editing",
    "drag-and-drop",
    "file-editors",
    "image-analysis",
    "menus",
    "online-library",
    "checkpoints-and-undo",
    "workflow-recipes",
    "managed-emulators",
    "hardware-deployment",
)

HOST_CAPABILITIES = {
    "web": ("browser-upload-download", "browser-visible-emulator"),
    "desktop": (
        "local-path-open",
        "native-window",
        "native-file-chooser",
        "native-file-drop",
        "desktop-file-associations",
        "native-emulator-window",
        "physical-floppy-write",
        "physical-floppy-read",
        "floppy-controller",
    ),
}

# Endpoint differences are contract changes. Tests compare the complete route
# maps and permit only names declared here.
HOST_EXCLUSIVE_ENDPOINTS = {
    "web": frozenset(),
    "desktop": frozenset({
        "desktop.open_local_path",
        "desktop.get_client_state",
        "desktop.put_client_state",
        "desktop.physical_floppy_status",
        "desktop.write_physical_floppy",
        "desktop.read_physical_floppy",
        "desktop.floppy_drive_status",
        "desktop.read_floppy_drive",
        "desktop.write_floppy_drive",
    }),
}


@dataclass(frozen=True)
class PlatformRuntime:
    kind: str = "web"
    desktop_token: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in PLATFORM_KINDS:
            raise ValueError(f"Unsupported application host: {self.kind}")
        if self.kind == "desktop" and len(self.desktop_token or "") < 32:
            raise ValueError("The desktop host requires a private launch token.")
        if self.kind == "web" and self.desktop_token is not None:
            raise ValueError("The web host cannot accept a desktop launch token.")

    def public_contract(self) -> dict:
        return {
            "format": PLATFORM_CONTRACT_FORMAT,
            "version": PLATFORM_CONTRACT_VERSION,
            "host": self.kind,
            "sharedCapabilities": list(SHARED_CAPABILITIES),
            "hostCapabilities": list(HOST_CAPABILITIES[self.kind]),
        }


def runtime(kind: str = "web", desktop_token: str | None = None) -> PlatformRuntime:
    return PlatformRuntime(str(kind or "web").strip().lower(), desktop_token)


__all__ = [
    "HOST_CAPABILITIES",
    "HOST_EXCLUSIVE_ENDPOINTS",
    "PLATFORM_CONTRACT_FORMAT",
    "PLATFORM_CONTRACT_VERSION",
    "PlatformRuntime",
    "SHARED_CAPABILITIES",
    "runtime",
]
