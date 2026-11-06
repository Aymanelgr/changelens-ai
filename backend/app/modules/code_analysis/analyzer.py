"""Static analysis of Python source (pipeline step 4).

SECURITY: uploaded code is only ever parsed as text. Nothing here may import, eval or
exec it. ast.parse builds a syntax tree and runs no user code (tested by NFR-006).
"""

import ast
import builtins
import io
import math
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from app.core.enums import SymbolKind

_DYNAMIC_CALLS = {"getattr", "eval", "exec", "__import__", "globals", "locals", "compile"}
_BUILTINS = set(dir(builtins)) - _DYNAMIC_CALLS

# Orientation is normalized so that "10000 < amount" reads as "amount > 10000".
_COMPARE_OPS = {
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Eq: "==",
    ast.NotEq: "!=",
}
_FLIPPED = {">": "<", ">=": "<=", "<": ">", "<=": ">=", "==": "==", "!=": "!="}
_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")


@dataclass
class RawCall:
    name: str
    line: int
    dynamic: bool = False


@dataclass
class SymbolInfo:
    file_path: str
    qualified_name: str
    kind: SymbolKind
    start_line: int
    end_line: int
    signature: str | None
    docstring: str | None
    is_test: bool
    source_text: str
    number_literals: list[dict[str, Any]] = field(default_factory=list)
    text_items: list[dict[str, Any]] = field(default_factory=list)
    raw_calls: list[RawCall] = field(default_factory=list)


@dataclass
class ResolvedCall:
    caller: SymbolInfo
    callee: SymbolInfo | None
    callee_name: str
    line: int
    resolved: bool
    external: bool


@dataclass
class ModuleAnalysis:
    symbols: list[SymbolInfo]
    imports: dict[str, tuple[str, str | None]]  # local name -> (module, attribute)
    error: str | None = None


@dataclass
class RepositoryAnalysis:
    symbols: list[SymbolInfo]
    calls: list[ResolvedCall]
    parse_errors: list[str]


def _module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_test_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return (
        pure.name.startswith("test_")
        or pure.name.endswith("_test.py")
        or "tests" in pure.parts
        or pure.name == "conftest.py"
    )


