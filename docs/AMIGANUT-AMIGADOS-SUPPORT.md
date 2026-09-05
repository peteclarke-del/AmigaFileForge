# The Amiganut AmigaDOS engine

Amiga File Forge does not depend on an external filing-system package. The
engine ships in this repository as `amiganut/`, needs nothing beyond the Python
standard library, and is versioned with the application.

## What it implements

One class covers `DOS\0` to `DOS\5`, because the variants differ in three
decisions rather than three implementations:

| DOS type | Name | Data blocks | Name hashing | Directories | Name limit |
| --- | --- | --- | --- | --- | ---: |
| `DOS\0` | OFS | 24-byte header per block | ASCII | hash table | 30 |
| `DOS\1` | FFS | whole 512-byte blocks | ASCII | hash table | 30 |
| `DOS\2` | OFS International | 24-byte header per block | Latin-1 folding | hash table | 30 |
| `DOS\3` | FFS International | whole 512-byte blocks | Latin-1 folding | hash table | 30 |
| `DOS\4` | OFS Directory Cache | 24-byte header per block | Latin-1 folding | hash table plus cache | 30 |
| `DOS\5` | FFS Directory Cache | whole 512-byte blocks | Latin-1 folding | hash table plus cache | 30 |

`DOS\6` and `DOS\7` (long filenames, 107 characters), `PFS\3`, `SFS\0` and
`SFS\2` are identified and reported, but opened read-only: this build will not
write structures it cannot verify.

An AmigaDOS directory is a hash table with overflow chains, so it has no fixed
entry count. The only real limit is free blocks, and that is what the pane
reports rather than an invented ceiling.

## What it does

- Creates 880 KiB and 1.76 MiB floppies, and hard-drive partitions of any size.
- Reads and writes files of any length, following the file-header and
  extension-block chains.
- Allocates blocks outwards from a file's own header, which is what keeps a
  volume readable at speed on real hardware.
- Preserves protection bits, comments and datestamps across a copy.
- Defragments in place, rewriting only the files whose data blocks are not
  already contiguous.
- Validates every structure: block checksums, hash chains, the bitmap's
  accounting against the files that actually exist, and blocks claimed twice.
- Reads and writes the Rigid Disk Block, its partition chain and its filesystem
  headers, so one `.hdf` can present several mountable volumes.
- Decodes a Kickstart ROM's header, declared size, reset checksum and every
  `$4AFC` resident tag.

## Hardfiles and geometry

An image with a Rigid Disk Block describes itself. A *hardfile* does not: it is
a bare volume, and the host has to be told how many surfaces and sectors to
pretend it has. Emulators keep that in a `.geo` sidecar of `key=value` lines,
and Amiga File Forge reads and writes the same file, so an image prepared for
FS-UAE opens here without being described twice.

The sidecar's surfaces, sectors and cylinders are chosen so that they multiply
back to exactly the file's size. An emulator that finds a mismatch refuses the
hardfile rather than guessing, so an approximate shape would be worse than
none.

## The workbench boundary

`app/ffs_capabilities.py` is the small adapter between the engine's generic
mount surface and the pane's constraints. Keeping it in one module stops
format-specific checks spreading through transfer and presentation code.

`app/amiganut_internals.py` is the single place the workbench reaches into the
engine's bulk-copy implementation, and `tests/test_component_boundaries.py`
asserts that no other module does.

## Verification

The generated-media matrix creates, writes, reads, validates and reopens every
writable DOS type on both floppy geometries. `tests/test_amiganut_internals.py`
checks that the borrowed bulk-copy names still exist. Container tests verify
that the engine reaches the runtime image and that no Amiga firmware is shipped
or downloaded.

Run the engine directly to check something without the web application:

```bash
python3 -m amiganut identify --as json disk.adf
python3 -m amiganut ls drive.hdf:
python3 -m amiganut validate drive.hdf
python3 -m amiganut kickstart kick31.rom
```
