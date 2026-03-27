from __future__ import annotations

from .rss_service import BundledRSSServiceManager, RSSServiceRuntimeStatus

# Backward-compatible exports while the repo migrates from Docker-focused naming
# to the bundled local RSS service terminology.
WeWeRSSManager = BundledRSSServiceManager
WeWeRSSRuntimeStatus = RSSServiceRuntimeStatus
