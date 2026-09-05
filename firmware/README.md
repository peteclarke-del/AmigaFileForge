# Firmware

Amiga File Forge ships **no** Amiga firmware, and it never will.

Kickstart ROMs, the Workbench disks and the CD32/CDTV extended ROMs remain the
copyright of their owners. They are not redistributable, so nothing in this
directory is a ROM and nothing is downloaded during a build.

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
(default `~/.config/amiga-file-forge/kickstarts`), or in the FS-UAE data
directory FS-UAE itself uses. The workbench reads that directory, decodes each
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
