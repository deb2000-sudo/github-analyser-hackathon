"""Language-independent source facts. Later analyzers consume these dicts, not AST nodes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _to_dict(obj: Any) -> dict[str, Any]:
    data = asdict(obj)
    fact_type = data.pop("fact_type")
    return {"fact_type": fact_type, **data}


@dataclass
class ImportFact:
    file: str
    line: int
    language: str
    module: str
    names: list[str] = field(default_factory=list)
    alias: str | None = None
    is_from: bool = False
    fact_type: str = "import"

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass
class FunctionFact:
    file: str
    line: int
    language: str
    name: str
    scope: str
    parameters: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    is_async: bool = False
    fact_type: str = "function"

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass
class ClassFact:
    file: str
    line: int
    language: str
    name: str
    scope: str
    bases: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    fact_type: str = "class"

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass
class FunctionCallFact:
    file: str
    line: int
    language: str
    scope: str
    callee: str
    arguments: list[str] = field(default_factory=list)
    fact_type: str = "function_call"

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass
class AssignmentFact:
    file: str
    line: int
    language: str
    scope: str
    target: str
    value: str | None = None
    fact_type: str = "assignment"

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass
class ReturnFact:
    file: str
    line: int
    language: str
    scope: str
    value: str | None = None
    fact_type: str = "return"

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass
class RouteFact:
    file: str
    line: int
    language: str
    scope: str
    method: str
    path: str | None
    handler: str | None = None
    callee: str | None = None
    fact_type: str = "route"

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass
class HttpRequestFact:
    file: str
    line: int
    language: str
    scope: str
    callee: str
    method: str | None = None
    url: str | None = None
    fact_type: str = "http_request"

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass
class EnvironmentVariableFact:
    file: str
    line: int
    language: str
    scope: str
    name: str | None
    accessor: str
    fact_type: str = "environment_variable"

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass
class AttributeAccessFact:
    file: str
    line: int
    language: str
    scope: str
    object: str
    attribute: str
    fact_type: str = "attribute_access"

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass
class ParseWarning:
    file: str
    language: str
    kind: str
    message: str
    line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


MODULE_SCOPE = "<module>"
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
HTTP_CLIENT_ROOTS = frozenset(
    {"requests", "httpx", "aiohttp", "axios", "got", "superagent", "urllib3", "urllib"}
)
ROUTE_METHODS = HTTP_METHODS | {"all", "route"}
