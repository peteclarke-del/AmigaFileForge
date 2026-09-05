window.AmigaTransferPlanning = (() => {
  function create({ targetNameRule }) {
    function folderTargetPlans(pane, records, mode) {
      const preserve = mode === "preserve" && pane.image.kind === "ffs";
      const componentNames = new Map();
      const usedByParent = new Map();
      const changes = [];
      const allocate = (parent, original, identity = "") => {
        const mapKey = `${parent}\u0000${original}\u0000${identity}`;
        if (componentNames.has(mapKey)) return componentNames.get(mapKey);
        const rule = targetNameRule(pane, original);
        const used = usedByParent.get(parent) || new Set();
        let candidate = rule.suggested;
        let suffix = 1;
        while (used.has(candidate.toLowerCase())) {
          const tail = String(suffix++);
          candidate = `${rule.suggested.slice(0, rule.limit - tail.length)}${tail}`;
        }
        used.add(candidate.toLowerCase());
        usedByParent.set(parent, used);
        componentNames.set(mapKey, candidate);
        if (candidate !== original) changes.push(`${original} → ${candidate}`);
        return candidate;
      };
      return {
        changes,
        plans: records.map(item => {
          const sourceParts = item.relativePath.replace(/\\/g, "/").split("/").filter(Boolean);
          if (item.metadata?.targetName) sourceParts[sourceParts.length - 1] = item.metadata.targetName;
          const keptParts = preserve ? sourceParts : sourceParts.slice(-1);
          const targetParts = [];
          for (const [partIndex, part] of keptParts.entries()) {
            const parent = targetParts.join("/").toLowerCase();
            const identity = !preserve && partIndex === keptParts.length - 1 ? item.relativePath : "";
            targetParts.push(allocate(parent, part, identity));
          }
          return { ...item, targetPath: targetParts.join("/") };
        }),
      };
    }
    return { folderTargetPlans };
  }
  return { create };
})();
