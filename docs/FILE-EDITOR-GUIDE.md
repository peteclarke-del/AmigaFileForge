# File editor and code analysis handbook

This handbook covers the file-level editors in Amiga File Forge. It describes
what the application proves from the bytes, what it infers from the active
hardware profile, and where it deliberately stops. The editor is intended for
maintenance, inspection and controlled changes inside a working image. It is
not a source-level debugger or a substitute for testing on the target machine.

Return to the [documentation index](README.md) for installation, media-format,
ROM, firmware and release references.

## Safety model

Opening a file does not modify it. Editable source remains local to the editor
until **File > Save** or **File > Save As** is selected. A successful write:

1. verifies that the file still matches the SHA-256 recorded when the editor
   opened;
2. validates or tokenises the source as required by its content type;
3. creates an automatic image checkpoint;
4. writes through the mounted filesystem while retaining Amiga metadata;
5. refreshes the pane and marks the image as changed.

The stale-file check prevents one editor from silently replacing a newer
change made elsewhere in the workspace. Save As creates a sibling file and
leaves the source file intact. The image is still a private working copy until
the pane's Save Image control prepares its timestamped download ZIP.

Archive members are expanded in memory. Readable members in ZIP, TAR,
compressed TAR, GZIP, BZIP2 and XZ containers can be edited. Save verifies the
member and parent archive SHA-256 values, rebuilds the complete container and
replaces the outer image file through the normal undoable transaction. A DMS
member is editable only when its complete block sequence maps uniquely onto
standard data chunks and its encoded length remains unchanged. The structural
review proves every other timing and control chunk is retained before the
normal undoable transaction runs.

## Opening and exporting a file

Double-click a file in an OFS, FFS, AmigaDOS, Kickstart ROM, DMS or archive view. The
same dispatch is available through **Analyse > Open selected file**. The arrow
beside a filename downloads the original file and applicable Amiga metadata
without opening an editor.

The pane's **Load** and **Execute** columns describe the catalogue entry rather
than the editor's interpretation of the bytes. Select either value to review
and, on writable media, change both words through the guarded metadata dialog.
See the [catalogue metadata guide](FILE-METADATA-GUIDE.md) before changing an
entry whose original values are unknown.

Content detection uses evidence in this order:

1. an authoritative Amiga filetype or recognised filename;
2. bounded inspection of files up to 128 KiB while the directory mount is
   already open;
3. complete inspection when the user opens a file;
4. a raw hexadecimal fallback when no safer interpretation is available.

This keeps large HDA and HDF directory listings responsive without leaving
ordinary BASIC programs, command files and archives with misleading icons.
The cache is tied to the working image revision and is discarded after a
mutation.

## Editor window

![Current AmigaBASIC editor workspace with tab strip, folding gutter and desktop-style menus](../app/static/help/editor-workspace-current.png)

Source and disassembly editors open as movable, resizable windows within the
browser. Drag the title bar to move one. Drag an edge or corner to resize it.
Use the square title-bar control, or double-click the title bar, to maximise
and restore it. The window is constrained to the browser viewport.

The menus follow desktop editor conventions:

- **File** contains Save, Save As, text or source export, original-byte
  download and Close where those operations apply.
- **Edit** contains Undo, Redo, clipboard operations, Select All, persistent
  Find and Replace, image-wide search, symbol references, symbol rename,
  completion and line navigation.
- **View** contains folding, synchronized bytes and structure guidance.
- **Tools** contains language checks, outlines, transformation history,
  normalisation, BASIC verification, Condense and Refactor.
- **Project** contains notes, bookmarks, symbols, code and data regions, test
  history and managed emulator execution or debugging where the selected
  profile supports the source media.
- **Help** contains the language overview, searchable command reference,
  document symbols and current diagnostics.

The native textarea remains the editable document. Syntax colour, indentation,
folding, annotations and hover targets are presentation layers. This preserves
normal browser selection, input methods, clipboard behaviour and undo.

After opening one editor menu, moving the pointer or keyboard focus across the
menubar opens each menu in turn. Selecting a command, clicking elsewhere or
pressing Escape closes the menu layer.

An editor tab strip keeps several files from the same mounted image open at
once. Unsaved source is retained when another tab is selected and a dot marks a
dirty tab. **Open from image…** searches filenames and bounded readable content,
then opens the selected result as another tab after navigating to its
partition and drawer. Closing a dirty tab or editor requires confirmation.
The tab set, active document, draft, selection and scroll position are stored
in browser session storage. They are restored after an ordinary refresh once
the private server-side image sessions have reopened. Session storage is scoped
to the browser tab and is bounded to 24 documents and 512 KiB per draft.

