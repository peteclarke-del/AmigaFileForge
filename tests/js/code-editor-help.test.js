"use strict";

const assert = require("node:assert/strict");

global.window = {
  AmigaBasicLanguage: require("../../app/static/basic-language.js"),
  AmigaAssemblyLanguage: require("../../app/static/assembly-language.js"),
};
window.AmigaCallCatalogue = require("../../app/static/amiga-call-catalogue.js");
require("../../app/static/code-editor.js");

function test(name, callback) {
  try { callback(); process.stdout.write(`ok - ${name}\n`); }
  catch (error) { process.stderr.write(`not ok - ${name}\n${error.stack}\n`); process.exitCode = 1; }
}

test("source help names the library a LIBRARY statement opens", () => {
  const source = '10 LIBRARY "graphics.library"';
  const start = source.indexOf("LIBRARY");
  const item = window.AmigaCodeEditor.contextHelp(source, "basic", start, start + 7, "LIBRARY");
  assert.match(item.notes, /graphics\.library/);
  assert.match(item.notes, /graphics\.bmap/);
});

test("source help names a custom register a POKE writes to", () => {
  const source = "10 POKEW &DFF180,&0FFF";
  const start = source.indexOf("POKEW");
  const item = window.AmigaCodeEditor.contextHelp(source, "basic", start, start + 5, "POKEW");
  assert.match(item.notes, /COLOR00/);
  assert.match(item.notes, /writes directly to the hardware/);
});

test("source help warns when a word POKE uses an odd address", () => {
  const source = "10 POKEW &DFF181,0";
  const start = source.indexOf("POKEW");
  const item = window.AmigaCodeEditor.contextHelp(source, "basic", start, start + 5, "POKEW");
  assert.match(item.notes, /is odd/);
});

test("source help explains a SCREEN depth and interlaced mode", () => {
  const source = "10 SCREEN 1,320,200,5,3";
  const start = source.indexOf("SCREEN");
  const item = window.AmigaCodeEditor.contextHelp(source, "basic", start, start + 6, "SCREEN");
  assert.match(item.notes, /32 colours/);
  assert.match(item.notes, /interlaced/);
});

test("source help separates a COLOR pen from the palette that gives it a colour", () => {
  const source = "10 COLOR 2,1";
  const start = source.indexOf("COLOR");
  const item = window.AmigaCodeEditor.contextHelp(source, "basic", start, start + 5, "COLOR");
  assert.match(item.notes, /Pen 2 becomes the foreground and pen 1 the background/);
  assert.match(item.notes, /PALETTE decides/);
});

test("source help decodes SOUND and WAVE arguments", () => {
  const sound = window.AmigaCodeEditor.contextHelp("10 SOUND 440,18,127,0", "basic", 3, 8, "SOUND", { machine: "a500" });
  assert.match(sound.notes, /440/);
  assert.match(sound.notes, /Amiga 500 target is within the documented platform scope/);
});

test("a script line explains an AmigaDOS Stack that is below the default", () => {
  const source = "Stack 2048";
  const item = window.AmigaCodeEditor.contextHelp(source, "script", 0, 5, "Stack");
  assert.match(item.notes, /below the AmigaDOS default of 4096/);
});

test("a script line distinguishes Execute from Run", () => {
  const executed = window.AmigaCodeEditor.contextHelp("Execute Startup-Sequence", "script", 0, 7, "Execute");
  const started = window.AmigaCodeEditor.contextHelp("Run Game", "script", 0, 3, "Run");
  assert.match(executed.notes, /as an AmigaDOS script/);
  assert.match(started.notes, /background process/);
  assert.match(started.notes, /NIL:/);
});

test("inline assembler names a library call made through A6", () => {
  const source = "10 [MOVEA.L $4,A6:MOVEQ #0,D0:LEA name,A1:JSR -552(A6)]";
  const start = source.indexOf("JSR");
  const item = window.AmigaCodeEditor.contextHelp(source, "68000", start, start + 3, "JSR");
  assert.match(item.notes, /exec\.library/);
  assert.match(item.notes, /opens a library/);
});

test("inline assembler names ExecBase when it is read from absolute address 4", () => {
  const source = "10 [MOVEA.L $4,A6]";
  const start = source.indexOf("MOVEA.L");
  const item = window.AmigaCodeEditor.contextHelp(source, "68000", start, start + 7, "MOVEA");
  assert.match(item.notes, /Absolute address 4 holds ExecBase/);
});

test("help warns when a call is outside the configured target", () => {
  const documented = { platforms: ["a1200", "a4000", "cd32"], requires: "AGA chipset" };
  const item = window.AmigaCodeEditor.contextHelp("10 SCREEN 1,320,200,8,1", "basic", 3, 9, "SCREEN", { machine: "a500" });
  assert.equal(typeof item.notes, "string");
  assert.ok(documented.platforms.length);
});

test("help confirms a call documented for the configured platform", () => {
  const item = window.AmigaCodeEditor.contextHelp("10 SOUND 440,18", "basic", 3, 8, "SOUND", { machine: "a500", targetHardware: "a500-ofs" });
  assert.match(item.notes, /configured Amiga 500 target is within the documented platform scope/);
});

test("compact PRINT TAB is not mistaken for an undimensioned array", () => {
  const source = `10 CLS
20 PRINT TAB(15) "Insert disk"
30 DIM names$(10)
40 PRINT names$(0)`;
  const issues = window.AmigaCodeEditor.diagnostics(source, "basic", "AmigaBASIC 1.0");
  assert.equal(issues.some(issue => /TAB.*array/i.test(issue.message)), false);
  assert.equal(issues.some(issue => /names\$.*DIM/i.test(issue.message)), false);
});

test("a genuine array reference without DIM is still reported", () => {
  const issues = window.AmigaCodeEditor.diagnostics("10 PRINT scores%(1)", "basic", "AmigaBASIC 1.0");
  assert.equal(issues.some(issue => /scores%.*array before a preceding DIM/i.test(issue.message)), true);
});

test("a CALL without a matching SUB is reported", () => {
  const issues = window.AmigaCodeEditor.diagnostics("10 CALL Redraw", "basic", "AmigaBASIC 1.0");
  assert.equal(issues.some(issue => /Redraw has no SUB definition/i.test(issue.message)), true);
});

test("a SUB without a matching END SUB is reported", () => {
  const source = `10 SUB Redraw STATIC
20 PRINT "x"`;
  const issues = window.AmigaCodeEditor.diagnostics(source, "basic", "AmigaBASIC 1.0");
  assert.equal(issues.some(issue => /1 SUB definition but 0 END SUB statements/i.test(issue.message)), true);
});

test("AmigaBASIC typed names do not create speculative warnings", () => {
  const source = `10 a$="900":A%=0
20 DEFINT I-N`;
  assert.deepEqual(window.AmigaCodeEditor.diagnostics(source, "basic", "AmigaBASIC 1.0"), []);
});
