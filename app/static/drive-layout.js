// The partition editor: a drive described as a list of partitions, checked by
// the server after every change so that what the editor shows is exactly what
// would be written. Nothing here decides a size or a limit for itself. The
// page holds what the person typed, and the plan that comes back says where
// each partition would start and end, what is wrong and what is worth knowing.
window.AmigaDriveLayout = (() => {
  function create({ api, upload, esc, humanSize, toast = () => {} }) {
    let cachedOptions = null;

    async function layoutOptions(refresh = false) {
      if (!cachedOptions || refresh) cachedOptions = await api("/api/drive-layout/options");
      return cachedOptions;
    }

    function filesystemChoices(options, selected, { families = null, placeholder = "", without = [] } = {}) {
      const rows = options.filesystems.filter(entry =>
        (!families || families.includes(entry.family)) && !without.includes(entry.id));
      const groups = [
        ["For large partitions", rows.filter(entry => entry.family !== "ffs")],
        ["FastFileSystem and OFS, up to " + humanSize(options.limits.largestFfsPartition), rows.filter(entry => entry.family === "ffs")],
      ].filter(([, entries]) => entries.length);
      const first = placeholder ? `<option value="" disabled ${selected ? "" : "selected"}>${esc(placeholder)}</option>` : "";
      return first + groups.map(([label, entries]) => `<optgroup label="${esc(label)}">${entries.map(entry =>
        `<option value="${esc(entry.id)}" ${entry.id === selected ? "selected" : ""}>${esc(entry.label)} · ${esc(entry.dosType)}</option>`).join("")}</optgroup>`).join("");
    }

    function handlerSummary(options) {
      return options.handlers.map(handler => {
        if (handler.source === "missing") return `<li><b>${esc(handler.label)}</b><span>Not supplied. Needed before a partition can use it.</span></li>`;
        const origin = handler.source === "bundled" ? "comes with Amiga File Forge" : "supplied by you";
        return `<li><b>${esc(handler.label)}</b><span>${esc(handler.description || handler.name)} · ${origin}</span></li>`;
      }).join("");
    }

    function editorMarkup(options, { sizeLabel = "" } = {}) {
      return `<section class="drive-layout-editor" aria-label="Partitions">
        <div class="field-grid two">
          <div class="field"><label for="drive-layout-preset">Starting layout</label><select id="drive-layout-preset" name="layoutPreset">
            <option value="" disabled selected>Choose a starting layout…</option>
            ${options.presets.map(preset => `<option value="${esc(preset.id)}">${esc(preset.label)}</option>`).join("")}
            <option value="empty">Start with no partitions</option>
          </select><small data-preset-help>Fills in the table below. Every row can be changed afterwards.</small></div>
          <div class="field"><label for="drive-layout-large">Filing system for the large partitions</label><select id="drive-layout-large" name="layoutLargeFilesystem">
            ${filesystemChoices(options, "", { families: ["pfs3", "sfs"], placeholder: "Choose a filing system…" })}
          </select><small data-large-help>Used by the starting layout for every partition too large for FFS.</small></div>
        </div>
        <div class="drive-layout-table-wrap"><table class="drive-layout-table">
          <thead><tr><th scope="col">Device</th><th scope="col">Volume name</th><th scope="col">Filing system</th><th scope="col">Size</th><th scope="col">Boot</th><th scope="col">Priority</th><th scope="col">Remove</th></tr></thead>
          <tbody data-layout-rows></tbody>
        </table></div>
        <div class="drive-layout-tools"><button type="button" class="button ghost" data-layout-add>Add partition</button><span data-layout-size>${esc(sizeLabel)}</span></div>
        <div class="drive-map" data-layout-map role="img" aria-label="How the drive is divided"></div>
        <div data-layout-messages aria-live="polite"></div>
      </section>`;
    }

    function rowMarkup(options, row, position) {
      return `<tr data-layout-row="${position}">
        <td><input data-field="name" value="${esc(row.name)}" maxlength="30" size="6" required spellcheck="false" autocomplete="off" aria-label="Device name of partition ${position + 1}"></td>
        <td><input data-field="label" value="${esc(row.label)}" maxlength="30" size="12" required spellcheck="false" autocomplete="off" aria-label="Volume name of partition ${position + 1}"></td>
        <td><select data-field="filesystem" aria-label="Filing system of partition ${position + 1}">${filesystemChoices(options, row.filesystem, { placeholder: "Choose…" })}</select></td>
        <td><input data-field="size" value="${esc(row.size)}" size="8" placeholder="The rest" spellcheck="false" autocomplete="off" aria-label="Size of partition ${position + 1}, or empty for the rest of the drive"></td>
        <td><input type="checkbox" data-field="bootable" ${row.bootable ? "checked" : ""} aria-label="Boot from partition ${position + 1}"></td>
        <td><input type="number" data-field="bootPriority" value="${Number(row.bootPriority) || 0}" min="-128" max="127" aria-label="Boot priority of partition ${position + 1}"></td>
        <td><button type="button" class="button ghost" data-layout-remove aria-label="Remove partition ${position + 1}" title="Remove this partition">×</button></td>
      </tr>`;
    }

    const sizeText = bytes => {
      if (!bytes) return "";
      const gib = 1024 ** 3;
      const mib = 1024 ** 2;
      if (bytes % gib === 0) return `${bytes / gib}GB`;
      if (bytes >= gib) return `${Math.round(bytes / mib)}MB`;
      return `${Math.max(1, Math.round(bytes / mib))}MB`;
    };

    function nextDeviceName(rows) {
      const taken = new Set(rows.map(row => row.name.toLowerCase()));
      let number = 0;
      while (taken.has(`dh${number}`)) number += 1;
      return `DH${number}`;
    }

    // Attach the editor's behaviour to markup already in the page. `driveSize`
    // returns what the person has entered for the drive's size, as text or as
    // a number of bytes, and `onChange` hears about every new state.
    function attachEditor(root, options, { driveSize, onChange = () => {}, initialRows = null } = {}) {
      const body = root.querySelector("[data-layout-rows]");
      const map = root.querySelector("[data-layout-map]");
      const messages = root.querySelector("[data-layout-messages]");
      const preset = root.querySelector('[name="layoutPreset"]');
      const large = root.querySelector('[name="layoutLargeFilesystem"]');
      const largeHelp = root.querySelector("[data-large-help]");
      const state = { rows: initialRows ? initialRows.map(row => ({ ...row })) : [], plan: null, error: "", valid: false, pending: false };
      let timer = null;
      let sequence = 0;

      const readRows = () => [...body.querySelectorAll("tr")].map(row => ({
        name: row.querySelector('[data-field="name"]').value.trim(),
        label: row.querySelector('[data-field="label"]').value.trim(),
        filesystem: row.querySelector('[data-field="filesystem"]').value,
        size: row.querySelector('[data-field="size"]').value.trim(),
        bootable: row.querySelector('[data-field="bootable"]').checked,
        bootPriority: Number(row.querySelector('[data-field="bootPriority"]').value) || 0,
      }));

      const requestRows = () => state.rows.map(row => ({
        name: row.name,
        label: row.label,
        filesystem: row.filesystem,
        sizeBytes: row.size || null,
        bootable: row.bootable,
        bootPriority: row.bootPriority,
      }));

      const drawRows = () => {
        body.innerHTML = state.rows.length
          ? state.rows.map((row, position) => rowMarkup(options, row, position)).join("")
          : '<tr class="drive-layout-empty"><td colspan="7">No partitions yet. Choose a starting layout above, or add a partition.</td></tr>';
      };

      const drawPlan = () => {
        const plan = state.plan;
        if (!plan) {
          map.innerHTML = "";
        } else {
          const total = plan.driveBytes || 1;
          const segment = (className, label, bytes, detail) => bytes > 0
            ? `<span class="drive-map-part ${className}" style="flex-grow:${Math.max(bytes / total, 0.015)}" title="${esc(`${label} · ${detail}`)}"><b>${esc(label)}</b><small>${esc(humanSize(bytes))}</small></span>`
            : "";
          map.innerHTML = segment("reserved", "Table", plan.reservedBytes, "the partition table and the handlers")
            + plan.partitions.map(part => segment(
              `family-${esc(options.filesystems.find(entry => entry.id === part.filesystem)?.family || "ffs")}`,
              part.name,
              part.sizeBytes,
              `${part.label} · ${part.filesystemLabel} · ${humanSize(part.sizeBytes)}${part.bootable ? " · boots" : ""}`,
            )).join("")
            + segment("unused", "Unused", plan.unusedBytes, "no partition uses this");
          body.querySelectorAll("tr[data-layout-row]").forEach((row, position) => {
            const planned = plan.partitions[position];
            const size = row.querySelector('[data-field="size"]');
            if (planned && size) size.title = `${humanSize(planned.sizeBytes)}, cylinders ${planned.lowCylinder} to ${planned.highCylinder}`;
            if (planned && size && !size.value) size.placeholder = `The rest · ${humanSize(planned.sizeBytes)}`;
          });
        }
        const errors = state.error ? [state.error] : (plan?.errors || []);
        const warnings = state.error ? [] : (plan?.warnings || []);
        const carried = plan?.handlers?.length
          ? `<div class="help-note"><strong>Carried in the partition table:</strong> ${plan.handlers.map(handler => `${esc(handler.dosType.replace(/[\x00-\x1f]/g, character => `\\${character.charCodeAt(0)}`))} handler ${esc(handler.version)}`).join(", ")}. The machine loads them from the drive, so nothing has to be installed first.</div>`
          : "";
        // A handler that is missing can be supplied from here, so that the
        // layout does not have to be abandoned and built again to add one.
        const supply = (plan?.missingHandlers || []).map(row =>
          `<div class="help-note"><strong>${esc(row.label)}.</strong> The file is usually called ${esc(row.name)}, and is the program the Amiga keeps in L:. <button type="button" class="button ghost" data-supply-handler="${esc(row.family)}">Supply ${esc(row.name)}…</button></div>`).join("");
        messages.innerHTML = errors.map(text => `<div class="help-warning"><strong>This cannot be made yet.</strong> ${esc(text)}</div>`).join("")
          + supply
          + warnings.map(text => `<div class="help-note">${esc(text)}</div>`).join("")
          + carried;
      };

      const publish = () => onChange({ ...state, partitions: requestRows() });

      const check = async () => {
        const mine = ++sequence;
        const size = driveSize();
        if (!state.rows.length || size === null || size === "" || size === undefined) {
          state.plan = null;
          state.error = "";
          state.valid = false;
          state.pending = false;
          drawPlan();
          publish();
          return;
        }
        if (state.rows.some(row => !row.filesystem)) {
          state.plan = null;
          state.error = "Choose a filing system for every partition.";
          state.valid = false;
          state.pending = false;
          drawPlan();
          publish();
          return;
        }
        try {
          const data = await api("/api/drive-layout/plan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ size, partitions: requestRows() }),
          });
          if (mine !== sequence) return;
          state.plan = data.plan;
          state.error = "";
          state.valid = Boolean(data.plan.ok);
        } catch (error) {
          if (mine !== sequence) return;
          state.plan = null;
          state.error = error.message;
          state.valid = false;
        }
        state.pending = false;
        drawPlan();
        publish();
      };

      const schedule = () => {
        state.valid = false;
        state.pending = true;
        publish();
        clearTimeout(timer);
        timer = setTimeout(check, 250);
      };

      const applyPreset = async () => {
        if (!preset.value) return;
        if (preset.value === "empty") {
          state.rows = [];
          drawRows();
          schedule();
          return;
        }
        if (!large.value) {
          largeHelp.textContent = "Choose this first: the starting layout needs to know which filing system its large partitions use.";
          large.focus();
          return;
        }
        const size = driveSize();
        if (size === null || size === "" || size === undefined) return;
        try {
          const data = await api("/api/drive-layout/preset", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ preset: preset.value, filesystem: large.value, size }),
          });
          state.rows = data.partitions.map(row => ({ ...row, size: sizeText(row.sizeBytes) }));
          state.error = "";
        } catch (error) {
          state.error = error.message;
        }
        drawRows();
        schedule();
      };

      preset.addEventListener("change", applyPreset);
      large.addEventListener("change", () => {
        const chosen = options.filesystems.find(entry => entry.id === large.value);
        largeHelp.textContent = chosen?.note || "";
        applyPreset();
      });
      root.querySelector("[data-layout-add]").addEventListener("click", () => {
        state.rows = readRows();
        state.rows.push({
          name: nextDeviceName(state.rows),
          label: state.rows.length ? "Work" : "System",
          filesystem: large.value || "",
          size: "",
          bootable: !state.rows.length,
          bootPriority: state.rows.length ? -128 : 0,
        });
        drawRows();
        body.querySelector("tr:last-child input")?.focus();
        schedule();
      });
      body.addEventListener("click", event => {
        const button = event.target.closest("[data-layout-remove]");
        if (!button) return;
        const position = Number(button.closest("tr").dataset.layoutRow);
        state.rows = readRows().filter((_row, index) => index !== position);
        drawRows();
        schedule();
      });
      const handlerFile = document.createElement("input");
      handlerFile.type = "file";
      handlerFile.hidden = true;
      root.append(handlerFile);
      let handlerFamily = "";
      messages.addEventListener("click", event => {
        const button = event.target.closest("[data-supply-handler]");
        if (!button) return;
        handlerFamily = button.dataset.supplyHandler;
        handlerFile.value = "";
        handlerFile.click();
      });
      handlerFile.addEventListener("change", async () => {
        if (!handlerFile.files.length || !upload) return;
        const form = new FormData();
        form.append("family", handlerFamily);
        form.append("handler", handlerFile.files[0], handlerFile.files[0].name);
        try {
          const kept = await upload("/api/filesystem-handlers", form);
          toast(`${kept.kept[0].label} kept: ${kept.kept[0].description || kept.kept[0].name}`);
          schedule();
        } catch (error) {
          toast(error.message, true);
        }
      });
      const changed = () => {
        state.rows = readRows();
        schedule();
      };
      body.addEventListener("input", changed);
      body.addEventListener("change", changed);

      drawRows();
      if (state.rows.length) schedule();
      return {
        state,
        recheck: schedule,
        reapplyPreset: applyPreset,
        partitions: requestRows,
      };
    }

    return {
      attachEditor,
      editorMarkup,
      filesystemChoices,
      handlerSummary,
      layoutOptions,
      sizeText,
    };
  }

  return { create };
})();