On a hard drive, **Open from image…** scans every partition rather than only
the one currently mounted. Results identify both the device name and the volume
title. An unreadable partition is counted in the result summary, and the search
remains bounded so a damaged drive cannot hold the browser indefinitely.

## Command-script editor

![Command script editor showing a real OFS Startup-Sequence file](../app/static/help/file-editor-script.png)

Readable `Startup-Sequence`, `Boot`, `Start`, `Startup`, `Loader`, `Menu` and
similar files are opened as unnumbered scripts when their bytes contain a
coherent run of AmigaDOS or AmigaBASIC commands. Detection is based on content as well as name. A
tokenised BASIC file named `Startup-Sequence` remains a BASIC program.

Scripts retain their physical order and are written back with one newline per
line, as AmigaDOS stores them. The editor recognises AmigaDOS commands and
AmigaBASIC statements, highlights strings and arguments, and flags:

- unclosed strings;
- rooted or filing-system-dependent `R.` and `L.` abbreviations;
- `CHAIN "Startup-Sequence"` where an executable command file should normally be passed
  to `*EXEC`.

These checks are deliberately narrow. A command file can depend on a ROM,
filing system, memory layout or machine configuration that static text cannot
prove.

## AmigaBASIC editor

![A saved AmigaBASIC program opened from an FFS floppy image](../app/static/help/file-editor-basic.png)

A saved AmigaBASIC program opens as numbered source with one visible space after
each line number. The application retains the tokenised bytes as the authority
for saving. A recognised AmigaBASIC 1.0 program with trailing binary data is editable
because Save replaces only its tokenised prefix and preserves the payload byte
for byte. AmigaBASIC 1.2 remains read-only because writing it through the AmigaBASIC 1.0
tokeniser would change its format.

### Editing and paste handling

Type a numbered line to insert or replace it. Remove the complete physical line
to delete it. When numbered text is pasted, the editor asks whether to validate
and normalise it as AmigaBASIC or insert the bytes as plain text. The complete
listing must still tokenise successfully before it can be saved.

**Tools > Renumber BASIC** changes physical line numbers and encoded direct
targets used by `GOTO`, `GOSUB` and `RESTORE`. It does not rewrite numbers in
strings or dynamic line expressions.

### Diagnostics and help

The shared scanner carries explicit BASIC I through VI capability profiles.
The live analyser reports missing, duplicated or out-of-order line numbers,
unresolved direct destinations, missing local procedures, unmatched procedure
boundaries, array use before DIM, FOR/NEXT mismatches,
dialect-incompatible commands, unclosed strings
and conservatively identified unreachable lines.
It also builds a procedure and function outline with direct call sites.

Array checks use token identities rather than raw name-and-parenthesis patterns,
so compact forms such as `PRINTTAB(0,15)` remain `PRINT TAB(...)` and are not
reported as arrays. The analyser deliberately does not claim that an assignment
is unused. AmigaBASIC pseudo-variables have immediate effects, assembler and
machine-code calls consume conventional variables implicitly, and chained
programs can share globals, so textual absence of a later read is not proof of
a defect. `A`, `A%` and `A$` are separate, valid AmigaBASIC variables and are
not reported merely because they share a base name.

Commands with reference data have dotted hover targets. Hovering displays the
command's purpose, syntax, context and relevant cautions. Put the caret in a
command and press F1 for the keyboard equivalent. Assembly source uses the same
68000 instruction, library-vector and directive help as the disassembly editor.

An AmigaDOS script and an AmigaBASIC program are told apart by vocabulary,
because AmigaDOS names its commands without any sigil. `LOAD "Program"` in a
BASIC line receives AmigaBASIC LOAD help, while `Execute Loader` in a script
receives the AmigaDOS command's help. The same distinction applies to
overlapping names such as RUN.

### Structure guidance and folding

Structure guidance understands procedures, multi-line functions, `FOR`,
`REPEAT`, structured `IF`, `CASE`, `WHILE` and inline assembler boundaries.
Choose a 2, 4 or 8-character guide step. Live lines show nesting and the
innermost block containing the caret is highlighted. This does not insert
whitespace, replace the textarea, change dirty state or alter saved bytes.

The left gutter folds recognised blocks. The state-aware View command reads
**Collapse all blocks** when everything is expanded and **Expand all blocks**
when anything is folded. Double-click a rendered source line to return to its
exact editable location.

