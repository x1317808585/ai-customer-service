import ast
import importlib
import inspect
import pkgutil
from pathlib import Path

import app.business
from app.business.errors import BusinessError

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


def test_business_layer_does_not_depend_on_web_framework_api_or_mcp() -> None:
    modules = imported_modules("business")

    for forbidden in ("fastapi", "starlette", "mcp", "app.api", "app.mcp_server", "app.actions", "app.action_kinds"):
        assert not imports_any(modules, forbidden), f"app/business 不应该 import {forbidden}"


def test_agent_reaches_business_data_only_through_mcp() -> None:
    modules = imported_modules("agent")

    for forbidden in ("app.business", "app.mcp_server", "app.actions", "app.action_kinds", "app.api"):
        assert not imports_any(modules, forbidden), f"app/agent 不应该 import {forbidden}"


def test_mcp_server_cannot_reach_the_confirmation_entry() -> None:
    """助手发起的写操作只能经确认接口执行（app.actions 和它用到的 app.action_kinds），模型能用到的 MCP Server 不能碰。"""
    modules = imported_modules("mcp_server")

    for forbidden in ("app.actions", "app.action_kinds", "app.api"):
        assert not imports_any(modules, forbidden), f"app/mcp_server 不应该 import {forbidden}"


def test_staff_transcript_is_only_used_by_admin_api() -> None:
    """transcript_for_staff 不检查会话归属，只能在已鉴权的管理接口里使用。"""
    users = {
        path.relative_to(APP_DIR).as_posix()
        for path in APP_DIR.rglob("*.py")
        if "transcript_for_staff" in path.read_text(encoding="utf-8")
    }

    assert users == {"conversations.py", "api/admin/tickets.py"}


def test_every_business_exception_is_a_business_error() -> None:
    """业务层抛出的异常都要继承 BusinessError，MCP Server 才知道哪些消息可以告诉顾客。"""
    for module_info in pkgutil.iter_modules(app.business.__path__):
        module = importlib.import_module(f"app.business.{module_info.name}")
        for name, value in inspect.getmembers(module, inspect.isclass):
            if value.__module__ == module.__name__ and issubclass(value, Exception):
                assert issubclass(value, BusinessError), f"{module.__name__}.{name} 应该继承 BusinessError"
