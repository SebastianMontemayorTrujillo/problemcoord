"""Model adapter boundary: review(request) -> ReviewDraft (or raw JSON), or a typed HWError."""

from typing import Any, Protocol

from hwreview.contracts import ReviewDraft, ReviewRequest


class ModelAdapter(Protocol):
    config_id: str  # becomes VersionKey.provider_config_id

    def review(self, request: ReviewRequest) -> ReviewDraft | dict[str, Any] | str: ...
