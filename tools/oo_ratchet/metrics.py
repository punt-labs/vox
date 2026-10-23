"""AST-derived object-orientation metrics for a single Python module."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from typing import ClassVar, Self


class ModuleMetrics:
    """Compute OO quality metrics for one parsed module."""

    # Nodes that each add exactly one path, whatever their contents.
    _ONE_WAY_BRANCHES: ClassVar[tuple[type[ast.AST], ...]] = (
        ast.If,
        ast.IfExp,
        ast.While,
        ast.For,
        ast.AsyncFor,
        ast.ExceptHandler,
        ast.Assert,
    )

    _path: str
    _tree: ast.Module
    _source_lines: int

    def __new__(cls, path: str, source: str) -> Self:
        self = super().__new__(cls)
        self._path = path
        self._tree = ast.parse(source, filename=path)
        self._source_lines = len([line for line in source.splitlines() if line.strip()])
        return self

    def compute(self) -> dict[str, float | int | str]:
        """Return the full metric dict for this module."""
        return {
            "file": self._path,
            "module_size": self._source_lines,
            "classes_per_module": self._count_classes(),
            "top_level_functions": self._count_top_level_functions(),
            "top_level_statements": self._count_top_level_statements(),
            "method_ratio": self._method_ratio(),
            "class_to_func_ratio": self._class_to_func_ratio(),
            "encapsulation_ratio": self._encapsulation_ratio(),
            "avg_params": self._avg_params(),
            "max_complexity": self._max_complexity(),
            "avg_complexity": self._avg_complexity(),
            "init_violations": self._count_init(),
            "public_attr_violations": self._count_public_attrs(),
            "future_annotations": self._has_future_annotations(),
        }

    def _count_classes(self) -> int:
        return sum(
            1
            for node in ast.iter_child_nodes(self._tree)
            if isinstance(node, ast.ClassDef) and not self._is_type_definition(node)
        )

    @staticmethod
    def _is_type_definition(node: ast.ClassDef) -> bool:
        type_bases = {"Protocol", "TypedDict"}
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in type_bases:
                return True
            if (
                isinstance(base, ast.Subscript)
                and isinstance(base.value, ast.Name)
                and base.value.id in type_bases
            ):
                return True
        return False

    def _count_top_level_functions(self) -> int:
        return sum(
            1
            for node in ast.iter_child_nodes(self._tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )

    def _count_top_level_statements(self) -> int:
        skip_types = (
            ast.Import,
            ast.ImportFrom,
            ast.ClassDef,
            ast.FunctionDef,
            ast.AsyncFunctionDef,
        )
        count = 0
        for node in ast.iter_child_nodes(self._tree):
            if isinstance(node, skip_types):
                continue
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                continue
            count += 1
        return count

    def _count_methods(self) -> int:
        count = 0
        for node in ast.walk(self._tree):
            if isinstance(node, ast.ClassDef):
                for item in ast.iter_child_nodes(node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        count += 1
        return count

    def _method_ratio(self) -> float:
        methods = self._count_methods()
        top_funcs = self._count_top_level_functions()
        total = methods + top_funcs
        if total == 0:
            top_stmts = self._count_top_level_statements()
            return 0.0 if top_stmts > 5 else 1.0
        return round(methods / total, 3)

    def _class_to_func_ratio(self) -> float:
        classes = self._count_classes()
        funcs = self._count_top_level_functions()
        total = classes + funcs
        if total == 0:
            top_stmts = self._count_top_level_statements()
            return 0.0 if top_stmts > 5 else 1.0
        return round(classes / total, 3)

    def _self_attr_names(self) -> Iterator[str]:
        """Yield the attribute name of every ``self.<attr> = ...`` assignment."""
        for node in ast.walk(self._tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign) and node.target is not None:
                targets = [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    yield target.attr

    def _encapsulation_ratio(self) -> float:
        attrs = list(self._self_attr_names())
        if not attrs:
            return 1.0
        private = sum(1 for attr in attrs if attr.startswith("_"))
        return round(private / len(attrs), 3)

    def _avg_params(self) -> float:
        counts = []
        for node in ast.walk(self._tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            param_count = len(args.args) + len(args.posonlyargs) + len(args.kwonlyargs)
            # The receiver is the first positional parameter, which lives in
            # posonlyargs for a PEP-570 signature (`def m(self, x, /)`) and in
            # args otherwise. Subtract it in both cases.
            leading = args.posonlyargs or args.args
            if leading and leading[0].arg in ("self", "cls"):
                param_count -= 1
            counts.append(param_count)
        if not counts:
            return 0.0
        return round(sum(counts) / len(counts), 2)

    def _max_complexity(self) -> int:
        max_cc = 0
        for node in ast.walk(self._tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                cc = self._cyclomatic_complexity(node)
                max_cc = max(max_cc, cc)
        return max_cc

    def _avg_complexity(self) -> float:
        complexities = [
            self._cyclomatic_complexity(node)
            for node in ast.walk(self._tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if not complexities:
            return 0.0
        return round(sum(complexities) / len(complexities), 2)

    @classmethod
    def _cyclomatic_complexity(
        cls,
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> int:
        """Return 1 plus the decision points of the code *func_node* runs.

        The module's functions are every ``def`` and ``async def`` in the tree,
        nested ones and methods of nested classes included, and each is scored
        on its own. So a nested ``def`` or ``async def`` body is not charged
        here; its header is, because its decorators and defaults run when this
        function executes the statement. For the same reason this function's
        own header belongs to the scope that defines it. A nested ``class``
        body is not a scored function: it runs once, when this function
        executes the ``class`` statement, so its bases, decorators, attributes,
        comprehensions, and ``if`` blocks count here, as do its methods'
        headers; only its methods' bodies are cut. A lambda is not scored on
        its own, so its body counts here.
        """
        return 1 + sum(map(cls._decision_points, cls._own_scope(func_node)))

    @staticmethod
    def _own_scope(
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> Iterator[ast.AST]:
        """Yield every node *func_node*'s body runs, stopping at nested def bodies."""
        pending: list[ast.AST] = list(func_node.body)
        while pending:
            node = pending.pop()
            yield node
            children = ast.iter_child_nodes(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nested_body = {id(stmt) for stmt in node.body}
                children = (c for c in children if id(c) not in nested_body)
            pending.extend(children)

    @classmethod
    def _decision_points(cls, node: ast.AST) -> int:
        """Return how many paths *node* adds to the function that contains it.

        Every ``case`` arm counts, ``case _`` included, and a guard is one more
        test on top of its pattern, the way ``and`` is in an ``if``. A match
        without a catch-all scores what its ``if``/``elif`` ladder scores; a
        ``case _`` costs one more than the ladder's ``else``, which counts 0.
        An or-pattern ``case A() | B()`` is the pattern form of ``or`` and adds
        one per alternative after the first, as a ``BoolOp`` does.
        """
        if isinstance(node, cls._ONE_WAY_BRANCHES):
            return 1
        match node:
            case ast.BoolOp(values=alternatives) | ast.MatchOr(patterns=alternatives):
                return len(alternatives) - 1
            case ast.comprehension(ifs=ifs):
                return 1 + len(ifs)
            case ast.match_case(guard=guard):
                return 1 + (guard is not None)
            case _:
                return 0

    @staticmethod
    def _has_dataclass_decorator(node: ast.ClassDef) -> bool:
        for d in node.decorator_list:
            if isinstance(d, ast.Name) and d.id == "dataclass":
                return True
            if (
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Name)
                and d.func.id == "dataclass"
            ):
                return True
            if isinstance(d, ast.Attribute) and d.attr == "dataclass":
                return True
        return False

    def _count_init(self) -> int:
        count = 0
        for node in ast.walk(self._tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if self._has_dataclass_decorator(node):
                continue
            for item in ast.iter_child_nodes(node):
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    count += 1
        return count

    def _count_public_attrs(self) -> int:
        return sum(1 for attr in self._self_attr_names() if not attr.startswith("_"))

    def _has_future_annotations(self) -> int:
        for node in ast.iter_child_nodes(self._tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "__future__"
                and any(alias.name == "annotations" for alias in node.names)
            ):
                return 1
        return 0
