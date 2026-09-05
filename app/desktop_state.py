"""Durable client state for the random-origin Linux desktop WebView."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading

from .errors import DiskError


class DesktopClientState:
    VERSION = 1
    MAX_BYTES = 64 * 1024 * 1024
    MAX_COLLECTION_IMAGES = 2_000
    MAX_COLLECTION_RECORDS = 1_000_000

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        self._lock = threading.RLock()

    @staticmethod
    def _empty() -> dict:
        return {
            "version": DesktopClientState.VERSION,
            "localStorage": {},
            "collection": {"images": [], "settings": {"key": "preferences", "wanted": []}},
        }

    def read(self) -> dict:
        with self._lock:
            try:
                if self.path.stat().st_size > self.MAX_BYTES:
                    raise DiskError("The Linux desktop preference store exceeds 64 MiB.")
                document = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return self._empty()
            except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
                raise DiskError("The Linux desktop preference store could not be read.") from exc
            if not isinstance(document, dict):
                raise DiskError("The Linux desktop preference store is not a JSON object.")
            if document.get("version") != self.VERSION:
                raise DiskError(
                    f"Linux desktop preference version {document.get('version')!r} is not supported."
                )
            try:
                self.path.chmod(0o600)
            except OSError as exc:
                raise DiskError(
                    "The Linux desktop preference store permissions could not be secured."
                ) from exc
            collection = self._normalise_collection(document.get("collection"))
            local_storage = self._normalise_local_storage(document.get("localStorage"))
            return {
                "version": self.VERSION,
                "localStorage": local_storage,
                "collection": collection,
            }

    @staticmethod
    def _normalise_local_storage(value: object) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict) or len(value) > 200:
            raise DiskError("The Linux desktop preference snapshot is invalid.")
        snapshot = {}
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, str):
                raise DiskError("Linux desktop preference keys and values must be text.")
            if len(key) > 200 or len(item.encode("utf-8")) > 2 * 1024 * 1024:
                raise DiskError(
                    "A Linux desktop preference key or value exceeds its safe size limit."
                )
            snapshot[key] = item
        return snapshot

    @classmethod
    def _normalise_collection(cls, value: object) -> dict:
        if value is None:
            return cls._empty()["collection"]
        if not isinstance(value, dict):
            raise DiskError("The Linux desktop collection snapshot is invalid.")
        images = value.get("images")
        settings = value.get("settings")
        if not isinstance(images, list) or not isinstance(settings, dict):
            raise DiskError("The Linux desktop collection snapshot is incomplete.")
        if len(images) > cls.MAX_COLLECTION_IMAGES or any(not isinstance(image, dict) for image in images):
            raise DiskError("The Linux desktop collection snapshot is invalid.")
        if any(not isinstance(image.get("records") or [], list) for image in images):
            raise DiskError("The Linux desktop collection contains invalid records.")
        if any(
            not isinstance(record, dict)
            for image in images
            for record in (image.get("records") or [])
        ):
            raise DiskError("The Linux desktop collection contains an invalid record.")
        records = sum(len(image.get("records") or []) for image in images)
        if records > cls.MAX_COLLECTION_RECORDS:
            raise DiskError("The Linux desktop collection contains too many records.")
        return {"images": images, "settings": settings}

    def update(self, *, local_storage=None, collection=None) -> dict:
        with self._lock:
            document = self.read()
            if local_storage is not None:
                document["localStorage"] = self._normalise_local_storage(local_storage)
            if collection is not None:
                document["collection"] = self._normalise_collection(collection)
            try:
                encoded = json.dumps(
                    document,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError, RecursionError) as exc:
                raise DiskError("The Linux desktop preference store contains invalid data.") from exc
            if len(encoded.encode("utf-8")) > self.MAX_BYTES:
                raise DiskError("The Linux desktop preference store exceeds 64 MiB.")
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.path.parent,
                    prefix="client-state-",
                    delete=False,
                ) as stream:
                    temporary_path = Path(stream.name)
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary_path.replace(self.path)
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
            return document


__all__ = ["DesktopClientState"]
