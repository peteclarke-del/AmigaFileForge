# Project governance

## Scope

Amiga File Forge is maintained as one application across the Docker web host,
native Linux host and headless CLI. It spans Amiga filing systems, media
containers, menu formats, editor and analysis services, managed emulators and
physical-floppy integration. Shared behaviour must remain shared. A host adapter
may provide native file selection, display or device access, but it must not
fork filesystem semantics or safety policy.

Generated images, patches, menus and deployment packages do not transfer
ownership of third-party software or user media to this repository.

## Roles

Peter Clarke (`@peteclarke-del`) is the current maintainer and release owner.
The maintainer sets release scope, approves changes, controls repository
settings and decides whether evidence satisfies a format or hardware gate.

Contributors may propose, implement, test and review changes. Repeated or
substantial contribution does not by itself grant release or repository
administration authority. Additional maintainers may be appointed publicly in
this file and, if introduced, `.github/CODEOWNERS`.

## Decision process

Decisions are made in this order of priority:

1. Preserve source images, private working sessions and recoverable changes.
2. Protect filesystem structure, geometry, catalogue metadata and loader
   behaviour on the selected Amiga target.
3. Preserve secure fail-closed handling of untrusted media, archives, local
   paths, subprocesses, sessions and physical devices.
4. Prefer reproducible generated-media, emulator and real-hardware evidence.
5. Keep web, native Linux and CLI behaviour within the documented platform
   contract.
6. Prefer one authoritative, maintainable implementation over duplicated
   format or interface logic.
7. Reject an unsupported operation explicitly rather than silently changing or
   discarding data.

Normal changes are decided through pull-request review. Significant image
format, security, licensing, dependency, hardware-support, compatibility or
release-policy changes require an issue describing the proposal before
implementation. The maintainer records the final decision and its evidence in
the issue or pull request.

## Compatibility and evidence

Automated tests are necessary but do not override results from supported
physical hardware. A change that affects FFS maps and directories, OFS
catalogues, HDF menus, FastFileSystem, Hardfile geometry, Tube coexistence, loader
conversion, physical disk writing or emulator firmware remains a hardware-test
candidate until the applicable checks in
`docs/RELEASE-CHECKLIST.md` pass.

An image accepted by another tool is useful evidence, but it is not sufficient
on its own. A format claim should identify the geometry, filing-system variant,
target machine and mutation tested. Repairs must be deterministic, reviewable
and covered by rollback. Uncertain token streams, protected media, unfamiliar
menu programs and ambiguous loaders remain read-only or require an explicit
user decision.

The platform contract in `docs/PLATFORM-CONTRACT.md` is mandatory. A shared
feature is incomplete if the web and native editions expose different
filesystem results, validation or recovery behaviour without a documented host
capability boundary.

## Releases

Only the maintainer creates a release. A release must identify its source
commit, version, dependency revisions, artifact hashes, completed architecture
and hardware validation matrix, and known limitations. Generated containers,
native packages, documentation, firmware selections and saved-media fixtures
must match the reviewed source.

A release candidate may retain named hardware gates, but it must not be
described as validated for those targets. The release process follows
`docs/RELEASE-CHECKLIST.md`. Signing policy and redistribution rights must be
settled before publishing signed native or container artifacts.

Repository protection, required checks, private vulnerability reporting and
release permissions require periodic administrator review because GitHub does
not derive those settings from this document.

## Upstream and third-party material

Amiganut, HxCFloppyEmulator (HxCFE), FS-UAE, vAmiga, 1MHzWifi, MAME, noVNC,
websockify, Greaseweazle
and system packages retain their upstream ownership and terms. Changes intended
for an upstream project should remain reviewable and suitable for submission
there. Third-party ROMs, disk images, dmss, credentials and private hardware
media must not be committed as fixtures or added to release packages without a
recorded redistribution basis.

Read `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md` and `firmware/README.md`
before importing source or binaries. The project MIT licence does not grant
rights to firmware, emulator ROM sets or software contained in user media.

## Conduct and security

Participation is governed by `CODE_OF_CONDUCT.md`. Security reports follow
`SECURITY.md`. A security or conduct report must not be moved into a public
issue without the reporter's agreement.
