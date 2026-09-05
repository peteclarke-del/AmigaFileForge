from __future__ import annotations

from copy import deepcopy


def _addon(identifier, label, group, machines, description, *, emulator="profile", requires=(), conflicts=()):
    return {
        "id": identifier, "label": label, "group": group,
        "machines": list(machines), "description": description,
        "emulator": emulator, "requires": list(requires), "conflicts": list(conflicts),
    }


ALL_MACHINES = ["a500", "a500plus", "a600", "a1200", "a2000", "a3000", "a4000", "cd32"]
BIG_BOX = ["a2000", "a3000", "a4000"]
WEDGE = ["a500", "a500plus", "a600", "a1200"]

ADDONS = [
    # ---- Kickstart firmware ----
    _addon("kick13", "Kickstart 1.3 (34.5)", "firmware", ["a500", "a500plus", "a2000"], "256 KiB Kickstart 1.3 ROM; the original Workbench 1.3 environment.", emulator="fs-uae", conflicts=["kick204", "kick305", "kick31"]),
    _addon("kick204", "Kickstart 2.04 (37.175)", "firmware", ["a500", "a500plus", "a600", "a2000", "a3000"], "512 KiB Kickstart 2.04 ROM with the AmigaOS 2.x Workbench.", emulator="fs-uae", conflicts=["kick13", "kick305", "kick31"]),
    _addon("kick305", "Kickstart 3.0 (39.106)", "firmware", ["a1200", "a4000"], "512 KiB AGA Kickstart 3.0 ROM.", emulator="fs-uae", conflicts=["kick13", "kick204", "kick31"]),
    _addon("kick31", "Kickstart 3.1 (40.68)", "firmware", ["a500", "a500plus", "a600", "a1200", "a2000", "a3000", "a4000", "cd32"], "512 KiB Kickstart 3.1 ROM, the usual target for modern software.", emulator="fs-uae", conflicts=["kick13", "kick204", "kick305"]),
    _addon("kickstart-remap", "MapROM / Kickstart remap", "firmware", ["a1200", "a3000", "a4000"], "Copies Kickstart into fast RAM for a measurable speed gain.", requires=["fast-ram"]),

    # ---- Main and expansion memory ----
    _addon("chip-512", "512 KiB trapdoor Chip RAM", "main-memory", ["a500"], "A501 style trapdoor expansion taking the machine to 1 MiB Chip RAM.", emulator="fs-uae", conflicts=["chip-1024"]),
    _addon("chip-1024", "1 MiB Chip RAM", "main-memory", ["a500plus", "a600", "a1200", "a2000"], "Full 1 MiB of Chip RAM through Agnus/Alice.", emulator="fs-uae", conflicts=["chip-512"]),
    _addon("chip-2048", "2 MiB Chip RAM", "main-memory", ["a1200", "a3000", "a4000", "cd32"], "AGA machines with the full 2 MiB Chip RAM complement.", emulator="fs-uae"),
    _addon("fast-ram", "Fast RAM expansion", "expansion-memory", ALL_MACHINES, "Autoconfig 32-bit Fast RAM. Required by most hard-disk installs and WHDLoad slaves.", emulator="fs-uae"),
    _addon("slow-ram", "512 KiB Slow (ranger) RAM", "expansion-memory", ["a500", "a500plus", "a2000"], "A501 trapdoor RAM mapped at $C00000.", emulator="fs-uae", conflicts=["fast-ram"]),

    # ---- Floppy interfaces ----
    _addon("df0-internal", "Internal DS/DD floppy (DF0:)", "disk", ALL_MACHINES, "The standard 880 KiB DS/DD Amiga drive, fitted to every model.", emulator="fs-uae", conflicts=["df0-hd"]),
    _addon("df0-hd", "High-density floppy (DF0:)", "disk", ["a3000", "a4000"], "1.76 MiB high-density drive fitted to later big-box machines.", emulator="fs-uae", conflicts=["df0-internal"]),
    _addon("df1-external", "External drive (DF1:)", "disk", ALL_MACHINES, "Second 880 KiB drive on the external floppy port.", emulator="fs-uae"),
    _addon("gotek", "Gotek / FlashFloppy", "disk", ALL_MACHINES, "Solid-state floppy emulator reading ADF and HFE images from USB."),
    _addon("catweasel", "Catweasel controller", "disk", BIG_BOX, "Flux-level floppy controller used for preservation captures."),

    # ---- Mass storage ----
    _addon("a590", "A590 SCSI / XT sidecar", "storage", ["a500", "a500plus"], "Commodore A590 hard-drive sidecar with autoboot ROM and RAM sockets.", emulator="fs-uae"),
    _addon("a2091", "A2091 SCSI controller", "storage", ["a2000"], "Zorro II SCSI controller with autoboot ROM.", emulator="fs-uae"),
    _addon("a4091", "A4091 SCSI-2 controller", "storage", ["a3000", "a4000"], "Zorro III SCSI-2 controller.", emulator="fs-uae"),
    _addon("scsi-internal", "Internal SCSI (scsi.device)", "storage", ["a3000"], "On-board WD33C93 SCSI controller.", emulator="fs-uae"),
    _addon("ide-internal", "Internal IDE (gayle/ide.device)", "storage", ["a600", "a1200", "a4000"], "On-board 2.5 inch IDE interface.", emulator="fs-uae"),
    _addon("cf-adapter", "CompactFlash adapter", "storage", ["a600", "a1200"], "CF card presented to the IDE bus as a hard drive.", emulator="fs-uae"),
    _addon("pcmcia-sram", "PCMCIA SRAM / CF card", "storage", ["a600", "a1200"], "Credit-card slot storage; the usual route for moving files onto a stock machine."),

    # ---- Accelerators ----
    _addon("acc-68020", "68020 accelerator", "accelerator", ["a500", "a500plus", "a600", "a1200", "a2000"], "68020 turbo board with optional 32-bit Fast RAM; Blizzard 1220 class on an A1200.", emulator="fs-uae", conflicts=["acc-68030", "acc-68040", "acc-68060", "pistorm", "pistorm32"]),
    _addon("acc-68030", "68030 accelerator", "accelerator", WEDGE + BIG_BOX, "Blizzard/GVP class 68030 with MMU and FPU socket.", emulator="fs-uae", conflicts=["acc-68020", "acc-68040", "acc-68060", "pistorm", "pistorm32"]),
    _addon("acc-68040", "68040 accelerator", "accelerator", ["a1200", "a3000", "a4000"], "68040 accelerator; the standard AmigaOS 3.5/3.9 target.", emulator="fs-uae", conflicts=["acc-68020", "acc-68030", "acc-68060", "pistorm", "pistorm32"]),
    _addon("acc-68060", "68060 accelerator", "accelerator", ["a1200", "a3000", "a4000"], "68060 accelerator, usually with 64-128 MiB of Fast RAM.", emulator="fs-uae", conflicts=["acc-68020", "acc-68030", "acc-68040", "pistorm", "pistorm32"]),
    # The two PiStorm boards fit different sockets and are not interchangeable.
    # The original replaces a socketed 68000; the 32-bit board goes in the
    # A1200's CPU slot, which is the only place it fits.
    _addon("pistorm", "PiStorm · 68000 socket", "accelerator", ["a500", "a500plus", "a600", "a2000"], "Raspberry Pi CPU replacement in the 68000 socket, providing emulated 68k, RAM and virtual SCSI.", conflicts=["acc-68020", "acc-68030", "acc-68040", "acc-68060", "pistorm32"]),
    _addon("pistorm32", "PiStorm32 · A1200 CPU slot", "accelerator", ["a1200"], "Raspberry Pi CPU replacement in the A1200's 32-bit CPU slot, with emulated 68k, RAM, virtual SCSI and optional RTG.", conflicts=["acc-68020", "acc-68030", "acc-68040", "acc-68060", "pistorm"]),
    # A PiStorm emulates the FPU, so a physical 68882 only applies to a real
    # 68k accelerator that has the socket.
    _addon("fpu-68882", "68882 FPU", "accelerator-option", ALL_MACHINES, "Floating-point coprocessor used by rendering and AmigaOS maths libraries.", emulator="fs-uae", requires=["acc-68020|acc-68030|acc-68040|acc-68060"]),
    _addon("pistorm-rtg", "PiStorm RTG output", "accelerator-option", ["a500", "a500plus", "a600", "a1200", "a2000"], "The PiStorm's own HDMI retargetable display, driven through Picasso96.", requires=["pistorm|pistorm32"]),

    # ---- Display ----
    _addon("gfx-picasso", "Picasso II RTG", "graphics", BIG_BOX + ["a1200"], "Retargetable graphics card driven through Picasso96.", emulator="fs-uae"),
    _addon("gfx-cybervision", "CyberVision 64", "graphics", BIG_BOX, "Zorro III RTG card driven through CyberGraphX.", emulator="fs-uae"),
    _addon("flicker-fixer", "Flicker fixer / scan doubler", "graphics", ALL_MACHINES, "De-interlaces the native display for a VGA monitor.", emulator="fs-uae"),

    # ---- Networking and ports ----
    _addon("net-a2065", "A2065 Ethernet", "network", BIG_BOX, "Zorro II Ethernet card using SANA-II drivers.", emulator="fs-uae"),
    _addon("net-pcmcia", "PCMCIA Ethernet", "network", ["a600", "a1200"], "Credit-card Ethernet adapter.", emulator="fs-uae"),
    _addon("parallel-sampler", "Parallel-port sampler", "ports", ALL_MACHINES, "8-bit audio sampler on the parallel port."),
    _addon("midi", "Serial MIDI interface", "ports", ALL_MACHINES, "MIDI in/out/thru on the serial port."),

    # ---- Software loaders ----
    _addon("whdload", "WHDLoad", "loader", ALL_MACHINES, "Installs floppy-only software to a hard disk with per-title patch slaves.", requires=["fast-ram"]),
    _addon("classicwb", "ClassicWB environment", "loader", ALL_MACHINES, "Pre-built Workbench install used as a base for hard-disk images.", requires=["whdload"]),
]

