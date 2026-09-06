# Amiga File Forge documentation

This directory is the technical handbook for Amiga File Forge. Start with the
task you need to complete, then follow the linked guide for the details. The
in-app **Help** handbook covers the same workflows with controls and terminology
that match the running frontend.

![The illustrated in-app handbook](images/in-app-help.png)

## Choose a guide

| I want to... | Read this |
| --- | --- |
| Install, update, back up or troubleshoot the Docker service | [Installation and operations](INSTALLATION.md) |
| Install or develop the native Linux application | [Linux desktop application](LINUX-DESKTOP.md) |
| Build for Windows, macOS or an RPM-based Linux | [Windows, macOS and RPM builds](CROSS-PLATFORM.md) |
| Read or write real disks with Greaseweazle or a floppy controller | [Physical floppy guide](PHYSICAL-FLOPPY-GUIDE.md) |
| Open, create, edit, verify or troubleshoot HFE or SCP flux images, or export an image to another compatible format | [HFE, SCP and export guide](HFE-HXC-GUIDE.md) |
| Build a checked Gotek, FastFileSystem, Hardfile, PiStorm or AmigaOS media tree | [Hardware deployment assistant](HARDWARE-DEPLOYMENT-GUIDE.md) |
| Review the mandatory web and desktop parity rules | [Web and desktop platform contract](PLATFORM-CONTRACT.md) |
| Understand every supported media family and normal workflow | [Main project handbook](../README.md) |
| Edit BASIC, command files, machine code, archives or binary data | [File editor and code analysis](FILE-EDITOR-GUIDE.md) |
| Inspect, preserve or edit protection bits, comments and datestamps | [Amiga file catalogue metadata](FILE-METADATA-GUIDE.md) |
| Inspect, compare, build, patch or program ROM and Kickstart ROMs | [ROM image handbook](ROM-GUIDE.md) |
| Build and validate a release | [Release checklist](RELEASE-CHECKLIST.md) |
| Review the stable 1.1.1 release | [Amiga File Forge 1.1.1 notes](releases/1.1.1.md) |
| Contribute code or documentation | [Contribution guide](../CONTRIBUTING.md) |
| Understand maintainership and project decisions | [Project governance](../GOVERNANCE.md) |
| Report a vulnerability | [Security policy](../SECURITY.md) |
| Check dependency, emulator and firmware licence boundaries | [Third-party notices](../THIRD_PARTY_NOTICES.md) |
| Ask for support or report conduct concerns | [Support](../SUPPORT.md) and [code of conduct](../CODE_OF_CONDUCT.md) |
| Review validation evidence | The CI run on the released commit; see the [release checklist](RELEASE-CHECKLIST.md) |
| Review completed and outstanding product improvements | [Product backlog](BACKLOG.md) |
| Audit the emulator firmware shipped in the image | [Firmware notes](../firmware/README.md) |
| Review the Amiganut AmigaDOS integration and format limits | [Amiganut AmigaDOS support](AMIGANUT-AMIGADOS-SUPPORT.md) |
| Automate creation, validation, imports, comparison and patching | [Headless CLI and deterministic recipes](CLI-GUIDE.md) |
| Catalogue owned images and find cross-image duplicates or missing titles | [Private collection catalogue](COLLECTION-GUIDE.md) |
| Find possible lives, energy, timer or collision modifications in game code | [Cheat-candidate analysis](CHEAT-ANALYSIS-GUIDE.md) |
| Complete a task while the application is open | Select **Help** in the application header |

## Capability map

