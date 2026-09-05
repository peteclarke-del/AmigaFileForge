window.AmigaEditorWorkspace = (() => {
  function create({ storage, key, maxDocuments = 24, maxDraftBytes = 512 * 1024, maxPanes = Number.POSITIVE_INFINITY }) {
    const state = {
      documents: new Map(),
      active: null,
      restoreCandidate: null,
    };

    function persist() {
      try {
        const documents = [...state.documents.values()].slice(-maxDocuments).map(document => ({
          ...document,
          draft: typeof document.draft === "string" ? document.draft.slice(0, maxDraftBytes) : null,
          savedValue: typeof document.savedValue === "string" ? document.savedValue.slice(0, maxDraftBytes) : null,
        }));
        storage.setItem(key, JSON.stringify({ active: state.active, documents }));
      } catch (_error) {
        // Storage can be unavailable in private browsing or after its quota is reached.
      }
    }

    function restore() {
      try {
        const saved = JSON.parse(storage.getItem(key) || "{}");
        if (!Array.isArray(saved.documents)) return;
        saved.documents.slice(-maxDocuments).forEach(document => {
          if (!document || typeof document.key !== "string" || !/^[0-9a-f]{32}$/.test(String(document.imageId || ""))) return;
          if (!Number.isInteger(document.index) || document.index < 0 || document.index >= maxPanes) return;
          if (typeof document.path !== "string" || typeof document.name !== "string") return;
          state.documents.set(document.key, {
            ...document,
            draft: typeof document.draft === "string" ? document.draft.slice(0, maxDraftBytes) : null,
            savedValue: typeof document.savedValue === "string" ? document.savedValue.slice(0, maxDraftBytes) : null,
          });
        });
        if (state.documents.has(saved.active)) state.restoreCandidate = saved.active;
      } catch (_error) {
        storage.removeItem(key);
      }
    }

    function remove(keyToRemove) {
      state.documents.delete(keyToRemove);
      if (state.active === keyToRemove) state.active = null;
      persist();
    }

    return { state, persist, remove, restore };
  }

  return { create };
})();