GROUPS = {
    "firmware": {"label": "Kickstart firmware", "max": 2},
    "main-memory": {"label": "Chip RAM", "max": 1},
    "expansion-memory": {"label": "Fast and Slow RAM", "max": 2},
    "disk": {"label": "Floppy interface", "max": 3},
    "storage": {"label": "Mass storage", "max": 3},
    "accelerator": {"label": "Processor", "max": 1},
    "accelerator-option": {"label": "Processor options", "max": 2},
    "graphics": {"label": "Display", "max": 2},
    "network": {"label": "Networking", "max": 1},
    "ports": {"label": "Ports and peripherals", "max": 4},
    "loader": {"label": "Software loaders", "max": 2},
}

MACHINES = [
    {"id": "a500", "label": "Amiga 500 (OCS)", "baseRam": "512K", "processor": "68000"},
    {"id": "a500plus", "label": "Amiga 500+ (ECS)", "baseRam": "1M", "processor": "68000"},
    {"id": "a600", "label": "Amiga 600 (ECS)", "baseRam": "1M", "processor": "68000"},
    {"id": "a1200", "label": "Amiga 1200 (AGA)", "baseRam": "2M", "processor": "68EC020"},
    {"id": "a2000", "label": "Amiga 2000 (ECS)", "baseRam": "1M", "processor": "68000"},
    {"id": "a3000", "label": "Amiga 3000 (ECS)", "baseRam": "2M", "processor": "68030"},
    {"id": "a4000", "label": "Amiga 4000 (AGA)", "baseRam": "2M", "processor": "68040"},
    {"id": "cd32", "label": "Amiga CD32 (AGA)", "baseRam": "2M", "processor": "68EC020"},
]