def _call_name(node: ast.expr) -> str | None:
    """ "a.b.c" for a dotted call. None when the callee is computed: foo()() or d["k"]()."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _is_number(node: ast.AST) -> bool:
    # 1e999 parses to infinity, which JSON cannot store, so an uploaded file could crash the import.
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
        and math.isfinite(node.value)
    )


def split_source_lines(source: str) -> list[str]:
    """Split exactly as the Python parser counts lines. str.splitlines() also breaks on
    form feeds and other separators, which would shift every later citation."""
    return _LINE_BREAK_RE.split(source)


class _ModuleVisitor(ast.NodeVisitor):
    def __init__(self, path: str, source: str):
        self.path = path
        self.lines = split_source_lines(source)
        self.is_test_file = _is_test_path(path)
        self.module_symbol = SymbolInfo(
            file_path=path,
            qualified_name="<module>",
            kind=SymbolKind.MODULE,
            start_line=1,
            end_line=max(len(self.lines), 1),
            signature=None,
            docstring=None,
            is_test=self.is_test_file,
            source_text=source,
        )
        self.symbols = [self.module_symbol]
        self.stack = [self.module_symbol]
        self.imports: dict[str, tuple[str, str | None]] = {}
        self.package = _module_name(path).split(".")[:-1]
        if path.endswith("__init__.py"):
            self.package = _module_name(path).split(".")

    # Scopes -----------------------------------------------------------------
    def _enter_scope(self, node, kind: SymbolKind) -> None:
        parent = self.stack[-1]
        prefix = "" if parent.kind == SymbolKind.MODULE else f"{parent.qualified_name}."
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        signature = None
        if not isinstance(node, ast.ClassDef):
            signature = f"{node.name}({ast.unparse(node.args)})"
        symbol = SymbolInfo(
            file_path=self.path,
            qualified_name=f"{prefix}{node.name}",
            kind=kind,
            start_line=start,
            end_line=node.end_lineno or node.lineno,
            signature=signature,
            docstring=ast.get_docstring(node),
            is_test=self.is_test_file,
            source_text="\n".join(self.lines[start - 1 : node.end_lineno]),
        )
        self.symbols.append(symbol)
        self.stack.append(symbol)
        for decorator in node.decorator_list:
            self.visit(decorator)
        if not isinstance(node, ast.ClassDef):
            self.visit(node.args)
            if node.returns:
                self.visit(node.returns)
        else:
            for base in node.bases + node.keywords:
                self.visit(base)
        for statement in node.body:
            self.visit(statement)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        in_class = self.stack[-1].kind == SymbolKind.CLASS
        self._enter_scope(node, SymbolKind.METHOD if in_class else SymbolKind.FUNCTION)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter_scope(node, SymbolKind.CLASS)

    # Imports ----------------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            target = alias.name if alias.asname else alias.name.split(".")[0]
            self.imports[local] = (target, None)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = node.module or ""
        if node.level:
            package = self.package[: len(self.package) - (node.level - 1)]
            base = ".".join(package + ([node.module] if node.module else []))
        for alias in node.names:
            self.imports[alias.asname or alias.name] = (base, alias.name)

    # Facts ------------------------------------------------------------------
    def visit_Compare(self, node: ast.Compare) -> None:
        # Chained comparisons (a < b < c) are checked pair by pair.
        left = node.left
        for operator, right in zip(node.ops, node.comparators, strict=True):
            symbol = _COMPARE_OPS.get(type(operator))
            if symbol:
                if _is_number(right) and not _is_number(left):
                    self._record_number(right, symbol, ast.unparse(left))
                elif _is_number(left) and not _is_number(right):
                    self._record_number(left, _FLIPPED[symbol], ast.unparse(right))
            left = right
        # Numbers already recorded with their operator must not be counted twice.
        for child in [node.left, *node.comparators]:
            if not _is_number(child):
                self.visit(child)

    def _record_number(self, node: ast.Constant, operator: str | None, subject: str | None) -> None:
        self.stack[-1].number_literals.append(
            {
                "value": node.value,
                "line": node.lineno,
                "compare_op": operator,
                "subject": subject,
            }
        )

    def visit_Constant(self, node: ast.Constant) -> None:
        if _is_number(node):
            self._record_number(node, None, None)
        elif isinstance(node.value, str):
            self._record_string_lines(node)

    def _record_string_lines(self, node: ast.Constant) -> None:
        # Only lines holding a digit can mention a value or an identifier like REQ-012.
        for line_number in range(node.lineno, (node.end_lineno or node.lineno) + 1):
            line = self.lines[line_number - 1]
            if any(c.isdigit() for c in line):
                self.stack[-1].text_items.append(
                    {"line": line_number, "text": line.strip(), "kind": "STRING"}
                )

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        if name is None:
            self.stack[-1].raw_calls.append(RawCall("<dynamique>", node.lineno, dynamic=True))
        elif name in _DYNAMIC_CALLS:
            self.stack[-1].raw_calls.append(RawCall(name, node.lineno, dynamic=True))
        elif name not in _BUILTINS or name in self.imports:
            self.stack[-1].raw_calls.append(RawCall(name, node.lineno))
        self.generic_visit(node)


def _attach_comments(source: str, visitor: _ModuleVisitor) -> None:
    """Comments do not exist in the AST, so they come from the tokenizer."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):
        return
    comments = [t for t in tokens if t.type == tokenize.COMMENT]
    # Bottom-up, so a block of several comment lines above a def is absorbed line by line.
    for token in reversed(comments):
        line = token.start[0]
        owner = next(
            (
                s
                for s in visitor.symbols
                if s.start_line == line + 1 and s.kind != SymbolKind.MODULE
            ),
            None,
        )
        if owner is not None:
            # A comment block right above a def belongs to it, and joins its source text
            # so that a citation of the comment can be verified against that source.
            owner.start_line = line
            owner.source_text = "\n".join(visitor.lines[line - 1 : owner.end_line])
        else:
            containing = [s for s in visitor.symbols if s.start_line <= line <= s.end_line]
            owner = min(
                containing,
                key=lambda s: s.end_line - s.start_line,
                default=visitor.module_symbol,
            )
        if any(c.isdigit() for c in token.string):
            owner.text_items.append({"line": line, "text": token.string, "kind": "COMMENT"})
    for symbol in visitor.symbols:
        symbol.text_items.sort(key=lambda item: item["line"])


