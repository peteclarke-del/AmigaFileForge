"""Launch Amiga File Forge as a native Linux desktop application."""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

from app.image_opening import IMAGE_EXTENSIONS
from app.rom_components import MAX_ROM_COMPONENTS

from . import webview_host
from .runtime import DesktopServer


NATIVE_OPEN_EXTENSIONS = IMAGE_EXTENSIONS | {".geo", ".zip"}
MAX_NATIVE_OPEN_PLANS = 256


def _desktop_message_text(result) -> str:
    """Return a script message from current and legacy WebKitGTK bindings.

    WebKitGTK 6 passes a JSC.Value directly to the signal handler. Older
    bindings wrapped that value in WebKit.JavascriptResult. Keeping the API
    difference here avoids coupling the native command handling to either
    representation.
    """
    get_js_value = getattr(result, "get_js_value", None)
    value = get_js_value() if callable(get_js_value) else result
    to_string = getattr(value, "to_string", None)
    if not callable(to_string):
        raise TypeError("WebKit supplied an unsupported script message value.")
    message = to_string()
    if not isinstance(message, str):
        raise TypeError("WebKit supplied a non-text script message.")
    return message


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Amiga File Forge Linux desktop host")
    parser.add_argument("images", nargs="*", type=Path, help="Images to open")
    parser.add_argument("--work-dir", type=Path, help="Override the XDG working directory")
    return parser.parse_args(argv)


def _desktop_libraries():
    try:
        import gi

        gi.require_version("Adw", "1")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Gtk", "4.0")
        gi.require_version("WebKit", "6.0")
        from gi.repository import Adw, Gdk, Gio, GLib, Gtk, WebKit
    except (ImportError, ValueError) as exc:
        raise RuntimeError(
            "The Linux desktop host needs GTK 4, Libadwaita, WebKitGTK 6 and "
            "their Python GObject bindings. The Docker/browser edition remains "
            "available without these desktop packages."
        ) from exc
    return Adw, Gdk, Gio, GLib, Gtk, WebKit


def _paired_selection(paths: list[Path]) -> list[Path]:
    resolved = [path.expanduser().resolve() for path in paths]
    dat_stems = {
        (path.parent, path.stem.casefold())
        for path in resolved if path.suffix.casefold() == ".hda"
    }
    return [
        path for path in resolved
        if path.suffix.casefold() != ".geo"
        or (path.parent, path.stem.casefold()) not in dat_stems
    ]


def _review_open_plans(message: str) -> list[dict]:
    """Validate one frontend message before any native work is queued."""
    data = json.loads(message)
    plans = data.get("plans")
    if data.get("command") != "open-plans" or not isinstance(plans, list):
        return []
    if not plans or len(plans) > MAX_NATIVE_OPEN_PLANS:
        raise ValueError(
            f"Choose between 1 and {MAX_NATIVE_OPEN_PLANS} images at a time."
        )
    reviewed = []
    for plan in plans:
        if not isinstance(plan, dict) or not isinstance(plan.get("paths"), list):
            raise ValueError("The native open plan is incomplete.")
        if len(plan["paths"]) > MAX_ROM_COMPONENTS:
            raise ValueError(
                f"A ROM set cannot contain more than {MAX_ROM_COMPONENTS} components."
            )
        if any(not isinstance(value, str) or not value for value in plan["paths"]):
            raise ValueError("The native open plan contains an invalid path.")
        try:
            paths = [
                Path(value).expanduser().resolve(strict=True)
                for value in plan["paths"]
            ]
        except OSError as exc:
            raise ValueError(
                "A selected native image is no longer available."
            ) from exc
        if not paths or any(not path.is_file() for path in paths):
            raise ValueError("A selected native image is no longer available.")
        reviewed.append({**plan, "paths": paths})
    return reviewed


def _run_portable_shell(args) -> int:
    """Run the system-webview shell used on Windows and macOS."""
    try:
        return webview_host.WebviewHost(args.work_dir).run(list(args.images))
    except webview_host.WebviewUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2


