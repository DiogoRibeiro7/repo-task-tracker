# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [1.1.0](https://github.com/DiogoRibeiro7/repo-task-tracker/compare/v1.0.3...v1.1.0) (2026-03-13)


### Features

* add configurable orphan issue handling modes ([139dcee](https://github.com/DiogoRibeiro7/repo-task-tracker/commit/139dcee2f3a17b6e90064ff264586f9a0c84965d))
* add dry-run mode for issue and project sync operations ([6a13a8c](https://github.com/DiogoRibeiro7/repo-task-tracker/commit/6a13a8caa4db11f0bb6f171665eb338e0bcdaf78))
* add GitHub step summary reporting for sync actions ([21813ca](https://github.com/DiogoRibeiro7/repo-task-tracker/commit/21813ca55891b352856b37c30b5f694ea05548dd))
* add multi-file tracker glob processing with per-file sync ([80951a9](https://github.com/DiogoRibeiro7/repo-task-tracker/commit/80951a923ebf82762bd578967d2e9be510397e89))
* add rate-limit buffering and secondary-limit retry ([fdfe940](https://github.com/DiogoRibeiro7/repo-task-tracker/commit/fdfe94065b226bab74b43225093f9b4d8a9bd604))
* add task dependency rendering and cycle detection ([9a2cab6](https://github.com/DiogoRibeiro7/repo-task-tracker/commit/9a2cab6bd2e005e4d29acaa9075e32debc06ed1b))
* add validate-only mode with config validation and CLI flag ([defbe8c](https://github.com/DiogoRibeiro7/repo-task-tracker/commit/defbe8c305ef94a2ccf923f1d845bc7fc878c6f0))
* strengthen config validation with source-aware errors ([226aa37](https://github.com/DiogoRibeiro7/repo-task-tracker/commit/226aa37e7c7952927f76c37a260e160c19e99d0b))
* support assignees and milestone fields in tasks ([975fb49](https://github.com/DiogoRibeiro7/repo-task-tracker/commit/975fb49c2e0e85f11bec36de9580143631bca836))

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
