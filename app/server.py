from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

from flask import Flask, g, jsonify, request

from .app_update import Activity, AppUpdater, installed_target
from .disk_service import SESSION_OWNER, DiskError, DiskService
from .desktop_state import DesktopClientState
from .operations import OperationRegistry
from .routes.app_update import create_app_update_blueprint
from .routes.files import create_files_blueprint
from .routes.hex_editor import create_hex_editor_blueprint
from .routes.catalog import create_catalog_blueprint
from .routes.desktop import create_desktop_blueprint
from .routes.images import create_images_blueprint
from .routes.install import create_install_blueprint
from .routes.tools import InteractiveEmulator, create_tools_blueprint
from .routes.rom_tools import create_rom_tools_blueprint
from .routes.effects import mutation_for
from .platform_contract import runtime as platform_runtime


ROOT = Path(__file__).resolve().parent
WORK_DIR = Path(os.environ.get("AMIGA_FILE_FORGE_WORK_DIR", ROOT.parent / "work"))

# These controls apply equally to the browser and the private desktop WebKit
# host, and are asserted identical for both by the platform contract tests. The
# noVNC viewer is the only intentional cross-origin frame: it uses the current
# host on its dedicated port 8668.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'self'; object-src 'none'; "
        "frame-ancestors 'self'; form-action 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "font-src 'self' data:; connect-src 'self' ws: wss:; "
        "frame-src 'self' http://*:8668 https://*:8668; "
        "worker-src 'self' blob:"
    ),
}


#: Requests that act on an image as a whole file, refused for a live drive.
DRIVE_UNAVAILABLE = frozenset({
    "images.create_image_checkpoint",
    "images.restore_image_checkpoint",
    "images.undo_image_change",
    "images.download_image",
    "images.prepare_image_download",
    "images.convert_image",
    "images.export_image",
    "images.compact",
    "hex_editor.read_hex",
    "hex_editor.search_hex",
    "hex_editor.write_hex",
    "hex_editor.compare_hex",
    "rom_tools.rom_hardware_export",
    # Each of these copies the whole drive into a private snapshot first.
    "tools.package_deployment",
    "tools.create_workflow_recipe",
    "tools.editor_emulator_run",
    "tools.editor_debugger_run",
    "tools.install_under_emulation",
})


