# Web and Linux desktop platform contract

Amiga File Forge has one application implementation with two hosts. The web
host runs in Docker and a normal browser. The Linux desktop host places the
same frontend, Flask routes, filesystem services, editors, format handlers and
tests inside a GTK 4 and Libadwaita window. It is not a second implementation.

This contract is part of the definition of done for every change. A feature or
fix that applies to shared behaviour must work in both hosts before it is
complete. Reviews must reject a web-only or desktop-only copy of shared domain
logic.

## Required boundaries

1. `app/server.py:create_app` is the only application factory. `app/wsgi.py`
   is the deliberately small WSGI composition root for Gunicorn, and the
   desktop runtime invokes the same factory with its native adapters.
2. `app/static/` is the only product frontend. The desktop package embeds it
   with WebKitGTK and must not carry a copied HTML, CSS or JavaScript tree.
3. Image opening, editing, validation, conversion, metadata, recipes,
   hardware deployment, undo and saving remain in shared `app/` modules.
4. Host adapters contain only work that a browser cannot perform. Current
   examples are choosing an absolute local path, owning a native window,
   launching an emulator on the host display and accessing a Greaseweazle USB
   floppy interface. Physical-media policy remains in the reusable
   `amiga_greaseweazle` package rather than in presentation code.
5. A host-only API must be declared in `HOST_EXCLUSIVE_ENDPOINTS` in
   `app/platform_contract.py`. Adding an exception is an architectural change,
   not a shortcut around parity.
6. A host-only user capability must be declared in `HOST_CAPABILITIES`, tested
   and documented. Shared capabilities belong in `SHARED_CAPABILITIES`.
7. Shared API response shapes and persisted image data cannot vary by host.
   A presentation hint such as `displayMode` is allowed when the operation is
   the same but its operating-system surface differs.

## Change checklist

Every pull request that changes application behaviour must answer these points:

- Does the change live in the shared service or frontend?
- Does it work through both `create_app()` and `create_app(platform="desktop")`?
- If it is genuinely host-specific, is the exception declared and tested?
- Do keyboard, pointer, dialog, progress, error and recovery paths still work
  in a browser and inside WebKitGTK?
- Are the main handbook, specialist guide and in-app Help updated where the
  workflow changed?
- Were the platform-contract tests and the relevant browser regressions run?

The route-map test constructs both hosts and fails when an undeclared endpoint
appears on only one. It also verifies that both hosts serve the same static
tree. These checks prevent accidental drift, but they do not replace a manual
desktop smoke test for native file selection, downloading and emulator windows.

Platform contract version 5 records native file-manager drag and drop, reviewed
local-path opening and durable WebView state as explicit desktop adapters.
Selections from the GTK chooser, file associations and file-manager drops are
presented to the shared frontend before opening. The user can review FFS target
hardware, distinguish independent ROMs from a physical component set and choose
linear or byte-interleaved ROM layout where valid. The native host then executes
those reviewed plans serially. Image parsing, HDA/GEO pairing, filename policy,
private working-copy creation and every operation after opening remain shared.
Hardware deployment continues to use the same target planner, isolated
snapshot, ZIP builder and workbench interface in both hosts.

## Storage and security

The web host uses the configured Docker work directory and browser-owner
identity. The desktop host stores working images under
`$XDG_DATA_HOME/amiga-file-forge/work`, or
`~/.local/share/amiga-file-forge/work` when `XDG_DATA_HOME` is unset.

The desktop Flask service listens only on a random `127.0.0.1` port. Each
launch creates a high-entropy authentication token. The initial WebKit request
supplies it in a private header, then the view receives its strict, HttpOnly
cookie. The token does not appear in the address or server access log. A
separate stable owner identifier is stored with mode `0600` under
`$XDG_CONFIG_HOME/amiga-file-forge/owner-id`, or the corresponding directory
under `~/.config`. Keeping authentication and ownership separate lets a new
random-port launch recover only that Linux user's working sessions.

Web storage is tied to an origin, and the desktop origin changes when its
private port changes. The desktop adapter therefore mirrors workspace settings,
hardware profiles and the private collection catalogue into
`client-state.json` in the same XDG configuration directory. Updates are
size-limited, written atomically and protected with mode `0600`. The web host
continues to use browser storage and IndexedDB. The desktop state endpoints and
the direct path-opening route do not exist in the web host, so a remote browser
cannot ask the server to read arbitrary host paths or access native state.

Source images are still copied into a working session. Neither the native file
chooser nor a file-manager drop grants in-place mutation. The desktop-only
adapter uses a filesystem reflink where available and falls back to one sparse
local copy, avoiding the browser multipart and upload-spooling path for large
media. Downloads use WebKitGTK's normal Downloads directory handling, and
saved packages retain the same timestamped image, metadata and README content
as browser downloads.

Both hosts emit the same anti-sniffing, framing, referrer and browser-feature
policy headers. Browser-owner and desktop launch cookies are HttpOnly,
SameSite Strict and gain the Secure attribute when the service is reached over
HTTPS. Archive browsing rejects unsafe parent paths, encrypted ZIP members,
more than 20,000 entries and more than 2 GiB of declared expanded content.
Content recognition while listing is capped at 16 MiB per archive so a large
collection cannot force an unbounded sequence of decompressions.

## Native host scope

The first native host deliberately reuses the mature web workbench. GTK and
Libadwaita own the application lifecycle, primary window and decorations,
application menu, symbolic header icons, file associations and local image
chooser. WebKitGTK renders the shared workspace, inheriting the GTK font and
system colour preference while retaining the Amiga media theme. Managed
emulators use ordinary native windows rather than the Docker noVNC surface.

Environment variables can point the desktop host at local emulator, firmware
and decoder-library builds:

| Variable | Default |
| --- | --- |
| `AMIGA_FSUAE_ROOT` | `/usr/bin` |
| `AMIGA_FILE_FORGE_KICKSTART_DIR` | `~/.config/amiga-file-forge/kickstarts` |
| `AMIGA_FILE_FORGE_CAPSIMAGE` | unset; the SPS decoder library is searched for |

The web Docker image continues to use those defaults. A Linux installation may
override them in its desktop session without changing shared emulator logic.
