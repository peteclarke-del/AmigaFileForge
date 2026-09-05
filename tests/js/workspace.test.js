"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function load(relativePath, exportName) {
  const context = vm.createContext({ window: {} });
  const source = fs.readFileSync(path.join(__dirname, "../..", relativePath), "utf8");
  vm.runInContext(source, context, { filename: relativePath });
  return context.window[exportName];
}

function test(name, callback) {
  try { callback(); process.stdout.write(`ok - ${name}\n`); }
  catch (error) { process.stderr.write(`not ok - ${name}\n${error.stack}\n`); process.exitCode = 1; }
}

const workspace = load("app/static/workspace.js", "AmigaWorkspace");
const visuals = load("app/static/file-visuals.js", "AmigaFileVisuals");
const imports = load("app/static/import-planning.js", "AmigaImportPlanning");
const metadata = load("app/static/amiga-metadata.js", "AmigaMetadata");
const help = load("app/static/help.js", "AmigaHelp");
const about = load("app/static/about.js", "AmigaAbout");
const editorWorkspace = load("app/static/editor-workspace.js", "AmigaEditorWorkspace");
const identifiers = load("app/static/identifiers.js", "AmigaIdentifiers");
const operationUI = load("app/static/operation-ui.js", "AmigaOperationUI");
const workspacePersistence = load("app/static/workspace-persistence.js", "AmigaWorkspacePersistence");
const paneWindows = load("app/static/pane-window-manager.js", "AmigaPaneWindowManager");
const paneView = load("app/static/pane-view.js", "AmigaPaneView");
const transferPlanning = load("app/static/transfer-planning.js", "AmigaTransferPlanning");

test("workspace pane state has one canonical initial shape", () => {
  const pane = workspace.newPaneState({ kind: "hdf", doubleSided: false });
  assert.equal(pane.path, "$");
  assert.equal(pane.menuDetectionPending, true);
  assert.deepEqual(Array.from(pane.selection), []);
  assert.equal(pane.windowState, null);
});

test("pane window geometry supports sides, corners and constrained free placement", () => {
  const bounds = { width: 1200, height: 800 };
  assert.deepEqual({ ...paneWindows.snapGeometry("left", bounds) }, { x: 0, y: 0, width: 600, height: 800 });
  assert.deepEqual({ ...paneWindows.snapGeometry("bottom-right", bounds) }, { x: 600, y: 400, width: 600, height: 400 });
  assert.equal(paneWindows.snapTarget({ x: 4, y: 5 }, bounds), "top-left");
  assert.equal(paneWindows.snapTarget({ x: 1198, y: 410 }, bounds), "right");
  assert.deepEqual(
    { ...paneWindows.constrainGeometry({ x: 1100, y: -20, width: 800, height: 900 }, bounds) },
    { x: 400, y: 0, width: 800, height: 800 },
  );
});

test("workspace selection helpers preserve unique stable keys", () => {
  const pane = workspace.newPaneState();
  workspace.setSelection(pane, ["3", "3", "4"]);
  assert.deepEqual(Array.from(workspace.selectionKeys(pane)), ["3", "4"]);
  assert.equal(pane.selected, null);
});

test("file visuals classify Amiga content consistently before rendering", () => {
  const pane = workspace.newPaneState({ kind: "ofs" });
  assert.equal(visuals.entryIcon(pane, { name: "Startup-Sequence" }, "file", false, false).kind, "script");
  assert.equal(visuals.entryIcon(pane, { name: "Game.bas" }, "file", false, false).kind, "basic");
  assert.equal(visuals.entryIcon(pane, { name: "Game", filetype: 3 }, "file", false, false).kind, "binary");
  assert.equal(visuals.entryIcon(pane, { name: "Kickstart", filetype: 7 }, "file", false, false).kind, "rom");
  assert.equal(visuals.entryIcon(pane, { name: "Manual.guide" }, "file", false, false).kind, "text");
  assert.equal(visuals.entryIcon(pane, { name: "Game.lha" }, "file", true, false).kind, "archive");
});

