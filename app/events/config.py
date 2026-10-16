"""Queue tuning. All intervals are seconds; attempt limits include the first try."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class QueueSettings:
    scan_seconds: int = 5
    reconnect_seconds: int = 5
    lease_seconds: int = 120
    heartbeat_seconds: int = 20
    retry_seconds: int = 30
    task_attempts: int = 3
    delivery_attempts: int = 5
    delivery_workers: int = 2

    def __post_init__(self):
        if any(value <= 0 for value in vars(self).values()):
            raise ValueError('Queue settings must be positive')
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError('Heartbeat must be shorter than lease')

    @classmethod
    def from_env(cls):
        return cls(**{name: int(os.environ['QUEUE_' + name.upper()])
                      for name in cls.__dataclass_fields__
                      if 'QUEUE_' + name.upper() in os.environ})


SETTINGS = QueueSettings.from_env()
CHANNEL = 'whisper_events'