def hardware_catalogue() -> dict:
    return {"machines": deepcopy(MACHINES), "groups": deepcopy(GROUPS), "addons": deepcopy(ADDONS)}


def normalise_hardware_profile(data: dict) -> dict:
    machine = str(data.get("machine") or "a500").strip().lower()
    machine_ids = {row["id"] for row in MACHINES}
    if machine not in machine_ids:
        raise ValueError("Choose a supported base machine.")
    known = {row["id"]: row for row in ADDONS}
    addons = []
    for value in data.get("addons", []):
        identifier = str(value).strip().lower()
        if identifier and identifier not in addons:
            addons.append(identifier)
    invalid = [identifier for identifier in addons if identifier not in known or machine not in known[identifier]["machines"]]
    if invalid:
        raise ValueError(f"{', '.join(invalid)} cannot be fitted to {machine}.")
    for group, definition in GROUPS.items():
        selected = [identifier for identifier in addons if known[identifier]["group"] == group]
        if len(selected) > definition["max"]:
            raise ValueError(f"Choose no more than {definition['max']} option(s) from {definition['label']}.")
    selected = set(addons)
    for identifier in addons:
        conflicts = selected.intersection(known[identifier].get("conflicts", []))
        if conflicts:
            labels = ", ".join(known[conflict]["label"] for conflict in sorted(conflicts))
            raise ValueError(f"{known[identifier]['label']} cannot be fitted with {labels}.")
        for requirement in known[identifier]["requires"]:
            scoped_machine, _, expression = requirement.partition(":")
            if expression and scoped_machine != machine:
                continue
            choices = (expression or scoped_machine).split("|")
            if not any(choice in selected for choice in choices):
                labels = " or ".join(known[choice]["label"] for choice in choices)
                raise ValueError(f"{known[identifier]['label']} requires {labels}.")
    profile = dict(data)
    profile["machine"] = machine
    profile["addons"] = addons
    profile["accelerated"] = any(known[item]["group"] == "accelerator" for item in addons)
    return profile


def profile_addons(session) -> set[str]:
    profile = getattr(session, "hardware_profile", {}) or {}
    addons = {str(value) for value in profile.get("addons", []) if isinstance(value, str)}
    if profile.get("accelerated") and not any(
        value.startswith("acc-") or value.startswith("pistorm") for value in addons
    ):
        addons.add("acc-68030")
    return addons