test("import planning applies filesystem limits without UI state", () => {
  // An AmigaDOS entry holds 30 characters on OFS and FFS alike, and a full
  // stop is an ordinary character in a name.
  const ofsRule = imports.targetNameRule({ image: { kind: "ofs" } }, "Read.Me");
  assert.equal(ofsRule.suggested, "Read.Me");
  assert.equal(ofsRule.valid, true);
  assert.equal(ofsRule.limit, 30);
  const longRule = imports.targetNameRule({ image: { kind: "ofs" } }, "A".repeat(40));
  assert.equal(longRule.valid, false);
  assert.equal(longRule.suggested.length, 30);
  // A long-filename variant raises the limit, and the server says so.
  const bigRule = imports.targetNameRule({
    image: { kind: "ffs", filesystemCapabilities: { nameLimit: 107 } },
  }, "A descriptive AmigaDOS filename");
  assert.equal(bigRule.suggested, "A descriptive AmigaDOS filename");
  assert.equal(bigRule.limit, 107);
  // The separator and the volume marker are the characters a name cannot hold.
  const slashRule = imports.targetNameRule({ image: { kind: "ffs" } }, "Games/Elite");
  assert.equal(slashRule.suggested, "Elite");
  const unicodeRule = imports.targetNameRule({
    image: { kind: "ffs", filenamePolicies: { file: { limit: 30, forbidden: ":/\\", latin1: true } } },
  }, "Elite🙂");
  assert.equal(unicodeRule.valid, false);
  assert.equal(unicodeRule.suggested, "Elite_");
  assert.equal(imports.targetNameRule({ image: { kind: "ffs" } }, " Café ").valid, false);
  // An 880 KiB volume holds 1758 blocks and OFS stores 488 bytes in each, so
  // a file just over half a volume forces a second disk.
  const disks = imports.allocateFilesToOfsDisks([
    { name: "One", length: 1000 * 488 },
    { name: "Two", length: 1000 * 488 },
  ], "adf");
  assert.equal(disks.length, 2);
  const together = imports.allocateFilesToOfsDisks([
    { name: "One", length: 100 },
    { name: "Two", length: 200 },
  ], "adf");
  assert.equal(together.length, 1);
  assert.equal(together[0].files.length, 2);
});

test("protection from a sidecar is accepted in either written form", () => {
  // The eight letters List prints are kept verbatim, because that is the form
  // a person can check at a glance.
  assert.equal(imports.normaliseProtection("----r-e-"), "----r-e-");
  // A raw long is normalised so one written value means one number.
  assert.equal(imports.normaliseProtection("&05"), "0x05");
  assert.equal(imports.normaliseProtection("0x05"), "0x05");
  // Anything else is reported as absent rather than guessed at.
  assert.equal(imports.normaliseProtection("read-only"), "");
});

test("protection bits round trip through their inverted low four", () => {
  // ----rwed is a fully permitted file, which stores zero in the low bits.
  assert.equal(metadata.formatProtection(0), "----rwed");
  // 0x05 denies writing and deleting, which is how a locked file reads.
  assert.equal(metadata.formatProtection(0x05), "----r-e-");
  assert.deepEqual({ ...metadata.protectionFlags(0x10) }, {
    h: false, s: false, p: false, a: true, r: true, w: true, e: true, d: true,
  });
  assert.equal(metadata.protectionValue(metadata.protectionFlags(0x95)), 0x95);
  assert.equal(metadata.protectionHex(metadata.protectionFlags(0x05)), "0x00000005");
  assert.equal(metadata.parseProtection("&10"), 0x10);
});

test("help handbook is isolated behind an injected modal boundary", () => {
  const showHelp = help.create({ showModal() {}, modalContent: {} });
  assert.equal(typeof showHelp, "function");
});

test("the application header exposes handbook and about help actions", () => {
  const markup = fs.readFileSync(path.join(__dirname, "../../app/static/index.html"), "utf8");
  assert.match(markup, /id="helpMenu"/);
  assert.match(markup, /id="helpGuideButton"/);
  assert.match(markup, /id="aboutButton"/);
});

test("about content uses runtime version and host metadata", () => {
  let markup = "";
  const showAbout = about.create({
    showModal(value) { markup = value; },
    esc: value => String(value),
    context: () => ({ version: "1.2.3", engine: "amiganut", host: "desktop" }),
  });
  showAbout();
  assert.match(markup, /Version 1\.2\.3/);
  assert.match(markup, /Linux desktop application/);
  assert.match(markup, /Third-party notices/);
});

test("editor workspace persistence validates, limits and restores documents", () => {
  const values = new Map();
  const storage = {
    getItem: key => values.get(key) || null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  };
  const manager = editorWorkspace.create({ storage, key: "editors", maxDocuments: 2, maxDraftBytes: 4, maxPanes: 3 });
  manager.state.documents.set("one", { key: "one", imageId: "a".repeat(32), index: 0, path: "$.ONE", name: "ONE", draft: "123456" });
  manager.state.documents.set("two", { key: "two", imageId: "b".repeat(32), index: 1, path: "$.TWO", name: "TWO" });
  manager.state.active = "one";
  manager.persist();

  const restored = editorWorkspace.create({ storage, key: "editors", maxDocuments: 2, maxDraftBytes: 4, maxPanes: 3 });
  restored.restore();
  assert.equal(restored.state.documents.get("one").draft, "1234");
  assert.equal(restored.state.restoreCandidate, "one");
});

