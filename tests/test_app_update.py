"""The About box's application update: reading the latest release, and fetching and installing it."""

from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from app import app_update
from app.app_update import (
    BUSY_WITH_MEDIA,
    RESTART_WITH_MEDIA,
    Activity,
    AppRelease,
    AppUpdater,
    PackageTarget,
    UpdateCancelled,
    UpdateError,
    check,
    download,
    install,
    installed_target,
    is_newer,
    parse_version,
    published_sum,
    release_from,
)
from app.branding import APPLICATION_NAME, PACKAGE_NAME
from app.errors import DiskError
from app.version import application_version

try:
    from flask import Flask, jsonify

    from app.routes.app_update import create_app_update_blueprint
    from app.routes.desktop import create_desktop_blueprint
    from app.server import create_app
    from desktop.__main__ import RESTART_MESSAGE, restart_command
except ModuleNotFoundError:  # Flask is installed in the application image.
    Flask = None

ROOT = Path(__file__).resolve().parents[1]
UBUNTU = PackageTarget("ubuntu24.04", "amd64", "Ubuntu 24.04")
# The name tools/build-linux-package.sh gives the package, with the tilde of
# its revision as the dot the release workflow publishes it under.
PACKAGE = f"{PACKAGE_NAME}_9.0.0-1.ubuntu24.04_amd64.deb"


def release(tag: str = "v9.0.0", assets=(PACKAGE, "SHA256SUMS"), base="http://x", **extra):
    return {
        "tag_name": tag,
        "name": f"{APPLICATION_NAME} {tag.removeprefix('v')}",
        "html_url": f"{base}/releases/tag/{tag}",
        "body": "Faster conversions.",
        "assets": [
            {"name": name, "browser_download_url": f"{base}/{name}", "size": 1234}
            for name in assets
        ],
        **extra,
    }


class _Site(http.server.SimpleHTTPRequestHandler):
    """Files from a folder, and one reply GitHub gives when it limits requests."""

    def do_GET(self) -> None:
        if self.path == "/limited":
            body = json.dumps({"message": "API rate limit exceeded for 192.0.2.1."}).encode()
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *_args) -> None:
        pass


@contextmanager
def serve(folder: Path):
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_Site, directory=str(folder))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class VersionTests(unittest.TestCase):
    def test_only_release_tags_are_versions(self) -> None:
        self.assertEqual(parse_version("v0.4.0"), (0, 4, 0))
        self.assertEqual(parse_version("0.4.0"), (0, 4, 0))
        for tag in ("v0.4", "v0.5.0-rc.1", "v0.4.0.1", "latest", ""):
            self.assertIsNone(parse_version(tag), tag)

    def test_versions_compare_as_numbers(self) -> None:
        self.assertTrue(is_newer("v0.10.0", "0.9.0"))
        self.assertTrue(is_newer("v1.0.0", "0.99.99"))
        self.assertFalse(is_newer("v0.4.0", "0.4.0"))
        self.assertFalse(is_newer("v0.3.9", "0.4.0"))
        self.assertFalse(is_newer("v0.5.0-rc.1", "0.4.0"))

    def test_the_running_version_is_a_release_version(self) -> None:
        self.assertIsNotNone(parse_version(application_version()))

    @unittest.skipIf(Flask is None, "Flask is available in the application image")
    def test_restart_starts_the_desktop_host_again_without_reopening_images(self) -> None:
        self.assertEqual(
            restart_command(argparse.Namespace(images=[Path("a.img")], work_dir=None)),
            [sys.executable, "-m", "desktop"],
        )
        self.assertEqual(
            restart_command(argparse.Namespace(images=[], work_dir=Path("/w"))),
            [sys.executable, "-m", "desktop", "--work-dir", "/w"],
        )

    @unittest.skipIf(Flask is None, "Flask is available in the application image")
    def test_the_desktop_host_restarts_when_the_page_asks(self) -> None:
        host = (ROOT / "desktop" / "__main__.py").read_text(encoding="utf-8")
        page = (ROOT / "app" / "static" / "app-update.js").read_text(encoding="utf-8")
        self.assertIn(f'RESTART_MESSAGE = "{RESTART_MESSAGE}"', page)
        self.assertIn("if message == RESTART_MESSAGE:", host)
        self.assertIn("os.execv(command[0], command)", host)


class TargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)

    def test_the_file_a_release_package_writes_names_its_system(self) -> None:
        path = self.folder / "package-target"
        path.write_text("distro=deb13\narch=arm64\nlabel=Debian 13\n", encoding="utf-8")
        self.assertEqual(installed_target(path), PackageTarget("deb13", "arm64", "Debian 13"))
        self.assertEqual(installed_target(path).system, "Debian 13 arm64")

    def test_a_copy_without_a_readable_record_has_no_system(self) -> None:
        path = self.folder / "package-target"
        self.assertIsNone(installed_target(path))
        for text in ("distro=deb13\n", "arch=amd64\n", "distro=../x\narch=amd64\n", "distro=Deb 13\narch=amd64\n"):
            path.write_text(text, encoding="utf-8")
            self.assertIsNone(installed_target(path), text)
        # The source tree has no package-target beside VERSION.
        self.assertFalse(app_update.PACKAGE_TARGET.exists())
        self.assertEqual(app_update.PACKAGE_TARGET.parent, ROOT)

    def test_the_package_is_found_under_the_name_the_release_publishes(self) -> None:
        self.assertEqual(UBUNTU.package_revision(PACKAGE, "9.0.0"), 1)
        self.assertEqual(
            UBUNTU.package_revision(f"{PACKAGE_NAME}_9.0.0-2.ubuntu24.04_amd64.deb", "9.0.0"), 2
        )
        for other in (
            f"{PACKAGE_NAME}_9.0.0-1~ubuntu24.04_amd64.deb",
            f"{PACKAGE_NAME}_9.0.0-1.deb13_amd64.deb",
            f"{PACKAGE_NAME}_9.0.0-1.ubuntu24.04_arm64.deb",
            f"{PACKAGE_NAME}_9.0.1-1.ubuntu24.04_amd64.deb",
            f"{PACKAGE_NAME}_9.0.0_amd64.deb",
        ):
            self.assertIsNone(UBUNTU.package_revision(other, "9.0.0"), other)

    def test_the_release_build_records_the_system_and_the_release_checks_it(self) -> None:
        builder = (ROOT / "tools" / "build-linux-package.sh").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("printf 'distro=%s\\narch=%s\\nlabel=%s\\n'", builder)
        self.assertIn('"${package_revision#*~}" "$architecture" "$package_target"', builder)
        self.assertIn('> "$application/package-target"', builder)
        # The release names the package by the revision and architecture the
        # record is made from, and publishes it with the tilde as a dot.
        self.assertIn('package_name=${PACKAGE_NAME}_${package_version}_${architecture}.deb'.replace(
            "${PACKAGE_NAME}", PACKAGE_NAME), builder)
        self.assertIn("tr '~' '.'", workflow)
        self.assertIn("app_update.installed_target()", workflow)
        self.assertIn("target.package_revision(", workflow)
        self.assertIn('--env EXPECTED_DISTRO="$PACKAGE_DISTRO"', workflow)


