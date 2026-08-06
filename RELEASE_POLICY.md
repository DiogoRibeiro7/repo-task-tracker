# Release Policy

## Scope

This project publishes release tags as `v*` (for example `v1.1.0`).

## Requirements

- Release tags must be **annotated**.
- Release tags must be **signed** (PGP or SSH signature block in tag object).
- GitHub releases must be created from an existing signed tag.

## Enforcement

- The `Release` workflow runs when any `v*` tag is pushed.
- The workflow validates that every `v*` tag is annotated and signed.
- The workflow rejects tags whose target commit is not contained in `main`.
- Full version tags such as `v1.1.0` create GitHub Releases. Moving major tags
  such as `v1` are verified but do not create releases.
- If GitHub does not start the tag-triggered workflow, run the same `Release`
  workflow manually and provide the existing signed tag.

## Recommended process

1. Prepare release changes on `develop`.
2. Open and merge a PR from `develop` into `main`.
3. Check out the updated `main` branch locally.
4. Create a signed annotated tag on `main`.
5. Push the tag to origin.
6. GitHub Actions verifies the tag and publishes the GitHub Release.
7. After Zenodo is connected to this GitHub repository, archive the published
   GitHub Release on Zenodo so it mints a software DOI using `.zenodo.json`.