test("operation lifecycle is isolated behind an injected pane controller", () => {
  const controller = operationUI.create({
    panes: [], api() {}, setLoading() {}, renderPane() {},
    modal: { open: false }, setModalAbort() {}, setModalProgress() {},
    newUuid: () => "00000000-0000-4000-8000-000000000000",
  });
  assert.equal(typeof controller.guardedPaneAction, "function");
  assert.equal(typeof controller.trackedPaneOperation, "function");
});

test("operation identifiers use the browser UUID implementation when available", () => {
  assert.equal(identifiers.newUuid({ randomUUID: () => "native-uuid" }), "native-uuid");
});

test("operation identifiers remain available on non-secure HTTP origins", () => {
  const cryptoSource = {
    getRandomValues(bytes) {
      bytes.fill(0);
      return bytes;
    },
  };
  assert.equal(identifiers.newUuid(cryptoSource), "00000000-0000-4000-8000-000000000000");
});

test("operation identifiers fail explicitly on obsolete browsers without Web Crypto", () => {
  assert.throws(
    () => identifiers.newUuid({}),
    /cannot create secure operation identifiers/i,
  );
});

test("workspace recovery is isolated behind an injected persistence controller", () => {
  const controller = workspacePersistence.create({
    panes: [], storage: { getItem() { return null; }, setItem() {} },
    storageKey: "workspace", newPaneState() { return {}; },
    restoredOfsPath() { return "$"; }, api() {}, rebuildPaneHosts() {},
    renderPane() {}, acceptImage() {}, loadDirectory() {},
    editorWorkspace: { state: {} }, activateEditorDocument() {}, toast() {},
  });
  assert.equal(typeof controller.remember, "function");
  assert.equal(typeof controller.restore, "function");
  assert.deepEqual(Array.from(controller.stored()), []);
});

test("pane presentation formats images and capacity through one component", () => {
  const view = paneView.create({
    esc: value => String(value),
    humanSize: value => `${value} B`,
  });
  assert.equal(view.paneFormat({ kind: "ofs", name: "demo.adz" }), "ADZ");
  assert.match(view.capacityMarkup({ available: true, total: 100, used: 75, free: 25, unit: "bytes" }), /capacity warning/);
  assert.match(view.crumbs("$.Games"), /data-path="\$"/);
});

test("the pane export control follows the formats the service offers", () => {
  const view = paneView.create({ esc: value => String(value), humanSize: value => `${value} B` });

  const exportable = view.exportAvailability({
    name: "demo.adz",
    exportFormats: [
      { format: "native", extension: "adl", label: "Native sector image (.adz)" },
      { format: "hfe", extension: "hfe", label: "HxC HFE flux image (.hfe)" },
      { format: "scp", extension: "scp", label: "SuperCard Pro flux image (.scp)" },
    ],
  });
  assert.equal(exportable.available, true);
  assert.match(exportable.label, /demo\.adz/);

  // A Hardfile pair carries geometry a flux or sector container cannot hold,
  // so the control is disabled and says which limitation applies.
  const hardfile = view.exportAvailability({
    name: "scsi0.hda",
    hasDescriptor: true,
    exportFormats: [],
  });
  assert.equal(hardfile.available, false);
  assert.match(hardfile.label, /Hardfile HDA and GEO pair/);

  // Anything else with no compatible target gets the general reason.
  const unsupported = view.exportAvailability({ name: "bank.rom", exportFormats: [] });
  assert.equal(unsupported.available, false);
  assert.match(unsupported.label, /no compatible format/);

  // A missing field must read as unavailable, never as an enabled control.
  assert.equal(view.exportAvailability({ name: "old.adf" }).available, false);
});

test("the pane export icon matches the other header controls", () => {
  const icons = visuals.PANE_ICONS;
  assert.ok(icons.exportImage, "the export control needs an icon");
  for (const [name, markup] of Object.entries(icons)) {
    assert.match(markup, /^<svg viewBox="0 0 24 24" aria-hidden="true">/, `${name} shares the icon frame`);
    assert.match(markup, /<\/svg>$/, `${name} is a complete element`);
  }
});

test("folder transfer planning preserves FFS trees and resolves collisions", () => {
  const planning = transferPlanning.create({
    targetNameRule: (_pane, name) => ({ suggested: name.slice(0, 10), limit: 10 }),
  });
  const result = planning.folderTargetPlans(
    { image: { kind: "ffs" } },
    [{ relativePath: "Pack/LongFilename" }, { relativePath: "Pack/LongFilename2" }],
    "preserve",
  );
  assert.deepEqual(Array.from(result.plans, item => item.targetPath), ["Pack/LongFilena", "Pack/LongFilen1"]);
});
