window.AmigaCodeEditor = (() => {
  const BASIC_LANGUAGE = window.AmigaBasicLanguage;
  const ASSEMBLY_LANGUAGE = window.AmigaAssemblyLanguage;
  const CALL_CATALOGUE = window.AmigaCallCatalogue;
  // Every Amiga processor decodes the same instruction set, so the editor
  // treats them as one language and varies only the extensions it accepts.
  const M68K_TARGETS = ["68000", "68010", "68020", "68030", "68040", "68060", "m68k"];
  const esc = value => String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[character]));

  const help = (summary, syntax, requirements = "None beyond the active language or filing system.", notes = "") => ({
    summary, syntax, requirements, notes,
  });

  const BASIC_HELP = {
    AND: help("Combines two values bit by bit using logical AND.", "expression AND expression", "Operands are converted to integers before the bitwise operation."),
    OR: help("Combines two values bit by bit using logical OR.", "expression OR expression", "Inside IF conditions, zero is false and a non-zero result is true."),
    XOR: help("Combines two values bit by bit using exclusive OR.", "expression XOR expression", "Operands are converted to integers before the bitwise operation."),
    NOT: help("Inverts every bit of an integer value.", "NOT expression", "The expression is converted to an integer before inversion."),
    MOD: help("Returns the integer remainder after division.", "integer MOD integer", "The divisor must not be zero."),
    EQV: help("Reports whether two values agree bit by bit.", "expression EQV expression", "Operands are converted to integers first."),
    IMP: help("Applies bitwise implication to two values.", "expression IMP expression", "Operands are converted to integers first."),
    CHAIN: help("Loads and runs another AmigaBASIC program, optionally keeping variables.", 'CHAIN "program"[,line][,ALL]', "The target must be an AmigaBASIC program reachable through the current path.", "Use MERGE to combine programs instead of replacing the running one."),
    MERGE: help("Merges the lines of a saved ASCII program into the current one.", 'MERGE "program"', "The file must have been saved with the ,A option."),
    CALL: help("Calls a machine-code routine or a named SUB.", "CALL address(arguments) or CALL name(arguments)", "A machine-code address must contain valid 68000 code that ends in RTS.", "A wrong address, or a routine that corrupts A5 or A7, will take the machine down."),
    LIBRARY: help("Opens an Amiga library so its functions can be called from BASIC.", 'LIBRARY "dos.library"', "A matching .bmap file must be present for the library being opened.", "LIBRARY CLOSE releases every library opened this way."),
    DECLARE: help("Declares a library function before it is called.", 'DECLARE FUNCTION name LIBRARY', "The function must exist in an opened library's .bmap file."),
    GOTO: help("Continues execution at a line number or label.", "GOTO line", "The destination must exist in the current program."),
    GOSUB: help("Calls a subroutine; RETURN resumes after the GOSUB.", "GOSUB line", "The destination must exist and every completed path should reach RETURN."),
    RETURN: help("Returns from the most recent GOSUB.", "RETURN [line]", "A matching active GOSUB is required."),
    SUB: help("Begins a named subprogram with its own local variables.", "SUB name(parameters) STATIC", "AmigaBASIC subprograms are STATIC; they end with END SUB."),
    FN: help("Calls a function defined with DEF FN.", "FNname(parameter…)", "A matching DEF FNname definition must have been executed."),
    DEF: help("Defines a single-line function or a default variable type.", "DEF FNname(x)=expression, or DEFINT A-Z", "A DEF FN definition must be executed before the function is called."),
    FOR: help("Starts a counted loop and assigns its control variable.", "FOR variable = start TO limit [STEP amount]", "A matching NEXT completes the loop."),
    NEXT: help("Advances one or more active FOR loops.", "NEXT [variable[,variable…]]", "The named variable must belong to an active FOR loop."),
    WHILE: help("Starts a pre-tested loop.", "WHILE condition", "A matching WEND ends the loop."),
    WEND: help("Returns to the matching WHILE test.", "WEND", "A matching active WHILE is required."),
    IF: help("Conditionally executes statements, on one line or as a block.", "IF condition THEN statement [ELSE statement]", "A block form must be closed with END IF."),
    ON: help("Selects a numbered branch, or installs error, menu, mouse or timer handling.", "ON expression GOTO/GOSUB list; ON ERROR GOTO line", "Branch destinations must exist. ON ERROR GOTO 0 restores the default handler."),
    ERROR: help("Raises an AmigaBASIC error with a numeric code.", "ERROR number", "The active ON ERROR handler may intercept it."),
    RESUME: help("Continues after an error handler.", "RESUME [NEXT | line]", "Only valid inside an active ON ERROR handler."),
    DIM: help("Reserves an array.", "DIM array(dimensions)", "Enough free memory must remain; ERASE releases a dynamic array."),
    ERASE: help("Releases a dynamically allocated array.", "ERASE array[,array…]", "The array must have been dimensioned."),
    CLEAR: help("Clears variables and optionally resizes BASIC's data and stack space.", "CLEAR [,data-size[,stack-size]]", "The requested sizes must fit in free memory.", "The stack size is the AmigaDOS Stack the program will run with."),
    OPEN: help("Opens a file or device on a numbered channel.", 'OPEN "name" FOR mode AS #channel', "The path or device must exist and the mode must be one of INPUT, OUTPUT, APPEND or RANDOM."),
    CLOSE: help("Closes one channel, or all channels when used bare.", "CLOSE [#channel[,#channel…]]", "The channel must be open."),
    GET: help("Reads a record from a random-access channel, or pixels from a window.", "GET #channel[,record] or GET (x1,y1)-(x2,y2),array", "A random channel must have a defined record length."),
    PUT: help("Writes a record to a random-access channel, or pixels to a window.", "PUT #channel[,record] or PUT (x,y),array", "A random channel must have a defined record length."),
    INPUT: help("Reads values from the keyboard or an open channel.", "INPUT [#channel,] variable…", "File input requires an open channel and compatible textual data."),
    PRINT: help("Writes values to the current window or an open channel.", "PRINT [#channel,] expression…", "File output requires a writable open channel."),
    WRITE: help("Writes values in comma-separated, quoted form.", "WRITE [#channel,] expression…", "Intended to be read back by INPUT."),
    LOAD: help("Loads an AmigaBASIC program without running it.", 'LOAD "program"[,R]', "The file must be an AmigaBASIC program."),
    SAVE: help("Saves the current program, optionally as plain text.", 'SAVE "program"[,A]', "The destination must be writable. The ,A option writes an ASCII listing."),
    RUN: help("Runs the current program, or loads and runs a named one.", 'RUN [line | "program"]', "A named target must be an AmigaBASIC program."),
    KILL: help("Deletes a file.", 'KILL "filename"', "The file must exist and not be write protected."),
    NAME: help("Renames a file.", 'NAME "old" AS "new"', "The new name must not already exist."),
    FILES: help("Lists the contents of a directory.", 'FILES ["pattern"]', "The path must be readable."),
    COLOR: help("Selects the foreground and background pens for text and drawing.", "COLOR foreground[,background]", "The pen numbers must exist in the current screen's depth.", "PALETTE changes what colour a pen actually is."),
    PALETTE: help("Sets the red, green and blue components of one colour register.", "PALETTE index,red,green,blue", "Components run from 0 to 1. The index must be within the screen's depth."),
    SCREEN: help("Opens a custom screen with a chosen size, depth and mode.", "SCREEN id,width,height,depth,mode", "Depth and mode must be supported by the chipset in the target machine.", "Modes 3 and 4 are interlaced; four and five bitplanes need enough Chip RAM."),
    WINDOW: help("Opens, selects, closes or reports on a window.", "WINDOW id[,title][,(x1,y1)-(x2,y2)][,flags][,screen]", "A window on a custom screen must fit inside it."),
    MENU: help("Defines or reads an Intuition menu item.", "MENU menu,item,state[,title$]", "Menu and item numbers start at 1; item 0 is the menu title."),
    MOUSE: help("Reads the mouse buttons and position.", "MOUSE(n)", "MOUSE ON must have been issued before the values change."),
    OBJECT: help("Defines and moves a bob or sprite.", "OBJECT.SHAPE, OBJECT.X, OBJECT.Y, OBJECT.ON …", "The object must have been given a shape before it is displayed."),
    LINE: help("Draws a line, box or filled box.", "LINE (x1,y1)-(x2,y2)[,colour][,b[f]]", "The coordinates are relative to the current output window."),
    PSET: help("Sets one pixel.", "PSET (x,y)[,colour]", "The coordinates must lie inside the output window."),
    PRESET: help("Resets one pixel to the background pen.", "PRESET (x,y)[,colour]", "The coordinates must lie inside the output window."),
    CIRCLE: help("Draws a circle, ellipse or arc.", "CIRCLE (x,y),radius[,colour][,start,end][,aspect]", "Angles are in radians."),
    PAINT: help("Flood-fills an area up to a boundary colour.", "PAINT (x,y)[,fill][,boundary]", "The starting point must be inside a closed boundary."),
    PATTERN: help("Sets the line and area fill patterns.", "PATTERN line[,area]", "The area pattern array length must be a power of two."),
    CLS: help("Clears the current output window.", "CLS", "None."),
    LOCATE: help("Moves the text cursor.", "LOCATE row[,column]", "The position must lie inside the output window."),
    WIDTH: help("Sets the line width for a channel or the screen.", "WIDTH [#channel,] columns", "The channel must be open."),
    SOUND: help("Plays a note on one of the four audio channels.", "SOUND frequency,duration[,volume[,voice]]", "The frequency is in hertz, the duration in eighteenths of a second, the volume 0 to 255 and the voice 0 to 3.", "SOUND WAIT queues notes so several voices start together; SOUND RESUME releases them."),
    WAVE: help("Defines the waveform a voice plays.", "WAVE voice,waveform", "The waveform is either SIN or an array of 256 values from -128 to 127."),
    SAY: help("Speaks a phonetic string through the narrator device.", "SAY phonemes$[,mode]", "narrator.device and translator.library must be available.", "TRANSLATE$ converts English text into the phonemes SAY expects."),
    PEEK: help("Reads one byte from an address.", "PEEK(address)", "The address must be readable; PEEKW and PEEKL read a word and a long."),
    POKE: help("Writes one byte to an address.", "POKE address,value", "Writing to an address the system owns will crash the machine.", "POKEW and POKEL write a word and a long, and both need an even address."),
    POKEW: help("Writes one 16-bit word to an even address.", "POKEW address,value", "The address must be even, because a 68000 word access cannot be odd."),
    POKEL: help("Writes one 32-bit long to an even address.", "POKEL address,value", "The address must be even, because a 68000 long access cannot be odd."),
    PEEKW: help("Reads one 16-bit word from an even address.", "PEEKW(address)", "The address must be even."),
    PEEKL: help("Reads one 32-bit long from an even address.", "PEEKL(address)", "The address must be even."),
    VARPTR: help("Returns the address of a variable or file control block.", "VARPTR(variable)", "The address is only valid until BASIC moves its variables."),
    SADD: help("Returns the address of a string's characters.", "SADD(string$)", "The address is only valid until the string is reassigned."),
    TIMER: help("Reports the seconds elapsed since midnight.", "TIMER", "ON TIMER(n) GOSUB installs a periodic handler."),
    SLEEP: help("Waits until an event the program is watching occurs.", "SLEEP", "At least one of ON MOUSE, ON MENU, ON TIMER or ON BREAK must be active."),
    SYSTEM: help("Returns to Workbench or the Shell, closing the program.", "SYSTEM", "Open files are closed first."),
    STOP: help("Halts the program and returns to the BASIC prompt.", "STOP", "CONT resumes where STOP left off."),
    END: help("Ends the program, closing open files.", "END", "None."),
    DATA: help("Stores constant values for sequential access by READ.", "DATA value[,value…]", "READ variables must be compatible with the stored values."),
    READ: help("Reads the next value from the program's DATA stream.", "READ variable[,variable…]", "Enough DATA values must remain; RESTORE repositions the stream."),
    RESTORE: help("Moves the DATA read pointer to the start or to a line.", "RESTORE [line]", "A named destination must exist."),
    RANDOMIZE: help("Reseeds the random number generator.", "RANDOMIZE [seed]", "Without a seed AmigaBASIC asks for one."),
    SWAP: help("Exchanges the values of two variables of the same type.", "SWAP first,second", "Both variables must have the same type."),
    REM: help("Introduces a comment; the rest of the line is not executed.", "REM comment", "None."),
  };

  const SCRIPT_HELP = {
    ADDBUFFERS: help("Adds cache buffers to a mounted drive.", "AddBuffers drive buffers", "Each buffer costs about 512 bytes of memory."),
    ALIAS: help("Defines a Shell alias.", "Alias [name [string]]", "Aliases live only in the Shell that defined them."),
    ASSIGN: help("Creates, removes or lists a logical device name.", "Assign [name: [target]]", "The target must exist unless DEFER or PATH is used.", "Assign name: target ADD adds a second directory to an existing assignment."),
    AVAIL: help("Reports free Chip, Fast and total memory.", "Avail [CHIP|FAST|TOTAL|FLUSH]", "FLUSH expunges unused libraries and devices first."),
    BINDDRIVERS: help("Loads the expansion drivers in SYS:Expansion.", "BindDrivers", "Normally issued once from the Startup-Sequence."),
    BREAK: help("Sends a break signal to a background process.", "Break process [ALL|C|D|E|F]", "The process number comes from Status."),
    CD: help("Changes or reports the current directory.", "CD [directory]", "The directory must exist and be readable."),
    COPY: help("Copies files or whole directory trees.", "Copy [FROM] source [TO] destination [ALL] [CLONE]", "The destination must be writable.", "CLONE preserves the datestamp, protection bits and comment."),
    DATE: help("Reads or sets the system date and time.", "Date [date] [time] [TO file]", "Setting the clock needs SetClock to make it permanent."),
    DELETE: help("Deletes files or directories.", "Delete file [file…] [ALL] [QUIET] [FORCE]", "A delete-protected file needs FORCE, and a directory needs ALL unless it is empty."),
    DIR: help("Lists a directory.", "Dir [directory] [OPT A|I|D]", "The directory must be readable."),
    DISKCHANGE: help("Tells AmigaDOS that a disk has been swapped.", "DiskChange device", "Needed for drives that cannot report a change themselves."),
    ECHO: help("Writes a line of text.", 'Echo "text" [NOLINE] [FIRST n] [LEN n]', "None."),
    ENDCLI: help("Closes the Shell it is issued in.", "EndCLI", "The Shell must not be the last one holding the program."),
    EXECUTE: help("Runs an AmigaDOS script.", "Execute script [arguments]", "The script must be readable.", "A script with its s protection bit set can be run by name alone."),
    FAILAT: help("Sets the return code at which a script stops.", "FailAt code", "The default is 10; 21 tolerates warnings and errors below failure."),
    FAULT: help("Explains a numeric AmigaDOS error code.", "Fault code [code…]", "None."),
    FILENOTE: help("Reads or sets a file's comment.", 'FileNote file "comment"', "The comment may be up to 79 characters."),
    FORMAT: help("Initialises a volume.", "Format DRIVE device NAME name [FFS] [INTERNATIONAL] [DIRCACHE] [QUICK]", "Formatting destroys everything on the volume."),
    GETENV: help("Prints an environment variable.", "GetEnv name", "The variable must exist in ENV:."),
    IF: help("Runs the following lines only when a condition holds.", "IF [NOT] [WARN|ERROR|FAIL|EXISTS file|value EQ value]", "Used only in a script; closed with EndIf."),
    INFO: help("Reports the size, use and state of mounted volumes.", "Info [device]", "None."),
    INSTALL: help("Writes or checks a floppy boot block.", "Install [DRIVE] device [CHECK] [NOBOOT] [FFS]", "The disk must be writable; CHECK only reports."),
    JOIN: help("Concatenates files into a new one.", "Join file file… AS destination", "The destination must not be one of the sources."),
    LAB: help("Marks a destination for Skip inside a script.", "Lab name", "Used only in a script."),
    LIST: help("Lists a directory with sizes, protection bits, dates and comments.", "List [directory] [pattern] [DATES] [KEYS]", "The directory must be readable."),
    LOADWB: help("Starts Workbench.", "LoadWB [DELAY] [-DEBUG]", "Normally the last line of the Startup-Sequence."),
    LOCK: help("Write protects or unprotects a mounted volume.", "Lock drive [ON|OFF] [password]", "The volume must be mounted."),
    MAKEDIR: help("Creates a directory.", "MakeDir directory", "The parent must exist and be writable."),
    MAKELINK: help("Creates a hard or soft link.", "MakeLink from to [HARD|SOFT] [FORCE]", "A hard link must stay on the same volume."),
    MOUNT: help("Mounts a device described in DEVS:MountList or DEVS:DOSDrivers.", "Mount device", "The mount entry and its handler must be present."),
    NEWSHELL: help("Opens another Shell window.", "NewShell [window] [FROM file]", "The window specification must be a valid CON: string."),
    PATH: help("Reads or changes the command search path.", "Path [directory…] [ADD] [SHOW] [RESET]", "Each directory must exist."),
    PROMPT: help("Sets the Shell prompt.", 'Prompt "string"', "%N is the process number and %S the current directory."),
    PROTECT: help("Reads or sets a file's protection bits.", "Protect file [+|-][hsparwed]", "The r, w, e and d bits control read, write, execute and delete.", "The s bit marks a script so Execute is not needed to run it."),
    QUIT: help("Ends a script with a chosen return code.", "Quit [code]", "Used only in a script."),
    RELABEL: help("Renames a volume.", "Relabel drive name", "The volume must be writable."),
    REMRAD: help("Removes the recoverable RAM disk.", "RemRad [device]", "RAD: must not be in use."),
    RENAME: help("Renames or moves a file within a volume.", "Rename from TO to", "Both paths must be on the same volume."),
    RESIDENT: help("Makes a command permanently resident in memory.", "Resident [name] [file] [REMOVE] [ADD] [PURE]", "The command must be marked pure."),
    RUN: help("Starts a command as a background process.", "Run command [command…]", "The Shell returns immediately.", "Use >NIL: <NIL: so the process does not hold the Shell window open."),
    SEARCH: help("Searches files for a string.", "Search [directory] [pattern] SEARCH string [ALL]", "The directories must be readable."),
    SETCLOCK: help("Copies the system time to or from the battery-backed clock.", "SetClock LOAD|SAVE|RESET", "The machine must have a real-time clock."),
    SETENV: help("Sets an environment variable.", "SetEnv name string", "ENV: must be assigned."),
    SKIP: help("Jumps forward to a Lab inside a script.", "Skip [label] [BACK]", "Used only in a script."),
    SORT: help("Sorts the lines of a file.", "Sort from TO to [COLSTART n]", "The destination must be writable."),
    STACK: help("Reads or sets the stack size given to commands.", "Stack [size]", "The default is 4096 bytes.", "A game or a deeply recursive program often needs 8192 or more."),
    STATUS: help("Lists the running processes.", "Status [process] [FULL] [TCB] [CLI|COM|SEGS]", "None."),
    TYPE: help("Displays a file as text or as hexadecimal.", "Type file [TO destination] [OPT H|N]", "OPT H shows hexadecimal, OPT N adds line numbers."),
    VERSION: help("Reports the version of Kickstart, Workbench or a named file.", "Version [file] [VERSION n] [REVISION n] [FULL]", "A named file must carry a $VER: string."),
    WAIT: help("Pauses for a period or until a time.", "Wait [n] [SEC|MIN] [UNTIL time]", "Used mostly in a Startup-Sequence."),
    WHICH: help("Reports where a command would be found on the path.", "Which command [NOALIAS] [RES] [ALL]", "None."),
    WHY: help("Explains why the previous command failed.", "Why", "Only meaningful immediately after a failure."),
  };

  const LIBRARY_HELP = {
    _LVOOPENLIBRARY: help("Opens a library and returns its base in D0.", "MOVEQ #version,D0 / LEA name,A1 / JSR _LVOOpenLibrary(A6)", "A6 must hold ExecBase, read from absolute address 4."),
    _LVOCLOSELIBRARY: help("Closes a library opened with OpenLibrary.", "MOVEA.L base,A1 / JSR _LVOCloseLibrary(A6)", "A6 must hold ExecBase."),
    _LVOALLOCMEM: help("Allocates memory of a requested type.", "MOVE.L size,D0 / MOVE.L requirements,D1 / JSR _LVOAllocMem(A6)", "A6 must hold ExecBase. D0 is zero when the request cannot be met.", "MEMF_CHIP is required for anything the custom chips must read."),
    _LVOFREEMEM: help("Frees memory previously allocated with AllocMem.", "MOVEA.L block,A1 / MOVE.L size,D0 / JSR _LVOFreeMem(A6)", "The size must match the allocation exactly."),
    _LVOFORBID: help("Stops task switching until Permit is called.", "JSR _LVOForbid(A6)", "A6 must hold ExecBase. Keep the forbidden section short."),
    _LVOPERMIT: help("Allows task switching again after Forbid.", "JSR _LVOPermit(A6)", "One Permit is needed for each Forbid."),
    _LVODISABLE: help("Disables interrupts until Enable is called.", "JSR _LVODisable(A6)", "Interrupts must be re-enabled quickly or the system will lose input and disk activity."),
    _LVOENABLE: help("Re-enables interrupts after Disable.", "JSR _LVOEnable(A6)", "One Enable is needed for each Disable."),
    _LVODOIO: help("Sends an I/O request and waits for it to finish.", "MOVEA.L request,A1 / JSR _LVODoIO(A6)", "The request must be initialised and its device opened."),
    _LVOOPEN: help("Opens a file and returns a BCPL file handle in D0.", "MOVE.L name,D1 / MOVE.L mode,D2 / JSR _LVOOpen(A6)", "A6 must hold the dos.library base. D0 is zero on failure.", "The name is a C string pointer; the mode is MODE_OLDFILE, MODE_NEWFILE or MODE_READWRITE."),
    _LVOCLOSE: help("Closes a file handle returned by Open.", "MOVE.L handle,D1 / JSR _LVOClose(A6)", "A6 must hold the dos.library base."),
    _LVOREAD: help("Reads bytes from a file handle into a buffer.", "MOVE.L handle,D1 / MOVE.L buffer,D2 / MOVE.L length,D3 / JSR _LVORead(A6)", "D0 returns the count read, 0 at end of file and -1 on error."),
    _LVOWRITE: help("Writes bytes from a buffer to a file handle.", "MOVE.L handle,D1 / MOVE.L buffer,D2 / MOVE.L length,D3 / JSR _LVOWrite(A6)", "D0 returns the count written, or -1 on error."),
    _LVOOUTPUT: help("Returns the process's standard output handle.", "JSR _LVOOutput(A6)", "A6 must hold the dos.library base."),
    _LVOINPUT: help("Returns the process's standard input handle.", "JSR _LVOInput(A6)", "A6 must hold the dos.library base."),
    _LVOLOCK: help("Locks a file or directory and returns a lock in D0.", "MOVE.L name,D1 / MOVE.L mode,D2 / JSR _LVOLock(A6)", "The lock must be released with UnLock."),
    _LVOUNLOCK: help("Releases a lock obtained from Lock.", "MOVE.L lock,D1 / JSR _LVOUnLock(A6)", "A6 must hold the dos.library base."),
    _LVOOPENSCREEN: help("Opens an Intuition screen.", "LEA newScreen,A0 / JSR _LVOOpenScreen(A6)", "A6 must hold the intuition.library base."),
    _LVOOPENWINDOW: help("Opens an Intuition window.", "LEA newWindow,A0 / JSR _LVOOpenWindow(A6)", "A6 must hold the intuition.library base."),
    _LVOWAITTOF: help("Waits for the next vertical blank.", "JSR _LVOWaitTOF(A6)", "A6 must hold the graphics.library base."),
  };

  const BASIC_KEYWORDS = BASIC_LANGUAGE?.KEYWORDS || new Set();
  const SCRIPT_COMMANDS = new Set([...Object.keys(SCRIPT_HELP), ...BASIC_KEYWORDS]);
  const ASM_HELP = {
    MOVE: help("Copies a value and sets the condition codes from it.", "MOVE.size source,destination", "The size suffix and both addressing modes must be legal for the operation."),
    MOVEQ: help("Loads a sign-extended byte constant into a data register.", "MOVEQ #value,Dn", "The value must be between -128 and 127."),
    MOVEA: help("Copies a value into an address register without touching the condition codes.", "MOVEA.W/L source,An", "A word source is sign-extended to the full 32 bits."),
    MOVEM: help("Saves or restores a set of registers in one instruction.", "MOVEM.size list,destination", "The same register list and size must be used to restore them."),
    LEA: help("Loads the effective address of an operand into an address register.", "LEA source,An", "The source must use a control addressing mode."),
    JSR: help("Calls a subroutine, pushing the return address on the stack.", "JSR destination", "The destination must contain code that returns with RTS.", "A library call is written JSR offset(A6), where the offset is negative."),
    BSR: help("Calls a subroutine at a displacement from the program counter.", "BSR[.B|.W] label", "The label must be within the displacement range."),
    JMP: help("Transfers control without pushing a return address.", "JMP destination", "The destination must contain executable code."),
    RTS: help("Returns from a subroutine.", "RTS", "The stack must hold a valid return address."),
    RTE: help("Returns from an exception handler.", "RTE", "Only valid in supervisor mode with an intact exception frame."),
    TRAP: help("Raises one of the sixteen TRAP exceptions.", "TRAP #vector", "The exception vector must be installed."),
    CMP: help("Compares two values by setting the condition codes.", "CMP.size source,Dn", "A conditional branch normally follows."),
    TST: help("Sets the condition codes from one operand.", "TST.size operand", "Useful for testing a value a MOVE did not already set flags for."),
    BEQ: help("Branches when the zero flag is set.", "BEQ[.B|.W] label", "The label must be within the displacement range."),
    BNE: help("Branches when the zero flag is clear.", "BNE[.B|.W] label", "The label must be within the displacement range."),
    DBRA: help("Decrements a counter and loops until it passes -1.", "DBRA Dn,label", "The counter is the low word of Dn, so the loop runs count+1 times."),
    BTST: help("Tests one bit and sets the zero flag from it.", "BTST #bit,operand", "Bit numbering starts at zero from the least significant bit."),
    ADD: help("Adds a value and sets the condition codes.", "ADD.size source,destination", "ADDA is used when the destination is an address register."),
    SUB: help("Subtracts a value and sets the condition codes.", "SUB.size source,destination", "SUBA is used when the destination is an address register."),
    ANDI: help("Combines an immediate value with a destination using AND.", "ANDI.size #value,destination", "ANDI to SR is privileged."),
    ORI: help("Combines an immediate value with a destination using OR.", "ORI.size #value,destination", "ORI to SR is privileged."),
  };

  const INLINE_ASSEMBLER_HELP = {
    "DC.B": help("Places one or more bytes in the output.", 'DC.B value[,value…] or DC.B "text"', "Follow an odd number of bytes with EVEN before any word-sized data."),
    "DC.W": help("Places one or more 16-bit words in the output.", "DC.W value[,value…]", "The address must be even."),
    "DC.L": help("Places one or more 32-bit longs in the output.", "DC.L value[,value…]", "The address must be even."),
    "DS.B": help("Reserves a number of bytes, filled with zero.", "DS.B count", "None."),
    "DS.W": help("Reserves a number of words, filled with zero.", "DS.W count", "The address must be even."),
    "DS.L": help("Reserves a number of longs, filled with zero.", "DS.L count", "The address must be even."),
    EVEN: help("Advances the assembly address to the next even address.", "EVEN", "Required after an odd number of bytes, because a 68000 word access must be even."),
    CNOP: help("Aligns the assembly address to a chosen boundary.", "CNOP offset,alignment", "The alignment is normally 2 or 4."),
    EQU: help("Gives a name to a constant value.", "name EQU value", "The value must be known when the line is assembled."),
    SECTION: help("Starts a named hunk of code, data or BSS.", "SECTION name,CODE|DATA|BSS[_C|_F]", "_C requests Chip RAM and _F requests Fast RAM."),
    INCLUDE: help("Assembles the contents of another source file at this point.", 'INCLUDE "file"', "The file must be reachable through the assembler's include path."),
    XREF: help("Declares a symbol defined in another object file.", "XREF name", "The linker resolves it."),
    XDEF: help("Makes a symbol visible to other object files.", "XDEF name", "The symbol must be defined in this file."),
    OPT: help("Sets assembler options.", "OPT option[,option…]", "The accepted options depend on the assembler."),
  };

  //: Absolute addresses an Amiga program reaches without going through a
  //: library, so a decoded operand can be named rather than left as a number.
  const SYSTEM_ADDRESS_HELP = new Map([
    [0x000004, "ExecBase"], [0xBFE001, "CIAA-PRA"], [0xBFD000, "CIAB-PRA"],
    [0xDFF000, "BLTDDAT"], [0xDFF002, "DMACONR"], [0xDFF004, "VPOSR"],
    [0xDFF006, "VHPOSR"], [0xDFF00A, "JOY0DAT"], [0xDFF00C, "JOY1DAT"],
    [0xDFF010, "ADKCONR"], [0xDFF016, "POTGOR"], [0xDFF01A, "DSKBYTR"],
    [0xDFF01C, "INTENAR"], [0xDFF01E, "INTREQR"], [0xDFF020, "DSKPTH"],
    [0xDFF024, "DSKLEN"], [0xDFF02A, "VPOSW"], [0xDFF034, "POTGO"],
    [0xDFF040, "BLTCON0"], [0xDFF042, "BLTCON1"], [0xDFF07E, "DSKSYNC"],
    [0xDFF080, "COP1LCH"], [0xDFF084, "COP2LCH"], [0xDFF088, "COPJMP1"],
    [0xDFF08E, "DIWSTRT"], [0xDFF090, "DIWSTOP"], [0xDFF092, "DDFSTRT"],
    [0xDFF094, "DDFSTOP"], [0xDFF096, "DMACON"], [0xDFF09A, "INTENA"],
    [0xDFF09C, "INTREQ"], [0xDFF09E, "ADKCON"], [0xDFF0A0, "AUD0LCH"],
    [0xDFF100, "BPLCON0"], [0xDFF102, "BPLCON1"], [0xDFF104, "BPLCON2"],
    [0xDFF108, "BPL1MOD"], [0xDFF10A, "BPL2MOD"], [0xDFF180, "COLOR00"],
    [0xDFF182, "COLOR01"], [0xDFF1FC, "FMODE"],
  ]);

  const normaliseHelpKey = value => String(value || "").trim().replace(/[^A-Za-z0-9$_.]/g, "").toUpperCase();
  const COMMAND_CASE = Object.freeze({ basic: "upper", script: "mixed", "68000": "upper", "68010": "upper", "68020": "upper", "68030": "upper", "68040": "upper", "68060": "upper", m68k: "upper" });
  const dictionary = language => language === "basic" ? { ...BASIC_HELP, ...INLINE_ASSEMBLER_HELP, ...ASM_HELP, ...LIBRARY_HELP } : language === "script" ? { ...SCRIPT_HELP, ...BASIC_HELP } : { ...INLINE_ASSEMBLER_HELP, ...ASM_HELP, ...LIBRARY_HELP };
  const lookup = (language, key) => {
    const normal = normaliseHelpKey(key);
    const found = dictionary(language)[normal];
    if (found) return { key: normal, ...found };
    if (language === "script" && /^[A-Z][A-Z0-9-]*$/.test(normal)) {
      return { key: normal, ...help("AmigaDOS command.", `${normal} [arguments]`, "The command must be resident, on the current path, or in C:.") };
    }
    if (language === "basic" && BASIC_KEYWORDS.has(normal)) return { key: normal, ...help("AmigaBASIC keyword.", normal, "Syntax and availability depend on the AmigaBASIC version.") };
    if (ASSEMBLY_LANGUAGE?.isMnemonic(language, normal)) return { key: normal, ...help(`${language.toUpperCase()} processor instruction.`, normal, "Operands and addressing modes must be valid for the selected processor variant.") };
    if (M68K_TARGETS.includes(language) && /^[A-Z][A-Z0-9.]*$/.test(normal)) {
      return { key: normal, ...help(`${language.toUpperCase()} instruction or assembler pseudo-operation.`, normal, "The decoded operands, processor variant and execution context determine its exact effect.") };
    }
    return null;
  };

  //: The library vectors this build can name, keyed by offset, so a decoded
  //: JSR through A6 can be explained without knowing which library is open.
  const VECTOR_HELP = Object.freeze(Object.fromEntries(Object.entries(CALL_CATALOGUE?.EXEC || {}).map(([offset, spec]) => [offset, spec.summary])));
  const CUSTOM_REGISTER_HELP = Object.freeze(Object.fromEntries(Object.entries(CALL_CATALOGUE?.GRAPHICS || {}).map(([offset, spec]) => [offset, spec.summary])));

  const sourceNumber = value => {
    const match = String(value || "").trim().match(/^(-?)(?:&([0-9a-f]+)|0x([0-9a-f]+)|(\d+))/i);
    if (!match) return null;
    const number = Number.parseInt(match[2] || match[3] || match[4], match[2] || match[3] ? 16 : 10);
    return match[1] ? -number : number;
  };

  function constantNumbers(value) {
    const numbers = [];
    let remaining = String(value || "").trim();
    while (remaining) {
      const match = remaining.match(/^(-?(?:&[0-9a-f]+|0x[0-9a-f]+|\d+))/i);
      if (!match) break;
      numbers.push(sourceNumber(match[1]));
      const separator = remaining.slice(match[0].length).match(/^(\s*,\s*|\s+)/);
      if (!separator) break;
      remaining = remaining.slice(match[0].length + separator[0].length);
    }
    return numbers;
  }

  function preceding68000Registers(line, relativeStart) {
    const registers = {};
    const prefix = line.slice(0, relativeStart);
    for (const match of prefix.matchAll(/\bLD([AXY])\s*#\s*(&[0-9a-f]+|0x[0-9a-f]+|\d+)/gi)) registers[match[1].toUpperCase()] = sourceNumber(match[2]);
    return registers;
  }

  const PLATFORM_NAMES = Object.freeze({
    a500: "Amiga 500", a500plus: "Amiga 500+", a600: "Amiga 600",
    a1200: "Amiga 1200", a2000: "Amiga 2000", a3000: "Amiga 3000",
    a4000: "Amiga 4000", cd32: "Amiga CD32", amigaos: "AmigaOS hard drive",
  });

  function configuredPlatform(profile = {}) {
    const identify = value => {
      const text = String(value || "").toLowerCase();
      if (/cd32/.test(text)) return "cd32";
      if (/a(?:miga[ -]*)?4000|amigaos/.test(text)) return "a4000";
      if (/a(?:miga[ -]*)?3000/.test(text)) return "a3000";
      if (/a(?:miga[ -]*)?2000/.test(text)) return "a2000";
      if (/a(?:miga[ -]*)?1200/.test(text)) return "a1200";
      if (/a(?:miga[ -]*)?600/.test(text)) return "a600";
      if (/a(?:miga[ -]*)?500[ ]*\+|a500plus/.test(text)) return "a500plus";
      if (/a(?:miga[ -]*)?500/.test(text)) return "a500";
      return "";
    };
    return identify(profile.machine) || identify(profile.targetHardware) || "auto";
  }

  function platformHelp(result, profile = {}) {
    const platform = configuredPlatform(profile);
    const targetName = platform === "auto" ? "the automatic target" : PLATFORM_NAMES[platform];
    const documented = result?.platforms || [];
    const requirements = result?.requires ? ` Requirements: ${result.requires}.` : "";
    if (platform === "auto") return `The workbench target is automatic, so compatibility cannot be confirmed.${requirements}`;
    if (!documented.length) return `The catalogue cannot prove that this machine-specific operation is supported by the configured ${targetName} target.${requirements}`;
    if (!documented.includes(platform)) {
      const designedFor = documented.map(item => PLATFORM_NAMES[item] || item).join(", ");
      return `Target warning: this operation is documented for ${designedFor}, not the configured ${targetName} target. It was not designed for the current platform and, if accepted at all, may cause unexpected behaviour.${requirements}`;
    }
    return `The configured ${targetName} target is within the documented platform scope.${requirements}`;
  }

  function catalogueText(result, profile) {
    if (!result) return "";
    const detail = (result.details || []).filter(Boolean).join(". ");
    return `${result.summary}.${detail ? ` ${detail}.` : ""} ${platformHelp(result, profile)}`;
  }

  function sourceContextHelp(source, language, start, end, key, targetProfile = {}) {
    const base = lookup(language, key);
    if (!base) return base;
    const lineStart = source.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
    const lineEnd = source.indexOf("\n", end);
    const line = source.slice(lineStart, lineEnd < 0 ? source.length : lineEnd);
    const relativeStart = start - lineStart;
    const relativeEnd = end - lineStart;
    const tail = line.slice(relativeEnd);
    const normal = normaliseHelpKey(key);
    const additions = [];
    if (normal === "LIBRARY" && ["basic", "script"].includes(language)) {
      const name = tail.match(/^\s*"([^"]*)"/)?.[1];
      if (name) {
        additions.push(`This opens ${JSON.stringify(name)}, whose functions become callable once a matching ${name.replace(/\.library$/i, "")}.bmap file is present in the current directory or in LIBS:.`);
      }
    }
    if (["POKE", "POKEW", "POKEL"].includes(normal) && language === "basic") {
      const [address] = constantNumbers(tail.split(":", 1)[0]);
      const register = address == null ? null : SYSTEM_ADDRESS_HELP.get(address);
      if (register) additions.push(`Address &${address.toString(16).toUpperCase()} is ${register}, so this writes directly to the hardware rather than to program memory.`);
      if (["POKEW", "POKEL"].includes(normal) && address != null && address % 2) {
        additions.push(`Address &${address.toString(16).toUpperCase()} is odd, and a 68000 word or long access must be even.`);
      }
    }
    if (["PEEK", "PEEKW", "PEEKL"].includes(normal) && language === "basic") {
      const [address] = constantNumbers(tail.replace(/^\s*\(/, "").split(")", 1)[0]);
      const register = address == null ? null : SYSTEM_ADDRESS_HELP.get(address);
      if (register) additions.push(`Address &${address.toString(16).toUpperCase()} is ${register}.`);
    }
    if (["SOUND", "WAVE", "SAY", "PALETTE", "OBJECT"].includes(normal) && language === "basic") {
      const statement = tail.split(":", 1)[0];
      const decoded = CALL_CATALOGUE?.explainBasicCall(normal, constantNumbers(statement));
      if (decoded) additions.push(catalogueText(decoded, targetProfile));
    }
    if (normal === "SCREEN" && language === "basic") {
      const [, , , depth, mode] = constantNumbers(tail.split(":", 1)[0]);
      if (Number.isFinite(depth)) {
        additions.push(`A depth of ${depth} gives ${2 ** depth} colours and needs ${depth} bitplanes of Chip RAM for every displayed line.`);
      }
      if (mode === 3 || mode === 4) additions.push(`Mode ${mode} is interlaced, so the display will flicker on a monitor without a scan doubler.`);
    }
    if (normal === "COLOR" && language === "basic") {
      const [foreground, background] = constantNumbers(tail.split(":", 1)[0]);
      if (Number.isFinite(foreground)) {
        additions.push(background == null
          ? `Pen ${foreground} becomes the foreground; PALETTE decides what colour that pen actually is.`
          : `Pen ${foreground} becomes the foreground and pen ${background} the background; PALETTE decides what colours those pens actually are.`);
      }
    }
    if (normal === "STACK" && language === "script") {
      const [size] = constantNumbers(tail);
      if (Number.isFinite(size)) {
        additions.push(size < 4096
          ? `A stack of ${size} bytes is below the AmigaDOS default of 4096 and will crash anything that recurses.`
          : `Commands started after this line run with a ${size}-byte stack.`);
      }
    }
    if (["EXECUTE", "RUN"].includes(normal) && language === "script") {
      const target = tail.trim().split(/\s+/)[0];
      if (target) {
        additions.push(normal === "EXECUTE"
          ? `This runs ${JSON.stringify(target)} as an AmigaDOS script, so its lines are read as commands rather than loaded as code.`
          : `This starts ${JSON.stringify(target)} as a background process; add >NIL: <NIL: so it does not hold this Shell open.`);
      }
    }
    if (["JSR", "JMP"].includes(normal) && M68K_TARGETS.includes(language)) {
      const registers = preceding68000Registers(line, relativeStart);
      const vector = tail.match(/^\s*(-?(?:&|\$|0x)?[0-9a-f]+)\s*\(\s*A6\s*\)/i);
      if (vector) {
        const offset = sourceNumber(vector[1].replace("$", "&"));
        if (offset != null) {
          additions.push(`This is a library call through the base in A6: ${catalogueText(CALL_CATALOGUE?.explainLibraryCall(offset, registers.library, [registers.D0, registers.D1, registers.A0, registers.A1]), targetProfile)}`);
        }
      }
      const absolute = tail.match(/^\s*(?:&|\$)([0-9a-f]+)/i);
      const named = absolute ? SYSTEM_ADDRESS_HELP.get(Number.parseInt(absolute[1], 16)) : null;
      if (named) additions.push(`The destination &${absolute[1].toUpperCase()} is ${named}.`);
    }
    if (["MOVE", "MOVEA", "MOVEQ"].includes(normal) && M68K_TARGETS.includes(language)) {
      const absolute = tail.match(/(?:&|\$)([0-9a-f]+)(?:\.[WL])?\s*(?:,|$)/i);
      const named = absolute ? SYSTEM_ADDRESS_HELP.get(Number.parseInt(absolute[1], 16)) : null;
      if (named) {
        additions.push(named === "ExecBase"
          ? "Absolute address 4 holds ExecBase, so this is how almost every Amiga program reaches exec.library."
          : `Address &${absolute[1].toUpperCase()} is the ${named} hardware register.`);
      }
    }
    if (!additions.length) return base;
    return { ...base, notes: [base.notes, ...additions].filter(Boolean).join(" ") };
  }

  function disassemblyInstructionHelp(row, architecture) {
    const mnemonic = normaliseHelpKey(row?.mnemonic || "DATA") || "DATA";
    const operand = String(row?.operand || "").trim();
    const known = lookup(architecture, mnemonic);
    let summary = known?.summary || `${architecture.toUpperCase()} decoded operation.`;
    if (M68K_TARGETS.includes(architecture) && !ASM_HELP[mnemonic] && !INLINE_ASSEMBLER_HELP[mnemonic]) {
      const base = mnemonic.replace(/\.(?:B|W|L|S)$/, "");
      if (/^MOVE/.test(base)) summary = "Moves the decoded source value to the destination register or memory location.";
      else if (/^LEA$/.test(base)) summary = "Loads the effective address of the operand into an address register.";
      else if (/^PEA$/.test(base)) summary = "Pushes the effective address of the operand onto the stack.";
      else if (/^(ADD|SUB|MUL|DIV|NEG|CMP|EXT|CLR|TST)/.test(base)) summary = "Performs an arithmetic, comparison or size operation using the decoded size and operands.";
      else if (/^(AND|OR|EOR|NOT|BTST|BSET|BCLR|BCHG)/.test(base)) summary = "Performs a logical or bit operation on the decoded destination.";
      else if (/^(AS|LS|RO|ROX|SWAP)/.test(base)) summary = "Shifts, rotates or exchanges halves of the decoded operand.";
      else if (/^(B|DB|S)(?:RA|SR|CC|CS|EQ|F|GE|GT|HI|LE|LS|LT|MI|NE|PL|T|VC|VS)/.test(base)) summary = "Applies the encoded condition to branch, loop or set a result byte.";
      else if (/^(JMP|JSR|RTS|RTE|RTR|LINK|UNLK|MOVEM)$/.test(base)) summary = "Changes control flow, or saves and restores a subroutine's registers and stack frame.";
      else if (/^(TRAP|TRAPV|CHK|STOP|RESET|NOP|ILLEGAL)$/.test(base)) summary = "Invokes a processor exception, or a control operation that is often privileged.";
    }
    let addressing = "No explicit operand; the operation uses implied processor state.";
    if (operand) {
      if (operand.startsWith("#")) addressing = `Immediate operand ${operand}.`;
      else if (/\(a\d\)\+/i.test(operand)) addressing = `Post-increment through ${operand}.`;
      else if (/-\(a\d\)/i.test(operand)) addressing = `Pre-decrement through ${operand}.`;
      else if (/\([^)]*pc[^)]*\)/i.test(operand)) addressing = `Program-counter relative operand ${operand}, so the code is position independent.`;
      else if (/-?\$?[0-9a-f]+\(\s*a6\s*\)/i.test(operand)) addressing = `Library vector ${operand}: the base in A6 decides which library it belongs to.`;
      else if (/\([^)]*a\d[^)]*\)/i.test(operand)) addressing = `Address-register operand ${operand}.`;
      else addressing = `Decoded operand: ${operand}.`;
    }
    const context = [addressing];
    if (row?.comment) context.push(`Analysis: ${row.comment}.`);
    if (Array.isArray(row?.references) && row.references.length) context.push(`Referenced from ${row.references.map(value => `$${Number(value).toString(16).toUpperCase()}`).join(", ")}.`);
    if (row?.bytes) context.push(`Encoding: ${row.bytes}.`);
    return {
      key: mnemonic,
      summary,
      syntax: `${mnemonic}${operand ? ` ${operand}` : ""}`,
      requirements: known?.requirements || `Valid ${architecture.toUpperCase()} code for the selected processor variant.`,
      notes: [known?.notes, ...context].filter(Boolean).join(" "),
    };
  }

  const token = (type, text, start, helpKey = "", helpLanguage = "") => ({ type, text, start, end: start + text.length, helpKey, helpLanguage });

  // Help keys deliberately discard punctuation, but AmigaBASIC's trailing `%`
  // is semantic: it marks an integer variable. Keep lexical classification
  // separate so names such as page%, load% and print% cannot be mistaken for
  // the STACK, LOAD and PRINT commands during highlighting or refactoring.
  const isBasicKeywordToken = (raw, key) => BASIC_LANGUAGE?.isKeywordToken(raw) ?? (!/%$/.test(raw) && BASIC_KEYWORDS.has(key));

  function inlineMnemonic(raw, architecture) {
    const key = normaliseHelpKey(raw);
    if (architecture === "m68k") {
      if (ASSEMBLY_LANGUAGE.isMnemonic("m68k", key)) return key;
      const withoutCondition = key.replace(/(?:EQ|NE|CS|HS|CC|LO|MI|PL|VS|VC|HI|LS|GE|LT|GT|LE|AL)$/, "").replace(/S$/, "");
      return ASSEMBLY_LANGUAGE.isMnemonic("m68k", withoutCondition) ? withoutCondition : "";
    }
    if (architecture === "m68k") {
      const base = key.replace(/\.(?:B|W|L|S)$/, "");
      return ASSEMBLY_LANGUAGE.isMnemonic("m68k", key) || ASSEMBLY_LANGUAGE.isMnemonic("m68k", base) ? key : "";
    }
    return ASSEMBLY_LANGUAGE.isMnemonic(architecture, key) ? key : "";
  }

  function sourceTokens(text, language, inlineAssemblyLanguage = "68000") {
    const tokens = [];
    let lineStart = 0;
    let inlineAssembler = false;
    for (const line of String(text).split("\n")) {
      let offset = 0;
      let assemblerStatementStart = inlineAssembler;
      const number = language === "basic" ? line.match(/^\s*(\d+)/) : null;
      if (number) tokens.push(token("line-number", number[1], lineStart + number.index + number[0].lastIndexOf(number[1])));
      while (offset < line.length) {
        if (language === "basic" && line[offset] === "[") { inlineAssembler = true; assemblerStatementStart = true; offset += 1; continue; }
        if (language === "basic" && inlineAssembler && line[offset] === "]") { inlineAssembler = false; assemblerStatementStart = false; offset += 1; continue; }
        if (language === "basic" && inlineAssembler && line[offset] === "\\") {
          tokens.push(token("comment", line.slice(offset), lineStart + offset));
          break;
        }
        if (line[offset] === '"') {
          let end = offset + 1;
          while (end < line.length) {
            if (line[end] === '"') { end += 1; break; }
            end += 1;
          }
          tokens.push(token("string", line.slice(offset, end), lineStart + offset));
          offset = end;
          continue;
        }
        const remainder = line.slice(offset);
        const basicLexeme = language === "basic" && !inlineAssembler && /^[A-Za-z]/.test(remainder)
          ? BASIC_LANGUAGE?.lexemeAt(remainder)
          : "";
        const word = basicLexeme ? [basicLexeme] : remainder.match(/^(?:[A-Za-z_][A-Za-z0-9_$%.]*|&[0-9A-Fa-f]+|\$[0-9A-Fa-f]+|\d+(?:\.\d+)?)/);
        if (!word) {
          if (inlineAssembler && line[offset] === ":") assemblerStatementStart = true;
          offset += 1;
          continue;
        }
        const raw = word[0];
        const key = normaliseHelpKey(raw);
        if (language === "basic" && !inlineAssembler && key === "REM" && isBasicKeywordToken(raw, key)) {
          tokens.push(token("comment", line.slice(offset), lineStart + offset, "REM"));
          break;
        }
        const isNumber = /^\d|^&/.test(raw);
        if (language === "basic" && inlineAssembler) {
          const mnemonic = /^[A-Za-z]+$/.test(raw) && line[offset - 1] !== "." ? inlineMnemonic(raw, inlineAssemblyLanguage) : "";
          const api = /^_?[A-Za-z][A-Za-z0-9_]*$/.test(raw) && LIBRARY_HELP[key]
            ? key
            : (/^(?:&|\$)[0-9A-F]+$/i.test(raw) ? SYSTEM_ADDRESS_HELP.get(Number.parseInt(raw.slice(1), 16)) : "");
          if (api && inlineAssemblyLanguage === "68000") tokens.push(token("api", raw, lineStart + offset, api, "68000"));
          else if (mnemonic) { tokens.push(token("keyword", raw, lineStart + offset, mnemonic, inlineAssemblyLanguage)); assemblerStatementStart = false; }
          else if (INLINE_ASSEMBLER_HELP[key]) { tokens.push(token("keyword", raw, lineStart + offset, key, inlineAssemblyLanguage)); assemblerStatementStart = false; }
          else if (assemblerStatementStart && /^[A-Za-z]+$/.test(raw) && line[offset - 1] !== ".") {
            tokens.push(token("keyword", raw, lineStart + offset, key, inlineAssemblyLanguage));
            assemblerStatementStart = false;
          }
          else if (isNumber) tokens.push(token("number", raw, lineStart + offset));
          if (/[$%]$/.test(raw) && /^\s*=/.test(line.slice(offset + raw.length))) assemblerStatementStart = false;
          offset += raw.length;
          continue;
        }
        // An AmigaDOS command has no sigil, so a Shell line is recognised by
        // its first word rather than by punctuation.
        const isKeyword = language === "basic"
          ? isBasicKeywordToken(raw, key)
          : language === "script"
            ? (SCRIPT_COMMANDS.has(key) || offset === (line.match(/^\s*/)?.[0].length || 0))
            : false;
        if (isNumber) tokens.push(token("number", raw, lineStart + offset));
        else if (isKeyword) tokens.push(token("keyword", raw, lineStart + offset, key));
        else if (/^(?:SUB|FN)/i.test(raw)) tokens.push(token("symbol", raw, lineStart + offset, raw.match(/^(SUB|FN)/i)?.[1]));
        offset += raw.length;
      }
      lineStart += line.length + 1;
    }
    return tokens.sort((left, right) => left.start - right.start || right.end - left.end);
  }

  function highlightedHtml(text, tokens) {
    let cursor = 0;
    const output = [];
    for (const item of tokens) {
      if (item.start < cursor) continue;
      output.push(esc(text.slice(cursor, item.start)));
      const helpAttributes = item.helpKey ? ` data-help-key="${esc(item.helpKey)}"${item.helpLanguage ? ` data-help-language="${esc(item.helpLanguage)}"` : ""} data-token-start="${item.start}" data-token-end="${item.end}"` : "";
      output.push(`<span class="code-token code-token-${item.type}${item.helpKey ? " code-help-token" : ""}"${helpAttributes}>${esc(item.text)}</span>`);
      cursor = item.end;
    }
    output.push(esc(text.slice(cursor)));
    return `${output.join("")}${text.endsWith("\n") ? "\n" : ""}`;
  }

  function highlightedSourceLines(lines, language, inlineAssemblyLanguage) {
    const source = lines.join("\n");
    const tokens = sourceTokens(source, language, inlineAssemblyLanguage);
    let offset = 0;
    return lines.map(line => {
      const end = offset + line.length;
      const local = tokens.filter(item => item.start >= offset && item.end <= end).map(item => ({
        ...item, start: item.start - offset, end: item.end - offset,
      }));
      const markup = highlightedHtml(line, local);
      offset = end + 1;
      return markup;
    });
  }

  function basicInlineAssemblerLines(text) {
    let inside = false;
    return String(text).split("\n").map(line => {
      let flagged = inside;
      let quoted = false;
      for (let index = 0; index < line.length; index += 1) {
        if (line[index] === '"') {
          if (quoted && line[index + 1] === '"') { index += 1; continue; }
          quoted = !quoted;
          continue;
        }
        if (quoted) continue;
        if (!inside && /^REM(?![$%])/i.test(line.slice(index)) && (index === 0 || /[^A-Za-z0-9_$%]/.test(line[index - 1]))) break;
        if (inside && line[index] === "\\") break;
        if (line[index] === "[") { inside = true; flagged = true; }
        else if (inside && line[index] === "]") { flagged = true; inside = false; }
      }
      return flagged;
    });
  }

  function diagnostics(text, language, dialect = "AmigaBASIC 1.0") {
    const issues = [];
    const add = (severity, line, message, offset = 0) => issues.push({ severity, line, message, offset });
    const lines = String(text).split("\n");
    const lineOffsets = [];
    lines.reduce((offset, line) => { lineOffsets.push(offset); return offset + line.length + 1; }, 0);
    lines.forEach((line, index) => {
      const quotes = (line.match(/"/g) || []).length;
      if (quotes % 2) add("error", index + 1, "String quotation mark is not closed.", lineOffsets[index] + line.indexOf('"'));
      if (language === "script" && /(^|:)\s*[RL]\./i.test(line)) add("warning", index + 1, "R. or L. is filing-system dependent; use RUN or LOAD when moving this script to FFS.", lineOffsets[index]);
      if (language === "script" && /\bCHAIN\s*"!?BOOT"/i.test(line)) add("warning", index + 1, "CHAIN expects tokenised BASIC. A command-script Startup-Sequence normally needs *EXEC.", lineOffsets[index]);
    });
    if (language !== "basic") return issues;
    const numbered = [];
    const lineSet = new Set();
    lines.forEach((line, index) => {
      if (!line.trim()) return;
      const match = line.match(/^\s*(\d+)\s/);
      if (!match) return add("error", index + 1, "AmigaBASIC source lines require a line number followed by a space.", lineOffsets[index]);
      const value = Number(match[1]);
      if (lineSet.has(value)) add("error", index + 1, `Line number ${value} is duplicated.`, lineOffsets[index]);
      lineSet.add(value);
      if (numbered.length && value <= numbered.at(-1).value) add("error", index + 1, `Line ${value} is not greater than the preceding line number.`, lineOffsets[index]);
      numbered.push({ value, index: index + 1, text: line, offset: lineOffsets[index] });
    });
    numbered.forEach(row => {
      for (const match of row.text.matchAll(/\b(?:GOTO|GOSUB|RESTORE)\s+(\d+)/gi)) {
        if (!lineSet.has(Number(match[1]))) add("error", row.index, `Referenced line ${match[1]} does not exist.`, row.offset + match.index);
      }
    });
    // AmigaBASIC names a subprogram with SUB … END SUB and a single-line
    // function with DEF FN, so both are checked for a matching definition.
    const definitions = new Set([...text.matchAll(/\bSUB\s+([A-Za-z][A-Za-z0-9_.]*)/gi)].map(match => match[1].toUpperCase()));
    for (const match of text.matchAll(/\bCALL\s+([A-Za-z][A-Za-z0-9_.]*)/gi)) {
      if (!definitions.has(match[1].toUpperCase())) {
        add("warning", text.slice(0, match.index).split("\n").length, `Subprogram ${match[1]} has no SUB definition in this file.`, match.index);
      }
    }
    const masked = sourceMask(text, language);
    for (const match of text.matchAll(/\bSUB\s+([A-Za-z][A-Za-z0-9_.]*)|\bDEF\s*(FN[A-Za-z][A-Za-z0-9_]*)/gi)) {
      const name = match[1] || match[2];
      const calls = [...masked.matchAll(new RegExp(`\\b${name}\\b`, "gi"))].filter(call => call.index !== match.index);
      if (calls.length <= 1) add("info", text.slice(0, match.index).split("\n").length, `${name.toUpperCase()} is defined but not called in this file.`, match.index);
    }
    const subprogramDefinitions = [...masked.matchAll(/\bSUB\s+[A-Za-z][A-Za-z0-9_.]*/gi)].length;
    const subprogramEnds = [...masked.matchAll(/\bEND\s+SUB\b/gi)].length;
    if (subprogramDefinitions !== subprogramEnds) add("warning", 1, `${subprogramDefinitions} SUB definition${subprogramDefinitions === 1 ? "" : "s"} but ${subprogramEnds} END SUB statement${subprogramEnds === 1 ? "" : "s"} were found.`, 0);
    numbered.forEach((row, index) => {
      if (!/\b(?:END|STOP|GOTO\s*\d+)\s*$/i.test(row.text)) return;
      const next = numbered[index + 1];
      if (next && ![...masked.matchAll(/\b(?:GOTO|GOSUB|RESTORE|THEN)\s*(\d+)/gi)].some(match => Number(match[1]) === next.value)) {
        add("info", next.index, `Line ${next.value} may be unreachable after an unconditional transfer.`, next.offset);
      }
    });
    if (BASIC_LANGUAGE) {
      issues.push(...advancedBasicDiagnostics(text));
      const profile = BASIC_LANGUAGE.dialectProfile(dialect);
      BASIC_LANGUAGE.scan(text).filter(token => token.type === "keyword").forEach(token => {
        const required = Number(BASIC_LANGUAGE.KEYWORD_GENERATION[token.name] || 1);
        if (required > Number(profile.generation || 2)) issues.push({
          severity: "warning", line: token.line, offset: token.start,
          message: `${token.name} needs AmigaBASIC 1.2; this file is ${dialect}.`,
        });
      });
    }
    return issues;
  }

  function advancedBasicDiagnostics(text) {
    const issues = [];
    const scannedTokens = BASIC_LANGUAGE.scan(text);
    const masked = BASIC_LANGUAGE.maskStringsAndComments(text);
    const dimmed = new Set();
    const forStack = [];
    const lineOffsets = [];
    text.split("\n").reduce((offset, line) => { lineOffsets.push(offset); return offset + line.length + 1; }, 0);
    text.split("\n").forEach((line, index) => {
      const lineOffset = lineOffsets[index];
      const code = masked.slice(lineOffset, lineOffset + line.length).replace(/^\s*\d+\s*/, "");
      const lineEnd = lineOffset + line.length;
      const lineTokens = scannedTokens.filter(token => token.start >= lineOffset && token.start < lineEnd);
      for (const [tokenIndex, token] of lineTokens.entries()) {
        if (token.type !== "identifier" || !/^\s*\(/.test(masked.slice(token.end, lineEnd))) continue;
        const name = token.text.toUpperCase();
        const previous = lineTokens[tokenIndex - 1];
        const followsDim = previous?.type === "keyword" && previous.name === "DIM";
        // FNname(...) is an indivisible user symbol in the scanner, not an
        // array. Built-in functions such as TAB(...) are keyword tokens, which
        // also prevents compact PRINT TAB(...) being mistaken for an array
        // reference.
        if (followsDim) dimmed.add(name);
        else if (!/^FN.+/i.test(token.text) && !dimmed.has(name)) {
          issues.push({ severity: "warning", line: index + 1, offset: token.start, message: `${token.text} is used as an array before a preceding DIM was found.` });
        }
      }
      for (const match of code.matchAll(/\bFOR\s*([A-Za-z][A-Za-z0-9_]*[$%]?)/gi)) forStack.push({ name: match[1].toUpperCase(), line: index + 1 });
      for (const match of code.matchAll(/\bNEXT\s*([A-Za-z][A-Za-z0-9_]*[$%]?)/gi)) {
        const active = forStack.pop();
        if (active && active.name !== match[1].toUpperCase()) issues.push({ severity: "warning", line: index + 1, offset: lineOffset + match.index, message: `NEXT ${match[1]} closes the active FOR ${active.name} from line ${active.line}.` });
      }
    });
    // A, A% and A$ are deliberately distinct variables in AmigaBASIC, so
    // sharing a base name across types is not itself suspicious. Likewise, do
    // not report apparently unused assignments: a variable can be read by a
    // SUB, by a CHAINed program that was given it with COMMON, or by machine
    // code reached through CALL, so the absence of a later textual read is not
    // evidence of a defect.
    return issues.slice(0, 500);
  }

  function symbols(text, language) {
    const rows = [];
    if (language === "basic") {
      for (const match of text.matchAll(/^\s*(\d+)\s/gm)) rows.push({ name: `Line ${match[1]}`, kind: "line", offset: match.index });
      for (const match of text.matchAll(/\bSUB\s+([A-Za-z][A-Za-z0-9_.]*)/gi)) rows.push({ name: `SUB ${match[1]}`, kind: "definition", offset: match.index });
      for (const match of text.matchAll(/\bDEF\s*(FN[A-Za-z][A-Za-z0-9_]*)/gi)) rows.push({ name: match[1].toUpperCase(), kind: "definition", offset: match.index });
    } else if (language === "script") {
      for (const match of text.matchAll(/^\s*(CD|ASSIGN|EXECUTE|RUN|PATH|LAB|STACK|MOUNT)\s+([^\r\n]+)/gim)) rows.push({ name: `${match[1].toUpperCase()} ${match[2].trim()}`, kind: "command", offset: match.index });
    }
    return rows.slice(0, 500);
  }

  function identifierAt(text, offset, language) {
    const allowed = language === "basic" ? /[A-Za-z0-9_$%]/ : /[A-Za-z0-9_.$]/;
    let start = Math.max(0, Math.min(offset, text.length));
    let end = start;
    while (start > 0 && allowed.test(text[start - 1])) start -= 1;
    while (end < text.length && allowed.test(text[end])) end += 1;
    const name = text.slice(start, end);
    return /^[A-Za-z_.][A-Za-z0-9_.$%]*$/.test(name) ? { name, start, end } : null;
  }

  function sourceMask(text, language) {
    if (language === "basic" && BASIC_LANGUAGE) return BASIC_LANGUAGE.maskStringsAndComments(text);
    const mask = [...text].map(character => character === "\n" ? "\n" : character);
    let quoted = false;
    for (let index = 0; index < text.length; index += 1) {
      if (text[index] === "\n") { quoted = false; continue; }
      if (text[index] === '"') { mask[index] = " "; quoted = !quoted; continue; }
      if (quoted) { mask[index] = " "; continue; }
      const rest = text.slice(index);
      if ((language === "basic" && /^REM(?![$%])/i.test(rest)) ||
          (language === "script" && /^\|/.test(rest))) {
        while (index < text.length && text[index] !== "\n") { mask[index] = " "; index += 1; }
        index -= 1;
      }
    }
    return mask.join("");
  }

  function symbolReferences(text, offset, language) {
    const selected = identifierAt(text, offset, language);
    if (!selected) return { name: "", rows: [] };
    const masked = sourceMask(text, language);
    const escaped = selected.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const pattern = new RegExp(`(?<![A-Za-z0-9_.$%])${escaped}(?![A-Za-z0-9_.$%])`, "giu");
    const rows = [...masked.matchAll(pattern)].map(match => ({
      offset: match.index,
      line: text.slice(0, match.index).split("\n").length,
      context: text.slice(text.lastIndexOf("\n", match.index - 1) + 1, text.indexOf("\n", match.index) < 0 ? text.length : text.indexOf("\n", match.index)).trim(),
    }));
    return { name: selected.name, rows };
  }

  function basicStructureRows(text) {
    let assembler = false;
    return String(text).split("\n").map(line => {
      const masked = [...line];
      let quoted = false;
      for (let index = 0; index < line.length; index += 1) {
        const character = line[index];
        if (assembler) {
          masked[index] = " ";
          if (character === "\\") { masked.fill(" ", index); break; }
          if (character === "]") assembler = false;
          continue;
        }
        if (character === '"') {
          masked[index] = " ";
          if (quoted && line[index + 1] === '"') { masked[index + 1] = " "; index += 1; continue; }
          quoted = !quoted;
          continue;
        }
        if (quoted) { masked[index] = " "; continue; }
        if (character === "[") { masked[index] = " "; assembler = true; continue; }
        if (/^REM(?![$%])/i.test(line.slice(index)) && (index === 0 || /[^A-Za-z0-9_$%]/.test(line[index - 1]))) {
          masked.fill(" ", index);
          break;
        }
      }
      const code = masked.join("").replace(/^\s*\d+\s*/, "");
      const events = [];
      code.split(":").forEach((part, statementIndex) => {
        const statement = part.trim();
        if (!statement) return;
        const event = { leading: statementIndex === 0, statement };
        let match = statement.match(/^SUB\s+([A-Za-z][A-Za-z0-9_.]*)/i);
        if (match) return events.push({ ...event, kind: "open", type: "subprogram", label: `SUB ${match[1]}` });
        match = statement.match(/^DEF\s*FN([A-Za-z][A-Za-z0-9_]*)/i);
        if (match) {
          const remainder = statement.slice(match[0].length);
          if (!remainder.includes("=")) events.push({ ...event, kind: "open", type: "function", label: `FN${match[1]}` });
          return;
        }
        if (/^END\s+SUB\b/i.test(statement)) return events.push({ ...event, kind: "close", type: "subprogram" });
        if (/^=/.test(statement)) return events.push({ ...event, kind: "close", type: "function" });
        match = statement.match(/^FOR\s*([A-Za-z][A-Za-z0-9_$%!#]*)(?=\s*=)/i);
        if (match) return events.push({ ...event, kind: "open", type: "for", label: `${match[1]} loop` });
        if (/^NEXT(?![$%!#])(?:\b|(?=[A-Za-z]))/i.test(statement)) return events.push({ ...event, kind: "close", type: "for" });
        if (/^IF(?![$%!#])(?:\b|(?=[A-Za-z]))/i.test(statement) && /\bTHEN\s*$/i.test(statement)) return events.push({ ...event, kind: "open", type: "if", label: "IF block" });
        if (/^ELSEIF\b/i.test(statement)) return events.push({ ...event, kind: "branch", type: "if" });
        if (/^ELSE(?![$%!#])/i.test(statement)) return events.push({ ...event, kind: "branch", type: "if" });
        if (/^END\s+IF\b/i.test(statement)) return events.push({ ...event, kind: "close", type: "if" });
        if (/^SELECT\s+CASE\b/i.test(statement)) return events.push({ ...event, kind: "open", type: "select", label: "SELECT CASE block" });
        if (/^CASE(?![$%!#])(?:\b|(?=[A-Za-z]))/i.test(statement)) return events.push({ ...event, kind: "branch", type: "select" });
        if (/^END\s+SELECT\b/i.test(statement)) return events.push({ ...event, kind: "close", type: "select" });
        if (/^WHILE(?![$%!#])(?:\b|(?=[A-Za-z]))/i.test(statement)) return events.push({ ...event, kind: "open", type: "while", label: "WHILE loop" });
        if (/^WEND(?![$%!#])/i.test(statement)) events.push({ ...event, kind: "close", type: "while" });
      });
      return { line, events };
    });
  }

  function foldBlocks(text, language) {
    if (language !== "basic") return [];
    const rows = basicStructureRows(text);
    const offsets = [];
    rows.reduce((offset, row) => { offsets.push(offset); return offset + row.line.length + 1; }, 0);
    const stack = [];
    const blocks = [];
    rows.forEach((row, lineIndex) => row.events.forEach(event => {
      if (event.kind === "open") { stack.push({ ...event, startLine: lineIndex }); return; }
      if (event.kind !== "close") return;
      const stackIndex = stack.findLastIndex(item => item.type === event.type);
      if (stackIndex < 0) return;
      const opener = stack[stackIndex];
      stack.splice(stackIndex);
      if (lineIndex <= opener.startLine) return;
      blocks.push({
        id: `${opener.type}:${opener.startLine}:${lineIndex}`,
        type: opener.type,
        label: opener.label,
        startLine: opener.startLine,
        endLine: lineIndex,
        start: offsets[opener.startLine],
        end: offsets[lineIndex] + rows[lineIndex].line.length,
      });
    }));
    return blocks.sort((left, right) => left.startLine - right.startLine || right.endLine - left.endLine);
  }

  function basicStatements(body) {
    const normal = String(body)
      .replace(/^IF(?![$%])(?=\S)/i, "$& ")
      .replace(/\bTHEN(?![$%])(?=\d)/gi, "$& ")
      .replace(/(\d)ELSE(?![$%])(?=\S)/gi, "$1 ELSE ")
      .replace(/\bELSE(?![$%])(?=[A-Za-z])/gi, "$& ");
    const statements = BASIC_LANGUAGE
      ? BASIC_LANGUAGE.splitStatements(normal).map(statement => statement.text)
      : [normal.trim()].filter(Boolean);
    return statements.map(statement => statement
      .replace(/^IF(?![$%])(?=\S)/i, "$& ")
      .replace(/\bTHEN(?![$%])(?=\S)/gi, "$& ")
      .replace(/(\d)ELSE(?![$%])(?=\S)/gi, "$1 ELSE "));
  }

  function keywordOutsideQuotes(text, keyword, from = 0) {
    let quoted = false;
    const upper = String(text).toUpperCase();
    for (let index = from; index <= text.length - keyword.length; index += 1) {
      if (text[index] === '"') quoted = !quoted;
      if (quoted || upper.slice(index, index + keyword.length) !== keyword) continue;
      const before = index === 0 ? " " : text[index - 1];
      const after = text[index + keyword.length] || " ";
      if (!/[A-Z0-9_$%]/i.test(before) && !/[A-Z0-9_$%]/i.test(after)) return index;
    }
    return -1;
  }

  function inlineIfElseChain(body, nextNumber) {
    if (nextNumber == null || !/^\s*IF(?![$%])/i.test(body)) return null;
    const branches = [];
    let remaining = String(body).trim();
    while (/^IF(?![$%])/i.test(remaining)) {
      const thenAt = keywordOutsideQuotes(remaining, "THEN", 2);
      const elseAt = keywordOutsideQuotes(remaining, "ELSE", thenAt >= 0 ? thenAt + 4 : 2);
      if (elseAt < 0) return null;
      let condition;
      let action;
      if (thenAt >= 0) {
        condition = remaining.slice(2, thenAt).trim();
        action = remaining.slice(thenAt + 4, elseAt).trim();
      } else {
        const beforeElse = remaining.slice(2, elseAt).trim();
        const falseBranch = remaining.slice(elseAt + 4).trim();
        const assignment = falseBranch.match(/^([A-Za-z][A-Za-z0-9_]*[$%]?(?:\([^)]*\))?)\s*=/);
        let actionAt = null;
        if (assignment) {
          actionAt = [...beforeElse.matchAll(/(?:^|\s)([A-Za-z][A-Za-z0-9_]*[$%]?(?:\([^)]*\))?)\s*=/g)].at(-1) || null;
        } else {
          actionAt = [...beforeElse.matchAll(/(?:^|\s)(PRINT|CALL|CHAIN|GOTO|GOSUB|RETURN|RUN|SCREEN|WINDOW|SOUND|WAVE|SAY|LINE|CIRCLE|PSET|PRESET|PAINT|PALETTE|COLOR|CLS|LOCATE|INPUT|READ|RESTORE|ERROR|STOP|END|POKE|POKEW|POKEL|PUT|GET|OPEN|CLOSE|WRITE|KILL|NAME|SLEEP|SYSTEM)\b/gi)].at(-1) || null;
        }
        if (!actionAt) return null;
        const leadingSpace = /^\s/.test(actionAt[0]) ? 1 : 0;
        const boundary = actionAt.index + leadingSpace;
        condition = beforeElse.slice(0, boundary).trim();
        action = beforeElse.slice(boundary).trim();
      }
      if (!condition || !action || /^IF(?![$%])/i.test(action)) return null;
      branches.push({ condition, action });
      remaining = remaining.slice(elseAt + 4).trim();
    }
    if (!branches.length || !remaining) return null;
    const actions = branches.map(branch => basicStatements(branch.action));
    const finalActions = basicStatements(remaining);
    if (actions.some(items => !items.length) || !finalActions.length) return null;
    const starts = [];
    let nextStart = 0;
    actions.forEach(items => {
      starts.push(nextStart);
      nextStart += 1 + items.length + 1;
    });
    const finalStart = nextStart;
    const statements = [];
    branches.forEach(({ condition }, index) => {
      statements.push(`IF NOT(${condition}) THEN {{SELF:${starts[index + 1] ?? finalStart}}}`);
      statements.push(...actions[index]);
      statements.push("GOTO {{END}}");
    });
    statements.push(...finalActions);
    return statements;
  }

  function basicStartsStatement(text) {
    const value = String(text).trimStart();
    if (!value) return false;
    if (/^[A-Za-z][A-Za-z0-9_]*[$%!#]?(?:\([^)]*\))?\s*=/.test(value)) return true;
    // A listing commonly omits the space between a command and its first
    // argument: PRINT"A", COLOR1, CHAINf$ and so on. These are statements, not
    // computed line-number expressions after THEN.
    return /^(?:BEEP|CALL|CHAIN|CIRCLE|CLEAR|CLOSE|CLS|COLOR|DATA|DECLARE|DEF|DIM|ERASE|ERROR|FIELD|FILES|FOR|GET|GOSUB|GOTO|IF|INPUT|KILL|LET|LIBRARY|LINE|LOAD|LOCATE|LSET|MENU|MERGE|MID\$|NAME|NEXT|ON|OPEN|PAINT|PALETTE|PATTERN|POKE|POKEW|POKEL|PRESET|PRINT|PSET|PUT|RANDOMIZE|READ|REM|RESET|RESTORE|RESUME|RETURN|RSET|RUN|SAVE|SAY|SCREEN|SLEEP|SOUND|STOP|SUB|SWAP|SYSTEM|TIMER|WAVE|WEND|WHILE|WIDTH|WINDOW|WRITE)/i.test(value);
  }

  function inlineIfExpansion(statements, nextNumber) {
    const ifIndex = statements.findIndex(statement => /^IF(?![$%])/i.test(statement));
    if (ifIndex < 0) return statements;
    const statement = statements[ifIndex];
    const prefix = statements.slice(0, ifIndex);
    const tail = statements.slice(ifIndex + 1);
    const directElse = statement.match(/^IF(?![$%])\s*(.*?)\s+THEN\s*(\d+)\s+ELSE\s*(.+)$/i);
    if (directElse) {
      const falseAction = /^\d+\s*$/.test(directElse[3]) ? `GOTO ${directElse[3].trim()}` : directElse[3];
      return [...prefix, `IF ${directElse[1]} THEN ${directElse[2]}`, falseAction, ...tail];
    }
    if (nextNumber == null || /\bELSE\b/i.test(statement)) return null;
    let condition = "";
    let action = "";
    const withThen = statement.match(/^IF(?![$%])\s*(.*?)\s+THEN\s*(.+)$/i);
    if (withThen) {
      [, condition, action] = withThen;
    } else {
      // AmigaBASIC permits THEN to be omitted. Only split when the beginning of
      // the consequent is a proven statement command; guessing where an
      // arbitrary assignment starts could change the condition.
      const actionAt = [...statement.matchAll(/\s+/g)]
        .map(space => space.index + space[0].length)
        .find(index => basicStartsStatement(statement.slice(index)));
      if (actionAt != null) {
        condition = statement.slice(2, actionAt).trim();
        action = statement.slice(actionAt).trim();
      } else {
        const assignments = [...statement.matchAll(/\s+([A-Za-z][A-Za-z0-9_]*[$%]?(?:\([^)]*\))?)\s*=/g)];
        const assignment = assignments.at(-1);
        if (!assignment) return null;
        const boundary = assignment.index + 1;
        condition = statement.slice(2, boundary).trim();
        action = statement.slice(boundary).trim();
      }
    }
    if (!condition || !action) return null;
    // A bare number after THEN is a destination, not an executable statement
    // that can be moved onto its own line.
    if (/^\d+\s*$/.test(action)) return null;
    return [...prefix, `IF NOT(${condition}) THEN ${nextNumber}`, action, ...tail];
  }

  function tangledBasicLine(line, nextNumber = null) {
    const match = String(line).match(/^\s*(\d+)\s+(.*)$/);
    if (!match) return null;
    // AmigaBASIC does not accept an empty numbered source line. A colon by
    // itself is the executable no-op used for visual separators, so preserve
    // it exactly instead of producing an invalid blank line.
    if (/^\s*:+\s*$/.test(match[2])) {
      return null;
    }
    // ON ERROR owns the remainder of its physical line. Split it by installing
    // an explicit handler target and a normal-flow jump over the extracted
    // handler; simply putting its colon-separated actions on following lines
    // would execute them immediately and change the program.
    const onError = match[2].trim().match(/^ON\s*ERROR(.*)$/i);
    if (onError) {
      const handler = basicStatements(onError[1]);
      if (nextNumber == null || handler.length < 2) return null;
      return {
        number: Number(match[1]),
        body: match[2],
        statements: ["ON ERROR GOTO {{SELF:2}}", "GOTO {{END}}", ...handler],
      };
    }
    const ifAt = keywordOutsideQuotes(match[2], "IF");
    const prefixText = ifAt > 0 ? match[2].slice(0, ifAt).replace(/:\s*$/, "") : "";
    const prefix = prefixText && !/\bREM(?![$%])/i.test(prefixText) ? basicStatements(prefixText) : [];
    const conditionalChain = ifAt >= 0
      ? inlineIfElseChain(match[2].slice(ifAt), nextNumber)
      : null;
    if (conditionalChain) {
      const shiftedChain = conditionalChain.map(statement => statement.replace(
        /\{\{SELF:(\d+)\}\}/g,
        (_whole, index) => `{{SELF:${Number(index) + prefix.length}}}`,
      ));
      return { number: Number(match[1]), body: match[2], statements: [...prefix, ...shiftedChain] };
    }
    const statements = basicStatements(match[2]);
    if (statements.length < 2 && !/^IF(?![$%])/i.test(statements[0] || "")) return null;
    const expanded = inlineIfExpansion(statements, nextNumber);
    if (!expanded) return null;
    if (statements.length < 2 && expanded.length === statements.length && expanded.every((statement, index) => statement === statements[index])) return null;
    return { number: Number(match[1]), body: match[2], statements: expanded };
  }

  function nextBasicLineNumber(lines, index) {
    for (let following = index + 1; following < lines.length; following += 1) {
      const number = lines[following].match(/^\s*(\d+)\s+/)?.[1];
      if (number != null) return Number(number);
    }
    return null;
  }

  function maskedBasicCode(text) {
    const value = String(text);
    const mask = [...value];
    let quoted = false;
    for (let index = 0; index < value.length; index += 1) {
      if (value[index] === '"') { quoted = !quoted; mask[index] = " "; continue; }
      if (quoted) mask[index] = " ";
    }
    const comment = mask.join("").search(/\bREM(?![$%])/i);
    if (comment >= 0) mask.fill(" ", comment);
    return mask.join("");
  }

  function basicDestinations(text) {
    const mask = maskedBasicCode(text);
    const matches = [];
    const positions = new Set();
    const add = (start, digits) => {
      if (positions.has(start)) return;
      positions.add(start);
      matches.push({ start, end: start + digits.length, target: Number(digits) });
    };
    for (const match of mask.matchAll(/\b(?:GOTO|GOSUB|RESTORE|THEN|RUN)(?![$%])\s*(\d+)\b/gi)) {
      const digits = match[1];
      add(match.index + match[0].lastIndexOf(digits), digits);
    }
    for (const match of mask.matchAll(/\b(?:GOTO|GOSUB)(?![$%])\s+(\d+(?:\s*,\s*\d+)+)/gi)) {
      const listAt = match.index + match[0].indexOf(match[1]);
      for (const number of match[1].matchAll(/\d+/g)) add(listAt + number.index, number[0]);
    }
    return matches.sort((left, right) => left.start - right.start);
  }

  function rewriteBasicDestinations(text, numbers) {
    let updated = String(text);
    basicDestinations(updated).reverse().forEach(match => {
      const replacement = String(numbers.get(match.target) ?? match.target);
      updated = `${updated.slice(0, match.start)}${replacement}${updated.slice(match.end)}`;
    });
    return updated;
  }

  function basicHasDynamicDestination(text) {
    const mask = maskedBasicCode(text);
    for (const match of mask.matchAll(/\b(GOTO|GOSUB|RESTORE|RUN)(?![$%])/gi)) {
      const remainder = mask.slice(match.index + match[0].length).split(":", 1)[0].trimStart();
      if (!remainder && /^(RESTORE|RUN)$/i.test(match[1])) continue;
      if (!/^\d+\b/.test(remainder)) return true;
    }
    for (const match of mask.matchAll(/\bTHEN(?![$%])/gi)) {
      const remainder = mask.slice(match.index + match[0].length).split(/\bELSE\b|:/i, 1)[0].trim();
      if (!remainder || /^\d+\b/.test(remainder)) continue;
      if (basicStartsStatement(remainder)) continue;
      if (/^[A-Za-z][A-Za-z0-9_]*[$%]?(?:\([^)]*\))?\s*=/.test(remainder)) continue;
      return true;
    }
    return false;
  }

  function basicHasSemanticErl(text) {
    const code = maskedBasicCode(text);
    if (!/\bERL(?![$%])/i.test(code)) return false;
    // Merely printing ERL reports the newly assigned physical line number and
    // remains correct after a refactor. Assignments, comparisons and other
    // uses can make program behaviour depend on the old number and must keep
    // the conservative safety stop.
    return basicStatements(code).some(statement => (
      /\bERL(?![$%])/i.test(statement)
      && !/^\s*PRINT(?=\s|['";,(]|$)/i.test(statement)
    ));
  }

  function basicCondenseBoundaryBefore(body) {
    return /^\s*(?:ELSE|WHEN|OTHERWISE|ENDIF|ENDCASE|ENDWHILE)(?![$%])/i.test(maskedBasicCode(body));
  }

  function basicCondenseBoundaryAfter(body) {
    const mask = maskedBasicCode(body);
    if (/\bIF(?![$%])/i.test(mask) || /^\s*ON\s+ERROR(?![$%])/i.test(mask)) return true;
    if (String(body).replace(/"(?:[^"]|"")*"/g, "").match(/\bREM(?![$%])/i)) return true;
    const finalStatement = basicStatements(body).at(-1) || "";
    return /^(?:GOTO|RETURN|END|STOP|CHAIN|RUN|ERROR|RESUME|SYSTEM)(?![$%!#])/i.test(finalStatement);
  }

  function rebuildBasic(lines, expansions, { startAt = null, fromIndex = 0, step = 10 } = {}) {
    const sourceRows = lines.map((line, index) => {
      const match = line.match(/^\s*(\d+)\s+(.*)$/);
      return match ? { index, old: Number(match[1]), bodies: expansions.get(index) || [match[2]] } : { index, old: null, bodies: [line] };
    });
    const map = new Map();
    let next = startAt;
    for (const row of sourceRows) {
      if (row.old == null) continue;
      const assigned = next == null || row.index < fromIndex ? row.old : next;
      map.set(row.old, assigned);
      if (next != null && row.index >= fromIndex) next += row.bodies.length * step;
    }
    return sourceRows.flatMap(row => {
      if (row.old == null) return row.bodies;
      let number = map.get(row.old);
      return row.bodies.map((body, bodyIndex) => {
        const rewritten = rewriteBasicDestinations(body, map)
          .replace(/\{\{SELF:(\d+)\}\}/g, (_whole, index) => String(map.get(row.old) + Number(index) * step))
          .replace(/\{\{END\}\}/g, () => String(map.get(nextBasicLineNumber(lines, row.index))));
        const result = `${number} ${rewritten}`;
        number += step;
        return result;
      });
    });
  }

  function normaliseBasicControlSpacing(line) {
    const match = String(line).match(/^(\s*\d+\s+)(.*)$/);
    if (!match || /^\s*:+\s*$/.test(match[2])) return line;
    // Detokenised listings often join a structural keyword directly to its
    // expression or loop variable. At statement start these forms are
    // unambiguous and a separating space materially improves readability.
    const body = match[2].replace(/^\s*(IF|FOR|NEXT|WHILE|WEND|CASE|SUB)(?![$%!#])(?=\S)/i, (_whole, keyword) => `${keyword} `);
    return `${match[1]}${body}`;
  }

  const languageName = language => ({ basic: "AmigaBASIC", script: "AmigaDOS script", text: "plain text", "68000": "MC68000 assembly", "68010": "MC68010 assembly", "68020": "MC68020 assembly", "68030": "MC68030 assembly", "68040": "MC68040 assembly", "68060": "MC68060 assembly", m68k: "MC68000 assembly" }[language] || language);

  function helpMarkup(item) {
    if (!item) return '<p class="code-empty-message">No built-in help is available for that token.</p>';
    return `<article class="code-help-detail"><h3>${esc(item.key)}</h3><p>${esc(item.summary)}</p><dl><dt>Syntax</dt><dd><code>${esc(item.syntax)}</code></dd><dt>Requirements</dt><dd>${esc(item.requirements)}</dd>${item.notes ? `<dt>Watch for</dt><dd>${esc(item.notes)}</dd>` : ""}</dl></article>`;
  }

  let hoverHelpRequest = 0;
  let lastHoverPointerMove = 0;
  let hoverHelpListenersInstalled = false;

  function dismissHoverHelp(owner = document) {
    hoverHelpRequest += 1;
    owner.querySelectorAll(".code-hover-help").forEach(node => node.remove());
    owner.querySelectorAll('[aria-describedby^="code-help-"]').forEach(node => node.removeAttribute("aria-describedby"));
  }

  function installHoverHelpDismissal(owner = document) {
    if (hoverHelpListenersInstalled) return;
    hoverHelpListenersInstalled = true;
    owner.addEventListener("pointermove", () => { lastHoverPointerMove = performance.now(); }, { passive: true });
    owner.addEventListener("pointerdown", () => dismissHoverHelp(owner), true);
    owner.addEventListener("scroll", () => dismissHoverHelp(owner), { capture: true, passive: true });
    owner.addEventListener("keydown", event => { if (event.key === "Escape") dismissHoverHelp(owner); }, true);
    owner.addEventListener("visibilitychange", () => dismissHoverHelp(owner));
    window.addEventListener("blur", () => dismissHoverHelp(owner));
  }

  function attachTooltip(root, language, element, key, suppliedItem = null) {
    const item = suppliedItem || lookup(language, key);
    if (!item) return;
    installHoverHelpDismissal(element.ownerDocument);
    let tooltip = null;
    let showTimer = null;
    const hide = () => {
      clearTimeout(showTimer);
      showTimer = null;
      tooltip?.remove();
      tooltip = null;
      element.removeAttribute("aria-describedby");
    };
    element.addEventListener("mouseenter", () => {
      const owner = element.ownerDocument;
      dismissHoverHelp(owner);
      const request = hoverHelpRequest;
      showTimer = setTimeout(() => {
        if (request !== hoverHelpRequest || !element.isConnected || !element.matches(":hover")) return;
        // Replacing highlighted source beneath a stationary pointer must not
        // manufacture a tooltip. A recent real pointer movement identifies a
        // deliberate hover over the token.
        if (performance.now() - lastHoverPointerMove > 750) return;
        tooltip = owner.createElement("div");
        tooltip.className = "code-hover-help";
        tooltip.id = `code-help-${Math.random().toString(36).slice(2)}`;
        tooltip.setAttribute("role", "tooltip");
        tooltip.innerHTML = `<strong>${esc(item.key)}</strong><span>${esc(item.summary)}</span><dl><dt>Syntax</dt><dd><code>${esc(item.syntax)}</code></dd><dt>Requirements</dt><dd>${esc(item.requirements)}</dd>${item.notes ? `<dt>Watch for</dt><dd>${esc(item.notes)}</dd>` : ""}</dl>`;
        // Native dialogs occupy the browser's top layer. Keep the tooltip in
        // the active dialog so it is painted above the editor.
        (root.closest("dialog") || owner.body).append(tooltip);
        const tokenRect = element.getBoundingClientRect();
        const tipRect = tooltip.getBoundingClientRect();
        tooltip.style.left = `${Math.max(8, Math.min(tokenRect.left, window.innerWidth - tipRect.width - 8))}px`;
        tooltip.style.top = `${tokenRect.bottom + tipRect.height + 8 < window.innerHeight ? tokenRect.bottom + 7 : Math.max(8, tokenRect.top - tipRect.height - 7)}px`;
        element.setAttribute("aria-describedby", tooltip.id);
      }, 300);
    });
    element.addEventListener("mouseleave", () => { hide(); dismissHoverHelp(element.ownerDocument); });
    element.addEventListener("focusout", () => { hide(); dismissHoverHelp(element.ownerDocument); });
    root.addEventListener("code-editor-destroy", hide, { once: true });
  }

  function enhance({ textarea, root, language = "text", dialect = "AmigaBASIC 1.0", inlineAssemblyLanguage = "68000", validateBasic = null, packBasic = null, initialHistory = [], targetProfile = {} }) {
    if (!textarea || !root || textarea.closest(".code-editor-surface")) return null;
    const surface = document.createElement("div");
    surface.className = "code-editor-surface";
    const visual = document.createElement("div");
    visual.className = "code-highlight-layer";
    visual.setAttribute("aria-hidden", "true");
    visual.innerHTML = "<pre></pre>";
    const hit = document.createElement("div");
    hit.className = "code-hit-layer";
    hit.setAttribute("aria-hidden", "true");
    hit.innerHTML = "<pre></pre>";
    const guides = document.createElement("div");
    guides.className = "code-structure-guides";
    guides.setAttribute("aria-hidden", "true");
    const gutter = document.createElement("div");
    gutter.className = "code-fold-gutter";
    gutter.setAttribute("aria-label", "Code folding controls");
    const foldView = document.createElement("div");
    foldView.className = "code-fold-view";
    foldView.hidden = true;
    foldView.setAttribute("aria-label", "Collapsed code outline. Double-click a visible line to expand all blocks and edit it.");
    textarea.before(surface);
    surface.append(gutter, guides, visual, textarea, hit, foldView);
    const drawer = document.createElement("section");
    drawer.className = "code-intelligence-drawer";
    drawer.hidden = true;
    root.insertBefore(drawer, root.querySelector(".editor-status"));
    let state = { tokens: [], issues: [], symbols: [], blocks: [] };
    const collapsedBlocks = new Set();
    let structureGuides = language === "basic" ? { size: 4 } : null;
    let refactorPlan = null;
    let timer = null;
    const refactorUndo = [];
    const refactorRedo = [];
    const editorHistory = Array.isArray(initialHistory) ? initialHistory.slice(-200) : [];
    const pendingHistory = [];

    const historyEntry = (action, detail = "") => {
      const entry = { time: new Date().toISOString(), action, detail };
      editorHistory.push(entry);
      pendingHistory.push(entry);
      if (editorHistory.length > 200) editorHistory.shift();
    };

    const syncScroll = () => {
      for (const layer of [visual, hit, gutter, guides]) {
        layer.scrollTop = textarea.scrollTop;
        layer.scrollLeft = textarea.scrollLeft;
      }
    };
    const foldButtonMarkup = block => `<button type="button" class="code-fold-toggle" data-fold-id="${esc(block.id)}" aria-expanded="${collapsedBlocks.has(block.id) ? "false" : "true"}" title="${collapsedBlocks.has(block.id) ? "Expand" : "Collapse"} ${esc(block.label)}">${collapsedBlocks.has(block.id) ? "+" : "−"}</button>`;
    const blockStartingOn = lineIndex => state.blocks.find(block => block.startLine === lineIndex);
    const bindFoldButtons = host => host.querySelectorAll("[data-fold-id]").forEach(button => {
      button.onclick = event => {
        event.stopPropagation();
        const id = button.dataset.foldId;
        if (collapsedBlocks.has(id)) collapsedBlocks.delete(id);
        else collapsedBlocks.add(id);
        renderFolds();
      };
    });
    const renderFolds = () => {
      dismissHoverHelp(textarea.ownerDocument);
      if (refactorPlan) {
        const rendered = refactorPlan.preview;
        const renderedMarkup = highlightedSourceLines(rendered, language, inlineAssemblyLanguage);
        const original = refactorPlan.before || [];
        const originalMarkup = highlightedSourceLines(original, language, inlineAssemblyLanguage);
        surface.classList.add("code-editor-folded");
        foldView.hidden = false;
        gutter.innerHTML = "";
        const operation = refactorPlan.mode === "condense" ? "condensation" : "refactor";
        const maximum = Math.max(original.length, rendered.length);
        const reviewRows = Array.from({ length: maximum }, (_unused, index) => {
          const before = original[index] ?? "";
          const after = rendered[index] ?? "";
          const changed = before !== after;
          return `<div class="code-transform-row${changed ? " changed" : ""}"><span>${index + 1}</span><pre>${originalMarkup[index] || " "}</pre><pre>${renderedMarkup[index] || " "}</pre></div>`;
        }).join("");
        const verification = refactorPlan.verification;
        foldView.innerHTML = `<section class="code-transform-review"><header><div><strong>Original</strong><small>${original.length} lines</small></div><div><strong>Proposed ${operation}</strong><small>${rendered.length} lines</small></div><div class="code-transform-actions"><button type="button" class="code-untangle-cancel" title="Cancel without changing the program">Cancel</button><button type="button" class="code-untangle-commit" title="Accept the proposed ${operation}">Accept</button></div></header>${verification ? `<p class="code-transform-verification ${verification.roundTripExact ? "pass" : "warn"}">${verification.roundTripExact ? "✓ Exact BASIC token round trip" : "! Round-trip warning"} · ${Number(verification.byteLength || 0).toLocaleString()} tokenised bytes · ${Number(verification.lineCount || 0).toLocaleString()} lines</p>` : ""}<div class="code-transform-columns"><span></span><strong>Before</strong><strong>After</strong></div>${reviewRows}</section>`;
        foldView.querySelector(".code-untangle-commit").onclick = () => commitRefactor();
        foldView.querySelector(".code-untangle-cancel").onclick = () => cancelRefactor();
        return;
      }
      const validIds = new Set(state.blocks.map(block => block.id));
      [...collapsedBlocks].forEach(id => { if (!validIds.has(id)) collapsedBlocks.delete(id); });
      const canFold = state.blocks.length > 0;
      const foldAll = root.querySelector('[data-editor-action="fold-toggle-all"]');
      if (foldAll) {
        foldAll.disabled = !canFold;
        foldAll.querySelector("span").textContent = collapsedBlocks.size ? "Expand all blocks" : "Collapse all blocks";
      }
      const guideToggle = root.querySelector('[data-editor-action="structure-guides"] span');
      if (guideToggle) guideToggle.textContent = structureGuides ? "Hide structure guides" : "Show structure guides";
      const lines = textarea.value.split("\n");
      const displayLines = lines;
      const displayMarkup = highlightedSourceLines(displayLines, language, inlineAssemblyLanguage);
      gutter.innerHTML = `<div>${lines.map((_line, index) => `<span>${blockStartingOn(index) ? foldButtonMarkup(blockStartingOn(index)) : ""}</span>`).join("")}</div>`;
      bindFoldButtons(gutter);
      const outlined = collapsedBlocks.size > 0;
      surface.classList.toggle("code-editor-folded", outlined);
      foldView.hidden = !outlined;
      if (!outlined) return;
      const offsets = [];
      lines.reduce((offset, line) => { offsets.push(offset); return offset + line.length + 1; }, 0);
      const visibleRows = [];
      lines.forEach((line, lineIndex) => {
        const hidingBlock = state.blocks.find(block => collapsedBlocks.has(block.id) && lineIndex > block.startLine && lineIndex <= block.endLine);
        if (hidingBlock) return;
        const block = blockStartingOn(lineIndex);
        const hiddenCount = block && collapsedBlocks.has(block.id) ? block.endLine - block.startLine : 0;
        const displayLine = displayLines[lineIndex] ?? line;
        visibleRows.push(`<div class="code-fold-row" data-code-offset="${offsets[lineIndex]}"><span class="code-fold-row-gutter">${block ? foldButtonMarkup(block) : ""}</span><pre>${displayMarkup[lineIndex]}</pre>${hiddenCount ? `<small>${hiddenCount.toLocaleString()} line${hiddenCount === 1 ? "" : "s"} folded</small>` : ""}</div>`);
      });
      foldView.innerHTML = visibleRows.join("");
      bindFoldButtons(foldView);
      foldView.querySelectorAll(".code-help-token").forEach(element => attachTooltip(root, element.dataset.helpLanguage || language, element, element.dataset.helpKey));
      foldView.querySelectorAll(".code-fold-row").forEach(row => row.ondblclick = event => {
        if (event.target.closest("button")) return;
        const offset = Number(row.dataset.codeOffset);
        showOriginalView();
        goTo(offset);
      });
      foldView.scrollTop = Math.min(foldView.scrollHeight, textarea.scrollTop);
    };
    const expandAll = () => {
      if (!collapsedBlocks.size) return;
      collapsedBlocks.clear();
      renderFolds();
      textarea.focus();
    };
    const collapseAll = () => {
      state.blocks.forEach(block => collapsedBlocks.add(block.id));
      renderFolds();
    };
    const toggleAll = () => collapsedBlocks.size ? expandAll() : collapseAll();
    const renderStructureGuides = () => {
      if (!structureGuides || language !== "basic") {
        guides.replaceChildren();
        guides.hidden = true;
        return;
      }
      guides.hidden = false;
      const lines = textarea.value.split("\n");
      const cursorLine = textarea.value.slice(0, textarea.selectionStart).split("\n").length - 1;
      const active = state.blocks
        .filter(block => cursorLine >= block.startLine && cursorLine <= block.endLine)
        .sort((left, right) => (left.endLine - left.startLine) - (right.endLine - right.startLine))[0];
      const size = [2, 4, 8].includes(Number(structureGuides.size)) ? Number(structureGuides.size) : 4;
      const guideWidth = Math.max(textarea.scrollWidth, textarea.clientWidth);
      guides.style.setProperty("--structure-guide-step", `${size}ch`);
      guides.innerHTML = lines.map((_line, lineIndex) => {
        const depth = state.blocks.filter(block => lineIndex > block.startLine && lineIndex <= block.endLine).length;
        const activeLine = active && lineIndex >= active.startLine && lineIndex <= active.endLine;
        const bars = Array.from({ length: depth }, (_unused, index) => `<i style="--guide-index:${index}"></i>`).join("");
        return `<span class="${activeLine ? "active" : ""}" style="width:${guideWidth}px">${bars}</span>`;
      }).join("");
      guides.scrollTop = textarea.scrollTop;
      guides.scrollLeft = textarea.scrollLeft;
    };
    const toggleStructureGuides = size => {
      structureGuides = structureGuides ? null : { size: Number(size) };
      renderStructureGuides();
      renderFolds();
    };
    const setStructureGuideSize = size => {
      if (!structureGuides) structureGuides = { size: Number(size) };
      else structureGuides.size = Number(size);
      renderStructureGuides();
    };
    const showOriginalView = () => {
      refactorPlan = null;
      collapsedBlocks.clear();
      renderFolds();
      textarea.focus();
    };
    const goTo = offset => {
      if (collapsedBlocks.size) {
        collapsedBlocks.clear();
        renderFolds();
      }
      textarea.focus();
      textarea.setSelectionRange(offset, offset);
      const before = textarea.value.slice(0, offset).split("\n");
      textarea.scrollTop = Math.max(0, (before.length - 3) * parseFloat(getComputedStyle(textarea).lineHeight || "16"));
      syncScroll();
      textarea.dispatchEvent(new Event("click", { bubbles: true }));
    };
    const closeDrawer = () => { drawer.hidden = true; };
    const renderDrawer = (title, body) => {
      drawer.hidden = false;
      drawer.innerHTML = `<header><div><small>CODE-AWARE HELP</small><h3>${esc(title)}</h3></div><button type="button" class="code-drawer-close" aria-label="Close code help">×</button></header><div class="code-drawer-body">${body}</div>`;
      drawer.querySelector(".code-drawer-close").onclick = closeDrawer;
      drawer.querySelectorAll("[data-code-offset]").forEach(button => button.onclick = () => goTo(Number(button.dataset.codeOffset)));
      drawer.querySelectorAll("[data-code-help]").forEach(button => button.onclick = () => renderDrawer(button.dataset.codeHelp, helpMarkup(lookup(language, button.dataset.codeHelp))));
      drawer.querySelectorAll("[data-code-completion]").forEach(button => button.onclick = () => {
        const selected = identifierAt(textarea.value, textarea.selectionStart, language);
        const start = selected?.start ?? textarea.selectionStart;
        const end = selected?.end ?? textarea.selectionEnd;
        const value = button.dataset.codeCompletion;
        textarea.setRangeText(value, start, end, "end");
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
        closeDrawer();
        textarea.focus();
      });
      drawer.querySelectorAll("[data-code-snippet]").forEach(button => button.onclick = () => {
        const value = button.dataset.codeSnippet;
        textarea.setRangeText(value, textarea.selectionStart, textarea.selectionEnd, "end");
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
        closeDrawer();
        textarea.focus();
      });
      const filter = drawer.querySelector("[data-code-reference-filter]");
      if (filter) filter.oninput = () => drawer.querySelectorAll("[data-code-help]").forEach(button => button.hidden = !button.textContent.toLowerCase().includes(filter.value.toLowerCase()));
    };
    const showCustom = (title, body) => renderDrawer(title, body);
    const overview = () => {
      const recognised = [...new Set(state.tokens.map(item => item.helpKey).filter(Boolean))];
      const profile = BASIC_LANGUAGE?.dialectProfile(dialect);
      renderDrawer(`${languageName(language)} overview`, `<div class="code-overview"><p>This file contains <strong>${textarea.value.split("\n").length.toLocaleString()} lines</strong>, <strong>${state.symbols.length.toLocaleString()} navigable symbols</strong> and <strong>${state.issues.length.toLocaleString()} diagnostics</strong>.</p><p>${language === "basic" ? `Detected dialect: <strong>${esc(dialect)}</strong>. Numbered source is tokenised when saved. ${profile ? `Its inline assembler targets ${esc(profile.processor)} and ${profile.structured ? "supports" : "predates"} structured CASE/WHILE syntax.` : ""} Line destinations and local procedure definitions are checked while you type.` : language === "script" ? "Commands are executed in order by *EXEC or the boot process. Filing-system dependencies and ambiguous OFS abbreviations are highlighted." : "Readable text is preserved as Latin-1. Syntax-specific checks are intentionally not imposed."}</p>${recognised.length ? `<h4>Commands used in this file</h4><div class="code-command-chips">${recognised.map(key => `<button type="button" data-code-help="${esc(key)}">${esc(key)}</button>`).join("")}</div>` : ""}</div>`);
    };
    const helpAtCursor = () => {
      const offset = textarea.selectionStart;
      const lineStart = textarea.value.lastIndexOf("\n", Math.max(0, offset - 1)) + 1;
      const found = state.tokens.find(item => item.start <= offset && item.end >= offset && item.helpKey)
        || state.tokens.filter(item => item.start >= lineStart && item.end <= offset && item.helpKey).at(-1);
      const item = found ? sourceContextHelp(textarea.value, found.helpLanguage || language, found.start, found.end, found.helpKey, targetProfile) : null;
      renderDrawer(item?.key || "Help at cursor", helpMarkup(item));
    };
    const showProblems = () => renderDrawer("Problems", state.issues.length ? `<div class="code-problem-list">${state.issues.map(item => `<button type="button" data-code-offset="${item.offset}"><b class="${esc(item.severity)}">${esc(item.severity)}</b><span>Line ${item.line}: ${esc(item.message)}</span></button>`).join("")}</div>` : '<p class="code-empty-message">No problems were found by the live checks.</p>');
    const showSymbols = () => renderDrawer("Document symbols", state.symbols.length ? `<div class="code-symbol-list">${state.symbols.map(item => `<button type="button" data-code-offset="${item.offset}"><b>${esc(item.kind)}</b><span>${esc(item.name)}</span></button>`).join("")}</div>` : '<p class="code-empty-message">No navigable symbols were found in this file.</p>');
    const showCompletions = () => {
      const selected = identifierAt(textarea.value, textarea.selectionStart, language);
      const prefix = String(selected?.name || "").toUpperCase();
      const identifiers = language === "basic" && BASIC_LANGUAGE
        ? BASIC_LANGUAGE.scan(textarea.value).filter(item => item.type === "identifier").map(item => item.text)
        : [];
      const commands = language === "basic" ? [...BASIC_KEYWORDS] : language === "script" ? [...SCRIPT_COMMANDS] : [];
      const candidates = [...new Set([...commands, ...identifiers, ...state.symbols.map(item => item.name.replace(/^Line\s+/, ""))])]
        .filter(value => !prefix || value.toUpperCase().startsWith(prefix))
        .sort((left, right) => left.localeCompare(right)).slice(0, 200);
      const snippets = language === "basic" ? [
        ["FOR loop", "FOR i%=1 TO 10:NEXT i%"], ["WHILE loop", "WHILE condition:WEND"],
        ["Conditional", "IF condition THEN statement"], ["Subprogram", "SUB name STATIC:END SUB"],
        ["Open a library", 'LIBRARY "graphics.library"'],
      ] : language === "script" ? [
        ["Set the stack", "Stack 8192"], ["Run a script", "Execute Startup-Sequence"],
        ["Start in the background", "Run >NIL: <NIL: program"],
        ["Change directory", "CD Games"], ["Make an assignment", "Assign MENU: SYS:"],
      ] : [];
      renderDrawer("Completion and snippets", `<p class="code-empty-message">${prefix ? `Candidates beginning with ${esc(prefix)}.` : "Choose a known command, identifier or template."}</p><div class="code-completion-list">${candidates.map(value => `<button type="button" data-code-completion="${esc(value)}">${esc(value)}</button>`).join("") || "<small>No matching candidates.</small>"}</div>${snippets.length ? `<h4 class="code-drawer-section-title">Templates</h4><div class="code-snippet-list">${snippets.map(([label, value]) => `<button type="button" data-code-snippet="${esc(value)}"><b>${esc(label)}</b><code>${esc(value)}</code></button>`).join("")}</div>` : ""}`);
    };
    const formatCode = async () => {
      if (textarea.readOnly) return false;
      const hasSelection = textarea.selectionStart !== textarea.selectionEnd;
      const lineStart = textarea.value.lastIndexOf("\n", Math.max(0, textarea.selectionStart - 1)) + 1;
      const lineEndAt = textarea.value.indexOf("\n", textarea.selectionEnd);
      const rangeStart = hasSelection ? lineStart : 0;
      const rangeEnd = hasSelection ? (lineEndAt < 0 ? textarea.value.length : lineEndAt) : textarea.value.length;
      const original = textarea.value.slice(rangeStart, rangeEnd);
      const formatted = original.split("\n").map(line => {
        let updated = line.replace(/[ \t]+$/g, "");
        if (language === "basic") updated = normaliseBasicControlSpacing(updated.replace(/^\s*(\d+)\s*/, "$1 "));
        if (language === "script") updated = updated.replace(/^\s*\*\s*/, "*");
        return updated;
      }).join("\n");
      if (formatted === original) {
        showCustom("Format source", '<p class="code-empty-message">The selected source already follows the conservative formatter rules.</p>');
        return false;
      }
      const candidate = `${textarea.value.slice(0, rangeStart)}${formatted}${textarea.value.slice(rangeEnd)}`;
      if (language === "basic" && validateBasic) {
        const check = await validateBasic(candidate, textarea.value);
        if (!check.roundTrip) {
          showCustom("Format source", `<p class="code-empty-message">Formatting was not applied because the BASIC token round trip failed: ${esc(check.message || "unknown error")}</p>`);
          return false;
        }
      }
      if (!window.confirm(`Apply conservative whitespace formatting to ${hasSelection ? "the selected lines" : "the complete file"}?`)) return false;
      const before = documentSnapshot();
      const selectionEnd = rangeStart + formatted.length;
      const after = { value: candidate, selectionStart: rangeStart, selectionEnd, scrollTop: textarea.scrollTop, scrollLeft: textarea.scrollLeft };
      refactorUndo.push({ before, after });
      refactorRedo.length = 0;
      applyDocumentSnapshot(after);
      historyEntry("Formatted source", hasSelection ? "Selected lines" : "Complete file");
      return true;
    };
    const findReferences = () => {
      const result = symbolReferences(textarea.value, textarea.selectionStart, language);
      renderDrawer(result.name ? `References to ${result.name}` : "Find all references", result.rows.length
        ? `<p>${result.rows.length.toLocaleString()} code occurrence${result.rows.length === 1 ? "" : "s"}; strings and comments are excluded.</p><div class="code-reference-results">${result.rows.map(row => `<button type="button" data-code-offset="${row.offset}"><b>Line ${row.line}</b><code>${esc(row.context)}</code></button>`).join("")}</div>`
        : '<p class="code-empty-message">Place the cursor on a symbol or variable to find its references.</p>');
    };
    const renameSymbol = () => {
      if (textarea.readOnly) return;
      const result = symbolReferences(textarea.value, textarea.selectionStart, language);
      if (!result.name || !result.rows.length) return window.alert("Place the cursor on a symbol or variable first.");
      if (language === "basic" && BASIC_KEYWORDS.has(result.name.toUpperCase())) return window.alert("AmigaBASIC commands cannot be renamed.");
      const replacement = window.prompt(`Rename ${result.rows.length} code occurrence${result.rows.length === 1 ? "" : "s"} of ${result.name} to:`, result.name);
      if (!replacement || replacement === result.name || !/^[A-Za-z_.][A-Za-z0-9_.$%]*$/.test(replacement)) return;
      if (!window.confirm(`Rename ${result.rows.length} code occurrence${result.rows.length === 1 ? "" : "s"} of ${result.name} to ${replacement}? Text inside strings and comments will not change.`)) return;
      const before = documentSnapshot();
      let updated = textarea.value;
      [...result.rows].reverse().forEach(row => { updated = `${updated.slice(0, row.offset)}${replacement}${updated.slice(row.offset + result.name.length)}`; });
      const cursor = Math.min(updated.length, textarea.selectionStart + replacement.length - result.name.length);
      const after = { value: updated, selectionStart: cursor, selectionEnd: cursor, scrollTop: textarea.scrollTop, scrollLeft: textarea.scrollLeft };
      refactorUndo.push({ before, after });
      refactorRedo.length = 0;
      applyDocumentSnapshot(after);
      historyEntry("Renamed symbol", `${result.name} → ${replacement}; ${result.rows.length} references`);
    };
    const showOutline = () => {
      if (language !== "basic") return showSymbols();
      const definitions = [...textarea.value.matchAll(/\bSUB\s+([A-Za-z][A-Za-z0-9_.]*)|\bDEF\s*(FN[A-Za-z][A-Za-z0-9_]*)/gi)].map(match => ({
        name: (match[1] || match[2]).toUpperCase(), offset: match.index,
        calls: [...sourceMask(textarea.value, language).matchAll(new RegExp(`\\b${match[1] || match[2]}\\b`, "gi"))].filter(call => call.index !== match.index),
      }));
      renderDrawer("Program outline and call graph", definitions.length
        ? `<div class="code-outline-list">${definitions.map(item => `<article><button type="button" data-code-offset="${item.offset}"><b>${esc(item.name)}</b><span>${item.calls.length} call${item.calls.length === 1 ? "" : "s"}</span></button>${item.calls.map(call => `<button type="button" data-code-offset="${call.index}">Called at physical line ${textarea.value.slice(0, call.index).split("\n").length}</button>`).join("")}</article>`).join("")}</div>`
        : '<p class="code-empty-message">No subprograms or functions were defined in this file.</p>');
    };
    const showHistory = () => renderDrawer("Editor history", editorHistory.length
      ? `<div class="code-history-list">${[...editorHistory].reverse().map(item => `<article><time>${esc(new Date(item.time).toLocaleTimeString())}</time><b>${esc(item.action)}</b><span>${esc(item.detail)}</span></article>`).join("")}</div>`
      : '<p class="code-empty-message">No transformations or symbol changes have been made in this editor window.</p>');
    const compareWith = baseline => {
      const before = String(baseline || "").split("\n");
      const after = textarea.value.split("\n");
      const maximum = Math.max(before.length, after.length);
      const rows = Array.from({ length: maximum }, (_unused, index) => ({ before: before[index] ?? "", after: after[index] ?? "" }))
        .map((row, index) => `<div class="code-inline-diff-row${row.before === row.after ? "" : " changed"}"><span>${index + 1}</span><pre>${esc(row.before) || " "}</pre><pre>${esc(row.after) || " "}</pre></div>`).join("");
      renderDrawer("Current source compared with saved file", `<div class="code-inline-diff"><header><span></span><b>Saved</b><b>Current</b></header>${rows}</div>`);
    };
    const verifyRoundTrip = async () => {
      if (!validateBasic) return;
      try {
        const result = await validateBasic(textarea.value, textarea.dataset.savedValue || "");
        renderDrawer("BASIC round-trip verification", `<div class="code-verification"><p class="${result.roundTripExact ? "pass" : "warn"}"><strong>${result.roundTripExact ? "Exact token round trip" : "Review required"}</strong></p><dl><dt>Lines</dt><dd>${Number(result.lineCount || 0).toLocaleString()}</dd><dt>Tokenised size</dt><dd>${Number(result.byteLength || 0).toLocaleString()} bytes</dd><dt>Destinations</dt><dd>${(result.destinations || []).length.toLocaleString()}</dd></dl>${(result.warnings || []).map(message => `<p>${esc(message)}</p>`).join("") || "<p>The listing tokenises, detokenises and reproduces identical token bytes.</p>"}</div>`);
        return result;
      } catch (error) { window.alert(error.message || String(error)); return null; }
    };
    const reference = () => {
      const keys = [...new Set([...Object.keys(dictionary(language)), ...(language === "basic" ? [...BASIC_KEYWORDS] : [])])].sort();
      renderDrawer(`${languageName(language)} reference`, `<label class="code-reference-filter">Filter commands<input type="search" data-code-reference-filter placeholder="Type a command name"></label><div class="code-reference-list">${keys.map(key => `<button type="button" data-code-help="${esc(key)}">${esc(key)}</button>`).join("")}</div>`);
      drawer.querySelector("[data-code-reference-filter]")?.focus();
    };
    const goToLine = () => {
      const requested = prompt(language === "basic" ? "Go to AmigaBASIC line number or physical editor line:" : "Go to editor line:");
      if (requested == null || !requested.trim()) return;
      const number = Number.parseInt(requested, 10);
      if (!Number.isInteger(number) || number < 1) return;
      let offset = null;
      if (language === "basic") {
        const match = [...textarea.value.matchAll(/^\s*(\d+)\s/gm)].find(item => Number(item[1]) === number);
        if (match) offset = match.index;
      }
      if (offset == null) {
        const lines = textarea.value.split("\n");
        if (number > lines.length) return;
        offset = lines.slice(0, number - 1).reduce((total, line) => total + line.length + 1, 0);
      }
      goTo(offset);
    };
    const normaliseCommands = () => {
      const convention = COMMAND_CASE[language];
      if (!convention) return;
      showOriginalView();
      const convert = value => convention === "lower" ? value.toLowerCase() : value.toUpperCase();
      const replacements = state.tokens.filter(item => item.type === "keyword" && item.text !== convert(item.text)).reverse();
      if (!replacements.length) return;
      const selectionStart = textarea.selectionStart;
      const selectionEnd = textarea.selectionEnd;
      let updated = textarea.value;
      replacements.forEach(item => { updated = `${updated.slice(0, item.start)}${convert(item.text)}${updated.slice(item.end)}`; });
      textarea.setRangeText(updated, 0, textarea.value.length, "end");
      textarea.setSelectionRange(selectionStart, selectionEnd);
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      textarea.focus();
      historyEntry("Normalised commands", `${replacements.length} recognised token${replacements.length === 1 ? "" : "s"}`);
    };
    const currentPhysicalLines = () => {
      const start = textarea.value.lastIndexOf("\n", Math.max(0, textarea.selectionStart - 1)) + 1;
      const endBreak = textarea.value.indexOf("\n", textarea.selectionEnd);
      const end = endBreak < 0 ? textarea.value.length : endBreak;
      const first = textarea.value.slice(0, start).split("\n").length - 1;
      const last = first + textarea.value.slice(start, end).split("\n").length - 1;
      return { first, last };
    };
    const lineOperation = action => {
      if (textarea.readOnly) return false;
      showOriginalView();
      const range = currentPhysicalLines();
      const lines = textarea.value.split("\n");
      const selected = lines.slice(range.first, range.last + 1);
      if (!selected.length) return false;
      const before = documentSnapshot();
      let first = range.first;
      if (action === "delete") lines.splice(range.first, selected.length);
      else if (action === "duplicate") { lines.splice(range.last + 1, 0, ...selected); first = range.last + 1; }
      else if (action === "move-up" && range.first > 0) {
        const previous = lines.splice(range.first - 1, 1)[0];
        lines.splice(range.last, 0, previous);
        first -= 1;
      } else if (action === "move-down" && range.last < lines.length - 1) {
        const following = lines.splice(range.last + 1, 1)[0];
        lines.splice(range.first, 0, following);
        first += 1;
      } else if (action === "join" && language !== "basic") {
        lines.splice(range.first, selected.length, selected.map(line => line.trim()).join(" "));
      } else return false;
      const updated = lines.join("\n");
      const start = lines.slice(0, first).reduce((total, line) => total + line.length + 1, 0);
      const count = action === "duplicate" ? selected.length : action === "join" ? 1 : action === "delete" ? 0 : selected.length;
      const end = count ? start + lines.slice(first, first + count).join("\n").length : start;
      const after = { value: updated, selectionStart: start, selectionEnd: end, scrollTop: textarea.scrollTop, scrollLeft: textarea.scrollLeft };
      refactorUndo.push({ before, after });
      refactorRedo.length = 0;
      applyDocumentSnapshot(after);
      historyEntry(`${action.replace("-", " ")} lines`, `${selected.length} line${selected.length === 1 ? "" : "s"}`);
      return true;
    };
    const sourcePosition = offset => {
      const rows = textarea.value.slice(0, Math.max(0, offset)).split("\n");
      return { line: rows.length - 1, column: rows.at(-1).length };
    };
    const rebuiltPosition = (position, lines, expansions, rebuiltLines) => {
      const sourceLine = Math.min(position.line, Math.max(0, lines.length - 1));
      let targetLine = 0;
      for (let index = 0; index < sourceLine; index += 1) {
        targetLine += expansions.get(index)?.length || 1;
      }
      targetLine = Math.min(targetLine, Math.max(0, rebuiltLines.length - 1));
      const column = Math.min(position.column, rebuiltLines[targetLine]?.length || 0);
      return rebuiltLines.slice(0, targetLine).reduce((total, line) => total + line.length + 1, 0) + column;
    };
    const documentSnapshot = () => ({
      value: textarea.value,
      selectionStart: textarea.selectionStart,
      selectionEnd: textarea.selectionEnd,
      scrollTop: textarea.scrollTop,
      scrollLeft: textarea.scrollLeft,
    });
    const applyDocumentSnapshot = snapshot => {
      textarea.focus();
      textarea.value = snapshot.value;
      textarea.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd);
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      const restoreView = () => {
        textarea.scrollTop = snapshot.scrollTop;
        textarea.scrollLeft = snapshot.scrollLeft;
        syncScroll();
      };
      restoreView();
      requestAnimationFrame(restoreView);
      setTimeout(restoreView, 0);
      setTimeout(restoreView, 100);
    };
    const recordRefactor = after => {
      refactorUndo.push({ before: documentSnapshot(), after });
      refactorRedo.length = 0;
      applyDocumentSnapshot(after);
      clearTimeout(timer);
      render();
      historyEntry("Accepted transformation", `${after.value.split("\n").length} lines`);
    };
    const undo = () => {
      const transaction = refactorUndo.at(-1);
      if (!transaction || textarea.value !== transaction.after.value) return false;
      refactorUndo.pop();
      refactorRedo.push(transaction);
      applyDocumentSnapshot(transaction.before);
      clearTimeout(timer);
      render();
      return true;
    };
    const redo = () => {
      const transaction = refactorRedo.at(-1);
      if (!transaction || textarea.value !== transaction.before.value) return false;
      refactorRedo.pop();
      refactorUndo.push(transaction);
      applyDocumentSnapshot(transaction.after);
      clearTimeout(timer);
      render();
      return true;
    };
    const cancelRefactor = () => {
      refactorPlan = null;
      renderFolds();
      textarea.focus();
    };
    const commitRefactor = () => {
      if (!refactorPlan) return;
      const condensing = refactorPlan.mode === "condense";
      const message = condensing
        ? "Accept this condensation? The reviewed proposal will replace the selected code as one undoable operation. Safe adjacent statements will share physical lines; surviving line numbers and all explicit destinations are preserved."
        : "Accept this refactor? The reviewed proposal will now replace the program as one undoable operation. Lines will be renumbered and direct GOTO, GOSUB, RESTORE, THEN and ON GOTO/GOSUB destinations will be updated. Dynamic line-number expressions cannot be rewritten automatically.";
      if (!window.confirm(message)) return;
      const after = refactorPlan.after;
      refactorPlan = null;
      recordRefactor(after);
      renderFolds();
    };
    const refactor = async () => {
      if (language !== "basic" || textarea.readOnly) return;
      const range = currentPhysicalLines();
      const noSelection = textarea.selectionStart === textarea.selectionEnd;
      const lines = textarea.value.split("\n");
      const selectionStart = sourcePosition(textarea.selectionStart);
      const selectionEnd = sourcePosition(textarea.selectionEnd);
      const scrollTop = textarea.scrollTop;
      const scrollLeft = textarea.scrollLeft;
      const first = noSelection ? 0 : range.first;
      const last = noSelection ? lines.length - 1 : range.last;
      const assemblerLines = basicInlineAssemblerLines(textarea.value);
      const numberedBodies = lines.map(line => line.match(/^\s*\d+\s+(.*)$/)?.[1]).filter(body => body != null);
      if (numberedBodies.some(basicHasDynamicDestination) || numberedBodies.some(basicHasSemanticErl)) {
        window.alert("This program uses a computed line destination or uses ERL in program logic. Refactoring would require renumbering physical lines and could change its behaviour, so the program has been left untouched.");
        return;
      }
      const expansions = new Map();
      for (let index = first; index <= last; index += 1) {
        if (assemblerLines[index]) continue;
        const tangled = tangledBasicLine(lines[index], nextBasicLineNumber(lines, index));
        if (tangled) expansions.set(index, tangled.statements);
      }
      const rawRebuiltLines = rebuildBasic(lines, expansions, { startAt: 10, step: 10 });
      const rebuiltAssemblerLines = basicInlineAssemblerLines(rawRebuiltLines.join("\n"));
      const rebuiltLines = rawRebuiltLines
        .map((line, index) => rebuiltAssemblerLines[index] ? line : normaliseBasicControlSpacing(line));
      const rebuilt = rebuiltLines.join("\n");
      if (rebuiltLines.some(line => Number(line.match(/^\s*(\d+)/)?.[1] || 0) > 32767)) {
        window.alert("This program is too long to renumber in steps of 10 without exceeding line 32767.");
        return;
      }
      const tokens = sourceTokens(rebuilt, language, inlineAssemblyLanguage).filter(item => item.type === "keyword").reverse();
      let normalised = rebuilt;
      tokens.forEach(item => { normalised = `${normalised.slice(0, item.start)}${item.text.toUpperCase()}${normalised.slice(item.end)}`; });
      let verification = null;
      if (validateBasic) {
        try { verification = await validateBasic(normalised, textarea.value); }
        catch (error) { window.alert(error.message || String(error)); return; }
      }
      const newStart = rebuiltPosition(selectionStart, lines, expansions, rebuiltLines);
      const newEnd = rebuiltPosition(selectionEnd, lines, expansions, rebuiltLines);
      refactorPlan = {
        mode: "refactor",
        before: textarea.value.split("\n"),
        preview: normalised.split("\n"),
        verification,
        after: { value: normalised, selectionStart: newStart, selectionEnd: newEnd, scrollTop, scrollLeft },
      };
      renderFolds();
    };
    const condense = async () => {
      if (language !== "basic" || textarea.readOnly || !packBasic) return;
      const lines = textarea.value.split("\n");
      const parsed = lines.map((line, index) => {
        const match = line.match(/^\s*(\d+)(?:\s+(.*))?$/);
        return match ? { index, number: Number(match[1]), body: match[2] || "", line } : null;
      });
      if (parsed.some((row, index) => lines[index].trim() && !row)) {
        window.alert("Condense needs a complete numbered AmigaBASIC listing. Correct the unnumbered source lines first.");
        return;
      }
      const numbered = parsed.filter(Boolean);
      const assemblerLines = basicInlineAssemblerLines(textarea.value);
      if (new Set(numbered.map(row => row.number)).size !== numbered.length) {
        window.alert("Condense cannot safely operate while BASIC line numbers are duplicated.");
        return;
      }
      if (numbered.some(row => basicHasDynamicDestination(row.body)) || numbered.some(row => basicHasSemanticErl(row.body))) {
        window.alert("This program uses a computed line destination or uses ERL in program logic. Removing physical line numbers could change its behaviour, so condensation has been left for manual review.");
        return;
      }
      const range = currentPhysicalLines();
      const noSelection = textarea.selectionStart === textarea.selectionEnd;
      const first = noSelection ? 0 : range.first;
      const last = noSelection ? lines.length - 1 : range.last;
      const targets = new Set(numbered.flatMap(row => basicDestinations(row.body).map(item => item.target)));
      const runs = [];
      const pieces = [];
      let index = 0;
      while (index < lines.length) {
        const row = parsed[index];
        if (index < first || index > last || !row || assemblerLines[index]) {
          pieces.push({ kind: "fixed", entries: [{ index, line: lines[index] }] });
          index += 1;
          continue;
        }
        if (!row.body.trim() && !targets.has(row.number)) { index += 1; continue; }
        const entries = [];
        while (index <= last) {
          const candidate = parsed[index];
          if (!candidate || assemblerLines[index]) break;
          if (!candidate.body.trim() && !targets.has(candidate.number)) { index += 1; continue; }
          if (entries.length && (targets.has(candidate.number) || basicCondenseBoundaryBefore(candidate.body))) break;
          entries.push(candidate);
          index += 1;
          if (basicCondenseBoundaryAfter(candidate.body)) break;
        }
        if (!entries.length) continue;
        const runIndex = runs.length;
        runs.push(entries.map(entry => entry.body));
        pieces.push({ kind: "run", runIndex, entries });
      }
      let packed;
      try { packed = await packBasic(runs); }
      catch (error) { window.alert(error.message || String(error)); return; }
      if (!Array.isArray(packed) || packed.length !== runs.length) {
        window.alert("The BASIC line packer returned an incomplete result.");
        return;
      }
      const output = [];
      const sourceMap = new Map();
      pieces.forEach(piece => {
        if (piece.kind === "fixed") {
          const outIndex = output.length;
          output.push(piece.entries[0].line);
          sourceMap.set(piece.entries[0].index, { outIndex, baseColumn: 0, sourceBodyStart: 0 });
          return;
        }
        let cursor = 0;
        for (const count of packed[piece.runIndex]) {
          const entries = piece.entries.slice(cursor, cursor + Number(count));
          if (!entries.length) continue;
          const numberPrefix = `${entries[0].number} `;
          const outIndex = output.length;
          output.push(`${numberPrefix}${entries.map(entry => entry.body).join(":")}`);
          let bodyOffset = 0;
          entries.forEach(entry => {
            const sourceBodyStart = entry.line.indexOf(entry.body);
            sourceMap.set(entry.index, { outIndex, baseColumn: numberPrefix.length + bodyOffset, sourceBodyStart });
            bodyOffset += entry.body.length + 1;
          });
          cursor += Number(count);
        }
      });
      const value = output.join("\n");
      if (value === textarea.value) {
        window.alert("No safely condensable physical lines were found in that selection.");
        return;
      }
      let verification = null;
      if (validateBasic) {
        try { verification = await validateBasic(value, textarea.value); }
        catch (error) { window.alert(error.message || String(error)); return; }
      }
      const mapPosition = position => {
        let mapping = sourceMap.get(position.line);
        if (!mapping) {
          const nearest = [...sourceMap.entries()].sort((left, right) => Math.abs(left[0] - position.line) - Math.abs(right[0] - position.line))[0];
          mapping = nearest?.[1] || { outIndex: 0, baseColumn: 0, sourceBodyStart: 0 };
        }
        const lineOffset = output.slice(0, mapping.outIndex).reduce((total, line) => total + line.length + 1, 0);
        const column = mapping.baseColumn + Math.max(0, position.column - mapping.sourceBodyStart);
        return lineOffset + Math.min(column, output[mapping.outIndex]?.length || 0);
      };
      const selectionStart = sourcePosition(textarea.selectionStart);
      const selectionEnd = sourcePosition(textarea.selectionEnd);
      refactorPlan = {
        mode: "condense",
        before: textarea.value.split("\n"),
        preview: output,
        verification,
        after: {
          value,
          selectionStart: mapPosition(selectionStart),
          selectionEnd: mapPosition(selectionEnd),
          scrollTop: textarea.scrollTop,
          scrollLeft: textarea.scrollLeft,
        },
      };
      renderFolds();
    };
    const toggleComment = () => {
      if (language !== "basic" || textarea.readOnly) return;
      showOriginalView();
      const start = textarea.value.lastIndexOf("\n", Math.max(0, textarea.selectionStart - 1)) + 1;
      const followingBreak = textarea.value.indexOf("\n", textarea.selectionEnd);
      const end = followingBreak < 0 ? textarea.value.length : followingBreak;
      const selectedLines = textarea.value.slice(start, end).split("\n");
      const nonEmpty = selectedLines.filter(line => line.trim());
      const remove = nonEmpty.length > 0 && nonEmpty.every(line => /^\s*\d+\s+REM(?:\s|$)/i.test(line));
      const replacement = selectedLines.map(line => {
        if (!line.trim()) return line;
        if (remove) return line.replace(/^(\s*\d+\s+)REM\s?/i, "$1");
        return line.replace(/^(\s*\d+\s+)/, "$1REM ");
      }).join("\n");
      textarea.setRangeText(replacement, start, end, "select");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      textarea.focus();
      historyEntry(remove ? "Removed comments" : "Added comments", `${selectedLines.length} line${selectedLines.length === 1 ? "" : "s"}`);
    };
    const updateMenus = () => {
      root.querySelector('[data-editor-action="help-problems"] span')?.replaceChildren(document.createTextNode(`Problems (${state.issues.length})`));
      root.querySelector('[data-editor-action="help-symbols"] span')?.replaceChildren(document.createTextNode(`Document symbols (${state.symbols.length})`));
    };
    const render = () => {
      dismissHoverHelp(textarea.ownerDocument);
      state = { tokens: sourceTokens(textarea.value, language, inlineAssemblyLanguage), issues: diagnostics(textarea.value, language, dialect), symbols: symbols(textarea.value, language), blocks: foldBlocks(textarea.value, language) };
      const html = highlightedHtml(textarea.value, state.tokens);
      visual.querySelector("pre").innerHTML = html;
      hit.querySelector("pre").innerHTML = html;
      hit.querySelectorAll(".code-help-token").forEach(element => {
        const tokenLanguage = element.dataset.helpLanguage || language;
        const tokenStart = Number(element.dataset.tokenStart);
        const tokenEnd = Number(element.dataset.tokenEnd);
        const contextualHelp = sourceContextHelp(textarea.value, tokenLanguage, tokenStart, tokenEnd, element.dataset.helpKey, targetProfile);
        attachTooltip(root, tokenLanguage, element, element.dataset.helpKey, contextualHelp);
        element.addEventListener("pointerdown", event => {
          event.preventDefault();
          textarea.focus();
          textarea.setSelectionRange(tokenStart, tokenEnd);
          textarea.dispatchEvent(new Event("click", { bubbles: true }));
        });
      });
      syncScroll();
      updateMenus();
      renderStructureGuides();
      renderFolds();
    };
    const schedule = () => { clearTimeout(timer); timer = setTimeout(render, 80); };
    textarea.addEventListener("input", schedule);
    textarea.addEventListener("scroll", syncScroll, { passive: true });
    const updateCursorContext = () => { updateMenus(); renderStructureGuides(); };
    textarea.addEventListener("selectionchange", updateCursorContext);
    textarea.addEventListener("select", updateCursorContext);
    textarea.addEventListener("click", updateCursorContext);
    textarea.addEventListener("keyup", updateCursorContext);
    textarea.addEventListener("keydown", event => {
      if (event.key === "F1") { event.preventDefault(); helpAtCursor(); }
      else if (event.key === " " && (event.ctrlKey || event.metaKey)) { event.preventDefault(); showCompletions(); }
    });
    render();
    return { overview, helpAtCursor, showProblems, showSymbols, showCompletions, showCustom, findReferences, renameSymbol, showOutline, showHistory, compareWith, verifyRoundTrip, reference, goToLine, normaliseCommands, toggleComment, lineOperation, formatCode, condense, refactor, undo, redo, expandAll, collapseAll, toggleAll, toggleStructureGuides, setStructureGuideSize, showOriginalView, closeDrawer, refresh: render, recordHistory: historyEntry, state: () => state, history: () => pendingHistory };
  }

  function enhanceDisassembly({ root, report }) {
    if (!root) return null;
    const language = report.architecture || "68000";
    const drawer = document.createElement("section");
    drawer.className = "code-intelligence-drawer";
    drawer.hidden = true;
    root.insertBefore(drawer, root.querySelector(".editor-status"));
    const labelElements = [...root.querySelectorAll(".disassembly-label")];
    const labels = labelElements.map(element => ({ name: element.querySelector("span:last-child")?.textContent.replace(/:$/, "") || "Label", offset: Number(element.nextElementSibling?.dataset.offset || 0) }));
    const foldedLabels = new Set();
    const foldBlocks = labelElements.map((element, index) => {
      const rows = [];
      let sibling = element.nextElementSibling;
      while (sibling && !sibling.classList.contains("disassembly-label")) {
        if (sibling.classList.contains("disassembly-source-line")) rows.push(sibling);
        sibling = sibling.nextElementSibling;
      }
      return { id: String(index), element, rows, label: labels[index].name };
    }).filter(block => block.rows.length > 0);
    const renderFolds = () => {
      dismissHoverHelp(root.ownerDocument);
      const canFold = foldBlocks.length > 0;
      const foldAll = root.querySelector('[data-disassembly-action="fold-toggle-all"]');
      if (foldAll) {
        foldAll.disabled = !canFold;
        foldAll.querySelector("span").textContent = foldedLabels.size ? "Expand all labelled blocks" : "Collapse all labelled blocks";
      }
      foldBlocks.forEach(block => {
        const collapsed = foldedLabels.has(block.id);
        const cell = block.element.querySelector(".disassembly-fold-cell");
        cell.innerHTML = `<button type="button" class="code-fold-toggle" aria-expanded="${collapsed ? "false" : "true"}" title="${collapsed ? "Expand" : "Collapse"} ${esc(block.label)}">${collapsed ? "+" : "−"}</button>`;
        cell.querySelector("button").onclick = () => {
          if (collapsed) foldedLabels.delete(block.id);
          else foldedLabels.add(block.id);
          renderFolds();
        };
        block.element.classList.toggle("fold-collapsed", collapsed);
        block.rows.forEach(row => { row.hidden = collapsed; });
      });
    };
    const expandAll = () => { foldedLabels.clear(); renderFolds(); };
    const collapseAll = () => { foldBlocks.forEach(block => foldedLabels.add(block.id)); renderFolds(); };
    const toggleAll = () => foldedLabels.size ? expandAll() : collapseAll();
    const commands = new Set();
    const commandHelp = new Map();
    root.querySelectorAll(".disassembly-instruction").forEach((element, index) => {
      const row = report.rows[index] || { mnemonic: element.textContent.split(/\s+/)[0], operand: element.textContent.replace(/^\S+\s*/, "") };
      const mnemonic = normaliseHelpKey(row.mnemonic) || "DATA";
      const contextualHelp = disassemblyInstructionHelp(row, language);
      commands.add(mnemonic);
      if (!commandHelp.has(mnemonic)) commandHelp.set(mnemonic, contextualHelp);
      element.classList.add("code-help-token", "code-token-keyword");
      element.dataset.helpKey = mnemonic;
      element.setAttribute("aria-label", element.getAttribute("title") || element.textContent);
      element.removeAttribute("title");
      attachTooltip(root, language, element, mnemonic, contextualHelp);
    });
    root.querySelectorAll(".disassembly-comment").forEach(element => {
      const text = element.textContent;
      const pattern = new RegExp(`\\b(${Object.keys(LIBRARY_HELP).join("|")})\\b`, "g");
      let cursor = 0;
      const chunks = [];
      for (const match of text.matchAll(pattern)) {
        chunks.push(esc(text.slice(cursor, match.index)), `<span class="code-help-token code-token-api" data-help-key="${match[1]}">${match[1]}</span>`);
        commands.add(match[1]);
        cursor = match.index + match[1].length;
      }
      if (!chunks.length) return;
      chunks.push(esc(text.slice(cursor)));
      element.innerHTML = chunks.join("");
      element.removeAttribute("title");
      element.querySelectorAll("[data-help-key]").forEach(tokenElement => attachTooltip(root, language, tokenElement, tokenElement.dataset.helpKey));
    });
    renderFolds();
    const show = (title, body) => {
      drawer.hidden = false;
      drawer.innerHTML = `<header><div><small>CODE-AWARE HELP</small><h3>${esc(title)}</h3></div><button type="button" class="code-drawer-close" aria-label="Close code help">×</button></header><div class="code-drawer-body">${body}</div>`;
      drawer.querySelector(".code-drawer-close").onclick = () => { drawer.hidden = true; };
      drawer.querySelectorAll("[data-code-help]").forEach(button => button.onclick = () => show(button.dataset.codeHelp, helpMarkup(commandHelp.get(button.dataset.codeHelp) || lookup(language, button.dataset.codeHelp))));
      drawer.querySelectorAll("[data-disassembly-offset]").forEach(button => button.onclick = () => root.querySelector(`.disassembly-source-line[data-offset="${button.dataset.disassemblyOffset}"]`)?.scrollIntoView({ block: "center" }));
    };
    const overview = () => show(`${languageName(language)} overview`, `<div class="code-overview"><p>This view contains <strong>${report.rows.length.toLocaleString()} decoded instructions or data records</strong>, <strong>${labels.length.toLocaleString()} labels</strong> and <strong>${report.strings.length.toLocaleString()} readable strings</strong>.</p><p>Hover a highlighted mnemonic or MOS routine for syntax, processor requirements and calling conventions. Disassembly remains read-only because data can resemble valid instructions.</p><h4>Recognised operations</h4><div class="code-command-chips">${[...commands].sort().map(key => `<button type="button" data-code-help="${key}">${key}</button>`).join("")}</div></div>`);
    const reference = () => show(`${languageName(language)} reference`, `<div class="code-reference-list">${[...new Set([...commands, ...Object.keys(INLINE_ASSEMBLER_HELP), ...Object.keys(ASM_HELP), ...Object.keys(LIBRARY_HELP)])].sort().map(key => `<button type="button" data-code-help="${key}">${key}</button>`).join("")}</div>`);
    const showSymbols = () => show("Disassembly symbols", labels.length ? `<div class="code-symbol-list">${labels.map(item => `<button type="button" data-disassembly-offset="${item.offset}"><b>label</b><span>${esc(item.name)}</span></button>`).join("")}</div>` : '<p class="code-empty-message">No labels were discovered in this range.</p>');
    return { overview, reference, showSymbols, showCustom: show, expandAll, collapseAll, toggleAll, helpAtCursor: overview, showProblems: () => show("Disassembly cautions", '<p class="code-empty-message">No writable source diagnostics apply. Treat unknown opcodes, unreachable regions and embedded data as cautions rather than automatic errors.</p>') };
  }

  return { enhance, enhanceDisassembly, lookup, contextHelp: sourceContextHelp, diagnostics };
})();
