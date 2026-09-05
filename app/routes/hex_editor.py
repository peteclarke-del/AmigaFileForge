from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..archive_browser import ArchiveError, MAX_ARCHIVE_BYTES, read_archive_member_details
from ..disk_service import DiskError, DiskService
from ..hex_service import compare_data, compare_raw_image, raw_image_range, search_raw_image, write_raw_image
from ..file_editor import data_range, file_range, search_data, search_file, write_file_range
from .common import apply_partition, payload
from .effects import image_mutation, request_effect
from .. import amiga_paths


def _integer(value: object, label: str, default: int = 0) -> int:
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError) as exc:
        raise DiskError(f"The {label} must be a whole number.") from exc


def _optional_integer(value: object, label: str) -> int | None:
    return None if value in (None, "") else _integer(value, label)


def _comparison_upload():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise DiskError("Choose a binary file to compare.")
    upload.stream.seek(0, 2)
    size = upload.stream.tell()
    upload.stream.seek(0)
    return upload, size


def create_hex_editor_blueprint(service: DiskService) -> Blueprint:
    blueprint = Blueprint("hex_editor", __name__)

    def archive_member(image_id):
        session = service.get(image_id)
        path = str(request.args.get("path") or "")
        member = str(request.args.get("member") or "")
        if not path or not member:
            raise ArchiveError("Choose an archive member to inspect.")
        apply_partition(service, session, request.args.get("partition"))
        side = _optional_integer(request.args.get("side"), "side")
        metadata = service.file_metadata(session, path, side)
        if int(metadata.get("length") or 0) > MAX_ARCHIVE_BYTES:
            raise ArchiveError("That archive is too large to browse safely in memory.")
        outer = service.read_file(session, path, side)
        filename = str(request.args.get("name") or amiga_paths.leaf(path))
        content, _metadata = read_archive_member_details(outer, filename, member)
        return member, content

    @blueprint.get("/api/images/<image_id>/hex")
    def read_hex(image_id):
        session = service.get(image_id)
        return jsonify(raw_image_range(
            session,
            _integer(request.args.get("offset"), "offset"),
            _integer(request.args.get("length"), "length", 256),
            str(request.args.get("target") or "image"),
        ))

    @blueprint.get("/api/images/<image_id>/hex/search")
    def search_hex(image_id):
        session = service.get(image_id)
        return jsonify(search_raw_image(
            session,
            str(request.args.get("query") or ""),
            str(request.args.get("mode") or "hex"),
            _integer(request.args.get("start"), "start"),
            str(request.args.get("direction") or "forward"),
            str(request.args.get("wrap") or "true").lower() not in {"0", "false", "no"},
            str(request.args.get("target") or "image"),
        ))

    @blueprint.post("/api/images/<image_id>/hex")
    @image_mutation("editing raw image bytes")
    def write_hex(image_id):
        data = payload()
        session = service.get(image_id)
        return jsonify(write_raw_image(
            service,
            session,
            str(data.get("version") or ""),
            data.get("changes"),
            data.get("confirmed") is True,
            str(data.get("target") or "image"),
        ))

    @blueprint.post("/api/images/<image_id>/hex/compare")
    @request_effect("read-only", "comparing image bytes")
    def compare_hex(image_id):
        upload, size = _comparison_upload()
        session = service.get(image_id)
        report = compare_raw_image(session, upload.stream, size, str(request.args.get("target") or "image"))
        report["name"] = upload.filename
        return jsonify(report)

    @blueprint.get("/api/images/<image_id>/file-hex")
    def read_file_hex(image_id):
        session = service.get(image_id)
        path = str(request.args.get("path") or "")
        if not path:
            raise DiskError("Choose a file to inspect.")
        return jsonify(file_range(
            service, session, path,
            _optional_integer(request.args.get("side"), "side"),
            _integer(request.args.get("offset"), "offset"), _integer(request.args.get("length"), "length", 256),
        ))

    @blueprint.get("/api/images/<image_id>/file-hex/search")
    def search_file_hex(image_id):
        session = service.get(image_id)
        path = str(request.args.get("path") or "")
        if not path:
            raise DiskError("Choose a file to search.")
        return jsonify(search_file(
            service, session, path,
            _optional_integer(request.args.get("side"), "side"), str(request.args.get("query") or ""),
            str(request.args.get("mode") or "hex"), _integer(request.args.get("start"), "start"),
            str(request.args.get("direction") or "forward"),
            str(request.args.get("wrap") or "true").lower() not in {"0", "false", "no"},
        ))

    @blueprint.post("/api/images/<image_id>/file-hex")
    @image_mutation("editing raw file bytes")
    def write_file_hex(image_id):
        data = payload()
        session = service.get(image_id)
        path = str(data.get("path") or "")
        if not path:
            raise DiskError("Choose a file to edit.")
        return jsonify(write_file_range(
            service, session, path,
            _optional_integer(data.get("side"), "side"), str(data.get("version") or ""),
            data.get("changes"), data.get("confirmed") is True,
        ))

    @blueprint.post("/api/images/<image_id>/file-hex/compare")
    @request_effect("read-only", "comparing file bytes")
    def compare_file_hex(image_id):
        upload, size = _comparison_upload()
        session = service.get(image_id)
        path = str(request.args.get("path") or "")
        if not path:
            raise DiskError("Choose a file to compare.")
        apply_partition(service, session, request.args.get("partition"))
        data = service.read_file(
            session, path, _optional_integer(request.args.get("side"), "side")
        )
        report = compare_data(data, upload.stream, size)
        report["name"] = upload.filename
        return jsonify(report)

    @blueprint.get("/api/images/<image_id>/archive-hex")
    def read_archive_hex(image_id):
        member, content = archive_member(image_id)
        return jsonify(data_range(
            content, member.rsplit("/", 1)[-1],
            _integer(request.args.get("offset"), "offset"),
            _integer(request.args.get("length"), "length", 256),
            read_only=True,
        ))

    @blueprint.get("/api/images/<image_id>/archive-hex/search")
    def search_archive_hex(image_id):
        _member, content = archive_member(image_id)
        return jsonify(search_data(
            content, str(request.args.get("query") or ""),
            str(request.args.get("mode") or "hex"),
            _integer(request.args.get("start"), "start"),
            str(request.args.get("direction") or "forward"),
            str(request.args.get("wrap") or "true").lower() not in {"0", "false", "no"},
        ))

    @blueprint.post("/api/images/<image_id>/archive-hex/compare")
    @request_effect("read-only", "comparing archive member bytes")
    def compare_archive_hex(image_id):
        upload, size = _comparison_upload()
        _member, content = archive_member(image_id)
        report = compare_data(content, upload.stream, size)
        report["name"] = upload.filename
        return jsonify(report)

    return blueprint