Classic AmigaBASIC `IF` semantics matter here. A line such as
`IF condition THEN 100` does not open a block, and an omitted-`THEN` form
controls only the following statement. Physical lines that follow it are not
indented as if the language had an implicit `ENDIF`.

### Refactor

Refactor operates on the physical selection, a selected line, or the complete
program when nothing is selected. It proposes a readable expansion of compact
AmigaBASIC and can:

- split proven colon-separated statement boundaries;
- expand inline and nested `IF`, `ELSE IF` and `ELSE` logic;
- extract a compact `ON ERROR` handler behind an explicit branch;
- separate commands on `SUB` and `END SUB` lines;
- update direct line destinations after its proposed renumbering.

The proposal appears beside the original. It is tokenised, detokenised and
tokenised again before acceptance is enabled. No source is changed or
renumbered until the user accepts the review and confirms it. Cancel returns
to the untouched document. Acceptance is one undoable editor operation and
retains the logical cursor and viewport.

Refactor does not rename variables, alter strings, invent procedures, rewrite
dynamic destinations or split inline assembler. When a statement boundary
cannot be proved safe it remains unchanged for manual review. A physical line
whose body is only `:` is retained because AmigaBASIC does not support a blank
numbered source line.

### Condense

Condense performs the controlled inverse. It packs adjacent statements with
`:` while preserving target lines and runtime order. It uses the installed Amiga
BASIC tokeniser to enforce the 251-byte physical-line limit. Packing stops at
inline `IF`, `ON ERROR`, `REM`, unconditional transfers and structured branch
boundaries. Code with computed line destinations, or code
that uses `ERL` in a way affected by removing physical lines, is left alone.

Condense uses the same original and proposal review, round-trip proof, explicit
acceptance and single undo operation as Refactor.

### Synchronized bytes

**View > Show synchronized bytes** maps the caret's BASIC line to the bytes in
the last saved tokenised program. Unsaved source is never presented as if it
were already on disk. A newly inserted or renumbered line has no saved byte
range until Save succeeds; the strip says so rather than pointing at offset
zero. The Hex shortcut opens the exact saved offset.

## Text editor

Readable Latin-1 content that is neither tokenised BASIC nor a command script
opens as text. Save encodes Latin-1 and rejects characters that cannot be
represented rather than silently replacing them. File > Export downloads
browser-local text. Save preserves the existing protection value, execution
address, filetype and access state where the destination filesystem supports
them.

Find and Replace supports case-sensitive matching, whole identifiers, regular
expressions, selection-only scope, preview and one-step Replace All. Ctrl+Space
offers commands, identifiers, symbols and templates. Text and script editors
can duplicate, move, join and delete selected lines. The conservative formatter
removes trailing whitespace and normalises proven prefixes; BASIC must pass a
token round trip before formatting is accepted. Image-wide search covers names
and bounded readable content across every partition and drawer.
Results open as another document tab. Each tab retains its unsaved draft,
selection and scroll position. **Open from image…** on the tab strip searches
the complete mounted image without closing the current document.

File Properties changes the protection bits, the comment, the Workbench icon type and
writable state without modifying content. Whole-image dependency analysis
distinguishes exact, unique-leaf, ambiguous, missing and root-relative launcher
targets.

## Disassembly editor

![Annotated 68000 disassembly opened from an OFS executable](../app/static/help/file-editor-disassembly.png)

Binary files open as editor-style disassembly rather than a report table. The
active workbench profile selects the initial processor, and the baseline 68000
is used when no profile is set. The toolbar can override that choice with
68000, 68010, 68020, 68030, 68040 or 68060, and accepts a mapped origin, file
offset and bounded byte count.

### Decoding and annotation

The 68000 decoder distinguishes official NMOS instructions from data. Unknown
opcodes remain `DC.B`. It tracks immediate register values only while the code
path proves them, drops assumptions at uncertain joins, and adds specific
comments for:

- library calls through A6, such as `OpenLibrary`, `AllocMem`, `Open` and `Write`;
- known library vector offsets and the proven register values passed to them;
- custom-chip and CIA registers named from the address the instruction uses;
- the memory type an `AllocMem` request asks for, and the file mode an `Open`
  request uses;
- Amiga hardware I/O regions;
- branch conditions and direct references;
- conventional Amiga BRK error blocks;
- the file's own protection bits.

