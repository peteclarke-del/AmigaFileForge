const { chromium } = require("playwright");

const target = process.env.AMIGA_FILE_FORGE_URL || "http://127.0.0.1:8666";

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  try {
    await page.goto(target, { waitUntil: "networkidle" });
    await page.evaluate(() => {
      localStorage.removeItem("amiga-file-forge-dynamic-panes");
      sessionStorage.removeItem("amiga-file-forge-dynamic-panes");
      sessionStorage.removeItem("amiga-file-forge-editor-documents-v1");
    });
    await page.reload({ waitUntil: "networkidle" });

    const panes = page.locator(".pane");
    if (await panes.count() !== 1) throw new Error("Workspace did not start with one pane");

    // The initial pane fills the workspace. Its first drag must restore it to
    // a movable size instead of leaving x/y clamped at zero.
    const firstPane = panes.first();
    const firstInitial = await firstPane.boundingBox();
    const firstGrip = await firstPane.locator(".pane-drag-handle").boundingBox();
    await page.mouse.move(firstGrip.x + firstGrip.width / 2, firstGrip.y + firstGrip.height / 2);
    await page.mouse.down();
    await page.mouse.move(firstGrip.x + 150, firstGrip.y + 110, { steps: 8 });
    await page.mouse.up();
    const firstMoved = await firstPane.boundingBox();
    if (firstMoved.x <= firstInitial.x || firstMoved.y <= firstInitial.y || firstMoved.width >= firstInitial.width) {
      throw new Error(`The initial full-size pane did not restore and move (${JSON.stringify({ firstInitial, firstMoved })})`);
    }

    const resizeHandle = await firstPane.locator(".resize-se").boundingBox();
    await page.mouse.move(resizeHandle.x + resizeHandle.width / 2, resizeHandle.y + resizeHandle.height / 2);
    await page.mouse.down();
    await page.mouse.move(resizeHandle.x - 60, resizeHandle.y - 40, { steps: 6 });
    await page.mouse.up();
    const firstResized = await firstPane.boundingBox();
    if (firstResized.width >= firstMoved.width || firstResized.height >= firstMoved.height) {
      throw new Error(`The lower-right resize handle did not resize the pane (${JSON.stringify({ firstMoved, firstResized })})`);
    }

    const workspaceBeforeViewportResize = await page.locator(".panes").boundingBox();
    await page.setViewportSize({ width: 1080, height: 680 });
    await page.waitForTimeout(100);
    const workspaceAfterViewportResize = await page.locator(".panes").boundingBox();
    const firstScaled = await firstPane.boundingBox();
    const expectedScaledWidth = firstResized.width * workspaceAfterViewportResize.width / workspaceBeforeViewportResize.width;
    if (Math.abs(firstScaled.width - expectedScaledWidth) > 5 || firstScaled.width >= firstResized.width) {
      throw new Error(`A free pane did not scale with its workspace (${JSON.stringify({ firstResized, firstScaled })})`);
    }
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.waitForTimeout(100);

    for (let count = 0; count < 4; count += 1) await page.locator("#addPaneButton").click();
    if (await panes.count() !== 5) throw new Error("Workspace still limits the number of panes");
    if (await page.locator("#addPaneButton").isDisabled()) throw new Error("Add Pane became disabled");

    const fifth = panes.nth(4);
    const initial = await fifth.boundingBox();
    const handle = fifth.locator(".pane-drag-handle");
    await handle.dragTo(page.locator(".panes"), { targetPosition: { x: 6, y: 6 } });
    const snapped = await fifth.boundingBox();
    const workspace = await page.locator(".panes").boundingBox();
    if (Math.abs(snapped.x - workspace.x) > 3 || Math.abs(snapped.width - workspace.width / 2) > 5) {
      throw new Error("Dragging to a workspace corner did not snap the pane");
    }
    if (initial.width === snapped.width && initial.height === snapped.height) throw new Error("Pane geometry did not change");

    const snappedResizeHandle = await fifth.locator(".resize-se").boundingBox();
    await page.mouse.move(snappedResizeHandle.x + snappedResizeHandle.width / 2, snappedResizeHandle.y + snappedResizeHandle.height / 2);
    await page.mouse.down();
    await page.mouse.move(snappedResizeHandle.x - 50, snappedResizeHandle.y - 40, { steps: 6 });
    await page.mouse.up();
    const resizedFromSnap = await fifth.boundingBox();
    if (Math.abs(resizedFromSnap.x - snapped.x) > 3 || Math.abs(resizedFromSnap.y - snapped.y) > 3
        || resizedFromSnap.width >= snapped.width || resizedFromSnap.height >= snapped.height) {
      throw new Error(`Resizing a snapped pane restored its old geometry (${JSON.stringify({ snapped, resizedFromSnap })})`);
    }

    await fifth.locator(".minimize-pane").click();
    if (!await fifth.isHidden()) throw new Error("Minimising a pane did not hide its window");
    const taskButton = page.locator("#paneTaskbar [data-restore-pane='4']");
    if (!await taskButton.isVisible()) throw new Error("Minimised pane was not added to the workspace shelf");
    await taskButton.click();
    if (!await fifth.isVisible()) throw new Error("Pane did not restore from the workspace shelf");

    await panes.nth(0).locator(".pane-drag-handle").focus();
    const firstZ = Number(await panes.nth(0).evaluate(element => getComputedStyle(element).zIndex));
    const fifthZ = Number(await fifth.evaluate(element => getComputedStyle(element).zIndex));
    if (firstZ <= fifthZ) throw new Error(`Selecting a stacked pane did not bring it to the front (${firstZ} <= ${fifthZ})`);

    await fifth.locator(".pane-drag-handle").focus();
    await fifth.locator(".close-empty-pane").click();
    if (await panes.count() !== 4) throw new Error("Closing an empty pane failed");

    await panes.nth(3).locator(".pane-drag-handle").focus();
    await panes.nth(3).locator(".minimize-pane").click();
    await page.reload({ waitUntil: "networkidle" });
    if (await page.locator(".pane").count() !== 4) throw new Error("Workspace window count was not restored");
    if (await page.locator(".pane").nth(3).isVisible()) throw new Error("Minimised state was not restored");
    await page.locator("#paneTaskbar [data-restore-pane='3']").click();
    console.log("Workspace window browser regression passed");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
