"""JavaScript/TypeScript source → normalized facts via Tree-sitter. No call graph."""

from __future__ import annotations

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

_LANG_JS = "javascript"
_LANG_TS = "typescript"
_PARSERS: dict[str, Any] = {}
_TS_LOAD_ERROR: str | None = None


def _load_parsers() -> None:
    global _TS_LOAD_ERROR
    if _PARSERS or _TS_LOAD_ERROR:
        return
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_javascript as tsjs
        import tree_sitter_typescript as tsts

        _PARSERS["javascript"] = Parser(Language(tsjs.language()))
        _PARSERS["typescript"] = Parser(Language(tsts.language_typescript()))
        _PARSERS["tsx"] = Parser(Language(tsts.language_tsx()))
    except Exception as exc:  # noqa: BLE001
        _TS_LOAD_ERROR = str(exc)


def extract_js_ts(
    path: str,
    source: str,
    *,
    language: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _load_parsers()
    if _TS_LOAD_ERROR:
        warning = ParseWarning(
            file=path,
            language=language,
            kind="parser_unavailable",
            message=_TS_LOAD_ERROR,
        )
        return [], [warning.to_dict()]

    key = _parser_key(path, language)
    parser = _PARSERS.get(key) or _PARSERS.get("javascript")
    if parser is None:
        warning = ParseWarning(
            file=path,
            language=language,
            kind="parser_unavailable",
            message="no tree-sitter grammar loaded",
        )
        return [], [warning.to_dict()]

    try:
        tree = parser.parse(source.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        warning = ParseWarning(
            file=path,
            language=language,
            kind="parse_error",
            message=str(exc),
        )
        return [], [warning.to_dict()]

    warnings: list[dict[str, Any]] = []
    if tree.root_node.has_error:
        err_line = _first_error_line(tree.root_node)
        warnings.append(
            ParseWarning(
                file=path,
                language=language,
                kind="syntax_error",
                message="syntax error in source",
                line=err_line,
            ).to_dict()
        )

    collector = _JsFacts(path, source, language)
    collector.walk(tree.root_node, [])
    return collector.facts, warnings


def _parser_key(path: str, language: str) -> str:
    lower = path.lower()
    if lower.endswith(".tsx") or lower.endswith(".jsx"):
        return "tsx"
    if language == "typescript" or lower.endswith(".ts"):
        return "typescript"
    return "javascript"


def _first_error_line(node: Any) -> int | None:
    if getattr(node, "type", None) == "ERROR":
        return node.start_point[0] + 1
    for child in getattr(node, "children", []) or []:
        line = _first_error_line(child)
        if line is not None:
            return line
    return node.start_point[0] + 1 if getattr(node, "has_error", False) else None


class _JsFacts:
    def __init__(self, path: str, source: str, language: str) -> None:
        self.path = path
        self.source = source
        self.language = language if language in {_LANG_JS, _LANG_TS} else _LANG_JS
        self.facts: list[dict[str, Any]] = []

    def _emit(self, model: Any) -> None:
        self.facts.append(model.to_dict())

    def _scope(self, stack: list[str]) -> str:
        return stack[-1] if stack else MODULE_SCOPE

    def _text(self, node: Any | None) -> str:
        if node is None:
            return ""
        raw = getattr(node, "text", None)
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        if isinstance(raw, str):
            return raw
        start = getattr(node, "start_byte", None)
        end = getattr(node, "end_byte", None)
        if start is None or end is None:
            return ""
        return self.source.encode("utf-8")[start:end].decode("utf-8", errors="replace")

    def _line(self, node: Any) -> int:
        return node.start_point[0] + 1

    def _named(self, node: Any, field: str) -> Any | None:
        return node.child_by_field_name(field)

    def _first_named(self, node: Any, types: set[str]) -> Any | None:
        for child in node.named_children:
            if child.type in types:
                return child
        return None

    def _ident(self, node: Any | None) -> str:
        if node is None:
            return ""
        if node.type in {"identifier", "property_identifier", "type_identifier", "shorthand_property_identifier"}:
            return self._text(node)
        found = self._first_named(node, {"identifier", "property_identifier", "type_identifier"})
        return self._text(found) if found is not None else ""

    def _dotted(self, node: Any | None) -> str:
        if node is None:
            return ""
        t = node.type
        if t in {"identifier", "property_identifier", "type_identifier"}:
            return self._text(node)
        if t in {"this", "super"}:
            return t
        if t == "member_expression":
            obj = self._dotted(self._named(node, "object"))
            prop = self._ident(self._named(node, "property")) or self._text(self._named(node, "property"))
            return f"{obj}.{prop}" if obj and prop else obj or prop
        if t == "subscript_expression":
            obj = self._dotted(self._named(node, "object"))
            index = self._arg(self._named(node, "index"))
            if obj and index is not None:
                return f"{obj}[{index}]"
            return obj
        if t == "call_expression":
            return self._dotted(self._named(node, "function"))
        if t == "new_expression":
            ctor = node.named_children[0] if node.named_children else None
            return self._dotted(ctor)
        return self._text(node).replace("\n", " ").strip()

    def _arg(self, node: Any | None) -> str | None:
        if node is None:
            return None
        t = node.type
        if t in {"identifier", "property_identifier"}:
            return self._text(node)
        if t == "template_string":
            text = self._text(node)
            if len(text) >= 2 and text[0] == "`" and text[-1] == "`":
                return text[1:-1]
            return text
        if t == "string":
            frag = self._first_named(node, {"string_fragment"})
            if frag is not None:
                return self._text(frag)
            text = self._text(node)
            return text[1:-1] if len(text) >= 2 and text[0] in {"'", '"'} else text
        if t in {"number", "true", "false", "null", "undefined"}:
            return self._text(node)
        dotted = self._dotted(node)
        return dotted or self._text(node).replace("\n", " ").strip() or None

    def _params(self, node: Any | None) -> list[str]:
        if node is None:
            return []
        if node.type == "identifier":
            return [self._text(node)]
        params: list[str] = []
        for child in node.named_children:
            if child.type == "identifier":
                params.append(self._text(child))
            elif child.type in {"required_parameter", "optional_parameter"}:
                ident = self._ident(child)
                if ident:
                    params.append(ident)
            elif child.type == "rest_parameter":
                ident = self._ident(child)
                if ident:
                    params.append(f"...{ident}")
            elif child.type == "assignment_pattern":
                ident = self._ident(child)
                if ident:
                    params.append(ident)
            elif child.type in {"object_pattern", "array_pattern"}:
                params.append(self._text(child))
        return params

    def _function_name_and_params(self, node: Any) -> tuple[str, list[str], bool]:
        name = self._ident(self._named(node, "name")) or self._ident(
            self._first_named(node, {"identifier", "property_identifier"})
        )
        params_node = self._named(node, "parameters") or self._first_named(node, {"formal_parameters"})
        if params_node is None and node.type == "arrow_function":
            for child in node.named_children:
                if child.type in {"formal_parameters", "identifier"}:
                    params_node = child
                    break
        is_async = any(getattr(c, "type", "") == "async" for c in node.children)
        return name or "<anonymous>", self._params(params_node), is_async

    def walk(self, node: Any, scopes: list[str]) -> None:
        t = node.type
        if t in {"comment", "html_comment"}:
            return

        if t == "import_statement":
            self._import_statement(node)
            return

        if t in {"function_declaration", "generator_function_declaration", "function_expression"}:
            self._function(node, scopes)
            return

        if t == "method_definition":
            self._method(node, scopes)
            return

        if t in {"class_declaration", "class"}:
            self._class(node, scopes)
            return

        if t == "variable_declarator":
            self._declarator(node, scopes)
            return

        if t == "arrow_function":
            self._function(node, scopes, name="<anonymous>")
            return

        if t == "call_expression":
            self._call(node, scopes, is_new=False)
        elif t == "new_expression":
            self._call(node, scopes, is_new=True)
        elif t == "return_statement":
            self._return(node, scopes)
        elif t == "assignment_expression":
            self._assignment(node, scopes)
        elif t == "member_expression":
            self._attribute(node, scopes)
        elif t == "subscript_expression":
            self._subscript(node, scopes)

        for child in node.children:
            if not getattr(child, "is_named", True) and child.type not in {"async"}:
                continue
            if child.type in {"comment"}:
                continue
            # Unnamed punctuation is skipped; named children are walked.
            if getattr(child, "is_named", False):
                self.walk(child, scopes)

    def _import_statement(self, node: Any) -> None:
        source = None
        for child in node.named_children:
            if child.type == "string":
                source = self._arg(child)
        names: list[str] = []
        alias = None
        clause = self._first_named(node, {"import_clause"})
        if clause is not None:
            for child in clause.named_children:
                if child.type == "identifier":
                    names.append(self._text(child))
                elif child.type == "namespace_import":
                    alias = self._ident(child)
                    names.append("*")
                elif child.type == "named_imports":
                    for spec in child.named_children:
                        ident = self._ident(spec)
                        if ident:
                            names.append(ident)
        self._emit(
            ImportFact(
                file=self.path,
                line=self._line(node),
                language=self.language,
                module=source or "",
                names=names,
                alias=alias,
                is_from=True,
            )
        )

    def _function(self, node: Any, scopes: list[str], name: str | None = None) -> None:
        parsed_name, params, is_async = self._function_name_and_params(node)
        fn_name = name or parsed_name
        self._emit(
            FunctionFact(
                file=self.path,
                line=self._line(node),
                language=self.language,
                name=fn_name,
                scope=self._scope(scopes),
                parameters=params,
                decorators=[],
                is_async=is_async,
            )
        )
        body_scopes = [*scopes, fn_name]
        for child in node.named_children:
            if child.type in {"statement_block", "class_body"}:
                self.walk(child, body_scopes)
            elif child.type not in {
                "identifier",
                "property_identifier",
                "formal_parameters",
                "type_annotation",
            }:
                self.walk(child, body_scopes)

    def _method(self, node: Any, scopes: list[str]) -> None:
        name, params, is_async = self._function_name_and_params(node)
        if not name:
            name = self._ident(self._first_named(node, {"property_identifier"})) or "<anonymous>"
        self._emit(
            FunctionFact(
                file=self.path,
                line=self._line(node),
                language=self.language,
                name=name,
                scope=self._scope(scopes),
                parameters=params,
                decorators=[],
                is_async=is_async,
            )
        )
        body_scopes = [*scopes, name]
        for child in node.named_children:
            if child.type == "statement_block":
                self.walk(child, body_scopes)

    def _class(self, node: Any, scopes: list[str]) -> None:
        name = self._ident(self._named(node, "name")) or self._ident(
            self._first_named(node, {"identifier", "type_identifier"})
        )
        bases: list[str] = []
        heritage = self._first_named(node, {"class_heritage"})
        if heritage is not None:
            for child in heritage.named_children:
                dotted = self._dotted(child)
                if dotted:
                    bases.append(dotted)
        self._emit(
            ClassFact(
                file=self.path,
                line=self._line(node),
                language=self.language,
                name=name or "<anonymous>",
                scope=self._scope(scopes),
                bases=bases,
                decorators=[],
            )
        )
        body_scopes = [*scopes, name or "<anonymous>"]
        body = self._first_named(node, {"class_body"})
        if body is not None:
            self.walk(body, body_scopes)

    def _declarator(self, node: Any, scopes: list[str]) -> None:
        name_node = self._named(node, "name") or self._first_named(node, {"identifier"})
        value = self._named(node, "value")
        target = self._ident(name_node) or self._text(name_node)
        self._emit(
            AssignmentFact(
                file=self.path,
                line=self._line(node),
                language=self.language,
                scope=self._scope(scopes),
                target=target or "<target>",
                value=self._arg(value) if value is not None else None,
            )
        )
        if value is not None and value.type in {
            "arrow_function",
            "function_expression",
            "generator_function",
        }:
            self._function(value, scopes, name=target or "<anonymous>")
            return
        if value is not None:
            self.walk(value, scopes)

    def _call(self, node: Any, scopes: list[str], *, is_new: bool) -> None:
        if is_new:
            ctor = node.named_children[0] if node.named_children else None
            callee = self._dotted(ctor)
            args_node = self._first_named(node, {"arguments"})
        else:
            callee = self._dotted(self._named(node, "function"))
            args_node = self._named(node, "arguments") or self._first_named(node, {"arguments"})
        arguments = [self._arg(c) or "<expr>" for c in (args_node.named_children if args_node else [])]
        self._emit(
            FunctionCallFact(
                file=self.path,
                line=self._line(node),
                language=self.language,
                scope=self._scope(scopes),
                callee=callee,
                arguments=arguments,
            )
        )
        if callee == "require" and arguments:
            self._emit(
                ImportFact(
                    file=self.path,
                    line=self._line(node),
                    language=self.language,
                    module=arguments[0],
                    names=[],
                    alias=None,
                    is_from=False,
                )
            )
        http = _http_method(callee)
        if http:
            self._emit(
                HttpRequestFact(
                    file=self.path,
                    line=self._line(node),
                    language=self.language,
                    scope=self._scope(scopes),
                    callee=callee,
                    method=http,
                    url=arguments[0] if arguments else None,
                )
            )
        if callee in {"os.getenv", "process.env.get"}:
            self._emit(
                EnvironmentVariableFact(
                    file=self.path,
                    line=self._line(node),
                    language=self.language,
                    scope=self._scope(scopes),
                    name=arguments[0] if arguments else None,
                    accessor=callee,
                )
            )
        last = callee.rsplit(".", 1)[-1].lower() if callee else ""
        if last in ROUTE_METHODS and "." in callee:
            path_arg = arguments[0] if arguments else None
            if path_arg and (str(path_arg).startswith("/") or last == "route"):
                handler = arguments[1] if len(arguments) > 1 else None
                self._emit(
                    RouteFact(
                        file=self.path,
                        line=self._line(node),
                        language=self.language,
                        scope=self._scope(scopes),
                        method=last.upper() if last != "route" else "GET",
                        path=path_arg,
                        handler=handler,
                        callee=callee,
                    )
                )

    def _return(self, node: Any, scopes: list[str]) -> None:
        value_node = node.named_children[0] if node.named_children else None
        self._emit(
            ReturnFact(
                file=self.path,
                line=self._line(node),
                language=self.language,
                scope=self._scope(scopes),
                value=self._arg(value_node) if value_node is not None else None,
            )
        )

    def _assignment(self, node: Any, scopes: list[str]) -> None:
        left = self._named(node, "left")
        right = self._named(node, "right")
        self._emit(
            AssignmentFact(
                file=self.path,
                line=self._line(node),
                language=self.language,
                scope=self._scope(scopes),
                target=self._dotted(left) or self._text(left) or "<target>",
                value=self._arg(right) if right is not None else None,
            )
        )

    def _attribute(self, node: Any, scopes: list[str]) -> None:
        obj = self._dotted(self._named(node, "object"))
        attr = self._ident(self._named(node, "property"))
        if not attr:
            return
        self._emit(
            AttributeAccessFact(
                file=self.path,
                line=self._line(node),
                language=self.language,
                scope=self._scope(scopes),
                object=obj,
                attribute=attr,
            )
        )
        if obj == "process.env":
            self._emit(
                EnvironmentVariableFact(
                    file=self.path,
                    line=self._line(node),
                    language=self.language,
                    scope=self._scope(scopes),
                    name=attr,
                    accessor="process.env",
                )
            )

    def _subscript(self, node: Any, scopes: list[str]) -> None:
        obj = self._dotted(self._named(node, "object"))
        index = self._arg(self._named(node, "index"))
        if obj == "process.env":
            self._emit(
                EnvironmentVariableFact(
                    file=self.path,
                    line=self._line(node),
                    language=self.language,
                    scope=self._scope(scopes),
                    name=index,
                    accessor="process.env",
                )
            )


def _http_method(callee: str) -> str | None:
    if callee in {"fetch"} or callee.endswith(".fetch"):
        return "GET"
    parts = callee.split(".")
    if len(parts) < 2:
        return None
    root, last = parts[0], parts[-1].lower()
    if root in HTTP_CLIENT_ROOTS | {"axios"} and last in HTTP_METHODS | {"request"}:
        return last.upper() if last != "request" else "REQUEST"
    return None
