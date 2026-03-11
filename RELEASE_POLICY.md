# Release Policy

## Scope

This project publishes release tags as `v*` (for example `v1.0.4`).

## Requirements

- Release tags must be **annotated**.
- Release tags must be **signed** (PGP or SSH signature block in tag object).
- GitHub releases must be created from an existing signed tag.

## Enforcement

- CI workflow `Tag Signature Policy` validates every pushed `v*` tag.
- Manual release workflow validates tag signature before publishing release notes.

## Recommended process

1. Create a signed annotated tag locally.
2. Push the tag to origin.
3. Run the `Release` workflow and provide the existing tag name.
