"""Shared application exceptions.

Keeping these exceptions outside the service modules avoids circular imports while
giving routes and collaborating services one stable error hierarchy to catch.
"""

from __future__ import annotations


class DiskError(RuntimeError):
    """A user-facing image or filing-system operation failed."""


class EmptyDiskError(DiskError):
    """An extraction found nothing to copy and needs a skip or abort decision."""

    def __init__(self, disk: dict):
        self.disk = disk
        super().__init__(
            f"{disk.get('sourceName') or 'The source image'} has an empty directory."
        )


class DestinationExistsError(DiskError):
    """An extraction needs a keep, replace or abort decision."""

    def __init__(self, conflict: dict):
        self.conflict = conflict
        super().__init__(
            f"{conflict.get('sourceName') or 'The source image'} cannot use "
            f"{conflict['destination']} because that drawer already exists."
        )


class DMSError(ValueError):
    """The bytes are not a usable DiskMasher archive."""
