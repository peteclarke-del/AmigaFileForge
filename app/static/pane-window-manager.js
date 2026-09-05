(function (global) {
  "use strict";

  const DEFAULT_MIN_WIDTH = 420;
  const DEFAULT_MIN_HEIGHT = 320;
  const SNAP_THRESHOLD = 34;
  const CASCADE_STEP = 34;
  const SNAP_TARGETS = new Set(["top", "left", "right", "bottom", "top-left", "top-right", "bottom-left", "bottom-right"]);

  const clamp = (value, minimum, maximum) => Math.min(Math.max(value, minimum), maximum);
  const escapeHtml = value => String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);

  function normaliseState(value = {}) {
    if (!value || typeof value !== "object") value = {};
    const number = (candidate, fallback) => Number.isFinite(Number(candidate)) ? Number(candidate) : fallback;
    return {
      x: number(value.x, 0),
      y: number(value.y, 0),
      width: number(value.width, 0),
      height: number(value.height, 0),
      z: Math.max(1, Math.trunc(number(value.z, 1))),
      minimized: Boolean(value.minimized),
      snap: SNAP_TARGETS.has(value.snap) ? value.snap : "",
      restore: value.restore && typeof value.restore === "object" ? {
        x: number(value.restore.x, 0),
        y: number(value.restore.y, 0),
        width: number(value.restore.width, 0),
        height: number(value.restore.height, 0),
      } : null,
    };
  }

  function availableBounds(container, taskbar) {
    const width = Math.max(1, container.clientWidth || container.getBoundingClientRect().width || 1);
    const taskbarHeight = taskbar && !taskbar.hidden ? taskbar.offsetHeight + 8 : 0;
    const height = Math.max(1, (container.clientHeight || container.getBoundingClientRect().height || 1) - taskbarHeight);
    return { width, height };
  }

  function defaultGeometry(index, bounds, minimums = {}) {
    const minWidth = Math.min(minimums.width || DEFAULT_MIN_WIDTH, bounds.width);
    const minHeight = Math.min(minimums.height || DEFAULT_MIN_HEIGHT, bounds.height);
    if (index === 0) return { x: 0, y: 0, width: bounds.width, height: bounds.height };
    const width = clamp(Math.round(bounds.width * 0.68), minWidth, Math.min(1040, bounds.width));
    const height = clamp(Math.round(bounds.height * 0.82), minHeight, bounds.height);
    const rangeX = Math.max(1, bounds.width - width);
    const rangeY = Math.max(1, bounds.height - height);
    return {
      x: Math.min((index * CASCADE_STEP) % Math.max(CASCADE_STEP, rangeX), rangeX),
      y: Math.min((index * CASCADE_STEP) % Math.max(CASCADE_STEP, rangeY), rangeY),
      width,
      height,
    };
  }

  function constrainGeometry(geometry, bounds, minimums = {}) {
    const minWidth = Math.min(minimums.width || DEFAULT_MIN_WIDTH, bounds.width);
    const minHeight = Math.min(minimums.height || DEFAULT_MIN_HEIGHT, bounds.height);
    const width = clamp(Number(geometry.width) || minWidth, minWidth, bounds.width);
    const height = clamp(Number(geometry.height) || minHeight, minHeight, bounds.height);
    return {
      x: clamp(Number(geometry.x) || 0, 0, Math.max(0, bounds.width - width)),
      y: clamp(Number(geometry.y) || 0, 0, Math.max(0, bounds.height - height)),
      width,
      height,
    };
  }

  function snapGeometry(target, bounds) {
    const halfWidth = Math.round(bounds.width / 2);
    const halfHeight = Math.round(bounds.height / 2);
    const layouts = {
      top: { x: 0, y: 0, width: bounds.width, height: bounds.height },
      left: { x: 0, y: 0, width: halfWidth, height: bounds.height },
      right: { x: halfWidth, y: 0, width: bounds.width - halfWidth, height: bounds.height },
      bottom: { x: 0, y: halfHeight, width: bounds.width, height: bounds.height - halfHeight },
      "top-left": { x: 0, y: 0, width: halfWidth, height: halfHeight },
      "top-right": { x: halfWidth, y: 0, width: bounds.width - halfWidth, height: halfHeight },
      "bottom-left": { x: 0, y: halfHeight, width: halfWidth, height: bounds.height - halfHeight },
      "bottom-right": { x: halfWidth, y: halfHeight, width: bounds.width - halfWidth, height: bounds.height - halfHeight },
    };
    return layouts[target] || null;
  }

  function snapTarget(point, bounds, threshold = SNAP_THRESHOLD) {
    const nearLeft = point.x <= threshold;
    const nearRight = point.x >= bounds.width - threshold;
    const nearTop = point.y <= threshold;
    const nearBottom = point.y >= bounds.height - threshold;
    if (nearTop && nearLeft) return "top-left";
    if (nearTop && nearRight) return "top-right";
    if (nearBottom && nearLeft) return "bottom-left";
    if (nearBottom && nearRight) return "bottom-right";
    if (nearTop) return "top";
    if (nearBottom) return "bottom";
    if (nearLeft) return "left";
    if (nearRight) return "right";
    return "";
  }

  function create(options) {
    const {
      container,
      taskbar,
      panes,
      paneLabel,
      onChange = () => {},
      minWidth = DEFAULT_MIN_WIDTH,
      minHeight = DEFAULT_MIN_HEIGHT,
    } = options;
    let highestZ = panes.reduce((highest, pane) => Math.max(highest, Number(pane.windowState?.z) || 0), 1);
    let activeIndex = null;
    let previewTarget = "";

    container.classList.add("freeform-panes");
    const preview = document.createElement("div");
    preview.className = "pane-snap-preview";
    preview.hidden = true;
    container.append(preview);

    const bounds = () => availableBounds(container, taskbar);
    const paneHost = index => container.querySelector(`.pane[data-pane="${index}"]`);
    let lastBounds = bounds();

    function ensureState(index) {
      const pane = panes[index];
      if (!pane) return null;
      const hadGeometry = pane.windowState && Number(pane.windowState.width) > 0 && Number(pane.windowState.height) > 0;
      const existing = pane.windowState;
      const normalised = normaliseState(existing);
      // Pointer interactions retain this object while apply() runs. Replacing
      // it here would leave the active drag or resize updating a stale object.
      pane.windowState = existing && typeof existing === "object"
        ? Object.assign(existing, normalised)
        : normalised;
      if (!hadGeometry) Object.assign(pane.windowState, defaultGeometry(index, bounds(), { width: minWidth, height: minHeight }));
      highestZ = Math.max(highestZ, pane.windowState.z);
      return pane.windowState;
    }

    function geometryFor(index) {
      const state = ensureState(index);
      if (!state) return null;
      return state.snap
        ? snapGeometry(state.snap, bounds())
        : constrainGeometry(state, bounds(), { width: minWidth, height: minHeight });
    }

    function apply(index) {
      const host = paneHost(index);
      const state = ensureState(index);
      if (!host || !state) return;
      const geometry = geometryFor(index);
      Object.assign(state, geometry);
      host.classList.add("pane-window");
      host.classList.toggle("pane-minimized", state.minimized);
      host.classList.toggle("pane-snapped", Boolean(state.snap));
      host.style.left = `${geometry.x}px`;
      host.style.top = `${geometry.y}px`;
      host.style.width = `${geometry.width}px`;
      host.style.height = `${geometry.height}px`;
      host.style.zIndex = String(state.z);
      host.hidden = state.minimized;
      host.setAttribute("aria-label", paneLabel(index));
      const maximize = host.querySelector(".maximize-pane");
      if (maximize) {
        const maximized = state.snap === "top";
        maximize.title = maximized ? "Restore Pane" : "Maximise Pane";
        maximize.setAttribute("aria-label", maximize.title);
        maximize.setAttribute("aria-pressed", String(maximized));
      }
    }

    function changed() {
      onChange();
    }

    function bringToFront(index, persist = true) {
      const state = ensureState(index);
      if (!state) return;
      if (activeIndex !== index) {
        state.z = ++highestZ;
        activeIndex = index;
      }
      const host = paneHost(index);
      if (host) host.style.zIndex = String(state.z);
      if (persist) changed();
    }

    function setSnap(index, target) {
      const state = ensureState(index);
      if (!state || !snapGeometry(target, bounds())) return;
      if (!state.snap) state.restore = { x: state.x, y: state.y, width: state.width, height: state.height };
      state.snap = target;
      state.minimized = false;
      bringToFront(index, false);
      apply(index);
      renderTaskbar();
      changed();
    }

    function restoreGeometry(index) {
      const state = ensureState(index);
      if (!state) return;
      if (state.snap && state.restore) Object.assign(state, constrainGeometry(state.restore, bounds(), { width: minWidth, height: minHeight }));
      state.snap = "";
      state.restore = null;
      state.minimized = false;
      bringToFront(index, false);
      apply(index);
      renderTaskbar();
      changed();
    }

    function detachSnap(index) {
      const state = ensureState(index);
      if (!state?.snap) return state;
      Object.assign(state, snapGeometry(state.snap, bounds()), { snap: "", restore: null });
      apply(index);
      return state;
    }

    function toggleMaximize(index) {
      if (ensureState(index)?.snap === "top") restoreGeometry(index); else setSnap(index, "top");
    }

    function minimize(index) {
      const state = ensureState(index);
      if (!state) return;
      state.minimized = true;
      apply(index);
      renderTaskbar();
      changed();
    }

    function restore(index) {
      const state = ensureState(index);
      if (!state) return;
      state.minimized = false;
      bringToFront(index, false);
      apply(index);
      renderTaskbar();
      paneHost(index)?.querySelector(".pane-drag-handle")?.focus();
      changed();
    }

    function showPreview(target) {
      previewTarget = target;
      const geometry = snapGeometry(target, bounds());
      preview.hidden = !geometry;
      if (!geometry) return;
      preview.dataset.snap = target;
      Object.assign(preview.style, {
        left: `${geometry.x}px`, top: `${geometry.y}px`,
        width: `${geometry.width}px`, height: `${geometry.height}px`,
      });
    }

    function hidePreview() {
      previewTarget = "";
      preview.hidden = true;
      delete preview.dataset.snap;
    }

    function trackPointer(event, move, finish) {
      const pointerId = event.pointerId;
      const pointerTarget = event.currentTarget;
      const onMove = moveEvent => {
        if (moveEvent.pointerId === pointerId) move(moveEvent);
      };
      const onEnd = endEvent => {
        if (endEvent.pointerId !== pointerId) return;
        global.removeEventListener("pointermove", onMove);
        global.removeEventListener("pointerup", onEnd);
        global.removeEventListener("pointercancel", onEnd);
        if (pointerTarget?.hasPointerCapture?.(pointerId)) pointerTarget.releasePointerCapture(pointerId);
        finish(endEvent);
      };
      global.addEventListener("pointermove", onMove);
      global.addEventListener("pointerup", onEnd);
      global.addEventListener("pointercancel", onEnd);
    }

    function wireDragging(index, host) {
      const handle = host.querySelector(".pane-drag-handle");
      if (!handle) return;
      handle.draggable = false;
      handle.title = "Drag the pane. You can also drag an empty part of its heading.";
      handle.setAttribute("aria-label", `Move ${paneLabel(index)}. Alt plus an arrow key snaps or minimises it. Shift plus Alt and an arrow key resizes it.`);
      const startDragging = event => {
        if (event.button !== 0) return;
        if (event.currentTarget.classList.contains("pane-head") && event.target.closest("button, input, a, summary, details, .image-title, .format-icon, .pane-head-actions")) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture?.(event.pointerId);
        bringToFront(index, false);
        const state = ensureState(index);
        const workspaceRect = container.getBoundingClientRect();
        const currentBounds = bounds();
        const fillsWorkspace = state.width >= currentBounds.width - 1 && state.height >= currentBounds.height - 1;
        if (state.snap || fillsWorkspace) {
          const restored = constrainGeometry(state.restore || defaultGeometry(index + 1, bounds(), { width: minWidth, height: minHeight }), bounds(), { width: minWidth, height: minHeight });
          const ratio = clamp((event.clientX - workspaceRect.left - state.x) / Math.max(1, state.width), 0.15, 0.85);
          Object.assign(state, restored, {
            x: clamp(event.clientX - workspaceRect.left - restored.width * ratio, 0, Math.max(0, bounds().width - restored.width)),
            y: clamp(event.clientY - workspaceRect.top - 18, 0, Math.max(0, bounds().height - restored.height)),
            snap: "", restore: null,
          });
          apply(index);
        }
        const start = { x: event.clientX, y: event.clientY, left: state.x, top: state.y };
        host.classList.add("pane-moving");
        trackPointer(event, moveEvent => {
          const currentBounds = bounds();
          state.x = clamp(start.left + moveEvent.clientX - start.x, 0, Math.max(0, currentBounds.width - state.width));
          state.y = clamp(start.top + moveEvent.clientY - start.y, 0, Math.max(0, currentBounds.height - state.height));
          apply(index);
          showPreview(snapTarget({ x: moveEvent.clientX - workspaceRect.left, y: moveEvent.clientY - workspaceRect.top }, currentBounds));
        }, () => {
          host.classList.remove("pane-moving");
          if (previewTarget) setSnap(index, previewTarget); else changed();
          hidePreview();
        });
      };
      handle.onpointerdown = startDragging;
      const heading = host.querySelector(".pane-head");
      if (heading) heading.onpointerdown = startDragging;
      handle.ondblclick = event => {
        event.preventDefault();
        toggleMaximize(index);
      };
      handle.onkeydown = event => {
        if (!event.altKey || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
        event.preventDefault();
        if (event.shiftKey) {
          const state = detachSnap(index);
          const widthDelta = event.key === "ArrowRight" ? 32 : event.key === "ArrowLeft" ? -32 : 0;
          const heightDelta = event.key === "ArrowDown" ? 32 : event.key === "ArrowUp" ? -32 : 0;
          Object.assign(state, constrainGeometry({
            x: state.x, y: state.y, width: state.width + widthDelta, height: state.height + heightDelta,
          }, bounds(), { width: minWidth, height: minHeight }));
          apply(index);
          changed();
          return;
        }
        if (event.key === "ArrowDown") minimize(index);
        else setSnap(index, event.key === "ArrowLeft" ? "left" : event.key === "ArrowRight" ? "right" : "top");
      };
    }

    function resizeGeometry(start, direction, dx, dy, currentBounds) {
      let { x, y, width, height } = start;
      if (direction.includes("e")) width += dx;
      if (direction.includes("s")) height += dy;
      if (direction.includes("w")) { x += dx; width -= dx; }
      if (direction.includes("n")) { y += dy; height -= dy; }
      if (width < Math.min(minWidth, currentBounds.width)) {
        if (direction.includes("w")) x -= Math.min(minWidth, currentBounds.width) - width;
        width = Math.min(minWidth, currentBounds.width);
      }
      if (height < Math.min(minHeight, currentBounds.height)) {
        if (direction.includes("n")) y -= Math.min(minHeight, currentBounds.height) - height;
        height = Math.min(minHeight, currentBounds.height);
      }
      if (x < 0) { width += x; x = 0; }
      if (y < 0) { height += y; y = 0; }
      width = Math.min(width, currentBounds.width - x);
      height = Math.min(height, currentBounds.height - y);
      return constrainGeometry({ x, y, width, height }, currentBounds, { width: minWidth, height: minHeight });
    }

    function wireResizing(index, host) {
      host.querySelectorAll(".pane-resize-handle").forEach(handle => handle.remove());
      for (const direction of ["n", "ne", "e", "se", "s", "sw", "w", "nw"]) {
        const handle = document.createElement("span");
        handle.className = `pane-resize-handle resize-${direction}`;
        handle.dataset.resize = direction;
        handle.setAttribute("aria-hidden", "true");
        handle.onpointerdown = event => {
          if (event.button !== 0) return;
          event.preventDefault();
          event.stopPropagation();
          handle.setPointerCapture?.(event.pointerId);
          detachSnap(index);
          bringToFront(index, false);
          const state = ensureState(index);
          const start = { x: state.x, y: state.y, width: state.width, height: state.height, pointerX: event.clientX, pointerY: event.clientY };
          host.classList.add("pane-resizing");
          trackPointer(event, moveEvent => {
            Object.assign(state, resizeGeometry(start, direction, moveEvent.clientX - start.pointerX, moveEvent.clientY - start.pointerY, bounds()));
            apply(index);
          }, () => {
            host.classList.remove("pane-resizing");
            changed();
          });
        };
        host.append(handle);
      }
    }

    function wireControls(index, host) {
      const minimizeButton = host.querySelector(".minimize-pane");
      if (minimizeButton) minimizeButton.onclick = event => {
        event.stopPropagation();
        minimize(index);
      };
      const maximizeButton = host.querySelector(".maximize-pane");
      if (maximizeButton) maximizeButton.onclick = event => {
        event.stopPropagation();
        toggleMaximize(index);
      };
    }

    function mount(index, host = paneHost(index)) {
      if (!host || !panes[index]) return;
      apply(index);
      if (!host.dataset.windowManagerEvents) {
        host.dataset.windowManagerEvents = "1";
        host.addEventListener("pointerdown", event => {
          if (!event.target.closest(".pane-resize-handle")) bringToFront(index);
        });
        host.addEventListener("focus", () => bringToFront(index), true);
      }
      wireDragging(index, host);
      wireResizing(index, host);
      wireControls(index, host);
    }

    function renderTaskbar() {
      const minimized = panes.map((pane, index) => ({ pane, index })).filter(({ pane }) => pane.windowState?.minimized);
      taskbar.hidden = minimized.length === 0;
      taskbar.innerHTML = minimized.map(({ index }) => {
        const label = escapeHtml(paneLabel(index));
        return `<button type="button" data-restore-pane="${index}" title="Restore ${label}"><span aria-hidden="true">▣</span><b>${label}</b></button>`;
      }).join("");
      taskbar.querySelectorAll("[data-restore-pane]").forEach(button => {
        button.onclick = () => restore(Number(button.dataset.restorePane));
      });
      panes.forEach((_pane, index) => apply(index));
    }

    function reconcile() {
      activeIndex = null;
      container.querySelectorAll(".pane[data-pane]").forEach(host => mount(Number(host.dataset.pane), host));
      renderTaskbar();
    }

    function resizeWorkspace() {
      const nextBounds = bounds();
      const previousBounds = lastBounds;
      lastBounds = nextBounds;
      const changedSize = previousBounds.width !== nextBounds.width || previousBounds.height !== nextBounds.height;
      if (changedSize && previousBounds.width > 1 && previousBounds.height > 1) {
        const scaleX = nextBounds.width / previousBounds.width;
        const scaleY = nextBounds.height / previousBounds.height;
        panes.forEach((_pane, index) => {
          const state = ensureState(index);
          if (!state) return;
          if (state.restore) {
            state.restore = constrainGeometry({
              x: state.restore.x * scaleX,
              y: state.restore.y * scaleY,
              width: state.restore.width * scaleX,
              height: state.restore.height * scaleY,
            }, nextBounds, { width: minWidth, height: minHeight });
          }
          if (state.snap) return;
          Object.assign(state, constrainGeometry({
            x: state.x * scaleX,
            y: state.y * scaleY,
            width: state.width * scaleX,
            height: state.height * scaleY,
          }, nextBounds, { width: minWidth, height: minHeight }));
        });
      }
      panes.forEach((_pane, index) => apply(index));
      if (!preview.hidden && previewTarget) showPreview(previewTarget);
      if (changedSize) changed();
    }

    const resizeObserver = typeof ResizeObserver === "function" ? new ResizeObserver(resizeWorkspace) : null;
    resizeObserver?.observe(container);
    global.addEventListener?.("resize", resizeWorkspace);

    return {
      apply,
      bringToFront,
      minimize,
      mount,
      reconcile,
      restore,
      setSnap,
      toggleMaximize,
    };
  }

  global.AmigaPaneWindowManager = {
    constrainGeometry,
    create,
    defaultGeometry,
    normaliseState,
    snapGeometry,
    snapTarget,
  };
})(window);
