import os
from dataclasses import dataclass, field
from typing import Final

@dataclass
class StorageConfig:
    DOWNLOAD_DIR: str = field(default_factory=lambda: os.getenv('DOWNLOAD_DIR', '/app/downloads'))
    TASKS_FILE: str = field(default_factory=lambda: os.getenv('TASKS_FILE', 'jsons/tasks.json'))
    KEYS_FILE: str = field(default_factory=lambda: os.getenv('KEYS_FILE', 'jsons/api_keys.json'))
    COOKIES_FILE: str = field(default_factory=lambda: os.getenv('COOKIES_FILE', '/tmp/cookies.txt'))
    COOKIES_CONTENT: str = field(default_factory=lambda: os.getenv('COOKIES_CONTENT', ''))
    COOKIES_B64: str = field(default_factory=lambda: os.getenv('COOKIES_B64', ''))

@dataclass
class TaskConfig:
    CLEANUP_TIME_MINUTES: Final[int] = 10
    REQUEST_LIMIT: Final[int] = 60
    MAX_WORKERS: Final[int] = 4

@dataclass
class MemoryConfig:
    DEFAULT_QUOTA_GB: Final[int] = 5
    DEFAULT_QUOTA_BYTES: Final[int] = 5 * 1024 * 1024 * 1024
    QUOTA_RATE_MINUTES: Final[int] = 10
    SIZE_BUFFER: Final[float] = 1.10
    AVAILABLE_BYTES: Final[int] = 20 * 1024 * 1024 * 1024

storage = StorageConfig()
task = TaskConfig()
memory = MemoryConfig()
