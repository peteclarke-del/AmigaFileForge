# Firmware

Amiga File Forge ships **no** Amiga firmware, and it never will.

Kickstart ROMs, the Workbench disks and the CD32/CDTV extended ROMs remain the
copyright of their owners. They are not redistributable, so none is committed
here and none is downloaded during a build.

This directory is a place to keep your own copies alongside the source. Its
contents are ignored by Git, which is what makes that safe: everything except
this README is excluded, so a ROM cannot reach the public repository through a
stray `git add`. That exclusion is the only reason ROMs may sit here at all: if
you move them somewhere else in the tree, they are no longer covered.

## What the emulator hand-off needs

The workbench can hand an image to FS-UAE so a change can be watched running.
FS-UAE needs one Kickstart ROM for the machine you select:

| Machine | Kickstart | Size |
| --- | --- | --- |
| Amiga 500, 2000 | 1.3 (34.5) | 256 KiB |
| Amiga 500+, 600 | 2.05 (37.350) | 512 KiB |
| Amiga 1200, 4000 | 3.0 (39.106) or 3.1 (40.68) | 512 KiB |
| Amiga CD32 | 3.1 (40.60) plus the extended ROM | 512 KiB each |

Put your ROMs in the directory named by `AMIGA_FILE_FORGE_KICKSTART_DIR`
(default `~/.config/amiga-file-forge/kickstarts`), in this `firmware/`
directory, or in the FS-UAE data directory FS-UAE itself uses.

The names matter, because each machine is matched to a ROM by filename:

| File | Machine |
| --- | --- |
| `kick13.rom` | Amiga 500, 2000 |
| `kick204.rom` | Amiga 500+ |
| `kick205.rom` | Amiga 600 |
| `kick30.rom` | Amiga 1200 with Kickstart 3.0 |
| `kick31.rom` | Amiga 1200, 3000, 4000 |
| `kick40060.CD32` | Amiga CD32 |

A machine with no matching file has no Kickstart, and the application says so
rather than booting whichever ROM happens to be first.

If FS-UAE is installed as a Snap it is confined and can read only its own
`~/snap/fsuae/common/FS-UAE/Kickstarts`. Copy the ROMs there as well when the
emulator reports a missing Kickstart that you can plainly see. The workbench reads that directory, decodes each
ROM's resident-module list and reports which machines it can drive. Nothing is
copied out of it.

Legitimate sources include an original machine you own, and the licensed
Cloanto *Amiga Forever* package. An encrypted Cloanto ROM
(`AMIROMTYPE1`) needs its `rom.key` file in the same directory; the workbench
reports the ROM as read-only until that key is present.

## Checking a ROM

    python3 -m amiganut kickstart /path/to/kick31.rom

That prints the release, the ROM checksum verdict and every resident module,
without loading an emulator.
