(() => {
  "use strict";

  // The About box's Check for Application Updates control.
  //
  // The server asks GitHub, downloads, checks and installs, and keeps the
  // state, so closing the About box does not stop a download and reopening it
  // shows how far it has got. Nothing is asked of GitHub until the button is
  // pressed. Only the desktop host has the install, cancel and restart
  // routes; everywhere else a newer release is offered as its release page.
  const CHECK_LABEL = "Check for Application Updates";
  const RESTART_MESSAGE = "restart-application";
  const POLL_INTERVAL = 500;

  /** What the control shows for one server state. Pure, so it is tested without a page. */
  function view(state, humanSize = String) {
    const phase = state?.phase || "idle";
    const release = state?.release || null;
    const shown = {
      status: state?.message || "",
      button: { label: CHECK_LABEL, action: "check", primary: false, disabled: false },
      progress: null,
      cancel: false,
      page: release?.pageUrl && phase !== "installed" ? release.pageUrl : "",
    };
    if (phase === "checking") {
      shown.button = { label: "Checking", action: "", primary: false, disabled: true };
      shown.progress = { fraction: null, text: "" };
    } else if (phase === "downloading") {
      const done = Number(state.done) || 0;
      const total = Number(state.total) || 0;
      shown.button = null;
      shown.cancel = true;
      shown.progress = {
        fraction: total ? Math.min(1, done / total) : null,
        text: total ? `${humanSize(done)} of ${humanSize(total)}` : humanSize(done),
      };
    } else if (phase === "installing") {
      shown.button = null;
      shown.progress = { fraction: null, text: "" };
    } else if (phase === "available" && release) {
      if (release.installable) {
        shown.button = { label: `Update to ${release.version}`, action: "install", primary: true, disabled: false };
      } else {
        // This copy cannot install it, so the release page is the answer.
        shown.button = {
          label: "Open Release Page", action: "page", primary: true, disabled: false,
          href: release.pageUrl || state.releasesPage,
        };
        shown.page = "";
      }
    } else if (phase === "installed") {
      shown.button = { label: `Restart ${state.application}`, action: "restart", primary: true, disabled: false };
    }
    return shown;
  }

  function create({ api, esc, humanSize, confirmChoice, nativeHost = () => null }) {
    let state = null;
    let container = null;
    let timer = null;
    // A note that belongs to this page rather than to the server's state,
    // such as a restart the server refused.
    let note = "";

    function markup(shown) {
      const button = shown.button;
      const buttonMarkup = !button ? ""
        : button.href
          ? `<a class="button small${button.primary ? " primary" : ""}" href="${esc(button.href)}" target="_blank" rel="noopener noreferrer">${esc(button.label)}</a>`
          : `<button type="button" class="button small${button.primary ? " primary" : ""}" data-update-action="${esc(button.action)}"${button.disabled ? " disabled" : ""}>${esc(button.label)}</button>`;
      const progress = shown.progress;
      const status = note || shown.status;
      return `<div class="about-update-controls">
          ${buttonMarkup}
          ${progress ? `<span class="progress${progress.fraction === null ? "" : " determinate"}" role="progressbar" aria-label="Application update progress"><i></i></span><small class="about-update-size" aria-hidden="true"></small>` : ""}
          ${shown.cancel ? '<button type="button" class="button small ghost" data-update-action="cancel">Cancel</button>' : ""}
        </div>
        ${status ? `<p class="about-update-status">${esc(status)}</p>` : ""}
        ${shown.page ? `<a class="about-update-page" href="${esc(shown.page)}" target="_blank" rel="noopener noreferrer">Release page</a>` : ""}`;
    }

    // The section is a polite live region, so it is rebuilt only when what it
    // says changes. Download progress moves the bar in place, where it is
    // not read out every half second.
    let rendered = "";
    function render() {
      if (!container?.isConnected) return;
      const shown = view(state || { phase: "idle" }, humanSize);
      const progress = shown.progress;
      const key = JSON.stringify([shown.button, shown.status, note, shown.page, shown.cancel, progress && progress.fraction === null]);
      if (key !== rendered) {
        const focused = container.contains(document.activeElement);
        container.innerHTML = markup(shown);
        rendered = key;
        container.querySelectorAll("[data-update-action]").forEach(control => {
          control.onclick = () => act(control.dataset.updateAction);
        });
        // A keyboard user keeps their place when the button they pressed is replaced.
        if (focused) (container.querySelector("button:not([disabled]), a.button") || container).focus();
      }
      const bar = container.querySelector(".progress");
      if (bar && progress) {
        if (progress.fraction !== null) {
          const percent = Math.round(progress.fraction * 100);
          bar.style.setProperty("--operation-progress", `${percent}%`);
          bar.setAttribute("aria-valuenow", String(percent));
          bar.setAttribute("aria-valuemin", "0");
          bar.setAttribute("aria-valuemax", "100");
        }
        if (progress.text) bar.setAttribute("aria-valuetext", progress.text);
        container.querySelector(".about-update-size").textContent = progress.text;
      }
    }

    function follow() {
      clearTimeout(timer);
      timer = null;
      if (container?.isConnected && state?.busy) timer = setTimeout(refresh, POLL_INTERVAL);
    }

    function accept(next) {
      state = next;
      render();
      follow();
    }

    async function refresh() {
      if (!container?.isConnected) return;
      try {
        accept(await api("/api/app-update"));
      } catch (error) {
        note = `Could not read the update status: ${error.message}`;
        render();
      }
    }

    async function send(url) {
      try {
        accept(await api(url, { method: "POST" }));
      } catch (error) {
        note = error.message;
        render();
      }
    }

    async function confirmInstall() {
      const release = state.release;
      const size = release.packageSize ? ` (${humanSize(release.packageSize)})` : "";
      const message = `Version ${release.version} is available; you have ${state.currentVersion}. `
        + `The package for ${state.system || "this system"}${size} is downloaded from GitHub, checked `
        + "against the release's checksums and installed, which asks for your password. Your working "
        + "images, settings and collection are kept. The release page describes what has changed.";
      return confirmChoice(`Update ${state.application}?`, message, { confirmLabel: "Download and Install" });
    }

    async function restart() {
      const host = nativeHost();
      if (!host) {
        note = `Close ${state.application} and open it again to use the new version.`;
        render();
        return;
      }
      try {
        await api("/api/desktop/app-update/restart", { method: "POST" });
      } catch (error) {
        note = error.message;
        render();
        return;
      }
      host.postMessage(RESTART_MESSAGE);
    }

    async function act(action) {
      note = "";
      if (action === "check") await send("/api/app-update/check");
      else if (action === "cancel") await send("/api/desktop/app-update/cancel");
      else if (action === "install") {
        if (await confirmInstall()) await send("/api/desktop/app-update/install");
      } else if (action === "restart") await restart();
    }

    /** Show the control in ``element``, the About box's update section. */
    function attach(element) {
      container = element || null;
      note = "";
      rendered = "";
      if (!container) return;
      render();
      refresh();
    }

    return Object.freeze({ attach });
  }

  window.AmigaAppUpdate = Object.freeze({ CHECK_LABEL, RESTART_MESSAGE, create, view });
})();
