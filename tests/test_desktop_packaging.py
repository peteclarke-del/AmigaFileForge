from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DesktopPackagingTests(unittest.TestCase):
    def test_desktop_entry_uses_a_validated_stable_launcher(self) -> None:
        template = (
            ROOT / "packaging/linux/uk.co.amigafileforge.AmigaFileForge.desktop.in"
        ).read_text(encoding="utf-8")
        installer = (ROOT / "tools/install-linux-desktop.sh").read_text(encoding="utf-8")

        self.assertIn("Exec=@EXEC@ %F", template)
        self.assertIn("TryExec=@TRY_EXEC@", template)
        self.assertIn('registered_launcher="$user_bin/amiga-file-forge"', installer)
        self.assertIn('ln -sfn "$launcher" "$registered_launcher"', installer)
        self.assertIn('desktop-file-validate "$desktop_file"', installer)

    def test_launcher_resolves_symlink_and_removes_snap_gtk_paths(self) -> None:
        launcher = (ROOT / "tools/amiga-file-forge-desktop").read_text(encoding="utf-8")
        environment = (ROOT / "tools/linux-desktop-environment.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('launcher_path=$(readlink -f -- "$0")', launcher)
        self.assertIn("linux-desktop-environment.sh", launcher)
        self.assertIn("GDK_PIXBUF_MODULEDIR", environment)
        self.assertIn("GSETTINGS_SCHEMA_DIR", environment)
        self.assertIn('"$HOME"/snap/*', environment)

    def test_launcher_handles_restricted_webkit_user_namespaces(self) -> None:
        launcher = (ROOT / "tools/amiga-file-forge-desktop").read_text(encoding="utf-8")
        environment = (ROOT / "tools/linux-desktop-environment.sh").read_text(
            encoding="utf-8"
        )
        desktop_host = (ROOT / "desktop/__main__.py").read_text(encoding="utf-8")

        self.assertIn("linux-desktop-environment.sh", launcher)
        self.assertIn("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1", environment)
        self.assertIn("apparmor_restrict_unprivileged_userns", environment)
        self.assertIn("AMIGA_FILE_FORGE_DISABLE_WEBKIT_SANDBOX", environment)
        self.assertIn('self.webview.connect("decide-policy", self._navigation_policy)', desktop_host)
        self.assertIn("Gio.AppInfo.launch_default_for_uri", desktop_host)

    def test_native_host_owns_file_chooser_bridge_and_gtk_chrome(self) -> None:
        desktop_host = (ROOT / "desktop/__main__.py").read_text(encoding="utf-8")
        frontend = (ROOT / "app/static/app.js").read_text(encoding="utf-8")

        self.assertIn('register_script_message_handler("amigaDesktop")', desktop_host)
        self.assertIn("user_content_manager=self.content_manager", desktop_host)
        self.assertIn('Gtk.Button.new_from_icon_name("document-open-symbolic")', desktop_host)
        self.assertIn('icon_name="open-menu-symbolic"', desktop_host)
        self.assertIn("Gtk.Window.set_default_icon_name", desktop_host)
        self.assertIn("self.window.set_icon_name", desktop_host)
        self.assertIn("applyNativeAppearance", frontend)
        self.assertIn("open-images:${index}", frontend)
        self.assertIn("self.chooser_targets[chooser]", desktop_host)
        self.assertNotIn("self.chooser_targets[id(chooser)]", desktop_host)
        self.assertIn("chooserOpened(preferredIndex", frontend)
        self.assertIn("evaluate_javascript_finish", desktop_host)
        self.assertIn("Gtk.DropTarget.new", desktop_host)
        self.assertIn("Gdk.FileList", desktop_host)
        self.assertIn("_native_files_dropped", desktop_host)
        self.assertIn("paneAtPoint(x, y)", frontend)

    def test_installer_rejects_snap_private_xdg_data_home(self) -> None:
        paths = (ROOT / "tools/linux-xdg-paths.sh").read_text(encoding="utf-8")
        installer = (ROOT / "tools/install-linux-desktop.sh").read_text(encoding="utf-8")

        self.assertIn('"$HOME"/snap/*', paths)
        self.assertIn('"$HOME/.local/share"', paths)
        self.assertIn("XDG_DATA_DIRS_VSCODE_SNAP_ORIG", installer)
        self.assertIn("inherited_data_home", installer)

    def test_debian_package_reuses_shared_application_and_desktop_environment(self) -> None:
        builder = (ROOT / "tools/build-linux-package.sh").read_text(encoding="utf-8")
        launcher = (ROOT / "packaging/linux/amiga-file-forge").read_text(
            encoding="utf-8"
        )

        for shared_source in (
            '"$project_root/amiga_greaseweazle"',
            '"$project_root/app"',
            '"$project_root/desktop"',
        ):
            self.assertIn(shared_source, builder)
        self.assertIn("linux-desktop-environment.sh", launcher)
        self.assertIn('PYTHONPATH="$project_root/vendor:$project_root', launcher)
        self.assertIn('PATH="$project_root/native/bin:', launcher)
        self.assertIn('LD_LIBRARY_PATH="$project_root/native/lib', launcher)
        self.assertIn("tools/build-hxc-runtime.sh", builder)
        self.assertIn("dpkg-deb --build --root-owner-group", builder)
        self.assertNotIn("firmware", builder)
        self.assertIn("AMIGA_PACKAGE_REVISION", builder)
        self.assertIn("AMIGA_PACKAGE_TARGET", builder)
        self.assertIn("X-Amiga-Target", builder)

    def test_debian_package_registers_desktop_mime_appstream_and_manual(self) -> None:
        builder = (ROOT / "tools/build-linux-package.sh").read_text(encoding="utf-8")
        postinst = (ROOT / "packaging/linux/postinst").read_text(encoding="utf-8")
        postrm = (ROOT / "packaging/linux/postrm").read_text(encoding="utf-8")
        metainfo = (
            ROOT / "packaging/linux/uk.co.amigafileforge.AmigaFileForge.metainfo.xml"
        ).read_text(encoding="utf-8")

        for required in (
            "uk.co.amigafileforge.AmigaFileForge.desktop",
            "uk.co.amigafileforge.AmigaFileForge.xml",
            "uk.co.amigafileforge.AmigaFileForge.metainfo.xml",
            "amiga-file-forge.1.gz",
        ):
            self.assertIn(required, builder)
        self.assertIn("<id>uk.co.amigafileforge.AmigaFileForge</id>", metainfo)
        self.assertIn("StartupWMClass=uk.co.amigafileforge.AmigaFileForge", (
            ROOT / "packaging/linux/uk.co.amigafileforge.AmigaFileForge.desktop.in"
        ).read_text(encoding="utf-8"))
        self.assertIn("Icon=amiga-file-forge", (
            ROOT / "packaging/linux/uk.co.amigafileforge.AmigaFileForge.desktop.in"
        ).read_text(encoding="utf-8"))
        for icon_size in (48, 64, 128, 256):
            self.assertTrue((
                ROOT
                / "packaging/linux/icons"
                / f"{icon_size}x{icon_size}"
                / "apps/amiga-file-forge.png"
            ).is_file())
        self.assertIn("packaging/linux/icons", builder)
        self.assertIn("usr/share/pixmaps/amiga-file-forge.png", builder)
        self.assertIn("packaging/linux/icons", (
            ROOT / "tools/install-linux-desktop.sh"
        ).read_text(encoding="utf-8"))
        for maintainer_script in (postinst, postrm):
            self.assertIn("gtk4-update-icon-cache", maintainer_script)
            self.assertIn("gtk-update-icon-cache", maintainer_script)

    def test_debian_dependency_lock_contains_every_application_requirement(self) -> None:
        application = {
            line.strip().lower()
            for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
        package_lock = {
            line.strip().lower()
            for line in (
                ROOT / "packaging/linux/requirements-debian.txt"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }

        self.assertTrue(application.issubset(package_lock))

    def test_stable_release_builds_debian_and_ubuntu_for_supported_architectures(self) -> None:
        """The release workflow covers every distribution and architecture claimed.

        This asserts nothing about which version is being released. A literal
        version here would have to be edited for every release, in a test whose
        subject is the workflow rather than the version, and forgetting it
        fails the suite for a reason that has nothing to do with packaging.
        ``ReleaseRecordTests`` owns that check, against ``VERSION`` as the one
        source of truth.
        """
        workflow = (ROOT / ".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )
        for required in (
            "debian:trixie-slim",
            "ubuntu:24.04",
            "linux/amd64",
            "linux/arm64",
            "linux/arm/v7",
            "--verify-tag",
            "SHA256SUMS",
        ):
            self.assertIn(required, workflow)
        self.assertIn("tools/build-source-archive.sh", workflow)
        self.assertIn('cd "$stage/opt/amiga-file-forge"', workflow)
        self.assertTrue((ROOT / "docs/releases/1.0.2.md").is_file())


if __name__ == "__main__":
    unittest.main()


class ShippedPackageTests(unittest.TestCase):
    """Every importable package must reach every artefact that ships the app.

    A new top-level package is easy to add and easy to forget in the Dockerfile
    and the Debian builder, which produces an application that imports cleanly
    in a checkout and fails only once installed.
    """

    @staticmethod
    def _application_packages() -> set[str]:
        root = Path(__file__).resolve().parent.parent
        return {
            path.name
            for path in root.iterdir()
            if path.is_dir()
            and (path / "__init__.py").is_file()
            and not path.name.startswith((".", "_"))
            and path.name not in {"tests"}
        }

    # The container is the web edition and deliberately has no GTK host, so it
    # does not ship the desktop shell. It must still ship everything the Flask
    # application imports, including the hardware adapters, because the desktop
    # blueprint is imported by the shared application factory on both hosts.
    CONTAINER_EXCLUDES = frozenset({"desktop"})

    def test_the_container_image_copies_every_package_the_web_edition_imports(self) -> None:
        root = Path(__file__).resolve().parent.parent
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        for package in sorted(self._application_packages() - self.CONTAINER_EXCLUDES):
            with self.subTest(package=package):
                self.assertIn(
                    f"COPY {package} ./{package}",
                    dockerfile,
                    f"the container image does not ship {package}",
                )

    def test_the_web_edition_starts_without_the_desktop_shell(self) -> None:
        """The excluded package must genuinely be unnecessary to serve the web app."""

        for package in sorted(self.CONTAINER_EXCLUDES):
            with self.subTest(package=package):
                self.assertFalse(
                    any(
                        f"import {package}" in line or f"from {package}" in line
                        for path in (Path(__file__).resolve().parent.parent / "app").rglob("*.py")
                        for line in path.read_text(encoding="utf-8").splitlines()
                        if not line.strip().startswith("#")
                    ),
                    f"app imports {package}, so the container cannot omit it",
                )

    def test_the_debian_package_copies_every_application_package(self) -> None:
        root = Path(__file__).resolve().parent.parent
        builder = (root / "tools" / "build-linux-package.sh").read_text(encoding="utf-8")
        for package in sorted(self._application_packages()):
            with self.subTest(package=package):
                self.assertIn(
                    f'"$project_root/{package}"',
                    builder,
                    f"the Debian package does not ship {package}",
                )

    def test_the_package_ships_source_rather_than_build_machine_bytecode(self) -> None:
        """Bytecode compiled here is wrong for any other supported Python.

        A stale ``.pyc`` is preferred over the source it no longer matches, so
        shipping the build machine's cache risks running code the package does
        not contain.
        """
        root = Path(__file__).resolve().parent.parent
        builder = (root / "tools" / "build-linux-package.sh").read_text(encoding="utf-8")
        self.assertIn("-name __pycache__", builder)
        self.assertIn("-name '*.py[co]' -delete", builder)
        # The removal must happen before the size is measured and the package built.
        self.assertLess(
            builder.index("__pycache__"),
            builder.index("dpkg-deb --build"),
        )

