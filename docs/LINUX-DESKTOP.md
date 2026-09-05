# Linux desktop application

The Linux desktop edition gives Amiga File Forge a normal application window,
file chooser, application-menu entry and Amiga image file associations. It
uses the same workbench and backend as the Docker edition, so format support,
editors, validation, recipes, checkpoints, deployment packages and save
packages stay in step.

## Requirements

Use a current Debian or Ubuntu desktop with a supported Python 3 release. The
container currently uses Python 3.14, while the native installer uses the
distribution-provided Python. Install the native libraries first:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-gi \
  gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-webkit-6.0 \
  shared-mime-info desktop-file-utils build-essential python3-dev
```

Package names can differ on Fedora, Arch and other distributions. The required
GObject namespaces are GTK 4, Adwaita 1 and WebKit 6.0.
Build tools are required because Capstone may need to compile on hosts where
PyPI does not provide a matching wheel.

## Install from a checkout

```bash
git clone https://github.com/peteclarke-del/AmigaFileForge.git
cd AmigaFileForge
tools/install-linux-desktop.sh
```

The installer creates `.venv-desktop` in the checkout, installs the Python
application dependencies there and registers the launcher, icon and MIME types
for the current user. The application-menu entry uses the stable
`~/.local/bin/amiga-file-forge` launcher, which points back to this checkout.
This avoids malformed desktop commands when the checkout path contains spaces.
It does not use `sudo` and does not modify the Docker installation.

When installation is started from a sandboxed IDE terminal, such as the Snap
build of Visual Studio Code, the script ignores that application's private
`XDG_DATA_HOME` and registers with the real user desktop under
`~/.local/share`. Any stale Amiga File Forge entry left in the IDE's private
data directory is removed.
The launcher resolves its real file after following the symbolic link, so it
still finds the checkout and virtual environment. It also removes Snap-private
GTK module paths when started from an IDE terminal, preventing incompatible
Snap libraries from being loaded into the native application.

Some Ubuntu installations deny the unprivileged user namespace that
WebKitGTK's Bubblewrap process normally creates. The launcher detects that
specific kernel restriction and enables WebKit's compatibility fallback only
when required. Set `AMIGA_FILE_FORGE_DISABLE_WEBKIT_SANDBOX=0` to require the
sandbox, `1` to force the fallback for diagnosis, or leave the default `auto`.
The fallback is limited to the desktop host, whose WebView loads only the
authenticated service bound to `127.0.0.1`; it does not change the
Docker/browser edition.

## Install a release package

A release `.deb` installs the same desktop host without retaining a Git
checkout or creating a per-checkout virtual environment:

```bash
sudo apt install ./amiga-file-forge_1.1.1-1~deb13_amd64.deb
```

The package places the shared application in `/opt/amiga-file-forge` and the
launcher at `/usr/bin/amiga-file-forge`. It registers the application menu,
file associations, scalable and fixed-size application icons, AppStream
metadata and the `amiga-file-forge(1)` manual. The native window also publishes
the same icon name to GTK, so GNOME, Ubuntu Dock and X11 window managers can
associate a running window with its application-menu entry.
Pinned Python dependencies are included in an architecture-specific vendor
directory. The package also carries an architecture-native
HxCFloppyEmulator command-line converter (`hxcfe`) and its private libraries,
so HFE creation, opening and verified saving work without a separate host
installation. GTK, Libadwaita, WebKit and PyGObject remain distribution
packages so security updates continue to come from APT. The
[HFE, SCP and HxCFE guide](HFE-HXC-GUIDE.md) documents its package paths and direct
runtime check.

Release filenames identify `deb13` or `ubuntu24.04` and use the Debian
architecture name `amd64`, `arm64` or `armhf`. Build packages on the Debian or
Ubuntu release that will run them. Native
Python extensions are not assumed to be portable between different Python
ABIs. The package intentionally excludes firmware, commercial images and
managed emulator binaries. Configure installed emulators with the variables in
the Emulator paths section below.

Launch **Amiga File Forge** from the desktop application menu, run
`~/.local/bin/amiga-file-forge`, or open a registered ADF, ADZ, HDF, FFS,
Hardfile, DMS, HFE or ROM image from the file manager. HDA and GEO partners are
matched automatically when they share a basename.

The folder button in the native header, **File → Open image** in a pane and
<kbd>Ctrl</kbd>+<kbd>O</kbd> all use the GTK file chooser. This keeps local media
off the browser upload path. A compact review appears before anything opens.
It starts with the active Workbench target, permits an explicit FFS target and
lets multiple ROM files be opened independently or assembled as one linear or
byte-interleaved component set. The same review is used for file associations
and file-manager drops.

You can also drag image files from the Linux file manager onto a workbench
pane. The first image targets the pane under the pointer and further images use
successive empty panes. HDA and GEO partners are paired before opening, so a
matching pair creates one Hardfile session. The GTK drop controller uses the
same trusted local-path adapter as the native chooser and does not upload image
bytes through WebKit.

## Desktop behaviour

- The application starts a private random-port service on `127.0.0.1` and
  closes it with the GTK application.
- A launch token protects every private service request. It is removed from
  the visible WebView address immediately after startup.
- A separate private owner identifier remains stable between launches. It
  recovers this Linux user's sessions without weakening the per-launch token.
- Workspace settings, hardware profiles and the private collection catalogue
  are retained in an atomic, mode-0600 XDG configuration file. They therefore
  survive the random loopback port and WebView-origin change.
- Native path selection avoids uploading through a browser request. The source
  is cloned by the filesystem when supported, otherwise it is sparse-copied to
  a safe working session before editing. A 512 MiB Hardfile HDA therefore does
  not need to be uploaded, spooled and copied a second time.
- Opening an HDA validates its GEO pairing, geometry and root AmigaDOS metadata.
  The expensive full-image sparse optimisation is deferred until Save, where
  the existing progress dialog describes directory repair, checksum and final
  validation stages.
- Working sessions are stored under the XDG data directory and use the same
  owner-isolated recovery model as the web edition.
- Reviewed native open plans run through one serial worker. A second chooser or
  file-manager action cannot race the first session creation or reuse a stale
  preferred pane.
- Save image produces the same timestamped ZIP and technical README. WebKitGTK
  writes it to the user's normal Downloads directory.
- **Tools → Build hardware deployment** uses the same isolated snapshot and
  target layouts as Docker. The finished Gotek, FastFileSystem, Hardfile, PiStorm or RISC
  OS ZIP is written through WebKitGTK to the normal Downloads directory.
- Run and Debug use native emulator windows. The Docker edition continues to
  use its browser-visible noVNC display.
- Supported floppy images and one selected partition can be written through a
  locally installed Greaseweazle. Choose **Tools → Write physical floppy** or
  right-click the image title. The workflow includes drive selection,
  destructive confirmation, tracked progress, cancellation and verification.
- GTK and Libadwaita own the title bar, window controls, application menu,
  keyboard shortcuts, file chooser and symbolic header icons. The embedded
  workbench inherits the desktop font, follows the system light or dark setting
  until a user theme is chosen, and uses flatter desktop-sized controls. Its
  Amiga-inspired media colours remain consistent with the browser edition.

### Why a large HDA used to pause at 24 percent

The old pane chooser was an HTML upload control. Its percentage measured the
transfer from WebKit into the loopback Flask request, not FFS parsing. A large
HDA was then spooled by Werkzeug, copied into the private session and scanned
again for zero ranges. On a 512 MiB Hardfile image that meant several complete
passes over the file before the root directory appeared.

The native chooser now passes the selected local path through the authenticated
desktop-only API and creates the private working copy directly. On filesystems
with copy-on-write reflinks this is effectively immediate. Other filesystems
perform one sparse copy, so removable media and network mounts can still take
time, but the redundant loopback upload and eager zero scan are gone.

There is intentionally no parallel GTK implementation of panes or editors.
That would double the maintenance burden and allow filesystem safety fixes to
drift. The detailed rules are in the
[platform contract](PLATFORM-CONTRACT.md).

## Greaseweazle physical disks

Greaseweazle is optional and is not installed automatically. Install the
official tools and Linux udev rules, then confirm `gw info` works in the same
desktop session that launches Amiga File Forge. ADF, ADZ and sector-based FFS
floppies are written with automatic read-back verification. HFE can be written,
but its raw bitcell representation does not support automatic verification.

The complete safety and troubleshooting workflow is in the
[physical floppy guide](PHYSICAL-FLOPPY-GUIDE.md).

## Emulator paths

The native application can use existing emulator installations. Export the
applicable variables before launching when they are not under `/opt`:

```bash
export AMIGA_FSUAE_ROOT="$HOME/Applications/fs-uae"
export AMIGA_FILE_FORGE_KICKSTART_DIR="$HOME/Amiga/kickstarts"
export AMIGA_FILE_FORGE_CAPSIMAGE="$HOME/lib/libcapsimage.so.5.1"
tools/amiga-file-forge-desktop
```

The Workbench profile still selects the machine, additions and emulator. A
missing executable or firmware set is reported before launch.

## Update and remove

Pull the new source and rerun the installer after dependency changes:

```bash
git pull --ff-only
tools/install-linux-desktop.sh
```

Rerunning the installer also repairs an application entry that points to an
older or moved checkout and refreshes the desktop, MIME and icon databases.

Remove the launcher and private environment with:

```bash
tools/uninstall-linux-desktop.sh
```

The uninstaller deliberately retains working sessions under the XDG data
directory. Remove that directory only after saving any images you need.

## Developer smoke test

Run the shared parity tests whenever composition or routes change:

```bash
python -m unittest tests.test_platform_contract
node --check app/static/app.js
```

Then launch the desktop host and verify opening from GTK, opening from a file
manager, recovery after restart, a complete image download and a native
emulator launch. Web browser regressions remain mandatory because the frontend
is shared.

## Troubleshooting application-menu launch

Rerun `tools/install-linux-desktop.sh` after pulling an update. If the icon is
present but no window appears, run `~/.local/bin/amiga-file-forge` in a terminal
to retain startup diagnostics. Older installations may report `bwrap: setting
up uid map: Permission denied`; rerunning the current installer refreshes the
launcher with the WebKitGTK fallback described above.

The application entry is
`~/.local/share/applications/uk.co.amigafileforge.AmigaFileForge.desktop`.
`desktop-file-validate` should report no errors for it. A checkout may live in
a path containing spaces because the desktop entry calls the stable launcher
under `~/.local/bin`, not the checkout path directly.

If the menu entry appears without its Amiga File Forge icon after upgrading an
older checkout installation, remove that checkout entry with
`tools/uninstall-linux-desktop.sh` before reinstalling the release package.
Release packages install 48, 64, 128 and 256 pixel PNG fallbacks as well as the
scalable SVG, then rebuild both GTK icon caches during package configuration.