| Media or feature | Browse | Edit | Create | Transfer | Analyse and repair | Export as | Save package |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OFS ADF and ADZ | Yes | Yes, including protection bits, comment and datestamp | Yes | Files and complete images | Directory, protection and capacity checks | Sector image, HFE and SCP | Image, metadata and README |
| Partitioned hard drives, HDF with a Rigid Disk Block | Yes, every partition it chains to | Yes, inside any mounted partition | Yes, with one FFS International partition | Files, drawers and complete images | Rigid Disk Block, partition bounds, launcher, STACK and access checks | One partition as a sector image | Complete drive and README |
| SPS preservation captures, IPF | Yes, when the SPS decoder library is installed | No, the format records a physical read | No | Recovered sectors into writable media | Per-sector recovery report | Sector image | Working image and README |
| FFS images in every DOS type, plain, international and directory cache | Yes, including drawers | Where the detected layout is writable, including protection bits, comment and datestamp | Yes, for supported layouts | Files, directories and images | Filesystem, bitmap, launcher and compatibility checks | Sector image; HFE and SCP for the 3.5-inch densities | Image, metadata and README |
| Hardfile HDA and GEO | Yes, including deep trees | Yes, including protection bits, comment and datestamp | Yes | Files, trees and extracted disks | Geometry, map, directory and installed-software checks | No, the HDA and GEO geometry has no single-file equivalent | HDA, GEO and README |
| HDF, HDD, IMG, RAW and BIN AmigaDOS media | Yes | Where the detected layout is writable | Selected layouts | Files and directories | Geometry, map and target-profile checks | No | Image and README |
| DMS archives | Yes, as a decoded hierarchy | Same-length proven members | No | Extracted files into writable media | Physical chunks, reconstruction proof and structural comparison | No | Rebuilt source or converted media |
| HFE floppy images | Yes | Clean sector HFE v1 only | Yes | Files and images | Track and sector capability checks | Sector image, HFE and SCP | HFE and README |
| SCP flux captures | Yes, when HxCFE decodes an OFS or FFS filesystem | Where the capture re-encodes byte-for-byte | No | Files and images | Round-trip re-encode verification | Sector image, HFE and SCP | SCP and README |
| ROM images | Banks, headers, commands and regions | Bytes, project data and supported structures | Yes | Banks and programmer files | Commands, help, code, data, checksums and compatibility | No | ROM, project JSON and README |
| Kickstart ROM data ROMs | Files and directories | Yes | Yes | Files and directories | Structure and capacity checks | No | ROM, project JSON and README |
| ZIP and other supported archives | Yes, as a hierarchy | Extract, inspect and edit supported members | No | Members into writable media | Type and metadata inspection | No | Exported member or destination image |

The table is a navigation aid, not a replacement for format restrictions.
Amiga File Forge rejects geometry, track and filesystem variants it cannot
write safely. Read the warning shown by the application before converting or
repairing unusual media.

## Main workflows

### Work with several images

The workspace starts with one pane and has no fixed pane-count limit. Each pane
is a movable, resizable window with its own open image, current directory,
selection, progress, undo history and hardware profile. Panes can overlap,
snap to workspace sides or corners, minimise to the workspace shelf and restore
their layout after refresh. Dragging between panes
uses the same validation as Cut, Copy and Paste, including the 30-character
AmigaDOS name limit, free-block capacity and metadata conversion.

Cross-format drag, clipboard, File-menu and Online Library batches stop at a
shared compatibility review before the first write. The review records every
target-name conversion and metadata loss using the same exportable schema as
**Analyse → Dry-run selected items**.

### Edit files by content

Double-click a file to open the suitable editor. Tokenised AmigaBASIC opens as
editable source, command files open as scripts, recognised machine code opens
as annotated disassembly, archives open as file hierarchies, and other binary
data opens in the hex editor. The editor includes search and replace, history,
safe save and save-as operations, local export, folding, language help, BASIC
formatting and guarded source transformations. See the
[editor handbook](FILE-EDITOR-GUIDE.md) for the exact save and byte-sync rules.

Open one BASIC or machine-code file and use **Tools → Find cheat candidates**
for a read-only gameplay-state report. It distinguishes strong,
likely and possible evidence, supports purpose filters and links to optional
online identification and specialist references. It does not patch uncertain
code. Proven machine-code changes can be saved as exact-hash guarded patches;
the host-private library matches the complete file hash and original bytes,
then applies through an automatic checkpoint. Runtime observations remain
tester supplied until managed watchpoint correlation is complete. See the
[cheat analysis guide](CHEAT-ANALYSIS-GUIDE.md).

