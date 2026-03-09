# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [Unreleased]

### Changed
- Improved release-facing documentation and repository publishing guidance.
- Standardized version maintenance around a single `__version__` source.

### Fixed
- LLM API key is no longer rendered back into the GUI in plain text.
- GUI LLM status now honors `OPENAI_API_KEY` when local config does not contain an API key.
- Advanced YAML editor no longer exposes the saved LLM API key in page HTML.

## [0.2.0] - 2026-03-08

### Added
- GUI-first workflow for configuring `wewe-rss`, LLM settings, output directory, and schedule.
- Daily briefing pipeline based on aggregate RSS, SQLite storage, optional content fetching, and LLM summarization.
- Windows packaging flow based on PyInstaller and Inno Setup.
- Automated test suite for configuration, web UI, runtime paths, scheduling, storage, summarization, and RSS workflow.

### Changed
- Consolidated feed handling around a single aggregate source (`all.atom`).
- Made Markdown the default end product for generated briefings.

### Notes
- Current release target is Windows.
- Docker Desktop remains an external dependency and is not bundled into the installer.
