# HFE, SCP and HxCFE guide

Amiga File Forge uses the official HxCFloppyEmulator command-line converter,
normally invoked as `hxcfe`, to open, create and save HFE floppy images. HFE is
a track and bit-cell container. OFS and FFS are the filing systems stored in
the sectors represented by those tracks.

![Creating an HFE-wrapped Amiga floppy](images/hfe-create.png)

## What is included

The Docker image and native Debian/Ubuntu packages contain an
architecture-native HxCFE executable, `libhxcfe.so` and `libusbhxcfe.so`. The
build is made from the revision pinned in `tools/build-hxc-runtime.sh`. Users of
an official Amiga File Forge image or package do not need to install a separate
HxC package.

The native package keeps HxCFE private to the application:

```text
/opt/amiga-file-forge/native/bin/hxcfe
/opt/amiga-file-forge/native/lib/libhxcfe.so
/opt/amiga-file-forge/native/lib/libusbhxcfe.so
/opt/amiga-file-forge/native/share/licenses/HxCFloppyEmulator-COPYING
```

The application launcher supplies the private library path. Running the binary
directly for diagnosis therefore requires:

```bash
LD_LIBRARY_PATH=/opt/amiga-file-forge/native/lib \
  /opt/amiga-file-forge/native/bin/hxcfe -help
```

The Docker image installs the same executable and libraries under
`/usr/local/bin` and `/usr/local/lib`.

## Open an HFE image

1. Select **Open image** or drag an `.hfe` file onto a pane.
2. Amiga File Forge validates the HFE signature, revision, track count and side
   count before invoking HxCFE.
3. HxCFE reports the track structure and decodes the sector stream to a private
   working image.
4. Amiga File Forge identifies the decoded filesystem as OFS or FFS and opens
   it with the applicable catalogue and filename rules.
5. Read the warning at the top of the pane. It states the HFE version, track
   count, side count, bitrate and whether the image is editable.

An HFE is not considered successfully opened until its decoded OFS or FFS
catalogue can be listed in the pane. HFE recognition without a browseable Amiga
filesystem is reported as a conversion or filesystem error, not as a blank
disk.

The original HFE is retained unchanged throughout the session. Filesystem edits
are made to decoded working sectors, not directly to the selected host file.

## Editable and read-only images

An ordinary HFE v1 image is editable when HxCFE decodes a clean sector image
and the contained OFS or FFS geometry is supported. File editing, access
changes, compaction and cross-image transfers then follow the rules of the
decoded filesystem.

Amiga File Forge opens these images read-only:

- HFE v2 or v3 images
- images for which HxCFE reports bad sectors
- images using weak bits, variable timing, protection data or another track
  feature that cannot be represented safely by a sector filesystem editor

Read-only HFE images can still be browsed, analysed and used as a source for
file extraction. This prevents an ordinary catalogue edit from silently
destroying non-sector data.

## Create a new HFE image

Choose **File → New → New Image**, then select one of these formats:

- HFE OFS DS/DD, equivalent to an 880 KiB ADF
- HFE FFS DS/DD, equivalent to an 880 KiB ADF
- HFE FFS International DS/DD
- HFE OFS high density, 1760 KiB
- HFE FFS high density, 1760 KiB

Amiga File Forge first creates the corresponding formatted sector image, then
asks HxCFE to encode it as HFE. The new pane behaves as OFS or FFS while its
format badge remains HFE.

## Save an edited HFE image

Saving is deliberately stricter than ordinary sector-image export:

1. Amiga File Forge asks HxCFE to encode the edited sectors as a new HFE, using
   the original HFE as a track-layout reference where applicable.
2. It asks HxCFE to decode the candidate output again.
3. It compares the complete decoded result with the private working sectors.
4. A byte mismatch blocks the download and leaves the original HFE untouched.
5. A successful result is included in the normal timestamped save package with
   its generated technical README.

The progress dialog remains open while encoding and verification run. Do not
close the application until it reports that the package is ready.

## Transfers and physical disks

An HFE can be extracted into a drawer on any writable volume. A sector image
stores only sectors,
not track timing, so weak-bit and protection information cannot be carried into
the destination. Amiga File Forge reports that loss before copying.

An HFE containing OFS or FFS can be copied into another writable filesystem by
extracting its files. Protection bits and comments are retained where the
destination format supports them.

Greaseweazle can write HFE track data to a physical floppy. Its normal sector
read-back verification is not available for HFE, so the application reports an
unverified write and requires the disk to be tested on suitable hardware. See
the [physical floppy guide](PHYSICAL-FLOPPY-GUIDE.md).

## Open an SCP flux capture