Context help uses the workbench profile applied to the containing pane. The
decoded operation and its actual constant parameters are explained first, then
the documented platform scope is compared with the configured target machine,
which spans the Amiga 500, 600, 1200, 3000, 4000 and CD32. An out-of-scope operation remains documented, but receives an
explicit warning that it was not designed for the current target and can fail
or cause unexpected behaviour. This applies to source help and to proven
library calls in assembly. Automatic targets are
reported as unconfirmed rather than being treated as compatible.

Local targets receive stable semantic labels where behaviour is proven, with
their hexadecimal address retained to keep similar routines distinct. ARM and
68000, plus explicit 68010 and 68020 modes, use Capstone. Static 68020 starts
with 16-bit accumulator and index widths because runtime M/X state cannot be
proved from isolated bytes. ARM words are decoded little-endian and 68000 words
big-endian. Saved project symbols apply to every supported architecture.

Static disassembly cannot prove indirect targets, generated code, compression,
bank switching or whether bytes are data. Treat the original bytes and target
execution as the final evidence.

### Layout, strings and navigation

The grid measures the widest byte and instruction fields in the current
result, adds a small gutter, and places annotations immediately after them.
Long cells are capped and expose their complete content on hover. The heading
remains visible while scrolling.

Readable strings require alphabetic content and exclude incidental punctuation
and number runs. Strings found inside the decoded range are rendered as `DC.B`
data rows. Select one in the Readable strings list to jump to its disassembled
location. If the location is outside the current block, the editor requests a
new bounded disassembly around it. Double-click an instruction only when the
corresponding raw bytes are required in Hex.

### Project metadata

Project metadata is stored outside the file bytes in the private recoverable
session. It includes:

- notes;
- bookmarks tied to saved file offsets;
- address symbols;
- free-form comments tied to exact saved file offsets;
- user-classified code, text, byte, 16-bit word, address-table and bitmap
  regions;
- transformation history;
- configured emulator and debugger results.

Shift-click disassembly rows to select a range, classify it, and rebuild the
listing using that decision. Word and address regions follow the selected
processor's byte order. Symbols can be imported and exported as
`&address = label`. Find references and the outline navigate direct users and
labelled entry points. Project metadata participates in session recovery and
checkpoints but does not alter the image bytes.

The project manager edits notes, symbols, comments and bookmarks together and
exposes a portable JSON representation. A row comment is anchored to its exact
saved file offset and is rendered beside that instruction on every later
disassembly. Compare with saved file shows the current and
persisted source side by side. The selected-data inspector renders text,
hexadecimal bytes, both 16-bit byte orders and a bounded one-bit bitmap preview.

## Cheat-candidate analysis

The editor's **Tools > Find cheat candidates** command accepts tokenised Amiga
BASIC and files that normal content inspection classifies as machine code. The
report opens inside that editor window, is read-only and does not alter the
editor project or image.

At normal desktop widths the report docks to the right of the code and scrolls
independently at the full listing height. Narrow windows place it below the
editor so the code and evidence remain readable. Its separator is draggable
and keyboard adjustable. Selecting a candidate centres and highlights the
corresponding BASIC line or disassembly address.

For BASIC, it correlates semantic variables, plausible initial values, updates,
zero or one tests and terminal paths. Unexplained direct memory writes and
opaque countdown loops are suppressed. For machine code, it uses the
profile-aware disassembly to join constant initialisation, access to the same
storage, updates, forward terminal branches and saved semantic labels. Generic
backward decrement loops and likely copy, clear, scan or delay counters are
discarded. Reachable unlabelled memory updates with a forward decision remain
visible as Possible candidates, while speculative instructions decoded from
data are excluded. Loader commands and payloads with almost no reachable code
are called out explicitly, including the need for a post-loader memory snapshot.
Every retained result contains its source line or decoded address,
corroborating evidence, confidence, suggested runtime check and remaining risk.

Purpose and confidence filters help separate lives, energy, ammunition, timer,
score and collision evidence from generic counters or memory writes. Optional
internet title identification uses the existing bounded metadata lookup.
Specialist browser searches come from `app/cheat_sources.json` and open only
when selected. See the [cheat analysis guide](CHEAT-ANALYSIS-GUIDE.md) for the
safe checkpoint, watchpoint and hardware-test workflow.

Selecting a machine-code result with a proven file offset enables **Prepare
guarded patch**. This workflow does not convert static confidence into proof.
It asks the tester to record the watchpoint and at least two distinct gameplay
events where the watched value changed, along with the intended replacement,
rationale and author. A valid `.affcheat.json` record contains:

- the full SHA-256 and size of the analysed source file;
- the exact file offset, original bytes and same-length replacement bytes;
- the applied hardware profile, watchpoint and gameplay observations;
- an author, rationale and explicit rollback instruction.

Apply repeats both the complete-file hash check and the original-byte check. It
then writes through the normal filesystem transaction, which creates an image
checkpoint before the change. A mismatched revision, byte sequence or target
is refused. The host-private patch library stores no image data, is limited
to 500 records and matches by exact hash rather than a title. Entries can be
exported individually and cleared without affecting images or checkpoints.
The web edition retains it in origin-scoped browser storage. The Linux desktop
edition retains it in the same private XDG client-state file as workspace
settings and the collection catalogue.
Archive members and BASIC source are not patch targets in this first guarded
workflow. Emulator observations are tester supplied until managed watchpoint
capture can correlate runtime events automatically.

## Managed emulator and debugger

Open **Workbench → Hardware profiles → Emulator and debugger integration**.
Choose a profile and machine. The dependent filing system, FastFileSystem build,
emulator, debugger, RAM and startup controls are populated from that choice.
Save the profile and apply it to the pane that will use it. Amiga File Forge
uses the effective profile shown for that pane rather than a global fallback.

The same managed launcher is available from each applicable pane's **Tools**
menu. Use **Run image** or **Debug image** for a standalone floppy or DMS
archive; it is copied to temporary media first, so emulator writes do not flow
back into the working image. A hard drive is attached whole from its partition
table, described below.

For a BASIC file it first asks which launch context is wanted:

- **Inject and run BASIC buffer** tokenises the current editor source, including
  unsaved changes, copies it to a temporary bootable OFS or FFS floppy as
  `PROGRAM`, supplies a matching `Startup-Sequence`, and starts it.
  This is suitable for self-contained programs but deliberately provides none
  of the parent image's companion files.
- **Mount and boot parent** attaches the complete image and follows its normal
  boot sequence, retaining dependencies and filing-system context.
- **Mount parent only** attaches compatible media without autoboot and leaves
  the emulated machine at its normal prompt.

The parent choices appear only when the selected emulator supports that exact
container. An FFS hard disk which FS-UAE cannot attach therefore still
allows an isolated 8-bit BASIC test. Capability and error messages name the
actual selected emulator and machine. Amiga File Forge then:

1. attaches the current bootable image to the selected managed machine;
2. uses the profile's safe machine, RAM and startup options;
3. runs an automated test with a bounded timeout;
4. retains the return code and the final 20,000 characters of each output
   stream in project metadata and presents it in the editor's retained
   test-results view;
5. keeps the image bytes in the recoverable working session.

The Docker build includes FS-UAE, which is the one managed emulator: a single
portable build covers every machine from an A500 to an A4000 and CD32, floppy
and hard drive alike, which keeps the capability checks honest rather than
spread across several tools with different gaps. The confirmation identifies the
machine and resolved safe arguments. An exit code records what that
configured tool observed. It does not prove compatibility with every expansion
or physical machine.

Run and Debug start a live virtual display and embed it in the editor through a
local noVNC viewer on port 8668. Click the display before typing, use Full screen
when needed, and use Stop and close to terminate the emulator and release its
temporary media. Starting another emulator replaces the current one. The
container routes audio to a null ALSA device, so headless Docker audio errors do
not obscure useful ROM, accelerator and machine configuration information.

No Kickstart ROM is shipped or downloaded. Point the profile at one you own;
until then Run and Debug report the missing firmware rather than starting to a
black screen.

`AMIGA_FILE_ASSEMBLER_COMMAND` enables the dangerous, explicit reassembly
workflow. It must contain `{source}` and `{output}` and can use `{origin}` and
`{architecture}`. Generated labels and comments are a starting point rather
than guaranteed source syntax. Amiga File Forge checks the original binary
hash, requires confirmation, runs the command without a shell and replaces the
whole binary through an undo checkpoint only when a bounded output file exists.

The debugger choice follows the selected managed emulator and offers the same
isolated BASIC, parent boot and parent mount contexts. Results are retained in
project test history. Formats which cannot be mounted directly explain that
specific emulator limitation without disabling a valid isolated BASIC run.

A hard drive is attached whole rather than a volume at a time. Amiga File Forge
copies the working `.hdf` to a private file and attaches that copy to the
FS-UAE hard-drive controller the applied profile declares, so the image you are
editing is never writable by the emulator. A profile with no mass-storage
interface says so plainly instead of attaching a drive that machine could not
have had.