class ReleaseTests(unittest.TestCase):
    def test_a_newer_release_with_this_systems_package_is_installable(self) -> None:
        found = release_from(release(), UBUNTU, current="0.4.0")
        assert found is not None
        self.assertEqual(
            (found.version, found.tag, found.name), ("9.0.0", "v9.0.0", f"{APPLICATION_NAME} 9.0.0")
        )
        self.assertTrue(found.installable)
        self.assertEqual(found.package_name, PACKAGE)
        self.assertEqual(found.package_url, f"http://x/{PACKAGE}")
        self.assertEqual(found.package_size, 1234)
        self.assertEqual(found.sums_url, "http://x/SHA256SUMS")
        self.assertEqual(found.page_url, "http://x/releases/tag/v9.0.0")

    def test_the_same_or_an_older_version_is_not_offered(self) -> None:
        self.assertIsNone(release_from(release("v0.4.0"), UBUNTU, current="0.4.0"))
        self.assertIsNone(release_from(release("v0.3.0"), UBUNTU, current="0.4.0"))

    def test_another_systems_package_or_an_unpackaged_copy_cannot_install(self) -> None:
        debian = release_from(release(), PackageTarget("deb13", "armhf"), current="0.4.0")
        assert debian is not None
        self.assertFalse(debian.installable)
        self.assertEqual(debian.package_name, "")
        source = release_from(release(), None, current="0.4.0")
        assert source is not None
        self.assertFalse(source.installable)
        self.assertEqual(source.page_url, "http://x/releases/tag/v9.0.0")
        no_sums = release_from(release(assets=(PACKAGE,)), UBUNTU, current="0.4.0")
        assert no_sums is not None
        self.assertFalse(no_sums.installable)

    def test_a_rebuilt_package_is_preferred(self) -> None:
        rebuilt = f"{PACKAGE_NAME}_9.0.0-2.ubuntu24.04_amd64.deb"
        found = release_from(release(assets=(PACKAGE, rebuilt, "SHA256SUMS")), UBUNTU, current="0.4.0")
        assert found is not None
        self.assertEqual(found.package_name, rebuilt)

    def test_a_latest_release_without_a_version_tag_is_a_publishing_mistake(self) -> None:
        with self.assertRaises(UpdateError) as caught:
            release_from(release("nightly"), UBUNTU, current="0.4.0")
        self.assertIn("the tag nightly, which is not a version", str(caught.exception))

    def test_checksum_lines_are_read_as_sha256sum_writes_them(self) -> None:
        digest = "a" * 64
        sums = f"{'b' * 64}  other.deb\n{digest}  {PACKAGE}\n{'c' * 64} *binary.deb\n"
        self.assertEqual(published_sum(sums, PACKAGE), digest)
        self.assertEqual(published_sum(sums, "binary.deb"), "c" * 64)
        self.assertEqual(published_sum(sums, "absent.deb"), "")


