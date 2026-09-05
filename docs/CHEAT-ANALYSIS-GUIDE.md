# Cheat-candidate analysis

Amiga File Forge can inspect one tokenised AmigaBASIC or machine-code file for
code that may control lives, energy, ammunition, time, score, collision or
other gameplay state. Open the file from a filesystem pane and choose
**Tools → Find cheat candidates** in its editor.

On a wide editor the report docks to the right of the source or disassembly,
uses the full listing height and scrolls independently. Narrow browser windows
place it below the listing so neither the code nor the evidence is squeezed
into an unreadable column. Drag the separator to resize the code and report;
the separator also accepts arrow keys when focused. Select a candidate to
centre and highlight its BASIC line or decoded machine-code address.

The result is deliberately called a candidate report. Static code cannot prove
what a memory location means, and many games compress, encrypt, relocate or
modify their own code. The analyser never changes the file.

## AmigaBASIC evidence

The BASIC pass examines the detokenised program and reports:

- direct byte and word writes using `?` and `!` indirection;
- semantically named gameplay variables such as lives, health, fuel and time;
- assignments that increment or decrement those variables;
- zero and one comparisons that may lead to death, timeout or game-over code.

A named variable is not enough on its own. The analyser scores independent
signals such as a plausible initial value, an update, a zero or one test and a
terminal path containing death, game-over or another gameplay clue. Opaque
variables need a semantic terminal path. An unexplained bare write such as
`?&70=3` is suppressed because it is more likely to be loader, display, sound
or operating-system state. A write of an instruction byte such as NOP or RTS
is retained as a possible trainer patch, with an explicit self-modifying-code
warning.

## Machine-code evidence

The binary pass uses the same profile-aware 68000, 68010, 68020, 68030, 68040
or 68060 disassembly as the file editor. It looks for:

- plausible constant initialisation of a state location;
- later decrements or load/subtract/store updates to that same location;
- forward terminal branches and destination labels associated with death,
  game over, damage, time or another gameplay outcome;
- saved symbols and comments that explicitly identify gameplay state;
- multi-byte and decimal counter components when their labels provide the
  necessary relationship.

Each result names the decoded address, corroborating evidence, confidence and
a suggested emulator experiment. An unlabelled memory decrement followed by a
forward decision can be retained as **Possible** when it is on a reachable code
path. It is never promoted without stronger evidence. Backward branches and
loop, copy, scan, clear, delay, row, column and byte contexts are treated as
loop evidence unless semantic game-state evidence overrides them. This matters
because countdown loops are normal 68000 code.
Simply replacing a retained instruction with NOP bytes can still break flags,
timing or control flow. A selected result with an exact file offset can open
the guarded patch builder, but it never invents replacement bytes or claims a
static result is proved.

The analyser honours the disassembler's reachability map. Bytes which were
only decoded by the linear fallback are ignored, preventing compressed data,
graphics and strings from becoming spurious DEC or SBC candidates. When the
selected file is a loader, the report lists loader commands it can recognise.
When almost none of a payload is reachable from its recorded entry point, the
report identifies likely packed, encrypted, data-only or runtime-generated
code instead of presenting an unexplained empty result. Chuckulus is one such
loader chain: its launcher refers to `EZZZIns` and `EZMC`, and the final game
image is prepared at runtime. Reliable candidates in that case require a
post-loader emulator memory snapshot.

## Filters and online evidence

Filter the report by likely purpose and confidence. **Check online title
evidence** uses the existing cautious software metadata lookup to identify the
selected file's likely title. Specialist reference searches are defined in
`app/cheat_sources.json`; they open only when selected and can lead to published
cheats, magazine listings or documented disassemblies. No search result is
treated as proof that it matches the selected bytes.

Community disassemblies demonstrate why this distinction matters. A lives
counter may be easy to locate, while a useful invulnerability patch can require
following the branch that handles death and preserving deliberate suicide or
restart behaviour. The [English Amiga Board coders' forum](https://eab.abime.net/forumdisplay.php?f=34)
carries worked examples of exactly this kind of analysis, and
[Hall of Light](https://hol.abime.net) indexes the contemporary cheat and
trainer releases for many titles.

## Safe workflow

1. Apply the intended hardware profile to the pane.
2. Create a named checkpoint for the image.
3. Open the file, run **Tools → Find cheat candidates** and filter the strongest evidence first.
4. Open the file in the editor and inspect every reference to the variable or
   address. Use saved symbols and comments where useful.
5. Run or debug the parent image in the configured emulator. Use watchpoints
   and breakpoints to confirm that the candidate changes during the relevant
   event.
6. Make a reviewed source or byte change only after the behaviour is proved.
7. Retest loading, normal play, death, restart, level transition and scoring.
8. Keep the original image and checkpoint until the edited build has also been
   tested on the target hardware.

## Guarded patches and the private library

After steps 4 and 5, select the machine-code candidate and choose **Prepare
guarded patch**. Enter the actual watched address, two distinct gameplay events
and their before and after values. Also supply the reviewed same-length machine
code replacement, a rationale and an author. Amiga File Forge validates the
record against the exact current file and downloads it as `.affcheat.json`.

The patch is bound to the complete source SHA-256, source size, file offset and
original bytes. Apply repeats every check and creates the usual automatic image
checkpoint. This prevents a patch for one build being applied to a similarly
named but different release. It does not prove that the tester's observations
were correctly interpreted, which is why target-hardware retesting remains
part of the workflow.

Validated records are also retained in a host-private library. The web edition
uses origin-scoped browser storage; the Linux desktop edition uses its private
XDG client-state file. The library contains metadata and replacement bytes,
not disk images. It is capped at 500
entries, can export individual records and can be cleared independently. An
entry can only apply when the current file has the exact recorded hash and
guarded bytes. Titles are descriptive only and never participate in matching.

Automatic watchpoint capture and correlation with repeatable gameplay events
remains a backlog item. Until that gate is complete, the user records debugger
observations and the interface labels them as such.

Compressed, encrypted, self-modifying and indirectly addressed games may
produce no useful static candidates. The panel now explains this condition and
distinguishes a loader or runtime payload from an ordinary negative scan. It
is an honest inconclusive result, not evidence that the game cannot be modified.
