from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

from flask import Flask, g, jsonify, request

from .disk_service import SESSION_OWNER, DiskError, DiskService
from .desktop_state import DesktopClientState
from .operations import OperationRegistry
from .routes.files import create_files_blueprint
from .routes.hex_editor import create_hex_editor_blueprint
from .routes.catalog import create_catalog_blueprint
from .routes.desktop import create_desktop_blueprint
from .routes.images import create_images_blueprint
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


def create_app(
    *,
    work_dir: Path | str | None = None,
    platform: str = "web",
    desktop_token: str | None = None,
    desktop_owner: str | None = None,
    desktop_state_path: Path | str | None = None,
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
    if runtime.kind == "desktop":
        state_path = Path(desktop_state_path) if desktop_state_path else active_work_dir / "client-state.json"
        application.register_blueprint(
            create_desktop_blueprint(service, operations, DesktopClientState(state_path))
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
