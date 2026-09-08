"""Python source → normalized facts via the stdlib ast module. No call graph."""

from __future__ import annotations

import ast
from typing import Any

from app.analysis.source_models import (
    HTTP_CLIENT_ROOTS,
    HTTP_METHODS,
    MODULE_SCOPE,
    ROUTE_METHODS,
    AssignmentFact,
    AttributeAccessFact,
    ClassFact,
    EnvironmentVariableFact,
    FunctionCallFact,
    FunctionFact,
    HttpRequestFact,
    ImportFact,
    ParseWarning,
    ReturnFact,
    RouteFact,
)


def extract_python(path: str, source: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        warning = ParseWarning(
            file=path,
            language="python",
            kind="syntax_error",
            message=exc.msg or "syntax error",
            line=exc.lineno,
        )
        return [], [warning.to_dict()]
    except Exception as exc:  # noqa: BLE001
        warning = ParseWarning(
            file=path,
            language="python",
            kind="parse_error",
            message=str(exc),
        )
        return [], [warning.to_dict()]

    visitor = _PythonFacts(path, source)
    visitor.visit(tree)
    return visitor.facts, []


class _PythonFacts(ast.NodeVisitor):
    def __init__(self, path: str, source: str) -> None:
        self.path = path
        self.source = source
        self.language = "python"
        self.facts: list[dict[str, Any]] = []
        self._scopes: list[str] = []

    def _scope(self) -> str:
        return self._scopes[-1] if self._scopes else MODULE_SCOPE

    def _emit(self, model: Any) -> None:
        self.facts.append(model.to_dict())

    def _src(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        try:
            text = ast.get_source_segment(self.source, node)
            if text:
                return " ".join(text.split())
        except Exception:
            pass
        try:
            return ast.unparse(node)
        except Exception:
            return None

    def _dotted(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._dotted(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        if isinstance(node, ast.Subscript):
            base = self._dotted(node.value)
            index = self._arg(node.slice)
            if base and index is not None:
                return f"{base}[{index}]"
            return base
        if isinstance(node, ast.Call):
            return self._dotted(node.func)
        return None

    def _arg(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return node.value
            return repr(node.value)
        dotted = self._dotted(node)
        if dotted:
            return dotted
        return self._src(node)

    def _params(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
        params: list[str] = []
        for arg in [*node.args.posonlyargs, *node.args.args]:
            params.append(arg.arg)
        if node.args.vararg:
            params.append(f"*{node.args.vararg.arg}")
        params.extend(arg.arg for arg in node.args.kwonlyargs)
        if node.args.kwarg:
            params.append(f"**{node.args.kwarg.arg}")
        return params

    def _decorators(self, node: ast.AST) -> list[str]:
        out: list[str] = []
        for deco in getattr(node, "decorator_list", []) or []:
            name = self._dotted(deco) or self._src(deco)
            if name:
                out.append(name)
        return out

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._emit(
                ImportFact(
                    file=self.path,
                    line=node.lineno,
                    language=self.language,
                    module=alias.name,
                    names=[],
                    alias=alias.asname,
                    is_from=False,
                )
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = ("." * (node.level or 0)) + (node.module or "")
        names = [alias.name for alias in node.names]
        self._emit(
            ImportFact(
                file=self.path,
                line=node.lineno,
                language=self.language,
                module=module,
                names=names,
                alias=None,
                is_from=True,
            )
        )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._emit(
            FunctionFact(
                file=self.path,
                line=node.lineno,
                language=self.language,
                name=node.name,
                scope=self._scope(),
                parameters=self._params(node),
                decorators=self._decorators(node),
                is_async=isinstance(node, ast.AsyncFunctionDef),
            )
        )
        for deco in node.decorator_list:
            route = self._route_from_call(deco, handler=node.name)
            if route:
                self._emit(route)
        self._scopes.append(node.name)
        self.generic_visit(node)
        self._scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = [self._dotted(b) or self._src(b) or "" for b in node.bases]
        self._emit(
            ClassFact(
                file=self.path,
                line=node.lineno,
                language=self.language,
                name=node.name,
                scope=self._scope(),
                bases=[b for b in bases if b],
                decorators=self._decorators(node),
            )
        )
        self._scopes.append(node.name)
        self.generic_visit(node)
        self._scopes.pop()

    def visit_Call(self, node: ast.Call) -> None:
        callee = self._dotted(node.func) or self._src(node.func) or ""
        arguments = [self._arg(a) or "<expr>" for a in node.args]
        for kw in node.keywords:
            if kw.arg:
                arguments.append(f"{kw.arg}={self._arg(kw.value) or '<expr>'}")
        self._emit(
            FunctionCallFact(
                file=self.path,
                line=node.lineno,
                language=self.language,
                scope=self._scope(),
                callee=callee,
                arguments=arguments,
            )
        )
        http = self._http_request(node, callee, arguments)
        if http:
            self._emit(http)
        env = self._env_from_call(node, callee)
        if env:
            self._emit(env)
        if len(node.args) >= 2:
            route = self._route_from_call(node)
            if route and route.path:
                self._emit(route)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        value = self._arg(node.value)
        for target in node.targets:
            self._emit(
                AssignmentFact(
                    file=self.path,
                    line=node.lineno,
                    language=self.language,
                    scope=self._scope(),
                    target=self._dotted(target) or self._src(target) or "<target>",
                    value=value,
                )
            )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._emit(
                AssignmentFact(
                    file=self.path,
                    line=node.lineno,
                    language=self.language,
                    scope=self._scope(),
                    target=self._dotted(node.target) or self._src(node.target) or "<target>",
                    value=self._arg(node.value),
                )
            )
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._emit(
            AssignmentFact(
                file=self.path,
                line=node.lineno,
                language=self.language,
                scope=self._scope(),
                target=self._dotted(node.target) or self._src(node.target) or "<target>",
                value=self._arg(node.value),
            )
        )
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        self._emit(
            ReturnFact(
                file=self.path,
                line=node.lineno,
                language=self.language,
                scope=self._scope(),
                value=self._arg(node.value) if node.value is not None else None,
            )
        )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        obj = self._dotted(node.value) or self._src(node.value) or ""
        self._emit(
            AttributeAccessFact(
                file=self.path,
                line=node.lineno,
                language=self.language,
                scope=self._scope(),
                object=obj,
                attribute=node.attr,
            )
        )
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        base = self._dotted(node.value)
        if base in {"os.environ", "os.getenv"}:
            self._emit(
                EnvironmentVariableFact(
                    file=self.path,
                    line=node.lineno,
                    language=self.language,
                    scope=self._scope(),
                    name=self._arg(node.slice),
                    accessor=base,
                )
            )
        self.generic_visit(node)

    def _env_from_call(self, node: ast.Call, callee: str) -> EnvironmentVariableFact | None:
        if callee not in {"os.getenv", "os.environ.get", "getenv"}:
            return None
        name = self._arg(node.args[0]) if node.args else None
        return EnvironmentVariableFact(
            file=self.path,
            line=node.lineno,
            language=self.language,
            scope=self._scope(),
            name=name,
            accessor=callee,
        )

    def _http_request(
        self, node: ast.Call, callee: str, arguments: list[str]
    ) -> HttpRequestFact | None:
        method = _http_method(callee)
        if not method:
            return None
        return HttpRequestFact(
            file=self.path,
            line=node.lineno,
            language=self.language,
            scope=self._scope(),
            callee=callee,
            method=method,
            url=arguments[0] if arguments else None,
        )

    def _route_from_call(self, node: ast.AST, handler: str | None = None) -> RouteFact | None:
        if not isinstance(node, ast.Call):
            if isinstance(node, ast.Attribute):
                return None
            return None
        callee = self._dotted(node.func) or ""
        last = callee.rsplit(".", 1)[-1].lower() if callee else ""
        if last not in ROUTE_METHODS:
            return None
        if "." not in callee and last != "route":
            return None
        path = self._arg(node.args[0]) if node.args else None
        if last == "route":
            method = "GET"
            for kw in node.keywords:
                if kw.arg == "methods":
                    raw = self._arg(kw.value) or ""
                    for candidate in HTTP_METHODS:
                        if candidate.upper() in raw.upper():
                            method = candidate.upper()
                            break
        else:
            method = last.upper()
        handler_name = handler
        if handler_name is None and len(node.args) > 1:
            handler_name = self._dotted(node.args[1]) or self._arg(node.args[1])
        return RouteFact(
            file=self.path,
            line=node.lineno,
            language=self.language,
            scope=self._scope(),
            method=method,
            path=path,
            handler=handler_name,
            callee=callee,
        )


def _http_method(callee: str) -> str | None:
    if callee in {"fetch", "urllib.request.urlopen"}:
        return "GET"
    if callee.endswith(".fetch"):
        return "GET"
    parts = callee.split(".")
    if len(parts) < 2:
        return None
    root, last = parts[0], parts[-1].lower()
    if root in HTTP_CLIENT_ROOTS and last in HTTP_METHODS | {"request"}:
        return last.upper() if last != "request" else "REQUEST"
    return None
