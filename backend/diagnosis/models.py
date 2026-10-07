"""The model can explain observations; it cannot supply executable instructions."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from backend.domain.models import Model


class RouteDraft(Model):
    name: str = Field(min_length=1, max_length=80)
    connector_id: str
    model: str = Field(pattern=r"^[A-Za-z0-9_-]+/[A-Za-z0-9_.:/-]{1,160}$")
    response_model: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,160}$")
    provider: str = Field(pattern=r"^[A-Za-z0-9_-]{1,60}$")
    free_terms_url: str = Field(max_length=512)
    qualification_note: str = Field(min_length=20, max_length=1000)
    expires_at: datetime
    free_only_gateway_verified: Literal[True]
    priority: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def reviewed_route(self):
        if any(part in {"auto", "combo", "free", "random"} for part in self.model.lower().split("/")):
            raise ValueError("Select an explicit provider/model route, not a routing alias")
        url = urlsplit(self.free_terms_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Supply an HTTPS provider terms page without embedded credentials")
        if self.expires_at.tzinfo is None or not datetime.now(UTC) < self.expires_at <= datetime.now(
            UTC
        ) + timedelta(days=30):
            raise ValueError("Route eligibility must expire within 30 days")
        return self


class Subject(Model):
    kind: Literal["incident", "recommendation"]
    id: str = Field(min_length=1, max_length=100)


class Claim(Model):
    explanation: str = Field(min_length=1, max_length=1200)
    confidence: Literal["low", "medium", "high"]
    citations: list[str] = Field(min_length=1, max_length=12)


class NextStep(Model):
    kind: Literal["measure", "physical_inspection", "review_configuration"]
    device_id: str
    description: str = Field(min_length=1, max_length=1000)
    citations: list[str] = Field(min_length=1, max_length=12)
    runbook_id: Literal["RB01", "RB02", "RB03", "RB04", "RB05", "RB06", "RB07"] | None = None


class Diagnosis(Model):
    summary: str = Field(min_length=1, max_length=1600)
    hypotheses: list[Claim] = Field(min_length=1, max_length=6)
    alternatives: list[Claim] = Field(default_factory=list, max_length=4)
    missing_information: list[str] = Field(default_factory=list, max_length=12)
    next_steps: list[NextStep] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def bounded_text(self):
        if any(len(item) > 500 for item in self.missing_information):
            raise ValueError("Missing-information entry exceeds its limit")
        if any(step.runbook_id and step.kind != "review_configuration" for step in self.next_steps):
            raise ValueError("Runbooks belong to configuration review steps")
        return self
