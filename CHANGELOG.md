# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [Unreleased]

## [1.1.1](https://github.com/DiogoRibeiro7/repo-task-tracker/compare/v1.1.0...v1.1.1) - 2026-08-06

### Changed
- Release workflow now supports a manual fallback for publishing an existing
  signed tag if the tag-triggered workflow does not start.
- Release creation is idempotent when the GitHub Release already exists.

## [1.1.0](https://github.com/DiogoRibeiro7/repo-task-tracker/compare/v1.0.5...v1.1.0) - 2026-08-06

### Added
- Zenodo metadata for GitHub release archiving.
- Citation metadata with the all-versions Zenodo DOI.
- README DOI badge and citation guidance.

### Changed
- Package, Zenodo, and citation metadata now target version `1.1.0`.
- Release automation now publishes directly from pushed signed `v*` tags.
- Release documentation now includes the Zenodo archive step.

## [1.0.4] - 2026-03-11

### Added
- Validation-only mode, dry-run mode, and orphan handling controls.
- Support for `assignees`, `milestone`, `depends_on`, and multi-file tracker glob processing.
- GitHub step summary reporting and REST integration test coverage.

### Changed
- Added rate-limit buffering and secondary-rate-limit retry handling.
- CI now includes mypy type-checking plus release guardrails for signed tags and manual release publishing.

## [1.0.3] - 2026-03-09

### Changed
- Marketplace metadata and release packaging updates.

## [1.0.2] - 2026-03-09

### Changed
- Initial public release metadata and action distribution updates.
