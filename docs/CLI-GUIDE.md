# Headless CLI and deterministic recipes

Amiga File Forge includes a supported command-line interface for repeatable
image work, build servers and collection maintenance. It calls the same disk,
validation, manifest, comparison and guarded-patch services as the web
application. The CLI does not reproduce filesystem rules in a separate tool.

The entry point is:

```bash
python -m app.cli --help
```

The complete Docker image contains the Amiga filesystem and conversion tools
required by the commands. A source checkout without its Python and native
dependencies can display help, but it cannot open or safely dry-run real
images.

## Run it in Docker

Create a host directory for input and output files, then mount it at `/media`:

```bash
mkdir -p media
docker compose run --rm \
  -v "$PWD/media:/media" \
  amiga-file-forge \
  python -m app.cli validate /media/game.adf
```

This one-off container uses the built application image and leaves the web
service alone. Files written below `/media` appear in the host `media`
directory. Use absolute container paths in recipes and commands.

## Output contract

Progress and human-readable phase messages go to standard error. Standard
output contains one JSON document with this stable envelope:

```json
{
  "format": "amiga-file-forge-cli-result",
  "version": 1,
  "command": "validate",
  "status": "ok",
  "exitCode": 0,
  "dryRun": false,
  "result": {}
}
```

The process exit codes are part of the version 1 interface:

| Code | Status | Meaning |
| ---: | --- | --- |
| 0 | `ok` or `planned` | The operation completed, or a dry-run completed without writing |
| 2 | `usage-error` | Command syntax or a required argument is wrong |
| 3 | `validation-failed` | The image, requested operation or target rule is invalid |
| 4 | `input-error` | A named source, descriptor or patch cannot be found |
| 5 | `identity-mismatch` | A recipe source no longer matches its recorded size or SHA-256 |
| 6 | `operation-failed` | A filesystem, conversion or host I/O operation failed |

Argument errors use the same JSON envelope on standard output and put the
short usage line on standard error. Scripts should test the numeric exit code
and may then inspect `status`, `result.error` and `result.errorType`.

## Commands

### Create an image

```bash
python -m app.cli create --format adf --title WORK --output /media/work.adf
```

`--format` accepts the same identifiers as the web creation service: the
DS/DD DOS types `adf`, `adf-intl`, `adf-dc`, `ffs`, `ffs-intl` and
`ffs-dc`, their high-density counterparts `adf-hd`, `ffs-hd` and `ffs-hd-dc`,
`hdf`, `hardfile`, `ffs-hard`, `ffs-physical`, `rom`, `kickfs` and the
supported HFE wrappers. Capacity is required
only for formats whose size is genuinely selectable. ROM commands can also
set bank size, total size, platform, layout and template.

### Finalise and save an existing image

```bash
python -m app.cli save /media/scsi0.hda \
  --descriptor /media/scsi0.geo \
  --target-hardware hardfile \
  --output /media/scsi0-ready.hda
```

Save runs the hardware finalisation path before copying bytes. A Hardfile HDA
output automatically receives a matching GEO with the same stem. HFE output is
re-encoded and verified when the source was opened as an editable HFE. Existing
outputs are rejected unless `--force` is explicit.

### Inspect and validate

```bash
python -m app.cli manifest /media/collection.hdf \
  --output /media/collection-manifest.json

python -m app.cli validate /media/collection.hdf --partition 1
```

The manifest contains filesystem records, Amiga metadata and hashes, plus the
deterministic logical fingerprint used by recipes and patches. A hard drive can
be validated one partition at a time. Omit `--partition` to validate the whole
container.

Build the same versioned compatibility report used by the browser from a JSON
array of proposed changes:

```bash
python -m app.cli preflight /media/work.adf \
  --changes /media/proposed-changes.json \
  --source-kind ffs --target-kind ofs \
  --operation copy \
  --output /media/compatibility-report.json
```

Each proposed row can supply name, source, type, load, execute, access and
filetype. The report records per-item conversions and losses, blocking
findings, the target profile and `canProceed`. `import-file --dry-run` embeds
this same report under `result.compatibility`.

### Import one host file

```bash
python -m app.cli import-file /media/work.adf /media/PROGRAM \
  --destination Games/PROGRAM \
  --protection '----r-e-' --comment 'Reviewed copy' \
  --output /media/work-with-program.adf
```

Use `--partition` for a volume on a hard drive. `--protection` accepts either
form: the eight letters `List` prints, such as `----rwed` or `----r-e-`, or the
raw long in hexadecimal, written `05`, `&05` or `0x05`. The four low bits are
stored inverted, so a letter shown means the operation is *permitted*; the
letters are the safer form to type for exactly that reason.

`--comment` holds up to 79 characters, which is the AmigaDOS limit.
`--filetype` sets the Workbench icon type in the entry's companion `.info`
file, and is refused where the destination has nowhere to keep one.

There is deliberately no load or execution address. AmigaDOS records none: a
load file carries its own hunk header and the loader reads that. Filename,
drawer and metadata restrictions are enforced by the destination filesystem
service.

### Convert a DMS archive

```bash
python -m app.cli convert /media/program.dms \
  --format adf --output /media/program.adf
```

