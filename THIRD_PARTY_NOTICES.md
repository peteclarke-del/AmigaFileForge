# Third-party notices

Amiga File Forge source is distributed under the MIT License in
[LICENSE](LICENSE). The application also uses software, system packages and
firmware governed by separate terms. This inventory records the dependency
boundary; it does not replace the authoritative licence text supplied by each
copyright holder.

## Python and browser dependencies

| Component | Version in this repository | Licence | Project |
| --- | --- | --- | --- |
| Flask | 3.1.3 | BSD-3-Clause | <https://github.com/pallets/flask> |
| Gunicorn | 26.2.0 | MIT | <https://github.com/benoitc/gunicorn> |
| Capstone | 5.0.9 | BSD-3-Clause | <https://github.com/capstone-engine/capstone> |
| Playwright | 1.62.1, development and browser tests | Apache-2.0 | <https://github.com/microsoft/playwright> |

The AmigaDOS filing-system engine in `amiganut/` is part of this project, not a
third-party dependency. It is covered by the same MIT licence as the rest of
the source and has no dependencies of its own beyond the Python standard
library.

**No Amiga firmware is included.** Kickstart ROMs, Workbench disks and the CD32
and CDTV extended ROMs remain the copyright of their owners, are not
redistributable, and are neither shipped nor downloaded by any build step. The
emulator hand-off reads ROMs the user supplies.

Transitive Python and Node packages retain their own terms. The authoritative
installed inventory is produced by `python -m pip list` and `npm ls`; package
metadata and licence files should be retained by a binary distributor.

The native Debian package vendors the pinned Python dependency set under
`/opt/amiga-file-forge/vendor`. GTK, Libadwaita, WebKitGTK, PyGObject and
desktop integration tools remain distribution packages and keep their system
copyright records. The package builder excludes the repository firmware tree,
samples, working images and Git metadata.

## Source-built and runtime tools

| Component | Pinned revision or source | Licence boundary |
| --- | --- | --- |
| HxC Floppy Emulator command-line engine | `b1eee4cd73391ceaf2ad4ac57e28bf11c91333ba` | GPL-3.0; the Linux package installs the upstream `COPYING` file under `native/share/licenses` |
| FS-UAE | Debian runtime package | GPL-2.0; the only emulator this project bundles. Emulated Kickstart ROMs are separate and are not shipped. |
| noVNC | Debian runtime package | MPL-2.0 for the core library, with separately licensed web assets |
| websockify | Debian runtime package | LGPL-3.0 |
| Greaseweazle | Optional host tool | GPL-3.0; not copied into the application repository |

The Docker build also installs Debian libraries and utilities, including GTK
and Libadwaita integration dependencies, Xvfb, x11vnc, ImageMagick and
xdotool. Their exact versions are selected by the pinned Debian
base distribution and retain the copyright files installed under
`/usr/share/doc`. Distributing a container image may trigger notice or source
obligations beyond those of the Amiga File Forge source repository.

## Firmware and ROM material

Files under `firmware/` are not covered by the Amiga File Forge MIT licence.
No Amiga firmware is shipped there; the directory records what a user must
supply themselves and where the application looks for it. Its purpose and
update rules are in [firmware/README.md](firmware/README.md).

The repository currently documents that redistribution rights must be
confirmed before publishing a derived source archive, container image or
native package. A distributor must not infer permission from the presence of a
binary in this repository. Replace or omit material whose redistribution basis
cannot be established.

ROMs, disk images, DMS archives and hard-drive images opened or downloaded
by a user retain the rights of their original authors and publishers. Amiga
File Forge does not relicense them.

## Maintaining this inventory

A dependency update must record the new version or commit, upstream source,
licence, required notices and any source-offer obligation. Firmware updates
also require provenance, redistribution review and SHA-256 verification. The
release checklist treats those records as a release gate.
