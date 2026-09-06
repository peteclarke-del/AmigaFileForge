from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from copy import copy
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, after_this_request, jsonify, request, send_file
from .effects import image_mutation, request_effect

from ..analysis_service import (
    build_manifest,
    accept_compatibility_report,
    dependency_report,
    duplicate_report,
    health_report,
    manifest_csv,
    preflight_report,
    workspace_metadata_records,
)
from ..checksum import sha256_bytes, sha256_path
from ..archive_browser import read_archive_member_details
from ..cheat_analysis import analyse_basic, analyse_disassembly, cheat_report, disassembly_diagnostics
from ..cheat_patches import (
    CheatPatchError,
    apply_guarded_cheat_patch,
    build_guarded_cheat_patch,
)
from ..disk_service import DiskError, DiskService
from ..deployment_service import (
    available_deployment_targets,
    build_deployment_archive,
    deployment_plan,
)
from ..emulator_config import (
    MAXIMUM_FLOPPY_DRIVES,
    configured_emulator,
    emulator_command,
    emulator_status,
)
from ..emulator_evidence import EmulatorEvidenceError, capture_emulator_evidence
from ..hardware_profiles import normalise_hardware_profile
from ..image_diff import compare_images, manifest_fingerprint
from ..image_patch import apply_patch_archive, inspect_patch_archive, write_patch_archive
from ..file_editor import (
    disassemble_file,
    disassemble_file_data,
    inspect_editable_file,
    inspect_file_data,
    normalise_basic_source,
    pack_basic_lines,
    prepare_basic_source,
    replace_file_bytes,
    save_editor_text,
    save_editor_text_as,
    search_image_files,
    update_file_properties,
    verify_basic_source,
    encode_editor_replacement,
)
from ..fat_media import FatMediaError, build_hdf_card
from ..operations import OperationRegistry
from ..platform_contract import PlatformRuntime
from ..workflow_recipe import build_workflow_recipe_bundle
from ..dms import DMSError, dms_project
from ..metadata_lookup import lookup_online, parse_distribution_filename
from .common import apply_partition, optional_int, payload, protection_field
from .. import amiga_paths


#: The stack AmigaDOS gives a shell command when nothing sets one, and the
#: range a Stack command may sensibly ask for.
DEFAULT_STACK = 4096
MIN_STACK = 1024
MAX_STACK = 262144


def _stack_bytes(value: object) -> int:
    """Return the stack a test disk should set, as a decimal byte count.

    AmigaDOS ``Stack`` takes a byte count, so anything outside the range a real
    machine accepts falls back to the shell default rather than being written
    into a boot script that would then fail.
    """
    try:
        number = int(str(value or "").strip() or DEFAULT_STACK)
    except ValueError:
        return DEFAULT_STACK
    return number if MIN_STACK <= number <= MAX_STACK else DEFAULT_STACK


def run_emulator_process(arguments: list[str], cwd: str, timeout: int):
    """Keep managed-emulator execution separate from filesystem subprocesses."""
    return subprocess.run(
        arguments, cwd=cwd, capture_output=True, text=True,
        timeout=timeout, check=False,
    )


def clean_emulator_output(output: str) -> str:
    """Remove expected headless audio and X-server shutdown diagnostics."""
    ignored_prefixes = (
        "ALSA lib ",
        "X connection to ",
    )
    return "\n".join(
        line for line in str(output or "").splitlines()
        if not line.startswith(ignored_prefixes)
    ).strip()


@contextmanager
def uploaded_patch_path(work_dir: Path):
    """Retain one uploaded patch only for the duration of its request."""
    upload = request.files.get("patch")
    if not upload or not upload.filename:
        raise DiskError("Choose an Amiga File Forge patch ZIP.")
    with tempfile.NamedTemporaryFile(
        dir=work_dir, prefix="uploaded-patch-", suffix=".zip", delete=False,
    ) as temporary:
        upload.save(temporary)
        patch_path = Path(temporary.name)
    try:
        yield patch_path
    finally:
        patch_path.unlink(missing_ok=True)