def create_app(
    *,
    work_dir: Path | str | None = None,
    platform: str = "web",
    desktop_token: str | None = None,
    desktop_owner: str | None = None,
    desktop_state_path: Path | str | None = None,
    desktop_update_dir: Path | str | None = None,
) -> Flask:
    application = Flask(__name__, static_folder="static", static_url_path="")
    runtime = platform_runtime(platform, desktop_token)
    if runtime.kind == "desktop" and not re.fullmatch(
        r"[A-Za-z0-9_-]{32,64}", desktop_owner or ""
    ):
        raise ValueError("The desktop host requires a stable private owner identity.")
    active_work_dir = Path(work_dir) if work_dir is not None else WORK_DIR
    max_upload_gib = max(1, int(os.environ.get("AMIGA_MAX_UPLOAD_GIB", "8")))
    application.config["MAX_CONTENT_LENGTH"] = max_upload_gib * 1024 * 1024 * 1024
    application.config["AMIGA_PLATFORM"] = runtime.public_contract()
    service = DiskService(active_work_dir)
    operations = OperationRegistry(active_work_dir / "operations.json")
    # Reading or writing a floppy disk counts as activity an application
    # update and a restart must wait for. Only the desktop host installs, and
    # only a package built for a release knows which system it was built for.
    media_activity = Activity()
    app_updater = AppUpdater(
        installed_target() if runtime.kind == "desktop" else None,
        folder=Path(desktop_update_dir) if desktop_update_dir else active_work_dir / "updates",
        activity=media_activity,
    )
    application.extensions["amiga_app_updater"] = app_updater

    @application.before_request
    def authenticate_desktop_host():
        if runtime.kind != "desktop":
            return None
        supplied = (
            request.headers.get("X-Amiga-Desktop-Token", "")
            or request.cookies.get("amiga_file_forge_desktop", "")
        )
        if not secrets.compare_digest(supplied, runtime.desktop_token or ""):
            return jsonify(error="This private desktop service rejected the request."), 403
        g.set_desktop_cookie = not secrets.compare_digest(
            request.cookies.get("amiga_file_forge_desktop", ""),
            runtime.desktop_token or "",
        )
        return None

    @application.before_request
    def establish_browser_owner():
        cookie_owner = request.cookies.get("amiga_file_forge_owner", "")
        browser_owner = request.headers.get("X-Amiga-Session-Owner", "")
        if runtime.kind == "desktop":
            owner_id = desktop_owner or ""
        elif re.fullmatch(r"[A-Za-z0-9_-]{32,64}", browser_owner):
            owner_id = browser_owner
        else:
            owner_id = cookie_owner
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", owner_id):
            owner_id = secrets.token_urlsafe(32)
        g.set_owner_cookie = cookie_owner != owner_id
        g.session_owner_token = SESSION_OWNER.set(owner_id)
        g.session_owner_id = owner_id

    @application.before_request
    def refuse_whole_image_operations_on_drives():
        """Turn away what only makes sense for an image file, on a live drive.

        A drive opened in place is not a file to download, convert, compact
        or roll back: copying a whole drive for any of those is the very thing
        opening it in place avoids.
        """
        if request.endpoint not in DRIVE_UNAVAILABLE:
            return None
        image_id = request.view_args.get("image_id") if request.view_args else None
        try:
            session = service.get(str(image_id)) if image_id else None
        except DiskError:
            return None
        if session is None or not session.attached_device:
            return None
        return jsonify(error=(
            "That works on an image file, not on a drive opened in place. Copy "
            "the files you need to an image in another pane instead."
        )), 400

    @application.before_request
    def checkpoint_image_mutation():
        """Create one undo point for every image-changing API request."""
        mutation = mutation_for(application.view_functions.get(request.endpoint))
        if mutation is None:
            return None
        image_id = request.view_args.get("image_id") if request.view_args else None
        if mutation.target == "targetImage":
            data = request.get_json(silent=True) or {}
            image_id = data.get("targetImage")
        if not image_id:
            return None
        session = service.get(str(image_id))
        if session.attached_device:
            # A drive opened in place has no undo: each checkpoint would be a
            # copy of the whole drive. Changes wait until writes are allowed.
            if not session.device_writes:
                return jsonify(error=(
                    "This drive is open read-only. Choose Allow writes on the "
                    "pane first; changes then go straight to the drive and "
                    "cannot be undone."
                )), 409
            # Advanced before the change rather than after it, so the summary
            # this request returns already carries the new revision, and a
            # request that fails part way still makes every pane look again.
            session.device_revision += 1
            return None
        g.undo_checkpoint_session = session
        g.undo_checkpoint_token = service.begin_automatic_checkpoint(
            session, mutation.reason
        )
        return None

    @application.teardown_request
    def release_browser_owner(_error=None):
        token = getattr(g, "session_owner_token", None)
        if token is not None:
            SESSION_OWNER.reset(token)

    application.register_blueprint(
        create_images_blueprint(service, ROOT / "static", operations, runtime)
    )
    application.register_blueprint(
        create_files_blueprint(service, active_work_dir, operations)
    )
    application.register_blueprint(create_catalog_blueprint(service, active_work_dir))
    application.register_blueprint(create_hex_editor_blueprint(service))
    emulator_manager = InteractiveEmulator(native=runtime.kind == "desktop")
    application.extensions["amiga_interactive_emulator"] = emulator_manager
    application.register_blueprint(
        create_tools_blueprint(
            service,
            operations,
            runtime,
            emulator_manager=emulator_manager,
        )
    )
    application.register_blueprint(create_rom_tools_blueprint(service, ROOT))
    application.register_blueprint(create_install_blueprint(service, operations))
    application.register_blueprint(
        create_app_update_blueprint(app_updater, desktop=runtime.kind == "desktop")
    )
    if runtime.kind == "desktop":
        state_path = Path(desktop_state_path) if desktop_state_path else active_work_dir / "client-state.json"
        application.register_blueprint(
            create_desktop_blueprint(
                service, operations, DesktopClientState(state_path), media_activity
            )
        )

    @application.errorhandler(DiskError)
    def disk_error(error):
        return jsonify(error=str(error)), 400

    @application.errorhandler(413)
    def too_large(_error):
        return jsonify(error=f"The image exceeds the {max_upload_gib} GiB upload limit."), 413

    @application.after_request
    def finalise_image_checkpoint(response):
        """Keep the undo point a successful request created, discard a failed one."""
        checkpoint_session = getattr(g, "undo_checkpoint_session", None)
        checkpoint_token = getattr(g, "undo_checkpoint_token", None)
        if checkpoint_session is None or checkpoint_token is None:
            return response
        try:
            if response.status_code >= 400:
                service.rollback_automatic_checkpoint(checkpoint_session, checkpoint_token)
            else:
                service.finish_automatic_checkpoint(checkpoint_session, checkpoint_token)
        except Exception:
            # Deliberately broad. This runs after the response is decided, so a
            # bookkeeping failure here must never replace a result the user has
            # already earned with a 500. Undo housekeeping is recoverable; the
            # response is not. Failures are logged rather than surfaced.
            application.logger.exception("Could not finalise the automatic image checkpoint")
        return response

    @application.after_request
    def apply_security_headers(response):
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    @application.after_request
    def identify_session_owner(response):
        """Echo the private owner identity and refresh its cookies when it changes."""
        owner_id = getattr(g, "session_owner_id", "")
        if owner_id:
            response.headers["X-Amiga-Session-Owner"] = owner_id
        if owner_id and getattr(g, "set_owner_cookie", False):
            response.set_cookie(
                "amiga_file_forge_owner",
                owner_id,
                max_age=365 * 24 * 60 * 60,
                httponly=True,
                samesite="Strict",
                secure=request.is_secure,
            )
        if getattr(g, "set_desktop_cookie", False):
            response.set_cookie(
                "amiga_file_forge_desktop",
                runtime.desktop_token,
                httponly=True,
                samesite="Strict",
                secure=request.is_secure,
            )
        return response

    @application.after_request
    def prevent_stale_frontend_assets(response):
        """Stop a cached client from outliving an upgraded service."""
        if request.path == "/" or request.path.endswith((".js", ".css")):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    return application


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=8666, threaded=True)
