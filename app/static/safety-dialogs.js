window.AmigaSafetyDialogs = (() => {
  function create({ esc, normalisePage, trapFocus }) {
    function showPageWarning({ heading, body, list = "", confirmLabel }) {
      return new Promise(resolve => {
        const overlay = document.createElement("div");
        overlay.className = "page-warning-overlay";
        overlay.setAttribute("role", "alertdialog");
        overlay.setAttribute("aria-modal", "true");
        overlay.setAttribute("aria-labelledby", "page-warning-title");
        overlay.innerHTML = `<div class="page-warning-card"><span class="page-warning-icon" aria-hidden="true">!</span><h2 id="page-warning-title">${heading}</h2>${body}${list}<div class="help-warning"><strong>Risk:</strong> the wrong STACK can overwrite filing-system workspace or loader data, corrupt BASIC, hang, or crash on real hardware.</div><div class="modal-actions"><button type="button" class="button ghost" data-page-cancel>Cancel</button><button type="button" class="button primary" data-page-confirm>${confirmLabel}</button></div></div>`;
        const previouslyFocused = document.activeElement;
        const finish = result => { overlay.remove(); previouslyFocused?.focus(); resolve(result); };
        overlay.querySelector("[data-page-cancel]").onclick = () => finish(false);
        overlay.querySelector("[data-page-confirm]").onclick = () => finish(true);
        overlay.onkeydown = event => event.key === "Escape" ? finish(false) : trapFocus(overlay, event);
        document.body.append(overlay);
        overlay.querySelector("[data-page-cancel]").focus();
      });
    }

    function confirmPageOverride(defaultPage, chosenPage, subjects = []) {
      if (Array.isArray(defaultPage)) {
        const overrides = defaultPage.filter(item => item?.defaultPage && item?.chosenPage);
        if (!overrides.length) return Promise.resolve(true);
        const list = `<div class="page-warning-list">${overrides.slice(0, 8).map(item => `<span><b>${esc(item.title)}</b><small>&amp;${esc(normalisePage(item.defaultPage))} recommended → &amp;${esc(normalisePage(item.chosenPage))} entered</small></span>`).join("")}${overrides.length > 8 ? `<em>and ${overrides.length - 8} more…</em>` : ""}</div>`;
        return showPageWarning({ heading: `Use ${overrides.length} changed STACK ${overrides.length === 1 ? "value" : "values"}?`, body: "<p>These values differ from the launchers in the actual disk images.</p>", list, confirmLabel: "Yes, use changed values" });
      }
      if (!defaultPage || normalisePage(defaultPage) === normalisePage(chosenPage)) return Promise.resolve(true);
      const labels = Array.isArray(subjects) ? subjects.filter(Boolean) : [subjects].filter(Boolean);
      return showPageWarning({
        heading: "Use a different stack size?",
        body: `<p>The actual launcher in the disk image indicates <strong>&amp;${esc(normalisePage(defaultPage))}</strong>, but you entered <strong>&amp;${esc(normalisePage(chosenPage))}</strong>.</p>${labels.length ? `<p class="page-warning-subject">${esc(labels.slice(0, 4).join(", "))}${labels.length > 4 ? ` and ${labels.length - 4} more` : ""}</p>` : ""}`,
        confirmLabel: `Yes, use &amp;${esc(normalisePage(chosenPage))}`,
      });
    }
    return { confirmPageOverride };
  }
  return { create };
})();
