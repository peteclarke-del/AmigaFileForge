from __future__ import annotations

import json
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from .disk_service import SESSION_OWNER, DiskError


class OperationCancelled(DiskError):
    """Raised at a safe operation boundary after cancellation was requested."""


class OperationRegistry:
    """Thread-safe progress records for requests that are still running."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self._items: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._storage_path = Path(storage_path) if storage_path else None
        self._load()

    def _load(self) -> None:
        if not self._storage_path or not self._storage_path.is_file():
            return
        try:
            items = json.loads(self._storage_path.read_text(encoding="utf-8"))
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict) or not re.fullmatch(
                    r"[0-9a-f-]{32,36}", str(item.get("id", ""))
                ):
                    continue
                if item.get("state") in {"running", "cancelling"}:
                    item.update(
                        state="interrupted",
                        message="Interrupted by an application restart; retry the operation safely",
                    )
                self._items[item["id"]] = item
        except (OSError, ValueError):
            self._items = {}

    def _persist(self) -> None:
        if not self._storage_path:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._storage_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(list(self._items.values()), separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self._storage_path)

    @staticmethod
    def _validate(operation_id: str) -> str:
        if not re.fullmatch(r"[0-9a-f-]{32,36}", operation_id or ""):
            raise DiskError("Invalid operation identifier.")
        return operation_id

    def start(self, operation_id: str, message: str) -> None:
        operation_id = self._validate(operation_id)
        now = time.time()
        with self._lock:
            self._items = {
                key: value
                for key, value in self._items.items()
                if now - value["updatedAt"] < 3600
            }
            existing = self._items.get(operation_id)
            cancel_requested = existing and existing["state"] == "cancelling"
            self._items[operation_id] = {
                "id": operation_id,
                "state": "cancelling" if cancel_requested else "running",
                "message": "Stopping at the next safe boundary" if cancel_requested else message,
                "current": None,
                "total": None,
                "updatedAt": now,
                "startedAt": now,
                "ownerId": SESSION_OWNER.get(),
            }
            self._persist()

    @contextmanager
    def tracked(
        self,
        operation_id: str | None,
        start_message: str,
        complete_message: str = "Complete",
    ) -> Iterator[Callable[..., None]]:
        """Own the complete lifecycle of one cancellable operation."""
        if operation_id:
            self.start(operation_id, start_message)
        progress = lambda message, current=None, total=None: self.update(
            operation_id, message, current, total
        )
        try:
            yield progress
        except OperationCancelled as exc:
            self.cancelled(operation_id, str(exc))
            raise
        except Exception as exc:
            self.fail(operation_id, str(exc))
            raise
        else:
            self.finish(operation_id, complete_message)

    def update(
        self,
        operation_id: str | None,
        message: str,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        if not operation_id:
            return
        operation_id = self._validate(operation_id)
        with self._lock:
            item = self._items.get(operation_id)
            if item is None:
                return
            if item["state"] == "cancelling":
                raise OperationCancelled("Operation aborted at a safe disk boundary.")
            item.update(
                message=message,
                current=current,
                total=total,
                updatedAt=time.time(),
            )
            self._persist()

    def finish(self, operation_id: str | None, message: str = "Complete") -> None:
        self._set_terminal(operation_id, "complete", message)

    def fail(self, operation_id: str | None, message: str) -> None:
        self._set_terminal(operation_id, "failed", message)

    def cancel(self, operation_id: str) -> dict:
        operation_id = self._validate(operation_id)
        now = time.time()
        with self._lock:
            item = self._items.get(operation_id)
            if item is None:
                item = {
                    "id": operation_id,
                    "state": "cancelling",
                    "message": "Stopping at the next safe boundary",
                    "current": None,
                    "total": None,
                    "updatedAt": now,
                    "startedAt": now,
                    "ownerId": SESSION_OWNER.get(),
                }
                self._items[operation_id] = item
            elif item.get("ownerId") and item.get("ownerId") != SESSION_OWNER.get():
                raise DiskError("Operation progress is no longer available.")
            elif item["state"] == "running":
                item.update(
                    state="cancelling",
                    message="Stopping at the next safe boundary",
                    updatedAt=now,
                )
            self._persist()
            return dict(item)

    def cancelled(self, operation_id: str | None, message: str = "Operation aborted safely") -> None:
        self._set_terminal(operation_id, "cancelled", message)

    def pause(self, operation_id: str | None, message: str) -> None:
        self._set_terminal(operation_id, "paused", message)

    def details(self, operation_id: str | None, **details) -> None:
        if not operation_id:
            return
        operation_id = self._validate(operation_id)
        with self._lock:
            item = self._items.get(operation_id)
            if item is not None:
                item.update(details=details, updatedAt=time.time())
                self._persist()

    def _set_terminal(self, operation_id: str | None, state: str, message: str) -> None:
        if not operation_id:
            return
        operation_id = self._validate(operation_id)
        with self._lock:
            item = self._items.get(operation_id)
            if item is not None:
                item.update(
                    state=state,
                    message=message,
                    updatedAt=time.time(),
                )
                self._persist()

    def get(self, operation_id: str) -> dict:
        operation_id = self._validate(operation_id)
        with self._lock:
            try:
                item = self._items[operation_id]
                if item.get("ownerId") and item.get("ownerId") != SESSION_OWNER.get():
                    raise KeyError(operation_id)
                return self._with_metrics(item)
            except KeyError as exc:
                raise DiskError("Operation progress is no longer available.") from exc

    def list(self, owner_id: str | None = None) -> list[dict]:
        owner_id = owner_id if owner_id is not None else SESSION_OWNER.get()
        with self._lock:
            return sorted(
                (
                    self._with_metrics(item)
                    for item in self._items.values()
                    if not item.get("ownerId") or item.get("ownerId") == owner_id
                ),
                key=lambda item: float(item.get("updatedAt", 0)),
                reverse=True,
            )

    @staticmethod
    def _with_metrics(item: dict) -> dict:
        """Return derived timing without persisting rapidly changing values."""
        result = dict(item)
        now = time.time()
        started = float(result.get("startedAt") or result.get("updatedAt") or now)
        terminal = {"complete", "failed", "cancelled", "interrupted", "paused"}
        endpoint = (
            float(result.get("updatedAt") or now)
            if result.get("state") in terminal
            else now
        )
        elapsed = max(0.0, endpoint - started)
        current = result.get("current")
        total = result.get("total")
        rate = None
        eta = None
        if isinstance(current, (int, float)) and current > 0 and elapsed > 0:
            rate = float(current) / elapsed
            if isinstance(total, (int, float)) and total > current and rate > 0:
                eta = (float(total) - float(current)) / rate
        result.update(elapsedSeconds=elapsed, ratePerSecond=rate, etaSeconds=eta)
        return result

    def clear_terminal(self, owner_id: str | None = None) -> int:
        owner_id = owner_id if owner_id is not None else SESSION_OWNER.get()
        terminal = {"complete", "failed", "cancelled", "interrupted", "paused"}
        with self._lock:
            remove = [
                key
                for key, item in self._items.items()
                if item.get("state") in terminal
                and (not item.get("ownerId") or item.get("ownerId") == owner_id)
            ]
            for key in remove:
                del self._items[key]
            self._persist()
            return len(remove)
