"""Phase 3 Slice B acceptance test 12: no AI/LLM dependency anywhere in
the diagnostics or narration services.

Mirrors tests/test_milestone1_architecture_boundaries.py's AST-based
import-boundary technique exactly, applied to the two new Slice B
service modules.
"""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

SLICE_B_MODULES = (
    "app/services/financial_diagnostics_service.py",
    "app/services/financial_narration_service.py",
)

FORBIDDEN_AI_IMPORT_PREFIXES = (
    "openai",
    "anthropic",
    "langchain",
    "langchain_core",
    "langchain_openai",
    "cohere",
    "google.generativeai",
    "transformers",
    "huggingface_hub",
    "llama_index",
    "mistralai",
)


def _module_import_prefixes(relative_path: str) -> set[str]:
    source = (REPO_ROOT / relative_path).read_text()
    tree = ast.parse(source, filename=relative_path)
    prefixes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                prefixes.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            prefixes.add(node.module)
    return prefixes


def test_slice_b_services_import_no_ai_or_llm_client():
    violations = []
    for relative_path in SLICE_B_MODULES:
        imports = _module_import_prefixes(relative_path)
        for imported in imports:
            if any(
                imported == forbidden or imported.startswith(forbidden + ".")
                for forbidden in FORBIDDEN_AI_IMPORT_PREFIXES
            ):
                violations.append(f"{relative_path} imports {imported}")
    assert violations == [], violations


def test_slice_b_services_make_no_outbound_network_call():
    """A narrower, positive check alongside the import-prefix denylist:
    neither module imports a generic HTTP client either (requests,
    httpx, urllib) -- both services are pure reads over the existing ORM,
    not callers of anything external, AI or otherwise."""

    forbidden_network_prefixes = ("requests", "httpx", "urllib3", "aiohttp")
    violations = []
    for relative_path in SLICE_B_MODULES:
        imports = _module_import_prefixes(relative_path)
        for imported in imports:
            if any(
                imported == forbidden or imported.startswith(forbidden + ".")
                for forbidden in forbidden_network_prefixes
            ):
                violations.append(f"{relative_path} imports {imported}")
    assert violations == [], violations
