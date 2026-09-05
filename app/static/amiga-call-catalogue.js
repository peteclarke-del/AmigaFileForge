window.AmigaCallCatalogue = (() => {
  // An Amiga program reaches the operating system through library vectors:
  // it puts a library base in A6 and calls a negative offset from it. This
  // catalogue turns those offsets, and the registers around them, into
  // readable explanations for the editor and the disassembler.

  const MEMORY_FLAGS = Object.freeze({
    1: "MEMF_PUBLIC, visible to other tasks",
    2: "MEMF_CHIP, addressable by the custom chips",
    4: "MEMF_FAST, not addressable by the custom chips",
    65536: "MEMF_CLEAR, zeroed before it is returned",
    131072: "MEMF_LARGEST, report the largest free block",
    262144: "MEMF_REVERSE, allocate from the top down",
    524288: "MEMF_NO_EXPUNGE, do not expunge to satisfy this request",
  });

  const FILE_MODES = Object.freeze({
    1005: "MODE_OLDFILE, open an existing file for reading and writing",
    1006: "MODE_NEWFILE, create or truncate a file for writing",
    1004: "MODE_READWRITE, open or create without truncating",
  });

  const SEEK_MODES = Object.freeze({
    "-1": "OFFSET_BEGINNING, relative to the start of the file",
    0: "OFFSET_CURRENT, relative to the current position",
    1: "OFFSET_END, relative to the end of the file",
  });

  const LOCK_MODES = Object.freeze({
    "-2": "EXCLUSIVE_LOCK, no other task may access the object",
    "-1": "SHARED_LOCK, other tasks may read the object",
  });

  const DRAW_MODES = Object.freeze({
    0: "JAM1, draw the foreground pen only",
    1: "JAM2, draw both foreground and background pens",
    2: "COMPLEMENT, invert the pixels",
    4: "INVERSVID, swap the pens",
  });

  // Library vectors, keyed by the negative offset from the library base.
  const EXEC = Object.freeze({
    "-30": { summary: "runs a routine in supervisor mode", parameters: [{ name: "A5", meaning: "routine to run" }] },
    "-96": { summary: "finds a resident tag by name", parameters: [{ name: "A1", meaning: "module name" }] },
    "-120": { summary: "disables interrupts until Enable is called", parameters: [] },
    "-126": { summary: "re-enables interrupts after Disable", parameters: [] },
    "-132": { summary: "forbids task switching until Permit is called", parameters: [] },
    "-138": { summary: "permits task switching again after Forbid", parameters: [] },
    "-162": { summary: "installs an interrupt vector", parameters: [{ name: "D0", meaning: "interrupt number" }, { name: "A1", meaning: "Interrupt structure" }] },
    "-198": { summary: "allocates memory of a requested type", parameters: [{ name: "D0", meaning: "byte size" }, { name: "D1", bits: [
      { mask: 1, set: "public memory", clear: "not required to be public" },
      { mask: 2, set: "Chip RAM, reachable by the custom chips", clear: "any memory the request allows" },
      { mask: 4, set: "Fast RAM", clear: "no Fast RAM requirement" },
      { mask: 65536, set: "cleared to zero", clear: "returned with its previous contents" },
    ] }] },
    "-210": { summary: "frees memory previously allocated", parameters: [{ name: "A1", meaning: "memory block" }, { name: "D0", meaning: "byte size, which must match the allocation" }] },
    "-216": { summary: "reports how much memory of a type is free", parameters: [{ name: "D1", values: MEMORY_FLAGS }] },
    "-294": { summary: "finds a task by name, or the current task when A1 is zero", parameters: [{ name: "A1", meaning: "task name, or 0 for the current task" }] },
    "-306": { summary: "reads or changes a task's signal bits", parameters: [{ name: "D0", meaning: "new signals" }, { name: "D1", meaning: "signal mask" }] },
    "-318": { summary: "waits until one of a set of signals arrives", parameters: [{ name: "D0", meaning: "signal mask to wait for" }] },
    "-324": { summary: "signals another task", parameters: [{ name: "A1", meaning: "task" }, { name: "D0", meaning: "signal mask" }] },
    "-366": { summary: "sends a message to a port", parameters: [{ name: "A0", meaning: "message port" }, { name: "A1", meaning: "message" }] },
    "-372": { summary: "receives a message from a port, returning zero when empty", parameters: [{ name: "A0", meaning: "message port" }] },
    "-378": { summary: "replies to a message", parameters: [{ name: "A1", meaning: "message" }] },
    "-384": { summary: "waits until a message arrives at a port", parameters: [{ name: "A0", meaning: "message port" }] },
    "-390": { summary: "finds a public message port by name", parameters: [{ name: "A1", meaning: "port name" }] },
    "-408": { summary: "opens a library, accepting any version", parameters: [{ name: "A1", meaning: "library name" }] },
    "-414": { summary: "closes a library", parameters: [{ name: "A1", meaning: "library base" }] },
    "-420": { summary: "replaces one entry in a library's jump table", parameters: [{ name: "A1", meaning: "library base" }, { name: "A0", meaning: "new function" }, { name: "D0", meaning: "vector offset" }] },
    "-444": { summary: "opens a device unit into an I/O request", parameters: [{ name: "A0", meaning: "device name" }, { name: "D0", meaning: "unit number" }, { name: "A1", meaning: "I/O request" }, { name: "D1", meaning: "flags" }] },
    "-450": { summary: "closes a device unit", parameters: [{ name: "A1", meaning: "I/O request" }] },
    "-456": { summary: "performs an I/O request and waits for it", parameters: [{ name: "A1", meaning: "I/O request" }] },
    "-462": { summary: "starts an I/O request without waiting", parameters: [{ name: "A1", meaning: "I/O request" }] },
    "-474": { summary: "waits for an I/O request to complete", parameters: [{ name: "A1", meaning: "I/O request" }] },
    "-498": { summary: "opens a resource by name", parameters: [{ name: "A1", meaning: "resource name" }] },
    "-516": { summary: "formats a string, calling a routine for each character", parameters: [{ name: "A0", meaning: "format string" }, { name: "A1", meaning: "argument array" }, { name: "A2", meaning: "per-character routine" }, { name: "A3", meaning: "routine data" }] },
    "-552": { summary: "opens a library by name and minimum version", parameters: [{ name: "A1", meaning: "library name" }, { name: "D0", meaning: "minimum version, or 0 for any" }] },
    "-564": { summary: "takes a signal semaphore, waiting if it is held", parameters: [{ name: "A0", meaning: "semaphore" }] },
    "-570": { summary: "releases a signal semaphore", parameters: [{ name: "A0", meaning: "semaphore" }] },
    "-624": { summary: "copies memory", parameters: [{ name: "A0", meaning: "source" }, { name: "A1", meaning: "destination" }, { name: "D0", meaning: "byte count" }] },
    "-732": { summary: "allocates memory that remembers its own size", parameters: [{ name: "D0", meaning: "byte size" }, { name: "D1", values: MEMORY_FLAGS }] },
    "-738": { summary: "frees an AllocVec allocation", parameters: [{ name: "A1", meaning: "memory block" }] },
  });

  const DOS = Object.freeze({
    "-30": { summary: "opens a file and returns a file handle", parameters: [{ name: "D1", meaning: "file name" }, { name: "D2", values: FILE_MODES }] },
    "-36": { summary: "closes a file handle", parameters: [{ name: "D1", meaning: "file handle" }] },
    "-42": { summary: "reads bytes from a file handle", parameters: [{ name: "D1", meaning: "file handle" }, { name: "D2", meaning: "buffer" }, { name: "D3", meaning: "byte count" }] },
    "-48": { summary: "writes bytes to a file handle", parameters: [{ name: "D1", meaning: "file handle" }, { name: "D2", meaning: "buffer" }, { name: "D3", meaning: "byte count" }] },
    "-54": { summary: "returns the standard input handle", parameters: [] },
    "-60": { summary: "returns the standard output handle", parameters: [] },
    "-66": { summary: "moves a file's read and write position", parameters: [{ name: "D1", meaning: "file handle" }, { name: "D2", meaning: "offset" }, { name: "D3", values: SEEK_MODES }] },
    "-72": { summary: "deletes a file or an empty drawer", parameters: [{ name: "D1", meaning: "name" }] },
    "-78": { summary: "renames or moves a file", parameters: [{ name: "D1", meaning: "old name" }, { name: "D2", meaning: "new name" }] },
    "-84": { summary: "locks a file or drawer", parameters: [{ name: "D1", meaning: "name" }, { name: "D2", values: LOCK_MODES }] },
    "-90": { summary: "releases a lock", parameters: [{ name: "D1", meaning: "lock" }] },
    "-102": { summary: "reads a lock's FileInfoBlock", parameters: [{ name: "D1", meaning: "lock" }, { name: "D2", meaning: "FileInfoBlock" }] },
    "-108": { summary: "reads the next entry of a locked drawer", parameters: [{ name: "D1", meaning: "lock" }, { name: "D2", meaning: "FileInfoBlock" }] },
    "-114": { summary: "reports a volume's free space", parameters: [{ name: "D1", meaning: "lock" }, { name: "D2", meaning: "InfoData structure" }] },
    "-120": { summary: "creates a drawer", parameters: [{ name: "D1", meaning: "name" }] },
    "-126": { summary: "changes the current drawer", parameters: [{ name: "D1", meaning: "lock" }] },
    "-132": { summary: "returns the code for the most recent error", parameters: [] },
    "-150": { summary: "loads an executable into memory", parameters: [{ name: "D1", meaning: "file name" }] },
    "-156": { summary: "unloads a previously loaded segment", parameters: [{ name: "D1", meaning: "segment list" }] },
    "-180": { summary: "sets a file's comment", parameters: [{ name: "D1", meaning: "name" }, { name: "D2", meaning: "comment, up to 79 characters" }] },
    "-186": { summary: "sets a file's protection bits", parameters: [{ name: "D1", meaning: "name" }, { name: "D2", bits: [
      { mask: 1, set: "delete protected", clear: "deletable" },
      { mask: 2, set: "not executable", clear: "executable" },
      { mask: 4, set: "write protected", clear: "writable" },
      { mask: 8, set: "not readable", clear: "readable" },
      { mask: 16, set: "archived since the last change", clear: "changed since the last archive" },
      { mask: 32, set: "pure and re-entrant", clear: "not marked pure" },
      { mask: 64, set: "an AmigaDOS script", clear: "not a script" },
    ] }] },
    "-192": { summary: "reads the system date and time", parameters: [{ name: "D1", meaning: "DateStamp structure" }] },
    "-198": { summary: "waits for a number of fiftieths of a second", parameters: [{ name: "D1", unit: "fiftieth-of-a-second", meaning: "delay" }] },
    "-210": { summary: "returns a lock on the parent drawer", parameters: [{ name: "D1", meaning: "lock" }] },
    "-222": { summary: "runs a command line", parameters: [{ name: "D1", meaning: "command string" }, { name: "D2", meaning: "input handle" }, { name: "D3", meaning: "output handle" }] },
  });

  const GRAPHICS = Object.freeze({
    "-240": { summary: "waits for the blitter to finish", parameters: [] },
    "-246": { summary: "fills a raster with one colour", parameters: [{ name: "A1", meaning: "RastPort" }, { name: "D0", meaning: "pen number" }] },
    "-270": { summary: "renders text into a RastPort", parameters: [{ name: "A1", meaning: "RastPort" }, { name: "A0", meaning: "text" }, { name: "D0", meaning: "character count" }] },
    "-306": { summary: "moves the graphics pen", parameters: [{ name: "A1", meaning: "RastPort" }, { name: "D0", meaning: "x" }, { name: "D1", meaning: "y" }] },
    "-312": { summary: "draws a line to a point", parameters: [{ name: "A1", meaning: "RastPort" }, { name: "D0", meaning: "x" }, { name: "D1", meaning: "y" }] },
    "-354": { summary: "sets the primary drawing pen", parameters: [{ name: "A1", meaning: "RastPort" }, { name: "D0", meaning: "pen number" }] },
    "-360": { summary: "sets the secondary drawing pen", parameters: [{ name: "A1", meaning: "RastPort" }, { name: "D0", meaning: "pen number" }] },
    "-366": { summary: "sets the drawing mode", parameters: [{ name: "A1", meaning: "RastPort" }, { name: "D0", values: DRAW_MODES }] },
  });

  const INTUITION = Object.freeze({
    "-198": { summary: "opens a screen", parameters: [{ name: "A0", meaning: "NewScreen structure" }] },
    "-204": { summary: "opens a window", parameters: [{ name: "A0", meaning: "NewWindow structure" }] },
    "-72": { summary: "closes a window", parameters: [{ name: "A0", meaning: "Window" }] },
    "-462": { summary: "closes a screen", parameters: [{ name: "A0", meaning: "Screen" }] },
    "-342": { summary: "flashes the screen instead of sounding a bell", parameters: [{ name: "A0", meaning: "Screen, or 0 for every screen" }] },
    "-348": { summary: "shows a simple requester and waits for an answer", parameters: [{ name: "A0", meaning: "Window" }, { name: "A1", meaning: "body text" }, { name: "A2", meaning: "positive text" }, { name: "A3", meaning: "negative text" }] },
  });

  const LIBRARIES = Object.freeze({
    "exec.library": EXEC,
    "dos.library": DOS,
    "graphics.library": GRAPHICS,
    "intuition.library": INTUITION,
  });

  const DEFAULT_MACHINES = Object.freeze(["a500", "a600", "a1200", "a3000", "a4000"]);

  // A register value is read as a number and written as a number, so both
  // forms are shown: the decimal the source used and the hexadecimal an
  // Amiga reference manual documents the field in.
  function formatValue(value) {
    if (value == null) return "unknown";
    if (typeof value !== "number") return String(value);
    const magnitude = Math.abs(value).toString(16).toUpperCase();
    return `${value} (${value < 0 ? "-" : ""}$${magnitude})`;
  }

  function parameterText(spec, value, context) {
    if (!spec) return "";
    const shown = formatValue(value);
    if (value == null) return `${spec.name} was not proved on this path`;
    if (spec.values) {
      const known = spec.values[String(value)];
      return known ? `${spec.name}=${shown}: ${known}` : `${spec.name}=${shown}: ${spec.otherwise || "an undocumented or system-specific value"}`;
    }
    if (spec.bits) {
      const set = spec.bits
        .map(bit => ((value & bit.mask) ? bit.set : bit.clear))
        .filter(Boolean);
      return `${spec.name}=${shown}: ${set.join("; ")}`;
    }
    if (spec.unit) return `${spec.name}=${shown} ${spec.unit}${Math.abs(value) === 1 ? "" : "s"}${spec.meaning ? ` (${spec.meaning})` : ""}`;
    if (spec.type === "character" && typeof value === "number" && value >= 32 && value <= 126) {
      return `${spec.name}=${shown} ('${String.fromCharCode(value)}')`;
    }
    void context;
    return `${spec.name}=${shown}${spec.meaning ? `: ${spec.meaning}` : ""}`;
  }

  function explainParameters(specs, values) {
    const descriptions = [];
    const context = {};
    let offset = 0;
    for (const spec of specs || []) {
      const value = values[offset++];
      context[spec.name] = value;
      descriptions.push(parameterText(spec, value, context));
    }
    if (values.length > offset) descriptions.push(`additional register values: ${values.slice(offset).map(formatValue).join(", ")}`);
    return descriptions;
  }

  // The vector offset is the reason code; the registers are its parameters.
  // The library name is often unknown at the call site, so exec.library is
  // reported as the fallback and stated as such rather than assumed silently.
  function explainLibraryCall(offset, library, registers) {
    const key = String(Number(offset));
    const table = LIBRARIES[library] || EXEC;
    const spec = table[key] || (library && library !== "exec.library" ? EXEC[key] : null);
    const resolved = table[key] ? (library || "exec.library") : "exec.library";
    if (!spec) {
      return {
        summary: `calls vector ${key} of ${library || "the library whose base is in A6"}, which is not in this catalogue`,
        details: [`Register values: ${(registers || []).map(formatValue).join(", ") || "none proved"}`],
        platforms: DEFAULT_MACHINES,
        requires: "the autodocs for the library in A6",
      };
    }
    return {
      summary: `${resolved}: ${spec.summary}`,
      details: explainParameters(spec.parameters, registers || []),
      platforms: spec.platforms || DEFAULT_MACHINES,
      requires: spec.requires || `${resolved} open in A6`,
    };
  }

  // Retained under the previous names so the editor's call sites are stable.
  function explainOsbyte(reason, x, y) {
    return explainLibraryCall(reason, "exec.library", [x, y]);
  }

  function explainVdu(bytes, complete = true) {
    const values = Array.isArray(bytes) ? bytes : [bytes];
    const register = GRAPHICS[String(Number(values[0]))];
    if (!register) {
      return {
        summary: "writes to a custom-chip register or a graphics vector that is not in this catalogue",
        details: [`Values: ${values.map(formatValue).join(", ")}${complete ? "" : " (constant prefix only)"}`],
        platforms: DEFAULT_MACHINES,
        requires: "the Amiga hardware reference manual",
      };
    }
    return {
      summary: `graphics.library: ${register.summary}`,
      details: explainParameters(register.parameters, values.slice(1)),
      platforms: DEFAULT_MACHINES,
      requires: "graphics.library open in A6",
    };
  }

  const BASIC_CALLS = Object.freeze({
    LIBRARY: { summary: "makes an Amiga library's functions callable from AmigaBASIC", parameters: [{ name: "name", meaning: "library name, without the .library suffix" }] },
    PEEK: { summary: "reads one byte from an address", parameters: [{ name: "address", meaning: "byte address" }] },
    PEEKW: { summary: "reads one word from an even address", parameters: [{ name: "address", meaning: "word address" }] },
    PEEKL: { summary: "reads one long from an even address", parameters: [{ name: "address", meaning: "long address" }] },
    POKE: { summary: "writes one byte to an address", parameters: [{ name: "address", meaning: "byte address" }, { name: "value", meaning: "byte to write" }] },
    POKEW: { summary: "writes one word to an even address", parameters: [{ name: "address", meaning: "word address" }, { name: "value", meaning: "word to write" }] },
    POKEL: { summary: "writes one long to an even address", parameters: [{ name: "address", meaning: "long address" }, { name: "value", meaning: "long to write" }] },
    SADD: { summary: "returns the address of a string's characters", parameters: [{ name: "string$", meaning: "string variable" }] },
    VARPTR: { summary: "returns the address of a variable", parameters: [{ name: "variable", meaning: "variable name" }] },
    WAVE: { summary: "defines an audio waveform for a channel", parameters: [{ name: "channel", meaning: "0 to 3" }, { name: "waveform", meaning: "waveform array or SIN" }] },
    SOUND: { summary: "plays a note on one of the four audio channels", parameters: [
      { name: "frequency", meaning: "pitch in hertz, from 20 to about 15000" },
      { name: "duration", meaning: "length in eighteenths of a second, up to 77" },
      { name: "volume", meaning: "0 to 255" },
      { name: "voice", meaning: "0 to 3; voices 0 and 3 are the left channel, 1 and 2 the right" },
    ] },
    COLOR: { summary: "selects the foreground and background pens", parameters: [
      { name: "foreground", meaning: "pen number" },
      { name: "background", meaning: "pen number" },
    ] },
    SAY: { summary: "speaks a phonetic string through the narrator device", parameters: [{ name: "phonemes$", meaning: "translated phonetic text" }] },
    OBJECT: { summary: "controls a bob or sprite object", parameters: [{ name: "object", meaning: "object number" }] },
    PALETTE: { summary: "sets one entry of the display palette", parameters: [{ name: "index", meaning: "colour register" }, { name: "red", meaning: "0 to 1" }, { name: "green", meaning: "0 to 1" }, { name: "blue", meaning: "0 to 1" }] },
  });

  function explainBasicCall(name, values) {
    const spec = BASIC_CALLS[String(name || "").toUpperCase()];
    return spec
      ? {
        summary: spec.summary,
        details: explainParameters(spec.parameters, values),
        platforms: spec.platforms || DEFAULT_MACHINES,
        requires: spec.requires,
      }
      : null;
  }

  return Object.freeze({
    EXEC,
    DOS,
    GRAPHICS,
    INTUITION,
    LIBRARIES,
    OSBYTE: EXEC,
    VDU: GRAPHICS,
    BASIC_CALLS,
    explainLibraryCall,
    explainOsbyte,
    explainVdu,
    explainBasicCall,
  });
})();

if (typeof module !== "undefined") module.exports = window.AmigaCallCatalogue;
