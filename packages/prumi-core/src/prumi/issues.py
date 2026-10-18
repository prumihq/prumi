"""Issues: what the validator, the engine and the APIs report back (codes: docs/bi-rules.md)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict

Severity = Literal["error", "warning"]


class Issue(BaseModel):
    """One problem. `path` uses the spec's field names and list indexes ("data[1].others").
    Messages are written for the agent: what's wrong and how to fix it."""

    model_config = ConfigDict(frozen=True)

    code: str
    severity: Severity
    path: str
    message: str
    hint: str = ""


def error(code: str, path: str, message: str, hint: str = "") -> Issue:
    return Issue(code=code, severity="error", path=path, message=message, hint=hint)


def warning(code: str, path: str, message: str, hint: str = "") -> Issue:
    return Issue(code=code, severity="warning", path=path, message=message, hint=hint)


class Report(BaseModel):
    model_config = ConfigDict(frozen=True)

    errors: tuple[Issue, ...] = ()
    warnings: tuple[Issue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def issues(self) -> tuple[Issue, ...]:
        return self.errors + self.warnings

    @classmethod
    def of(cls, issues: Iterable[Issue]) -> Report:
        unique = list(dict.fromkeys(issues))  # the same issue found twice is reported once
        return cls(
            errors=tuple(i for i in unique if i.severity == "error"),
            warnings=tuple(i for i in unique if i.severity == "warning"),
        )
