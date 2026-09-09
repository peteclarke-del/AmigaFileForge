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

      // Staging writes onto the drive being built, so each run gets its own
      // drive and its own title name: a leftover from an interrupted run
      // cannot make the next one fail for the wrong reason.
      const title = `Browser Title ${Date.now() % 100000}`;
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

      // Two discs of one title stage into one tree, on the drive itself.
      await json(`/api/images/${drive}/install/stage`, body({
        sourceImage: first, title, discLabel: "Disk 1", partition: 0,
      }));
      const staged = (await json(`/api/images/${drive}/install/stage`, body({
        sourceImage: second, title, discLabel: "Disk 2", partition: 0,
      }))).staged;
      if (staged.discCount !== 2) throw new Error(`Expected one title of two discs, got ${staged.discCount}`);

      // Staging has to land on the target image, not on the machine running
      // the application: the whole point is that the install can be finished
      // in an emulator or on real hardware, where a host directory is
      // unreachable.
      // Storage/Install rather than a plain Install drawer: a Workbench
      // install copies the AmigaOS Install disk to Install:, and staging into
      // the same place listed that disk's own drawers as staged titles.
      if (staged.path !== `Storage/Install/${title}`) {
        throw new Error(`Staged to ${staged.path} rather than a drawer on the drive`);
      }
      const stagedTree = await json(
        `/api/images/${drive}/tree?path=${encodeURIComponent(staged.path)}&partition=0`);
      const stagedNames = stagedTree.entries.map(row => row.name).sort();
      if (!stagedNames.includes("Loader") || !stagedNames.includes("Level2")) {
        throw new Error(`Both discs should be staged on the drive, found ${JSON.stringify(stagedNames)}`);
      }

      const listed = await json(`/api/images/${drive}/install/staged?partition=0`);
      if (!listed.titles.some(row => row.name === staged.name)) {
        throw new Error("A staged title did not appear in the drive's staging list");
      }

      const before = (await json(`/api/images/${drive}/checkpoints`)).checkpoints.length;
      const installed = await json(`/api/images/${drive}/install/staged`, body({
        name: staged.name, parent: "Games", partition: 0,
      }));
      if (installed.path !== `Games/${title}`) {
        throw new Error(`Installed to ${installed.path} rather than its own drawer`);
      }

      const drawer = await json(`/api/images/${drive}/tree?path=${encodeURIComponent(installed.path)}&partition=0`);
      const names = drawer.entries.map(row => row.name).sort();
      if (!names.includes("Loader") || !names.includes("Level2")) {
        throw new Error(`Both discs should have merged into one drawer, found ${JSON.stringify(names)}`);
      }

      // Installing empties the staging drawer, so nothing is counted twice.
      const remaining = await json(`/api/images/${drive}/install/staged?partition=0`);
      if (remaining.titles.some(row => row.name === staged.name)) {
        throw new Error("The staging drawer still held the title after it was installed");
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
      const restored = await json(`/api/images/${drive}/install/staged?partition=0`);
      if (!restored.titles.some(row => row.name === staged.name)) {
        throw new Error("Undo removed the install but did not put the staged discs back");
      }

      // Workbench is installed from the operator's own floppies, and the
      // disks are recognised by the volume name inside each image. A file
      // name that says nothing about its contents must not change the answer.
      const wbDisc = (await json("/api/images/create", body({ format: "adf", title: "Workbench3.1" }))).image.id;
      await json(`/api/images/${wbDisc}/empty-file`, body({ destination: "", name: "Shell" }));
      const survey = (await json(`/api/images/${drive}/install/workbench/survey`, body({
        discs: [wbDisc, first], partition: 0,
      }))).survey;
      if (survey.chosen.workbench !== wbDisc) {
        throw new Error("The Workbench disk was not recognised by its volume name");
      }
      if (!survey.unrecognised.length) {
        throw new Error("A disc that is not part of a release should not be claimed by a role");
      }
      if (survey.version !== "3.1") {
        throw new Error(`The release should be read from the volume name, got ${survey.version}`);
      }

      // The copy order is what makes a Workbench install correct: the
      // Workbench disk is copied first so that its full C: survives the
      // cut-down copy Extras carries. Which bytes win is asserted against
      // real file contents in tests/test_workbench_install.py; what this
      // level can show is that the order is right and that the second disc
      // left the shared name alone rather than writing over it.
      const extrasDisc = (await json("/api/images/create", body({ format: "adf", title: "Extras3.1" }))).image.id;
      for (const disc of [wbDisc, extrasDisc]) {
        await json(`/api/images/${disc}/empty-file`, body({ destination: "", name: "Dir" }));
      }
      const prepared = (await json("/api/images/create", body({
        format: "ffs-hard", title: "WBTARGET", capacity: "40MB",
      }))).image.id;
      const workbench = (await json(`/api/images/${prepared}/install/workbench`, body({
        discs: { workbench: wbDisc, extras: extrasDisc }, partition: 0, version: "3.1",
      }))).workbench;
      if (workbench.discs.map(row => row.role).join(",") !== "workbench,extras") {
        throw new Error(`Workbench must be copied before Extras, got ${JSON.stringify(workbench.discs.map(row => row.role))}`);
      }
      if (!workbench.discs[1].skipped) {
        throw new Error("Extras should have left the Workbench disk's shared file alone");
      }
      const installedRoot = await json(`/api/images/${prepared}/tree?partition=0`);
      const rootNames = installedRoot.entries.map(row => row.name);
      for (const drawer of ["T", "Trashcan", "Devs"]) {
        if (!rootNames.includes(drawer)) {
          throw new Error(`The install script's ${drawer} drawer was not created`);
        }
      }

      return {
        images: [drive, first, second, wbDisc, extrasDisc, prepared],
        discCount: staged.discCount,
      };
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
