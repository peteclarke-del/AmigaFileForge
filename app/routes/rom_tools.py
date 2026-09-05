from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from ..checksum import sha256_bytes
from ..disk_service import DiskError, DiskService
from ..emulator_config import emulator_status
from ..rom_workbench import (
    RomWorkbenchError, apply_patch, audit_rom, bank_map, build_data_archive,
    build_expansion_rom, compare_roms, disassemble, hardware_export,
    hardware_export_zip, identify_rom, make_patch, make_selective_patch, repair_extension_checksum,
    repair_header_role_flags,
)
from .common import payload
from .effects import image_mutation, request_effect


def create_rom_tools_blueprint(service: DiskService, root: Path) -> Blueprint:
    blueprint = Blueprint("rom_tools", __name__)
    catalogue = root / "rom_catalogue.json"

    def user_catalogue_path(session) -> Path:
        folder = service.work_dir / "rom-catalogues"
        folder.mkdir(exist_ok=True)
        return folder / f"{session.owner_id or 'local'}.json"

    def combined_catalogue(session) -> Path:
        records = []
        for path in (catalogue, user_catalogue_path(session)):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                records.extend(row for row in document.get("roms", []) if isinstance(row, dict))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        target = session.path.parent / "combined-rom-catalogue.json"
        target.write_text(json.dumps({"roms": records}, separators=(",", ":")), encoding="utf-8")
        return target

    def rom_session(image_id: str):
        session = service.get(image_id)
        if session.kind != "rom":
            raise DiskError("This image is not a ROM.")
        return session

    def session_bytes(image_id: str) -> tuple:
        session = rom_session(image_id)
        return session, session.path.read_bytes()

    @blueprint.get("/api/images/<image_id>/rom/map")
    def rom_map(image_id):
        session, data = session_bytes(image_id)
        result = bank_map(data, session.rom_bank_size, session.rom_erase_byte)
        lanes = int(session.rom_layout.rsplit("-", 1)[-1]) if session.rom_layout.startswith("byte-interleaved-") else 1
        result["layout"] = session.rom_layout
        result["lanes"] = lanes
        for row in result["banks"]:
            row["physical"] = [
                {"lane": lane + 1, "offset": row["fileOffset"] // lanes}
                for lane in range(lanes)
            ]
        return jsonify(result)

    @blueprint.get("/api/images/<image_id>/rom/identify")
    def rom_identify(image_id):
        session, data = session_bytes(image_id)
        return jsonify(identify_rom(data, combined_catalogue(session)))

    @blueprint.put("/api/images/<image_id>/rom/identity")
    @request_effect("external", "saving a user ROM identity record")
    def rom_save_identity(image_id):
        session, data = session_bytes(image_id)
        document = payload()
        record = {"sha256": sha256_bytes(data), "size": len(data)}
        for key, limit in {"title": 160, "version": 80, "publisher": 160,
                           "platform": 120, "notes": 2000}.items():
            record[key] = str(document.get(key) or "")[:limit]
        if not record["title"]:
            raise DiskError("Enter the ROM's recognised title.")
        path = user_catalogue_path(session)
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            saved = {"format": "amiga-file-forge-user-rom-catalogue-1", "roms": []}
        saved["roms"] = [row for row in saved.get("roms", []) if row.get("sha256") != record["sha256"]]
        saved["roms"].append(record)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        temporary.replace(path)
        project = dict(session.rom_project); project["identity"] = record
        service.save_rom_project(session, project)
        return jsonify(identity=record, image=service.summary(session))

    @blueprint.get("/api/images/<image_id>/rom/audit")
    def rom_audit(image_id):
        session, data = session_bytes(image_id)
        return jsonify(audit_rom(data, session.rom_bank_size, session.rom_erase_byte))

    @blueprint.get("/api/images/<image_id>/rom/disassembly")
    def rom_disassembly(image_id):
        session = rom_session(image_id)
        bank = int(request.args.get("bank", "0"))
        block = service.rom_bank_bytes(session, f"bank:{bank}")
        decoded = service.inspect_rom_bank(session, bank)
        requested_architecture = str(request.args.get("architecture") or "auto").lower()
        processor = str((decoded.get("header") or {}).get("processor") or "").lower()
        architecture = (
            "arm" if requested_architecture == "auto" and ("arm" in processor or session.rom_platform == "a4000")
            else "m68k" if requested_architecture == "auto" and "68000" in processor
            else "68000" if requested_architecture == "auto"
            else requested_architecture
        )
        origin = int(request.args.get("origin", "0" if architecture == "arm" else "0x8000"), 0)
        entry_points = []
        header = decoded.get("header") or {}
        entry_points.extend(value for value in (header.get("languageEntry"), header.get("serviceEntry")) if isinstance(value, int))
        entry_points.extend(row["handlerAddress"] for row in decoded.get("starCommands", []) if isinstance(row.get("handlerAddress"), int))
        project_symbols = session.rom_project.get("symbols", {})
        symbols = {}
        for key, value in project_symbols.items():
            try:
                symbols[int(str(key), 0)] = value
            except ValueError:
                continue
        report = disassemble(
            block, architecture=architecture, origin=origin,
            start=int(request.args.get("offset", "0"), 0),
            length=int(request.args.get("length", "4096"), 0), symbols=symbols,
            entry_points=entry_points,
        )
        regions = []
        for row in session.rom_project.get("regions", []):
            try:
                parse = lambda value: int(str(value).replace("&", "0x"), 0)
                regions.append((parse(row.get("start")), parse(row.get("end")), str(row.get("name") or "")))
            except (TypeError, ValueError):
                continue
        for row in report["rows"]:
            region = next((name for start, end, name in regions if start <= row["address"] <= end), "")
            if region:
                row["comment"] = " · ".join(value for value in (row.get("comment"), region) if value)
        return jsonify(report)

    @blueprint.post("/api/images/<image_id>/rom/compare")
    @request_effect("read-only", "comparing ROM images")
    def rom_compare(image_id):
        session, left = session_bytes(image_id)
        document = payload()
        other = service.get(str(document.get("targetImage") or ""))
        if other.kind != "rom":
            raise DiskError("Choose another open ROM image to compare.")
        right = other.path.read_bytes()
        report = compare_roms(left, right)
        if document.get("includePatch"):
            try:
                report["patch"] = (
                    make_selective_patch(left, right, document["rangeIndexes"])
                    if isinstance(document.get("rangeIndexes"), list)
                    else make_patch(left, right)
                )
            except RomWorkbenchError as exc:
                report["patchUnavailable"] = str(exc)
        report["leftName"], report["rightName"] = session.name, other.name
        return jsonify(report)

    @blueprint.put("/api/images/<image_id>/rom/project")
    @image_mutation("editing ROM project notes")
    def rom_project(image_id):
        session = rom_session(image_id)
        return jsonify(project=service.save_rom_project(session, payload()), image=service.summary(session))

    @blueprint.post("/api/images/<image_id>/rom/patch")
    @image_mutation("applying a ROM patch")
    def rom_patch(image_id):
        session, data = session_bytes(image_id)
        try:
            result = apply_patch(data, payload().get("patch") or {})
        except (RomWorkbenchError, KeyError, TypeError, ValueError) as exc:
            raise DiskError(str(exc)) from exc
        service.replace_rom_bytes(session, result)
        return jsonify(image=service.summary(session))

    @blueprint.post("/api/images/<image_id>/rom/repair")
    @image_mutation("repairing ROM metadata")
    def rom_repair(image_id):
        session, data = session_bytes(image_id)
        action = payload().get("action")
        try:
            result = (
                repair_extension_checksum(data) if action == "extension-checksum"
                else repair_header_role_flags(data, session.rom_bank_size) if action == "header-role-flags"
                else None
            )
        except RomWorkbenchError as exc:
            raise DiskError(str(exc)) from exc
        if result is None:
            raise DiskError("That ROM repair is not available.")
        service.replace_rom_bytes(session, result)
        return jsonify(image=service.summary(session), report=audit_rom(result, session.rom_bank_size, session.rom_erase_byte))

    @blueprint.post("/api/images/<image_id>/rom/build")
    @image_mutation("building a ROM image")
    def rom_build(image_id):
        session = rom_session(image_id)
        document = payload()
        try:
            if document.get("template") == "data-archive":
                files = [(str(row.get("name") or "FILE"), bytes.fromhex(str(row.get("hex") or "")))
                         for row in document.get("files", [])]
                result = build_data_archive(document.get("title"), files,
                                            size=int(document.get("size", 16384)), erase_byte=session.rom_erase_byte)
            else:
                result = build_expansion_rom(document.get("title"), document.get("commands", []),
                                            size=int(document.get("size", 16384)), erase_byte=session.rom_erase_byte)
        except (RomWorkbenchError, TypeError, ValueError) as exc:
            raise DiskError(str(exc)) from exc
        service.replace_rom_bytes(session, result)
        return jsonify(image=service.summary(session))

    @blueprint.post("/api/images/<image_id>/rom/hardware-export")
    @request_effect("read-only", "preparing ROM programmer files")
    def rom_hardware_export(image_id):
        session, data = session_bytes(image_id)
        document = payload()
        try:
            address_swaps = []
            for value in document.get("addressSwaps", []):
                if not isinstance(value, list) or len(value) != 2:
                    raise RomWorkbenchError("Each address-line swap needs two bit numbers.")
                address_swaps.append((int(value[0]), int(value[1])))
            result = hardware_export(data, device_size=int(document.get("deviceSize", len(data))),
                                     erase_byte=int(document.get("eraseByte", session.rom_erase_byte)),
                                     mirror=bool(document.get("mirror")), lanes=int(document.get("lanes", 1)),
                                     byte_swap=bool(document.get("byteSwap")),
                                     word_swap=bool(document.get("wordSwap")), address_swaps=address_swaps)
        except (RomWorkbenchError, TypeError, ValueError) as exc:
            raise DiskError(str(exc)) from exc
        body = hardware_export_zip(result, Path(session.name).stem)
        return Response(body, mimetype="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{Path(session.name).stem}-programmer.zip"'
        })

    @blueprint.get("/api/images/<image_id>/rom/emulator")
    def rom_emulator_status(image_id):
        session = rom_session(image_id)
        status = emulator_status(session)
        return jsonify(available=False, image=session.name, command="", configuredBy=status["configuredBy"],
                       message=f"{status['label']} is managed for this target. Direct ROM attachment is not exposed until the selected machine's ROM address mapping can be proved safely.")

    @blueprint.post("/api/images/<image_id>/rom/emulator")
    @request_effect("external", "launching a ROM in an emulator")
    def rom_emulator_run(image_id):
        rom_session(image_id)
        raise DiskError("Direct ROM attachment is not enabled for this managed machine. Use the ROM programmer export or place the ROM in a machine-specific image first.")

    return blueprint
