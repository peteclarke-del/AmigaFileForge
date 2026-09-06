const { chromium } = require("playwright");

const target = process.env.AMIGA_FILE_FORGE_URL || "http://127.0.0.1:8666";

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  let imageId = null;
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

      // A partitioned drive: one Rigid Disk Block chaining to one FFS
      // International partition, which is what an Amiga expects to find.
      const created = await json(
        "/api/images/create",
        body({ format: "ffs-hard", title: "BROWSER", capacity: "20MB" }),
      );
      const id = created.image.id;

      // The drive opens on its partition table, not inside a volume.
      const table = await json(`/api/images/${id}/tree`);
      if (table.entries.length !== 1 || table.entries[0].name !== "DH0") {
        throw new Error(
          `Expected one partition named DH0, got ${JSON.stringify(table.entries.map(row => row.name))}`,
        );
      }
      if (table.entries[0].type !== "dir") {
        throw new Error("A partition must be presented as enterable");
      }

      // Writing goes into the volume the partition mounts.
      await json(`/api/images/${id}/empty-file`, body({
        partition: 0, destination: "", name: "TESTFILE", protection: "----rwed",
      }));
      const written = await json(`/api/images/${id}/tree?partition=0`);
      if (!written.entries.some(row => row.name === "TESTFILE")) {
        throw new Error("The new file was not written into the partition");
      }

      const checkpoints = await json(`/api/images/${id}/checkpoints`);
      if (!checkpoints.checkpoints.some(checkpoint => checkpoint.automatic)) {
        throw new Error("Writing into a partition did not create an automatic undo checkpoint");
      }

      await json(`/api/images/${id}/undo`, body({}));
      const undone = await json(`/api/images/${id}/tree?partition=0`);
      if (undone.entries.some(row => row.name === "TESTFILE")) {
        throw new Error("Undo did not remove the file from the partition");
      }

      // Exercise the same Web Crypto path used when the app is opened from a
      // Pi's plain-HTTP LAN address, where randomUUID is not exposed but
      // getRandomValues remains available.
      const operationId = AmigaIdentifiers.newUuid({
        getRandomValues: crypto.getRandomValues.bind(crypto),
      });
      await json(`/api/images/${id}/download/prepare`, body({ operationId }));
      const operation = await json(`/api/operations/${operationId}`);
      if (operation.operation.state !== "complete") throw new Error("Save preparation did not complete");
      const download = await fetch(`/api/images/${id}/download`);
      if (!download.ok || !String(download.headers.get("content-type")).includes("zip")) {
        throw new Error("Prepared image did not download as a ZIP archive");
      }
      return { id, bytes: (await download.arrayBuffer()).byteLength };
    });
    imageId = result.id;
    if (result.bytes < 1024) throw new Error("Downloaded image archive was unexpectedly small");
    console.log("Partitioned drive, checkpoint, undo, operation and save browser regression passed");
  } finally {
    if (imageId) {
      await page.evaluate(async id => { await fetch(`/api/images/${id}`, { method: "DELETE" }); }, imageId).catch(() => {});
    }
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
