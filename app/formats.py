"""File-format declarations shared by upload and image handling.

Extensions are a hint, never a decision: every opened image is identified from
its bytes. What these sets do is decide which probe runs first and which files
the browser offers, so a 400 MiB hard-drive file is not scanned for a
Kickstart ROM before it is scanned for a partition table.

``.adf`` appears in both the OFS and FFS sets on purpose. An Amiga floppy
image carries no hint of which filing system formatted it; only its boot block
knows, and that is read at open time.
"""

#: Double-density and high-density floppy images.
OFS_EXTENSIONS = {".adf", ".adz"}

#: A partitioned hard-drive file: one container, many mountable volumes.
HDF_EXTENSIONS = {".hdf", ".hdz", ".rdsk"}

#: DiskMasher archives: a whole disk, compressed track by track.
DMS_EXTENSIONS = {".dms"}

#: Gotek and HxC track images.
HFE_EXTENSIONS = {".hfe"}

#: SuperCard Pro flux captures.
SCP_EXTENSIONS = {".scp"}

#: SPS preservation captures. Reading one needs the SPS decoder library, which
#: is looked for at run time rather than shipped.
IPF_EXTENSIONS = {".ipf"}

#: Kickstart, cartridge and expansion ROM images.
ROM_EXTENSIONS = {
    ".rom",
    ".kick",
    ".a500",
    ".a600",
    ".a1200",
    ".a3000",
    ".a4000",
    ".cd32",
    ".rom0",
    ".rom1",
    ".rom2",
    ".rom3",
}

#: Single-volume AmigaDOS media, from an 880 KiB floppy to an RDB-less hardfile.
FFS_EXTENSIONS = {
    ".adf",
    ".adz",
    ".bin",
    ".dsk",
    ".hda",
    ".hdf",
    ".img",
    ".raw",
}

#: The geometry sidecar that accompanies an RDB-less hardfile.
GEOMETRY_EXTENSIONS = {".geo"}