def run(argv: list[str] | None = None) -> int:
    """Start the desktop application in whichever shell suits this platform.

    Linux prefers the GTK host, which carries the native menus, file chooser,
    drag and drop and desktop associations. Windows and macOS use the portable
    webview host instead, because GTK 4 with WebKitGTK has no supportable form
    there. Both shells run the same server and frontend.
    """
    args = _arguments(list(argv if argv is not None else sys.argv[1:]))
    if webview_host.preferred_for_platform():
        return _run_portable_shell(args)
    try:
        Adw, Gdk, Gio, GLib, Gtk, WebKit = _desktop_libraries()
    except RuntimeError as exc:
        # A Linux machine without the GTK stack can still use the portable
        # shell, which is better than refusing to start at all.
        if webview_host.available():
            return _run_portable_shell(args)
        print(str(exc), file=sys.stderr)
        return 2

    server = DesktopServer(args.work_dir)

    class AmigaFileForgeApplication(Adw.Application):
        def __init__(self) -> None:
            super().__init__(
                application_id="uk.co.amigafileforge.AmigaFileForge",
                flags=Gio.ApplicationFlags.HANDLES_OPEN,
            )
            self.window = None
            self.webview = None
            self.content_manager = None
            self.style_manager = None
            self.loaded = False
            self.pending_paths = []
            self.open_queue: queue.Queue[dict | None] = queue.Queue()
            self.open_worker: threading.Thread | None = None
            self.stopping = False
            # Retain each native dialog until its response. Keeping only its
            # numeric id lets the PyGObject wrapper be finalised while GTK or
            # the desktop portal still owns the visible chooser.
            self.chooser_targets = {}
            self.native_drop_target = None

        def do_startup(self) -> None:
            Adw.Application.do_startup(self)
            action = Gio.SimpleAction.new("open", None)
            action.connect("activate", self._choose_images)
            self.add_action(action)
            self.set_accels_for_action("app.open", ["<Primary>o"])
            quit_action = Gio.SimpleAction.new("quit", None)
            quit_action.connect("activate", lambda *_args: self.quit())
            self.add_action(quit_action)
            self.set_accels_for_action("app.quit", ["<Primary>q"])

        def do_activate(self) -> None:
            if self.window is None:
                server.start()
                Gtk.Window.set_default_icon_name("amiga-file-forge")
                self.window = Adw.ApplicationWindow(application=self)
                self.window.set_title("Amiga File Forge")
                self.window.set_icon_name("amiga-file-forge")
                self.window.set_default_size(1440, 900)
                self.style_manager = Adw.StyleManager.get_default()
                self.style_manager.connect(
                    "notify::dark",
                    self._native_appearance_changed,
                )
                settings = Gtk.Settings.get_default()
                if settings is not None:
                    settings.connect(
                        "notify::gtk-font-name",
                        self._native_appearance_changed,
                    )
                toolbar = Adw.ToolbarView()
                header = Adw.HeaderBar()
                header.set_title_widget(Adw.WindowTitle.new(
                    "Amiga File Forge",
                    "Amiga media image workbench",
                ))
                open_button = Gtk.Button.new_from_icon_name("document-open-symbolic")
                open_button.set_tooltip_text("Open media image")
                open_button.set_action_name("app.open")
                header.pack_start(open_button)
                menu = Gio.Menu()
                menu.append("Open Image…", "app.open")
                menu.append("Quit", "app.quit")
                menu_button = Gtk.MenuButton(
                    icon_name="open-menu-symbolic",
                    menu_model=menu,
                )
                menu_button.set_tooltip_text("Application menu")
                header.pack_end(menu_button)
                toolbar.add_top_bar(header)
                self.content_manager = WebKit.UserContentManager()
                self.content_manager.register_script_message_handler("amigaDesktop")
                self.content_manager.connect(
                    "script-message-received::amigaDesktop",
                    self._desktop_message,
                )
                self.webview = WebKit.WebView(
                    user_content_manager=self.content_manager,
                )
                self.webview.connect("load-changed", self._loaded)
                self.webview.connect("decide-policy", self._navigation_policy)
                self.native_drop_target = Gtk.DropTarget.new(
                    Gdk.FileList,
                    Gdk.DragAction.COPY,
                )
                self.native_drop_target.set_propagation_phase(
                    Gtk.PropagationPhase.CAPTURE
                )
                self.native_drop_target.connect("drop", self._native_files_dropped)
                self.webview.add_controller(self.native_drop_target)
                toolbar.set_content(self.webview)
                self.window.set_content(toolbar)
                request = WebKit.URIRequest.new(server.url)
                request.get_http_headers().append(
                    "X-Amiga-Desktop-Token", server.token
                )
                self.webview.load_request(request)
                self.window.connect("close-request", self._closing)
            self.window.present()

        def _desktop_message(self, _manager, result) -> None:
            try:
                message = _desktop_message_text(result)
            except TypeError as exc:
                GLib.idle_add(
                    self._deliver_error,
                    "the native desktop command",
                    str(exc),
                )
                return
            if message == "open-images" or message.startswith("open-images:"):
                _command, separator, pane_value = message.partition(":")
                try:
                    preferred_pane = int(pane_value) if separator else None
                except ValueError:
                    preferred_pane = None
                self._choose_images(None, None, preferred_pane)
                return
            try:
                reviewed = _review_open_plans(message)
                for plan in reviewed:
                    self.open_queue.put(plan)
                if reviewed:
                    self._start_open_worker()
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                GLib.idle_add(self._deliver_error, "the selected image", str(exc))

        def _navigation_policy(self, _view, decision, decision_type) -> bool:
            if decision_type not in (
                WebKit.PolicyDecisionType.NAVIGATION_ACTION,
                WebKit.PolicyDecisionType.NEW_WINDOW_ACTION,
            ):
                return False
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri.startswith(server.url) or uri == "about:blank" or uri.startswith("blob:"):
                return False
            decision.ignore()
            if uri.startswith(("http://", "https://")):
                Gio.AppInfo.launch_default_for_uri(uri, None)
            return True

        def do_open(self, files, _count, _hint) -> None:
            paths = _paired_selection(
                [Path(item.get_path()) for item in files if item.get_path()]
            )
            if paths:
                self.pending_paths.append((paths, None))
            self.activate()
            self._drain_paths()

        def _loaded(self, _view, event) -> None:
            if event != WebKit.LoadEvent.FINISHED:
                return
            self.loaded = True
            self._apply_native_appearance()
            self._drain_paths()

        def _apply_native_appearance(self) -> None:
            settings = Gtk.Settings.get_default()
            font = settings.get_property("gtk-font-name") if settings else "system-ui 11"
            dark = self.style_manager.get_dark() if self.style_manager else False
            script = (
                "window.AmigaDesktopHost.applyNativeAppearance("
                f"{json.dumps({'font': font, 'dark': dark})});"
            )
            self.webview.evaluate_javascript(script, -1, None, None, None)

        def _native_appearance_changed(self, *_args) -> None:
            if self.loaded:
                self._apply_native_appearance()

        def _choose_images(
            self,
            _action,
            _parameter,
            preferred_pane: int | None = None,
        ) -> None:
            chooser = Gtk.FileChooserNative.new(
                "Open Amiga media images",
                self.window,
                Gtk.FileChooserAction.OPEN,
                "_Open",
                "_Cancel",
            )
            chooser.set_select_multiple(True)
            self.chooser_targets[chooser] = preferred_pane
            chooser.connect("response", self._files_chosen)
            chooser.show()
            self._evaluate_frontend(
                "window.AmigaDesktopHost.chooserOpened("
                f"{json.dumps(preferred_pane)});"
            )

        def _files_chosen(self, chooser, response) -> None:
            try:
                if response not in {
                    Gtk.ResponseType.ACCEPT,
                    Gtk.ResponseType.OK,
                    Gtk.ResponseType.YES,
                    Gtk.ResponseType.APPLY,
                }:
                    return
                files = chooser.get_files()
                paths = []
                for index in range(files.get_n_items()):
                    selected = files.get_item(index)
                    selected_path = selected.get_path() if selected is not None else None
                    if selected_path:
                        paths.append(Path(selected_path))
                if not paths:
                    raise RuntimeError(
                        "The native file chooser returned no local file path. "
                        "Choose a file stored on a mounted local or network filesystem."
                    )
                preferred = self.chooser_targets.get(chooser)
                self.pending_paths.append((_paired_selection(paths), preferred))
                self._drain_paths()
            except Exception as exc:
                GLib.idle_add(
                    self._deliver_error,
                    "the selected image",
                    str(exc) or type(exc).__name__,
                )
            finally:
                self.chooser_targets.pop(chooser, None)
                chooser.destroy()

        def _native_files_dropped(self, _target, file_list, x, y) -> bool:
            """Open host files through the local-path adapter, never an upload."""
            try:
                paths = []
                for selected in file_list.get_files():
                    selected_path = selected.get_path()
                    if not selected_path:
                        return False
                    path = Path(selected_path)
                    if not path.is_file() or path.suffix.casefold() not in NATIVE_OPEN_EXTENSIONS:
                        # Preserve the shared workbench's existing file and
                        # folder import behaviour for non-image drops.
                        return False
                    paths.append(path)
                paths = _paired_selection(paths)
                if not paths:
                    return False
            except Exception as exc:
                GLib.idle_add(
                    self._deliver_error,
                    "the dropped image",
                    str(exc) or type(exc).__name__,
                )
                return True

            self.webview.evaluate_javascript(
                "window.AmigaDesktopHost.paneAtPoint("
                f"{float(x)}, {float(y)});",
                -1,
                None,
                None,
                None,
                self._native_drop_pane_resolved,
                paths,
            )
            return True

        def _native_drop_pane_resolved(self, webview, result, paths) -> None:
            preferred_pane = None
            try:
                value = webview.evaluate_javascript_finish(result)
                candidate = int(value.to_int32())
                if candidate >= 0:
                    preferred_pane = candidate
            except Exception as exc:
                print(
                    f"Could not resolve the pane under a native file drop: {exc}",
                    file=sys.stderr,
                )
            self.pending_paths.append((paths, preferred_pane))
            self._drain_paths()

        def _evaluate_frontend(self, script: str) -> None:
            """Run a bounded host notification and report JavaScript errors."""
            expression = f"(() => {{ {script} return true; }})()"
            self.webview.evaluate_javascript(
                expression,
                -1,
                None,
                None,
                None,
                self._frontend_evaluated,
                None,
            )

        def _frontend_evaluated(self, webview, result, _data=None) -> None:
            try:
                webview.evaluate_javascript_finish(result)
            except Exception as exc:
                print(f"Desktop frontend bridge failed: {exc}", file=sys.stderr)

        def _drain_paths(self) -> None:
            if not self.loaded or not self.pending_paths:
                return
            selections, self.pending_paths = self.pending_paths, []
            for paths, preferred_pane in selections:
                GLib.idle_add(self._deliver_selection, paths, preferred_pane)

        def _start_open_worker(self) -> None:
            if self.stopping or (self.open_worker and self.open_worker.is_alive()):
                return
            self.open_worker = threading.Thread(
                target=self._open_plans,
                name="amiga-file-forge-desktop-open",
                daemon=True,
            )
            self.open_worker.start()

        def _open_plans(self) -> None:
            while not self.stopping:
                plan = self.open_queue.get()
                if plan is None:
                    self.open_queue.task_done()
                    return
                display_name = "the selected image"
                try:
                    paths = plan["paths"]
                    display_name = paths[0].name
                    preferred_pane = plan.get("preferredPane")
                    GLib.idle_add(self._deliver_opening, display_name, preferred_pane)
                    body = {
                        "path": str(paths[0]),
                        "componentPaths": [str(path) for path in paths],
                        "targetHardware": plan.get("targetHardware") or "auto",
                        "forceKind": plan.get("forceKind") or "",
                        "rom": plan.get("rom") if isinstance(plan.get("rom"), dict) else {},
                    }
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{server.port}/api/desktop/open-path",
                        data=json.dumps(body).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "X-Amiga-Desktop-Token": server.token,
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=3600) as response:
                        image = json.load(response)["image"]
                    GLib.idle_add(self._deliver_image, image, preferred_pane)
                except urllib.error.HTTPError as exc:
                    try:
                        details = json.load(exc)
                        message = str(details.get("error") or exc.reason)
                    except (OSError, ValueError, AttributeError):
                        message = str(exc.reason or exc)
                    finally:
                        exc.close()
                    GLib.idle_add(self._deliver_error, display_name, message)
                except (KeyError, OSError, TypeError, ValueError, urllib.error.URLError) as exc:
                    GLib.idle_add(self._deliver_error, display_name, str(exc))
                finally:
                    self.open_queue.task_done()

        def _deliver_selection(self, paths: list[Path], preferred_pane: int | None) -> bool:
            try:
                rows = [
                    {"path": str(path), "name": path.name, "size": path.stat().st_size}
                    for path in paths
                ]
            except OSError as exc:
                return self._deliver_error("the selected image", str(exc))
            self._evaluate_frontend(
                "window.AmigaDesktopHost.reviewSelection("
                f"{json.dumps(rows)}, {json.dumps(preferred_pane)});"
            )
            return GLib.SOURCE_REMOVE

        def _deliver_opening(self, name: str, preferred_pane: int | None) -> bool:
            script = (
                "window.AmigaDesktopHost.showOpening("
                f"{json.dumps(name)}, {json.dumps(preferred_pane)});"
            )
            self._evaluate_frontend(script)
            return GLib.SOURCE_REMOVE

        def _deliver_image(self, image: dict, preferred_pane: int | None) -> bool:
            script = (
                "window.AmigaDesktopHost.acceptImage("
                f"{json.dumps(image)}, {json.dumps(preferred_pane)});"
            )
            self._evaluate_frontend(script)
            return GLib.SOURCE_REMOVE

        def _deliver_error(self, name: str, message: str) -> bool:
            script = (
                "window.AmigaDesktopHost.showError("
                f"{json.dumps(f'Could not open {name}: {message}')});"
            )
            self._evaluate_frontend(script)
            return GLib.SOURCE_REMOVE

        def _stop_workers(self) -> None:
            if self.stopping:
                return
            self.stopping = True
            self.open_queue.put(None)
            if self.open_worker and self.open_worker is not threading.current_thread():
                self.open_worker.join(timeout=5)

        def _closing(self, _window) -> bool:
            self._stop_workers()
            server.stop()
            return False

        def do_shutdown(self) -> None:
            self._stop_workers()
            server.stop()
            Adw.Application.do_shutdown(self)

    application = AmigaFileForgeApplication()
    return int(application.run([sys.argv[0], *map(str, args.images)]))


if __name__ == "__main__":
    raise SystemExit(run())
