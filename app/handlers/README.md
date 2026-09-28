# Filing-system handlers

Kickstart carries one filing system, the FastFileSystem. A partition in any
other mounts only if its handler travels with the drive, in the Rigid Disk
Block, where the machine finds it before it mounts the first partition. The
files here are what Amiga File Forge puts there when it creates a drive.

| File | What it is | Licence |
| --- | --- | --- |
| `pfs3aio` | Professional File System III 19.2, the all-in-one build by Toni Wilen, from <https://github.com/tonioni/pfs3aio> | BSD-4-Clause, in `pfs3aio.LICENSE` |

This product includes software developed by Michiel Pelt.

The one file serves every DOS type the Professional File System uses, `PFS\3`
and `PDS\3` among them, so it is recorded in a drive's table once for each of
those types the drive's partitions ask for.

No other handler is shipped. The Smart File System and the FastFileSystem of
AmigaOS 3.1.4 and later are supplied by the person using the application,
through **Tools → Filing-system handlers**, and are kept in the directory named
by `AMIGA_FILE_FORGE_HANDLER_DIR`, which defaults to
`~/.config/amiga-file-forge/handlers`. A handler supplied there takes the place
of the one shipped here for the same filing system.
