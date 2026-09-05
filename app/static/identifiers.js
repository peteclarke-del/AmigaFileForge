window.AmigaIdentifiers = (() => {
  "use strict";

  function newUuid(cryptoSource = globalThis.crypto) {
    if (typeof cryptoSource?.randomUUID === "function") {
      return cryptoSource.randomUUID();
    }
    if (typeof cryptoSource?.getRandomValues !== "function") {
      throw new Error(
        "This browser cannot create secure operation identifiers. Update the browser and try again."
      );
    }
    const bytes = new Uint8Array(16);
    cryptoSource.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hexadecimal = [...bytes].map(value => value.toString(16).padStart(2, "0"));
    return [
      hexadecimal.slice(0, 4).join(""),
      hexadecimal.slice(4, 6).join(""),
      hexadecimal.slice(6, 8).join(""),
      hexadecimal.slice(8, 10).join(""),
      hexadecimal.slice(10, 16).join(""),
    ].join("-");
  }

  return Object.freeze({ newUuid });
})();
