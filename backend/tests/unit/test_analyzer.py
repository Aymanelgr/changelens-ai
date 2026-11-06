from app.core.enums import SymbolKind
from app.modules.code_analysis.analyzer import analyze_module, analyze_repository


def _symbol(analysis, name):
    return next(s for s in analysis.symbols if s.qualified_name == name)


def test_extracts_symbols_lines_and_docstring():
    source = (
        "class Service:\n"
        '    """Doc classe."""\n'
        "    def run(self, x=1):\n"
        '        """Doc méthode."""\n'
        "        return x\n"
        "\n"
        "def outer():\n"
        "    def inner():\n"
        "        return 1\n"
    )
    analysis = analyze_module("pkg/mod.py", source)
    method = _symbol(analysis, "Service.run")
    assert method.kind == SymbolKind.METHOD
    assert (method.start_line, method.end_line) == (3, 5)
    assert method.docstring == "Doc méthode."
    assert method.signature == "run(self, x=1)"
    assert _symbol(analysis, "outer.inner").kind == SymbolKind.FUNCTION


def test_comparison_records_normalized_operator():
    source = (
        "def f(a):\n    if a > 10000:\n        pass\n"
        "    if 10000 < a:\n        pass\n    return 10000 >= a\n"
    )
    literals = _symbol(analyze_module("m.py", source), "f").number_literals
    assert [(n["value"], n["compare_op"]) for n in literals] == [
        (10000, ">"),
        (10000, ">"),  # 10000 < a means a > 10000
        (10000, "<="),
    ]


def test_literals_are_counted_once_and_underscores_are_understood():
    literals = _symbol(
        analyze_module("m.py", "def f(a):\n    return a > 10_000\n"), "f"
    ).number_literals
    assert len(literals) == 1
    assert literals[0]["value"] == 10000


def test_booleans_and_infinity_are_not_numbers():
    source = "def f():\n    return True, 1e999, 3\n"
    assert [n["value"] for n in _symbol(analyze_module("m.py", source), "f").number_literals] == [3]


def test_comment_above_a_function_belongs_to_it():
    source = "# REQ-012 : règle\ndef f():\n    return 1  # seuil 10 000\n"
    items = _symbol(analyze_module("m.py", source), "f").text_items
    assert [i["line"] for i in items] == [1, 3]
    assert all(i["kind"] == "COMMENT" for i in items)


def test_comment_block_above_a_function_joins_its_source():
    source = "x = 1\n\n# Règle métier\n# REQ-012\ndef f():\n    return 1\n"
    symbol = _symbol(analyze_module("m.py", source), "f")
    assert symbol.start_line == 3
    assert symbol.source_text.startswith("# Règle métier\n# REQ-012\ndef f():")
    assert symbol.text_items[0]["text"] in symbol.source_text


def test_test_files_and_test_functions_are_marked():
    assert _symbol(analyze_module("tests/test_a.py", "def helper():\n    pass\n"), "helper").is_test
    assert _symbol(analyze_module("a.py", "def test_x():\n    pass\n"), "test_x").is_test
    assert not _symbol(analyze_module("a.py", "def run():\n    pass\n"), "run").is_test


def test_syntax_error_is_reported_not_raised():
    assert analyze_module("bad.py", "def (:\n").error
    assert analyze_module("nul.py", "a = 1\x00\n").error


def test_form_feed_does_not_shift_lines():
    source = "a = 1\n\x0c\ndef f():\n    return 42\n"
    symbol = _symbol(analyze_module("m.py", source), "f")
    assert symbol.start_line == 3
    assert symbol.source_text.startswith("def f()")


def _edges(files):
    analysis = analyze_repository(files)
    return {(c.caller.qualified_name, c.callee_name): c for c in analysis.calls}


def test_call_resolution_across_modules():
    edges = _edges(
        {
            "auth/strong.py": "def check():\n    pass\n",
            "app/a.py": (
                "from auth.strong import check\n"
                "import auth.strong as strong\n"
                "from auth import strong as s2\n"
                "def one():\n    check()\n"
                "def two():\n    strong.check()\n"
                "def three():\n    s2.check()\n"
                "def local():\n    one()\n"
            ),
        }
    )
    for key in [("one", "check"), ("two", "strong.check"), ("three", "s2.check"), ("local", "one")]:
        assert edges[key].resolved, key
    assert edges[("one", "check")].callee.file_path == "auth/strong.py"


def test_relative_import_and_self_method():
    edges = _edges(
        {
            "pkg/__init__.py": "",
            "pkg/util.py": "def helper():\n    pass\n",
            "pkg/mod.py": (
                "from .util import helper\n"
                "class C:\n"
                "    def a(self):\n        self.b()\n"
                "    def b(self):\n        helper()\n"
            ),
        }
    )
    assert edges[("C.a", "self.b")].resolved
    assert edges[("C.b", "helper")].callee.qualified_name == "helper"


def test_unresolved_calls_stay_unresolved():
    edges = _edges(
        {
            "a.py": (
                "import json\n"
                "def f(obj, name):\n"
                "    json.dumps(obj)\n"
                "    obj.save()\n"
                "    getattr(obj, name)()\n"
                "    len(name)\n"
            )
        }
    )
    assert edges[("f", "json.dumps")].external
    assert not edges[("f", "obj.save")].resolved
    assert not edges[("f", "obj.save")].external
    assert not edges[("f", "getattr")].resolved
    assert not edges[("f", "<dynamique>")].resolved
    assert ("f", "len") not in edges  # builtins are not noise in the unresolved count
