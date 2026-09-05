(() => {
  "use strict";

  function create({ showModal, esc, context }) {
    return function showAbout() {
      const details = context();
      const host = details.host === "desktop" ? "Linux desktop application" : "Web application";
      showModal(`<div class="about-dialog">
        <header class="about-heading">
          <img src="/favicon.svg" alt="">
          <div><small>AMIGA FILE IMAGE WORKSHOP</small><h2>Amiga File Forge</h2><p>Version ${esc(details.version)}</p></div>
        </header>
        <p>Create, inspect, edit, convert, validate and deploy Amiga media images from one shared workbench.</p>
        <dl class="about-facts">
          <dt>Edition</dt><dd>${esc(host)}</dd>
          <dt>Filesystem engine</dt><dd>${esc(details.engine)}</dd>
          <dt>Formats</dt><dd>OFS ADF/ADZ, HFE, SCP, HDF, FFS and AmigaDOS, Hardfile HDA/GEO, HDF/RAW, DMS, ROM and Kickstart ROM</dd>
          <dt>Platforms</dt><dd>Amiga 500 and Master, Amiga 600, Amiga 4000 and AmigaOS</dd>
          <dt>Licence</dt><dd>MIT License · Copyright © 2026 Pete Clarke</dd>
        </dl>
        <nav class="about-links" aria-label="Project links">
          <a class="button small" href="https://github.com/peteclarke-del/AmigaFileForge" target="_blank" rel="noopener noreferrer">Source and support</a>
          <a class="button small" href="https://github.com/peteclarke-del/AmigaFileForge/releases" target="_blank" rel="noopener noreferrer">Release downloads</a>
          <a class="button small" href="https://github.com/peteclarke-del/AmigaFileForge/blob/main/THIRD_PARTY_NOTICES.md" target="_blank" rel="noopener noreferrer">Third-party notices</a>
        </nav>
        <div class="modal-actions"><button class="button primary" value="cancel">Close</button></div>
      </div>`);
    };
  }

  window.AmigaAbout = Object.freeze({ create });
})();
