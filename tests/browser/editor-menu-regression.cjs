const { chromium } = require("playwright");

const target = process.env.AMIGA_FILE_FORGE_URL || "http://127.0.0.1:8666";

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  try {
    await page.goto(target, { waitUntil: "networkidle" });
    await page.evaluate(() => {
      const fixture = document.createElement("section");
      fixture.id = "editor-menu-regression";
      fixture.innerHTML = `<details class="editor-menu" id="menu-file"><summary>File</summary><div class="editor-menu-panel"><button type="button">Save</button></div></details><details class="editor-menu" id="menu-edit"><summary>Edit</summary><div class="editor-menu-panel"><button type="button">Copy</button></div></details>`;
      document.body.append(fixture);
      window.AmigaEditorTestHooks.installEditorMenuDismissal(fixture);
    });
    await page.locator("#menu-file summary").click();
    if (!await page.locator("#menu-file").evaluate(element => element.open)) throw new Error("File menu did not open");
    await page.locator("#menu-edit").hover();
    if (await page.locator("#menu-file").evaluate(element => element.open)) throw new Error("Previous editor menu remained open");
    if (!await page.locator("#menu-edit").evaluate(element => element.open)) throw new Error("Hover did not transfer the open menu");
    await page.locator("#menu-edit button").click();
    if (await page.locator("#menu-edit").evaluate(element => element.open)) throw new Error("Menu item did not close the menu");
    await page.locator("#menu-file summary").click();
    await page.locator("body").click({ position: { x: 8, y: 8 } });
    if (await page.locator("#menu-file").evaluate(element => element.open)) throw new Error("Outside click did not close the menu");
    console.log("Editor menu browser regression passed");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
