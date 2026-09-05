(function initialiseAmigaBasicLanguage(globalObject) {
  "use strict";

  // The AmigaBASIC 1.0 token table, in the same three banks the interpreter
  // stores them in: a base bank, a shared &FE overflow bank, and an &FF bank
  // that only AmigaBASIC 1.2 understands. The Python tokeniser in amiganut
  // holds the same lists, so a program round-trips through either.
  const AMIGABASIC_BASE_KEYWORDS = (
    "ABS AND ASC ATN AUTO BEEP BLOAD BSAVE CALL CDBL CHAIN CHR$ CINT CLEAR CLNG CLOSE "
    + "CLS COLOR COMMON CONT COS CSNG CSRLIN DATA DEF DEFDBL DEFINT DEFSNG DEFSTR DELETE "
    + "DIM EDIT ELSE END EQV ERASE ERL ERR ERROR EXP FIX FN FOR GOSUB GOTO HEX$ IF IMP "
    + "INKEY$ INPUT INPUT$ INSTR INT KEY LEFT$ LEN LET LINE LIST LLIST LOAD LOCATE LOG "
    + "LPRINT MERGE MID$ MOD MOTOR NEW NEXT NOT OCT$ OFF ON OPEN OPTION OR OUT PEEK POINT "
    + "POKE PRESET PRINT PSET RANDOMIZE READ REM RENUM RESTORE RESUME RETURN RIGHT$ RND "
    + "RUN SAVE SCREEN SGN SHARED SIN SOUND SPACE$ SPC SQR STEP STOP STR$ STRING$ SUB "
    + "SWAP TAB TAN THEN TO TROFF TRON USING USR VAL VARPTR WAIT WEND WHILE WIDTH WRITE XOR"
  ).split(/\s+/);
  const AMIGABASIC_EXTENDED_KEYWORDS = (
    "AREA AREAFILL COLLISION DECLARE LIBRARY MENU MOUSE OBJECT PALETTE PATTERN PTAB SAY "
    + "SCROLL SLEEP STATIC STICK STRIG TIMER TRANSLATE$ WAVE WINDOW"
  ).split(/\s+/);
  //: Only AmigaBASIC 1.2 has this bank, so a program using any of these words
  //: will not load into 1.0.
  const AMIGABASIC_12_KEYWORDS = (
    "CHDIR CIRCLE CVD CVI CVL CVS DATE$ EOF FIELD FILES FRE GET KILL LBOUND LOC LOF LPOS "
    + "LSET MKD$ MKI$ MKL$ MKS$ NAME PAINT POS PUT RESET RSET SADD SEGMENT SYSTEM TIME$ "
    + "UBOUND UCASE$"
  ).split(/\s+/);
  //: Words AmigaBASIC spells as two, which the scanner has to see as one.
  const COMPOUND_KEYWORDS = ["END IF", "END SUB", "SELECT CASE", "END SELECT", "LIBRARY CLOSE", "ON ERROR", "SOUND WAIT", "SOUND RESUME"];
  const KEYWORDS = new Set([...AMIGABASIC_BASE_KEYWORDS, ...AMIGABASIC_EXTENDED_KEYWORDS, ...AMIGABASIC_12_KEYWORDS]);

  // AmigaBASIC tokenises a word as a keyword only when it is not glued to a
  // name character on either side, which is what keeps TOTAL, PRINTER and
  // FORMAT as ordinary variables. The scanner applies the same rule as the
  // tokeniser in amiganut, so highlighting and storage always agree.
  const isNameCharacter = character => Boolean(character) && /[A-Za-z0-9_]/.test(character);

  const DIALECTS = Object.freeze({
    "AmigaBASIC 1.0": { id: "amigabasic-10", generation: 1, structured: true, inlineAssembler: false, processor: "68000" },
    "AmigaBASIC 1.2": { id: "amigabasic-12", generation: 2, structured: true, inlineAssembler: false, processor: "68000" },
  });

  const KEYWORD_GENERATION = Object.freeze(
    Object.fromEntries(AMIGABASIC_12_KEYWORDS.map(keyword => [keyword, 2])),
  );

  // AmigaBASIC types a name with a trailing $, %, ! or #.
  const identifierPattern = /^[A-Za-z][A-Za-z0-9_.]*(?:[$%!#])?/;
  const compactKeywordBoundary = character => !character || !/[$%!#]/.test(character);
  const normaliseKeyword = value => String(value || "").toUpperCase();

  function isTypedIdentifier(value) {
    return /[$%]$/.test(String(value || ""));
  }

  function isKeywordToken(value) {
    const raw = String(value || "");
    return KEYWORDS.has(normaliseKeyword(raw));
  }

  function keywordPrefix(value, candidates) {
    const source = String(value || "");
    const upper = source.toUpperCase();
    return [...candidates]
      .sort((left, right) => right.length - left.length)
      .find(candidate => upper.startsWith(candidate)
        && compactKeywordBoundary(source[candidate.length])
        && !isNameCharacter(source[candidate.length])) || "";
  }

  function lexemeAt(value) {
    const source = String(value || "");
    const identifier = source.match(identifierPattern)?.[0] || "";
    if (!identifier) return "";
    const upper = normaliseKeyword(identifier);
    if (KEYWORDS.has(upper)) return identifier;
    const suffix = /[$%!#]$/.test(identifier) ? identifier.at(-1) : "";
    const base = suffix ? upper.slice(0, -1) : upper;
    // print%, len! and similar names are variables, not compact spellings of
    // the corresponding command, and FNname is an indivisible user symbol.
    // Other joined forms follow AmigaBASIC's grammar, so IFA, PRINT"x" and
    // GOTO90 expose their leading keyword first.
    if ((suffix && KEYWORDS.has(base)) || /^FN.+/i.test(identifier)) return identifier;
    const prefix = keywordPrefix(identifier, KEYWORDS);
    return prefix ? identifier.slice(0, prefix.length) : identifier;
  }

  function scanLine(line, lineOffset = 0, state = {}) {
    const tokens = [];
    let offset = 0;
    let inlineAssembler = Boolean(state.inlineAssembler);
    const number = String(line).match(/^\s*(\d+)/);
    if (number) {
      const local = number.index + number[0].lastIndexOf(number[1]);
      tokens.push({ type: "line-number", text: number[1], start: lineOffset + local, end: lineOffset + local + number[1].length });
      offset = number[0].length;
    }
    while (offset < line.length) {
      const character = line[offset];
      if (character === '"') {
        let end = offset + 1;
        while (end < line.length) {
          if (line[end] === '"') {
            if (line[end + 1] === '"') { end += 2; continue; }
            end += 1;
            break;
          }
          end += 1;
        }
        tokens.push({ type: "string", text: line.slice(offset, end), start: lineOffset + offset, end: lineOffset + end });
        offset = end;
        continue;
      }
      if (inlineAssembler && character === "\\") {
        tokens.push({ type: "comment", text: line.slice(offset), start: lineOffset + offset, end: lineOffset + line.length });
        break;
      }
      if (character === "[") inlineAssembler = true;
      if (character === "]") inlineAssembler = false;
      // AmigaBASIC has no star commands: a leading * is multiplication.
      // Numbers may be decimal, &H hexadecimal or &O octal.
      const numeric = line.slice(offset).match(/^(?:&[HO][0-9A-Fa-f]+|&[0-9A-Fa-f]+|\d+(?:\.\d+)?(?:[ED][-+]?\d+)?)/i)?.[0];
      if (numeric) {
        tokens.push({ type: "number", text: numeric, start: lineOffset + offset, end: lineOffset + offset + numeric.length });
        offset += numeric.length;
        continue;
      }
      const identifier = lexemeAt(line.slice(offset));
      if (identifier) {
        const keyword = !inlineAssembler && isKeywordToken(identifier);
        const type = keyword ? "keyword" : "identifier";
        tokens.push({ type, text: identifier, name: identifier.toUpperCase(), start: lineOffset + offset, end: lineOffset + offset + identifier.length });
        offset += identifier.length;
        if (keyword && identifier.toUpperCase() === "REM") {
          if (offset < line.length) tokens.push({ type: "comment", text: line.slice(offset), start: lineOffset + offset, end: lineOffset + line.length });
          break;
        }
        continue;
      }
      offset += 1;
    }
    return { tokens, inlineAssembler };
  }

  function scan(source) {
    const tokens = [];
    let lineOffset = 0;
    let inlineAssembler = false;
    const lines = String(source || "").split("\n");
    lines.forEach((line, index) => {
      const result = scanLine(line, lineOffset, { inlineAssembler });
      tokens.push(...result.tokens.map(token => ({ ...token, line: index + 1 })));
      inlineAssembler = result.inlineAssembler;
      lineOffset += line.length + 1;
    });
    return tokens;
  }

  function splitStatements(body) {
    const source = String(body || "");
    const statements = [];
    let start = 0;
    let quoted = false;
    let assembler = false;
    for (let index = 0; index < source.length; index += 1) {
      const character = source[index];
      if (character === '"') {
        if (quoted && source[index + 1] === '"') { index += 1; continue; }
        quoted = !quoted;
        continue;
      }
      if (quoted) continue;
      if (character === "[") assembler = true;
      if (character === "]") assembler = false;
      if (assembler && character === "\\") break;
      const remainder = source.slice(index);
      if (!assembler && /^REM(?![$%])/i.test(remainder) && (index === 0 || /[^A-Za-z0-9_$%]/.test(source[index - 1]))) break;
      if (!assembler && character === ":") {
        statements.push({ text: source.slice(start, index).trim(), start, end: index });
        start = index + 1;
      }
    }
    statements.push({ text: source.slice(start).trim(), start, end: source.length });
    return statements.filter(statement => statement.text);
  }

  function maskStringsAndComments(source) {
    const value = String(source || "");
    const mask = [...value];
    let quoted = false;
    for (let index = 0; index < value.length; index += 1) {
      if (value[index] === "\n") { quoted = false; continue; }
      if (value[index] === '"') {
        mask[index] = " ";
        if (quoted && value[index + 1] === '"') { mask[index + 1] = " "; index += 1; continue; }
        quoted = !quoted;
        continue;
      }
      if (quoted) { mask[index] = " "; continue; }
      if (/^REM(?![$%])/i.test(value.slice(index)) && (index === 0 || /[^A-Za-z0-9_$%]/.test(value[index - 1]))) {
        while (index < value.length && value[index] !== "\n") { mask[index] = " "; index += 1; }
        index -= 1;
      }
    }
    return mask.join("");
  }

  function dialectProfile(name) {
    return DIALECTS[name] || DIALECTS["AmigaBASIC 1.0"];
  }

  const api = Object.freeze({
    DIALECTS,
    KEYWORDS,
    AMIGABASIC_BASE_KEYWORDS,
    AMIGABASIC_EXTENDED_KEYWORDS,
    AMIGABASIC_12_KEYWORDS,
    COMPOUND_KEYWORDS,
    KEYWORD_GENERATION,
    compactKeywordBoundary,
    dialectProfile,
    isKeywordToken,
    isTypedIdentifier,
    keywordPrefix,
    lexemeAt,
    maskStringsAndComments,
    scan,
    scanLine,
    splitStatements,
  });

  globalObject.AmigaBasicLanguage = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
