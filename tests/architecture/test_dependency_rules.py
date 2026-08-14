"""Executable Clean Dependency Rule checks."""

import ast
from collections.abc import Iterable
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[2] / "src"
PANGI_ROOT = SOURCE_ROOT / "pangi"

FORBIDDEN_FRAMEWORK_ROOTS = {
    "aiosqlite",
    "anthropic",
    "fastapi",
    "httpx",
    "mcp",
    "openai",
    "pydantic",
    "slack_bolt",
    "slack_sdk",
    "sqlite3",
    "starlette",
    "typer",
}


def _module_name(path: Path) -> tuple[str, bool]:
    relative = path.relative_to(SOURCE_ROOT).with_suffix("")
    parts = list(relative.parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def _resolve_from_import(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""

    module_name, is_package = _module_name(path)
    package_parts = module_name.split(".") if is_package else module_name.split(".")[:-1]
    parents_to_remove = node.level - 1
    if parents_to_remove > len(package_parts):
        return ""
    base = package_parts[: len(package_parts) - parents_to_remove]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(_resolve_from_import(path, node))
    return imported


def _python_files(layer: str) -> Iterable[Path]:
    return (path for path in (PANGI_ROOT / layer).rglob("*.py") if path.is_file())


def _assert_no_framework_imports(paths: Iterable[Path]) -> None:
    violations: list[str] = []
    for path in paths:
        for imported in _imports(path):
            if imported.partition(".")[0] in FORBIDDEN_FRAMEWORK_ROOTS:
                violations.append(f"{path.relative_to(SOURCE_ROOT)} -> {imported}")
    assert not violations, "forbidden framework imports:\n" + "\n".join(sorted(violations))


def _assert_pangi_imports_within(paths: Iterable[Path], allowed: tuple[str, ...]) -> None:
    violations: list[str] = []
    for path in paths:
        for imported in _imports(path):
            if imported.startswith("pangi.") and not imported.startswith(allowed):
                violations.append(f"{path.relative_to(SOURCE_ROOT)} -> {imported}")
    assert not violations, "outward Pangi imports:\n" + "\n".join(sorted(violations))


def test_domain_is_framework_free_and_imports_only_domain() -> None:
    files = tuple(_python_files("domain"))
    _assert_no_framework_imports(files)
    _assert_pangi_imports_within(files, ("pangi.domain",))


def test_application_is_framework_free_and_imports_only_inner_layers() -> None:
    files = tuple(_python_files("application"))
    _assert_no_framework_imports(files)
    _assert_pangi_imports_within(files, ("pangi.application", "pangi.domain"))


def test_inbound_and_outbound_adapters_do_not_import_each_other() -> None:
    inbound_imports = {
        imported for path in _python_files("adapters/inbound") for imported in _imports(path)
    }
    outbound_imports = {
        imported for path in _python_files("adapters/outbound") for imported in _imports(path)
    }
    assert not any(name.startswith("pangi.adapters.outbound") for name in inbound_imports)
    assert not any(name.startswith("pangi.adapters.inbound") for name in outbound_imports)

