window.AmigaHexEditor = (() => {
  const BYTES_PER_ROW = 16;
  const MAX_SELECTION = 1024 * 1024;
  const STACK_SIZES = [128, 256, 512, 1024];

  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
  const hex = (value, width = 2) => Number(value).toString(16).toUpperCase().padStart(width, "0");
  const printable = value => value >= 32 && value <= 126 ? String.fromCharCode(value) : ".";
  const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);

  function parseAddress(value) {
    const text = String(value || "").trim();
    if (!text) return null;
    if (/^\d+[dD]$/.test(text)) return Number.parseInt(text.slice(0, -1), 10);
    const cleaned = text.replace(/^(?:0x|&)/i, "");
    return /^[0-9a-f]+$/i.test(cleaned) ? Number.parseInt(cleaned, 16) : null;
  }

  function parsePaste(text, mode) {
    const value = String(text || "");
    const compact = value.replace(/(?:0x|&)/gi, "").replace(/[\s,;:_-]+/g, "");
    if (mode === "hex" && compact && /^[0-9a-f]+$/i.test(compact) && compact.length % 2 === 0) {
      return compact.match(/../g).map(pair => Number.parseInt(pair, 16));
    }
    return [...value].map(character => character.charCodeAt(0) & 0xFF);
  }

  function decision({ title, message, warning, actions }) {
    return new Promise(resolve => {
      const shade = document.createElement("div");
      shade.className = "hex-confirm-shade";
      shade.setAttribute("role", "alertdialog");
      shade.setAttribute("aria-modal", "true");
      shade.setAttribute("aria-labelledby", "hex-confirm-title");
      shade.innerHTML = `<section class="hex-confirm-card">
        <span class="hex-confirm-icon" aria-hidden="true">!</span>
        <h2 id="hex-confirm-title">${title}</h2>
        <p>${message}</p>
        ${warning ? `<div class="hex-confirm-warning">${warning}</div>` : ""}
        <div class="modal-actions">${actions.map(action => `<button type="button" class="button ${action.className || ""}" data-choice="${action.value}">${action.label}</button>`).join("")}</div>
      </section>`;
      const previouslyFocused = document.activeElement;
      const finish = choice => {
        shade.remove();
        previouslyFocused?.focus();
        resolve(choice);
      };
      shade.querySelectorAll("[data-choice]").forEach(button => {
        button.onclick = () => finish(button.dataset.choice);
      });
      shade.onkeydown = event => {
        if (event.key === "Escape") finish("cancel");
        if (event.key === "Tab") {
          const controls = [...shade.querySelectorAll("button:not(:disabled)")];
          const first = controls[0];
          const last = controls[controls.length - 1];
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
      };
      document.body.append(shade);
      shade.querySelector('[data-choice="cancel"]')?.focus();
    });
  }

  function editorMarkup(image, initialPageSize, scope, kicker, title, exportUrl) {
    const descriptorOption = image.hasDescriptor
      ? `<option value="descriptor">${image.descriptorName || "GEO geometry descriptor"}</option>`
      : "";
    return `<section class="hex-editor" tabindex="-1" aria-label="${scope === "file" ? "File" : "Raw image"} hex editor">
      <header class="hex-editor-head">
        <div><small>${kicker}</small><h2>${title}</h2><span class="hex-target-name"></span></div>
        <div class="hex-head-actions">
          <span class="hex-change-count">No staged changes</span>
          <button type="button" class="button small hex-save" disabled>Write changes</button>
          <button type="button" class="icon-button hex-close" title="Close hex editor" aria-label="Close hex editor">×</button>
        </div>
      </header>
      <nav class="editor-menubar hex-editor-menubar" aria-label="Hex editor menus">
        <details class="editor-menu"><summary>File</summary><div class="editor-menu-panel">
          <button type="button" class="hex-menu-save" disabled><span>Write Changes</span><kbd>Ctrl+S</kbd></button>
          ${exportUrl ? `<a href="${String(exportUrl).replaceAll("&", "&amp;")}"><span>Export original binary…</span></a>` : ""}
          <span class="editor-menu-separator" role="separator"></span>
          <button type="button" class="hex-menu-close"><span>Close</span><kbd>Esc</kbd></button>
        </div></details>
        <details class="editor-menu"><summary>Edit</summary><div class="editor-menu-panel">
          <button type="button" class="hex-menu-undo" disabled><span>Undo</span><kbd>Ctrl+Z</kbd></button>
          <button type="button" class="hex-menu-redo" disabled><span>Redo</span><kbd>Ctrl+Y</kbd></button>
          <span class="editor-menu-separator" role="separator"></span>
          <button type="button" class="hex-menu-copy-hex"><span>Copy Hex</span><kbd>Ctrl+C</kbd></button>
          <button type="button" class="hex-menu-copy-text"><span>Copy Text</span></button>
          <button type="button" class="hex-menu-paste"><span>Paste</span><kbd>Ctrl+V</kbd></button>
          <button type="button" class="hex-menu-fill"><span>Fill Selection…</span></button>
          <button type="button" class="hex-menu-revert-selection"><span>Revert Selection</span></button>
          <button type="button" class="hex-menu-revert-all"><span>Revert All</span></button>
        </div></details>
        <details class="editor-menu"><summary>Search</summary><div class="editor-menu-panel">
          <button type="button" class="hex-menu-find"><span>Find…</span><kbd>Ctrl+F</kbd></button>
          <button type="button" class="hex-menu-replace" ${image.readOnly ? "disabled" : ""}><span>Find and Replace…</span><kbd>Ctrl+H</kbd></button>
          <button type="button" class="hex-menu-find-previous"><span>Find Previous</span><kbd>Shift+Enter</kbd></button>
          <button type="button" class="hex-menu-find-next"><span>Find Next</span><kbd>Enter</kbd></button>
          <button type="button" class="hex-menu-goto"><span>Go to Offset…</span><kbd>Ctrl+G</kbd></button>
        </div></details>
        <details class="editor-menu"><summary>Analyse</summary><div class="editor-menu-panel">
          <button type="button" class="hex-menu-compare"><span>Compare with binary file…</span></button>
          <button type="button" class="hex-menu-next-difference" disabled><span>Next difference</span></button>
          <span class="editor-menu-separator" role="separator"></span>
          <label class="hex-template-menu">Structure template<select class="hex-template"><option value="auto">Automatic</option><option value="generic">Generic values</option><option value="boot-block">AmigaDOS boot block</option><option value="root-block">AmigaDOS root block</option><option value="rigid-disk">Rigid Disk Block</option><option value="kickstart-rom">Kickstart ROM header</option><option value="resident-tag">Resident module tag</option><option value="hardfile-geo">Hardfile GEO geometry</option><option value="dms-track">DMS header and track</option><option value="custom" hidden>Custom JSON template</option></select></label>
          <button type="button" class="hex-menu-load-template"><span>Load custom JSON template…</span></button><input class="hex-template-file" type="file" accept="application/json,.json" hidden>
        </div></details>
      </nav>
      <div class="hex-toolbar">
        <label ${scope === "file" ? "hidden" : ""}>Component<select class="hex-target"><option value="image">${image.name}</option>${descriptorOption}</select></label>
        <label>Go to offset<input class="hex-goto" spellcheck="false" placeholder="00000000"></label>
        <button type="button" class="button small hex-go">Go</button>
        <span class="hex-separator"></span>
        <button type="button" class="hex-nav hex-first" title="First page" aria-label="First hex page">|◀</button>
        <button type="button" class="hex-nav hex-previous" title="Previous page" aria-label="Previous hex page">◀</button>
        <button type="button" class="hex-nav hex-next" title="Next page" aria-label="Next hex page">▶</button>
        <button type="button" class="hex-nav hex-last" title="Last page" aria-label="Last hex page">▶|</button>
        <label>Page<select class="hex-page-size">${STACK_SIZES.map(size => `<option value="${size}" ${size === initialPageSize ? "selected" : ""}>${size} bytes</option>`).join("")}</select></label>
        <span class="hex-separator"></span>
        <button type="button" class="hex-nav hex-undo" disabled title="Undo byte edit (Ctrl/Cmd+Z)" aria-label="Undo byte edit">↶</button>
        <button type="button" class="hex-nav hex-redo" disabled title="Redo byte edit (Ctrl/Cmd+Y)" aria-label="Redo byte edit">↷</button>
      </div>
      <div class="hex-searchbar">
        <label>Find<select class="hex-search-mode"><option value="hex">Hex bytes</option><option value="text">Text</option></select></label>
        <input class="hex-search-query" spellcheck="false" placeholder="44 69 73 63" aria-label="Hex editor search value">
        <button type="button" class="button small hex-find-previous">Find previous</button>
        <button type="button" class="button small hex-find-next">Find next</button>
        <label>Replace<input class="hex-replace-query" spellcheck="false" placeholder="00 00 00 00" aria-label="Hex editor replacement value" ${image.readOnly ? "disabled" : ""}></label>
        <button type="button" class="button small hex-replace-next" ${image.readOnly ? "disabled" : ""}>Replace next</button>
        <label class="hex-check"><input class="hex-search-wrap" type="checkbox" checked> Wrap</label>
        <span class="hex-search-status" aria-live="polite"></span>
      </div>
      <div class="hex-workarea">
        <div class="hex-grid-shell">
          <div class="hex-grid-head"><span>OFFSET</span><span>${[...Array(16)].map((_item, index) => hex(index)).join(" ")}</span><span>ASCII</span></div>
          <div class="hex-grid" role="grid" aria-label="Image bytes"></div>
          <div class="hex-loading" hidden><span class="progress"><i></i></span><b>Reading bytes…</b></div>
        </div>
        <aside class="hex-inspector">
          <section><small>SELECTION</small><strong class="hex-selection-label">No byte selected</strong><span class="hex-selection-size"></span></section>
          <section class="hex-values"><small>VALUE INSPECTOR</small><dl></dl></section>
          <section><small>EDIT MODE</small><div class="hex-mode"><button type="button" data-mode="hex" class="active">HEX</button><button type="button" data-mode="ascii">ASCII</button></div><p>Type to replace bytes. Use Shift and the arrow keys to extend the selection.</p></section>
          <section><small>SELECTION TOOLS</small><div class="hex-side-actions"><button type="button" class="hex-copy-hex">Copy hex</button><button type="button" class="hex-copy-text">Copy text</button><button type="button" class="hex-paste">Paste</button><button type="button" class="hex-fill">Fill…</button><button type="button" class="hex-revert-selection">Revert selection</button><button type="button" class="hex-revert-all">Revert all</button></div></section>
          <section class="hex-change-list-section"><small>STAGED CHANGES</small><div class="hex-change-list"><em>None</em></div></section>
          <section class="hex-structure-section"><small>STRUCTURED VIEW</small><strong class="hex-structure-name">Generic values</strong><dl class="hex-structure-values"></dl></section>
          <section class="hex-comparison-section"><small>BINARY COMPARISON</small><strong class="hex-comparison-name">No comparison loaded</strong><span class="hex-comparison-summary"></span></section>
        </aside>
      </div>
      <footer class="hex-status"><span class="hex-position"></span><span class="hex-image-size"></span><span>Offsets are hexadecimal · append <b>d</b> for decimal</span></footer>
    </section>`;
  }

  async function open({ host, image, request, notify, onSaved, initialOffset = 0, initialPageSize = 256,
    endpoint = null, context = {}, scope = "image", kicker = null, title = "Hex editor", exportUrl = null }) {
    const pageSize = STACK_SIZES.includes(Number(initialPageSize)) ? Number(initialPageSize) : 256;
    const apiEndpoint = endpoint || `/api/images/${image.id}/hex`;
    const endpointUrl = (suffix = "", params = {}) => {
      const query = new URLSearchParams({ ...context, ...params });
      return `${apiEndpoint}${suffix}${query.size ? `?${query}` : ""}`;
    };
    const overlay = document.createElement("div");
    overlay.className = "hex-editor-overlay";
    overlay.innerHTML = editorMarkup(image, pageSize, scope, kicker || (scope === "file" ? "RAW FILE TOOLS" : "RAW IMAGE TOOLS"), title, exportUrl);
    host.append(overlay);
    const editor = overlay.querySelector(".hex-editor");
    const state = {
      target: "image",
      offset: Math.max(0, Number(initialOffset) || 0),
      pageSize,
      size: image.size || 0,
      version: null,
      bytes: new Map(),
      originals: new Map(),
      changes: new Map(),
      active: Math.max(0, Number(initialOffset) || 0),
      anchor: Math.max(0, Number(initialOffset) || 0),
      mode: "hex",
      highNibble: null,
      history: [],
      future: [],
      loading: false,
      closed: false,
      template: "auto",
      customTemplate: null,
      comparison: null,
    };
    let resolveClosed;
    const closed = new Promise(resolve => { resolveClosed = resolve; });
    const $ = selector => overlay.querySelector(selector);

    function effectiveByte(offset) {
      return state.changes.has(offset) ? state.changes.get(offset) : state.bytes.get(offset);
    }

    function selectedRange() {
      return [Math.min(state.anchor, state.active), Math.max(state.anchor, state.active)];
    }

    function selectedOffsets() {
      const [start, end] = selectedRange();
      const result = [];
      for (let offset = start; offset <= end && offset < state.size; offset += 1) result.push(offset);
      return result;
    }

    function stageByte(offset, value) {
      if (!state.originals.has(offset)) state.originals.set(offset, effectiveByte(offset));
      const original = state.originals.get(offset);
      if (value === original) state.changes.delete(offset); else state.changes.set(offset, value);
    }

    function applyEdit(edits, record = true) {
      if (image.readOnly) return;
      const normalised = edits
        .filter(edit => edit.offset >= 0 && edit.offset < state.size)
        .map(edit => ({ offset: edit.offset, before: effectiveByte(edit.offset), after: edit.after & 0xFF }))
        .filter(edit => edit.before != null && edit.before !== edit.after);
      if (!normalised.length) return;
      normalised.forEach(edit => stageByte(edit.offset, edit.after));
      if (record) {
        state.history.push(normalised);
        state.future.length = 0;
      }
      render();
    }

    function undo() {
      const edits = state.history.pop();
      if (!edits) return;
      edits.forEach(edit => stageByte(edit.offset, edit.before));
      state.future.push(edits);
      render();
    }

    function redo() {
      const edits = state.future.pop();
      if (!edits) return;
      edits.forEach(edit => stageByte(edit.offset, edit.after));
      state.history.push(edits);
      render();
    }

    function valuesMarkup() {
      const offset = state.active;
      const values = [0, 1, 2, 3].map(delta => effectiveByte(offset + delta));
      if (values[0] == null) return "<dt>Value</dt><dd>-</dd>";
      const u16le = values[1] == null ? null : values[0] | values[1] << 8;
      const u16be = values[1] == null ? null : values[0] << 8 | values[1];
      const u32le = values.some(value => value == null) ? null : (values[0] | values[1] << 8 | values[2] << 16 | values[3] << 24) >>> 0;
      const u32be = values.some(value => value == null) ? null : (values[0] * 0x1000000 + values[1] * 0x10000 + values[2] * 0x100 + values[3]) >>> 0;
      const rows = [
        ["u8", `${values[0]} · &${hex(values[0])}`],
        ["i8", String(values[0] > 127 ? values[0] - 256 : values[0])],
        ["u16 LE", u16le == null ? "-" : `${u16le} · &${hex(u16le, 4)}`],
        ["u16 BE", u16be == null ? "-" : `${u16be} · &${hex(u16be, 4)}`],
        ["u32 LE", u32le == null ? "-" : `${u32le} · &${hex(u32le, 8)}`],
        ["u32 BE", u32be == null ? "-" : `${u32be} · &${hex(u32be, 8)}`],
        ["ASCII", printable(values[0])],
      ];
      return rows.map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join("");
    }

    const loadedValues = (offset, count) => Array.from({ length: count }, (_item, index) => effectiveByte(offset + index));
    const word = (values, offset, little = true) => values[offset] == null || values[offset + 1] == null ? null : little ? values[offset] | values[offset + 1] << 8 : values[offset] << 8 | values[offset + 1];
    const dword = (values, offset, little = true) => {
      const part = values.slice(offset, offset + 4); if (part.some(value => value == null) || part.length < 4) return null;
      return little ? (part[0] | part[1] << 8 | part[2] << 16 | part[3] << 24) >>> 0 : (part[0] * 0x1000000 + part[1] * 0x10000 + part[2] * 0x100 + part[3]) >>> 0;
    };
    const textValue = values => values.filter(value => value != null && value !== 0).map(value => printable(value)).join("").trim();
    function detectedTemplate() {
      const first = loadedValues(0, 16);
      const lowerName = String(image.name || "").toLowerCase();
      const signature = textValue(first.slice(0, 4));
      if (lowerName.endsWith(".dms") || signature === "DMS!") return "dms-track";
      if (lowerName.endsWith(".geo")) return "hardfile-geo";
      if (signature === "RDSK") return "rigid-disk";
      if (lowerName.endsWith(".hdf") || lowerName.endsWith(".hda")) return "rigid-disk";
      // A Kickstart image begins with $1111 or $1114 followed by a JMP.
      if ((word(first, 0, false) === 0x1111 || word(first, 0, false) === 0x1114) && word(first, 4, false) === 0x4EF9) return "kickstart-rom";
      if (word(first, 0, false) === 0x4AFC) return "resident-tag";
      if (signature.startsWith("DOS") || signature.startsWith("PFS") || signature.startsWith("SFS")) return "boot-block";
      if (dword(first, 0, false) === 2) return "root-block";
      return "generic";
    }
    function renderStructure() {
      const template = state.template === "auto" ? detectedTemplate() : state.template;
      const fixedTemplate = ["boot-block", "root-block", "rigid-disk", "kickstart-rom", "resident-tag", "hardfile-geo", "dms-track"].includes(template);
      const base = fixedTemplate ? 0 : state.active;
      const values = loadedValues(base, 512);
      const row = (name, value) => value == null || value === "" ? "" : `<dt>${escapeHtml(name)}</dt><dd>${escapeHtml(value)}</dd>`;
      // Every structure the Amiga writes on disk or in ROM is big-endian.
      const long = offset => dword(values, offset, false);
      const short = offset => word(values, offset, false);
      // AmigaDOS stores a name as a BSTR: a length byte followed by that many
      // characters, in a fixed-size field.
      const bstr = offset => {
        const length = values[offset];
        return length == null ? null : textValue(values.slice(offset + 1, offset + 1 + Math.min(length, 30)));
      };
      let name = "Generic values";
      let rows = valuesMarkup();
      if (template === "boot-block") {
        name = "AmigaDOS boot block";
        const dosType = values[3];
        const flags = dosType == null ? null : [
          dosType & 1 ? "FFS" : "OFS",
          dosType & 2 ? "international" : "original character set",
          dosType & 4 ? "directory cache" : "no directory cache",
        ].join(", ");
        rows = [
          row("Signature", textValue(values.slice(0, 3))),
          row("DOS type", dosType == null ? null : `DOS\\${dosType} (${flags})`),
          row("Boot-block checksum", long(4) == null ? null : `$${hex(long(4), 8)}`),
          row("Root block", long(8)),
          row("Boot code", values.slice(12, 24).every(value => value === 0) ? "None; the disk is not bootable" : "Present"),
        ].join("");
      } else if (template === "root-block") {
        name = "AmigaDOS root block";
        const blockSize = 512;
        rows = [
          row("Primary type", long(0) === 2 ? "2 (T_HEADER)" : long(0)),
          row("Hash table size", long(12)),
          row("Bitmap valid", long(blockSize - 200) === 0xFFFFFFFF ? "Yes" : "No; the volume needs validating"),
          row("First bitmap block", long(blockSize - 196)),
          row("Root protection", long(blockSize - 192) == null ? null : `$${hex(long(blockSize - 192), 8)}`),
          row("Last root change", `${long(blockSize - 92)} days, ${long(blockSize - 88)} minutes, ${long(blockSize - 84)} ticks`),
          row("Volume name", bstr(blockSize - 80)),
          row("Volume created", `${long(blockSize - 28)} days, ${long(blockSize - 24)} minutes, ${long(blockSize - 20)} ticks`),
          row("Secondary type", long(blockSize - 4) === 1 ? "1 (ST_ROOT)" : long(blockSize - 4)),
          row("Block checksum", long(20) == null ? null : `$${hex(long(20), 8)}`),
        ].join("");
      } else if (template === "rigid-disk") {
        name = "Rigid Disk Block";
        rows = [
          row("Identifier", textValue(values.slice(0, 4))),
          row("Block size in longs", long(4)),
          row("Checksum", long(8) == null ? null : `$${hex(long(8), 8)}`),
          row("Host identifier", long(12)),
          row("Block bytes", long(16)),
          row("Flags", long(20) == null ? null : `$${hex(long(20), 8)}`),
          row("First partition block", long(28)),
          row("First filesystem header block", long(32)),
          row("Cylinders", long(64)),
          row("Sectors per track", long(68)),
          row("Heads", long(72)),
          row("Low cylinder", long(88)),
          row("High cylinder", long(92)),
          row("Drive manufacturer", textValue(values.slice(160, 176))),
        ].join("");
      } else if (template === "kickstart-rom") {
        name = "Kickstart ROM header";
        const size = long(16);
        rows = [
          row("Header word", short(0) == null ? null : `$${hex(short(0), 4)} (${short(0) === 0x1114 ? "512 KiB or larger" : "256 KiB"})`),
          row("Jump instruction", short(4) === 0x4EF9 ? "JMP absolute long" : short(4) == null ? null : `$${hex(short(4), 4)}`),
          row("Entry point", long(6) == null ? null : `$${hex(long(6), 8)}`),
          row("Exec version", short(12)),
          row("Declared size", size == null ? null : `${size.toLocaleString()} bytes`),
        ].join("");
      } else if (template === "resident-tag") {
        name = "Resident module tag";
        const type = values[10];
        const typeNames = { 0: "NT_UNKNOWN", 1: "NT_TASK", 2: "NT_INTERRUPT", 3: "NT_DEVICE", 4: "NT_MSGPORT", 9: "NT_LIBRARY", 10: "NT_MEMORY", 11: "NT_RESOURCE" };
        rows = [
          row("Match word", short(0) == null ? null : `$${hex(short(0), 4)}${short(0) === 0x4AFC ? " (RTC_MATCHWORD)" : ""}`),
          row("Match tag", long(2) == null ? null : `$${hex(long(2), 8)}`),
          row("End of module", long(6) == null ? null : `$${hex(long(6), 8)}`),
          row("Flags", values[10] == null ? null : `$${hex(values[10])}`),
          row("Version", values[11]),
          row("Type", values[12] == null ? null : `${values[12]}${typeNames[values[12]] ? ` (${typeNames[values[12]]})` : ""}`),
          row("Priority", values[13] == null ? null : (values[13] > 127 ? values[13] - 256 : values[13])),
          row("Name pointer", long(14) == null ? null : `$${hex(long(14), 8)}`),
          row("Identification pointer", long(18) == null ? null : `$${hex(long(18), 8)}`),
          row("Initialisation routine", long(22) == null ? null : `$${hex(long(22), 8)}`),
          row("Node type byte", type == null ? null : `$${hex(type)}`),
        ].join("");
      } else if (template === "hardfile-geo") {
        name = "UAE hardfile geometry sidecar";
        const text = values.filter(value => value != null).map(value => printable(value)).join("");
        const field = key => text.match(new RegExp(`^\\s*${key}\\s*=\\s*(\\S+)`, "im"))?.[1] || null;
        rows = [
          row("Surfaces", field("surfaces")),
          row("Blocks per track", field("blockspertrack")),
          row("Cylinders", field("cylinders")),
          row("Sector size", field("blocksize") || field("sectorsize")),
          row("Reserved blocks", field("reserved")),
          row("Descriptor bytes", Math.min(state.size, 512)),
        ].join("");
      } else if (template === "dms-track") {
        name = "DMS archive header and first track";
        const modes = { 0: "NOCOMP", 1: "SIMPLE", 2: "QUICK", 3: "MEDIUM", 4: "DEEP", 5: "HEAVY1", 6: "HEAVY2" };
        const trackMode = values[73];
        rows = [
          row("Signature", textValue(values.slice(0, 4))),
          row("Low track", short(14)),
          row("High track", short(16)),
          row("Packed size", long(18)),
          row("Unpacked size", long(22)),
          row("Archive compression", values[53] == null ? null : `${values[53]}${modes[values[53]] ? ` (${modes[values[53]]})` : ""}`),
          row("First track header", textValue(values.slice(56, 58))),
          row("Track number", short(58)),
          row("Packed length", short(62)),
          row("Unpacked length", short(66)),
          row("Track compression", trackMode == null ? null : `${trackMode}${modes[trackMode] ? ` (${modes[trackMode]})` : ""}`),
          row("Packed checksum", short(70) == null ? null : `$${hex(short(70), 4)}`),
          row("Header checksum", short(74) == null ? null : `$${hex(short(74), 4)}`),
        ].join("");
      } else if (template === "custom" && state.customTemplate) {
        name = state.customTemplate.name;
        const typeValue = (field, fieldValues) => {
          if (field.type === "u8") return fieldValues[0];
          if (field.type === "u16le") return word(fieldValues, 0, true);
          if (field.type === "u16be") return word(fieldValues, 0, false);
          if (field.type === "u32le") return dword(fieldValues, 0, true);
          if (field.type === "u32be") return dword(fieldValues, 0, false);
          if (field.type === "hex") return fieldValues.map(value => value == null ? "??" : hex(value)).join(" ");
          return textValue(fieldValues);
        };
        rows = state.customTemplate.fields.map(field => row(field.name, typeValue(field, loadedValues(base + field.offset, field.length)))).join("");
      }
      $(".hex-structure-name").textContent = name;
      $(".hex-structure-values").innerHTML = rows || "<dt>Data</dt><dd>Load the header page to decode it.</dd>";
    }

    async function loadCustomTemplate(file) {
      if (!file) return;
      let document;
      try { document = JSON.parse(await file.text()); }
      catch (error) { return notify(`Custom template JSON is invalid: ${error.message}`, true); }
      const fields = Array.isArray(document.fields) ? document.fields.slice(0, 128).map(field => ({
        name: String(field?.name || "Field").slice(0, 80),
        offset: Number(field?.offset),
        type: String(field?.type || "hex").toLowerCase(),
        length: Number(field?.length || (["u16le", "u16be"].includes(field?.type) ? 2 : ["u32le", "u32be"].includes(field?.type) ? 4 : 1)),
      })) : [];
      if (!fields.length || fields.some(field => !Number.isInteger(field.offset) || field.offset < 0 || field.offset > 4095 || !Number.isInteger(field.length) || field.length < 1 || field.length > 256 || !["u8", "u16le", "u16be", "u32le", "u32be", "ascii", "hex"].includes(field.type))) {
        return notify("A custom template needs valid fields with offset, type and bounded length values.", true);
      }
      state.customTemplate = { name: String(document.name || file.name || "Custom template").slice(0, 120), fields };
      state.template = "custom";
      $(".hex-template").value = "custom";
      const extent = Math.max(...fields.map(field => field.offset + field.length));
      await ensureRange(state.active, Math.min(state.size - 1, state.active + extent - 1));
      renderStructure();
      notify(`${state.customTemplate.name} loaded. Offsets are relative to the selected byte.`);
    }

    function renderComparison() {
      const comparison = state.comparison;
      $(".hex-menu-next-difference").disabled = !comparison?.differences.length;
      $(".hex-comparison-name").textContent = comparison ? comparison.name : "No comparison loaded";
      $(".hex-comparison-summary").textContent = comparison ? `${comparison.count.toLocaleString()} differing byte${comparison.count === 1 ? "" : "s"}${comparison.sizeMismatch ? ` · sizes differ (${state.size.toLocaleString()} vs ${comparison.size.toLocaleString()})` : ""}${comparison.truncated ? " · compared first 1 GiB" : ""}${comparison.navigationTruncated ? " · navigation shows the first 100,000 differences" : ""}` : "";
    }

    function comparisonContains(offset) {
      const ranges = state.comparison?.ranges || [];
      let low = 0; let high = ranges.length - 1;
      while (low <= high) {
        const middle = (low + high) >> 1;
        const range = ranges[middle];
        if (offset < range[0]) high = middle - 1;
        else if (offset > range[1]) low = middle + 1;
        else return true;
      }
      return false;
    }

    function renderChanges() {
      const changes = [...state.changes.entries()].sort((a, b) => a[0] - b[0]);
      $(".hex-change-count").textContent = changes.length ? `${changes.length.toLocaleString()} changed byte${changes.length === 1 ? "" : "s"}` : "No staged changes";
      $(".hex-save").disabled = !changes.length || image.readOnly;
      $(".hex-menu-save").disabled = !changes.length || image.readOnly;
      $(".hex-revert-all").disabled = !changes.length;
      $(".hex-paste").disabled = image.readOnly;
      $(".hex-fill").disabled = image.readOnly;
      $(".hex-undo").disabled = !state.history.length;
      $(".hex-redo").disabled = !state.future.length;
      $(".hex-menu-undo").disabled = !state.history.length;
      $(".hex-menu-redo").disabled = !state.future.length;
      $(".hex-menu-paste").disabled = image.readOnly;
      $(".hex-menu-fill").disabled = image.readOnly;
      $(".hex-menu-revert-all").disabled = !changes.length;
      $(".hex-change-list").innerHTML = changes.length
        ? changes.slice(-80).reverse().map(([offset, value]) => `<button type="button" data-change-offset="${offset}"><b>&${hex(offset, Math.max(6, state.size.toString(16).length))}</b><span>${hex(state.originals.get(offset))} → ${hex(value)}</span></button>`).join("")
        : "<em>None</em>";
      overlay.querySelectorAll("[data-change-offset]").forEach(button => {
        button.onclick = () => goTo(Number(button.dataset.changeOffset));
      });
    }

    function renderGrid() {
      const addressWidth = Math.max(6, state.size.toString(16).length);
      const [selectionStart, selectionEnd] = selectedRange();
      const rows = [];
      for (let rowOffset = state.offset; rowOffset < Math.min(state.offset + state.pageSize, state.size); rowOffset += BYTES_PER_ROW) {
        const byteCells = [];
        const asciiCells = [];
        for (let column = 0; column < BYTES_PER_ROW; column += 1) {
          const offset = rowOffset + column;
          if (offset >= state.size || !state.bytes.has(offset)) {
            byteCells.push("<span class=\"hex-blank\"></span>");
            asciiCells.push("<span class=\"hex-blank\"></span>");
            continue;
          }
          const value = effectiveByte(offset);
          const classes = [
            offset >= selectionStart && offset <= selectionEnd ? "selected" : "",
            offset === state.active ? "active" : "",
            state.changes.has(offset) ? "changed" : "",
            comparisonContains(offset) ? "different" : "",
          ].filter(Boolean).join(" ");
          byteCells.push(`<button type="button" class="hex-byte ${classes}" data-offset="${offset}" data-cell-mode="hex" role="gridcell">${hex(value)}</button>`);
          asciiCells.push(`<button type="button" class="hex-char ${classes}" data-offset="${offset}" data-cell-mode="ascii" role="gridcell">${printable(value)}</button>`);
        }
        rows.push(`<div class="hex-row" role="row"><button type="button" class="hex-address" data-address="${rowOffset}">${hex(rowOffset, addressWidth)}</button><div class="hex-bytes">${byteCells.join("")}</div><div class="hex-ascii">${asciiCells.join("")}</div></div>`);
      }
      $(".hex-grid").innerHTML = rows.join("") || '<div class="hex-empty">This component is empty.</div>';
      overlay.querySelectorAll("[data-offset]").forEach(button => {
        button.onclick = event => {
          const offset = Number(button.dataset.offset);
          state.mode = button.dataset.cellMode;
          state.active = offset;
          if (!event.shiftKey) state.anchor = offset;
          state.highNibble = null;
          render();
          editor.focus();
        };
      });
      overlay.querySelectorAll("[data-address]").forEach(button => {
        button.onclick = () => {
          state.anchor = Number(button.dataset.address);
          state.active = Math.min(state.anchor + 15, state.size - 1);
          render();
        };
      });
    }

    function render() {
      renderGrid();
      renderChanges();
      renderStructure();
      renderComparison();
      const [start, end] = selectedRange();
      const count = Math.max(0, end - start + 1);
      $(".hex-selection-label").textContent = count > 1 ? `&${hex(start)} to &${hex(end)}` : `&${hex(state.active)}`;
      $(".hex-selection-size").textContent = `${count.toLocaleString()} byte${count === 1 ? "" : "s"} selected`;
      $(".hex-values dl").innerHTML = valuesMarkup();
      $(".hex-position").textContent = `Page &${hex(state.offset)} · cursor &${hex(state.active)}`;
      $(".hex-image-size").textContent = `${state.size.toLocaleString()} bytes · &${hex(state.size)}`;
      $(".hex-target-name").textContent = $(".hex-target").selectedOptions[0]?.textContent || image.name;
      overlay.querySelectorAll(".hex-mode button").forEach(button => button.classList.toggle("active", button.dataset.mode === state.mode));
      $(".hex-previous").disabled = state.offset <= 0;
      $(".hex-first").disabled = state.offset <= 0;
      $(".hex-next").disabled = state.offset + state.pageSize >= state.size;
      $(".hex-last").disabled = state.offset + state.pageSize >= state.size;
      $(".hex-revert-selection").disabled = !selectedOffsets().some(offset => state.changes.has(offset));
    }

    async function loadPage(offset = state.offset, { resetVersion = false } = {}) {
      state.loading = true;
      $(".hex-loading").hidden = false;
      try {
        const aligned = Math.floor(clamp(offset, 0, Math.max(0, state.size - 1)) / state.pageSize) * state.pageSize;
        const data = await request(endpointUrl("", { offset: aligned, length: state.pageSize, target: state.target }));
        if (!resetVersion && state.version && state.changes.size && data.version !== state.version) {
          throw new Error("The image changed outside the hex editor. Close it and reopen before continuing.");
        }
        state.offset = data.offset;
        state.size = data.size;
        state.version = data.version;
        const values = String(data.data).match(/../g) || [];
        values.forEach((value, index) => state.bytes.set(data.offset + index, Number.parseInt(value, 16)));
        state.active = clamp(state.active, data.offset, Math.max(data.offset, data.offset + values.length - 1));
        image.readOnly = Boolean(data.readOnly);
        render();
      } catch (error) {
        notify(`Hex editor: ${error.message}`, true);
      } finally {
        state.loading = false;
        $(".hex-loading").hidden = true;
      }
    }

    async function goTo(offset, extend = false) {
      if (!Number.isInteger(offset) || offset < 0 || offset >= state.size) {
        notify("That offset is outside the image.", true);
        return;
      }
      if (extend) offset = clamp(offset, state.anchor - MAX_SELECTION + 1, state.anchor + MAX_SELECTION - 1);
      if (offset < state.offset || offset >= state.offset + state.pageSize) await loadPage(offset);
      state.active = offset;
      if (!extend) state.anchor = offset;
      state.highNibble = null;
      render();
    }

    function serialiseChanges() {
      const entries = [...state.changes.entries()].sort((a, b) => a[0] - b[0]);
      const chunks = [];
      for (const [offset, value] of entries) {
        const previous = chunks[chunks.length - 1];
        if (previous && previous.offset + previous.values.length === offset) previous.values.push(value);
        else chunks.push({ offset, values: [value] });
      }
      return chunks.map(chunk => ({ offset: chunk.offset, data: chunk.values.map(value => hex(value)).join("") }));
    }

    async function save() {
      if (!state.changes.size) return true;
      const choice = await decision({
        title: "This is dangerous. Are you sure?",
        message: `You are about to overwrite ${state.changes.size.toLocaleString()} raw byte${state.changes.size === 1 ? "" : "s"} in ${$(".hex-target-name").textContent}. ${scope === "file" ? "Raw edits can corrupt tokenised programs, loaders and executable code." : "Raw edits bypass filesystem rules and can make the image unbootable or destroy its catalogue."}`,
        warning: "An automatic undo checkpoint will be created first. Keep the image open and run Analyse → Image health dashboard after writing.",
        actions: [
          { value: "cancel", label: "Cancel", className: "ghost" },
          { value: "write", label: "Write raw bytes", className: "danger" },
        ],
      });
      if (choice !== "write") return false;
      const button = $(".hex-save");
      button.disabled = true;
      button.textContent = "Writing…";
      try {
        const result = await request(endpointUrl(), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            target: state.target,
            ...context,
            version: state.version,
            confirmed: true,
            changes: serialiseChanges(),
          }),
        });
        state.version = result.version;
        state.changes.clear();
        state.originals.clear();
        state.history.length = 0;
        state.future.length = 0;
        Object.assign(image, result.image);
        onSaved?.(result.image);
        await loadPage(state.offset, { resetVersion: true });
        notify(`${result.written.toLocaleString()} raw byte${result.written === 1 ? "" : "s"} written. An undo checkpoint is available.`);
        return true;
      } catch (error) {
        notify(`Could not write raw bytes: ${error.message}`, true);
        return false;
      } finally {
        button.textContent = "Write changes";
        renderChanges();
      }
    }

    async function closeEditor() {
      if (state.closed) return;
      if (state.changes.size) {
        const choice = await decision({
          title: "Unsaved raw changes",
          message: `${state.changes.size.toLocaleString()} changed byte${state.changes.size === 1 ? " is" : "s are"} still staged in the hex editor.`,
          warning: "Closing without writing discards only the staged hex edits. The underlying image is unchanged.",
          actions: [
            { value: "cancel", label: "Keep editing", className: "ghost" },
            { value: "discard", label: "Discard changes", className: "danger" },
            { value: "save", label: "Review and write", className: "primary" },
          ],
        });
        if (choice === "cancel") return;
        if (choice === "save" && !await save()) return;
      }
      state.closed = true;
      overlay.remove();
      resolveClosed();
    }

    async function changeTarget(target) {
      if (target === state.target) return;
      if (state.changes.size) {
        const choice = await decision({
          title: "Discard staged changes?",
          message: "Changing image components clears the raw edits currently staged in this editor.",
          actions: [
            { value: "cancel", label: "Cancel", className: "ghost" },
            { value: "discard", label: "Discard and switch", className: "danger" },
          ],
        });
        if (choice !== "discard") {
          $(".hex-target").value = state.target;
          return;
        }
      }
      state.target = target;
      state.offset = state.active = state.anchor = 0;
      state.version = null;
      state.bytes.clear();
      state.changes.clear();
      state.originals.clear();
      state.history.length = state.future.length = 0;
      await loadPage(0, { resetVersion: true });
    }

    function searchBytes(selector) {
      const value = $(selector).value;
      return value ? parsePaste(value, $(".hex-search-mode").value === "text" ? "ascii" : "hex") : [];
    }

    async function selectMatch(offset, length) {
      await goTo(offset);
      if (length > 1) await goTo(Math.min(state.size - 1, offset + length - 1), true);
    }

    async function find(direction) {
      const queryText = $(".hex-search-query").value;
      if (!queryText) return $(".hex-search-query").focus();
      const start = direction === "forward" ? state.active + 1 : state.active - 1;
      const query = {
        query: queryText,
        mode: $(".hex-search-mode").value,
        start,
        direction,
        wrap: $(".hex-search-wrap").checked,
        target: state.target,
      };
      $(".hex-search-status").textContent = "Searching…";
      try {
        const result = await request(endpointUrl("/search", query));
        if (result.offset == null) {
          $(".hex-search-status").textContent = "Not found";
          return;
        }
        await selectMatch(result.offset, Math.max(1, searchBytes(".hex-search-query").length));
        $(".hex-search-status").textContent = `${result.wrapped ? "Wrapped · " : ""}found at &${hex(result.offset)}`;
        return result.offset;
      } catch (error) {
        $(".hex-search-status").textContent = error.message;
      }
    }

    async function replaceNext() {
      if (image.readOnly) return;
      const findValues = searchBytes(".hex-search-query");
      const replacement = searchBytes(".hex-replace-query");
      if (!findValues.length) return $(".hex-search-query").focus();
      if (!replacement.length) return $(".hex-replace-query").focus();
      if (replacement.length !== findValues.length) {
        $(".hex-search-status").textContent = "Replacement must contain the same number of bytes";
        return;
      }
      await ensureRange(...selectedRange());
      let offsets = selectedOffsets();
      const selectionMatches = offsets.length === findValues.length
        && offsets.every((offset, index) => effectiveByte(offset) === findValues[index]);
      if (!selectionMatches) {
        const found = await find("forward");
        if (found == null) return;
        await ensureRange(...selectedRange());
        offsets = selectedOffsets();
      }
      applyEdit(offsets.map((offset, index) => ({ offset, after: replacement[index] })));
      $(".hex-search-status").textContent = `Replaced ${replacement.length.toLocaleString()} byte${replacement.length === 1 ? "" : "s"} at &${hex(offsets[0])}`;
    }

    async function copySelection(asText) {
      await ensureRange(...selectedRange());
      const values = selectedOffsets().map(offset => effectiveByte(offset)).filter(value => value != null);
      const text = asText ? values.map(value => String.fromCharCode(value)).join("") : values.map(value => hex(value)).join(" ");
      try {
        await navigator.clipboard.writeText(text);
        notify(`Copied ${values.length.toLocaleString()} byte${values.length === 1 ? "" : "s"}.`);
      } catch (_error) {
        notify("The browser did not allow clipboard access.", true);
      }
    }

    async function pasteSelection() {
      try {
        const text = await navigator.clipboard.readText();
        const values = parsePaste(text, state.mode);
        if (!values.length) return;
        if (values.length > MAX_SELECTION) return notify("Paste at most 1 MiB at a time.", true);
        await ensureRange(state.active, Math.min(state.size - 1, state.active + values.length - 1));
        applyEdit(values.map((value, index) => ({ offset: state.active + index, after: value })));
        await goTo(Math.min(state.size - 1, state.active + values.length - 1));
      } catch (_error) {
        notify("The browser did not allow clipboard access.", true);
      }
    }

    async function ensureRange(start, end) {
      for (let offset = start; offset <= end;) {
        if (state.bytes.has(offset)) { offset += 1; continue; }
        const length = Math.min(4096, end - offset + 1);
        const data = await request(endpointUrl("", { offset, length, target: state.target }));
        if (state.version && data.version !== state.version) {
          throw new Error("The image changed outside the hex editor. Close it and reopen before continuing.");
        }
        const values = String(data.data).match(/../g) || [];
        values.forEach((value, index) => state.bytes.set(data.offset + index, Number.parseInt(value, 16)));
        offset += Math.max(1, values.length);
      }
    }

    async function compareWithFile() {
      const picker = document.createElement("input");
      picker.type = "file";
      picker.accept = ".bin,.rom,.img,.hda,.geo,.adf,.adz,.adf,*/*";
      picker.onchange = async () => {
        const file = picker.files?.[0];
        if (!file) return;
        $(".hex-comparison-name").textContent = `Comparing ${file.name}…`;
        try {
          const form = new FormData(); form.append("file", file);
          const comparison = await request(endpointUrl("/compare", { target: state.target }), { method: "POST", body: form });
          if (state.version && comparison.version && comparison.version !== state.version) throw new Error("The image changed outside the hex editor. Close it and reopen before continuing.");
          state.comparison = { ...comparison, name: file.name, size: comparison.candidateSize, sizeMismatch: comparison.sourceSize !== comparison.candidateSize };
          render();
          if (comparison.differences.length) await goTo(comparison.differences[0]);
        } catch (error) { notify(`Binary comparison failed: ${error.message}`, true); }
      };
      picker.click();
    }

    async function nextDifference() {
      const differences = state.comparison?.differences || [];
      if (!differences.length) return;
      await goTo(differences.find(offset => offset > state.active) ?? differences[0]);
    }

    async function fillSelection() {
      if (image.readOnly) return;
      const value = prompt("Fill the selected bytes with which hex value?", "00");
      if (value == null) return;
      const compact = value.trim().replace(/^(?:0x|&)/i, "");
      if (!/^[0-9a-f]{2}$/i.test(compact)) return notify("Enter one byte from 00 to FF.", true);
      const byte = Number.parseInt(compact, 16);
      await ensureRange(...selectedRange());
      applyEdit(selectedOffsets().map(offset => ({ offset, after: byte })));
    }

    function revertSelection(all = false) {
      const offsets = all ? [...state.changes.keys()] : selectedOffsets().filter(offset => state.changes.has(offset));
      applyEdit(offsets.map(offset => ({ offset, after: state.originals.get(offset) })));
    }

    async function moveCursor(delta, extend) {
      await goTo(clamp(state.active + delta, 0, Math.max(0, state.size - 1)), extend);
    }

    editor.addEventListener("keydown", async event => {
      const control = event.ctrlKey || event.metaKey;
      if (event.target.matches("input,select")) return;
      if (control && event.key.toLowerCase() === "s") { event.preventDefault(); await save(); return; }
      if (control && event.key.toLowerCase() === "z") { event.preventDefault(); event.shiftKey ? redo() : undo(); return; }
      if (control && event.key.toLowerCase() === "y") { event.preventDefault(); redo(); return; }
      if (control && event.key.toLowerCase() === "c") { event.preventDefault(); await copySelection(false); return; }
      if (control && event.key.toLowerCase() === "v") { event.preventDefault(); await pasteSelection(); return; }
      if (control && event.key.toLowerCase() === "g") { event.preventDefault(); $(".hex-goto").focus(); return; }
      if (control && event.key.toLowerCase() === "f") { event.preventDefault(); $(".hex-search-query").focus(); return; }
      if (control && event.key.toLowerCase() === "h" && !image.readOnly) { event.preventDefault(); $(".hex-replace-query").focus(); return; }
      const moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -16, ArrowDown: 16, PageUp: -state.pageSize, PageDown: state.pageSize };
      if (event.key in moves) { event.preventDefault(); await moveCursor(moves[event.key], event.shiftKey); return; }
      if (event.key === "Home") { event.preventDefault(); await goTo(event.ctrlKey ? 0 : state.active - state.active % 16, event.shiftKey); return; }
      if (event.key === "End") { event.preventDefault(); await goTo(event.ctrlKey ? state.size - 1 : Math.min(state.size - 1, state.active - state.active % 16 + 15), event.shiftKey); return; }
      if (event.key === "Escape") { event.preventDefault(); await closeEditor(); return; }
      if (image.readOnly) return;
      if (state.mode === "hex" && /^[0-9a-f]$/i.test(event.key)) {
        event.preventDefault();
        const nibble = Number.parseInt(event.key, 16);
        if (state.highNibble == null) {
          state.highNibble = { offset: state.active, value: nibble };
        } else {
          const high = state.highNibble.offset === state.active ? state.highNibble.value : nibble;
          applyEdit([{ offset: state.active, after: high << 4 | nibble }]);
          state.highNibble = null;
          await moveCursor(1, false);
        }
      } else if (state.mode === "ascii" && event.key.length === 1 && !control && !event.altKey) {
        event.preventDefault();
        applyEdit([{ offset: state.active, after: event.key.charCodeAt(0) & 0xFF }]);
        await moveCursor(1, false);
      } else if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        applyEdit(selectedOffsets().map(offset => ({ offset, after: 0 })));
      }
    });

    $(".hex-close").onclick = closeEditor;
    $(".hex-save").onclick = save;
    $(".hex-menu-close").onclick = closeEditor;
    $(".hex-menu-save").onclick = save;
    $(".hex-menu-undo").onclick = undo;
    $(".hex-menu-redo").onclick = redo;
    $(".hex-menu-copy-hex").onclick = () => copySelection(false);
    $(".hex-menu-copy-text").onclick = () => copySelection(true);
    $(".hex-menu-paste").onclick = pasteSelection;
    $(".hex-menu-fill").onclick = fillSelection;
    $(".hex-menu-revert-selection").onclick = () => revertSelection(false);
    $(".hex-menu-revert-all").onclick = () => revertSelection(true);
    $(".hex-menu-find").onclick = () => $(".hex-search-query").focus();
    $(".hex-menu-replace").onclick = () => $(".hex-replace-query").focus();
    $(".hex-menu-find-previous").onclick = () => find("backward");
    $(".hex-menu-find-next").onclick = () => find("forward");
    $(".hex-menu-goto").onclick = () => $(".hex-goto").focus();
    $(".hex-menu-compare").onclick = compareWithFile;
    $(".hex-menu-next-difference").onclick = nextDifference;
    $(".hex-template").onchange = async event => {
      state.template = event.target.value;
      if (["boot-block", "root-block", "rigid-disk", "kickstart-rom", "resident-tag", "hardfile-geo", "dms-track"].includes(state.template)) {
        await ensureRange(0, Math.min(state.size - 1, 511));
      }
      renderStructure();
    };
    $(".hex-menu-load-template").onclick = () => $(".hex-template-file").click();
    $(".hex-template-file").onchange = event => loadCustomTemplate(event.target.files?.[0]);
    const hexMenus = [...overlay.querySelectorAll(".editor-menu")];
    const closeHexMenus = except => hexMenus.forEach(menu => {
      if (menu !== except) menu.removeAttribute("open");
    });
    const transferHexMenu = menu => {
      if (!overlay.querySelector(".editor-menu[open]") || menu.open) return;
      closeHexMenus(menu);
      menu.open = true;
    };
    hexMenus.forEach(menu => {
      menu.addEventListener("toggle", () => { if (menu.open) closeHexMenus(menu); });
      menu.addEventListener("pointerenter", () => transferHexMenu(menu));
      menu.addEventListener("focusin", () => transferHexMenu(menu));
    });
    overlay.querySelectorAll(".editor-menu-panel button, .editor-menu-panel a").forEach(control => {
      control.addEventListener("click", () => closeHexMenus());
    });
    const dismissHexMenus = event => {
      if (!overlay.isConnected) return document.removeEventListener("pointerdown", dismissHexMenus, true);
      if (!event.target.closest?.(".hex-editor .editor-menu")) closeHexMenus();
    };
    document.addEventListener("pointerdown", dismissHexMenus, true);
    closed.finally(() => document.removeEventListener("pointerdown", dismissHexMenus, true));
    $(".hex-go").onclick = () => goTo(parseAddress($(".hex-goto").value));
    $(".hex-goto").onkeydown = event => { if (event.key === "Enter") { event.preventDefault(); $(".hex-go").click(); } };
    $(".hex-first").onclick = () => goTo(0);
    $(".hex-previous").onclick = () => goTo(Math.max(0, state.offset - state.pageSize));
    $(".hex-next").onclick = () => goTo(Math.min(state.size - 1, state.offset + state.pageSize));
    $(".hex-last").onclick = () => goTo(Math.max(0, state.size - state.pageSize));
    $(".hex-page-size").onchange = async event => { state.pageSize = Number(event.target.value); await loadPage(state.active); };
    $(".hex-target").onchange = event => changeTarget(event.target.value);
    $(".hex-find-next").onclick = () => find("forward");
    $(".hex-find-previous").onclick = () => find("backward");
    $(".hex-replace-next").onclick = replaceNext;
    $(".hex-search-query").onkeydown = event => { if (event.key === "Enter") { event.preventDefault(); find(event.shiftKey ? "backward" : "forward"); } };
    $(".hex-replace-query").onkeydown = event => { if (event.key === "Enter") { event.preventDefault(); replaceNext(); } };
    $(".hex-undo").onclick = undo;
    $(".hex-redo").onclick = redo;
    $(".hex-copy-hex").onclick = () => copySelection(false);
    $(".hex-copy-text").onclick = () => copySelection(true);
    $(".hex-paste").onclick = pasteSelection;
    $(".hex-fill").onclick = fillSelection;
    $(".hex-revert-selection").onclick = () => revertSelection(false);
    $(".hex-revert-all").onclick = () => revertSelection(true);
    overlay.querySelectorAll(".hex-mode button").forEach(button => {
      button.onclick = () => { state.mode = button.dataset.mode; state.highNibble = null; render(); editor.focus(); };
    });

    await loadPage(state.active, { resetVersion: true });
    editor.focus();
    return closed;
  }

  return { open };
})();
