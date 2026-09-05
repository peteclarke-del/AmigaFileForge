(() => {
  "use strict";

  function targetNameRule(pane, original) {
    const policyKey = pane.image?.kind === "hdf" && pane.partition == null ? "disk" : "file";
    const contract = pane.image?.filenamePolicies?.[policyKey];
    if (pane.image?.kind === "rom") return { valid: true, suggested: original, limit: Number(contract?.limit || 180), label: contract?.label || "ROM bank", adjusted: false, truncated: false };
    if (pane.image?.kind === "kickfs") {
      const raw = String(original || "");
      const limit = Number(contract?.limit || 60);
      const suggested = raw.normalize("NFKC").replace(/[^\x20-\xff]/g, "_").slice(0, limit) || "FILE";
      return {
        valid: raw.length > 0 && raw.length <= limit && !/[\x00-\x1f]/.test(raw),
        suggested, limit, label: contract?.label || "Kickstart ROM", adjusted: raw !== suggested, truncated: raw.length > limit,
      };
    }
    const isOfs = pane.image?.kind === "ofs" || (pane.image?.kind === "hdf" && pane.partition !== null);
    // An AmigaDOS directory entry holds 30 characters whatever the DOS type
    // is; only the long-filename variants raise that, and the server tells us
    // when they apply.
    const limit = Number(contract?.limit || pane.image?.filesystemCapabilities?.nameLimit || 30);
    const label = contract?.label || (isOfs ? "OFS" : "FFS");
    const raw = String(original || "").split(/[/:]/).pop();
    // AmigaDOS forbids only the separator, the volume marker and a backslash.
    const forbidden = String(contract?.forbidden || ":/\\").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const invalidPattern = `[${forbidden}\\x00-\\x1f]`;
    const invalid = new RegExp(invalidPattern, "g");
    const latin1 = contract?.latin1 ?? true;
    let suggested = Array.from(raw.normalize("NFKC"), character => (
      latin1 && character.codePointAt(0) > 0xFF ? "_" : character
    )).join("").replace(invalid, "_").trim().slice(0, limit);
    if (!suggested) suggested = "FILE";
    const valid = raw.length > 0
      && raw === raw.trim()
      && raw.length <= limit
      && (!latin1 || Array.from(raw).every(character => character.codePointAt(0) <= 0xFF))
      && !new RegExp(invalidPattern).test(raw);
    return { valid, suggested, limit, label, adjusted: !valid || raw !== suggested, truncated: raw.length > limit };
  }

  function ignoredFolderFile(name) {
    const parts = String(name).replace(/\\/g, "/").split("/");
    const leaf = parts.at(-1).toLowerCase();
    return leaf === ".ds_store" || leaf === "thumbs.db" || leaf === "desktop.ini"
      || parts.some(part => part === "__MACOSX");
  }

  // AmigaDOS protection, as the eight letters List prints. A stored value may
  // also arrive as the raw long in hexadecimal, which an .inf sidecar written
  // by another tool sometimes uses.
  const PROTECTION_LETTERS = "hsparwed";

  function normaliseProtection(value) {
    const text = String(value || "").trim();
    const lowered = text.toLowerCase();
    // Each position is either its own letter or a dash, in List's fixed order.
    if (
      lowered.length === PROTECTION_LETTERS.length
      && [...lowered].every((character, index) => (
        character === "-" || character === PROTECTION_LETTERS[index]
      ))
    ) return lowered;
    const hex = text.match(/^(?:0x|&)?([0-9a-f]{1,8})$/i);
    return hex ? `0x${hex[1].toUpperCase()}` : "";
  }

  //: An 880 KiB AmigaDOS volume holds 1758 usable blocks, and OFS stores 488
  //: bytes of a file in each 512-byte block. A directory is a hash table with
  //: overflow chains, so free blocks are the only real limit on entry count.
  const BLOCKS_PER_VOLUME = 1758;
  const OFS_BYTES_PER_BLOCK = 488;

  // An AmigaDOS floppy is one 880 KiB volume, so a disk is filled and then a
  // new one is started. The DOS type chosen in the dialog changes how a block
  // is used, not how many blocks there are: FFS fills all 512 bytes of a data
  // block where OFS keeps 24 for its header, so the OFS figure is used and an
  // FFS disk simply has room to spare.
  function allocateFilesToOfsDisks(items, _diskFormat) {
    const disks = [];
    let disk = null;
    let diskBlocks = 0;
    for (const item of items) {
      // Each file also costs one header block, and a long file costs an
      // extension block for every 72 data blocks it uses.
      const dataBlocks = Math.max(1, Math.ceil(Number(item.length || 0) / OFS_BYTES_PER_BLOCK));
      const sectors = dataBlocks + 1 + Math.floor(dataBlocks / 72);
      if (sectors > BLOCKS_PER_VOLUME) throw new Error(`${item.name} is too large for one volume.`);
      if (!disk || diskBlocks + sectors > BLOCKS_PER_VOLUME) {
        disk = { files: [] };
        disks.push(disk);
        diskBlocks = 0;
      }
      disk.files.push({ ...item, targetSide: 0 });
      diskBlocks += sectors;
    }
    return disks;
  }

  //: AmigaDOS compares names without regard to case, so two files whose names
  //: differ only in case cannot share a drawer.
  function uniqueOfsNames(items) {
    const used = new Set();
    return items.map(item => {
      const rule = targetNameRule({ image: { kind: "ofs" } }, item.name);
      let proposed = rule.suggested;
      let suffix = 1;
      while (used.has(proposed.toLowerCase())) {
        const tail = String(suffix++);
        proposed = `${rule.suggested.slice(0, Math.max(1, rule.limit - tail.length))}${tail}`;
      }
      used.add(proposed.toLowerCase());
      // The source drawer becomes the destination drawer, so a copied tree
      // keeps the shape it had.
      const parts = String(item.path || "").split("/").slice(0, -1);
      return { ...item, targetName: proposed, prefix: parts.join("/") };
    });
  }

  window.AmigaImportPlanning = Object.freeze({
    allocateFilesToOfsDisks,
    ignoredFolderFile,
    normaliseProtection,
    targetNameRule,
    uniqueOfsNames,
  });
})();