class ServedTests(unittest.TestCase):
    """The check and the download against a local web server."""

    def setUp(self) -> None:
        self.site = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.site)
        self.cache = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.cache)
        context = serve(self.site)
        self.base = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def publish(self, package: bytes = b"a package", sums: bytes | None = None) -> AppRelease:
        (self.site / PACKAGE).write_bytes(package)
        digest = hashlib.sha256(package).hexdigest()
        (self.site / "SHA256SUMS").write_bytes(sums or f"{digest}  {PACKAGE}\n".encode())
        (self.site / "latest").write_text(json.dumps(release(base=self.base)))
        found = check(UBUNTU, url=f"{self.base}/latest", current="0.4.0")
        assert found is not None
        return found

    def test_the_check_finds_the_newer_release(self) -> None:
        found = self.publish()
        self.assertEqual(found.version, "9.0.0")
        self.assertTrue(found.installable)
        self.assertIsNone(check(UBUNTU, url=f"{self.base}/latest", current="9.0.0"))

    def test_the_check_names_the_application(self) -> None:
        seen = []

        def opener(request, timeout):
            seen.append((request.get_header("User-agent"), request.get_header("Accept"), timeout))
            raise OSError("offline")

        with self.assertRaises(UpdateError):
            check(UBUNTU, opener=opener)
        agent, accept, timeout = seen[0]
        self.assertTrue(agent.startswith(f"{app_update.REPOSITORY.rpartition('/')[2]}/"))
        self.assertIn(app_update.HOMEPAGE, agent)
        self.assertEqual(accept, "application/vnd.github+json")
        self.assertEqual(timeout, app_update.CHECK_TIMEOUT)

    def test_a_check_that_fails_says_why_and_never_says_newest(self) -> None:
        with self.assertRaises(UpdateError) as caught:
            check(UBUNTU, url="http://127.0.0.1:9/latest")
        self.assertIn("127.0.0.1:9 could not be reached", str(caught.exception))
        with self.assertRaises(UpdateError) as caught:
            check(UBUNTU, url=f"{self.base}/absent")
        self.assertEqual(str(caught.exception), "No release has been published on GitHub yet.")
        with self.assertRaises(UpdateError) as caught:
            check(UBUNTU, url=f"{self.base}/limited")
        self.assertIn(
            "refused the request (HTTP 403): API rate limit exceeded for 192.0.2.1.",
            str(caught.exception),
        )
        (self.site / "odd").write_text('{"message": "Bad credentials"}')
        with self.assertRaises(UpdateError) as caught:
            check(UBUNTU, url=f"{self.base}/odd")
        self.assertIn("did not send a release: Bad credentials.", str(caught.exception))
        (self.site / "broken").write_text("<html>")
        with self.assertRaises(UpdateError) as caught:
            check(UBUNTU, url=f"{self.base}/broken")
        self.assertIn("could not be read", str(caught.exception))

    def test_the_package_is_downloaded_and_checked(self) -> None:
        found = self.publish()
        seen: list[tuple[int, int | None]] = []
        path = download(found, lambda done, total: seen.append((done, total)), folder=self.cache)
        self.assertEqual(path, self.cache / PACKAGE)
        self.assertEqual(path.read_bytes(), b"a package")
        self.assertEqual(seen[-1], (9, 9))
        self.assertEqual(sorted(item.name for item in self.cache.iterdir()), [PACKAGE])

    def test_a_package_that_does_not_match_its_checksum_is_removed(self) -> None:
        found = self.publish(sums=f"{'0' * 64}  {PACKAGE}\n".encode())
        with self.assertRaises(UpdateError) as caught:
            download(found, folder=self.cache)
        self.assertIn("does not match its published checksum", str(caught.exception))
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_a_package_the_checksums_do_not_list_is_not_downloaded(self) -> None:
        found = self.publish(sums=f"{'0' * 64}  other.deb\n".encode())
        with self.assertRaises(UpdateError) as caught:
            download(found, folder=self.cache)
        self.assertIn("has no line for the package", str(caught.exception))
        self.assertFalse((self.cache / PACKAGE).exists())

    def test_a_cancelled_download_says_so_and_leaves_nothing(self) -> None:
        found = self.publish()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(UpdateCancelled):
            download(found, cancel=cancel, folder=self.cache)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_a_download_larger_than_any_package_is_refused(self) -> None:
        found = self.publish()
        with mock.patch.object(app_update, "MAX_PACKAGE_BYTES", 4), self.assertRaises(UpdateError) as caught:
            download(found, folder=self.cache)
        self.assertIn("larger than any release package", str(caught.exception))
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_a_release_without_this_systems_package_is_not_downloaded(self) -> None:
        with self.assertRaises(UpdateError):
            download(AppRelease("9.0.0", "v9.0.0", "name", "http://x"), folder=self.cache)


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.package = self.folder / PACKAGE
        self.package.write_bytes(b"a package")
        which = {"pkexec": "/usr/bin/pkexec", "apt-get": "/usr/bin/apt-get"}
        patcher = mock.patch.object(app_update.shutil, "which", side_effect=which.get)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_with(self, returncode: int, stderr: str = ""):
        return mock.Mock(
            return_value=subprocess.CompletedProcess([], returncode, stdout="", stderr=stderr)
        )

    def test_apt_installs_the_package_with_the_users_password(self) -> None:
        run = self.run_with(0)
        install(self.package, run=run)
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/pkexec", "/usr/bin/apt-get", "install", "--yes", str(self.package)],
        )
        self.assertFalse(self.package.exists())

    def test_the_install_waits_for_the_password_prompt_and_apt_however_long(self) -> None:
        # pkexec and apt run as root and cannot be stopped from here, so a time
        # limit would report a failure while apt went on to install.
        run = self.run_with(0)
        install(self.package, run=run)
        self.assertNotIn("timeout", run.call_args.kwargs)

    def test_a_dismissed_password_prompt_installs_nothing(self) -> None:
        with self.assertRaises(UpdateCancelled):
            install(self.package, run=self.run_with(126))
        self.assertTrue(self.package.exists())

    def test_a_refusal_or_an_apt_failure_says_what_to_run_by_hand(self) -> None:
        with self.assertRaises(UpdateError) as caught:
            install(self.package, run=self.run_with(127))
        self.assertNotIsInstance(caught.exception, UpdateCancelled)
        self.assertIn("did not allow", str(caught.exception))
        self.assertIn(f"sudo apt install {self.package}", str(caught.exception))
        with self.assertRaises(UpdateError) as caught:
            install(self.package, run=self.run_with(100, "E: Unable to locate package\n"))
        self.assertIn("E: Unable to locate package", str(caught.exception))
        self.assertTrue(self.package.exists())

    def test_without_pkexec_the_command_is_given_instead(self) -> None:
        with (
            mock.patch.object(app_update.shutil, "which", return_value=None),
            self.assertRaises(UpdateError) as caught,
        ):
            install(self.package, run=self.run_with(0))
        self.assertIn("pkexec is not installed", str(caught.exception))
        self.assertIn("sudo apt install", str(caught.exception))


