const { chromium } = require("playwright");

const target = process.env.AMIGA_FILE_FORGE_URL || "http://127.0.0.1:8666";

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
  const created = [];
  try {
    await page.goto(target, { waitUntil: "domcontentloaded" });
    const ids = await page.evaluate(async () => {
      const create = async title => {
        const response = await fetch("/api/images/create", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ format: "adf", title }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `Create failed: ${response.status}`);
        return result.image.id;
      };
      return [await create("COMPAREA"), await create("COMPAREB")];
    });
    created.push(...ids);
    await page.evaluate(imageIds => {
      localStorage.setItem("amiga-file-forge-dynamic-panes", JSON.stringify(
        imageIds.map((imageId, index) => ({
          imageId,
          slot: null,
          side: null,
          path: "",
          windowState: {
            x: 20 + index * 700,
            y: 20,
            width: 670,
            height: 720,
            z: index + 1,
            minimized: false,
            snap: "",
            restore: null,
          },
        })),
      ));
      location.reload();
    }, ids);
    await page.waitForLoadState("domcontentloaded");
    await page.waitForFunction(() => document.querySelectorAll(".pane .image-title").length === 2);

    await page.getByRole("button", { name: "Search" }).click();
    const initialSearchBox = await page.locator("#modal").boundingBox();
    if (!initialSearchBox || initialSearchBox.height > 330 || initialSearchBox.width > 710) {
      throw new Error(`Empty workspace search is oversized: ${JSON.stringify(initialSearchBox)}`);
    }
    await page.locator('[name="workspaceQuery"]').fill("NOT-PRESENT");
    await page.locator("[data-run-workspace-search]").click();
    await page.waitForFunction(() => !document.querySelector(".workspace-search-status")?.textContent.includes("Searching"));
    const searchStatus = await page.locator(".workspace-search-status").textContent();
    if (!searchStatus.includes("across 2 images")) throw new Error(`Workspace search omitted an image: ${searchStatus}`);
    const searchJobs = await page.evaluate(async () => {
      const response = await fetch("/api/operations");
      const data = await response.json();
      return data.operations.filter(item => item.message === "Workspace image search complete");
    });
    if (searchJobs.length !== 2 || searchJobs.some(item => item.state !== "complete")) {
      throw new Error(`Workspace searches were not tracked to completion: ${JSON.stringify(searchJobs)}`);
    }
    await page.locator("#modal .modal-close").click();

    const firstPane = page.locator('.pane[data-pane="0"]');
    await firstPane.locator("summary", { hasText: "Analyse" }).click();
    await firstPane.locator(".export-manifest").click();
    const [manifestDownload] = await Promise.all([
      page.waitForEvent("download"),
      page.locator('[data-manifest="json"]').click(),
    ]);
    if (!manifestDownload.suggestedFilename().endsWith("-manifest.json")) {
      throw new Error(`Manifest used an unexpected filename: ${manifestDownload.suggestedFilename()}`);
    }
    const manifestJob = await page.evaluate(async () => {
      const response = await fetch("/api/operations");
      const data = await response.json();
      return data.operations.find(item => item.message === "Collection manifest ready");
    });
    if (!manifestJob || manifestJob.state !== "complete") {
      throw new Error(`Manifest export was not tracked to completion: ${JSON.stringify(manifestJob)}`);
    }
    await page.locator("#modal").waitFor({ state: "hidden" });

    // A completed tracked operation replaces the pane once to clear its busy
    // state. In slower CI renderers that replacement can land between opening
    // this menu and Playwright's stability check. Resolve the command afresh
    // and reopen its containing menu for the single permitted retry.
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const analyseMenu = firstPane.locator("summary", { hasText: "Analyse" });
      const findDuplicates = firstPane.locator(".find-duplicates");
      try {
        if (!(await findDuplicates.isVisible())) await analyseMenu.click();
        await findDuplicates.click({ timeout: 5000 });
        break;
      } catch (error) {
        if (attempt === 1) throw error;
        if (await analyseMenu.isVisible()) await analyseMenu.click();
      }
    }
    await page.locator("#modal").waitFor({ state: "visible" });
    await page.locator("#modal .duplicate-groups").waitFor({ state: "visible" });
    const duplicateJob = await page.evaluate(async () => {
      const response = await fetch("/api/operations");
      const data = await response.json();
      return data.operations.find(item => item.message === "Duplicate analysis complete");
    });
    if (!duplicateJob || duplicateJob.state !== "complete") {
      throw new Error(`Duplicate analysis was not tracked to completion: ${JSON.stringify(duplicateJob)}`);
    }
    await page.locator("#modal .modal-close").click();
    await page.locator("#modal").waitFor({ state: "hidden" });

    await firstPane.locator("summary", { hasText: "Analyse" }).click();
    const compare = firstPane.locator(".compare-image");
    if (await compare.isDisabled()) throw new Error("Comparison was disabled with two different images open");
    await compare.click();
    const initialComparisonBox = await page.locator("#modal").boundingBox();
    if (!initialComparisonBox || initialComparisonBox.height > 400 || initialComparisonBox.width > 790) {
      throw new Error(`Empty image comparison is oversized: ${JSON.stringify(initialComparisonBox)}`);
    }
    await page.locator("[data-run-comparison]").click();
    await page.locator(".comparison-summary").waitFor();
    const total = (await page.locator(".comparison-summary strong").last().locator("span").textContent()).trim();
    if (total !== "0") throw new Error(`Identical blank filesystems reported ${total} changes`);
    if (await page.locator("[data-export-comparison]").isDisabled()) throw new Error("Comparison export stayed disabled");
    const comparisonJob = await page.evaluate(async () => {
      const response = await fetch("/api/operations");
      const data = await response.json();
      return data.operations.find(item => item.message === "Image comparison complete");
    });
    if (!comparisonJob || comparisonJob.state !== "complete" || comparisonJob.current !== 3 || comparisonJob.total !== 3) {
      throw new Error(`Comparison did not retain useful operation progress: ${JSON.stringify(comparisonJob)}`);
    }
    if (!(await page.locator(".raw-comparison").textContent()).includes("Primary image")) {
      throw new Error("The comparison omitted raw component evidence");
    }
    const dialogBox = await page.locator("#modal").boundingBox();
    const contentBox = await page.locator(".image-comparison-dialog").boundingBox();
    if (!dialogBox || !contentBox || contentBox.x < dialogBox.x || contentBox.x + contentBox.width > dialogBox.x + dialogBox.width + 1) {
      throw new Error("Image comparison content overflowed its dialog");
    }

    await page.evaluate(async imageId => {
      const response = await fetch(`/api/images/${imageId}/empty-file`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ destination: "", name: "PATCHED", protection: "----rwed" }),
      });
      if (!response.ok) throw new Error((await response.json()).error || "Could not prepare patch candidate");
    }, ids[1]);

    const selectivePatch = await page.evaluate(async imageIds => {
      const comparisonResponse = await fetch(`/api/images/${imageIds[0]}/compare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ otherImage: imageIds[1] }),
      });
      const comparison = await comparisonResponse.json();
      if (!comparisonResponse.ok) throw new Error(comparison.error || "Could not refresh comparison");
      const selectedKeys = comparison.changes.added.map(change => change.key);
      const response = await fetch(`/api/images/${imageIds[0]}/patch/build`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ otherImage: imageIds[1], selectedKeys }),
      });
      if (!response.ok) throw new Error((await response.json()).error || "Could not create selective patch");
      return {
        type: response.headers.get("content-type"),
        disposition: response.headers.get("content-disposition"),
        size: (await response.blob()).size,
      };
    }, ids);
    if (!selectivePatch.type?.includes("zip") || !selectivePatch.disposition?.includes("affpatch.zip") || selectivePatch.size < 100) {
      throw new Error(`Selective patch download was invalid: ${JSON.stringify(selectivePatch)}`);
    }

    await page.locator("#modal .modal-close").click();
    await firstPane.locator("summary", { hasText: "Analyse" }).click();
    await firstPane.locator(".apply-image-patch").click();
    if (!(await page.locator("[data-apply-patch]").isDisabled())) {
      throw new Error("Patch apply was enabled before preflight verification");
    }
    await page.evaluate(async imageIds => {
      const response = await fetch(`/api/images/${imageIds[0]}/patch?otherImage=${imageIds[1]}`);
      if (!response.ok) throw new Error((await response.json()).error || "Could not create patch for preflight");
      const file = new File([await response.blob()], "browser.affpatch.zip", { type: "application/zip" });
      const transfer = new DataTransfer();
      transfer.items.add(file);
      const input = document.querySelector('[name="patch"]');
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }, ids);
    await page.waitForFunction(() => !document.querySelector("[data-apply-patch]")?.disabled);
    const preflight = await page.locator(".patch-preflight-results").textContent();
    if (!preflight.includes("BASEblank.adf") || !preflight.includes("CANDIDATEblank.adf") || !preflight.includes("1operations") || !preflight.includes("PATCHEDadded")) {
      throw new Error(`Patch preflight did not describe the exact change: ${preflight}`);
    }
    const preflightJob = await page.evaluate(async () => {
      const response = await fetch("/api/operations");
      const data = await response.json();
      return data.operations.find(item => item.message === "Patch preflight complete");
    });
    if (!preflightJob || preflightJob.state !== "complete"
      || typeof preflightJob.current !== "number" || preflightJob.current !== preflightJob.total) {
      throw new Error(`Patch preflight did not retain byte progress: ${JSON.stringify(preflightJob)}`);
    }
    const preflightBox = await page.locator("#modal").boundingBox();
    if (!preflightBox || preflightBox.width > 790 || preflightBox.height > 760) {
      throw new Error(`Patch preflight dialog is oversized: ${JSON.stringify(preflightBox)}`);
    }
    await page.locator("#modal .modal-close").click();

    const patchResult = await page.evaluate(async imageIds => {
      const patchResponse = await fetch(`/api/images/${imageIds[0]}/patch?otherImage=${imageIds[1]}`);
      if (!patchResponse.ok) throw new Error((await patchResponse.json()).error || "Could not create patch");
      const patchBlob = await patchResponse.blob();
      const form = new FormData();
      form.append("patch", patchBlob, "browser.affpatch.zip");
      const applyResponse = await fetch(`/api/images/${imageIds[0]}/patch`, { method: "POST", body: form });
      const applied = await applyResponse.json();
      if (!applyResponse.ok) throw new Error(applied.error || "Could not apply patch");
      const compareResponse = await fetch(`/api/images/${imageIds[0]}/compare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ otherImage: imageIds[1] }),
      });
      const comparison = await compareResponse.json();
      if (!compareResponse.ok) throw new Error(comparison.error || "Could not verify patch");
      const staleForm = new FormData();
      staleForm.append("patch", patchBlob, "browser.affpatch.zip");
      const staleResponse = await fetch(`/api/images/${imageIds[0]}/patch`, { method: "POST", body: staleForm });
      return {
        operations: applied.patch.operations,
        remaining: comparison.summary.total,
        staleStatus: staleResponse.status,
        staleError: (await staleResponse.json()).error,
      };
    }, ids);
    if (patchResult.operations !== 1 || patchResult.remaining !== 0) {
      throw new Error(`Patch round trip did not converge: ${JSON.stringify(patchResult)}`);
    }
    if (patchResult.staleStatus !== 400 || !patchResult.staleError.includes("exact base revision")) {
      throw new Error(`A stale patch was not rejected explicitly: ${JSON.stringify(patchResult)}`);
    }
    console.log("Workspace search, image comparison and guarded patch preflight browser regression passed");
  } finally {
    for (const id of created) {
      await page.evaluate(async imageId => fetch(`/api/images/${imageId}`, { method: "DELETE" }), id).catch(() => {});
    }
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
