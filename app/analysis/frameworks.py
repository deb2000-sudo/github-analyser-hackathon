"""Deterministic framework evidence for the fullstack metric. No AST, no Gemini."""

from __future__ import annotations

import json
import re
from typing import Any

from app.analysis.inventory import FileInventory
from app.analysis.manifests import PHASE2_MANIFESTS, parse_npm_deps, parse_python_packages

STRONG = 0.95
CONFIG = 0.85
CODE = 0.88
WEAK_README = 0.25
CLASSIFY_MIN = 0.70

FRONTEND_DISPLAY = {
    "react": "React",
    "nextjs": "Next.js",
    "vue": "Vue",
    "angular": "Angular",
    "svelte": "Svelte",
}
BACKEND_DISPLAY = {
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "express": "Express",
    "nestjs": "NestJS",
    "spring_boot": "Spring Boot",
    "nextjs_api": "Next.js API",
}

NEXT_API_PATH = re.compile(
    r"(^|/)(src/)?(app|pages)/api/.+\.(t|j)sx?$"
)
NEXT_ROUTE_HANDLER = re.compile(
    r"export\s+(async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b"
)
REACT_IMPORT = re.compile(r"""from\s+['"]react['"]|require\s*\(\s*['"]react['"]\s*\)""")
VUE_IMPORT = re.compile(r"""from\s+['"]vue['"]|require\s*\(\s*['"]vue['"]\s*\)""")
ANGULAR_IMPORT = re.compile(r"""from\s+['"]@angular/core['"]""")
SVELTE_IMPORT = re.compile(r"""from\s+['"]svelte['"]""")
FASTAPI_CODE = re.compile(r"\bfrom\s+fastapi\b|\bimport\s+fastapi\b|FastAPI\s*\(")
FLASK_CODE = re.compile(r"\bfrom\s+flask\b|\bimport\s+flask\b|Flask\s*\(")
DJANGO_CODE = re.compile(r"\bfrom\s+django\b|\bimport\s+django\b")
EXPRESS_CODE = re.compile(
    r"""(?:from\s+['"]express['"]|require\s*\(\s*['"]express['"]\s*\))"""
)
NEST_CODE = re.compile(r"""from\s+['"]@nestjs/common['"]|from\s+['"]@nestjs/core['"]""")


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _basename(path: str) -> str:
    return _norm(path).split("/")[-1]


def _signal(
    rule_id: str,
    category: str,
    value: str,
    confidence: float,
    file: str,
    description: str,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "category": category,
        "value": value,
        "confidence": confidence,
        "evidence": [{"file": file, "description": description}],
    }


