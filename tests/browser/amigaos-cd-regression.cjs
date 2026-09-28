// Installing AmigaOS 3.9 from its CD, through the dialog a person uses.
//
// The unit tests cover the installation. What they cannot cover is that the
// dialog takes a disc and a pack from the file chooser, says what it is about
// to install, and leaves the pane showing a drive with the system on it.
//
// The disc and the pack are built by the same fixture the unit tests use,
// because the real ones are a commercial CD and an archive nobody can commit.
// The pack used here is the plain one, so no emulator is needed to apply it.

const { chromium } = require("playwright");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const target = process.env.AMIGA_FILE_FORGE_URL || "http://127.0.0.1:8666";
const root = path.resolve(__dirname, "..", "..");

function fixtures() {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "amigaos-cd-"));
  execFileSync(process.env.PYTHON || "python3", ["-c", [
    "import sys",
    "from pathlib import Path",
    "from tests import amigaos_fixture as fixture",
    "folder = Path(sys.argv[1])",
    "fixture.release_disc(folder, name='AmigaOS39.iso')",
    "(folder / 'BB3-4.lha').write_bytes(fixture.lha_archive(fixture.boingbag_34()))",
    "(folder / 'OS39FAQ.lha').write_bytes(fixture.lha_archive({'Archives/faq.html': b'x'}))",
  ].join("\n"), folder], { cwd: root });
  return folder;
}

(async () => {
  const folder = fixtures();
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1360, height: 1000 } });
  const problems = [];
  page.on("pageerror", error => problems.push(error.message));
  let imageId = null;
  const settle = () => page.waitForTimeout(450);
  const expect = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  const choose = async (button, files) => {
    const [chooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      page.locator(button).click(),
    ]);
    await chooser.setFiles(files.map(name => path.join(folder, name)));
  };
  try {
    await page.goto(target, { waitUntil: "networkidle" });
    const opener = (await page.locator(".pane-new").count()) ? ".pane-new" : ".pane .new-image";
    await page.locator(opener).first().click();
    await page.waitForSelector('select[name="format"]');
    await settle();
    await page.selectOption('select[name="format"]', "ffs-hard");
    await settle();
    await page.fill('input[name="capacity"]', "64MB");
    await page.selectOption('select[name="layoutLargeFilesystem"]', "pfs3");
    await page.selectOption('select[name="layoutPreset"]', "single");
    await page.waitForFunction(() => !document.querySelector('button[value="create"]').disabled);
    await page.fill('input[name="title"]', "BrowserOS39");
    await page.locator('button[value="create"]').click();
    await page.waitForSelector(".partition-list", { timeout: 30000 });
    await settle();

    imageId = await page.evaluate(async () => {
      const data = await (await fetch("/api/images/recoverable")).json();
      return data.images.find(image => image.name === "BrowserOS39.hdf")?.id || null;
    });
    expect(imageId, "The drive was not created.");
    const profiled = await page.evaluate(async id => (await fetch(`/api/images/${id}/hardware-profile`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ machine: "a1200", addons: ["pistorm32"] }),
    })).status, imageId);
    expect(profiled === 200, `The hardware profile was refused: ${profiled}`);

    const pane = page.locator(".pane", { hasText: "BrowserOS39.hdf" }).first();
    await pane.locator(".partition-list tbody tr").first().dblclick();
    await page.waitForFunction(() => !document.querySelector(".pane .partition-list"));
    await settle();

    await pane.locator(".tool-menu summary", { hasText: "Tools" }).click();
    await pane.locator(".install-amigaos-cd").click();
    await page.waitForSelector("[data-choose-cd]");
    await settle();
    const install = page.locator("[data-install-cd]");
    expect(await install.isDisabled(), "Nothing can be installed before a disc is chosen.");

    await choose("[data-choose-cd]", ["AmigaOS39.iso"]);
    await page.waitForSelector(".install-layers", { timeout: 20000 });
    const said = await page.locator("[data-cd-summary]").innerText();
    expect(/The disc is fine/.test(said) && /AmigaOS 3\.9/.test(said), `The disc line: ${said}`);
    const layers = await page.locator(".install-layers tbody tr").count();
    expect(layers === 9, `Nine layers are listed before installing, found ${layers}.`);
    const plan = await page.locator("[data-cd-preflight]").innerText();
    expect(/Prefs\/Presets\/Backdrops/.test(plan), "The backdrops destination is shown.");
    expect(!(await install.isDisabled()), "A ready drive can be installed onto.");
    expect(
      await page.locator("[data-boot-cd]").isEnabled(),
      "A disc carrying an emergency system can start the machine for its own installer.",
    );

    await choose("[data-choose-packs]", ["BB3-4.lha", "OS39FAQ.lha"]);
    await page.waitForSelector("[data-pack]", { timeout: 20000 });
    const packs = await page.locator("[data-pack-summary]").innerText();
    expect(/1 update pack found/.test(packs), `The pack line: ${packs}`);
    expect(/OS39FAQ\.lha/.test(packs), "An archive that is not a pack is named.");
    expect(await page.locator('[data-pack][value="3.9-34"]').isChecked(), "The pack is ticked.");

    await install.click();
    await page.waitForFunction(() => !document.querySelector("dialog[open] [data-install-cd]"), null, {
      timeout: 60000,
    });
    await settle();

    const written = await page.evaluate(async id => {
      const listing = await (await fetch(`/api/images/${id}/tree?path=C&partition=0`)).json();
      const points = await (await fetch(`/api/images/${id}/checkpoints`)).json();
      return {
        commands: (listing.entries || []).map(entry => entry.name),
        undo: (points.checkpoints || []).map(point => point.reason),
      };
    }, imageId);
    expect(written.commands.includes("SetPatch"), `C: holds ${written.commands}`);
    expect(written.commands.includes("AddBuffers"), "The base system is installed.");
    expect(
      written.undo.includes("installing AmigaOS from a release CD"),
      `The install can be undone: ${written.undo}`,
    );
    const shown = await pane.innerText();
    expect(/Libs/.test(shown) && /Prefs/.test(shown), "The pane shows the installed system.");
    expect(!problems.length, `The page reported errors: ${problems.join("; ")}`);
    console.log("AmigaOS CD install dialog: passed");
  } catch (error) {
    await page.screenshot({ path: path.join(os.tmpdir(), "amigaos-cd-regression.png") }).catch(() => {});
    console.error(error.message);
    process.exitCode = 1;
  } finally {
    if (imageId) {
      await page.evaluate(id => fetch(`/api/images/${id}`, { method: "DELETE" }), imageId).catch(() => {});
    }
    await browser.close();
    fs.rmSync(folder, { recursive: true, force: true });
  }
})();