An `.scp` file is a SuperCard Pro track and bit-cell capture, most often
produced by a separate flux-reading tool talking to Greaseweazle or SuperCard
Pro hardware. No device is required to inspect an existing capture. Amiga File
Forge opens `.scp` files exactly the way it opens `.hfe` files:

1. Select **Open image** or drag an `.scp` file onto a pane.
2. HxCFE decodes the flux capture to a private working sector image.
3. Amiga File Forge identifies the decoded filesystem as OFS or FFS and opens
   it with the applicable catalogue and filename rules. It runs the complete
   filesystem validator before presenting the pane, so a plausible root header
   cannot hide a broken directory tree.
4. HxCFE re-encodes the decoded sectors back to SCP and decodes that result
   again. If it does not match byte-for-byte, the capture opens read-only; it
   can still be browsed and copied from, but not rewritten safely.

The original SCP is retained unchanged throughout the session, and an editable
capture is saved the same way an editable HFE is: HxCFE encodes the edited
sectors back to SCP, decodes that candidate again, and blocks the download if
any byte differs from the working sectors.

Some Greaseweazle SCP captures expose an HxCFE raw-writer edge case in which
the final blank 256-byte sector is omitted even though all 16 sectors are
reported on the last track of a DS/DD disk. Amiga File Forge recognises only that exact
one-sector-short form at the end of a known floppy geometry, restores the blank
tail sector, then validates the complete image. It never pads a missing sector
in the middle of a track. The supplied 80-track, double-sided sample
therefore opens as a 901,120-byte `.adf`, exposes its complete nested directory
tree, and exports directly through **File → Export as…**.

In the native Linux edition, **Tools → Write physical floppy** can also send an
SCP capture to a connected Greaseweazle. As with HFE, the flux-level write
cannot use automatic sector verification and is reported as unverified.

## Export an image to another format

**File → Export as…** converts the current image's decoded sectors into
another compatible container, independent of how the image was opened. It
downloads a single converted file, separately from the timestamped **Save**
ZIP, and never changes the working image.

The same action has an **Export** control in every pane header, between
**Save Image** and **Refresh View**. It stays visible but greyed out when the
open media has no compatible target, and its tooltip gives the reason: a
Hardfile HDA and GEO pair carries geometry no sector or flux container can
represent, and HDF, DMS, ROM, Kickstart ROM and archive panes have nothing to convert.

Available targets depend on the image's filing system and geometry:

- Every OFS or FFS image can export its plain sector image as an `.adf`,
  whichever filing system formatted it, or as the same image gzipped to `.adz`.
  This is useful when an image was opened from an HFE or SCP container and a
  plain sector image is wanted for an emulator.
- DS/DD and high-density 3.5-inch floppies can also export as an HFE
  or SCP flux container, using the same encode-then-verify check used when
  saving an edited HFE or SCP.

The 5.25-inch geometry, RDB hard drives and Hardfile HDA/GEO pairs only offer
the native sector export, because HxCFE has no blank flux layout for them and a
hard drive's geometry is not something a flux container can represent.

## Troubleshooting

### “The HFE conversion engine is not installed”

Official 1.1.1 Docker images and native packages bundle HxCFE. If this error
appears, confirm that the package is current and that all runtime files are
present:

```bash
dpkg-query -W amiga-file-forge
test -x /opt/amiga-file-forge/native/bin/hxcfe
test -f /opt/amiga-file-forge/native/lib/libhxcfe.so
test -f /opt/amiga-file-forge/native/lib/libusbhxcfe.so
```

Reinstall the matching Debian or Ubuntu package if a file is absent. A source
checkout does not create this private runtime until
`tools/build-linux-package.sh` is run. The Docker build constructs it
automatically.

### HxCFE cannot load its libraries

Use the Amiga File Forge launcher rather than invoking the private binary. For
a direct diagnostic invocation, supply `LD_LIBRARY_PATH` as shown above.

### The image opens read-only

Read the pane warning. HFE v2/v3, bad-sector and advanced track layouts are
protected intentionally. Copy readable files to a new ADF, ADZ, FFS or clean
HFE v1 image instead of forcing a lossy rewrite.

### Conversion times out or saving fails verification

Keep the original image. Check available temporary disk space and application
logs, then retry once. A verification failure means the re-encoded sectors did
not exactly match the edited filesystem, so Amiga File Forge correctly withheld
the output.

## Build and licence boundary

`tools/build-hxc-runtime.sh` is the single build path used by Docker and native
packages. It checks out the pinned upstream revision, builds the HxCFE command
line target, stages the executable and shared libraries, installs the upstream
GPL-3.0 licence and executes a runtime smoke test. This avoids different HxCFE
implementations drifting between the web and desktop editions.

The exact pinned revision and redistribution boundary are recorded in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
