"""All cleanup tuning. Durations are seconds, workers/batch sizes are counts."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CleanupSettings:
    workers: int = 2
    scan_seconds: int = 1
    reconcile_seconds: int = 60
    lease_seconds: int = 120
    retry_seconds: int = 10
    retry_max_seconds: int = 3600
    orphan_guard_seconds: int = 300

    def __post_init__(self):
        if any(value <= 0 for value in vars(self).values()):
            raise ValueError('Cleanup settings must be positive')
        if self.retry_max_seconds < self.retry_seconds:
            raise ValueError('Cleanup retry maximum must cover the initial delay')

    @classmethod
    def from_env(cls):
        return cls(**{name: int(os.environ['CLEANUP_' + name.upper()])
                      for name in cls.__dataclass_fields__
                      if 'CLEANUP_' + name.upper() in os.environ})


SETTINGS = CleanupSettings.from_env()