## Archive and DMS members

DMS, gzip-compressed DMS, ZIP, TAR, TAR.GZ, TGZ, TAR.BZ2, TAR.XZ, standalone
GZIP, BZIP2 and XZ containers open as bounded hierarchies. DMS members expose
their reconstructed metadata and whether the archive
block sequence was complete. Complete, unambiguous standard-block members can
be edited when their encoded length does not change. Save presents a physical
chunk comparison, updates only the selected data and its block CRCs, and proves
that timing, control and unknown chunks retain order, length and bytes. Every
ambiguous, incomplete, cycle-level or length-changing case remains read-only.
Readable members in the other formats use the existing checksum-guarded
container rebuild and normal image checkpoint.

Inspect, disassembly, Hex and cheat-candidate analysis retain the outer image,
container and member path as one context. A nested name such as
`Arcadians/ARCAD2` is therefore resolved inside its DMS or archive rather than
being mistaken for an FFS path containing a slash.

Archive handling rejects parent traversal, non-regular TAR objects, archives
over 512 MiB, individual expanded members over 128 MiB and catalogues with
20,000 or more entries. Small members are classified while the archive is open;
larger members are classified only when explicitly opened. These limits bound
memory use and decompression work.

## Hex fallback

The fixed-range hex editor remains available from the pane and from every file
editor. It shows byte offsets, hexadecimal data, ASCII, typed values and staged
changes. Search accepts text or byte patterns. **Analyse → Compare with binary
file** highlights differing bytes, reports size differences and navigates to
the next changed offset. Structured templates decode generic integer values,
AmigaDOS boot and root blocks, Rigid Disk Blocks, Kickstart ROM headers and
FFS map fields without changing bytes. They also decode Rigid Disk Block
partition entries, Hardfile GEO descriptors and DMS headers. A custom JSON template
can define up to 128 fields relative to the selected byte using `u8`, `u16le`,
`u16be`, `u32le`, `u32be`, `ascii` or `hex`. Field offsets are bounded to 4095
and lengths to 256 bytes.

Writes cannot insert, remove or resize bytes. They require explicit
confirmation, reject overlapping or stale changes, create a checkpoint and
refresh decoded caches.

Renaming or moving a file or drawer also moves its editor project metadata.
Deleting it removes matching notes, symbols, comments, bookmarks, regions and
retained emulator results. Metadata in other partitions remains untouched.

## Keyboard reference

| Key | Action |
| --- | --- |
| `Ctrl+S` | Save editable source |
| `Ctrl+Shift+S` | Save As inside the image |
| `Ctrl+F` | Find |
| `Ctrl+H` | Find and Replace |
| `Ctrl+Z` / `Ctrl+Y` | Undo / Redo |
| `Ctrl+A` | Select All |
| `Ctrl+W` | Close editor, with an unsaved-change warning |
| `F1` | Help for the command at the caret |
| `Escape` | Dismiss hover help, menus or the current nested view |

## Troubleshooting

### A file opens as binary

Check its Amiga filetype, protection value and actual bytes. A generic filename is
not sufficient evidence. Files larger than the directory sniff limit are
classified when opened, not during every listing.

### BASIC opens read-only

The program is AmigaBASIC 1.2, has a trailing binary payload, exceeds the safe editor
limit or failed exact AmigaBASIC 1.0 round-trip requirements. Export it and use a
tool that understands that exact dialect or compound format.

### Disassembly looks wrong

Confirm architecture, mapped origin, file offset and hardware profile. The
selected bytes may be data, text, compressed content or code that depends on
relocation or bank switching. Classify proven regions and retain useful labels,
but do not treat a plausible instruction stream as proof.

### A bookmark points at older bytes

Bookmarks use saved file offsets. Save a newly inserted or renumbered BASIC
line before bookmarking it. After a successful save the line map is rebuilt
from the new tokenised bytes.

### Save reports a stale file

Another operation changed the file after this editor opened. Export or copy
the editor text if needed, close it, reopen the current file and reapply the
change. The stale check is intentional data-loss protection.

### Emulator testing is unavailable

Confirm that the Workbench profile applied to the pane selects an installed
managed emulator. The error names the chosen emulator and distinguishes an
unsupported parent container from a missing emulator. A self-contained 8-bit
AmigaBASIC file may still run from a generated test floppy. Archive members must
be extracted into an image before they can be handed to an emulator.
