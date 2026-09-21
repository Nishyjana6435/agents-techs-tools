"""Restricted Python execution used by the analysis tool and the RLM planner.

Two layers:
* **Static AST policy** - rejects imports (except an allow-list), dunder attribute access, and
  dangerous names (``exec``, ``eval``, ``open``, ``__import__``...). Deterministic and explainable:
  the error names the offending construct.
* **Process isolation** - the analysis tool runs the code in a separate ``python -I`` subprocess
  with a hard timeout (kill on expiry) and captured stdout. The planner only needs to build a
  Python literal and runs in-process under the same AST policy with a minimal builtins table.

This is a POC sandbox, not a security boundary against a determined attacker; production would use
a container/microVM (e.g. Vercel Sandbox, Firecracker) - documented in docs/SECURITY.md.
"""
from __future__ import annotations

import ast
import asyncio
import json
import sys
import textwrap
from typing import Any

ALLOWED_IMPORTS = frozenset({"math", "statistics", "json", "re", "collections", "datetime", "itertools", "functools"})
FORBIDDEN_NAMES = frozenset({"exec", "eval", "open", "compile", "__import__", "globals", "locals", "input", "breakpoint", "exit", "quit", "help", "vars", "getattr", "setattr", "delattr"})


class UnsafeCodeError(ValueError):
    pass


def check_code(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise UnsafeCodeError(f"syntax error: {exc.msg} (line {exc.lineno})") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import) else [(node.module or "").split(".")[0]]
            for n in names:
                if n not in ALLOWED_IMPORTS:
                    raise UnsafeCodeError(f"import of '{n}' is not allowed (allowed: {sorted(ALLOWED_IMPORTS)})")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise UnsafeCodeError(f"dunder attribute access '{node.attr}' is not allowed")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise UnsafeCodeError(f"use of '{node.id}' is not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            raise UnsafeCodeError("global/nonlocal statements are not allowed")


SAFE_BUILTINS: dict[str, Any] = {
    n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n)
    for n in (
        "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int", "len", "list", "map", "max", "min",
        "range", "round", "set", "sorted", "str", "sum", "tuple", "zip", "isinstance", "print", "reversed", "frozenset",
    )
}


def run_plan_code(code: str, namespace: dict[str, Any]) -> dict[str, Any]:
    """Execute planner code in-process with a minimal builtins table; returns the resulting namespace."""
    check_code(code)
    env: dict[str, Any] = {"__builtins__": SAFE_BUILTINS, **namespace}
    exec(compile(code, "<rlm-plan>", "exec"), env)  # noqa: S102 - guarded by check_code + restricted builtins
    return {k: v for k, v in env.items() if not k.startswith("__") and k not in namespace}


_RUNNER = textwrap.dedent(
    """
    import json, sys
    payload = json.loads(sys.stdin.read())
    data = payload["data"]
    env = {"data": data, "result": None}
    exec(compile(payload["code"], "<analysis>", "exec"), env)
    out = env.get("result")
    try:
        json.dumps(out)
    except TypeError:
        out = repr(out)
    print("\\n__RESULT__" + json.dumps(out))
    """
)


async def run_analysis_subprocess(code: str, data: Any, timeout: float) -> dict[str, Any]:
    """Run analysis code in an isolated interpreter. Returns ``{"stdout", "result"}`` or raises."""
    check_code(code)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", _RUNNER,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    payload = json.dumps({"code": code, "data": data}).encode()
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(payload), timeout=timeout)
    except TimeoutError:
        proc.kill()
        raise TimeoutError(f"analysis code exceeded {timeout:.0f}s and was killed") from None
    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip().splitlines()
        raise RuntimeError(err[-1] if err else f"exit code {proc.returncode}")
    text = stdout.decode(errors="replace")
    printed, _, result_json = text.rpartition("__RESULT__")
    return {"stdout": printed.strip()[:4000], "result": json.loads(result_json) if result_json.strip() else None}