### Identify what a disk is and how it starts

An imported disk arrives with a volume name, a set of files and nothing else.
Amiga File Forge reads that evidence and proposes a title, the file that starts
the software, and the stack that file needs. `DiskMenu` outranks the
`Startup-Sequence`, because it is what the disk's own author wrote to be run;
conventional loader names follow, judged by their actual content rather than
their name alone. Every proposal carries its evidence, and one the evidence
does not support is marked ambiguous so the caller asks rather than writes.

### Test against a hardware profile

The Workbench describes the base machine, filing system, compatible additions,
accelerator state, memory and FastFileSystem ROM build. Analysis and help use
that profile when deciding whether a command, loader or image is appropriate.
A managed FS-UAE session provides launch and debugging paths for every medium
it can genuinely mount; it is the one bundled emulator because a single
portable build covers the whole Amiga range.

![Hardware profile and emulator configuration](images/hardware-workbench-current.png)

Use **Tools → Build hardware deployment** to create a validated Gotek,
whole-drive, Hardfile, PiStorm or AmigaOS directory tree from the open image. The assistant
works on an isolated snapshot, shows exact paths and SHA-256 values, and writes
the installation, verification and rollback procedure into the downloaded
ZIP. See the [deployment guide](HARDWARE-DEPLOYMENT-GUIDE.md).

### Preserve and recover work

Browser-owned sessions are private working copies. Named checkpoints and undo
cover image changes, while workspace restoration reopens panes after an
ordinary refresh. Saving builds a timestamped ZIP only after the image and its
documentation are complete. Each package includes the image, partner and
metadata files where applicable, checksums, target details, warnings and a
generated README.

### Automate a repeatable build

The supported headless CLI exposes image creation, finalisation, validation,
manifest export, host-file import, DMS conversion, compaction, comparison and
guarded patches. Mutating commands have a dry-run
mode with stable JSON status and exit codes. Completed commands can record a
versioned recipe containing exact source hashes and replayable non-secret
decisions. See the [CLI guide](CLI-GUIDE.md).

### Catalogue a collection

The header **Collection** command indexes complete manifests in private local
state. The web edition uses origin-scoped IndexedDB; the Linux desktop edition
uses an atomic, mode-0600 XDG configuration file. It records user-supplied
locations and machines, identifies exact
cross-image content and title variants, maintains a wanted-title list and marks
entries stale when an open image revision changes. Full database backup/import
and a smaller report export remain separate. See the
[collection guide](COLLECTION-GUIDE.md).

## Documentation conventions

- Write direct, factual prose. State the supported operation, its validation
  boundary and the observable failure mode. Do not imply support that has not
  been exercised.
- Use commas, colons, semicolons or separate sentences instead of em dashes.
- Distinguish implemented behaviour, retained test evidence and work that
  still requires hardware or architecture-specific validation.
- Menu paths use **File → Save image** style notation.
- Amiga paths use their native syntax: `/` between components and a bare `:`
  for the volume root, as in `Games/Chuckulus` or `S/Startup-Sequence`. A full
  stop is an ordinary character in a name, never a separator.
- Sizes use KiB, MiB and GiB when describing byte capacity.
- “Working image” means the private server-side copy, not the source selected
  from the local computer.
- “Save” updates the working image. “Export” downloads an individual file.
  “Save image” creates the timestamped download package.
- Screenshots are captured from the current Docker build and should be updated
  whenever the illustrated controls or workflow change materially.

## Keeping the handbook current

Documentation changes are part of feature work. A change is complete when:

1. The main README and the relevant specialist guide describe the behaviour.
2. The in-app handbook uses the same names and restrictions.
3. Configuration, environment variables, ports and persistence rules match the
   Docker files in the repository.
4. Local links and image references resolve.
5. Changed UI screenshots are captured from a clean current build.
6. The release checklist includes any new generated-media or manual test gate.
7. Documentation regression tests pass and the published prose contains no em
   dashes or obsolete pane-count claims.

Do not use files from `samples/` as published documentation assets. That
directory is intentionally excluded from Git, release archives and the Docker
build context.
