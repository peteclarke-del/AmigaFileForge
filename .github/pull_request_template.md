## Summary

Describe the user-visible result and the technical boundary changed.

## Risk and compatibility

- Formats and target machines:
- Source-image and rollback behaviour:
- Metadata, geometry or capacity implications:
- Web, native Linux and CLI parity:

## Verification

- [ ] Focused Python regressions pass.
- [ ] JavaScript syntax and unit regressions pass where applicable.
- [ ] Browser regressions pass for frontend changes.
- [ ] Docker and affected architecture builds pass.
- [ ] Generated-media, corruption and cancellation cases are covered.
- [ ] No private or copyrighted fixture has been added.

List exact commands and relevant real-hardware evidence:

## Engineering review

- [ ] The change reuses authoritative services and does not duplicate format logic.
- [ ] Untrusted paths, filenames, metadata, archives and subprocess arguments are bounded and validated.
- [ ] Changed controls meet keyboard, focus, labelling, contrast and status-announcement requirements.
- [ ] Main documentation, specialist guide and in-app help use current terminology.
- [ ] Dependency, firmware and copied-asset changes update third-party notices and provenance.
