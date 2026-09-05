# Hardware deployment assistant

Amiga File Forge can turn an open image into a checked directory tree for a
Gotek, FastFileSystem, Hardfile, PiStorm or AmigaOS host. Open the image, apply the target
hardware profile, then choose **Tools → Build hardware deployment**.

![The deployment assistant showing a validated Gotek layout](images/hardware-deployment-assistant.png)

The assistant is separate from **Save image**. Save creates the canonical
archive of the working image. Deployment creates a hardware-specific package
whose filenames and directories match the selected target. It never writes
directly to an SD card, USB device or physical disk.

## Safety model

Validation and packaging use a sparse private snapshot. An HDA image is
hardware-finalised, checked and hashed in that snapshot, so opening the
assistant does not advance the live FFS disc ID, alter a directory sequence
or clear the pane's changed state. The reviewed source revision is recorded in
the plan. If the image changes before **Download deployment ZIP** is selected,
the server rejects the stale plan and requires another validation.

Every package contains:

- the exact target directory tree;
- `README.md`, generated from the chosen target and applied hardware profile;
- `Deployment/manifest.json`, with source revision, paths, sizes and SHA-256
  values;
- `Deployment/compatibility-report.md`, using the same compatibility schema as
  cross-format copies.

Blocking findings disable download. Warnings remain visible and are copied to
the package so a manual hardware requirement cannot be forgotten after the
browser closes.

## Target layouts

### Gotek and FlashFloppy

Supported floppy images can be packaged in Native mode, retaining useful
filenames, or Indexed mode. Indexed mode creates names beginning at the chosen
`DSKA0000` position and includes an `FF.CFG` which selects indexed navigation.
A Gotek package holds floppy images, so a hard drive is not offered for one:
its partitions are not floppies and inventing them as such would mislead.
Copy the contents of `GOTEK-USB` to
the USB root.

The assistant does not generate `HXCSDFE.CFG`. That file contains physical
directory-order state maintained by the HxC selector workflow, and creating a
lookalike from filenames would not be safe.

### HDF on an SD card

An open hard-drive image becomes `SD-CARD/AMIGA.HDF`. Copy `AMIGA.HDF` to the
FAT root of the card. The profile check warns when no mass-storage interface or
FastFileSystem build is declared. STACK, ROM and machine compatibility remain
part of image audits, not something deployment silently changes.

Both shapes of hard drive are accepted here, because the package copies the
file as it stands and never reads inside it. A drive carrying a Rigid Disk
Block declares its own geometry, so the receiving side needs no configuration.
A bare hardfile does not, and the plan says so: the adapter or firmware has to
be told the heads, sectors and cylinders, or be one that assumes them. If you
would rather the file described itself, convert it first with
**File → Export as… → Partitioned drive with a Rigid Disk Block**.

### Hardfile

A matched HDA and GEO pair becomes:

```text
SD-CARD/
└── Hardfile0/
    ├── scsi0.hda
    └── scsi0.geo
```

The disposable HDA copy is normalised to the GEO geometry, its directory block
checksums and bitmap are checked, and both files are hashed before the ZIP is
enabled. Merge the `Hardfile0` directory into a backed-up card. Do not rename
one half of the pair or combine files from different saves.

### PiStorm

Both PiStorm boards take the same package. Which one a machine can accept is
decided by its CPU socket, so the workbench offers the original 68000-socket
board for the A500, A500+, A600 and A2000, and the PiStorm32 for the A1200
alone. A profile that names the wrong one for its machine is reported before
the package is built.


An HDF uses `SD-CARD/AMIGA.HDF`. A Hardfile pair uses the `Hardfile0` layout
above. The package is a merge tree: preserve the working Pi firmware,
`PiStorm.cfg`, saved state and unrelated target directories already on the
card. Amiga 600 profiles are warned when they do not include AP5 or another
compatible 1 MHz bus route.

### AmigaOS and Amiga 4000 hosts

A supported AmigaDOS image is placed below `AMIGA-HOST/Images`. The assistant
can validate the image and its companion metadata, but it cannot infer the
geometry or controller configuration of every emulator, expansion card or storage
adapter. The generated README therefore marks attachment as a manual step.
Run the target filing-system checks before enabling application writes.

## Recommended workflow

1. Apply the exact hardware profile in **Workbench**.
2. Save or checkpoint important edits.
3. Choose **Tools → Build hardware deployment** and select the target.
4. For Gotek, choose Native or Indexed mode and the first index.
5. Select **Validate layout**. Review target paths, byte totals, SHA-256 values,
   profile warnings and installation steps.
6. Resolve blocking findings. Revalidate after changing either the image or
   target options.
7. Download the ZIP and extract it to a temporary host directory.
8. Back up the known-good physical medium, then merge the generated tree.
9. Perform the catalogue, read, write and reboot checks listed in its README.
10. Keep the previous medium unchanged until those checks pass.

## Cross-format preflight

Drag and drop, Cut/Copy/Paste, **File → Insert File**, folder import and Online
Library installation use the same versioned compatibility report before a
cross-format batch starts. The report shows each proposed target name, load and
execute metadata, directory loss, filetype loss, truncation and collisions.
Nothing is copied while that review is open. Online Library keeps the review
inside its search dialog and requires a second, explicitly reviewed Install
action.

JSON and Markdown exports are available from the full review dialog. The
manual **Analyse → Dry-run selected items** command remains useful when a
report is needed without starting a transfer.

## Limits

- Deployment does not format removable media or overwrite an attached device.
- AmigaOS controller geometry remains a documented manual decision.
- Whole-HDF emulator mounting is available for Amiga 600 FastFileSystem profiles through
  the bundled FS-UAE whole-drive adapter. The deployment ZIP itself is
  still a generated directory tree and never writes a removable card.
- Ambiguous DMS recordings, unsupported HFE track layouts and ambiguous
  AmigaDOS media retain their read-only or rejected behaviour.
