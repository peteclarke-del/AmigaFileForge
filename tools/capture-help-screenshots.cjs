// Regenerate the handbook screenshots for the install workflow.
//
// The images under app/static/help are checked in, because the handbook is
// served from the application itself and cannot go and take its own pictures.
// That makes them the part of the documentation most likely to fall behind the
// interface, so the way they were produced is kept here rather than in
// somebody's shell history.
//
// The existing images are 2800x1760, which is a 1400x880 window at two device
// pixels per CSS pixel. Anything captured at another size looks wrong beside
// them, so the size lives in one place below.
//
//   AMIGA_WORKBENCH_DISCS=/path/to/adfs \
//     node tools/capture-help-screenshots.cjs
//
// The application has to be running; AMIGA_FILE_FORGE_URL points at it and
// defaults to the port the container publishes. The Workbench install shot
// needs a folder of AmigaOS floppy images, which is what AMIGA_WORKBENCH_DISCS
// names. Without it that shot is skipped rather than taken empty, because a
// picture of the dialog with nothing chosen teaches the reader nothing about
// the part that matters.

const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const VIEWPORT = { width: 1400, height: 880 };
const SCALE = 2;
const OUTPUT = path.join(__dirname, "..", "app", "static", "help");
const target = process.env.AMIGA_FILE_FORGE_URL || "http://127.0.0.1:8666";
const discs = process.env.AMIGA_WORKBENCH_DISCS || "";

const wait = (page, ms) => page.waitForTimeout(ms);

async function openPaneMenu(page, label) {
  await page.locator(".pane .tool-menu > summary", { hasText: label }).first().click();
  await wait(page, 250);
}

async function newHardDrive(page, title) {
  await page.click(".empty-pane .pane-new");
  await wait(page, 800);
  await page.selectOption('#modal select[name="format"]', "ffs-hard");
  await page.fill('#modal input[name="title"]', title);
  await page.click('#modal button[value="create"]');
  await wait(page, 4000);
  // Open the partition: a drive showing its partition table is not a volume,
  // and none of the install commands apply to one.
  await page.evaluate(() => {
    const row = [...document.querySelectorAll(".pane .file-row")].find(r => /DH0/.test(r.textContent));
    row?.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
  });
  await wait(page, 1500);
}

async function captureWorkbenchInstall(page) {
  if (!discs) {
    console.log("skipped workbench-install.png: set AMIGA_WORKBENCH_DISCS to a folder of ADFs");
    return;
  }
  const files = fs.readdirSync(discs)
    .filter(name => /\.(adf|adz|dms|hfe)$/i.test(name))
    .map(name => path.join(discs, name));
  if (!files.length) throw new Error(`No disc images in ${discs}`);

  await newHardDrive(page, "SYSTEM");
  await openPaneMenu(page, "Tools");
  await page.click(".pane .install-workbench");
  await wait(page, 900);
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser"),
    page.click("#modal [data-choose-files]"),
  ]);
  await chooser.setFiles(files);
  for (let attempt = 0; attempt < 60; attempt += 1) {
    await wait(page, 700);
    if (await page.evaluate(() => !document.querySelector("[data-workbench-survey]")?.hidden)) break;
  }
  await page.screenshot({ path: path.join(OUTPUT, "workbench-install.png") });
  console.log("wrote workbench-install.png");

  // Finish the install, so the drive the staging shot is taken on is a
  // prepared one rather than a blank partition.
  await page.click("#modal [data-install-workbench]");
  for (let attempt = 0; attempt < 120; attempt += 1) {
    await wait(page, 1000);
    if (!(await page.evaluate(() => document.querySelector("#modal").open))) break;
  }
  await wait(page, 2000);
  return files;
}

// Stage one disc so the staged list has something in it. A picture of an
// empty list would not show the reader what the dialog is for.
async function captureStagedInstallations(page, disc) {
  await openPaneMenu(page, "File");
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser"),
    page.click(".pane .import-file"),
  ]);
  await chooser.setFiles([disc]);
  await wait(page, 4000);
  if (await page.$('#modal button[name="action"][value="continue"]')) {
    await page.click('#modal button[name="action"][value="continue"]');
    await wait(page, 3500);
  }
  await page.selectOption('#modal select[name="storageMethod"]', "install");
  await wait(page, 700);
  await page.fill('#modal input[name="installTitle"]', "Hyper Sports");
  await page.fill('#modal input[name="discLabel"]', "Disk 1");
  // Stage it without installing, which is what the staged list is about.
  await page.evaluate(() => {
    const now = document.querySelector('input[name="installNow"]');
    if (now) now.checked = false;
  });
  await page.click('#modal button.primary[value="continue"]');
  for (let attempt = 0; attempt < 60; attempt += 1) {
    await wait(page, 800);
    if (!(await page.evaluate(() => document.querySelector("#modal").open))) break;
  }
  await wait(page, 1500);

  await openPaneMenu(page, "Tools");
  await page.click(".pane .staged-installations");
  await wait(page, 2000);
  await page.screenshot({ path: path.join(OUTPUT, "staged-installations.png") });
  console.log("wrote staged-installations.png");
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: SCALE });
  page.on("pageerror", error => console.error("page error:", error.message));
  try {
    // The application polls while it is idle, so "networkidle" never arrives.
    await page.goto(target, { waitUntil: "domcontentloaded" });
    await wait(page, 1500);
    const installed = await captureWorkbenchInstall(page);
    if (installed) {
      // Any disc that is not part of an AmigaOS release will do; the first
      // one the survey ignored is by definition one of those.
      const spare = installed.find(name => /hyper|game|title/i.test(path.basename(name)))
        || installed[installed.length - 1];
      await captureStagedInstallations(page, spare);
    }
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
