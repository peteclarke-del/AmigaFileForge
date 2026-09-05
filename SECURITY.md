# Security policy

Amiga File Forge parses untrusted disk, dms, ROM, archive and hard-drive
images, runs format tools, and can launch emulators. Security reports are
therefore welcome even when the default deployment is a single-user local
service.

## Supported versions

| Version | Security fixes |
| --- | --- |
| Current `main` branch | Yes |
| Latest published release candidate or release | Yes |
| Older tags and unmaintained branches | No |

Until a stable release is published, fixes may land on `main` before a new
release candidate is available.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private
[Report a vulnerability](https://github.com/peteclarke-del/AmigaFileForge/security/advisories/new)
workflow. If that workflow is unavailable, contact the repository owner
privately through the address published on the maintainer's GitHub profile.

Include:

- the affected version or commit;
- Docker, native Linux or both;
- host architecture and operating system;
- a minimal reproduction that does not contain copyrighted or personal media;
- the security boundary crossed and the expected boundary;
- logs, stack traces and hashes after removing secrets and local paths;
- whether the issue is already being exploited or publicly discussed.

You should receive an acknowledgement within five working days. Triage will
establish severity, affected versions, disclosure timing and whether a release
is required. These are response targets, not a service-level agreement.

## Security boundaries

Reports are especially useful for:

- archive traversal, decompression bombs or unbounded image parsing;
- access to another browser user's working session or recovered image;
- bypass of the desktop loopback token or local-path restrictions;
- command or argument injection into filesystem tools, emulators or
  Greaseweazle;
- unauthorised writes to host files, physical disks or source images;
- unsafe HTML rendering from filenames, metadata or online catalogues;
- forged save packages, checkpoints, patches or workflow recipes;
- remote exposure caused by incorrect proxy, cookie or origin handling;
- vulnerable bundled dependencies or firmware provenance problems.

The application does not provide a hostile multi-tenant sandbox. Emulator
guests and third-party firmware are isolated from working images where the
documented workflow says so, but they should not be treated as trusted code.
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the dependency and
firmware boundary.

## Safe research

Use generated media and a disposable working volume. Do not test against
systems or data you do not own. Avoid publishing an exploit before a fix and
coordinated disclosure are available. The project does not currently operate a
bug bounty or promise payment for reports.

## Outbound network policy

The online library and metadata lookup are the only features that make outbound
requests. Requests are restricted to `http` and `https`, and to publicly
routable addresses. A catalogue source that resolves to a loopback, private,
link-local, reserved or multicast address is refused before any connection is
made, so an editable source list cannot be used to reach services that only the
host can see.

The check runs immediately before each request rather than when a source is
saved, because a destination that was acceptable when configured may not be
when it is used. It narrows the reachable target set; it does not defeat a name
that resolves differently between the check and the connection.

Set `AMIGA_ALLOW_PRIVATE_SOURCES=1` to permit a local archive mirror on a
trusted network. It is off by default so the safe behaviour does not depend on
the operator noticing.
