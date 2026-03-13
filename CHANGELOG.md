# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [Unreleased]

### Changed
- Codecov patch coverage gate now uses repository config (`target: 90%`, `threshold: 1%`).

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
