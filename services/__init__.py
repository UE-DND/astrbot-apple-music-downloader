"""
Apple Music Downloader Services Module
"""

from .downloader import (
    ConfigGenerator,
    DockerService,
    DownloadQuality,
    DownloadResult,
    ServiceStatus,
    URLParser,
    MetadataFetcher,
)
from .queue import (
    DownloadQueue,
    DownloadTask,
    TaskStatus,
    TaskPriority,
    QueueStats,
)

__all__ = [
    # downloader
    "ConfigGenerator",
    "DockerService",
    "DownloadQuality",
    "DownloadResult",
    "ServiceStatus",
    "URLParser",
    "MetadataFetcher",
    # queue
    "DownloadQueue",
    "DownloadTask",
    "TaskStatus",
    "TaskPriority",
    "QueueStats",
]