This command deliberately means DMS to ADF or ADZ. It does not claim a generic
sector-image conversion. Every track is written back at the cylinder it came
from, so the output is the disk the archive was made from, exactly as in the
browser. A track this build cannot decompress stops the conversion rather than
producing a disk with a hole in it.

### Compact an image

```bash
python -m app.cli compact /media/work.adf \
  --order name --output /media/work-compact.adf
```

A hard drive is compacted one partition at a time with `--partition`. Kickstart ROM is already rebuilt into storage order
after each edit. DMS has no sector-compaction operation, so that request is
rejected even when a member would be editable in the browser's dms project.

### Compare images and create patches

```bash
python -m app.cli compare /media/before.adf /media/after.adf \
  --output /media/comparison.json

python -m app.cli patch-create /media/before.adf /media/after.adf \
  --output /media/change.affpatch.zip

python -m app.cli patch-apply /media/before.adf /media/change.affpatch.zip \
  --output /media/patched.adf
```

Comparison uses logical records and exact fingerprints. Patch creation includes
only required payloads. Patch application verifies the base fingerprint,
layout, canonical operation plan and every payload before writing, then checks
the complete candidate fingerprint. Paired candidates use `--descriptor` for
the base and `--candidate-descriptor` for the candidate.

## Dry-run

Add `--dry-run` to every mutating command. No output image or patch is created.
The returned `status` is `planned`, `dryRun` is true and `result` contains the
resolved source identity, decisions and intended output where applicable.
Dry-run opens a private disposable copy and performs the real requested
mutation there, then discards it instead of writing the chosen output. This
catches capacity, catalogue and format errors that a descriptive plan alone
would miss. Patch application performs the complete guarded preflight,
including payload hashes, without applying it.

## Versioned recipes

`create`, `save`, `import-file`, `convert` and `compact` accept
`--recipe-out`. After a successful operation, the file records:

- recipe format and version;
- exact physical size and SHA-256 for every input;
- the image's logical fingerprint where an image was opened;
- every non-secret action decision;
- the target-hardware and raw-ROM interpretation choices used to open inputs;
- the chosen output and hashes of generated files.

Run the recipe by mapping each source alias to a current path:

```bash
python -m app.cli recipe-run /media/import.affrecipe.json \
  --source image=/media/original.adf \
  --source payload=/media/PROGRAM \
  --output /media/rebuilt.adf
```

The rebuild stops with exit code 5 if an input's bytes have changed. The open
image's logical fingerprint is checked as a second guard. Paths are supplied at
run time so a recipe can move between computers without weakening its identity
checks. Secrets, browser session identifiers and private working paths are not
stored as execution authority.

After the rebuild, every generated primary and companion file is checked
against the size and SHA-256 recorded by the completed workflow. A mismatched
result also returns exit code 5 instead of being reported as a successful
deterministic rebuild.

Version 1 recipes execute create, import-file, compact, DMS-convert, guarded
patch application and final save decisions. A recipe produced by a newer application
version is rejected until its schema is supported rather than guessed.

### Export a completed GUI workflow

Open **Workbench → Portable project**, choose an open image and select
**Export workflow bundle**. The downloaded `.affrecipe.zip` contains:

- `workflow.affrecipe.json`, the versioned recipe and every expected output
  hash;
- `changes.affpatch.zip`, the guarded logical changes from the earliest
  retained pre-change checkpoint to the current image;
- `README.md`, the exact base and optional GEO identities plus a ready-to-edit
  replay command.

The original image is deliberately not copied into the bundle. Extract it and
map `image` to the recorded base and `changes` to the bundled patch:

```bash
python -m app.cli recipe-run workflow.affrecipe.json \
  --source image=/media/original.adf \
  --source changes=changes.affpatch.zip \
  --output rebuilt.adf
```

For Hardfile, also map the companion with
`--descriptor image=/media/original.geo`. Replay verifies both physical input
hashes, the base logical fingerprint, the patch payloads and the final output
hashes. The recipe retains the chosen hardware profile, target validation and
accepted compatibility reports as descriptive decisions, but never browser
ownership tokens or private server paths.

An edited legacy session with no retained pre-change checkpoint is rejected
rather than exported as a false reconstruction. Save it, create a named
checkpoint and use that as the base for subsequent recorded changes. DMS and
HFE workflow export remains unavailable until the container-level rebuild can
be proved lossless.

HFE operations use the same bundled HxCFloppyEmulator command-line converter
(`hxcfe`) as the graphical workbench. Docker and native release packages carry
the executable and its libraries. See the [HFE, SCP and HxCFE guide](HFE-HXC-GUIDE.md)
for supported layouts and the encode, decode and byte-comparison save gate.

## Safety notes

- Inputs are copied into an isolated temporary work directory. The source file
  is never edited in place.
- Mutating commands require a separate output and reject existing files unless
  `--force` is given.
- HDA and GEO are treated as one hardware image. Keep both files together.
- The CLI does not bypass ambiguous DMS reconstruction, protected HFE,
  composite Kickstart ROM or incomplete geometry rules. Proof-gated DMS member edits
  currently use the browser dms-project review rather than a headless command.
- Keep the JSON result with automated build logs. It records the exact failure
  category even when the filesystem utility writes additional diagnostics to
  standard error.