def analyze_module(path: str, source: str) -> ModuleAnalysis:
    try:
        tree = ast.parse(source, filename=path)
        visitor = _ModuleVisitor(path, source)
        visitor.module_symbol.docstring = ast.get_docstring(tree)
        visitor.visit(tree)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        return ModuleAnalysis([], {}, error=f"{path} : analyse impossible ({type(exc).__name__}).")
    _attach_comments(source, visitor)
    for symbol in visitor.symbols:
        if symbol.kind != SymbolKind.MODULE and symbol.qualified_name.split(".")[-1].startswith(
            "test_"
        ):
            symbol.is_test = True
    return ModuleAnalysis(visitor.symbols, visitor.imports)


def analyze_repository(files: dict[str, str]) -> RepositoryAnalysis:
    modules: dict[str, ModuleAnalysis] = {}
    errors: list[str] = []
    symbol_index: dict[tuple[str, str], SymbolInfo] = {}

    for path, source in files.items():
        module_name = _module_name(path)
        analysis = analyze_module(path, source)
        if analysis.error:
            errors.append(analysis.error)
            continue
        modules[module_name] = analysis
        for symbol in analysis.symbols:
            symbol_index[(module_name, symbol.qualified_name)] = symbol

    def find_module(name: str) -> str | None:
        # A src/ layout imports "pkg.mod" while the file lives at src/pkg/mod.py.
        return next((m for m in (name, f"src.{name}") if m in modules), None)

    def resolve(module: str, analysis: ModuleAnalysis, caller: SymbolInfo, name: str):
        """Returns (symbol or None, is_external). Anything uncertain stays unresolved."""
        head, _, rest = name.partition(".")

        if head == "self" and rest and "." not in rest:
            class_name = caller.qualified_name.rpartition(".")[0]
            return symbol_index.get((module, f"{class_name}.{rest}")), False

        if head in analysis.imports:
            target_module, attribute = analysis.imports[head]
            parts = [attribute] if attribute else []
            parts += rest.split(".") if rest else []
            # "from pkg import mod" followed by mod.func() names a module, not a symbol.
            for split in range(len(parts) + 1):
                candidate = ".".join([target_module, *parts[:split]])
                found = find_module(candidate)
                symbol_name = ".".join(parts[split:])
                if found and symbol_name:
                    symbol = symbol_index.get((found, symbol_name))
                    if symbol:
                        return symbol, False
            top_level = target_module.split(".")[0]
            known_top_levels = {m.split(".")[0] for m in modules} | {
                m.split(".")[1] for m in modules if m.startswith("src.") and m.count(".") >= 1
            }
            return None, top_level not in known_top_levels

        symbol = symbol_index.get((module, name))
        return symbol, False

    calls: list[ResolvedCall] = []
    all_symbols: list[SymbolInfo] = []
    for module_name, analysis in modules.items():
        all_symbols.extend(analysis.symbols)
        for caller in analysis.symbols:
            for raw in caller.raw_calls:
                callee, external = (None, False)
                if not raw.dynamic:
                    callee, external = resolve(module_name, analysis, caller, raw.name)
                calls.append(
                    ResolvedCall(
                        caller=caller,
                        callee=callee,
                        callee_name=raw.name,
                        line=raw.line,
                        resolved=callee is not None,
                        external=external,
                    )
                )
    return RepositoryAnalysis(all_symbols, calls, errors)
