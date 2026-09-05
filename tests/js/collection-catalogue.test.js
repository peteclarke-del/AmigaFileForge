"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const context = vm.createContext({ window: {} });
const source = fs.readFileSync(path.join(__dirname, "../../app/static/collection-catalogue.js"), "utf8");
vm.runInContext(source, context, { filename: "collection-catalogue.js" });
const catalogue = context.window.AmigaCollectionCatalogue;

function test(name, callback) {
  try { callback(); process.stdout.write(`ok - ${name}\n`); }
  catch (error) { process.stderr.write(`not ok - ${name}\n${error.stack}\n`); process.exitCode = 1; }
}

const manifest = (name, hash, title) => ({
  image: { id: `session-${name}`, name, kind: "hdf", title, size: 100 },
  fingerprint: hash.repeat(64),
  revision: `100-${hash}`,
  records: [
    { recordType: "partition", partition: 0, device: "DH0", title, path: "DH0:" },
    { recordType: "file", partition: 0, path: "Game", size: 4, sha256: hash.repeat(64) },
  ],
  menus: [],
});

test("collection entries retain deterministic identity and decoded titles", () => {
  const entry = catalogue.catalogueEntry(
    manifest("games.hdf", "a", "Arcadians"),
    { location: "SD card 1", machines: ["Amiga 500"] },
    null,
    () => "2026-08-17T12:00:00Z",
    () => "entry-1",
  );
  assert.equal(entry.id, "entry-1");
  assert.equal(entry.location, "SD card 1");
  assert.equal(entry.titles[0].key, "arcadians");
  assert.equal(entry.stale, false);
});

test("collection reports span closed-image manifests by hash and title", () => {
  const first = catalogue.catalogueEntry(manifest("one.hdf", "b", "Repton 2"), {}, null, () => "now", () => "one");
  const second = catalogue.catalogueEntry(manifest("two.hdf", "b", "REPTON-2"), {}, null, () => "now", () => "two");
  const report = catalogue.collectionReport([first, second], ["Repton 2", "Elite"]);
  assert.equal(report.exactDuplicates.length, 1);
  assert.equal(report.titleVariants.length, 1);
  assert.deepEqual(Array.from(report.missingTitles), ["Elite"]);
});

test("collection backup validation rejects unversioned input", () => {
  assert.throws(() => catalogue.validateBackup({ images: [] }), /version 1/);
  assert.equal(catalogue.validateBackup({
    format: catalogue.BACKUP_FORMAT,
    version: catalogue.BACKUP_VERSION,
    images: [{ id: "one", records: [], menus: [] }],
  }).images.length, 1);
});

(async () => {
  let document = { images: [], settings: { key: "preferences", wanted: [] } };
  const remote = catalogue.createRemote({
    load: async () => structuredClone(document),
    save: async value => { document = structuredClone(value); },
    now: () => "2026-08-24T12:00:00Z",
    uuid: () => "remote-one",
  });
  await remote.upsertManifest(manifest("desktop.hdf", "c", "Chuckie Egg"), { sessionId: "desktop-session" });
  await remote.saveSettings({ wanted: ["Elite"] });
  assert.equal((await remote.list())[0].id, "remote-one");
  assert.deepEqual(Array.from((await remote.settings()).wanted), ["Elite"]);
  process.stdout.write("ok - remote collection adapter persists through its host store\n");
})().catch(error => {
  process.stderr.write(`not ok - remote collection adapter persists through its host store\n${error.stack}\n`);
  process.exitCode = 1;
});
