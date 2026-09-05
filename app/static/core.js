window.AmigaUI = (() => {
  const OWNER_STORAGE_KEY = "amiga-file-forge-session-owner";
  const modal = document.querySelector("#modal");
  const modalContent = document.querySelector("#modalContent");
  const modalProgress = document.querySelector("#modalProgress");
  const modalProgressTitle = document.querySelector("#modalProgressTitle");
  const modalProgressMessage = document.querySelector("#modalProgressMessage");
  const modalProgressDetails = document.querySelector("#modalProgressDetails");
  const modalProgressBar = document.querySelector("#modalProgressBar");
  const modalProgressCount = document.querySelector("#modalProgressCount");
  const modalAbort = document.querySelector("#modalAbort");
  const modalErrorMessage = document.querySelector("#modalErrorMessage");
  const modalErrorDetails = document.querySelector("#modalErrorDetails");
  const modalErrorBack = document.querySelector("#modalErrorBack");
  const modalErrorClose = document.querySelector("#modalErrorClose");
  let modalAbortHandler = null;
  let modalReturnFocus = null;

  const esc = value => String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  }[character]));

  const humanSize = value => {
    const bytes = Number(value || 0);
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
  };

  const storedOwner = () => {
    try {
      const value = localStorage.getItem(OWNER_STORAGE_KEY) || "";
      return /^[A-Za-z0-9_-]{32,64}$/.test(value) ? value : "";
    } catch (_error) {
      return "";
    }
  };

  const rememberOwner = value => {
    if (!/^[A-Za-z0-9_-]{32,64}$/.test(value || "")) return;
    try {
      localStorage.setItem(OWNER_STORAGE_KEY, value);
    } catch (_error) {
      // The private cookie remains the fallback when storage is unavailable.
    }
  };

  async function api(url, options = {}) {
    const {
      networkRetries,
      onNetworkRetry,
      ...fetchOptions
    } = options;
    const method = String(fetchOptions.method || "GET").toUpperCase();
    const headers = new Headers(fetchOptions.headers || {});
    const owner = storedOwner();
    if (owner) headers.set("X-Amiga-Session-Owner", owner);
    fetchOptions.headers = headers;
    const retries = Math.max(
      0,
      Number(networkRetries ?? (["GET", "HEAD"].includes(method) ? 2 : 0))
    );
    for (let attempt = 0; ; attempt += 1) {
      let response;
      try {
        response = await fetch(url, fetchOptions);
      } catch (error) {
        if (error?.name === "AbortError" || attempt >= retries) {
          if (!retries || error?.name === "AbortError") throw error;
          const interrupted = new Error(
            `Connection to Amiga File Forge was interrupted after ${attempt + 1} attempts. The image remains available; try the operation again.`
          );
          interrupted.cause = error;
          throw interrupted;
        }
        const retryNumber = attempt + 1;
        onNetworkRetry?.(retryNumber, retries, error);
        await new Promise(resolve => setTimeout(resolve, 700 * retryNumber));
        continue;
      }
      rememberOwner(response.headers.get("X-Amiga-Session-Owner"));
      const contentType = response.headers.get("content-type") || "";
      const data = contentType.includes("application/json") ? await response.json() : null;
      if (!response.ok) {
        const error = new Error(data?.error || `Request failed (${response.status})`);
        error.data = data;
        error.status = response.status;
        throw error;
      }
      return data;
    }
  }

  function uploadApi(
    url,
    formData,
    {
      onProgress = null,
      onProcessing = null,
      timeout = 5 * 60 * 1000
    } = {}
  ) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", url);
      const owner = storedOwner();
      if (owner) request.setRequestHeader("X-Amiga-Session-Owner", owner);
      request.responseType = "json";
      request.timeout = timeout;
      request.upload.addEventListener("progress", event => {
        onProgress?.(
          event.loaded,
          event.lengthComputable ? event.total : null
        );
      });
      request.upload.addEventListener("load", () => onProcessing?.());
      request.addEventListener("load", () => {
        rememberOwner(request.getResponseHeader("X-Amiga-Session-Owner"));
        const data = request.response;
        if (request.status >= 200 && request.status < 300) {
          resolve(data);
          return;
        }
        const error = new Error(
          data?.error || `Request failed (${request.status})`
        );
        error.data = data;
        error.status = request.status;
        reject(error);
      });
      request.addEventListener("error", () => {
        reject(new Error(
          "The upload connection failed before Amiga File Forge received the image."
        ));
      });
      request.addEventListener("abort", () => {
        reject(new Error("The image upload was cancelled."));
      });
      request.addEventListener("timeout", () => {
        reject(new Error(
          "The image upload stopped responding for five minutes. "
          + "The pane has been released so you can retry."
        ));
      });
      request.send(formData);
    });
  }

  function toast(message, error = false) {
    const item = document.createElement("div");
    item.className = `toast${error ? " error" : ""}`;
    item.textContent = message;
    let region = document.querySelector("#toasts");
    if (modal.open) {
      const form = modal.querySelector(":scope > form");
      region = form.querySelector(":scope > .modal-toast-region");
      if (!region) {
        region = document.createElement("div");
        region.className = "toast-region modal-toast-region";
        region.setAttribute("role", "status");
        region.setAttribute("aria-live", error ? "assertive" : "polite");
        form.append(region);
      }
      if (error) region.setAttribute("aria-live", "assertive");
    }
    region.append(item);
    setTimeout(() => item.remove(), error ? 6500 : 3500);
  }

  function trapFocus(container, event) {
    if (event.key !== "Tab") return;
    const controls = [...container.querySelectorAll('a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])')]
      .filter(control => !control.hidden && !control.closest("[inert]") && control.getClientRects().length);
    if (!controls.length) return event.preventDefault();
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function setModalProgress(message, current, total) {
    const update = typeof message === "object" && message !== null
      ? message
      : { message };
    modalProgressTitle.textContent = update.title || "Operation in progress";
    modalProgressMessage.textContent = update.message || "Working…";
    modalProgressDetails.replaceChildren();
    for (const detail of update.details || []) {
      const row = document.createElement("span");
      if (typeof detail === "object" && detail !== null) {
        const label = document.createElement("b");
        label.textContent = detail.label ? `${detail.label}: ` : "";
        row.append(label, document.createTextNode(detail.value || ""));
      } else {
        row.textContent = detail;
      }
      modalProgressDetails.append(row);
    }
    if (total !== undefined) {
      const determinate = Number(total) > 0;
      modalProgressBar.classList.toggle("determinate", determinate);
      if (determinate) {
        const progress = Math.min(100, Math.round(100 * Number(current || 0) / Number(total)));
        modalProgressBar.style.setProperty("--operation-progress", `${progress}%`);
        modalProgressCount.textContent = `${Number(current || 0)} of ${Number(total)}`;
      } else {
        modalProgressBar.style.removeProperty("--operation-progress");
        modalProgressCount.textContent = "";
      }
    }
  }

  function setModalAbort(handler) {
    modalAbortHandler = typeof handler === "function" ? handler : null;
    modalAbort.hidden = !modalAbortHandler;
    modalAbort.disabled = !modalAbortHandler;
    modalAbort.textContent = "Abort operation";
  }

  modalAbort.addEventListener("click", async () => {
    if (!modalAbortHandler || modalAbort.disabled) return;
    modalAbort.disabled = true;
    modalAbort.textContent = "Stopping…";
    try {
      await modalAbortHandler();
    } catch (error) {
      modalAbort.disabled = false;
      modalAbort.textContent = "Try abort again";
      toast(`Could not request an abort: ${error.message}`, true);
    }
  });

  function showModalError(error) {
    modalErrorBack.disabled = false;
    modalErrorClose.disabled = false;
    modalErrorMessage.textContent = error?.message || "The operation failed.";
    modalErrorDetails.replaceChildren();
    const completed = error?.data?.completed;
    const skipped = error?.data?.skipped;
    const details = [
      ...(Array.isArray(completed)
        ? [{
            label: "Completed safely",
            value: `${completed.length} ${completed.length === 1 ? "item" : "items"} will be skipped when you retry`
          }]
        : []),
      ...(Array.isArray(skipped) && skipped.length
        ? [{
            label: "Items skipped",
            value: `${skipped.length} ${skipped.length === 1 ? "item was" : "items were"} not copied to FFS`
          }]
        : []),
      ...(error?.data?.path ? [{ label: "Last path", value: error.data.path }] : []),
      {
        label: "Next step",
        value: "Use Back / retry to review the same operation, or Close to inspect the image"
      }
    ];
    for (const detail of details) {
      const row = document.createElement("span");
      const label = document.createElement("b");
      label.textContent = `${detail.label}: `;
      row.append(label, document.createTextNode(detail.value));
      modalErrorDetails.append(row);
    }
    modal.classList.remove("busy");
    modal.classList.add("failed");
  }

  modalErrorBack.addEventListener("click", () => {
    modal.classList.remove("failed");
    modalContent.querySelector("input,select,button")?.focus();
  });
  modalErrorClose.addEventListener("click", () => modal.close());
  modal.addEventListener("close", () => {
    modal.classList.remove("busy", "failed");
    modalErrorBack.disabled = false;
    modalErrorClose.disabled = false;
    setModalAbort(null);
    modalReturnFocus?.focus();
    modalReturnFocus = null;
    modal.querySelector(".modal-toast-region")?.remove();
  });
  modal.addEventListener("keydown", event => trapFocus(modal, event));

  function showModal(html, onSubmit, { replace = false } = {}) {
    const replacing = modal.open;
    if (replacing && !replace) return Promise.resolve(false);
    const closed = replacing
      ? Promise.resolve(true)
      : new Promise(resolve => {
          modal.addEventListener("close", () => resolve(true), { once: true });
        });
    setModalAbort(null);
    if (!replacing) modalReturnFocus = document.activeElement;
    modalContent.innerHTML = html;
    const form = modal.querySelector("form");
    form.querySelectorAll('button[value="cancel"]').forEach(button => {
      button.formNoValidate = true;
    });
    form.onsubmit = event => {
      if (event.submitter?.value === "cancel") return;
      event.preventDefault();
      modal.classList.remove("failed");
      const formData = new FormData(form);
      const controls = [...form.elements];
      const disabledBeforeSubmit = controls.map(control => control.disabled);
      controls.forEach(control => {
        control.disabled = true;
      });
      form.setAttribute("aria-busy", "true");
      modal.classList.add("busy");
      setModalProgress("Starting operation…", null, null);
      Promise.resolve(onSubmit?.(formData)).then(result => {
        if (result !== false) {
          modal.close();
          return;
        }
        controls.forEach((control, index) => {
          control.disabled = disabledBeforeSubmit[index];
        });
      }).catch(error => {
        controls.forEach((control, index) => {
          control.disabled = disabledBeforeSubmit[index];
        });
        showModalError(error);
      }).finally(() => {
        controls.forEach((control, index) => {
          control.disabled = disabledBeforeSubmit[index];
        });
        setModalAbort(null);
        form.removeAttribute("aria-busy");
        modal.classList.remove("busy");
      });
    };
    if (!replacing) modal.showModal();
    setTimeout(() => {
      const preferred = modalContent.querySelector('[autofocus], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), button:not(:disabled), a[href], summary, [tabindex]:not([tabindex="-1"])')
        || modal.querySelector(".modal-close");
      preferred?.focus();
    }, 40);
    return closed;
  }

  return { api, uploadApi, esc, humanSize, modal, modalContent, setModalAbort, setModalProgress, showModal, toast, trapFocus };
})();
