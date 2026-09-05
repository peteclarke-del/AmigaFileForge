const { chromium } = require("playwright");

const target = process.env.AMIGA_FILE_FORGE_URL || "http://127.0.0.1:8666";

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1400, height: 850 } });
  let page = await context.newPage();
  let imageId = null;
  try {
    await page.goto(target, { waitUntil: "networkidle" });
    imageId = await page.evaluate(async () => {
      const created = await window.AmigaUI.api("/api/images/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ format: "adf", title: "CHEATS" }),
      });
      const form = new FormData();
      // A 68000 lives counter: initialise a byte to three, decrement it, and
      // branch when it reaches zero. That is the shape the analyser looks for
      // -- a store that initialises a target, a decrement of the same target,
      // and a forward branch out of it.
      //   MOVE.B #3,$1234.w   11 FC 00 03 12 34
      //   SUBQ.B #1,$1234.w   53 38 12 34
      //   BEQ.B  +6           67 06
      //   NOP NOP RTS         4E 71 4E 71 4E 75
      form.append("file", new File([new Uint8Array([
        0x11, 0xFC, 0x00, 0x03, 0x12, 0x34,
        0x53, 0x38, 0x12, 0x34,
        0x67, 0x06,
        0x4E, 0x71, 0x4E, 0x71, 0x4E, 0x75,
      ])], "GAME", { type: "application/octet-stream" }));
      form.append("destination", "");
      form.append("targetName", "GAME");
      form.append("protection", "----rwed");
      const inserted = await fetch(`/api/images/${created.image.id}/files`, { method: "POST", body: form });
      if (!inserted.ok) throw new Error((await inserted.json()).error || "Could not create cheat-analysis fixture");
      localStorage.setItem("amiga-file-forge-dynamic-panes", JSON.stringify([{
        imageId: created.image.id,
        slot: null,
        side: null,
        path: "",
        windowState: { x: 20, y: 20, width: 1120, height: 700, z: 1, minimized: false, snap: "", restore: null },
      }]));
      return created.image.id;
    });
    await page.close();
    page = await context.newPage();
    await page.goto(target, { waitUntil: "domcontentloaded" });
    const pane = page.locator(".pane").first();
    await pane.locator('tr[data-name="GAME"]').waitFor({ state: "visible" });
    await pane.locator('tr[data-name="GAME"]').dblclick();
    const editor = page.locator("#modal.editor-window");
    await editor.waitFor({ state: "visible" });
    await editor.locator(".editor-menu summary", { hasText: "Tools" }).click();
    const command = editor.locator('[data-disassembly-action="cheat-candidates"]');
    await command.waitFor({ state: "visible" });
    await command.click();
    await editor.locator(".cheat-analysis-dialog").waitFor({ state: "visible" });
    const text = await editor.locator(".cheat-analysis-dialog").textContent();
    // The analyser reports this as Possible, not Strong, and that is the
    // honest answer: nothing in the file says the counter is the player's
    // lives rather than an object or animation count. What is asserted is
    // that the target was found, a confidence was rendered, and the safety
    // boundary is stated.
    if (!text.includes("Candidate evidence, not a proven cheat") || !text.includes("&1234") || !text.includes("Possible")) {
      throw new Error(`Cheat report omitted its evidence or safety boundary: ${text}`);
    }
    if (await editor.locator(".cheat-candidate-list .cheat-candidate").count() < 1) {
      throw new Error("No machine-code cheat candidate was rendered");
    }
    const sourceBounds = await editor.locator(".disassembly-source").boundingBox();
    const drawerBounds = await editor.locator(".code-intelligence-drawer-docked").boundingBox();
    if (!sourceBounds || !drawerBounds || drawerBounds.x <= sourceBounds.x || drawerBounds.height < sourceBounds.height * 0.75) {
      throw new Error(`Cheat report did not dock to the right at editor height: ${JSON.stringify({ sourceBounds, drawerBounds })}`);
    }
    const splitter = editor.locator(".code-editor-drawer-splitter");
    const splitterBounds = await splitter.boundingBox();
    if (!splitterBounds) throw new Error("The cheat-panel splitter is not visible");
    await page.mouse.move(splitterBounds.x + splitterBounds.width / 2, splitterBounds.y + splitterBounds.height / 2);
    await page.mouse.down();
    await page.mouse.move(splitterBounds.x - 60, splitterBounds.y + splitterBounds.height / 2, { steps: 4 });
    await page.mouse.up();
    const resizedDrawer = await editor.locator(".code-intelligence-drawer-docked").boundingBox();
    if (!resizedDrawer || resizedDrawer.width < drawerBounds.width + 40) {
      throw new Error(`Dragging the splitter did not enlarge the cheat panel: ${JSON.stringify({ drawerBounds, resizedDrawer })}`);
    }
    const firstCandidate = editor.locator(".cheat-candidate").first();
    const navigation = JSON.parse(await firstCandidate.getAttribute("data-cheat-navigation"));
    await firstCandidate.click();
    await editor.locator(`.disassembly-source-line.found[data-address="${navigation.address}"]`).waitFor({ state: "visible" });
    const prepare = editor.locator("[data-cheat-prove]");
    if (await prepare.isDisabled()) throw new Error("An exact-offset machine-code candidate did not enable guarded patch preparation");
    await prepare.click();
    const guarded = page.locator("#modal .guarded-cheat-patch-dialog");
    await guarded.waitFor({ state: "visible" });
    const guardedText = await guarded.textContent();
    if (!guardedText.includes("does not prove a cheat automatically") || !guardedText.includes("Exact source")) {
      throw new Error(`Guarded patch dialog omitted its proof boundary: ${guardedText}`);
    }
    await guarded.locator("[data-cheat-patch-cancel]").click();
    await editor.locator(".guarded-cheat-patch-dialog").waitFor({ state: "detached" });
    const bounds = await editor.boundingBox();
    if (!bounds || bounds.width > 1120 || bounds.height > 820) {
      throw new Error(`Cheat analysis dialog is oversized: ${JSON.stringify(bounds)}`);
    }
    if (process.env.CHEAT_ANALYSIS_SCREENSHOT) {
      await editor.screenshot({ path: process.env.CHEAT_ANALYSIS_SCREENSHOT });
    }
    console.log("Cheat analysis browser regression passed");
  } finally {
    if (imageId) await page.evaluate(async id => fetch(`/api/images/${id}`, { method: "DELETE" }), imageId).catch(() => {});
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
