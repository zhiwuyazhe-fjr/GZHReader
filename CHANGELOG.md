# Changelog

All notable changes to this project will be documented in this file.

## [v2.0.0] - 2026-03-27

### Changed
- Rebuilt the product around a bundled local RSS service instead of a Docker-managed workflow.
- Replaced the old wizard-style control panel with a calmer editorial workspace and a dedicated settings page.
- Switched the primary runtime model to local SQLite-backed storage for both GZHReader and bundled `wewe-rss`, using separate database files.
- Renamed CLI service management from Docker-centric commands to `gzhreader service ...`.

### Added
- Vendored upstream `wewe-rss` source under `third_party/wewe-rss/`.
- Added `THIRD_PARTY_NOTICES.md` and a recorded design baseline in `.impeccable.md`.
- Added `scripts/build_wewe_rss.ps1` to build a distributable bundled `wewe-rss` runtime.
- Added packaged runtime support in the PyInstaller/Inno Setup build chain.
- Added a new local service manager with health checks, PID management, log tailing, and admin URL launching.
- Added new home/settings templates and a dual-theme editorial UI system.

### Removed
- Removed Docker Desktop, WSL, MySQL, compose scaffolding, and container-first language from the primary product path.
- Removed the old step-by-step onboarding wizard, Docker-blocked landing page, and sidebar-heavy control panel layout.

## [v1.5.0] - 2026-03-14

### Changed
- Refined the earlier dashboard layout, Docker onboarding copy, and status presentation.
