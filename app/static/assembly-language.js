(function initialiseAmigaAssemblyLanguage(globalObject) {
  "use strict";

  // Every Amiga runs a 68000-family processor, so there is one instruction
  // set with additions per generation rather than several unrelated ones.
  // The base list is the MC68000 user and supervisor instruction set; each
  // later list holds only what that processor added.
  const MC68000 = (
    "ABCD ADD ADDA ADDI ADDQ ADDX AND ANDI ASL ASR "
    + "BCC BCS BEQ BGE BGT BHI BLE BLS BLT BMI BNE BPL BVC BVS "
    + "BCHG BCLR BRA BSET BSR BTST CHK CLR CMP CMPA CMPI CMPM "
    + "DBCC DBCS DBEQ DBF DBGE DBGT DBHI DBLE DBLS DBLT DBMI DBNE DBPL DBRA DBT DBVC DBVS "
    + "DIVS DIVU EOR EORI EXG EXT ILLEGAL JMP JSR LEA LINK LSL LSR "
    + "MOVE MOVEA MOVEM MOVEP MOVEQ MULS MULU NBCD NEG NEGX NOP NOT "
    + "OR ORI PEA RESET ROL ROR ROXL ROXR RTE RTR RTS SBCD "
    + "SCC SCS SEQ SF SGE SGT SHI SLE SLS SLT SMI SNE SPL ST SVC SVS "
    + "STOP SUB SUBA SUBI SUBQ SUBX SWAP TAS TRAP TRAPV TST UNLK"
  ).split(/\s+/);
  const MC68010_ADDITIONS = "BKPT MOVEC MOVES RTD".split(/\s+/);
  const MC68020_ADDITIONS = (
    "BFCHG BFCLR BFEXTS BFEXTU BFFFO BFINS BFSET BFTST CALLM CAS CAS2 CHK2 "
    + "CMP2 DIVSL DIVUL EXTB PACK RTM TRAPCC TRAPEQ TRAPNE TRAPF TRAPT UNPK"
  ).split(/\s+/);
  const MC68030_ADDITIONS = "PFLUSH PLOAD PMOVE PTEST".split(/\s+/);
  const MC68040_ADDITIONS = (
    "CINV CPUSH MOVE16 FABS FADD FCMP FDIV FMOVE FMOVEM FMUL FNEG FSQRT FSUB FTST"
  ).split(/\s+/);
  const MC68060_ADDITIONS = "LPSTOP PLPA".split(/\s+/);

  const unique = values => Object.freeze([...new Set(values)]);
  const with68010 = [...MC68000, ...MC68010_ADDITIONS];
  const with68020 = [...with68010, ...MC68020_ADDITIONS];
  const with68030 = [...with68020, ...MC68030_ADDITIONS];
  const with68040 = [...with68030, ...MC68040_ADDITIONS];
  const CATALOGUES = Object.freeze({
    "68000": unique(MC68000),
    "68010": unique(with68010),
    "68020": unique(with68020),
    "68030": unique(with68030),
    "68040": unique(with68040),
    "68060": unique([...with68040, ...MC68060_ADDITIONS]),
    m68k: unique(MC68000),
  });
  const SETS = Object.freeze(Object.fromEntries(Object.entries(CATALOGUES).map(([key, values]) => [key, new Set(values)])));
  const mnemonicsFor = architecture => SETS[String(architecture || "68000").toLowerCase()] || SETS["68000"];
  const isMnemonic = (architecture, mnemonic) => mnemonicsFor(architecture).has(String(mnemonic || "").toUpperCase());

  const api = Object.freeze({
    CATALOGUES,
    MC68000,
    MC68010_ADDITIONS,
    MC68020_ADDITIONS,
    MC68030_ADDITIONS,
    MC68040_ADDITIONS,
    MC68060_ADDITIONS,
    mnemonicsFor,
    isMnemonic,
  });
  globalObject.AmigaAssemblyLanguage = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
