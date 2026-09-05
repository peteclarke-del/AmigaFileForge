from __future__ import annotations

import json
import shlex
import tempfile
import zipfile
from pathlib import Path

from .disk_service import DiskError, DiskService, ImageSession
from .headless import create_recipe, save_image, source_identity
from .image_patch import apply_patch_archive, write_patch_archive


WORKFLOW_RECIPE_NAME = "workflow.affrecipe.json"
WORKFLOW_PATCH_NAME = "changes.affpatch.zip"


def _checkpoint_source(
    service: DiskService, session: ImageSession
) -> tuple[Path, Path | None, dict]:
    """Return the oldest retained pre-change snapshot and its recorded state."""
    snapshot = service.oldest_checkpoint_snapshot(session)
    if snapshot is not None:
        image, descriptor, metadata = snapshot
        state = dict(metadata.get("state") or {})
        state["workflowCheckpointReason"] = str(
            metadata.get("reason") or metadata.get("name") or "retained checkpoint"
        )
        return image, descriptor, state
    if session.dirty:
        raise DiskError(
            "This edited session has no retained pre-change checkpoint, so an exact workflow "
            "recipe cannot be proved. Save the image, make a named checkpoint, then record "
            "subsequent changes for a deterministic export."
        )
    return session.path, session.descriptor_path, {
        "name": session.name,
        "descriptorName": session.descriptor_name,
        "targetHardware": session.target_hardware,
        "romBankSize": session.rom_bank_size,
        "romEraseByte": session.rom_erase_byte,
        "romPlatform": session.rom_platform,
        "romLayout": session.rom_layout,
    }


def _open_snapshot(
    service: DiskService,
    image: Path,
    descriptor: Path | None,
    state: dict,
) -> ImageSession:
    name = str(state.get("name") or image.name)
    descriptor_name = str(state.get("descriptorName") or "image.geo")
    with image.open("rb") as source:
        if descriptor is None:
            session = service.create_from_stream(
                name, source, target_hardware=str(state.get("targetHardware") or "auto")
            )
        else:
            with descriptor.open("rb") as companion:
                session = service.create_from_stream(
                    name,
                    source,
                    (descriptor_name, companion),
                    target_hardware=str(state.get("targetHardware") or "auto"),
                )
    if session.kind == "rom":
        session.rom_bank_size = int(state.get("romBankSize") or session.rom_bank_size)
        session.rom_erase_byte = int(state.get("romEraseByte", session.rom_erase_byte)) & 0xFF
        session.rom_platform = str(state.get("romPlatform") or session.rom_platform)
        session.rom_layout = str(state.get("romLayout") or session.rom_layout)
    return session


def _save_replay_outputs(
    service: DiskService,
    session: ImageSession,
    folder: Path,
    progress=None,
) -> list[dict]:
    rows = save_image(
        service, session, folder / session.name, force=True, progress=progress
    )
    return [
        {
            "name": Path(row["path"]).name,
            "size": int(row["size"]),
            "sha256": str(row["sha256"]),
        }
        for row in rows
    ]


