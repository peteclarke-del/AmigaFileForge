"use strict";

const assert = require("node:assert/strict");
const basic = require("../../app/static/basic-language.js");

function test(name, callback) {
  try { callback(); process.stdout.write(`ok - ${name}\n`); }
  catch (error) { process.stderr.write(`not ok - ${name}\n${error.stack}\n`); process.exitCode = 1; }
}

test("typed variables that resemble commands remain identifiers", () => {
  const names = ["print%", "load%", "if%", "then%", "else%", "rem%", "goto%", "run%", "for%", "next%", "while%", "end%", "line!", "screen#"];
  const tokens = basic.scan(`10 ${names.map((name, index) => `${name}=${index}`).join(":")}`);
  assert.deepEqual(tokens.filter(token => token.type === "identifier").map(token => token.text), names);
  assert.equal(tokens.some(token => token.type === "keyword"), false);
  assert.equal(tokens.some(token => token.type === "comment"), false);
});

test("real commands beside typed variables retain keyword identity", () => {
  const tokens = basic.scan('10 page%=1:if page%=1 then print "OK"');
  assert.deepEqual(tokens.filter(token => token.type === "keyword").map(token => token.name), ["IF", "THEN", "PRINT"]);
  assert.deepEqual(tokens.filter(token => token.type === "identifier").map(token => token.text), ["page%", "page%"]);
});

test("a leading asterisk is multiplication, not a command", () => {
  const tokens = basic.scan("10 total = columns*rows");
  assert.equal(tokens.some(token => token.type === "star-command"), false);
  assert.deepEqual(tokens.filter(token => token.type === "identifier").map(token => token.text), ["total", "columns", "rows"]);
});

test("a listing exposes every keyword it uses", () => {
  const source = `10 SCREEN 1,320,200,5,1:WINDOW 2,"Game",,0,1
20 PALETTE 0,0,0,0:COLOR 1,0:CLS
30 FOR i=1 TO 10:PRINT"line";i:NEXT i
40 IF i>5 THEN GOTO 60 ELSE GOTO 30
50 CHAIN"Menu"
60 END`;
  const names = basic.scan(source).filter(token => token.type === "keyword").map(token => token.name);
  for (const keyword of ["SCREEN", "WINDOW", "PALETTE", "COLOR", "CLS", "FOR", "TO", "PRINT", "NEXT", "IF", "THEN", "GOTO", "ELSE", "CHAIN", "END"]) {
    assert.ok(names.includes(keyword), `${keyword} was not recognised`);
  }
  assert.equal(names.filter(name => name === "GOTO").length, 2);
});

test("a keyword glued to a name stays part of that name", () => {
  // This is the tokeniser's own rule: FORMAT, PRINTER and TOTAL are variables,
  // so the scanner must not colour a command inside them.
  const tokens = basic.scan("10 ending=1:printer=2:total=3:format=4");
  assert.deepEqual(
    tokens.filter(token => token.type === "identifier").map(token => token.text),
    ["ending", "printer", "total", "format"],
  );
  assert.equal(tokens.some(token => token.type === "keyword"), false);
});

test("every AmigaBASIC token-table keyword is recognised", () => {
  const every = [
    ...basic.AMIGABASIC_BASE_KEYWORDS,
    ...basic.AMIGABASIC_EXTENDED_KEYWORDS,
    ...basic.AMIGABASIC_12_KEYWORDS,
  ];
  for (const keyword of every) {
    const token = basic.scan(`10 ${keyword}`).find(item => item.start > 2);
    assert.equal(token?.type, "keyword", `${keyword} was not recognised as a BASIC keyword`);
    assert.equal(token?.name, keyword, `${keyword} was recognised under the wrong name`);
  }
});

test("AmigaBASIC 1.2 keywords are marked as needing the later release", () => {
  assert.equal(basic.KEYWORD_GENERATION.CIRCLE, 2);
  assert.equal(basic.KEYWORD_GENERATION.PAINT, 2);
  assert.equal(basic.KEYWORD_GENERATION.PRINT, undefined);
  assert.equal(basic.KEYWORD_GENERATION.WINDOW, undefined);
});

test("hexadecimal and octal literals scan as numbers", () => {
  const tokens = basic.scan("10 POKEL &HDFF180,&O777");
  assert.deepEqual(tokens.filter(token => token.type === "number").map(token => token.text), ["&HDFF180", "&O777"]);
});

test("statement splitting respects strings and comments", () => {
  assert.deepEqual(basic.splitStatements('A=1:PRINT "A:B":REM C:D').map(row => row.text), ["A=1", 'PRINT "A:B"', "REM C:D"]);
  assert.deepEqual(basic.splitStatements('x=a*b:PRINT x').map(row => row.text), ["x=a*b", "PRINT x"]);
});

test("masking leaves executable code positions stable", () => {
  const source = '10 PRINT "GOTO 90":REM GOTO 80\n20 GOTO 10';
  const masked = basic.maskStringsAndComments(source);
  assert.equal(masked.length, source.length);
  assert.equal(masked.includes("GOTO 90"), false);
  assert.equal(masked.includes("GOTO 80"), false);
  assert.equal(masked.includes("GOTO 10"), true);
});

test("dialect profiles are explicit and default conservatively", () => {
  assert.equal(basic.dialectProfile("AmigaBASIC 1.2").generation, 2);
  assert.equal(basic.dialectProfile("unknown").id, "amigabasic-10");
});
