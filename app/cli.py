"""Supported automation interface for Amiga File Forge."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .analysis_service import build_manifest, preflight_report
from .disk_service import DiskError, DiskService
from .headless import (
    BLANK_FORMATS,
    RESULT_FORMAT,
    RESULT_VERSION,
    apply_patch,
    compare,
    create_recipe,
    load_recipe,
    open_image,
    open_image_pair,
    progress_to_stderr,
    save_image,
    source_identity,
    verify_identity,
    write_patch,
)
from .image_diff import manifest_fingerprint


EXIT_OK = 0
EXIT_USAGE = 2
EXIT_VALIDATION = 3
EXIT_INPUT = 4
EXIT_IDENTITY = 5
EXIT_OPERATION = 6


class IdentityError(DiskError):
    """A deterministic recipe source no longer matches its recorded bytes."""


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep usage failures as machine-readable as operation failures."""

    def error(self, message: str) -> None:
        document = _result(
            "usage",
            "usage-error",
            EXIT_USAGE,
            {"error": message, "errorType": "ArgumentError"},
        )
        sys.stdout.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
        self.print_usage(sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _result(command: str, status: str, exit_code: int, result=None, *, dry_run=False) -> dict:
    return {
        "format": RESULT_FORMAT,
        "version": RESULT_VERSION,
        "command": command,
        "status": status,
        "exitCode": exit_code,
        "dryRun": bool(dry_run),
        "result": result or {},
    }


def _write_json(document: dict, destination: str | None = None, *, force=False) -> None:
    text = json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if destination:
        path = Path(destination)
        if path.exists() and not force:
            raise FileExistsError(f"Output already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def _image_arguments(parser: argparse.ArgumentParser, name: str = "image") -> None:
    parser.add_argument(name, type=Path)
    parser.add_argument("--descriptor", type=Path, help="Matching Hardfile GEO descriptor")
    parser.add_argument("--target-hardware", default="auto")
    parser.add_argument("--force-kind", choices=("rom",))


def _output_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--force", action="store_true", help="Replace an existing output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--recipe-out", type=Path, help="Record this completed workflow as a versioned recipe")


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="amiga-file-forge")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create a blank image")
    create.add_argument("--format", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--capacity")
    create.add_argument("--target-hardware", default="auto")
    create.add_argument("--bank-size", type=int, default=16384)
    create.add_argument("--total-size", type=int)
    create.add_argument("--platform", default="kickstart")
    create.add_argument("--layout", default="linear")
    create.add_argument("--template", default="blank")
    _output_arguments(create)

    manifest = sub.add_parser("manifest", help="Create a deterministic image manifest")
    _image_arguments(manifest)
    manifest.add_argument("--output", type=Path)
    manifest.add_argument("--force", action="store_true")

    validate = sub.add_parser("validate", help="Run structural validation")
    _image_arguments(validate)
    validate.add_argument("--partition", type=int)

    preflight = sub.add_parser("preflight", help="Build a versioned compatibility report")
    _image_arguments(preflight)
    preflight.add_argument("--changes", required=True, type=Path, help="Proposed changes as a JSON array")
    preflight.add_argument("--operation", default="review")
    preflight.add_argument("--source-kind")
    preflight.add_argument("--target-kind")
    preflight.add_argument("--output", type=Path)
    preflight.add_argument("--force", action="store_true")

    save = sub.add_parser("save", help="Finalise and save a hardware-ready image")
    _image_arguments(save)
    _output_arguments(save)

    import_file = sub.add_parser("import-file", help="Import one host file")
    _image_arguments(import_file)
    import_file.add_argument("source", type=Path)
    import_file.add_argument("--destination", required=True)
    import_file.add_argument("--partition", type=int)
    import_file.add_argument("--side", type=int)
    import_file.add_argument(
        "--protection",
        help="Protection, as the eight letters List prints or as &hex.",
    )
    import_file.add_argument("--comment", help="File comment, up to 79 characters.")
    import_file.add_argument("--filetype", help="Workbench icon type.")
    _output_arguments(import_file)

    convert = sub.add_parser("convert", help="Convert a DMS archive to ADF or ADZ")
    _image_arguments(convert)
    convert.add_argument("--format", choices=("adf", "adz"), required=True)
    _output_arguments(convert)

    compact = sub.add_parser("compact", help="Compact a writable filesystem")
    _image_arguments(compact)
    compact.add_argument("--partition", type=int)
    compact.add_argument("--order")
    _output_arguments(compact)


    comparison = sub.add_parser("compare", help="Compare two images logically")
    _image_arguments(comparison, "base")
    comparison.add_argument("candidate", type=Path)
    comparison.add_argument("--candidate-descriptor", type=Path)
    comparison.add_argument("--output", type=Path)
    comparison.add_argument("--force", action="store_true")

    patch_create = sub.add_parser("patch-create", help="Create a guarded image patch")
    _image_arguments(patch_create, "base")
    patch_create.add_argument("candidate", type=Path)
    patch_create.add_argument("--candidate-descriptor", type=Path)
    patch_create.add_argument("--output", required=True, type=Path)
    patch_create.add_argument("--force", action="store_true")
    patch_create.add_argument("--dry-run", action="store_true")

    patch_apply = sub.add_parser("patch-apply", help="Verify and apply a guarded image patch")
    _image_arguments(patch_apply)
    patch_apply.add_argument("patch", type=Path)
    _output_arguments(patch_apply)

    recipe = sub.add_parser("recipe-run", help="Verify and execute a versioned deterministic recipe")
    recipe.add_argument("recipe", type=Path)
    recipe.add_argument("--source", action="append", default=[], metavar="ALIAS=PATH")
    recipe.add_argument("--descriptor", action="append", default=[], metavar="ALIAS=PATH")
    recipe.add_argument("--output", type=Path)
    recipe.add_argument("--force", action="store_true")
    recipe.add_argument("--dry-run", action="store_true")
    return parser


def _select_partition(service, session, args) -> None:
    """Point a hard-drive session at the partition the command names."""
    partition = getattr(args, "partition", None)
    if partition is not None and session.kind == "hdf":
        service.select_partition(session, int(partition))


def _open_kwargs(args) -> dict:
    return {
        "descriptor": getattr(args, "descriptor", None),
        "target_hardware": getattr(args, "target_hardware", "auto"),
        "force_kind": getattr(args, "force_kind", None),
    }


def _recorded_open_context(args) -> dict:
    """Retain image interpretation choices required for recipe replay."""
    return {
        "targetHardware": getattr(args, "target_hardware", "auto"),
        "forceKind": getattr(args, "force_kind", None),
    }


def _record_recipe(args, source: dict, actions: list[dict], outputs: list[dict]) -> None:
    if not getattr(args, "recipe_out", None):
        return
    document = create_recipe(
        f"{args.command} workflow",
        source,
        actions,
        {"path": str(args.output), "files": outputs},
    )
    _write_json(document, str(args.recipe_out), force=args.force)


def _validate_declared_outputs(args) -> None:
    """Reject collisions before a long operation or its first output write."""
    targets = [
        Path(value)
        for value in (getattr(args, "output", None), getattr(args, "recipe_out", None))
        if value
    ]
    resolved = [target.resolve() for target in targets]
    if len(set(resolved)) != len(resolved):
        raise DiskError("The image/report output and recipe output must be different files.")
    inputs = [
        value.resolve()
        for name in (
            "image", "base", "candidate", "source", "patch", "changes",
            "recipe", "descriptor", "candidate_descriptor",
        )
        if isinstance((value := getattr(args, name, None)), Path)
    ]
    if any(target in inputs for target in resolved):
        raise DiskError("Choose output files different from every source, descriptor and recipe input.")
    if not getattr(args, "force", False):
        existing = next((target for target in targets if target.exists()), None)
        if existing:
            raise FileExistsError(f"Output already exists: {existing}")


def _create(args, progress) -> dict:
    if args.format not in BLANK_FORMATS:
        raise DiskError(f"Unknown blank image format: {args.format}")
    options = {
        "bankSize": args.bank_size,
        "totalSize": args.total_size or args.bank_size,
        "platform": args.platform,
        "layout": args.layout,
        "template": args.template,
    }
    plan = {
        "format": args.format,
        "title": args.title,
        "capacity": args.capacity,
        "targetHardware": args.target_hardware,
        "output": str(args.output),
        "options": options,
    }
    with tempfile.TemporaryDirectory(prefix="amiga-file-forge-cli-") as work:
        service = DiskService(work)
        session = service.create_blank(
            args.format, args.title, args.capacity, args.target_hardware, options
        )
        if args.dry_run:
            return {**plan, "image": service.summary(session), "validated": True}
        outputs = save_image(service, session, args.output, force=args.force, progress=progress)
        _record_recipe(
            args,
            {},
            [{"action": "create", **plan}],
            outputs,
        )
        return {**plan, "files": outputs}


def _manifest(args, progress) -> dict:
    with open_image(args.image, **_open_kwargs(args)) as (service, session):
        result = build_manifest(service, session, progress)
        result["fingerprint"] = manifest_fingerprint(result)
        if args.output:
            _write_json(result, str(args.output), force=args.force)
            return {"output": str(args.output), "fingerprint": result["fingerprint"]}
        return result


def _validate(args, _progress) -> dict:
    with open_image(args.image, **_open_kwargs(args)) as (service, session):
        _select_partition(service, session, args)
        return {"image": service.summary(session), "message": service.validate(session)}


def _preflight(args, _progress) -> dict:
    try:
        changes = json.loads(args.changes.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DiskError(f"The proposed changes are not readable JSON: {exc}") from exc
    if not isinstance(changes, list):
        raise DiskError("The proposed changes document must be a JSON array.")
    with open_image(args.image, **_open_kwargs(args)) as (service, session):
        report = preflight_report(service, session, {
            "operation": args.operation,
            "changes": changes,
            "sourceKind": args.source_kind or session.kind,
            "targetKind": args.target_kind or session.kind,
        })
    if args.output:
        _write_json(report, str(args.output), force=args.force)
        return {"output": str(args.output), "summary": report["summary"], "canProceed": report["canProceed"]}
    return report


def _save(args, progress) -> dict:
    with open_image(args.image, **_open_kwargs(args)) as (service, session):
        identity = source_identity(
            args.image,
            descriptor=args.descriptor,
            service=service,
            session=session,
        )
        action = {"action": "save", **_recorded_open_context(args)}
        if args.dry_run:
            service.prepare_download(session, progress)
            return {"image": identity, "action": action, "output": str(args.output), "validated": True}
        files = save_image(service, session, args.output, force=args.force, progress=progress)
        _record_recipe(args, {"image": identity}, [action], files)
        return {"image": identity, "files": files}


def _mutate(args, progress, action) -> dict:
    if not args.source.is_file():
        raise FileNotFoundError(f"Source file not found: {args.source}")
    with open_image(args.image, **_open_kwargs(args)) as (service, session):
        identity = source_identity(
            args.image,
            descriptor=args.descriptor,
            service=service,
            session=session,
        )
        _select_partition(service, session, args)
        payload_identity = source_identity(args.source)
        decision = {
            "action": action,
            **_recorded_open_context(args),
            "source": "payload",
            "destination": args.destination,
            "partition": args.partition,
            "side": args.side,
            "protection": args.protection,
            "comment": args.comment,
            "filetype": args.filetype,
        }
        compatibility = preflight_report(service, session, {
            "operation": "import-file",
            "sourceKind": "host",
            "targetKind": session.kind,
            "changes": [{
                "name": args.destination,
                "destination": args.destination,
                "source": str(args.source),
                "type": "file",
                "protection": args.protection,
                "comment": args.comment,
                "filetype": args.filetype,
            }],
        })
        blocking = next(
            (issue for issue in compatibility["issues"] if issue["severity"] == "error"),
            None,
        )
        if blocking:
            raise DiskError(f"Compatibility preflight failed: {blocking['message']}")
        service.put(
            session,
            args.destination,
            args.source,
            args.protection,
            args.comment,
            args.filetype,
            args.side,
        )
        if args.dry_run:
            return {"image": identity, "payload": payload_identity, "action": decision, "compatibility": compatibility, "output": str(args.output), "validated": True}
        outputs = save_image(service, session, args.output, force=args.force, progress=progress)
        _record_recipe(args, {"image": identity, "payload": payload_identity}, [decision], outputs)
        return {"action": decision, "compatibility": compatibility, "files": outputs}


def _convert(args, progress) -> dict:
    with open_image(args.image, **_open_kwargs(args)) as (service, session):
        identity = source_identity(args.image, descriptor=args.descriptor, service=service, session=session)
        action = {"action": "convert-dms", "format": args.format, **_recorded_open_context(args)}
        converted, files = service.convert_dms(session, args.format)
        if args.dry_run:
            return {"image": identity, "action": action, "output": str(args.output), "convertedFiles": files, "validated": True}
        outputs = save_image(service, converted, args.output, force=args.force, progress=progress)
        _record_recipe(args, {"image": identity}, [action], outputs)
        return {"convertedFiles": files, "files": outputs}


def _compact(args, progress) -> dict:
    with open_image(args.image, **_open_kwargs(args)) as (service, session):
        identity = source_identity(args.image, descriptor=args.descriptor, service=service, session=session)
        _select_partition(service, session, args)
        action = {"action": "compact", "partition": args.partition, "order": args.order, **_recorded_open_context(args)}
        service.compact(session, args.order)
        if args.dry_run:
            return {"image": identity, "action": action, "output": str(args.output), "validated": True}
        outputs = save_image(service, session, args.output, force=args.force, progress=progress)
        _record_recipe(args, {"image": identity}, [action], outputs)
        return {"action": action, "files": outputs}


def _two_images(args, callback):
    with open_image_pair(
        args.base,
        args.candidate,
        first_descriptor=args.descriptor,
        second_descriptor=args.candidate_descriptor,
        target_hardware=args.target_hardware,
        force_kind=args.force_kind,
    ) as (service, first, second):
        return callback((service, first), (service, second))


def _compare(args, progress) -> dict:
    result = _two_images(args, lambda first, second: compare(first[0], first[1], second[1], progress))
    if args.output:
        _write_json(result, str(args.output), force=args.force)
        return {"output": str(args.output), "summary": result["summary"]}
    return result


def _patch_create(args, progress) -> dict:
    if args.dry_run:
        with tempfile.TemporaryDirectory(prefix="amiga-file-forge-patch-dry-run-") as work:
            temporary = Path(work) / "review.affpatch.zip"
            document = _two_images(
                args,
                lambda first, second: write_patch(
                    first[0], first[1], second[1], temporary, progress
                ),
            )
        return {
            "output": str(args.output),
            "summary": document["summary"],
            "baseFingerprint": document["baseFingerprint"],
            "candidateFingerprint": document["candidateFingerprint"],
            "operations": len(document["operations"]),
            "validated": True,
        }
    document = _two_images(args, lambda first, second: write_patch(first[0], first[1], second[1], args.output, progress))
    return {"output": str(args.output), "summary": document["summary"], "operations": len(document["operations"])}


def _patch_apply(args, progress) -> dict:
    if not args.patch.is_file():
        raise FileNotFoundError(f"Patch not found: {args.patch}")
    with open_image(args.image, **_open_kwargs(args)) as (service, session):
        if args.dry_run:
            from .headless import inspect_patch
            return {"preflight": inspect_patch(service, session, args.patch, progress), "output": str(args.output)}
        applied = apply_patch(service, session, args.patch, progress)
        outputs = save_image(service, session, args.output, force=args.force, progress=progress)
        return {"patch": applied, "files": outputs}


def _aliases(values: list[str]) -> dict[str, Path]:
    result = {}
    for value in values:
        alias, separator, path = value.partition("=")
        if not separator or not alias or not path:
            raise DiskError("Recipe source overrides use ALIAS=PATH.")
        result[alias] = Path(path)
    return result


def _verify_recipe_outputs(document: dict, files: list[dict]) -> None:
    expected = document.get("output", {}).get("files")
    if not isinstance(expected, list) or len(expected) != len(files):
        raise IdentityError("The recipe has no complete expected output identity list.")
    for index, (wanted, actual) in enumerate(zip(expected, files), start=1):
        if not isinstance(wanted, dict) or any(
            wanted.get(field) != actual.get(field) for field in ("size", "sha256")
        ):
            raise IdentityError(
                f"Rebuilt output {index} does not match the recipe's expected size and SHA-256."
            )


def _recipe_run(args, progress) -> dict:
    document = load_recipe(args.recipe)
    paths = _aliases(args.source)
    descriptors = _aliases(args.descriptor)
    expected_aliases = set(document["sources"])
    if set(paths) != expected_aliases:
        extras = sorted(set(paths) - expected_aliases)
        if extras:
            raise DiskError(f"Recipe source alias is not declared and cannot be verified: {extras[0]}.")
    expected_descriptors = {
        alias for alias, identity in document["sources"].items()
        if identity.get("descriptor") is not None
    }
    if set(descriptors) != expected_descriptors:
        raise DiskError("Supply exactly the descriptor aliases recorded by this recipe.")
    for alias, expected in document["sources"].items():
        if alias not in paths:
            raise DiskError(f"Supply recipe source {alias} with --source {alias}=PATH.")
        try:
            verify_identity(paths[alias], expected, descriptors.get(alias))
        except DiskError as exc:
            raise IdentityError(str(exc)) from exc
    actions = document["actions"]
    if not actions:
        raise DiskError("The recipe contains no actions.")
    first = actions[0]
    output = args.output or Path(document["output"].get("path") or "")
    if not output:
        raise DiskError("Choose an output path for this recipe.")
    if Path(output).resolve() == args.recipe.resolve():
        raise DiskError("Choose a recipe output different from the recipe document.")
    if first.get("action") == "create":
        if len(actions) != 1:
            raise DiskError("Version 1 create recipes cannot contain additional actions.")
        with tempfile.TemporaryDirectory(prefix="amiga-file-forge-cli-") as work:
            service = DiskService(work)
            session = service.create_blank(
                first["format"], first["title"], first.get("capacity"),
                first.get("targetHardware", "auto"), first.get("options"),
            )
            if args.dry_run:
                service.prepare_download(session, progress)
                return {"verifiedSources": list(paths), "actions": actions, "output": str(output), "validated": True}
            files = save_image(
                service, session, output, force=args.force, progress=progress,
                verify=lambda generated: _verify_recipe_outputs(document, generated),
            )
            return {"verifiedSources": list(paths), "actions": actions, "files": files, "outputVerified": True}
    image_path = paths.get("image")
    if image_path is None:
        raise DiskError("A mutating recipe requires --source image=PATH.")
    output_resolved = Path(output).resolve()
    recipe_inputs = [Path(path).resolve() for path in paths.values()]
    recipe_inputs.extend(Path(path).resolve() for path in descriptors.values())
    if output_resolved in recipe_inputs:
        raise DiskError("Choose a recipe output different from every mapped source and descriptor.")
    with open_image(
        image_path,
        descriptor=descriptors.get("image"),
        target_hardware=str(first.get("targetHardware") or "auto"),
        force_kind=first.get("forceKind"),
    ) as (service, session):
        expected_fingerprint = document["sources"]["image"].get("logicalFingerprint")
        if expected_fingerprint:
            actual_fingerprint = manifest_fingerprint(build_manifest(service, session, progress))
            if actual_fingerprint != expected_fingerprint:
                raise IdentityError(
                    "Recipe image passed its physical hash but failed its logical fingerprint check."
                )
        for action in actions:
            kind = action.get("action")
            if kind == "import-file":
                service.put(
                    session,
                    action["destination"],
                    paths[action["source"]],
                    action.get("protection"),
                    action.get("comment"),
                    action.get("filetype"),
                    action.get("side"),
                )
            elif kind == "compact":
                service.compact(session, action.get("order"))
            elif kind == "save":
                continue
            elif kind == "convert-dms":
                session, _files = service.convert_dms(session, action["format"])
            elif kind == "apply-patch":
                apply_patch(service, session, paths[action["source"]], progress)
            else:
                raise DiskError(f"Recipe action is not supported: {kind}")
        if args.dry_run:
            service.prepare_download(session, progress)
            return {"verifiedSources": list(paths), "actions": actions, "output": str(output), "validated": True}
        files = save_image(
            service, session, output, force=args.force, progress=progress,
            verify=lambda generated: _verify_recipe_outputs(document, generated),
        )
        return {"verifiedSources": list(paths), "actions": actions, "files": files, "outputVerified": True}


COMMANDS = {
    "create": _create,
    "manifest": _manifest,
    "validate": _validate,
    "preflight": _preflight,
    "save": _save,
    "import-file": lambda args, progress: _mutate(args, progress, "import-file"),
    "convert": _convert,
    "compact": _compact,
    "compare": _compare,
    "patch-create": _patch_create,
    "patch-apply": _patch_apply,
    "recipe-run": _recipe_run,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    progress = progress_to_stderr(sys.stderr)
    dry_run = bool(getattr(args, "dry_run", False))
    try:
        _validate_declared_outputs(args)
        result = COMMANDS[args.command](args, progress)
    except IdentityError as exc:
        code, status = EXIT_IDENTITY, "identity-mismatch"
        result = {"error": str(exc), "errorType": type(exc).__name__}
    except FileNotFoundError as exc:
        code, status = EXIT_INPUT, "input-error"
        result = {"error": str(exc), "errorType": type(exc).__name__}
    except (DiskError, ValueError, KeyError, TypeError) as exc:
        code, status = EXIT_VALIDATION, "validation-failed"
        result = {"error": str(exc), "errorType": type(exc).__name__}
    except (OSError, RuntimeError) as exc:
        code, status = EXIT_OPERATION, "operation-failed"
        result = {"error": str(exc), "errorType": type(exc).__name__}
    except Exception as exc:  # Preserve the JSON contract for unforeseen tool failures.
        code, status = EXIT_OPERATION, "operation-failed"
        result = {"error": str(exc), "errorType": type(exc).__name__}
    else:
        code, status = EXIT_OK, "planned" if dry_run else "ok"
    _write_json(_result(args.command, status, code, result, dry_run=dry_run))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