def build_workflow_recipe_bundle(
    service: DiskService,
    session: ImageSession,
    destination: Path,
    progress=None,
) -> dict:
    """Package an exact retained base, guarded change set and expected output identity."""
    if session.kind == "dms":
        raise DiskError("Writable DMS workflow recipes are not available yet.")
    if session.hfe_original_path:
        raise DiskError(
            "HFE workflow recipes are not yet safe because replay would need to preserve the "
            "original track container as well as the decoded filesystem."
        )
    report = progress or (lambda _message, _current=None, _total=None: None)
    base_path, base_descriptor, state = _checkpoint_source(service, session)
    base = _open_snapshot(service, base_path, base_descriptor, state)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=service.work_dir, prefix="workflow-recipe-") as work:
            work_path = Path(work)
            patch_path = work_path / WORKFLOW_PATCH_NAME
            report("Building the guarded workflow change set", 0, None)
            patch = write_patch_archive(service, base, session, patch_path, report)
            replay = _open_snapshot(service, base.path, base.descriptor_path, state)
            try:
                report("Proving the guarded replay on a disposable image", 0, None)
                apply_patch_archive(service, replay, patch_path, report)
                report("Calculating the deterministic replay output identity", 0, None)
                output_files = _save_replay_outputs(
                    service, replay, work_path / "replay", report
                )
            finally:
                service.discard_session(replay)
            sources = {
                "image": source_identity(
                    base.path,
                    descriptor=base.descriptor_path,
                    service=service,
                    session=base,
                    progress=report,
                ),
                "changes": source_identity(patch_path, progress=report),
            }
            sources["image"]["name"] = str(state.get("name") or session.name)
            if base_descriptor and sources["image"].get("descriptor"):
                sources["image"]["descriptor"]["name"] = str(
                    state.get("descriptorName") or session.descriptor_name or "image.geo"
                )
            recipe = create_recipe(
                f"Rebuild {session.name}",
                sources,
                [{
                    "action": "apply-patch",
                    "source": "changes",
                    "targetHardware": str(state.get("targetHardware") or "auto"),
                }],
                {"path": session.name, "files": output_files},
            )
            recipe["decisions"] = {
                "targetHardware": session.target_hardware,
                "hardwareProfile": dict(session.hardware_profile),
                "acceptedCompatibilityReports": [
                    dict(report)
                    for report in session.compatibility_reports
                    if isinstance(report, dict)
                ],
                "baseCheckpointReason": str(
                    state.get("workflowCheckpointReason")
                    or "earliest retained pre-change state"
                ),
            }
            readme = _workflow_readme(session, recipe, patch)
            report("Writing the portable workflow bundle", 0, None)
            with zipfile.ZipFile(
                destination, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
            ) as archive:
                archive.writestr(WORKFLOW_RECIPE_NAME, json.dumps(recipe, indent=2) + "\n")
                archive.write(patch_path, WORKFLOW_PATCH_NAME)
                archive.writestr("README.md", readme)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        service.discard_session(base)
    report("Deterministic workflow bundle ready", 1, 1)
    return {
        "recipe": recipe,
        "patch": patch,
        "baseCheckpoint": {
            "name": sources["image"]["name"],
            "size": sources["image"]["size"],
            "sha256": sources["image"]["sha256"],
        },
    }


def _workflow_readme(session: ImageSession, recipe: dict, patch: dict) -> str:
    descriptor = recipe["sources"]["image"].get("descriptor")
    image_argument = shlex.quote(
        f"image=/path/to/{recipe['sources']['image']['name']}"
    )
    patch_argument = shlex.quote(f"changes={WORKFLOW_PATCH_NAME}")
    output_argument = shlex.quote(session.name)
    descriptor_option = (
        " \\\n  --descriptor " + shlex.quote(f"image=/path/to/{descriptor['name']}")
        if descriptor else ""
    )
    return f"""# Deterministic rebuild for {session.name}

This bundle records the earliest retained pre-change checkpoint, an exact guarded
filesystem patch and the expected SHA-256 of every saved output. It contains no
original image bytes. Supply the base image whose identity is listed below.

## Required base

- Filename: `{recipe['sources']['image']['name']}`
- Size: `{recipe['sources']['image']['size']}` bytes
- SHA-256: `{recipe['sources']['image']['sha256']}`
{f"- GEO filename: `{descriptor['name']}`\n- GEO size: `{descriptor['size']}` bytes\n- GEO SHA-256: `{descriptor['sha256']}`" if descriptor else ""}

## Rebuild

Extract this ZIP, then run from an Amiga File Forge checkout:

```sh
python -m app.cli recipe-run {WORKFLOW_RECIPE_NAME} \\
  --source {image_argument} \\
  --source {patch_argument}{descriptor_option} \\
  --output {output_argument}
```

The command refuses a different base, a changed patch or a rebuilt output that
does not match the recorded size and SHA-256. Use `--dry-run` first to verify all
inputs without publishing an output.

## Recorded result

- Guarded logical operations: {len(patch.get('operations') or [])}
{chr(10).join(f"- `{row['name']}`: {row['size']} bytes, SHA-256 `{row['sha256']}`" for row in recipe['output']['files'])}
"""


__all__ = ["build_workflow_recipe_bundle"]
