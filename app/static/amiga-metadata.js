(() => {
  "use strict";

  // AmigaDOS keeps eight protection bits per directory entry, written
  // "hsparwed" from the top. The low four are inverted on disk: a set bit
  // denies the operation, so a fully permitted file reads "----rwed" and
  // stores zero in those positions. Everything here works in that stored
  // form, so what the workbench shows is what the volume holds.
  const PROTECTION_LETTERS = ["h", "s", "p", "a", "r", "w", "e", "d"];
  const INVERTED = new Set(["r", "w", "e", "d"]);

  function parseProtection(value) {
    if (typeof value === "number" && Number.isFinite(value)) return value >>> 0;
    const match = String(value ?? "").trim().match(/^(?:&|0x)?([0-9a-f]{1,8})$/i);
    return match ? Number.parseInt(match[1], 16) >>> 0 : null;
  }

  function protectionFlags(value) {
    const parsed = parseProtection(value) ?? 0;
    const flags = {};
    PROTECTION_LETTERS.forEach((letter, index) => {
      const bit = (parsed >>> (7 - index)) & 1;
      flags[letter] = INVERTED.has(letter) ? bit === 0 : bit === 1;
    });
    return flags;
  }

  function formatProtection(value) {
    const flags = typeof value === "object" && value !== null && !Array.isArray(value)
      ? value
      : protectionFlags(value);
    return PROTECTION_LETTERS.map(letter => (flags[letter] ? letter : "-")).join("");
  }

  function protectionValue(flags) {
    let value = 0;
    PROTECTION_LETTERS.forEach((letter, index) => {
      const set = INVERTED.has(letter) ? !flags[letter] : Boolean(flags[letter]);
      if (set) value |= 1 << (7 - index);
    });
    return value >>> 0;
  }

  function protectionHex(flags) {
    return `0x${protectionValue(flags).toString(16).toUpperCase().padStart(8, "0")}`;
  }

  window.AmigaMetadata = Object.freeze({
    PROTECTION_LETTERS,
    formatProtection,
    parseProtection,
    protectionFlags,
    protectionHex,
    protectionValue,
  });
})();
