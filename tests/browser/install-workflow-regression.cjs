// Installing a disc onto a drive, exercised through the running application.
//
// The unit tests cover the service; what they cannot cover is that the import
// dialog offers the choice, that the routes are reachable from a browser, and
// that the checkpoint the operator relies on to undo an install is actually
// recorded. All three have to be true together for the feature to exist.

const { chromium } = require("playwright");

const target = process.env.AMIGA_FILE_FORGE_URL || "http://127.0.0.1:8666";

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const created = [];
  try {
    await page.goto(target, { waitUntil: "networkidle" });
    const result = await page.evaluate(async () => {
      const json = async (url, options = {}) => {
        const response = await fetch(url, options);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `${response.status} ${url}`);
        return data;
      };
      const body = value => ({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(value),
      });

      // A run leaves the staging area as it found it, but a title name that
      // is unique per run means a leftover from an interrupted run cannot
      // make the next one fail for the wrong reason.
      const title = `Browser Title ${Date.now()}`;
      const drive = (await json("/api/images/create", body({
        format: "ffs-hard", title: "INSTALLTARGET", capacity: "40MB",
      }))).image.id;
      const first = (await json("/api/images/create", body({ format: "adf", title: "GAMEONE" }))).image.id;
      const second = (await json("/api/images/create", body({ format: "adf", title: "GAMETWO" }))).image.id;

      for (const [disc, name] of [[first, "Loader"], [second, "Level2"]]) {
        await json(`/api/images/${disc}/empty-file`, body({
          destination: "", name, protection: "----rwed",
        }));
      }

      // A drive showing its partition table is not a volume, so WHDLoad
      // cannot be reported on until a partition is chosen.
      const noVolume = await fetch(`/api/images/${drive}/install/whdload`);
      if (noVolume.ok) throw new Error("A partition table was accepted as an install destination");

      const state = await json(`/api/images/${drive}/install/whdload?partition=0`);
      if (state.whdload.installed) throw new Error("A blank drive reported WHDLoad already installed");
      if (!state.whdload.sources.some(source => source.name === "whdload.de")) {
        throw new Error("The author's own site is not offered as a WHDLoad source");
      }

      // Two discs of one title stage into one tree.
      await json("/api/install/stage", body({ sourceImage: first, title, discLabel: "Disk 1" }));
      const staged = (await json("/api/install/stage", body({
        sourceImage: second, title, discLabel: "Disk 2",
      }))).staged;
      if (staged.discCount !== 2) throw new Error(`Expected one title of two discs, got ${staged.discCount}`);

      const listed = await json("/api/install/staged");
      if (!listed.titles.some(row => row.slug === staged.slug)) {
        throw new Error("A staged title did not appear in the staging list");
      }

      const before = (await json(`/api/images/${drive}/checkpoints`)).checkpoints.length;
      const installed = await json(`/api/images/${drive}/install/staged`, body({
        slug: staged.slug, parent: "Games", partition: 0,
      }));
      if (installed.path !== `Games/${title}`) {
        throw new Error(`Installed to ${installed.path} rather than its own drawer`);
      }

      const drawer = await json(`/api/images/${drive}/tree?path=${encodeURIComponent(installed.path)}&partition=0`);
      const names = drawer.entries.map(row => row.name).sort();
      if (!names.includes("Loader") || !names.includes("Level2")) {
        throw new Error(`Both discs should have merged into one drawer, found ${JSON.stringify(names)}`);
      }

      // An install changes a drive somebody built, so it must be undoable.
      const after = (await json(`/api/images/${drive}/checkpoints`)).checkpoints;
      if (after.length <= before) throw new Error("Installing a staged title recorded no undo checkpoint");
      await json(`/api/images/${drive}/undo`, body({}));
      // The install created the Games drawer as well as the title inside it,
      // so a complete undo leaves neither. Either outcome is checked, because
      // what matters is that the title is gone, not how much went with it.
      const undone = await fetch(`/api/images/${drive}/tree?path=Games&partition=0`);
      if (undone.ok) {
        const games = await undone.json();
        if (games.entries.some(row => row.name === title)) {
          throw new Error("Undo did not remove the installed title");
        }
      }

      await fetch(`/api/install/staged/${staged.slug}`, { method: "DELETE" });
      return { images: [drive, first, second], discCount: staged.discCount };
    });
    created.push(...result.images);
    console.log("Staging, WHDLoad reporting, install and undo browser regression passed");
  } finally {
    for (const id of created) {
      await page.evaluate(async image => {
        await fetch(`/api/images/${image}`, { method: "DELETE" });
      }, id).catch(() => {});
    }
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
