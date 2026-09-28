const { chromium } = require("playwright");

const target = process.env.AMIGA_FILE_FORGE_URL || "http://127.0.0.1:8666";

// The partition editor, from an empty pane to a drive of 128 GB with its
// partitions changed afterwards. Every dialog sets its focus a moment after
// it opens, so each step waits for that before it types.
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1360, height: 1000 } });
  const problems = [];
  page.on("pageerror", error => problems.push(error.message));
  let imageId = null;
  const settle = () => page.waitForTimeout(450);
  const expect = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  try {
    await page.goto(target, { waitUntil: "networkidle" });
    const opener = (await page.locator(".pane-new").count()) ? ".pane-new" : ".pane .new-image";
    await page.locator(opener).first().click();
    await page.waitForSelector('select[name="format"]');
    await settle();
    await page.selectOption('select[name="format"]', "ffs-hard");
    await settle();

    const create = page.locator('button[value="create"]');
    expect(await create.isDisabled(), "A drive with no partitions must not be creatable.");
    expect(
      !(await page.inputValue('select[name="layoutPreset"]')),
      "No starting layout may be chosen for the user.",
    );
    expect(
      !(await page.inputValue('select[name="layoutLargeFilesystem"]')),
      "No filing system may be chosen for the user.",
    );

    await page.fill('input[name="capacity"]', "128GB");
    await page.selectOption('select[name="layoutLargeFilesystem"]', "pfs3");
    await page.selectOption('select[name="layoutPreset"]', "system-work");
    await page.waitForSelector('tr[data-layout-row="1"]');
    await page.waitForFunction(() => !document.querySelector('button[value="create"]').disabled);
    expect(
      (await page.locator(".drive-map-part").count()) === 3,
      "The map shows the table and both partitions.",
    );

    // FFS is kept to a size it copes with, and says what to use instead.
    await page.locator('tr[data-layout-row="1"] select[data-field="filesystem"]').selectOption("ffs-intl");
    await page.waitForSelector("[data-layout-messages] .help-warning");
    expect(await create.isDisabled(), "A 126 GB FFS partition must be refused.");
    const refusal = await page.locator("[data-layout-messages] .help-warning").first().innerText();
    expect(/Professional File System/.test(refusal), `The refusal must name PFS3: ${refusal}`);

    // A filing system with no handler can be supplied from where it is needed.
    await page.locator('tr[data-layout-row="1"] select[data-field="filesystem"]').selectOption("sfs");
    await page.waitForSelector("[data-supply-handler]");
    expect(await create.isDisabled(), "An SFS partition needs its handler first.");

    await page.locator('tr[data-layout-row="1"] select[data-field="filesystem"]').selectOption("pds3");
    await page.waitForFunction(() => !document.querySelector('button[value="create"]').disabled);
    await page.fill('input[name="title"]', "Browser128");
    await create.click();
    await page.waitForSelector(".partition-list", { timeout: 30000 });
    await settle();

    const pane = page.locator(".pane", { hasText: "Browser128.hdf" }).first();
    imageId = await page.evaluate(async () => {
      const response = await fetch("/api/images/recoverable");
      const data = await response.json();
      return data.images.find(image => image.name === "Browser128.hdf")?.id || null;
    });
    const rows = await pane.locator(".partition-list tbody tr").count();
    expect(rows === 2, `Expected two partitions in the pane, found ${rows}.`);

    // The partition manager: remove one, put another in its place.
    await pane.locator(".tool-menu summary", { hasText: "Tools" }).click();
    await pane.locator(".manage-partitions").click();
    await page.waitForSelector(".partition-manager");
    await settle();
    await page.locator('[data-partition-remove="1"]').click();
    await page.waitForSelector('[data-choice="confirm"]');
    await page.locator('[data-choice="confirm"]').click();
    await page.waitForSelector(".partition-manager .drive-layout-free", { timeout: 20000 });
    await settle();

    await page.locator("[data-partition-add]").first().click();
    await page.waitForSelector('input[name="size"]');
    await settle();
    await page.fill('input[name="name"]', "DH1");
    await page.fill('input[name="label"]', "Games");
    await page.selectOption('select[name="filesystem"]', "pfs3");
    await page.fill('input[name="size"]', "40GB");
    await page.locator('button[value="add"]').click();
    await page.waitForSelector(".partition-manager .drive-layout-free", { timeout: 30000 });
    await settle();
    const table = await page.locator(".partition-manager .drive-layout-table").innerText();
    expect(/DH1/.test(table) && /40\.0 GB/.test(table), `The new partition is missing: ${table}`);

    const layout = await page.evaluate(async id => {
      const response = await fetch(`/api/images/${id}/drive-layout`);
      return response.json();
    }, imageId);
    expect(layout.layout.partitions.length === 2, "The drive has two partitions again.");
    expect(layout.layout.missingHandlers.length === 0, "Every partition has its handler.");
    expect(layout.image.size === 128 * 1024 ** 3, "The image is the size of the drive.");
    expect(problems.length === 0, `The page reported errors: ${problems.join("; ")}`);
    console.log("Drive layout regression passed.");
  } finally {
    if (imageId) {
      await page.evaluate(id => fetch(`/api/images/${id}`, { method: "DELETE" }), imageId).catch(() => {});
    }
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
