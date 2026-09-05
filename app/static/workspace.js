window.AmigaWorkspace = (() => {
  function newPaneState(image = null) {
    return {
      image,
      partition: null,
      side: image?.doubleSided ? 0 : null,
      partitionName: "",
      path: "$",
      archivePath: null,
      archiveName: "",
      archiveMember: "",
      archiveKind: "",
      entries: [],
      capacity: null,
      selected: null,
      selection: [],
      selectionAnchor: null,
      loading: Boolean(image),
      requestToken: 0,
      menuDetected: false,
      fileKinds: {},
      windowState: null,
      menuDetectionPending: Boolean(image?.kind === "hdf")
    };
  }

  const isOfsPane = pane => (
    pane?.image?.kind === "ofs"
    || (pane?.image?.kind === "hdf" && pane.partition !== null)
  );

  // A saved workspace written before the separator changed spells the volume
  // root "$". The root is the empty path now, so those are folded back.
  function restoredOfsPath(saved) {
    const path = typeof saved?.path === "string" ? saved.path : "";
    return path === "$" || path === ":" ? "" : path;
  }

  function normalisePage(value) {
    const cleaned = String(value || "").trim().replace(/^&/, "").toUpperCase();
    return cleaned.replace(/^0+(?=[0-9A-F])/, "") || "0";
  }

  // AmigaDOS separates path components with "/" and names the volume root
  // with a bare ":". A full stop cannot be a separator, because Amiga
  // filenames routinely contain one.
  function splitPath(path) {
    const text = String(path ?? "").trim();
    if (text === "" || text === "$" || text === ":" || text === "/") return [];
    return text.replace(/^[$:]/, "").split("/").filter(Boolean);
  }

  function fullPath(directory, name) {
    const leaf = String(name ?? "").replace(/^\/+|\/+$/g, "");
    const parts = splitPath(directory);
    return leaf ? [...parts, leaf].join("/") : parts.join("/");
  }

  function parentPath(path) {
    return splitPath(path).slice(0, -1).join("/");
  }

  function selectionKeys(pane) {
    if (Array.isArray(pane.selection) && pane.selection.length) {
      return pane.selection.map(String);
    }
    return pane.selected == null ? [] : [String(pane.selected)];
  }

  function setSelection(pane, keys, anchor = null) {
    pane.selection = [...new Set(keys.map(String))];
    pane.selected = pane.selection.length === 1 ? pane.selection[0] : null;
    pane.selectionAnchor = anchor ?? pane.selection.at(-1) ?? null;
  }

  const entrySelectionKey = entry => String(entry.partition ?? entry.path ?? entry.name);
  const pathNameWithoutExtension = value => String(value || "").replace(/\.[^.]+$/, "");

  return {
    entrySelectionKey,
    fullPath,
    splitPath,
    isOfsPane,
    newPaneState,
    normalisePage,
    parentPath,
    pathNameWithoutExtension,
    restoredOfsPath,
    selectionKeys,
    setSelection,
  };
})();