NEWER = AppRelease(
    "9.0.0", "v9.0.0", f"{APPLICATION_NAME} 9.0.0", "http://x/releases/tag/v9.0.0",
    PACKAGE, f"http://x/{PACKAGE}", 1234, "http://x/SHA256SUMS",
)


class UpdaterTests(unittest.TestCase):
    """The state the About box shows, with the work run in place of a thread."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.activity = Activity()
        self.found: AppRelease | None | Exception = NEWER
        self.installed: list[Path] = []
        self.downloaded: list[str] = []
        self.install_error: Exception | None = None

    def checker(self, _target):
        if isinstance(self.found, Exception):
            raise self.found
        return self.found

    def downloader(self, release, progress, cancel, *, folder):
        self.downloaded.append(release.package_name)
        progress(1234, 1234)
        if cancel.is_set():
            raise UpdateCancelled("The update was cancelled.")
        package = folder / release.package_name
        folder.mkdir(parents=True, exist_ok=True)
        package.write_bytes(b"a package")
        return package

    def installer(self, package):
        if self.install_error is not None:
            raise self.install_error
        self.installed.append(package)

    def updater(self, target=UBUNTU, **overrides) -> AppUpdater:
        return AppUpdater(
            target,
            folder=self.folder,
            activity=self.activity,
            checker=self.checker,
            downloader=overrides.get("downloader", self.downloader),
            installer=self.installer,
            start=lambda work, _name: work(),
        )

    def available(self, **overrides) -> AppUpdater:
        updater = self.updater(**overrides)
        updater.check()
        self.assertEqual(updater.state.phase, "available")
        return updater

    def test_nothing_is_checked_until_asked(self) -> None:
        updater = self.updater()
        snapshot = updater.snapshot()
        self.assertEqual((snapshot["phase"], snapshot["message"], snapshot["release"]), ("idle", "", None))
        self.assertEqual(snapshot["system"], "Ubuntu 24.04 amd64")
        self.assertEqual(snapshot["application"], APPLICATION_NAME)

    def test_the_newest_version_is_reported_as_such(self) -> None:
        self.found = None
        updater = self.updater()
        updater.check()
        self.assertEqual(updater.state.phase, "current")
        self.assertEqual(
            updater.state.message, f"{APPLICATION_NAME} {application_version()} is the newest version"
        )

    def test_a_newer_version_is_offered_with_its_page(self) -> None:
        snapshot = self.available().snapshot()
        self.assertEqual(snapshot["release"]["version"], "9.0.0")
        self.assertTrue(snapshot["release"]["installable"])
        self.assertEqual(snapshot["release"]["pageUrl"], "http://x/releases/tag/v9.0.0")
        self.assertIn(f"{APPLICATION_NAME} 9.0.0 is available", snapshot["message"])

    def test_a_failed_check_says_why_and_never_says_newest(self) -> None:
        for error in (UpdateError("api.github.com could not be reached."), KeyError("assets")):
            self.found = error
            updater = self.updater()
            updater.check()
            self.assertEqual(updater.state.phase, "failed")
            self.assertTrue(updater.state.message.startswith("Could not check for a newer version: "))
            self.assertNotIn("newest version", updater.state.message)

    def test_a_copy_that_cannot_install_is_sent_to_the_release_page(self) -> None:
        self.found = release_from(release(), None, current="0.4.0")
        updater = self.updater(target=None)
        updater.check()
        self.assertIn("cannot update itself", updater.state.message)
        with self.assertRaises(UpdateError):
            updater.install()
        self.found = release_from(release(), PackageTarget("deb13", "armhf", "Debian 13"), current="0.4.0")
        updater = self.updater(target=PackageTarget("deb13", "armhf", "Debian 13"))
        updater.check()
        self.assertIn("no package for Debian 13 armhf", updater.state.message)

    def test_the_update_downloads_installs_and_offers_a_restart(self) -> None:
        updater = self.available()
        updater.install()
        self.assertEqual(updater.state.phase, "installed")
        self.assertEqual(
            updater.state.message,
            f"{APPLICATION_NAME} 9.0.0 is installed. Restart {APPLICATION_NAME} to use it.",
        )
        self.assertEqual(self.installed, [self.folder / PACKAGE])

    def test_nothing_is_installed_while_a_disk_is_read_or_written(self) -> None:
        updater = self.available()
        with self.activity.running():
            updater.install()
            self.assertEqual(updater.state.phase, "available")
            self.assertEqual(updater.state.message, BUSY_WITH_MEDIA)
            self.assertEqual(updater.restart_refusal(), RESTART_WITH_MEDIA)
        # Nothing was even downloaded.
        self.assertEqual((self.downloaded, self.installed), ([], []))
        self.assertEqual(updater.restart_refusal(), "")

    def test_a_disk_started_during_the_download_holds_the_install(self) -> None:
        def downloader(release, progress, cancel, *, folder):
            package = self.downloader(release, progress, cancel, folder=folder)
            self.enter = self.activity.running()
            self.enter.__enter__()
            return package

        updater = self.available(downloader=downloader)
        updater.install()
        self.enter.__exit__(None, None, None)
        self.assertEqual((updater.state.phase, updater.state.message), ("available", BUSY_WITH_MEDIA))
        self.assertEqual(self.installed, [])

    def test_a_cancelled_download_or_dismissed_prompt_leaves_the_update_offered(self) -> None:
        def cancelled(release, progress, cancel, *, folder):
            updater.cancel()
            return self.downloader(release, progress, cancel, folder=folder)

        updater = self.available(downloader=cancelled)
        updater.install()
        self.assertEqual((updater.state.phase, updater.state.message), ("available", "The update was cancelled."))
        self.install_error = UpdateCancelled("The password prompt was dismissed, so nothing was installed.")
        updater = self.available()
        updater.install()
        self.assertEqual(updater.state.phase, "available")
        self.assertIn("password prompt was dismissed", updater.state.message)

    def test_a_failed_install_says_why(self) -> None:
        self.install_error = UpdateError("The system did not allow the installation. Install it ...")
        updater = self.available()
        updater.install()
        self.assertEqual(updater.state.phase, "failed")
        self.assertTrue(updater.state.message.startswith("The update failed: The system did not allow"))

    def test_an_update_is_not_started_twice_or_without_a_check(self) -> None:
        updater = self.updater()
        with self.assertRaises(UpdateError):
            updater.install()
        pending = []
        updater = AppUpdater(
            UBUNTU, folder=self.folder, activity=self.activity, checker=self.checker,
            start=lambda work, _name: pending.append(work),
        )
        updater.check()
        updater.check()
        self.assertEqual(len(pending), 1)
        self.assertTrue(updater.snapshot()["busy"])


@unittest.skipIf(Flask is None, "Flask is available in the application image")
class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)

    def test_only_the_desktop_host_can_install(self) -> None:
        web = create_app(work_dir=self.folder / "web")
        desktop = create_app(
            work_dir=self.folder / "desktop",
            platform="desktop",
            desktop_token="d" * 32,
            desktop_owner="o" * 32,
        )
        client = web.test_client()
        self.assertEqual(client.get("/api/app-update").get_json()["phase"], "idle")
        # Only the static files answer these paths in the web host, and never to a POST.
        for action in ("install", "cancel", "restart"):
            self.assertIn(client.post(f"/api/desktop/app-update/{action}").status_code, (404, 405))
        headers = {"X-Amiga-Desktop-Token": "d" * 32}
        refused = desktop.test_client().post("/api/desktop/app-update/install", headers=headers)
        self.assertEqual(refused.status_code, 409)
        self.assertIn("Check for Application Updates first", refused.get_json()["error"])

    def test_the_web_host_never_takes_a_package_target(self) -> None:
        with mock.patch("app.server.installed_target", return_value=UBUNTU):
            web = create_app(work_dir=self.folder / "web")
            desktop = create_app(
                work_dir=self.folder / "desktop",
                platform="desktop",
                desktop_token="d" * 32,
                desktop_owner="o" * 32,
            )
        self.assertIsNone(web.extensions["amiga_app_updater"].target)
        self.assertEqual(web.test_client().get("/api/app-update").get_json()["system"], "")
        self.assertEqual(desktop.extensions["amiga_app_updater"].target, UBUNTU)

    def test_the_disk_routes_hold_an_update_and_a_restart(self) -> None:
        activity = Activity()
        updater = AppUpdater(UBUNTU, folder=self.folder, activity=activity, checker=lambda _t: NEWER,
                             start=lambda work, _name: work())
        service = mock.Mock(work_dir=self.folder)
        application = Flask(__name__)
        application.register_blueprint(create_desktop_blueprint(service, None, mock.Mock(), activity))
        application.register_blueprint(create_app_update_blueprint(updater, desktop=True))
        application.register_error_handler(DiskError, lambda error: (jsonify(error=str(error)), 400))
        client = application.test_client()
        seen = []

        def payload():
            seen.append(activity.busy)
            # The update and the restart are refused while the disk is in use.
            seen.append(client.post("/api/desktop/app-update/restart").get_json())
            raise DiskError("stopped before the drive")

        with mock.patch("app.routes.desktop.payload", side_effect=payload):
            for url in (
                "/api/desktop/images/image-id/physical-floppy",
                "/api/desktop/physical-floppy/read",
                "/api/desktop/floppy-drive/read",
                "/api/desktop/floppy-drive/write",
            ):
                self.assertEqual(client.post(url, json={}).status_code, 400, url)
        self.assertEqual(seen, [True, {"error": RESTART_WITH_MEDIA}] * 4)
        self.assertFalse(activity.busy)
        self.assertEqual(client.post("/api/desktop/app-update/restart").get_json(), {"restart": True})
        client.post("/api/app-update/check")
        with activity.running():
            held = client.post("/api/desktop/app-update/install").get_json()
        self.assertEqual((held["phase"], held["message"]), ("available", BUSY_WITH_MEDIA))


if __name__ == "__main__":
    unittest.main()
