import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def imported_modules(package: str) -> set[str]:
    """找出某个包下所有 .py 文件 import 了哪些模块。"""
    modules: set[str] = set()
    for path in (APP_DIR / package).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
    return modules


def imports_any(modules: set[str], forbidden: str) -> bool:
    return any(module == forbidden or module.startswith(forbidden + ".") for module in modules)


def test_business_layer_does_not_depend_on_web_framework_or_api() -> None:
    modules = imported_modules("business")

    for forbidden in ("fastapi", "starlette", "app.api"):
        assert not imports_any(modules, forbidden), f"app/business 不应该 import {forbidden}"


def test_agent_does_not_call_business_layer_directly() -> None:
    assert not imports_any(imported_modules("agent"), "app.business")
