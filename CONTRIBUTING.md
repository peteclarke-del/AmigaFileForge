# Contributing to Amiga File Forge

Amiga File Forge handles media that may be irreplaceable and targets machines
whose filing-system rules differ in small but consequential ways. A useful
change must preserve those constraints, provide an understandable failure
mode, and work through both the web and native Linux hosts where applicable.

## Before starting

Use a GitHub issue for a material format change, new dependency or workflow
redesign. Describe the target machine, filing system, image geometry and the
observable result. Do not attach copyrighted software, firmware or private
images to a public issue. A small, purpose-built fixture is preferable.

Security defects must follow [SECURITY.md](SECURITY.md), not the public issue
tracker. General support questions belong in the channels listed in
[SUPPORT.md](SUPPORT.md).

## Development workflow

1. Clone the repository over HTTPS or a configured SSH connection.
2. Create a focused branch from current `main`.
3. Keep filesystem semantics in the relevant Python service and shared user
   interaction in the common frontend. Do not fork behaviour between the web
   and desktop editions without a documented platform boundary.
4. Add a regression test before or alongside the fix.
5. Update the main handbook, specialist guide and in-app help when behaviour or
   terminology changes.
6. Submit a pull request using the repository template.

The [platform contract](docs/PLATFORM-CONTRACT.md) is mandatory. Native-only
capabilities must be exposed through the narrow authenticated adapter defined
there. Everything else should remain shared.

Maintainer authority, decision priorities, evidence requirements and release
ownership are defined in [GOVERNANCE.md](GOVERNANCE.md).

## Engineering standards

- Preserve source images. Mutations operate on private working copies and must
  retain checkpoint, rollback and cancellation behaviour.
- Reject unsupported media explicitly. Do not guess geometry or silently
  discard metadata to make a file appear to work.
- Keep one authoritative implementation for checksums, HDF offsets, catalogue
  metadata, menu codecs, archive bounds, flux-container policy and filesystem
  mutations. Flux geometry, encode and verify rules belong in
  `app/flux_containers.py` so HFE and SCP cannot drift apart; they previously
  did, and only the HFE save path repaired an omitted trailing sector.
- Reach into Amiganut's private API only through `app/amiganut_internals.py`. That
  module names every borrowed underscore-prefixed symbol in one reviewed place,
  so an Amiganut upgrade has a single file to check.
  `tests/test_amiganut_internals.py` enforces both halves of this.
- Bound archive expansion, content scanning, uploads, subprocess execution and
  long-running operations. Treat image contents and filenames as untrusted.
- Do not use shell interpolation for user-controlled values. Use argument
  arrays, validated paths and the existing process helpers.
- Meet WCAG 2.2 AA for changed controls in both themes. Every pointer action
  needs an operable keyboard path and every state change needs a non-colour
  indication.
- Keep prose direct and technical. Use commas, colons, semicolons or separate
  sentences instead of em dashes.
- Avoid compatibility layers for behaviour that has never been released. This
  project values a clear current implementation over dormant branches.

## Lint

The correctness lint runs first in CI and takes seconds:

```bash
pipx run ruff==0.16.4 check .
```

`ruff.toml` deliberately enforces a narrow set: undefined names, unused
imports, broken f-strings, syntax errors and pylint errors. It does not enforce
formatting. Keep it green; widen it one rule family at a time, with the
resulting fixes in the same change.

## Tests

Run the Python suite from an environment containing the application runtime
dependencies:

```bash
python3 -m unittest discover -s tests -v
```

### A complete local environment

Most of the suite runs on the host once the pinned runtime packages are
installed, which is considerably faster than rebuilding the container between
edits:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests -q
```

The `disc` engine is installed into `.venv/bin` by the Amiganut packages, so it
must be on `PATH` for the subprocess calls to find it.

HFE and SCP tests additionally need the `hxcfe` binary, which is not on PyPI.
Build the pinned revision once and put it on `PATH`:

```bash
tools/build-hxc-runtime.sh /tmp/hxc
PATH="/tmp/hxc/bin:$PWD/.venv/bin:$PATH" LD_LIBRARY_PATH=/tmp/hxc/lib \
  .venv/bin/python -m pytest tests -q
```

The managed emulator tests are the only ones that then remain unrunnable on a
plain host, because they execute the vamiga, FS-UAE and MAME binaries that the
container builds. Run those in the image, as below.

Run the JavaScript unit and syntax checks:

```bash
node tests/run_js_tests.js
node --check app/static/app.js
```

Build the production image and run the complete suite inside it when Python
native dependencies are not installed on the host:

```bash
docker compose build amiga-file-forge
docker run --rm -v "$PWD:/source:ro" amiga-file-forge:latest \
  python -m unittest discover -s /source/tests -v
```

Run the browser regressions against a current service:

```bash
npm install
npx playwright install chromium
npm run test:browser
```

Changes to Docker dependencies, emulator builders or native packaging must
also pass the AMD64, ARM64 and ARMv7 build matrix. Format changes should include
generated fixtures and negative cases for truncation, corruption, capacity and
rollback. Real hardware results are valuable evidence, but they do not replace
deterministic regression tests.

## Dependencies and third-party material

Do not add a package, ROM, firmware image, font or copied source file without
recording its source, exact version, licence, checksum where applicable, and
redistribution basis. Update [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
and the relevant build or firmware documentation in the same pull request.

The project MIT licence does not grant rights to third-party firmware or to
media supplied by users.

## Pull-request expectations

A pull request should explain what changed, why the chosen boundary is correct,
which failure modes were tested and whether web/native parity, accessibility,
security, documentation, saved packages or third-party notices are affected.
Reviewers should be able to reproduce the result without private media.
