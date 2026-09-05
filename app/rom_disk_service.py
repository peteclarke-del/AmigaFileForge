from __future__ import annotations

from pathlib import Path

from .editor_project import editor_project_key, normalise_editor_project
from .errors import DiskError
from .image_session import ImageSession
from .rom import (
    MAX_ROM_SIZE,
    RomError,
    bank_count,
    bank_number,
    inspect_bank as inspect_rom_bank,
    inspect_image as inspect_rom_image,
    parse_rom_header,
    read_bank as read_rom_bank,
    validate_bank_size,
    validate_layout,
    validate_platform,
)
from .rom_workbench import normalise_project


COPY_BUFFER_SIZE = 8 * 1024 * 1024


class RomDiskMixin:
    """Raw ROM bank maintenance and persistent ROM/editor project state."""

    def list_rom_banks(self, session: ImageSession) -> list[dict]:
        if session.kind != "rom":
            raise DiskError("This image is not a ROM.")
        with session.lock:
            return inspect_rom_image(
                session.path,
                session.rom_bank_size,
                session.rom_erase_byte,
            )

    def inspect_rom_bank(self, session: ImageSession, bank: int) -> dict:
        """Decode one bank deeply without bloating every directory listing."""
        if session.kind != "rom":
            raise DiskError("This image is not a ROM.")
        rows = self.list_rom_banks(session)
        summary = next((row for row in rows if int(row["bank"]) == int(bank)), None)
        if summary is None:
            raise DiskError(f"ROM bank {bank} does not exist.")
        try:
            data = read_rom_bank(session.path, int(bank), session.rom_bank_size)
        except RomError as exc:
            raise DiskError(str(exc)) from exc
        decoded = inspect_rom_bank(
            data,
            int(bank),
            session.rom_erase_byte,
            include_contents=True,
            # Kickstart finds a ROM's contents by scanning for resident tags,
            # so every Amiga ROM is scanned for them, not only an extended one.
            include_resident_modules=True,
        )
        decoded["matchingBanks"] = summary.get("matchingBanks", [])
        if summary.get("extensionHeader"):
            decoded["extensionHeader"] = summary["extensionHeader"]
            decoded["filetype"] = summary["filetype"]
            decoded["structures"] = summary["structures"]
        return decoded

    def configure_rom(
        self,
        session: ImageSession,
        *,
        bank_size: int,
        erase_byte: int,
        platform: str,
        layout: str,
    ) -> None:
        if session.kind != "rom":
            raise DiskError("This image is not a ROM.")
        try:
            session.rom_bank_size = validate_bank_size(bank_size)
        except RomError as exc:
            raise DiskError(str(exc)) from exc
        session.warnings = [
            warning
            for warning in session.warnings
            if not warning.startswith("The final ROM bank is partial")
        ]
        partial = session.path.stat().st_size % session.rom_bank_size
        if partial:
            session.warnings.append(
                f"The final ROM bank is partial ({partial:,} bytes). It is preserved exactly."
            )
        session.rom_erase_byte = int(erase_byte) & 0xFF
        try:
            session.rom_platform = validate_platform(platform)
            session.rom_layout = validate_layout(layout)
        except RomError as exc:
            raise DiskError(str(exc)) from exc
        session.dirty = True
        (session.path.parent / "download-ready.zip").unlink(missing_ok=True)
        (session.path.parent / "download-ready.json").unlink(missing_ok=True)
        self._persist_session(session)

    def put_rom_bank(self, session: ImageSession, data: bytes, bank: int | None = None) -> int:
        if session.kind != "rom":
            raise DiskError("This image is not a ROM.")
        if not data:
            raise DiskError("The selected ROM bank is empty.")
        if len(data) > session.rom_bank_size:
            raise DiskError(
                f"That file contains {len(data):,} bytes and does not fit one "
                f"{session.rom_bank_size:,}-byte bank. Split it into banks or change the ROM layout first."
            )
        rows = self.list_rom_banks(session)
        if bank is None:
            bank = next((int(row["bank"]) for row in rows if row["empty"]), len(rows))
        if bank < 0:
            raise DiskError("Choose a ROM bank.")
        offset = bank * session.rom_bank_size
        padded = data.ljust(session.rom_bank_size, bytes((session.rom_erase_byte,)))
        with session.lock, session.path.open("r+b") as image:
            current_size = session.path.stat().st_size
            if offset > current_size:
                image.seek(current_size)
                image.write(bytes((session.rom_erase_byte,)) * (offset - current_size))
            image.seek(offset)
            image.write(padded)
        self._mark_mutated(session)
        self._persist_session(session)
        return bank

    def clear_rom_banks(self, session: ImageSession, banks: list[int]) -> list[int]:
        if session.kind != "rom":
            raise DiskError("This image is not a ROM.")
        count = bank_count(session.path.stat().st_size, session.rom_bank_size)
        selected = sorted(set(int(bank) for bank in banks))
        if not selected or any(bank < 0 or bank >= count for bank in selected):
            raise DiskError("Choose one or more existing ROM banks.")
        blank = bytes((session.rom_erase_byte,)) * session.rom_bank_size
        with session.lock, session.path.open("r+b") as image:
            for bank in selected:
                offset = bank * session.rom_bank_size
                image.seek(offset)
                length = min(session.rom_bank_size, session.path.stat().st_size - offset)
                image.write(blank[:length])
        self._mark_mutated(session)
        self._persist_session(session)
        return selected

    def move_rom_banks(self, session: ImageSession, sources: list[int], target_start: int) -> list[int]:
        """Move banks atomically, including overlapping source/target ranges."""
        if session.kind != "rom":
            raise DiskError("This image is not a ROM.")
        source_banks = [int(bank) for bank in sources]
        if not source_banks or len(set(source_banks)) != len(source_banks):
            raise DiskError("Choose distinct ROM banks to move.")
        count = bank_count(session.path.stat().st_size, session.rom_bank_size)
        if (
            any(bank < 0 or bank >= count for bank in source_banks)
            or target_start < 0
            or target_start > count
        ):
            raise DiskError("Choose valid ROM bank positions.")
        targets = list(range(int(target_start), int(target_start) + len(source_banks)))
        data = [read_rom_bank(session.path, bank, session.rom_bank_size) for bank in source_banks]
        blank = bytes((session.rom_erase_byte,)) * session.rom_bank_size
        with session.lock, session.path.open("r+b") as image:
            for bank in set(source_banks) - set(targets):
                image.seek(bank * session.rom_bank_size)
                image.write(blank)
            for bank, content in zip(targets, data, strict=True):
                image.seek(bank * session.rom_bank_size)
                image.write(content.ljust(session.rom_bank_size, bytes((session.rom_erase_byte,))))
        self._mark_mutated(session)
        self._persist_session(session)
        return targets

    def rename_rom_bank(self, session: ImageSession, bank: int, title: str) -> None:
        try:
            data = bytearray(read_rom_bank(session.path, bank, session.rom_bank_size))
        except RomError as exc:
            raise DiskError(str(exc)) from exc
        header = parse_rom_header(data)
        if header is None:
            raise DiskError("That bank has no editable Amiga-family ROM title header.")
        try:
            encoded = str(title).encode("ascii")
        except UnicodeEncodeError as exc:
            raise DiskError("ROM titles can use printable ASCII characters only.") from exc
        marker = int(data[7])
        copyright_end = data.find(0, marker + 1, min(len(data), marker + 192)) if marker < len(data) else -1
        region_end = copyright_end + 1 if copyright_end >= 0 else marker + 1
        version = header.version.encode("ascii", "replace")
        copyright_text = header.copyright.encode("ascii", "replace")
        required = len(encoded) + 1 + len(version) + 1 + len(copyright_text) + 1
        available = region_end - 9
        maximum_title = max(0, available - (required - len(encoded)))
        if not encoded or required > available:
            raise DiskError(
                f"This header has room for a title of 1 to {maximum_title} characters. "
                "Use the hex editor to reorganise the header before making it longer."
            )
        if any(byte < 32 or byte > 126 for byte in encoded):
            raise DiskError("ROM titles can use printable ASCII characters only.")
        data[9:region_end] = bytes((session.rom_erase_byte,)) * available
        cursor = 9
        for value in (encoded, version):
            data[cursor : cursor + len(value)] = value
            cursor += len(value)
            data[cursor] = 0
            cursor += 1
        data[7] = cursor - 1
        data[cursor : cursor + len(copyright_text)] = copyright_text
        data[cursor + len(copyright_text)] = 0
        self.put_rom_bank(session, bytes(data), bank)

    def rom_bank_bytes(self, session: ImageSession, inner: str) -> bytes:
        try:
            return read_rom_bank(session.path, bank_number(inner), session.rom_bank_size)
        except RomError as exc:
            raise DiskError(str(exc)) from exc

    def replace_rom_bytes(self, session: ImageSession, data: bytes) -> None:
        if session.kind != "rom":
            raise DiskError("This image is not a ROM.")
        if not data or len(data) > MAX_ROM_SIZE:
            raise DiskError("ROM images must contain between 1 byte and 64 MiB.")
        temporary = session.path.with_name(f".{session.path.name}.rom-update")
        with session.lock:
            try:
                temporary.write_bytes(data)
                temporary.replace(session.path)
            finally:
                temporary.unlink(missing_ok=True)
            self._mark_mutated(session)
            self._persist_session(session)

    def save_rom_project(self, session: ImageSession, document: dict) -> dict:
        if session.kind != "rom":
            raise DiskError("This image is not a ROM.")
        session.rom_project = normalise_project(document)
        session.dirty = True
        self._persist_session(session)
        return session.rom_project

    def editor_project(self, session: ImageSession, path: str, side: int | None) -> dict:
        key = editor_project_key(path, side)
        return normalise_editor_project(session.editor_projects.get(key))

    def save_editor_project(
        self, session: ImageSession, path: str, side: int | None, document: dict,
    ) -> dict:
        key = editor_project_key(path, side)
        project = normalise_editor_project(document)
        with session.lock:
            session.editor_projects[key] = project
            self._persist_session(session)
        return project

    def move_editor_projects(
        self,
        session: ImageSession,
        moves: list[dict],
        side: int | None,
    ) -> int:
        """Follow file and directory moves without orphaning editor annotations."""
        replacements = sorted(
            (
                (str(item.get("source") or "").rstrip("."), str(item.get("destination") or "").rstrip("."))
                for item in moves
                if item.get("source") and item.get("destination")
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        if not replacements:
            return 0
        changed: dict[str, dict] = {}
        removed: list[str] = []
        side_key = str(side) if side is not None else "-"
        for key, project in list(session.editor_projects.items()):
            key_side, separator, path = key.partition("|")
            if not separator or key_side != side_key:
                continue
            folded = path.casefold()
            for source, destination in replacements:
                source_folded = source.casefold()
                if folded != source_folded and not folded.startswith(source_folded + "/"):
                    continue
                suffix = path[len(source):]
                changed[editor_project_key(destination + suffix, side)] = project
                removed.append(key)
                break
        if not removed:
            return 0
        with session.lock:
            for key in removed:
                session.editor_projects.pop(key, None)
            session.editor_projects.update(changed)
            self._persist_session(session)
        return len(removed)

    def delete_editor_projects(
        self,
        session: ImageSession,
        paths: list[str],
        side: int | None,
    ) -> int:
        """Remove annotations belonging to deleted files or directory trees."""
        prefixes = [str(path or "").rstrip(".").casefold() for path in paths if path]
        side_key = str(side) if side is not None else "-"
        removed = []
        for key in session.editor_projects:
            key_side, separator, path = key.partition("|")
            if not separator or key_side != side_key:
                continue
            folded = path.casefold()
            if any(folded == prefix or folded.startswith(prefix + "/") for prefix in prefixes):
                removed.append(key)
        if not removed:
            return 0
        with session.lock:
            for key in removed:
                session.editor_projects.pop(key, None)
            self._persist_session(session)
        return len(removed)

    def rom_component_exports(self, session: ImageSession) -> list[tuple[Path, str]]:
        """Create byte-wide chip files for a documented interleaved ROM set."""
        if session.kind != "rom" or not session.rom_layout.startswith("byte-interleaved-"):
            return []
        try:
            component_count = int(session.rom_layout.rsplit("-", 1)[-1])
        except ValueError as exc:
            raise DiskError("The ROM component layout is invalid.") from exc
        if component_count not in {2, 4}:
            raise DiskError("Only two-chip and four-chip ROM layouts can be exported.")
        names = list(session.rom_component_names[:component_count])
        names.extend(
            f"{Path(session.name).stem}-chip-{index + 1}.rom"
            for index in range(len(names), component_count)
        )
        paths = [session.path.parent / f"rom-component-{index}.bin" for index in range(component_count)]
        handles = [path.open("wb") for path in paths]
        try:
            with session.path.open("rb") as source:
                remainder = b""
                while chunk := source.read(COPY_BUFFER_SIZE):
                    chunk = remainder + chunk
                    complete = len(chunk) - (len(chunk) % component_count)
                    body, remainder = chunk[:complete], chunk[complete:]
                    for index, handle in enumerate(handles):
                        handle.write(body[index::component_count])
                if remainder:
                    raise DiskError(
                        f"The ROM size is not divisible by its {component_count}-chip byte layout."
                    )
        finally:
            for handle in handles:
                handle.close()
        return list(zip(paths, [self.safe_filename(name) for name in names], strict=True))