class InteractiveEmulator:
    """Own one browser-visible or native managed emulator process."""

    def __init__(self, *, native: bool = False):
        self.native = native
        self.lock = threading.RLock()
        self.process = None
        self.xvfb = None
        self.vnc = None
        self.media_context = None

    @staticmethod
    def _terminate(process):
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()

    def _stop_locked(self):
        process, vnc, xvfb, media = self.process, self.vnc, self.xvfb, self.media_context
        self.process = self.vnc = self.xvfb = self.media_context = None
        self._terminate(process)
        self._terminate(vnc)
        self._terminate(xvfb)
        if media:
            media.__exit__(None, None, None)

    def stop(self):
        with self.lock:
            self._stop_locked()

    def start(self, media_context, *, debug: bool, floppies: list | None = None):
        """Run one emulator session, optionally with discs already inserted.

        ``floppies`` is what makes installing a title possible: the machine
        boots the hard drive with the disc in DF0:, which is the state every
        Amiga installer expects and cannot be reached by handing it the disc
        alone.
        """
        with self.lock:
            self._stop_locked()
            launch, media = media_context.__enter__()
            try:
                arguments, cwd = emulator_command(
                    launch,
                    media,
                    debug=debug,
                    interactive=True,
                    native=self.native,
                    floppies=floppies,
                )
                if not self.native:
                    self.xvfb = subprocess.Popen(
                        ["Xvfb", ":99", "-screen", "0", "1280x960x24", "-ac", "-nolisten", "tcp"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    time.sleep(0.3)
                    self.vnc = subprocess.Popen(
                        ["x11vnc", "-display", ":99", "-rfbport", "5900", "-nopw", "-forever", "-shared", "-quiet"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                self.process = subprocess.Popen(
                    arguments,
                    cwd=cwd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.media_context = media_context
            except Exception:
                self._terminate(self.vnc)
                self._terminate(self.xvfb)
                self.process = self.vnc = self.xvfb = None
                media_context.__exit__(None, None, None)
                raise
            process = self.process
            threading.Thread(target=self._reap, args=(process,), daemon=True).start()
            return arguments, launch

    def _reap(self, process):
        process.communicate()
        with self.lock:
            if self.process is process:
                vnc, xvfb, media = self.vnc, self.xvfb, self.media_context
                self.process = self.vnc = self.xvfb = self.media_context = None
                self._terminate(vnc)
                self._terminate(xvfb)
                if media:
                    media.__exit__(None, None, None)


def create_tools_blueprint(
    service: DiskService,
    operations: OperationRegistry,
    runtime: PlatformRuntime | None = None,
    emulator_manager: InteractiveEmulator | None = None,
) -> Blueprint:
    runtime = runtime or PlatformRuntime()
    interactive_emulator = emulator_manager or InteractiveEmulator(
        native=runtime.kind == "desktop"
    )
    blueprint = Blueprint("tools", __name__)

    def requested_emulator_session(session, data: dict):
        """Apply the browser's effective Workbench profile without mutating the image."""
        requested = data.get("hardwareProfile")
        if not isinstance(requested, dict) or not requested:
            return session
        try:
            profile = normalise_hardware_profile(requested)
        except ValueError as exc:
            raise DiskError(f"The selected Workbench profile is invalid: {exc}") from exc
        configured = copy(session)
        configured.hardware_profile = profile
        configured.target_hardware = str(profile.get("targetHardware") or session.target_hardware)
        return configured

    def status_request_data() -> dict:
        encoded = str(request.args.get("hardwareProfile") or "")
        if not encoded:
            return {}
        try:
            profile = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise DiskError("The Workbench profile sent to the emulator is invalid.") from exc
        return {"hardwareProfile": profile}

    def record_editor_run(
        session,
        path,
        side,
        result: dict,
        *,
        kind: str | None = None,
    ) -> dict:
        """Append one bounded emulator result to the file's shared project history."""
        project = service.editor_project(session, path, side)
        stored = {**result, "kind": kind} if kind else result
        project["tests"] = [*project.get("tests", []), stored][-100:]
        return service.save_editor_project(session, path, side, project)

    @contextmanager
    def isolated_basic_media(session, configured, data: dict):
        path = str(data.get("path") or "")
        apply_partition(service, session, data.get("partition"))
        side = optional_int(data.get("side"))
        if not path:
            raise DiskError("Choose a BASIC file to run.")
        inspection = inspect_editable_file(service, session, path, side)
        if not inspection.get("tokenisedBasic"):
            raise DiskError("Only a recognised AmigaBASIC program can be run in isolation.")
        original = service.read_file(session, path, side)
        source = data.get("source")
        content = encode_editor_replacement(original, str(source), True) if isinstance(source, str) else original
        profile = configured.hardware_profile or {}
        filing_system = str(profile.get("filingSystem") or "ofs").lower()
        disk_format = "ffs" if "ffs" in filing_system else "adf"
        scratch = service.create_blank(disk_format, "Editor", target_hardware=str(configured.target_hardware or "auto"))
        stack = _stack_bytes(profile.get("stack"))
        try:
            with tempfile.NamedTemporaryFile(dir=service.work_dir, prefix="editor-basic-", delete=False) as program_file:
                program_file.write(content)
                program_path = Path(program_file.name)
            with tempfile.NamedTemporaryFile(dir=service.work_dir, prefix="editor-boot-", delete=False) as boot_file:
                # A test disk boots straight into the program under test, with
                # the stack the pane's hardware profile asks for.
                boot_file.write(f"Stack {stack}\nAmigaBASIC Program\n".encode("latin-1"))
                boot_path = Path(boot_file.name)
            try:
                service.put(scratch, "Program", program_path)
                service.make_directory(scratch, "S")
                service.put(scratch, "S/Startup-Sequence", boot_path)
                service._run(["opt", str(scratch.path), "3"])
            finally:
                program_path.unlink(missing_ok=True)
                boot_path.unlink(missing_ok=True)
            yield scratch.path
        finally:
            service.discard_session(scratch)

    @contextmanager
    def whole_drive_media(session, configured):
        """Expose the complete hard drive to the emulator as one attached drive."""
        if session.kind != "hdf":
            raise DiskError("A whole-drive launch requires a hard-drive image.")
        temporary = tempfile.NamedTemporaryFile(
            dir=service.work_dir, prefix="hdf-card-", suffix=".img", delete=False,
        )
        path = Path(temporary.name)
        temporary.close()
        launch = copy(configured)
        launch.emulator_media_kind = "whole-drive"
        try:
            build_hdf_card(session.path, path)
            yield launch, path
        except FatMediaError as exc:
            raise DiskError(str(exc)) from exc
        finally:
            path.unlink(missing_ok=True)

    def selected_media_probe(session, configured, *, debug: bool = False):
        """Build a command for a target without extracting or changing its bytes."""
        if getattr(session, "kind", "") == "hdf":
            probe = copy(configured)
            probe.emulator_media_kind = "whole-drive"
            return emulator_command(probe, Path("selected-hard-drive.img"), debug=debug)
        return emulator_command(configured, configured.path, debug=debug)

    def launch_media(session, configured, data: dict):
        mode = str(data.get("mode") or "parent-auto")
        if mode == "isolated-basic":
            source = isolated_basic_media(session, configured, data)

            @contextmanager
            def isolated():
                with source as media:
                    yield configured, media

            return isolated()
        if mode not in {"parent-auto", "parent-mount", "whole-drive-auto", "whole-drive-mount"}:
            raise DiskError("Choose how the emulator should receive the selected file or its parent image.")
        launch = copy(configured)
        launch.hardware_profile = dict(configured.hardware_profile or {})
        launch.hardware_profile["emulatorBoot"] = "boot" if mode.endswith("auto") else "catalogue"

        if getattr(session, "kind", "") == "hdf":
            return whole_drive_media(session, launch)

        @contextmanager
        def parent_media():
            yield launch, launch.path

        return parent_media()

    @blueprint.post("/api/images/<image_id>/preflight")
    @request_effect("read-only", "building an import preflight report")
    def preflight(image_id):
        data = payload()
        with operations.tracked(
            data.get("operationId"),
            "Reviewing proposed cross-format changes",
            "Compatibility preflight complete",
        ) as progress:
            progress("Checking destination names and metadata", 0, 1)
            report = preflight_report(service, service.get(image_id), data)
            progress("Compatibility report ready", 1, 1)
            return jsonify(report)

    @blueprint.post("/api/images/<image_id>/preflight/accept")
    @request_effect("external", "retaining an accepted compatibility report")
    def accept_preflight(image_id):
        session = service.get(image_id)
        report = accept_compatibility_report(service, session, payload())
        service._persist_session(session)
        return jsonify(
            acceptedAt=report["acceptedAt"],
            retained=len(session.compatibility_reports),
        )

    @blueprint.get("/api/images/<image_id>/deployment/targets")
    @request_effect("read-only", "checking hardware deployment targets")
    def deployment_targets(image_id):
        session = service.get(image_id)
        return jsonify(targets=available_deployment_targets(service, session))

    @blueprint.post("/api/images/<image_id>/deployment/plan")
    @request_effect("read-only", "validating a hardware deployment layout")
    def plan_deployment(image_id):
        data = payload()
        operation_id = data.get("operationId")
        with operations.tracked(
            operation_id,
            "Preparing an isolated deployment snapshot",
            "Hardware deployment plan ready",
        ) as progress:
            return jsonify(deployment_plan(service, service.get(image_id), data, progress))

    @blueprint.post("/api/images/<image_id>/deployment/package")
    @request_effect("read-only", "building a hardware deployment package")
    def package_deployment(image_id):
        session = service.get(image_id)
        data = payload()
        operation_id = data.get("operationId")
        with tempfile.NamedTemporaryFile(
            dir=service.work_dir,
            prefix="hardware-deployment-",
            suffix=".zip",
            delete=False,
        ) as temporary:
            archive_path = Path(temporary.name)
        try:
            with operations.tracked(
                operation_id,
                "Preparing an isolated deployment snapshot",
                "Hardware deployment package ready",
            ) as progress:
                plan = build_deployment_archive(service, session, data, archive_path, progress)
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise

        @after_this_request
        def remove_deployment_archive(response):
            archive_path.unlink(missing_ok=True)
            return response

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        return send_file(
            archive_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{Path(session.name).stem}-{plan['target']}-{timestamp}.zip",
        )

    @blueprint.get("/api/images/<image_id>/health")
    def health(image_id):
        operation_id = request.args.get("operationId")
        with operations.tracked(
            operation_id,
            "Preparing image health checks",
            "Image health check complete",
        ) as progress:
            report = health_report(service, service.get(image_id), progress)
            return jsonify(report)

    @blueprint.get("/api/images/<image_id>/ffs-installations/audit")
    def audit_ffs_installations(image_id):
        session = service.get(image_id)
        operation_id = request.args.get("operationId")
        root = str(request.args.get("root") or "$")
        with operations.tracked(
            operation_id,
            "Finding installed FFS software",
            "Installed FFS software audit complete",
        ) as progress:
            result = service.audit_ffs_installations(session, root, progress)
            return jsonify(result)

    @blueprint.post("/api/images/<image_id>/ffs-installations/repair")
    @image_mutation("repairing installed FFS software")
    def repair_ffs_installations(image_id):
        session = service.get(image_id)
        data = payload()
        operation_id = data.get("operationId")
        directories = data.get("directories")
        if not isinstance(directories, list):
            raise DiskError("Choose the installed disk directories to repair.")
        with operations.tracked(
            operation_id,
            "Rechecking proposed FFS repairs",
            "Installed FFS software repair complete",
        ) as progress:
            result = service.repair_ffs_installations(session, directories, progress)
            return jsonify(image=service.summary(session), repair=result)

    @blueprint.get("/api/images/<image_id>/manifest")
    def manifest(image_id):
        session = service.get(image_id)
        operation_id = request.args.get("operationId")
        with operations.tracked(
            operation_id,
            "Cataloguing image contents",
            "Collection manifest ready",
        ) as progress:
            report = build_manifest(service, session, progress)
            report["fingerprint"] = manifest_fingerprint(report)
            report["revision"] = service.summary(session)["revision"]
        output_format = request.args.get("format", "json").lower()
        if output_format == "csv":
            body = manifest_csv(report)
            suffix = "csv"
            mimetype = "text/csv"
        else:
            body = json.dumps(report, indent=2, ensure_ascii=False)
            suffix = "json"
            mimetype = "application/json"
        stem = Path(session.name).stem
        return Response(
            body,
            mimetype=mimetype,
            headers={"Content-Disposition": f'attachment; filename="{stem}-manifest.{suffix}"'},
        )

    @blueprint.post("/api/images/<image_id>/compare")
    @request_effect("read-only", "comparing logical image contents")
    def compare_image(image_id):
        data = payload()
        operation_id = data.get("operationId")
        other_image_id = str(data.get("otherImage") or "").strip()
        if not other_image_id:
            raise DiskError("Choose another open image to compare.")
        if other_image_id == image_id:
            raise DiskError("Choose two different open images to compare.")
        with operations.tracked(
            operation_id,
            "Cataloguing images for comparison",
            "Image comparison complete",
        ) as progress:
            return jsonify(compare_images(
                service,
                service.get(image_id),
                service.get(other_image_id),
                progress,
            ))

    @blueprint.get("/api/images/<image_id>/patch")
    def create_image_patch(image_id):
        operation_id = request.args.get("operationId")
        other_image_id = str(request.args.get("otherImage") or "").strip()
        return _create_patch_download(image_id, other_image_id, operation_id, None)

    @blueprint.post("/api/images/<image_id>/patch/build")
    @request_effect("read-only", "building a selective guarded image patch")
    def create_selective_image_patch(image_id):
        data = payload()
        selected_keys = data.get("selectedKeys")
        if not isinstance(selected_keys, list):
            raise DiskError("A selective patch requires a reviewed list of change keys.")
        return _create_patch_download(
            image_id,
            str(data.get("otherImage") or "").strip(),
            str(data.get("operationId") or "").strip() or None,
            [str(key) for key in selected_keys],
        )

    def _create_patch_download(image_id, other_image_id, operation_id, selected_keys):
        if not other_image_id or other_image_id == image_id:
            raise DiskError("Choose a different open image as the patch candidate.")
        base, candidate = service.get(image_id), service.get(other_image_id)
        with tempfile.NamedTemporaryFile(
            dir=service.work_dir, prefix="image-patch-", suffix=".affpatch.zip", delete=False,
        ) as temporary:
            patch_path = Path(temporary.name)
        try:
            with operations.tracked(
                operation_id,
                "Cataloguing images for a guarded patch",
                "Guarded patch archive ready",
            ) as progress:
                write_patch_archive(
                    service, base, candidate, patch_path, progress,
                    selected_keys=selected_keys,
                )
        except Exception:
            patch_path.unlink(missing_ok=True)
            raise

        @after_this_request
        def remove_patch(response):
            patch_path.unlink(missing_ok=True)
            return response

        return send_file(
            patch_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{Path(base.name).stem}-to-{Path(candidate.name).stem}.affpatch.zip",
        )

    @blueprint.get("/api/images/<image_id>/workflow-recipe")
    @request_effect("read-only", "building a deterministic workflow recipe")
    def create_workflow_recipe(image_id):
        session = service.get(image_id)
        operation_id = request.args.get("operationId")
        with tempfile.NamedTemporaryFile(
            dir=service.work_dir,
            prefix="workflow-recipe-",
            suffix=".affrecipe.zip",
            delete=False,
        ) as temporary:
            bundle_path = Path(temporary.name)
        try:
            with operations.tracked(
                operation_id,
                "Building deterministic workflow recipe",
                "Deterministic workflow recipe ready",
            ) as progress:
                build_workflow_recipe_bundle(service, session, bundle_path, progress)
        except Exception:
            bundle_path.unlink(missing_ok=True)
            raise

        @after_this_request
        def remove_workflow_recipe(response):
            bundle_path.unlink(missing_ok=True)
            return response

        return send_file(
            bundle_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{Path(session.name).stem}-workflow.affrecipe.zip",
        )

    @blueprint.post("/api/images/<image_id>/patch")
    @image_mutation("applying a guarded image patch")
    def apply_image_patch(image_id):
        operation_id = request.form.get("operationId")
        with operations.tracked(
            operation_id,
            "Verifying the guarded patch",
            "Guarded patch applied and verified",
        ) as progress, uploaded_patch_path(service.work_dir) as patch_path:
            result = apply_patch_archive(service, service.get(image_id), patch_path, progress)
        return jsonify(image=service.summary(service.get(image_id)), patch=result)

    @blueprint.post("/api/images/<image_id>/patch/inspect")
    @request_effect("read-only", "inspecting a guarded image patch")
    def inspect_image_patch(image_id):
        operation_id = request.form.get("operationId")
        with operations.tracked(
            operation_id,
            "Inspecting the guarded patch",
            "Patch preflight complete",
        ) as progress, uploaded_patch_path(service.work_dir) as patch_path:
            result = inspect_patch_archive(service, service.get(image_id), patch_path, progress)
        return jsonify(patch=result)

    @blueprint.get("/api/images/<image_id>/duplicates")
    def duplicates(image_id):
        operation_id = request.args.get("operationId")
        with operations.tracked(
            operation_id,
            "Hashing image contents for duplicate analysis",
            "Duplicate analysis complete",
        ) as progress:
            return jsonify(duplicate_report(service, service.get(image_id), progress))

    @blueprint.get("/api/images/<image_id>/inspect")
    def inspect(image_id):
        session = service.get(image_id)
        path = request.args.get("path", "")
        if not path:
            raise DiskError("Choose a file to inspect.")
        return jsonify(inspect_editable_file(
            service,
            session,
            path,
            optional_int(request.args.get("side")),
        ))

    @blueprint.get("/api/images/<image_id>/dms-project")
    def inspect_dms_project(image_id):
        session = service.get(image_id)
        if session.kind != "dms":
            raise DiskError("The dms project view is available only for DMS images.")
        try:
            return jsonify(dms_project(session.path.read_bytes()))
        except DMSError as exc:
            raise DiskError(str(exc)) from exc

    @blueprint.get("/api/images/<image_id>/dependencies")
    def dependencies(image_id):
        session = service.get(image_id)
        path = request.args.get("path", "")
        if not path:
            raise DiskError("Choose a launcher to inspect.")
        operation_id = request.args.get("operationId")
        with operations.tracked(
            operation_id,
            "Indexing launcher dependencies",
            "Dependency analysis complete",
        ) as progress:
            return jsonify(dependency_report(
                service,
                session,
                path,
                optional_int(request.args.get("side")),
                progress,
            ))

    @blueprint.get("/api/images/<image_id>/cheat-candidates")
    def cheat_candidates(image_id):
        session = service.get(image_id)
        path = str(request.args.get("path") or "")
        if not path:
            raise DiskError("Choose one BASIC or machine-code file to analyse.")
        apply_partition(service, session, request.args.get("partition"))
        side = optional_int(request.args.get("side"))
        online = str(request.args.get("online") or "false").lower() in {"1", "true", "yes"}
        operation_id = request.args.get("operationId")
        with operations.tracked(
            operation_id,
            "Looking for cheat candidates",
            "Cheat-candidate analysis complete",
        ):
            member = str(request.args.get("member") or "")
            if member:
                outer_name = str(request.args.get("name") or amiga_paths.leaf(path))
                outer = service.read_file(session, path, side)
                content, metadata = read_archive_member_details(outer, outer_name, member)
                inspection = inspect_file_data(
                    content, metadata, member, read_only=True,
                    size=len(content), digest=sha256_bytes(content),
                )
                report_path = member
            else:
                content = metadata = None
                inspection = inspect_editable_file(service, session, path, side)
                report_path = path
            if inspection["view"] == "basic":
                kind = "AmigaBASIC"
                findings = analyse_basic(inspection["text"])
                diagnostics = []
            elif inspection["view"] in {"disassembly", "hex"}:
                disassembly = (disassemble_file_data(
                    content, metadata, session, member,
                    size=len(content), digest=sha256_bytes(content),
                ) if member else disassemble_file(service, session, path, side))
                kind = str(disassembly.get("architecture") or "machine code").upper()
                findings = analyse_disassembly(disassembly)
                diagnostics = disassembly_diagnostics(disassembly)
            else:
                raise DiskError("This analyser supports tokenised AmigaBASIC and machine-code files. Scripts and plain text do not contain executable game state to trace.")
            parsed = parse_distribution_filename(report_path.rsplit("/", 1)[-1].rsplit(".", 1)[-1])
            title = parsed.get("title") or report_path.rsplit("/", 1)[-1].rsplit(".", 1)[-1]
            profile = session.hardware_profile or {}
            machine = str(profile.get("name") or profile.get("machine") or session.target_hardware or "")
            matches = lookup_online(title) if online else []
            return jsonify(cheat_report(
                path=report_path, kind=kind, findings=findings, title=title,
                machine=machine, matches=matches, diagnostics=diagnostics,
            ))

    @blueprint.get("/api/images/<image_id>/cheat-patch/context")
    def cheat_patch_context(image_id):
        session = service.get(image_id)
        path = str(request.args.get("path") or "")
        if not path or request.args.get("member"):
            raise DiskError("Guarded cheat patches currently require one file stored directly in an image.")
        apply_partition(service, session, request.args.get("partition"))
        side = optional_int(request.args.get("side"))
        try:
            offset = int(request.args.get("offset", "-1"))
            length = max(1, min(32, int(request.args.get("length", "1"))))
        except ValueError as exc:
            raise DiskError("The patch offset or byte length is invalid.") from exc
        original = service.read_file(session, path, side)
        if offset < 0 or offset + length > len(original):
            raise DiskError("The selected candidate is outside the current file.")
        return jsonify(
            path=path, offset=offset, length=length,
            originalHex=original[offset:offset + length].hex().upper(),
            sourceSha256=sha256_bytes(original), sourceSize=len(original),
            hardwareProfile=session.hardware_profile or {},
        )

    @blueprint.post("/api/images/<image_id>/cheat-patch/preview")
    @request_effect("read-only", "validating a guarded cheat patch")
    def preview_cheat_patch(image_id):
        session = service.get(image_id)
        data = payload()
        path = str(data.get("path") or "")
        if not path or data.get("member"):
            raise DiskError("Guarded cheat patches currently require one file stored directly in an image.")
        original = service.read_file(
            session, path, optional_int(data.get("side")),
        )
        try:
            patch = build_guarded_cheat_patch(original, data)
        except CheatPatchError as exc:
            raise DiskError(str(exc)) from exc
        return jsonify(patch=patch)

    @blueprint.post("/api/images/<image_id>/cheat-patch/apply")
    @image_mutation("applying an exact-hash guarded cheat patch")
    def apply_cheat_patch(image_id):
        session = service.get(image_id)
        data = payload()
        patch = dict(data.get("patch") or {})
        path = str(data.get("path") or patch.get("path") or "")
        if not path or data.get("member"):
            raise DiskError("Guarded cheat patches currently require one file stored directly in an image.")
        apply_partition(service, session, data.get("partition"))
        side = optional_int(data.get("side"))
        original = service.read_file(session, path, side)
        try:
            replacement = apply_guarded_cheat_patch(original, patch)
        except CheatPatchError as exc:
            raise DiskError(str(exc)) from exc
        image = replace_file_bytes(
            service, session, path, side, replacement, sha256_bytes(original),
        )
        project = service.editor_project(session, path, side)
        project["tests"] = [*project.get("tests", []), {
            "kind": "guarded-cheat-patch",
            "time": datetime.now(timezone.utc).isoformat(),
            "patchId": patch.get("id"),
            "sourceSha256": patch.get("sourceSha256"),
            "resultSha256": sha256_bytes(replacement),
            "summary": patch.get("title"),
            "rollback": patch.get("rollback"),
        }][-100:]
        project = service.save_editor_project(session, path, side, project)
        return jsonify(image=image, patch=patch, project=project)

    @blueprint.get("/api/images/<image_id>/inspect/search")
    def search_inspected_files(image_id):
        session = service.get(image_id)
        operation_id = request.args.get("operationId")
        with operations.tracked(
            operation_id,
            "Searching image catalogue and file content",
            "Workspace image search complete",
        ) as progress:
            return jsonify(search_image_files(
                service, session, str(request.args.get("query") or ""), optional_int(request.args.get("side")),
                str(request.args.get("root") or "$"),
                str(request.args.get("allPartitions") or "false").lower() in {"1", "true", "yes"},
                progress,
                workspace_metadata_records(service, session),
            ))

    @blueprint.put("/api/images/<image_id>/inspect")
    @image_mutation("editing a BASIC or text file")
    def save_inspected_text(image_id):
        data = payload()
        session = service.get(image_id)
        path = str(data.get("path") or "")
        apply_partition(service, session, data.get("partition"))
        side = optional_int(data.get("side"))
        current = inspect_editable_file(service, session, path, side)
        if not current["editable"] or current["readOnly"]:
            raise DiskError("This file cannot be edited safely in the current image.")
        if data.get("newName") not in (None, ""):
            image, saved_path = save_editor_text_as(
                service, session, path, side, str(data.get("newName") or ""),
                str(data.get("text") or ""), bool(current["tokenisedBasic"]),
                str(data.get("sha256") or ""),
            )
            return jsonify(
                image=image,
                path=saved_path,
                inspection=inspect_editable_file(service, session, saved_path, side),
            )
        image = save_editor_text(
            service, session, path, side, str(data.get("text") or ""),
            bool(current["tokenisedBasic"]), str(data.get("sha256") or ""),
        )
        return jsonify(image=image, path=path, inspection=inspect_editable_file(service, session, path, side))

    @blueprint.post("/api/images/<image_id>/inspect/dms-rebuild-preview")
    @request_effect("read-only", "proving a DMS DMS track rebuild")
    def preview_dms_member_rebuild(image_id):
        data = payload()
        session = service.get(image_id)
        if session.kind != "dms":
            raise DiskError("This structural comparison is only used by DMS archive projects.")
        path = str(data.get("path") or "")
        apply_partition(service, session, data.get("partition"))
        side = optional_int(data.get("side"))
        current = inspect_editable_file(service, session, path, side)
        if not current["editable"] or current["readOnly"]:
            raise DiskError("This DMS member does not have a complete reconstruction proof.")
        if str(data.get("sha256") or "") != current["sha256"]:
            raise DiskError("The DMS member changed after the editor opened it. Reopen it before reviewing the rebuild.")
        original = service.read_file(session, path, side)
        replacement = encode_editor_replacement(
            original, str(data.get("text") or ""), bool(current["tokenisedBasic"]),
        )
        return jsonify(service.preview_dms_member_replacement(session, path, replacement))

    @blueprint.put("/api/images/<image_id>/inspect/properties")
    @image_mutation("editing file properties")
    def save_inspected_properties(image_id):
        data = payload()
        session = service.get(image_id)
        path = str(data.get("path") or "")
        apply_partition(service, session, data.get("partition"))
        side = optional_int(data.get("side"))
        if not path or session.kind in {"rom", "dms"} or session.hfe_read_only:
            raise DiskError("This file's catalogue properties cannot be changed in the current image.")
        image = update_file_properties(
            service, session, path, side, str(data.get("sha256") or ""),
            protection=protection_field(data.get("protection")) or "",
            comment=str(data.get("comment") or ""),
            filetype=str(data.get("filetype") or ""),
            writable=bool(data.get("writable", True)),
        )
        return jsonify(image=image, inspection=inspect_editable_file(service, session, path, side))

    @blueprint.post("/api/images/<image_id>/inspect/basic/renumber")
    @request_effect("read-only", "previewing a BASIC renumber operation")
    def renumber_basic(image_id):
        service.get(image_id)
        data = payload()
        try:
            start = int(data.get("start", 10))
            step = int(data.get("step", 10))
        except (TypeError, ValueError) as exc:
            raise DiskError("The BASIC start and step must be whole numbers.") from exc
        return jsonify(prepare_basic_source(str(data.get("text") or ""), start, step))

    @blueprint.post("/api/images/<image_id>/inspect/basic/normalise")
    @request_effect("read-only", "normalising BASIC source for review")
    def normalise_basic(image_id):
        service.get(image_id)
        return jsonify(normalise_basic_source(str(payload().get("text") or "")))

    @blueprint.post("/api/images/<image_id>/inspect/basic/verify")
    @request_effect("read-only", "verifying BASIC source")
    def verify_basic(image_id):
        service.get(image_id)
        data = payload()
        return jsonify(verify_basic_source(str(data.get("text") or ""), str(data.get("baseline") or "")))

    @blueprint.get("/api/images/<image_id>/editor-project")
    def editor_project(image_id):
        session = service.get(image_id)
        path = str(request.args.get("path") or "")
        if not path:
            raise DiskError("Choose a file project to open.")
        return jsonify(project=service.editor_project(
            session, path, optional_int(request.args.get("side")),
        ))

    @blueprint.put("/api/images/<image_id>/editor-project")
    @image_mutation("editing image project metadata")
    def save_editor_project(image_id):
        session = service.get(image_id)
        data = payload()
        path = str(data.get("path") or "")
        if not path:
            raise DiskError("Choose a file project to save.")
        project = service.save_editor_project(
            session, path, optional_int(data.get("side")),
            dict(data.get("project") or {}),
        )
        return jsonify(project=project)

    @blueprint.get("/api/images/<image_id>/editor-emulator")
    def editor_emulator_status(image_id):
        session = service.get(image_id)
        configured = requested_emulator_session(session, status_request_data())
        status = emulator_status(configured)
        parent_mountable = False
        parent_message = ""
        try:
            command, _cwd = selected_media_probe(session, configured)
            status["command"] = " ".join(command)
            parent_mountable = True
        except ValueError as exc:
            status["command"] = ""
            parent_message = str(exc)
        is_basic = str(request.args.get("basic") or "false").lower() in {"1", "true", "yes"}
        isolated_basic = bool(is_basic and status["machine"] != "a4000" and status["available"])
        if not parent_mountable and not isolated_basic:
            status["available"] = False
            status["message"] = parent_message or status["message"]
        return jsonify(
            **status, hardware=configured.target_hardware,
            parentMountable=parent_mountable, parentMessage=parent_message,
            isolatedBasic=isolated_basic,
            mediaTarget=(
                "whole-drive" if getattr(session, "kind", "") == "hdf" else "image"
            ),
            targetLabel=(
                f"complete hard drive · {getattr(session, 'name', 'drive.hdf')}"
                if getattr(session, "kind", "") == "hdf"
                else getattr(session, "name", "Current image")
            ),
        )

    @blueprint.post("/api/images/<image_id>/drive-sandbox")
    @request_effect("external", "booting a hard drive in an emulator sandbox")
    def drive_sandbox(image_id):
        session = service.get(image_id)
        if session.kind != "hdf":
            raise DiskError("The isolated sandbox requires a complete hard-drive image.")
        data = payload()
        configured = requested_emulator_session(session, data)
        source_hash = sha256_path(session.path)
        earlier = [
            row
            for row in service.editor_project(session, "drive", None).get("tests", [])
            if row.get("kind") == "drive-sandbox"
            and row.get("sourceSha256") == source_hash
        ]
        try:
            with whole_drive_media(session, configured) as (launch, media):
                launch.hardware_profile = dict(launch.hardware_profile or {})
                launch.hardware_profile["emulatorBoot"] = "boot"
                arguments, cwd = emulator_command(launch, media)
                evidence = capture_emulator_evidence(arguments, cwd)
        except (ValueError, FatMediaError, EmulatorEvidenceError) as exc:
            raise DiskError(f"The isolated capture could not complete: {exc}") from exc
        public_frames = evidence.pop("frames")
        frame_hashes = [frame["sha256"] for frame in public_frames]
        repeatable = any(row.get("frameHashes") == frame_hashes for row in earlier)
        try:
            partitions = service.list_partitions(session)
        except DiskError:
            partitions = []
        bootable = [
            str(partition.get("device") or "")
            for partition in partitions
            if partition.get("bootable")
        ]
        result = {
            **evidence,
            "time": datetime.now(timezone.utc).isoformat(),
            "kind": "drive-sandbox",
            "sourceSha256": source_hash,
            "emulator": configured_emulator(configured).label,
            "machine": str(configured.hardware_profile.get("machine") or ""),
            "frameHashes": frame_hashes,
            "repeatable": repeatable,
            "profileStatus": "repeatable-evidence" if repeatable else "first-capture",
            "bootablePartitions": bootable,
            "summary": (
                "The drive produced a captured display and responded to a key."
                if evidence.get("inputChangedDisplay")
                else "The drive produced a captured display; the key did not visibly change it."
            ),
        }
        project = record_editor_run(
            session, "drive", None, result, kind="drive-sandbox"
        )
        return jsonify(result={**result, "frames": public_frames}, project=project)

    @blueprint.post("/api/images/<image_id>/editor-emulator")
    @request_effect("external", "launching an editor document in an emulator")
    def editor_emulator_run(image_id):
        session = service.get(image_id)
        data = payload()
        configured = requested_emulator_session(session, data)
        path = str(data.get("path") or ("drive" if session.kind == "hdf" else ""))
        apply_partition(service, session, data.get("partition"))
        side = optional_int(data.get("side"))
        if bool(data.get("interactive")):
            try:
                arguments, launch = interactive_emulator.start(
                    launch_media(session, configured, data), debug=False,
                )
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                raise DiskError(f"The interactive emulator could not start: {exc}") from exc
            emulator = configured_emulator(launch)
            result = {
                "time": datetime.now(timezone.utc).isoformat(), "command": arguments[0],
                "returnCode": 0, "bounded": False, "interactive": True,
                "emulator": emulator.label, "machine": str(launch.hardware_profile.get("machine") or ""),
                "launchMode": str(data.get("mode") or "parent-auto"),
                "summary": (
                    f"{emulator.label} is running in its native desktop window."
                    if runtime.kind == "desktop"
                    else f"{emulator.label} is running in the browser display."
                ),
                "stdout": "", "stderr": "",
                "displayMode": "native" if runtime.kind == "desktop" else "browser",
                **({} if runtime.kind == "desktop" else {"viewerPort": 8668}),
            }
            project = record_editor_run(session, path, side, result)
            return jsonify(result=result, project=project)
        try:
            with launch_media(session, configured, data) as (launch, media):
                arguments, cwd = emulator_command(launch, media)
                completed = run_emulator_process(arguments, cwd, 30)
        except ValueError as exc:
            raise DiskError(str(exc)) from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DiskError(f"The managed emulator test could not complete: {exc}") from exc
        bounded = completed.returncode == 124
        emulator = configured_emulator(configured)
        mode = str(data.get("mode") or "parent-auto")
        result = {
            "time": datetime.now(timezone.utc).isoformat(),
            "command": arguments[0], "returnCode": 0 if bounded else completed.returncode,
            "bounded": bounded,
            "emulator": emulator.label, "machine": str(configured.hardware_profile.get("machine") or ""),
            "launchMode": mode,
            "summary": (
                f"{emulator.label} completed its expected managed test window."
                if bounded else f"{emulator.label} exited with return code {completed.returncode}."
            ),
            "stdout": clean_emulator_output(completed.stdout)[-20000:],
            "stderr": clean_emulator_output(completed.stderr)[-20000:],
        }
        project = record_editor_run(session, path, side, result)
        return jsonify(result=result, project=project)

    @blueprint.delete("/api/images/<image_id>/editor-emulator")
    @request_effect("external", "stopping the managed emulator")
    def editor_emulator_stop(image_id):
        service.get(image_id)
        interactive_emulator.stop()
        return jsonify(stopped=True)

    @blueprint.post("/api/images/<image_id>/install/emulator")
    @request_effect("external", "booting a drive with a disc to run its own installer")
    def install_under_emulation(image_id):
        """Boot this hard drive with a title's discs already in the drives.

        Some software can only be installed by its own installer: it asks
        which drawer, which language, which screen mode, and no tool can
        answer those for somebody else. So this mode stops trying. It puts the
        machine in the state the installer needs and hands the operator the
        keyboard.

        The drive is handed over as a whole-drive image, the same way a
        hard-drive launch already works, so the installer sees the partitions
        and the Workbench the operator actually built.
        """
        session = service.get(image_id)
        if session.kind != "hdf" and not service.summary(session).get("hardDisk"):
            raise DiskError("Running an installer needs a hard-drive image to install onto.")
        data = payload()
        configured = requested_emulator_session(session, data)
        discs = [service.get(str(item)) for item in (data.get("discs") or [])]
        if not discs:
            raise DiskError("Choose at least one disc for the installer to read.")
        if len(discs) > MAXIMUM_FLOPPY_DRIVES:
            raise DiskError(
                f"An Amiga has {MAXIMUM_FLOPPY_DRIVES} floppy drives. "
                f"Insert up to {MAXIMUM_FLOPPY_DRIVES} discs and swap the rest as the installer asks."
            )
        launch = copy(configured)
        launch.hardware_profile = dict(configured.hardware_profile or {})
        # The installer is on the hard drive's Workbench, not on the disc, so
        # the machine must boot the drive rather than the disc in DF0:.
        launch.hardware_profile["emulatorBoot"] = "boot"
        try:
            arguments, started = interactive_emulator.start(
                whole_drive_media(session, launch),
                debug=False,
                floppies=[disc.path for disc in discs],
            )
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise DiskError(f"The emulator could not start: {exc}") from exc
        emulator = configured_emulator(started)
        return jsonify(result={
            "time": datetime.now(timezone.utc).isoformat(),
            "command": arguments[0],
            "interactive": True,
            "emulator": emulator.label,
            "machine": str(started.hardware_profile.get("machine") or ""),
            "discs": [disc.name for disc in discs],
            "summary": (
                f"{emulator.label} is running with {len(discs)} disc"
                f"{'' if len(discs) == 1 else 's'} inserted. "
                "Run the title's installer from the Workbench and point it at this drive."
            ),
            "displayMode": "native" if runtime.kind == "desktop" else "browser",
            **({} if runtime.kind == "desktop" else {"viewerPort": 8668}),
        })

    @blueprint.get("/api/images/<image_id>/editor-assembler")
    def editor_assembler_status(image_id):
        service.get(image_id)
        command = os.environ.get("AMIGA_FILE_ASSEMBLER_COMMAND", "").strip()
        available = bool(command and "{source}" in command and "{output}" in command)
        return jsonify(
            available=available,
            message=(
                "Configured by AMIGA_FILE_ASSEMBLER_COMMAND."
                if available
                else "Set AMIGA_FILE_ASSEMBLER_COMMAND with {source} and {output} placeholders."
            ),
        )

    @blueprint.post("/api/images/<image_id>/editor-assembler")
    @request_effect("external", "assembling an editor document")
    def editor_assembler_run(image_id):
        session = service.get(image_id)
        data = payload()
        path = str(data.get("path") or "")
        apply_partition(service, session, data.get("partition"))
        side = optional_int(data.get("side"))
        source = str(data.get("source") or "")
        architecture = str(data.get("architecture") or "68000").casefold()
        origin = str(data.get("origin") or "0")
        template = os.environ.get("AMIGA_FILE_ASSEMBLER_COMMAND", "").strip()
        if not template or "{source}" not in template or "{output}" not in template:
            raise DiskError("No compatible external assembler command is configured.")
        if len(source.encode("utf-8")) > 4 * 1024 * 1024:
            raise DiskError("Assembly source is limited to 4 MiB per operation.")
        current = service.read_file(session, path, side)
        expected = str(data.get("sha256") or "")
        if sha256_bytes(current) != expected:
            raise DiskError("The binary changed after the disassembly opened. Reopen it before assembling.")
        with tempfile.TemporaryDirectory(dir=service.work_dir, prefix="assemble-file-") as folder:
            source_path = Path(folder) / "source.asm"
            output_path = Path(folder) / "output.bin"
            source_path.write_text(source, encoding="utf-8")
            replacements = {
                "{source}": str(source_path), "{output}": str(output_path),
                "{origin}": origin, "{architecture}": architecture,
            }
            arguments = shlex.split(template)
            for key, value in replacements.items():
                arguments = [part.replace(key, value) for part in arguments]
            try:
                completed = subprocess.run(arguments, capture_output=True, text=True, timeout=60, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise DiskError(f"The assembler could not complete: {exc}") from exc
            if completed.returncode or not output_path.is_file():
                detail = (completed.stderr or completed.stdout or "No output file was produced.")[-20000:]
                raise DiskError(f"The assembler rejected the source: {detail}")
            assembled = output_path.read_bytes()
        if not assembled or len(assembled) > 16 * 1024 * 1024:
            raise DiskError("The assembler output is empty or exceeds the safe 16 MiB limit.")
        changed = sum(left != right for left, right in zip(current, assembled)) + abs(len(current) - len(assembled))
        image = replace_file_bytes(service, session, path, side, assembled, expected)
        return jsonify(
            image=image,
            result={
                "size": len(assembled), "changedBytes": changed,
                "sha256": sha256_bytes(assembled),
                "stdout": completed.stdout[-20000:], "stderr": completed.stderr[-20000:],
            },
        )

    @blueprint.get("/api/images/<image_id>/editor-debugger")
    def editor_debugger_status(image_id):
        session = service.get(image_id)
        configured = requested_emulator_session(session, status_request_data())
        status = emulator_status(configured)
        parent_mountable = False
        parent_message = ""
        apply_partition(service, session, request.args.get("partition"))
        try:
            command, _cwd = selected_media_probe(session, configured, debug=True)
            parent_mountable = True
        except ValueError as exc:
            command = []
            parent_message = str(exc)
        is_basic = str(request.args.get("basic") or "false").lower() in {"1", "true", "yes"}
        isolated_basic = bool(is_basic and status["machine"] != "a4000" and status["available"])
        available = bool(status["available"] and (parent_mountable or isolated_basic))
        return jsonify(
            available=available,
            hardware=configured.target_hardware,
            command=" ".join(command), configuredBy="managed workbench profile",
            message=(f"{status['label']} provides the managed debugger for this target." if available else parent_message or status["message"]),
            label=status["label"], machine=status["machine"],
            parentMountable=parent_mountable, parentMessage=parent_message,
            isolatedBasic=isolated_basic, actions=["launch"] if available else [],
            mediaTarget=(
                "whole-drive" if getattr(session, "kind", "") == "hdf" else "image"
            ),
            targetLabel=(
                f"complete hard drive · {getattr(session, 'name', 'drive.hdf')}"
                if getattr(session, "kind", "") == "hdf"
                else getattr(session, "name", "Current image")
            ),
        )

    @blueprint.post("/api/images/<image_id>/editor-debugger")
    @request_effect("external", "running the managed debugger")
    def editor_debugger_run(image_id):
        session = service.get(image_id)
        data = payload()
        configured = requested_emulator_session(session, data)
        path = str(data.get("path") or ("drive" if session.kind == "hdf" else ""))
        apply_partition(service, session, data.get("partition"))
        side = optional_int(data.get("side"))
        action = str(data.get("action") or "launch").strip().lower()
        if action != "launch":
            raise DiskError("Start the managed debugger before using its native step, register and memory controls.")
        expression = str(data.get("expression") or "").strip()[:500]
        if bool(data.get("interactive")):
            try:
                arguments, launch = interactive_emulator.start(
                    launch_media(session, configured, data), debug=True,
                )
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                raise DiskError(f"The interactive debugger could not start: {exc}") from exc
            emulator = configured_emulator(launch)
            result = {
                "time": datetime.now(timezone.utc).isoformat(), "command": arguments[0],
                "returnCode": 0, "bounded": False, "interactive": True,
                "emulator": emulator.label, "machine": str(launch.hardware_profile.get("machine") or ""),
                "launchMode": str(data.get("mode") or "parent-auto"),
                "summary": (
                    f"{emulator.label} debugger is running in its native desktop window."
                    if runtime.kind == "desktop"
                    else f"{emulator.label} debugger is running in the browser display."
                ),
                "stdout": "", "stderr": "",
                "displayMode": "native" if runtime.kind == "desktop" else "browser",
                **({} if runtime.kind == "desktop" else {"viewerPort": 8668}),
                "breakpoint": str(data.get("breakpoint") or ""), "action": action,
                "expression": expression, "kind": "debugger",
            }
            project = record_editor_run(session, path, side, result)
            return jsonify(result=result, project=project)
        try:
            with launch_media(session, configured, data) as (launch, media):
                arguments, cwd = emulator_command(launch, media, debug=True)
                completed = run_emulator_process(arguments, cwd, 120)
        except ValueError as exc:
            raise DiskError(str(exc)) from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DiskError(f"The managed debugger session could not complete: {exc}") from exc
        bounded = completed.returncode == 124
        emulator = configured_emulator(configured)
        mode = str(data.get("mode") or "parent-auto")
        result = {
            "time": datetime.now(timezone.utc).isoformat(), "command": arguments[0],
            "returnCode": 0 if bounded else completed.returncode, "bounded": bounded,
            "emulator": emulator.label, "machine": str(configured.hardware_profile.get("machine") or ""),
            "launchMode": mode,
            "summary": (
                f"{emulator.label} completed its expected managed debugger window."
                if bounded else f"{emulator.label} debugger exited with return code {completed.returncode}."
            ),
            "stdout": clean_emulator_output(completed.stdout)[-50000:],
            "stderr": clean_emulator_output(completed.stderr)[-50000:], "breakpoint": str(data.get("breakpoint") or ""),
            "action": action, "expression": expression,
        }
        project = record_editor_run(
            session, path, side, result, kind="debugger"
        )
        return jsonify(result=result, project=project)

    @blueprint.post("/api/images/<image_id>/inspect/basic/pack")
    @request_effect("read-only", "previewing packed BASIC source")
    def pack_basic(image_id):
        service.get(image_id)
        data = payload()
        runs = data.get("runs")
        if not isinstance(runs, list):
            raise DiskError("BASIC packing requires a list of safe statement runs.")
        return jsonify(pack_basic_lines(runs))

    @blueprint.get("/api/images/<image_id>/disassembly")
    def inspect_disassembly(image_id):
        session = service.get(image_id)
        path = str(request.args.get("path") or "")
        if not path:
            raise DiskError("Choose a file to disassemble.")
        try:
            origin = int(str(request.args.get("origin")), 0) if request.args.get("origin") not in (None, "") else None
            start = int(str(request.args.get("start") or "0"), 0)
            length = int(str(request.args.get("length")), 0) if request.args.get("length") not in (None, "") else None
        except ValueError as exc:
            raise DiskError("Origin, offset and length must be valid decimal or 0x-prefixed numbers.") from exc
        return jsonify(disassemble_file(
            service, session, path,
            optional_int(request.args.get("side")), str(request.args.get("architecture") or "auto"),
            origin, start, length,
        ))

    return blueprint
