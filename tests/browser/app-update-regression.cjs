// The About box's Check for Application Updates control, in a real browser.
//
// The live service is asked first, to show that opening the About box asks
// nothing of GitHub. After that the update routes are answered here, so each
// state the server can report is shown without the network or a package.
const { chromium } = require("playwright");

const target = process.env.AMIGA_FILE_FORGE_URL || "http://127.0.0.1:8666";
const APPLICATION = "Amiga File Forge";
const PAGE = "https://example.org/releases/tag/v9.0.0";

function state(phase, extra = {}) {
  return {
    phase,
    message: "",
    busy: ["checking", "downloading", "installing"].includes(phase),
    done: 0,
    total: null,
    release: null,
    application: APPLICATION,
    currentVersion: "0.4.0",
    system: "Debian 13 amd64",
    releasesPage: "https://example.org/releases",
    ...extra,
  };
}

function release(installable) {
  return { version: "9.0.0", tag: "v9.0.0", name: `${APPLICATION} 9.0.0`, pageUrl: PAGE, installable, packageName: "", packageSize: 1000 };
}

async function openAbout(page) {
  await page.locator("#helpMenu summary").click();
  await page.locator("#aboutButton").click();
  await page.waitForSelector("#modal[open] [data-app-update] .about-update-controls");
}

async function closeAbout(page) {
  await page.locator('#modalContent .modal-actions button[value="cancel"]').click();
  await page.waitForFunction(() => !document.querySelector("#modal").open);
}

async function statusText(page, wanted) {
  await page.waitForFunction(
    text => document.querySelector("[data-app-update] .about-update-status")?.textContent.includes(text),
    wanted,
    { timeout: 5000 },
  );
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const checks = [];
  page.on("request", request => {
    if (request.url().includes("/api/app-update/check")) checks.push(request.method());
  });
  try {
    await page.goto(target, { waitUntil: "networkidle" });

    // The live service: the button is there and nothing has been checked.
    await openAbout(page);
    await page.locator("[data-app-update] button", { hasText: "Check for Application Updates" }).waitFor();
    await page.waitForTimeout(300);
    if (checks.length) throw new Error("Opening the About box checked for an update without being asked");
    await closeAbout(page);

    // What the server holds, and what a check started now will find.
    let current = state("idle");
    let found = null;
    const posted = [];
    await page.route("**/api/app-update", route => route.fulfill({ json: current }));
    await page.route("**/api/app-update/check", route => {
      posted.push("check");
      current = found;
      route.fulfill({ json: state("checking", { message: "Asking GitHub for the newest version" }) });
    });
    await page.route("**/api/desktop/app-update/*", route => {
      const action = route.request().url().split("/").pop();
      posted.push(action);
      if (action === "install") {
        current = state("downloading", { release: release(true), done: 500, total: 1000 });
        route.fulfill({ json: state("downloading", { release: release(true), done: 0, total: 1000 }) });
      } else if (action === "cancel") {
        current = state("available", { release: release(true), message: "The update was cancelled." });
        route.fulfill({ json: current });
      } else {
        route.fulfill({ json: { restart: true } });
      }
    });

    // A check that cannot reach GitHub says so and offers the check again.
    found = state("failed", { message: "Could not check for a newer version: api.github.com did not answer in time." });
    await openAbout(page);
    await page.locator("[data-app-update] button", { hasText: "Check for Application Updates" }).click();
    await statusText(page, "Could not check for a newer version");
    if (!(await page.locator("[data-app-update] button", { hasText: "Check for Application Updates" }).isEnabled())) {
      throw new Error("A failed check did not offer the check again");
    }
    if (await page.locator("[data-app-update]").getByText("newest version").count()) {
      throw new Error("A failed check claimed this is the newest version");
    }

    // This copy cannot install the release, so its page is offered instead.
    found = state("available", {
      release: release(false),
      message: `${APPLICATION} 9.0.0 is available. You have version 0.4.0. This copy cannot update itself because it was not installed from a release package.`,
    });
    await page.locator("[data-app-update] button", { hasText: "Check for Application Updates" }).click();
    const pageLink = page.locator("[data-app-update] a.button", { hasText: "Open Release Page" });
    await pageLink.waitFor();
    if (await pageLink.getAttribute("href") !== PAGE) throw new Error("Open Release Page does not open the release");
    if (await pageLink.getAttribute("target") !== "_blank") throw new Error("The release page would replace the workbench");
    await statusText(page, "cannot update itself");
    await closeAbout(page);

    // An installable release asks first, then downloads with progress and Cancel.
    current = state("available", { release: release(true), message: `${APPLICATION} 9.0.0 is available. You have version 0.4.0.` });
    await openAbout(page);
    await page.locator("[data-app-update] a.about-update-page", { hasText: "Release page" }).waitFor();
    await page.locator("[data-app-update] button", { hasText: "Update to 9.0.0" }).click();
    const confirm = page.locator(".overlay-dialog", { hasText: `Update ${APPLICATION}?` });
    await confirm.waitFor();
    if (!(await confirm.textContent()).includes("Debian 13 amd64")) throw new Error("The question does not name the system");
    await confirm.locator("button", { hasText: "Download and Install" }).click();
    await page.waitForFunction(() => document.querySelector("[data-app-update] .progress.determinate")?.getAttribute("aria-valuenow") === "50", null, { timeout: 5000 });
    const size = await page.locator("[data-app-update] .about-update-size").textContent();
    if (!size.includes(" of ")) throw new Error(`The download does not show its size: ${size}`);
    await page.locator("[data-app-update] button", { hasText: "Cancel" }).click();
    await statusText(page, "The update was cancelled.");
    await page.locator("[data-app-update] button", { hasText: "Update to 9.0.0" }).waitFor();

    // Closing the About box does not lose a download; reopening shows it.
    current = state("downloading", { release: release(true), done: 250, total: 1000 });
    await closeAbout(page);
    await openAbout(page);
    await page.waitForFunction(() => document.querySelector("[data-app-update] .progress.determinate")?.getAttribute("aria-valuenow") === "25", null, { timeout: 5000 });

    // Installed: a restart is offered. A browser cannot restart the service,
    // so it says what to do instead.
    current = state("installed", { release: release(true), message: `${APPLICATION} 9.0.0 is installed. Restart ${APPLICATION} to use it.` });
    await page.locator("[data-app-update] button", { hasText: `Restart ${APPLICATION}` }).click();
    await statusText(page, "open it again");
    if (posted.includes("restart")) throw new Error("A page without the desktop host asked the server to restart");
    await closeAbout(page);

    for (const expected of ["check", "install", "cancel"]) {
      if (!posted.includes(expected)) throw new Error(`The page never sent ${expected}`);
    }
    console.log("Application update regression passed");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
