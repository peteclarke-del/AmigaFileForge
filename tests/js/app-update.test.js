"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const context = vm.createContext({ window: {} });
const source = fs.readFileSync(path.join(__dirname, "../../app/static/app-update.js"), "utf8");
vm.runInContext(source, context, { filename: "app-update.js" });
const updates = Object.values(context.window).find(value => value && typeof value.view === "function");

function test(name, callback) {
  try { callback(); process.stdout.write(`ok - ${name}\n`); }
  catch (error) { process.stderr.write(`not ok - ${name}\n${error.stack}\n`); process.exitCode = 1; }
}

const size = value => `${value} B`;
const release = (installable = true) => ({
  version: "9.0.0",
  name: "Forge 9.0.0",
  pageUrl: "https://example.org/releases/tag/v9.0.0",
  installable,
  packageSize: 1234,
});
const state = (phase, extra = {}) => ({
  phase,
  message: `${phase} message`,
  application: "Forge",
  releasesPage: "https://example.org/releases",
  ...extra,
});

test("the control offers the check until it is pressed", () => {
  const shown = updates.view(state("idle", { message: "" }), size);
  assert.equal(shown.button.label, "Check for Application Updates");
  assert.equal(shown.button.action, "check");
  assert.equal(shown.status, "");
  assert.equal(shown.progress, null);
  assert.equal(updates.view(null).button.label, updates.CHECK_LABEL);
});

test("a check in progress cannot be started again", () => {
  const shown = updates.view(state("checking"), size);
  assert.equal(shown.button.disabled, true);
  assert.equal(shown.progress.fraction, null);
});

test("the newest version and a failed check keep the check button", () => {
  for (const phase of ["current", "failed"]) {
    const shown = updates.view(state(phase), size);
    assert.equal(shown.button.action, "check");
    assert.equal(shown.status, `${phase} message`);
  }
  const failedInstall = updates.view(state("failed", { release: release() }), size);
  assert.equal(failedInstall.page, "https://example.org/releases/tag/v9.0.0");
});

test("an installable release offers the update and its page", () => {
  const shown = updates.view(state("available", { release: release() }), size);
  assert.equal(shown.button.label, "Update to 9.0.0");
  assert.equal(shown.button.action, "install");
  assert.equal(shown.page, "https://example.org/releases/tag/v9.0.0");
});

test("a release this copy cannot install opens its release page instead", () => {
  const shown = updates.view(state("available", { release: release(false) }), size);
  assert.equal(shown.button.label, "Open Release Page");
  assert.equal(shown.button.href, "https://example.org/releases/tag/v9.0.0");
  assert.equal(shown.page, "");
  const unnamed = updates.view(state("available", { release: { ...release(false), pageUrl: "" } }), size);
  assert.equal(unnamed.button.href, "https://example.org/releases");
});

test("a download shows its progress and can be cancelled", () => {
  const shown = updates.view(state("downloading", { release: release(), done: 617, total: 1234 }), size);
  assert.equal(shown.button, null);
  assert.equal(shown.cancel, true);
  assert.equal(shown.progress.fraction, 0.5);
  assert.equal(shown.progress.text, "617 B of 1234 B");
  const unknown = updates.view(state("downloading", { release: release(), done: 10, total: null }), size);
  assert.equal(unknown.progress.fraction, null);
  assert.equal(unknown.progress.text, "10 B");
});

test("an install cannot be cancelled once apt is running", () => {
  const shown = updates.view(state("installing", { release: release() }), size);
  assert.equal(shown.button, null);
  assert.equal(shown.cancel, false);
  assert.equal(shown.progress.fraction, null);
});

test("an installed update offers a restart", () => {
  const shown = updates.view(state("installed", { release: release() }), size);
  assert.equal(shown.button.label, "Restart Forge");
  assert.equal(shown.button.action, "restart");
  assert.equal(shown.page, "");
});
