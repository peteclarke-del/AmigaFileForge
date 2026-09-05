# Windows, macOS and RPM-based Linux

Amiga File Forge is one application with several shells. The Flask service, the
filesystem and editor services and the whole frontend are portable Python and
JavaScript. Only the window differs by platform, so a change to shared
behaviour reaches every edition at once. The
[platform contract](PLATFORM-CONTRACT.md) is what keeps that true.

## What runs where

| Edition | Shell | Status |
| --- | --- | --- |
| Docker web service | Browser | Supported and released |
| Debian and Ubuntu package | GTK 4 with WebKitGTK | Supported and released |
| RPM package (Fedora, RHEL, openSUSE) | GTK 4 with WebKitGTK | Buildable from this tree |
| Windows | WebView2 through pywebview | Buildable from this tree |
| macOS | WKWebView through pywebview | Buildable from this tree |

The Debian and Docker editions are the two the project releases and tests in
CI. The other three build from the same sources and are exercised by the test
suite, but they are not yet part of the release matrix. Treat them as working
builds rather than shipped artefacts.

## Why the shell differs

The Linux desktop host uses GTK 4 with WebKitGTK, which has no supportable form
on Windows or macOS. Rather than port the toolkit, those platforms use the
system webview through [pywebview](https://pywebview.flowrl.com/):

- Windows uses WebView2, the Edge runtime present on current Windows installs.
- macOS uses WKWebView, part of the operating system.

The same private, token-authenticated loopback server sits behind both, so
sessions, working images, undo history and the platform contract behave
identically. Linux keeps the GTK host because it also provides native menus,
the file chooser, drag and drop and desktop file associations, which the
portable shell does not.

A Linux machine without the GTK stack falls back to the portable shell rather
than refusing to start.

## Where per-user data lives

Each platform's own convention is followed:

| Platform | Settings and sessions | Working images |
| --- | --- | --- |
| Linux | `$XDG_DATA_HOME/amiga-file-forge`, `$XDG_CONFIG_HOME/amiga-file-forge` | `$XDG_DATA_HOME/amiga-file-forge/work` |
| macOS | `~/Library/Application Support/AmigaFileForge` | the same, under `work` |
| Windows | `%APPDATA%\AmigaFileForge` | `%LOCALAPPDATA%\AmigaFileForge\work` |

On Windows the working images sit under Local rather than Roaming. They can be
rebuilt from their sources and are frequently large, so synchronising them with
a roaming profile would be slow and pointless.

## Building an RPM

```bash
tools/build-rpm-package.sh dist
```

Requires `rpmbuild` (the `rpm-build` package on Fedora and RHEL, `rpm` on
openSUSE) and `dpkg-deb`.

The RPM is not a separate implementation. The script builds the Debian package
first, unpacks its payload, and packages that same tree, so both distributions
install byte-identical application trees under `/opt/amiga-file-forge`. Only the
metadata, dependency names and scriptlets differ. A fault reproduced on one
distribution therefore reproduces on the other.

Set `AMIGA_RPM_RELEASE` to control the release field, for example
`AMIGA_RPM_RELEASE=2.fc41`.

## Building for Windows and macOS

Both need the runtime dependencies plus the portable shell:

```bash
python -m pip install -r requirements.txt pywebview
python -m desktop
```

On Windows, WebView2 is required. Current Windows installs already include it;
otherwise install the Microsoft Edge WebView2 runtime.

### Native engine availability

The application shells out to two engines, and this is the part that needs
attention when moving away from Linux:

- **`disc`**, the Amiganut filesystem engine, installs from PyPI with the pinned
  requirements and works on all three platforms.
- **`hxcfe`**, the HxCFloppyEmulator converter, is a C program built from
  source by `tools/build-hxc-runtime.sh`. That script targets POSIX shells and
  produces a Linux binary. HFE and SCP support needs an `hxcfe` on `PATH`;
  everything else, including OFS, FFS, HDF, DMS and ROM work, runs without it.

An edition without `hxcfe` reports the absence clearly when an HFE or SCP
operation is attempted rather than failing obscurely.

## Physical media

Reading and writing real disks is desktop-only on every platform, because it
needs hardware the browser cannot reach. Two adapters exist, and they are not
equivalent. See the
[physical floppy guide](PHYSICAL-FLOPPY-GUIDE.md) for which to choose.

Greaseweazle works wherever its `gw` command runs, which includes all three
platforms. The floppy-controller adapter reads `/dev/fd0` directly and is
therefore Linux only.
