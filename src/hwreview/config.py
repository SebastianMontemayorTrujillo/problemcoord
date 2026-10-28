"""Limits. ALL VALUES ARE PROPOSED (docs/contracts.md) until the user approves them."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    max_files_per_batch: int = 5
    max_bytes_per_file: int = 5 * 1024 * 1024
    max_pages_per_file: int = 10
    max_pages_per_batch: int = 25
    max_active_model_requests: int = 1
    provider_timeout_s: float = 45.0
    max_attempts: int = 2
    lease_s: float = 60.0
    heartbeat_s: float = 15.0
    min_chars_per_page: int = 20
    token_cap: int | None = None  # UNSET: blocks live provider calls

    def __post_init__(self):
        # Heartbeats happen at stage boundaries, so one provider call must fit inside a lease.
        if self.provider_timeout_s >= self.lease_s:
            raise ValueError("provider_timeout_s must be shorter than lease_s")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")


DEFAULT_LIMITS = Limits()
EXTRACTION_VERSION = "pypdf-text:v1"
