"""The About box's application update: the shared check and the desktop-only install."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..app_update import AppUpdater, UpdateError
from .effects import request_effect


def create_app_update_blueprint(updater: AppUpdater, *, desktop: bool) -> Blueprint:
    """Both hosts can check; only the desktop host, running as the user, can install.

    The web host has no install, cancel or restart route at all, so a browser
    on another computer can never start ``pkexec`` on the server.
    """
    blueprint = Blueprint("app_update", __name__)

    @blueprint.get("/api/app-update")
    def app_update_state():
        return jsonify(updater.snapshot())

    @blueprint.post("/api/app-update/check")
    @request_effect("external", "asking GitHub for the latest application release")
    def check_app_update():
        return jsonify(updater.check())

    if not desktop:
        return blueprint

    @blueprint.post("/api/desktop/app-update/install")
    @request_effect("external", "downloading and installing the latest application release")
    def install_app_update():
        try:
            return jsonify(updater.install())
        except UpdateError as error:
            return jsonify(error=str(error)), 409

    @blueprint.post("/api/desktop/app-update/cancel")
    @request_effect("external", "cancelling the application update download")
    def cancel_app_update():
        return jsonify(updater.cancel())

    @blueprint.post("/api/desktop/app-update/restart")
    @request_effect("lifecycle", "asking whether the application may restart after an update")
    def restart_after_app_update():
        refusal = updater.restart_refusal()
        if refusal:
            return jsonify(error=refusal), 409
        return jsonify(restart=True)

    return blueprint


__all__ = ["create_app_update_blueprint"]
