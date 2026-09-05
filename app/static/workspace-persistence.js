(function (global) {
  "use strict";

  function create(options) {
    const {
      panes,
      storage,
      storageKey,
      newPaneState,
      restoredOfsPath,
      api,
      rebuildPaneHosts,
      reconcilePaneWindows = () => {},
      renderPane,
      acceptImage,
      loadDirectory,
      editorWorkspace,
      activateEditorDocument,
      toast,
    } = options;
    let ready = false;

    function remember() {
      if (!ready) return;
      const snapshot = panes.map(pane => ({
        imageId: pane.image?.id || null,
        partition: pane.partition,
        side: pane.side,
        path: pane.path,
        archivePath: pane.archivePath,
        archiveName: pane.archiveName,
        archiveMember: pane.archiveMember,
        pathModel: pane.image?.kind === "ofs" ? "ofs-prefixes" : "hierarchical",
        windowState: pane.windowState,
      }));
      try {
        storage.setItem(storageKey, JSON.stringify(snapshot));
      } catch (_error) {
        // Server-side recovery remains available when browser storage is unavailable.
      }
    }

    function stored() {
      try {
        const saved = JSON.parse(storage.getItem(storageKey) || "[]");
        return Array.isArray(saved) ? saved : [];
      } catch (_error) {
        return [];
      }
    }

    function hasStored() {
      try {
        return storage.getItem(storageKey) !== null;
      } catch (_error) {
        return true;
      }
    }

    async function restore() {
      let savedPanes = stored();
      if (!hasStored()) {
        try {
          const recoverable = await api("/api/images/recoverable");
          const newest = recoverable.images?.[0];
          if (newest) savedPanes = [{ imageId: newest.id, partition: null, side: null, path: "" }];
        } catch (_error) {
          // Leave the empty workspace available if server recovery is unavailable.
        }
      }
      const paneCount = Math.max(1, savedPanes.length || 1);
      while (panes.length < paneCount) panes.push(newPaneState());
      rebuildPaneHosts();
      for (const [index, saved] of savedPanes.entries()) {
        if (saved && typeof saved.windowState === "object") panes[index].windowState = saved.windowState;
        if (!saved?.imageId) continue;
        if (!saved || !/^[0-9a-f]{32}$/.test(String(saved.imageId || ""))) continue;
        panes[index].loading = true;
        panes[index].loadingMessage = "Restoring your open image…";
        renderPane(index);
        try {
          const data = await api(`/api/images/${encodeURIComponent(saved.imageId)}`);
          await acceptImage(index, data.image);
          const pane = panes[index];
          pane.side = saved.side === 2 ? 2 : data.image.doubleSided ? 0 : null;
          if (data.image.kind === "hdf" && Number.isInteger(saved.partition)) {
            const volume = pane.entries.find(entry => entry.partition === saved.partition);
            if (volume) {
              pane.partition = saved.partition;
              pane.partitionName = volume.name;
              pane.path = restoredOfsPath(saved);
              await loadDirectory(index);
            }
          } else if (
            data.image.kind !== "hdf"
            && typeof saved.path === "string"
            && (
              (data.image.kind === "ofs" && restoredOfsPath(saved) !== "")
              || (data.image.kind !== "ofs" && saved.path !== "$")
              || pane.side !== (data.image.doubleSided ? 0 : null)
            )
          ) {
            pane.path = data.image.kind === "ofs" ? restoredOfsPath(saved) : saved.path;
            await loadDirectory(index);
          }
          if (typeof saved.archivePath === "string" && saved.archivePath) {
            pane.archivePath = saved.archivePath;
            pane.archiveName = String(saved.archiveName || "Archive");
            pane.archiveMember = String(saved.archiveMember || "");
            await loadDirectory(index);
          }
        } catch (error) {
          panes[index] = newPaneState();
          renderPane(index);
          if (error.status !== 404) toast(`Could not restore an open pane: ${error.message}`, true);
        }
      }
      ready = true;
      remember();
      panes.forEach((_pane, index) => renderPane(index));
      reconcilePaneWindows();
      if (editorWorkspace.state.restoreCandidate) {
        const key = editorWorkspace.state.restoreCandidate;
        editorWorkspace.state.restoreCandidate = null;
        editorWorkspace.state.active = null;
        await activateEditorDocument(key, true);
      }
    }

    return { remember, restore, stored, hasStored, isReady: () => ready };
  }

  global.AmigaWorkspacePersistence = { create };
})(window);
