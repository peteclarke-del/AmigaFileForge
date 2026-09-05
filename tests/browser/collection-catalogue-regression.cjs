const { chromium } = require("playwright");

const target = process.env.AMIGA_FILE_FORGE_URL || "http://127.0.0.1:8666";

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1500, height: 900 } });
  let page = await context.newPage();
  let imageId = null;
  try {
    await page.goto(target, { waitUntil: "networkidle" });
    imageId = await page.evaluate(async () => {
      await new Promise((resolve, reject) => {
        const request = indexedDB.deleteDatabase("amiga-file-forge-private-collection-v1");
        request.onsuccess = resolve;
        request.onerror = () => reject(request.error);
      });
      const data = await window.AmigaUI.api("/api/images/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ format: "adf", title: "COLLECT" }),
      });
      localStorage.setItem("amiga-file-forge-dynamic-panes", JSON.stringify([{
        imageId: data.image.id,
        slot: null,
        side: null,
        path: "$",
        windowState: { x: 20, y: 20, width: 1200, height: 720, z: 1, minimized: false, snap: "", restore: null },
      }]));
      return data.image.id;
    });
    await page.close();
    page = await context.newPage();
    await page.goto(target, { waitUntil: "domcontentloaded" });
    try {
      await page.waitForFunction(() => document.querySelector(".pane .image-title")?.textContent.includes("blank.adf"));
    } catch (error) {
      const state = await page.evaluate(() => ({ saved: localStorage.getItem("amiga-file-forge-dynamic-panes"), titles: [...document.querySelectorAll(".pane .image-title")].map(item => item.textContent), text: document.body.innerText.slice(0, 800) }));
      throw new Error(`Collection fixture pane was not restored: ${JSON.stringify(state)} · ${error.message}`);
    }
    await page.locator("#collectionButton").click();
    await page.locator('[name="collectionLocation"]').fill("Workbench SD card");
    await page.locator('[name="collectionMachines"]').fill("Amiga B, Amiga 600");
    await page.locator("[data-index-pane]").click();
    await page.locator('.collection-list tr[data-collection-id]').waitFor({ state: "visible" });
    const row = page.locator('.collection-list tr[data-collection-id]').first();
    if (!await row.textContent().then(value => value.includes("Workbench SD card") && value.includes("Amiga B"))) {
      throw new Error("Indexed collection metadata was not rendered");
    }
    await page.locator('#modalContent button[value="cancel"]').click();
    await page.locator("#collectionButton").click();
    if (await page.locator('.collection-list tr[data-collection-id]').count() !== 1) {
      throw new Error("The private collection did not persist in IndexedDB");
    }
    const stored = await page.evaluate(async () => new Promise((resolve, reject) => {
      const request = indexedDB.open("amiga-file-forge-private-collection-v1");
      request.onsuccess = () => {
        const images = request.result.transaction("images").objectStore("images").getAll();
        images.onsuccess = () => resolve(images.result);
        images.onerror = () => reject(images.error);
      };
      request.onerror = () => reject(request.error);
    }));
    if (stored.length !== 1 || !stored[0].fingerprint || stored[0].sessionId !== imageId) {
      throw new Error(`Stored collection identity is incomplete: ${JSON.stringify(stored)}`);
    }
    console.log("Private collection browser regression passed");
  } finally {
    if (imageId) await page.evaluate(async id => fetch(`/api/images/${id}`, { method: "DELETE" }), imageId).catch(() => {});
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
