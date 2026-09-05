# Reading and writing physical floppy disks

The native Linux edition of Amiga File Forge can send an open floppy image to
a Greaseweazle drive, and capture a physical disk back into a working image. The browser and Docker editions deliberately cannot
access host USB hardware. Image editing remains shared between both editions;
only the final hardware adapter is desktop-specific.

## Supported images

| Image | Write | Read | Automatic verification |
| --- | ---: | ---: | ---: |
| OFS ADF and ADZ | Yes | Yes | Yes |
| FFS ADF, ADS, ADM and ADL floppy images | Yes | Yes | Yes |
| HFE | Yes | Yes | No |
| SCP | Yes | Yes | No |
| One formatted disk selected in an HDF | Yes, as an extracted ADF snapshot | Not applicable | Yes |
| HDF container, HDA/GEO, HDF and other hard disks | No | No | Not applicable |

## Reading a physical disk

Select **Read physical floppy**, choose the connected drive and the capture
format, then read. The capture is written to a private temporary file first and
is only opened as an image pane once Greaseweazle has exited cleanly and left a
usable file behind, so an empty drive or a failed read never becomes a pane you
might mistake for real data. A failed capture removes its partial file.

Choose the capture format to match the intent:

| Format | Captures | Use when |
| --- | --- | --- |
| `adf` | Decoded AmigaDOS sectors | The disk is a standard OFS or FFS floppy |
| `img` | Decoded PC sectors | The disk is an MS-DOS disk an Amiga reads through CrossDOS |
| `ipf` | Preserved flux | The disk is being kept in the SPS preservation format |
| `hfe` | Bitcell image | The disk has non-standard tracks worth keeping |
| `scp` | Raw flux | Preservation, copy protection, or a disk that will not decode |

A sector format decodes while reading and fails on an unreadable track. A flux
capture keeps everything the drive produced, including tracks no filesystem
decoder accepts, so it is the safer choice for a disk of unknown condition or
one you may only get one chance to read. Use `--revs` through the API, or the
revolutions control, to capture several revolutions per track when a disk is
marginal.

Greaseweazle describes HFE and SCP as flux or raw bitcell data, so it cannot
perform its usual sector read-back verification. Amiga File Forge calls this
out before and after the write. Test an HFE- or SCP-derived physical disk on suitable hardware before
depending on it.

Opening, creating and saving the HFE itself uses the HxCFloppyEmulator
command-line converter (`hxcfe`) bundled with Amiga File Forge. That conversion
stage is separate from the optional Greaseweazle hardware write. See the
[HFE, SCP and HxCFE guide](HFE-HXC-GUIDE.md) for the supported track-container
workflow and its byte-comparison save check.

## Using a real floppy controller

A host with an actual floppy controller, such as a Raspberry Pi or a PC with a
drive attached, can read and write disks directly through `/dev/fd0` with no
Greaseweazle hardware. Select the drive and the disk's geometry, then read or
write.

This path is not equivalent to Greaseweazle, and the difference decides which
you should use:

- A floppy controller returns **decoded sectors** at whatever geometry the
  kernel has been told the disk uses. Anything the controller cannot decode
  fails rather than being captured.
- Greaseweazle captures **flux**, so it reads a disk whether or not a
  filesystem decoder accepts it.

AmigaDOS writes a whole track at once in its own MFM encoding, eleven 512-byte
sectors per side on a DS/DD disk, without the per-sector gaps a PC controller
expects. A standard controller cannot decode that, so an Amiga disk is captured
through Greaseweazle; the controller path serves hardware and kernels that have
been told the exact geometry, and the CrossDOS-compatible PC disks an Amiga also
reads. The kernel geometry must already match the disk, normally set with
`setfdprm` from the `fdutils` package or by using a device node such as
`/dev/fd0u1760`. Amiga File Forge checks the
captured length against the chosen geometry and refuses a short or mismatched
read, so a disk the controller could not fully decode is never presented as a
complete image.

Use the controller for ordinary, healthy disks in a standard format. Use
Greaseweazle for anything damaged, copy protected, unusual, or that you may
only get one chance to read.

Writing through the controller erases the disk completely and cannot be undone,
so the write is refused until it is explicitly confirmed, and the image size
must match both a known Amiga geometry and the drive.

Supported geometries are OFS 40 and 80 track, single and double sided; and FFS
S, M, L, D and E.

## Install Greaseweazle

Install the official Greaseweazle host tools so the `gw` command is available
in the desktop session. Follow the project's current installation and Linux
udev instructions:

- <https://github.com/keirf/greaseweazle/wiki/Software-Installation>
- <https://github.com/keirf/greaseweazle/wiki/Supported-Image-Types>

Connect the device and check it outside Amiga File Forge first:

```bash
gw info
```

If `gw info` fails, correct the USB connection, firmware or udev permissions.
Amiga File Forge reports the same diagnostic and does not start a write.

## Write a disk

1. Open a supported image in the native Linux application. At the root of an
   hard drive, open the partition you want to write.
2. Open **Tools** and choose **Write physical floppy**, or right-click the
   image title or coloured format badge and choose the same command.
3. Select Greaseweazle drive A, B, 0, 1, 2 or 3.
4. Insert the destination disk. Confirm that all existing data on it may be
   overwritten.
5. Select **Write and verify**. HFE and SCP instead say **finish unverified**.
6. Keep the device connected while cylinder, head and verification progress is
   shown. **Abort operation** terminates Greaseweazle, but the disk in the
   drive must then be treated as incomplete and rewritten.
7. Keep the disk only after the completion dialog reports verification. For
   HFE or SCP, test it separately because automatic verification is unavailable.

The current working image is finalised, then copied to a private stable
snapshot before `gw write` starts. Further edits cannot change bytes halfway
through a physical write. The source image and its undo history are never
modified by the hardware operation.

## Safety and failure handling

- Commands are executed as argument arrays without a shell. Drive identifiers
  are restricted to the supported connector values.
- A failed probe never starts the motor or writes a track.
- Sector images are not reported as successful unless Greaseweazle prints its
  complete verification confirmation.
- A verification failure, missing confirmation, timeout, cancellation or
  non-zero exit status says that the physical disk may be incomplete.
- A 30-minute watchdog terminates a stalled command.
- Temporary HDF extractions and write snapshots are removed after success,
  failure or cancellation.

## Shared integration module

The UI-neutral implementation is the top-level `amiga_greaseweazle` Python
package. It owns supported suffixes, drive validation, discovery, stable
snapshots, subprocess control, progress parsing and verification policy. It has
no Flask, GTK or Nautilus dependency, so the companion `nautilus-amigafs`
project can consume the same module rather than maintaining a second hardware
implementation.