def _collect_manifest_map(contents: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for path, content in contents.items():
        if _basename(path) in PHASE2_MANIFESTS:
            out[path] = content
    return out


def _parse_package_json(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _python_pkgs(manifests: dict[str, str]) -> set[str]:
    return set(parse_python_packages(manifests))


def collect_manifest_signals(inventory: FileInventory) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in inventory.files:
        name = entry.name.lower()
        if name not in PHASE2_MANIFESTS or name in seen:
            continue
        seen.add(name)
        signals.append(
            _signal(
                f"MANIFEST.{name.upper().replace('.', '_')}",
                "manifest",
                name,
                STRONG,
                entry.path,
                f"{name} detected",
            )
        )
    return signals


def collect_framework_signals(
    inventory: FileInventory,
    contents: dict[str, str],
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    manifests = _collect_manifest_map(contents)
    npm = parse_npm_deps(manifests)
    py_pkgs = _python_pkgs(manifests)
    paths = inventory.paths

    def add_dep(pkg: str, rule: str, value: str, file: str, label: str) -> None:
        if pkg in npm:
            signals.append(_signal(rule, "framework", value, STRONG, file, label))

    pkg_files = [p for p in manifests if _basename(p).lower() == "package.json"]
    req_files = [
        p
        for p in manifests
        if _basename(p).lower() in {"requirements.txt", "pyproject.toml"}
    ]
    pkg_file = pkg_files[0] if pkg_files else "package.json"
    py_file = req_files[0] if req_files else "requirements.txt"

    add_dep("react", "FRAMEWORK.REACT.DEPENDENCY", "react", pkg_file, "React dependency detected")
    add_dep("react-dom", "FRAMEWORK.REACT.DEPENDENCY", "react", pkg_file, "react-dom dependency detected")
    add_dep("next", "FRAMEWORK.NEXT.DEPENDENCY", "nextjs", pkg_file, "Next.js dependency detected")
    add_dep("vue", "FRAMEWORK.VUE.DEPENDENCY", "vue", pkg_file, "Vue dependency detected")
    add_dep("@angular/core", "FRAMEWORK.ANGULAR.DEPENDENCY", "angular", pkg_file, "Angular dependency detected")
    add_dep("svelte", "FRAMEWORK.SVELTE.DEPENDENCY", "svelte", pkg_file, "Svelte dependency detected")
    add_dep("express", "FRAMEWORK.EXPRESS.DEPENDENCY", "express", pkg_file, "Express dependency detected")
    add_dep("@nestjs/core", "FRAMEWORK.NESTJS.DEPENDENCY", "nestjs", pkg_file, "NestJS dependency detected")

    if "fastapi" in py_pkgs:
        signals.append(
            _signal(
                "FRAMEWORK.FASTAPI.DEPENDENCY",
                "framework",
                "fastapi",
                STRONG,
                py_file,
                "FastAPI dependency detected",
            )
        )
    if "flask" in py_pkgs:
        signals.append(
            _signal(
                "FRAMEWORK.FLASK.DEPENDENCY",
                "framework",
                "flask",
                STRONG,
                py_file,
                "Flask dependency detected",
            )
        )
    if "django" in py_pkgs:
        signals.append(
            _signal(
                "FRAMEWORK.DJANGO.DEPENDENCY",
                "framework",
                "django",
                STRONG,
                py_file,
                "Django dependency detected",
            )
        )

    for path in paths:
        base = _basename(path).lower()
        if base.startswith("next.config."):
            signals.append(
                _signal(
                    "FRAMEWORK.NEXT.CONFIG",
                    "framework",
                    "nextjs",
                    CONFIG,
                    path,
                    "Next.js config file",
                )
            )
        if base == "angular.json":
            signals.append(
                _signal(
                    "FRAMEWORK.ANGULAR.CONFIG",
                    "framework",
                    "angular",
                    CONFIG,
                    path,
                    "Angular workspace config",
                )
            )
        if base.startswith("svelte.config."):
            signals.append(
                _signal(
                    "FRAMEWORK.SVELTE.CONFIG",
                    "framework",
                    "svelte",
                    CONFIG,
                    path,
                    "Svelte config file",
                )
            )

    for path, content in manifests.items():
        base = _basename(path).lower()
        lower = content.lower()
        if base in {"pom.xml", "build.gradle", "build.gradle.kts"}:
            if "spring-boot" in lower or "org.springframework.boot" in lower:
                signals.append(
                    _signal(
                        "FRAMEWORK.SPRING.DEPENDENCY",
                        "framework",
                        "spring_boot",
                        STRONG,
                        path,
                        "Spring Boot detected in build manifest",
                    )
                )

    _README_WEAK = (
        (r"\breact\b", "FRAMEWORK.REACT.README", "react", "README mentions React (weak contextual evidence)"),
        (r"\bnext\.js\b|\bnextjs\b", "FRAMEWORK.NEXT.README", "nextjs", "README mentions Next.js (weak contextual evidence)"),
        (r"\bvue(?:\.js)?\b", "FRAMEWORK.VUE.README", "vue", "README mentions Vue (weak contextual evidence)"),
        (r"\bangular\b", "FRAMEWORK.ANGULAR.README", "angular", "README mentions Angular (weak contextual evidence)"),
        (r"\bsvelte\b", "FRAMEWORK.SVELTE.README", "svelte", "README mentions Svelte (weak contextual evidence)"),
        (r"\bfastapi\b", "FRAMEWORK.FASTAPI.README", "fastapi", "README mentions FastAPI (weak contextual evidence)"),
        (r"\bflask\b", "FRAMEWORK.FLASK.README", "flask", "README mentions Flask (weak contextual evidence)"),
        (r"\bdjango\b", "FRAMEWORK.DJANGO.README", "django", "README mentions Django (weak contextual evidence)"),
        (r"\bexpress(?:\.js)?\b", "FRAMEWORK.EXPRESS.README", "express", "README mentions Express (weak contextual evidence)"),
        (r"\bnestjs\b|\bnest\.js\b", "FRAMEWORK.NESTJS.README", "nestjs", "README mentions NestJS (weak contextual evidence)"),
    )

    for path, content in contents.items():
        if not content:
            continue
        base = _basename(path).lower()
        if base.startswith("readme"):
            blob = content.lower()
            for pattern, rule_id, value, description in _README_WEAK:
                if re.search(pattern, blob):
                    signals.append(
                        _signal(rule_id, "framework", value, WEAK_README, path, description)
                    )
            continue
        if NEXT_API_PATH.search(_norm(path)) and NEXT_ROUTE_HANDLER.search(content):
            signals.append(
                _signal(
                    "FRAMEWORK.NEXT.API_ROUTE",
                    "framework",
                    "nextjs_api",
                    CODE,
                    path,
                    "Next.js App Router / Pages API handler",
                )
            )
        if REACT_IMPORT.search(content):
            signals.append(
                _signal("FRAMEWORK.REACT.IMPORT", "framework", "react", CODE, path, "React import in source")
            )
        if VUE_IMPORT.search(content):
            signals.append(
                _signal("FRAMEWORK.VUE.IMPORT", "framework", "vue", CODE, path, "Vue import in source")
            )
        if ANGULAR_IMPORT.search(content):
            signals.append(
                _signal(
                    "FRAMEWORK.ANGULAR.IMPORT",
                    "framework",
                    "angular",
                    CODE,
                    path,
                    "Angular import in source",
                )
            )
        if SVELTE_IMPORT.search(content):
            signals.append(
                _signal("FRAMEWORK.SVELTE.IMPORT", "framework", "svelte", CODE, path, "Svelte import in source")
            )
        if FASTAPI_CODE.search(content):
            signals.append(
                _signal("FRAMEWORK.FASTAPI.IMPORT", "framework", "fastapi", CODE, path, "FastAPI usage in source")
            )
        if FLASK_CODE.search(content):
            signals.append(
                _signal("FRAMEWORK.FLASK.IMPORT", "framework", "flask", CODE, path, "Flask usage in source")
            )
        if DJANGO_CODE.search(content):
            signals.append(
                _signal("FRAMEWORK.DJANGO.IMPORT", "framework", "django", CODE, path, "Django usage in source")
            )
        if EXPRESS_CODE.search(content):
            signals.append(
                _signal("FRAMEWORK.EXPRESS.IMPORT", "framework", "express", CODE, path, "Express usage in source")
            )
        if NEST_CODE.search(content):
            signals.append(
                _signal("FRAMEWORK.NESTJS.IMPORT", "framework", "nestjs", CODE, path, "NestJS usage in source")
            )

    for path, content in manifests.items():
        if _basename(path).lower() != "package.json":
            continue
        data = _parse_package_json(content)
        if data.get("bin"):
            signals.append(
                _signal("APP.CLI.NPM_BIN", "application", "cli", STRONG, path, "package.json bin field (CLI)")
            )

    if py_pkgs & {"click", "typer"} and not (py_pkgs & {"fastapi", "flask", "django"}):
        signals.append(
            _signal(
                "APP.CLI.PYTHON",
                "application",
                "cli",
                STRONG,
                py_file,
                "Python CLI toolkit without a web framework",
            )
        )

    return signals


def _best_framework(
    signals: list[dict[str, Any]],
    allowed: set[str],
    *,
    preference: list[str] | None = None,
) -> tuple[str | None, float, list[dict[str, Any]]]:
    by_value: dict[str, list[dict[str, Any]]] = {}
    for sig in signals:
        if sig.get("category") != "framework":
            continue
        value = str(sig.get("value") or "")
        if value not in allowed:
            continue
        by_value.setdefault(value, []).append(sig)

    ranked: list[tuple[str, float, list[dict[str, Any]]]] = []
    for value, hits in by_value.items():
        conf = max(float(h.get("confidence") or 0) for h in hits)
        if conf >= CLASSIFY_MIN:
            ranked.append((value, conf, hits))
    if not ranked:
        weak = [
            (v, max(float(h.get("confidence") or 0) for h in hits), hits)
            for v, hits in by_value.items()
        ]
        if weak:
            weak.sort(key=lambda row: row[1], reverse=True)
            return None, weak[0][1], weak[0][2]
        return None, 0.0, []

    if preference:
        for pref in preference:
            for value, conf, hits in ranked:
                if value == pref:
                    return value, conf, hits
    ranked.sort(key=lambda row: row[1], reverse=True)
    return ranked[0]


def classify_application(
    inventory: FileInventory,
    contents: dict[str, str],
) -> dict[str, Any]:
    manifest_signals = collect_manifest_signals(inventory)
    framework_signals = collect_framework_signals(inventory, contents)
    signals = manifest_signals + framework_signals

    fe_id, fe_conf, _ = _best_framework(
        framework_signals,
        {"react", "nextjs", "vue", "angular", "svelte"},
        preference=["nextjs", "react", "vue", "angular", "svelte"],
    )
    be_id, be_conf, _ = _best_framework(
        framework_signals,
        {"fastapi", "flask", "django", "express", "nestjs", "spring_boot", "nextjs_api"},
        preference=["nextjs_api", "fastapi", "django", "flask", "nestjs", "express", "spring_boot"],
    )

    fe_detected = fe_id is not None
    be_detected = be_id is not None
    cli_hits = [s for s in signals if s.get("rule_id", "").startswith("APP.CLI.") and s["confidence"] >= CLASSIFY_MIN]
    has_pkg = inventory.has_named("package.json") or inventory.has_named("pyproject.toml")
    has_lib_manifest = inventory.has_named("setup.py") or inventory.has_named("Cargo.toml") or has_pkg

    if fe_detected and be_detected:
        application_type = "full_stack"
        confidence = round(min(0.99, 0.90 + 0.05 * (fe_conf + be_conf) / 2), 2)
    elif fe_detected:
        application_type = "frontend"
        confidence = round(fe_conf, 2)
    elif be_detected:
        application_type = "backend"
        confidence = round(be_conf, 2)
    elif cli_hits and not fe_detected and not be_detected:
        application_type = "cli"
        confidence = 0.85
    elif has_lib_manifest and not fe_detected and not be_detected:
        application_type = "library"
        confidence = 0.7
    else:
        application_type = "unknown"
        confidence = 0.3

    return {
        "application_type": application_type,
        "confidence": confidence,
        "frontend": {
            "detected": fe_detected,
            "framework": fe_id,
        },
        "backend": {
            "detected": be_detected,
            "framework": be_id,
        },
        "signals": signals,
        "detected_manifests": sorted(
            {s["value"] for s in manifest_signals if s.get("category") == "manifest"}
        ),
    }
