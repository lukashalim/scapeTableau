"""Tableau formula tokenizer, AST parser, and SQL/DAX emitters."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Union

from engine.schema import ParameterInfo, ParseStatus, WorksheetInfo

KEYWORDS = {
    "IF",
    "THEN",
    "ELSEIF",
    "ELSE",
    "END",
    "AND",
    "OR",
    "NOT",
    "TRUE",
    "FALSE",
    "NULL",
    "CASE",
    "WHEN",
}

AGG_FUNCS = {"SUM", "AVG", "COUNT", "MIN", "MAX", "COUNTD", "ATTR"}
PASSTHROUGH = {"TRIM", "UPPER", "LOWER", "ABS", "ROUND"}
ROW_FUNCS = {
    "YEAR",
    "MONTH",
    "DAY",
    "QUARTER",
    "DATEDIFF",
    "DATEADD",
    "DATETRUNC",
    "DATEPART",
    "MAKEDATE",
    "LEN",
    "TRIM",
    "UPPER",
    "LOWER",
    "ABS",
    "ROUND",
    "CONTAINS",
    "STARTSWITH",
    "ENDSWITH",
    "ZN",
    "IFNULL",
    "ISNULL",
    "IIF",
}
DAX_ITER = {
    "SUM": "SUMX",
    "AVG": "AVERAGEX",
    "MIN": "MINX",
    "MAX": "MAXX",
    "COUNT": "COUNTX",
    "ATTR": "MINX",
}
DATE_INTERVAL = {
    "day": "DAY",
    "dd": "DAY",
    "month": "MONTH",
    "mm": "MONTH",
    "year": "YEAR",
    "yy": "YEAR",
    "yyyy": "YEAR",
    "quarter": "QUARTER",
    "qq": "QUARTER",
    "week": "WEEK",
    "wk": "WEEK",
    "hour": "HOUR",
    "hh": "HOUR",
    "minute": "MINUTE",
    "mi": "MINUTE",
    "second": "SECOND",
    "ss": "SECOND",
}
TABLE_CALC_PREFIXES = (
    "WINDOW_",
    "RUNNING_",
    "LOOKUP",
    "INDEX",
    "SIZE",
    "FIRST",
    "LAST",
    "RANK",
    "TOTAL",
    "PREVIOUS_VALUE",
)
LOD_KINDS = {"FIXED", "INCLUDE", "EXCLUDE"}
ILIKE_DIALECTS = {"snowflake", "postgres", "duckdb"}
SQL_DIALECTS = {"ansi", "snowflake", "postgres", "duckdb", "bigquery", "tsql"}
_BOOL_CMP_OPS = {"=", "<>", "!=", "<", ">", "<=", ">="}
_BOOL_LOGIC_OPS = {"AND", "OR"}

Node = Union[
    "Literal",
    "FieldRef",
    "ParamRef",
    "UnaryOp",
    "BinaryOp",
    "Call",
    "IfExpr",
    "CaseExpr",
    "LodExpr",
    "Paren",
]


@dataclass
class Literal:
    kind: str
    value: str


@dataclass
class FieldRef:
    name: str
    qualifier: str | None = None


@dataclass
class ParamRef:
    name: str


@dataclass
class UnaryOp:
    op: str
    expr: Node


@dataclass
class BinaryOp:
    op: str
    left: Node
    right: Node


@dataclass
class Call:
    name: str
    args: list[Node]


@dataclass
class IfExpr:
    branches: list[tuple[Node, Node]]
    else_expr: Node | None


@dataclass
class CaseExpr:
    expr: Node | None
    branches: list[tuple[Node, Node]]
    else_expr: Node | None


@dataclass
class LodExpr:
    kind: str
    dims: list[Node]
    agg: Node


@dataclass
class Paren:
    expr: Node


@dataclass
class Token:
    kind: str
    value: str
    pos: int


@dataclass
class FormulaContext:
    dialect: str = "ansi"
    target: str = "sql"
    parameters: dict[str, ParameterInfo] = field(default_factory=dict)
    calc_sql: dict[str, str] = field(default_factory=dict)
    calc_dax: dict[str, str] = field(default_factory=dict)
    calc_status: dict[str, ParseStatus] = field(default_factory=dict)
    calc_dax_status: dict[str, ParseStatus] = field(default_factory=dict)
    calc_dax_kind: dict[str, str] = field(default_factory=dict)
    calc_sql_kind: dict[str, str] = field(default_factory=dict)
    view_dims: list[str] | None = None
    source_table: str = "Orders"
    dax_table: str = "Orders"
    current_keys: set[str] = field(default_factory=set)
    original_formula: str = ""


class TranspileFormulaError(Exception):
    def __init__(self, message: str, pos: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.pos = pos


class LodFallback(Exception):
    def __init__(self, raw: str, reason: str = "LOD expression") -> None:
        super().__init__(raw)
        self.raw = raw
        self.reason = reason


class IncludeExcludeGrainError(Exception):
    def __init__(self, message: str = "INCLUDE/EXCLUDE requires view grain") -> None:
        super().__init__(message)
        self.message = message


def quote_ident(name: str, dialect: str = "ansi") -> str:
    cleaned = name.strip()
    dialect_l = dialect.lower()
    if dialect_l == "tsql":
        escaped = cleaned.replace("]", "]]")
        return f"[{escaped}]"
    escaped = cleaned.replace('"', '""')
    return f'"{escaped}"'


def split_tableau_ident(raw: str | None) -> list[str]:
    """Split Tableau ``[Schema].[Table]`` (or bare names) into unbracketed parts."""

    if not raw:
        return []
    text = raw.strip()
    if not text:
        return []
    # Multi-part: [Extract].[Extract] or Extract].[Extract after bad strip
    if "].[" in text:
        chunks = text.split("].[")
        parts: list[str] = []
        for chunk in chunks:
            part = chunk.strip().lstrip("[").rstrip("]")
            if part.endswith("$"):
                part = part[:-1]
            if part:
                parts.append(part)
        return parts
    part = text
    if part.startswith("[") and part.endswith("]"):
        part = part[1:-1]
    if part.endswith("$"):
        part = part[:-1]
    return [part] if part else []


def quote_table_ref(raw: str | None, dialect: str = "ansi") -> str:
    """Quote a Tableau table reference part-by-part: ``[A].[B]`` → ``"A"."B"``."""

    parts = split_tableau_ident(raw)
    if not parts:
        return quote_ident("Orders", dialect)
    return ".".join(quote_ident(part, dialect) for part in parts)


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def dax_string(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def normalize_number_literal(text: str) -> str:
    """Collapse float noise: ``18.400000000000002`` → ``18.4``."""

    raw = (text or "").strip()
    if not raw:
        return raw
    try:
        as_float = float(raw)
    except ValueError:
        return raw
    if as_float != as_float or as_float in {float("inf"), float("-inf")}:  # NaN / Inf
        return raw
    # Shortest round-trip that removes binary float noise written as long decimals.
    formatted = format(as_float, ".16g")
    if formatted.lower() in {"nan", "inf", "-inf"}:
        return raw
    return formatted


def like_op(dialect: str) -> str:
    if dialect.lower() in ILIKE_DIALECTS:
        return "ILIKE"
    return "LIKE"


def normalize_table_name(raw: str | None) -> str:
    """Return a single unquoted table label (last part of a multi-part ref)."""

    parts = split_tableau_ident(raw)
    if not parts:
        return ""
    return parts[-1]


def is_bool_sql_node(node: Node) -> bool:
    if isinstance(node, Literal) and node.kind == "bool":
        return True
    if isinstance(node, UnaryOp) and node.op == "NOT":
        return True
    if isinstance(node, BinaryOp):
        op = node.op.upper()
        if op in _BOOL_CMP_OPS or op in _BOOL_LOGIC_OPS:
            return True
    if isinstance(node, Call) and node.name.upper() in {"ISNULL", "CONTAINS", "STARTSWITH", "ENDSWITH"}:
        return True
    if isinstance(node, Paren):
        return is_bool_sql_node(node.expr)
    return False


def wrap_bool_sql(sql: str, dialect: str) -> str:
    """Emit dialect-safe boolean column/expression (T-SQL has no bare BOOLEAN)."""

    if dialect.lower() != "tsql":
        return sql
    return f"CASE WHEN {sql} THEN 1 ELSE 0 END"


def resolve_internal_names(formula: str, name_map: dict[str, str]) -> str:
    """Replace [Calculation_…] (and other internal names) with captions."""

    def repl(match: re.Match[str]) -> str:
        inner = match.group(1)
        caption = name_map.get(inner) or name_map.get(f"[{inner}]")
        if caption and caption != inner:
            return f"[{caption}]"
        return match.group(0)

    return re.sub(r"\[([^\]]+)\]", repl, formula)


def tokenize(formula: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(formula)
    while i < n:
        ch = formula[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "/" and i + 1 < n and formula[i + 1] == "/":
            while i < n and formula[i] not in "\n\r":
                i += 1
            continue
        if ch == "/" and i + 1 < n and formula[i + 1] == "*":
            i += 2
            while i + 1 < n and not (formula[i] == "*" and formula[i + 1] == "/"):
                i += 1
            i = min(i + 2, n)
            continue
        if ch == "{":
            tokens.append(Token("LBRACE", ch, i))
            i += 1
            continue
        if ch == "}":
            tokens.append(Token("RBRACE", ch, i))
            i += 1
            continue
        if ch == ":":
            tokens.append(Token("COLON", ch, i))
            i += 1
            continue
        if ch == "[":
            start = i
            i += 1
            inner: list[str] = []
            while i < n:
                if formula[i] == "]" and i + 1 < n and formula[i + 1] == "]":
                    inner.append("]")
                    i += 2
                    continue
                if formula[i] == "]":
                    i += 1
                    break
                inner.append(formula[i])
                i += 1
            tokens.append(Token("FIELD", "".join(inner), start))
            continue
        if ch in ("'", '"'):
            quote = ch
            start = i
            i += 1
            chars: list[str] = []
            while i < n:
                if formula[i] == quote and i + 1 < n and formula[i + 1] == quote:
                    chars.append(quote)
                    i += 2
                    continue
                if formula[i] == quote:
                    i += 1
                    break
                chars.append(formula[i])
                i += 1
            tokens.append(Token("STRING", "".join(chars), start))
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and formula[i + 1].isdigit()):
            start = i
            while i < n and (formula[i].isdigit() or formula[i] == "."):
                i += 1
            tokens.append(Token("NUMBER", formula[start:i], start))
            continue
        if ch.isalpha() or ch == "_":
            start = i
            while i < n and (formula[i].isalnum() or formula[i] == "_"):
                i += 1
            word = formula[start:i]
            upper = word.upper()
            if upper in KEYWORDS:
                tokens.append(Token("KEYWORD", upper, start))
            else:
                tokens.append(Token("IDENT", word, start))
            continue
        two = formula[i : i + 2]
        if two in {"<=", ">=", "<>", "!=", "=="}:
            tokens.append(Token("OP", "=" if two == "==" else two, i))
            i += 2
            continue
        if ch == "(":
            tokens.append(Token("LPAREN", ch, i))
            i += 1
            continue
        if ch == ")":
            tokens.append(Token("RPAREN", ch, i))
            i += 1
            continue
        if ch == ",":
            tokens.append(Token("COMMA", ch, i))
            i += 1
            continue
        if ch in "+-*/=<>.":
            tokens.append(Token("OP", ch, i))
            i += 1
            continue
        tokens.append(Token("OP", ch, i))
        i += 1
    tokens.append(Token("EOF", "", n))
    return tokens


def field_references(formula: str) -> list[str]:
    refs: list[str] = []
    tokens = tokenize(formula)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.kind == "FIELD":
            if (
                i + 2 < len(tokens)
                and tokens[i + 1].kind == "OP"
                and tokens[i + 1].value == "."
                and tokens[i + 2].kind == "FIELD"
            ):
                i += 3
                continue
            refs.append(tok.value)
        i += 1
    return refs


class Parser:
    def __init__(self, tokens: list[Token], dialect: str = "ansi") -> None:
        self.tokens = tokens
        self.i = 0
        self.dialect = dialect.lower()

    def peek(self) -> Token:
        return self.tokens[self.i]

    def advance(self) -> Token:
        tok = self.tokens[self.i]
        if tok.kind != "EOF":
            self.i += 1
        return tok

    def match(self, kind: str, value: str | None = None) -> Token | None:
        tok = self.peek()
        if tok.kind == kind and (value is None or tok.value == value):
            return self.advance()
        return None

    def expect(self, kind: str, value: str | None = None) -> Token:
        tok = self.match(kind, value)
        if tok is None:
            got = self.peek()
            wanted = value or kind
            raise TranspileFormulaError(f"Expected {wanted}, got {got.kind} {got.value!r}", got.pos)
        return tok

    def parse(self) -> Node:
        expr = self.parse_or()
        if self.peek().kind != "EOF":
            leftover = self.peek()
            if leftover.kind == "KEYWORD" and leftover.value in {"THEN", "ELSEIF", "ELSE", "END", "WHEN"}:
                raise TranspileFormulaError(f"Unexpected {leftover.value}", leftover.pos)
            raise TranspileFormulaError(
                f"Unexpected token {leftover.kind} {leftover.value!r}", leftover.pos
            )
        return expr

    def parse_or(self) -> Node:
        left = self.parse_and()
        while self.match("KEYWORD", "OR"):
            right = self.parse_and()
            left = BinaryOp("OR", left, right)
        return left

    def parse_and(self) -> Node:
        left = self.parse_not()
        while self.match("KEYWORD", "AND"):
            right = self.parse_not()
            left = BinaryOp("AND", left, right)
        return left

    def parse_not(self) -> Node:
        if self.match("KEYWORD", "NOT"):
            return UnaryOp("NOT", self.parse_not())
        return self.parse_comparison()

    def parse_comparison(self) -> Node:
        left = self.parse_add()
        tok = self.peek()
        if tok.kind == "OP" and tok.value in {"=", "<>", "!=", "<", ">", "<=", ">="}:
            op = self.advance().value
            if op == "!=":
                op = "<>"
            right = self.parse_add()
            return BinaryOp(op, left, right)
        return left

    def parse_add(self) -> Node:
        left = self.parse_mul()
        while self.peek().kind == "OP" and self.peek().value in {"+", "-"}:
            op = self.advance().value
            right = self.parse_mul()
            left = BinaryOp(op, left, right)
        return left

    def parse_mul(self) -> Node:
        left = self.parse_unary()
        while self.peek().kind == "OP" and self.peek().value in {"*", "/"}:
            op = self.advance().value
            right = self.parse_unary()
            left = BinaryOp(op, left, right)
        return left

    def parse_unary(self) -> Node:
        if self.peek().kind == "OP" and self.peek().value == "-":
            self.advance()
            return UnaryOp("-", self.parse_unary())
        if self.peek().kind == "OP" and self.peek().value == "+":
            self.advance()
            return self.parse_unary()
        return self.parse_primary()

    def parse_primary(self) -> Node:
        tok = self.peek()
        if tok.kind == "KEYWORD" and tok.value == "IF":
            return self.parse_if()
        if tok.kind == "KEYWORD" and tok.value == "CASE":
            return self.parse_case()
        if tok.kind == "KEYWORD" and tok.value in {"TRUE", "FALSE"}:
            self.advance()
            return Literal("bool", tok.value)
        if tok.kind == "KEYWORD" and tok.value == "NULL":
            self.advance()
            return Literal("null", "NULL")
        if tok.kind == "NUMBER":
            return Literal("number", normalize_number_literal(self.advance().value))
        if tok.kind == "STRING":
            return Literal("string", self.advance().value)
        if tok.kind == "FIELD":
            return self.parse_field()
        if tok.kind == "IDENT":
            name = self.advance().value
            if name.upper() == "NULL":
                return Literal("null", "NULL")
            if self.peek().kind == "LPAREN":
                return Call(name, self.parse_args())
            return FieldRef(name)
        if tok.kind == "LPAREN":
            self.advance()
            expr = self.parse_or()
            self.expect("RPAREN")
            return Paren(expr)
        if tok.kind == "LBRACE":
            return self.parse_lod()
        raise TranspileFormulaError(f"Unexpected token {tok.kind} {tok.value!r}", tok.pos)

    def parse_field(self) -> Node:
        name = self.advance().value
        if self.peek().kind == "OP" and self.peek().value == ".":
            self.advance()
            if self.peek().kind == "FIELD":
                child = self.advance().value
                if name.lower() == "parameters":
                    return ParamRef(child)
                return FieldRef(child, qualifier=name)
            if self.peek().kind == "IDENT":
                child = self.advance().value
                if name.lower() == "parameters":
                    return ParamRef(child)
                return FieldRef(child, qualifier=name)
        if name.lower() == "parameters":
            return ParamRef(name)
        return FieldRef(name)

    def parse_if(self) -> IfExpr:
        self.expect("KEYWORD", "IF")
        branches: list[tuple[Node, Node]] = []
        cond = self.parse_or()
        self.expect("KEYWORD", "THEN")
        then_expr = self.parse_or()
        branches.append((cond, then_expr))
        else_expr: Node | None = None
        while True:
            if self.match("KEYWORD", "ELSEIF"):
                c = self.parse_or()
                self.expect("KEYWORD", "THEN")
                t = self.parse_or()
                branches.append((c, t))
                continue
            if self.match("KEYWORD", "ELSE"):
                else_expr = self.parse_or()
                break
            break
        self.expect("KEYWORD", "END")
        return IfExpr(branches, else_expr)

    def parse_case(self) -> CaseExpr:
        self.expect("KEYWORD", "CASE")
        if self.peek().kind == "KEYWORD" and self.peek().value == "WHEN":
            expr: Node | None = None
        else:
            expr = self.parse_or()
        branches: list[tuple[Node, Node]] = []
        while self.match("KEYWORD", "WHEN"):
            c = self.parse_or()
            self.expect("KEYWORD", "THEN")
            t = self.parse_or()
            branches.append((c, t))
        else_expr: Node | None = None
        if self.match("KEYWORD", "ELSE"):
            else_expr = self.parse_or()
        self.expect("KEYWORD", "END")
        return CaseExpr(expr, branches, else_expr)

    def parse_args(self) -> list[Node]:
        self.expect("LPAREN")
        args: list[Node] = []
        if self.peek().kind != "RPAREN":
            args.append(self.parse_or())
            while self.match("COMMA"):
                args.append(self.parse_or())
        self.expect("RPAREN")
        return args

    def parse_lod(self) -> LodExpr:
        self.expect("LBRACE")
        kind = "FIXED"
        tok = self.peek()
        if tok.kind == "IDENT" and tok.value.upper() in LOD_KINDS:
            kind = self.advance().value.upper()
        dims: list[Node] = []
        if self.match("COLON"):
            agg = self.parse_or()
        else:
            first = self.parse_or()
            if self.match("COMMA"):
                dims = [first, self.parse_or()]
                while self.match("COMMA"):
                    dims.append(self.parse_or())
                self.expect("COLON")
                agg = self.parse_or()
            elif self.match("COLON"):
                dims = [first]
                agg = self.parse_or()
            else:
                agg = first
        self.expect("RBRACE")
        return LodExpr(kind, dims, agg)


def _walk(node: Node):
    yield node
    if isinstance(node, (UnaryOp, Paren)):
        yield from _walk(node.expr)
    elif isinstance(node, BinaryOp):
        yield from _walk(node.left)
        yield from _walk(node.right)
    elif isinstance(node, Call):
        for arg in node.args:
            yield from _walk(arg)
    elif isinstance(node, IfExpr):
        for c, t in node.branches:
            yield from _walk(c)
            yield from _walk(t)
        if node.else_expr is not None:
            yield from _walk(node.else_expr)
    elif isinstance(node, CaseExpr):
        if node.expr is not None:
            yield from _walk(node.expr)
        for c, t in node.branches:
            yield from _walk(c)
            yield from _walk(t)
        if node.else_expr is not None:
            yield from _walk(node.else_expr)
    elif isinstance(node, LodExpr):
        for dim in node.dims:
            yield from _walk(dim)
        yield from _walk(node.agg)


def _has_lod(node: Node) -> bool:
    return any(isinstance(n, LodExpr) for n in _walk(node))


def _has_countd(node: Node) -> bool:
    return any(isinstance(n, Call) and n.name.upper() == "COUNTD" for n in _walk(node))


def _is_dateish_name(name: str) -> bool:
    lowered = name.strip().lower()
    return "date" in lowered


def _unwrap_node(node: Node) -> Node:
    while isinstance(node, Paren):
        node = node.expr
    return node


def _is_row_level_lod(node: LodExpr) -> bool:
    """FIXED MIN/MAX of a date is a row attribute (e.g. First Purchase Date)."""

    agg = _unwrap_node(node.agg)
    if isinstance(agg, Call) and agg.name.upper() in {"MIN", "MAX", "ATTR"} and agg.args:
        arg = _unwrap_node(agg.args[0])
        if isinstance(arg, FieldRef) and _is_dateish_name(arg.name):
            return True
    return False


def _kind_combine(kinds: list[str]) -> str:
    if "calculated_column" in kinds:
        return "calculated_column"
    if "measure" in kinds:
        return "measure"
    return "calculated_column"


def _is_table_calc_name(fn: str) -> bool:
    upper = fn.upper()
    return any(upper.startswith(p) or upper == p.rstrip("_") for p in TABLE_CALC_PREFIXES)


def _lookup_sql_kind(ctx: FormulaContext, name: str) -> str | None:
    bare = name.strip("[]")
    for key in (name, bare, f"[{bare}]"):
        kind = ctx.calc_sql_kind.get(key)
        if kind:
            return kind
    return None


def _has_bare_tableau_agg(node: Node) -> bool:
    """True if a Tableau agg/table-calc appears outside any LOD wrapper."""

    node = _unwrap_node(node)
    if isinstance(node, LodExpr):
        return False
    if isinstance(node, Call):
        fn = node.name.upper()
        if fn in AGG_FUNCS or _is_table_calc_name(fn):
            return True
        return any(_has_bare_tableau_agg(arg) for arg in node.args)
    if isinstance(node, UnaryOp):
        return _has_bare_tableau_agg(node.expr)
    if isinstance(node, BinaryOp):
        return _has_bare_tableau_agg(node.left) or _has_bare_tableau_agg(node.right)
    if isinstance(node, IfExpr):
        for cond, then in node.branches:
            if _has_bare_tableau_agg(cond) or _has_bare_tableau_agg(then):
                return True
        return bool(node.else_expr and _has_bare_tableau_agg(node.else_expr))
    if isinstance(node, CaseExpr):
        if node.expr is not None and _has_bare_tableau_agg(node.expr):
            return True
        for cond, then in node.branches:
            if _has_bare_tableau_agg(cond) or _has_bare_tableau_agg(then):
                return True
        return bool(node.else_expr and _has_bare_tableau_agg(node.else_expr))
    if isinstance(node, Paren):
        return _has_bare_tableau_agg(node.expr)
    return False


def classify_sql_kind(node: Node, ctx: FormulaContext | None = None) -> str:
    """Classify a formula for SQL script emission: ``row`` or ``aggregate``.

    - **row**: no bare Tableau agg at the expression root. Includes row helpers and
      LOD (`{FIXED …}` etc.) which emit ``OVER (…)`` / scalar subqueries at grain.
    - **aggregate**: bare ``SUM``/``AVG``/…, table calcs, wrappers containing those
      (e.g. ``ABS(SUM(Profit)/SUM(Sales))``), or a FieldRef to another aggregate calc.
    """

    ctx = ctx or FormulaContext()
    node = _unwrap_node(node)
    if isinstance(node, LodExpr):
        return "row"
    if isinstance(node, FieldRef):
        ref_kind = _lookup_sql_kind(ctx, node.name)
        return "aggregate" if ref_kind == "aggregate" else "row"
    if isinstance(node, ParamRef):
        return "row"
    if isinstance(node, Call):
        fn = node.name.upper()
        if fn in AGG_FUNCS or _is_table_calc_name(fn):
            return "aggregate"
        if any(_has_bare_tableau_agg(arg) for arg in node.args):
            return "aggregate"
        if any(_lookup_sql_kind(ctx, n.name) == "aggregate" for n in _walk(node) if isinstance(n, FieldRef)):
            return "aggregate"
        return "row"
    if isinstance(node, (UnaryOp, BinaryOp, IfExpr, CaseExpr)):
        if _has_bare_tableau_agg(node):
            return "aggregate"
        if any(
            _lookup_sql_kind(ctx, n.name) == "aggregate"
            for n in _walk(node)
            if isinstance(n, FieldRef)
        ):
            return "aggregate"
        return "row"
    return "row"


def classify_dax_kind(node: Node, ctx: FormulaContext | None = None) -> str:
    """measure if the root is an agg/LOD; bare T[Col] anywhere → calculated_column."""

    ctx = ctx or FormulaContext()
    if isinstance(node, LodExpr):
        return "calculated_column" if _is_row_level_lod(node) else "measure"
    if isinstance(node, Paren):
        return classify_dax_kind(node.expr, ctx)
    if isinstance(node, UnaryOp):
        return classify_dax_kind(node.expr, ctx)
    if isinstance(node, FieldRef):
        return ctx.calc_dax_kind.get(node.name, "calculated_column")
    if isinstance(node, ParamRef):
        return "calculated_column"
    if isinstance(node, Call):
        fn = node.name.upper()
        if fn in AGG_FUNCS:
            return "measure"
        if fn in ROW_FUNCS:
            return "calculated_column"
        kinds = [classify_dax_kind(arg, ctx) for arg in node.args]
        return _kind_combine(kinds)
    if isinstance(node, BinaryOp):
        return _kind_combine(
            [classify_dax_kind(node.left, ctx), classify_dax_kind(node.right, ctx)]
        )
    if isinstance(node, IfExpr):
        parts = [classify_dax_kind(c, ctx) for c, _ in node.branches]
        parts.extend(classify_dax_kind(t, ctx) for _, t in node.branches)
        if node.else_expr is not None:
            parts.append(classify_dax_kind(node.else_expr, ctx))
        return _kind_combine(parts)
    if isinstance(node, CaseExpr):
        parts = [classify_dax_kind(c, ctx) for c, _ in node.branches]
        parts.extend(classify_dax_kind(t, ctx) for _, t in node.branches)
        if node.expr is not None:
            parts.append(classify_dax_kind(node.expr, ctx))
        if node.else_expr is not None:
            parts.append(classify_dax_kind(node.else_expr, ctx))
        return _kind_combine(parts)
    return "calculated_column"


def param_measure_name(caption: str) -> str:
    return f"{caption} Value"


def _param_values(info: ParameterInfo) -> tuple[str, str]:
    raw = info.value if info.value not in (None, "") else (info.formula or "NULL")
    dt = (info.datatype or "").lower()
    if dt in {"string", "str"}:
        text = str(raw).strip().strip('"').strip("'")
        # Tableau stores list members with escaped \% etc.
        text = text.replace("\\%", "%").replace('\\"', '"')
        return sql_string(text), dax_string(text)
    if dt in {"boolean", "bool"}:
        flag = str(raw).strip().lower() in {"true", "1", "yes"}
        lit = "TRUE" if flag else "FALSE"
        return lit, lit
    text = str(raw).strip().strip('"')
    if re.fullmatch(r"-?\d+(\.\d+)?([eE][+-]?\d+)?", text):
        normalized = normalize_number_literal(text)
        return normalized, normalized
    if dt in {"date", "datetime"} or (text.startswith("#") and text.endswith("#")):
        inner = text.strip("#")
        return sql_string(inner), f"DATEVALUE({dax_string(inner)})"
    return text, text


def param_dax_literal(info: ParameterInfo) -> str:
    _, dax = _param_values(info)
    return dax


def _norm_part(arg: Node) -> str:
    if isinstance(arg, Literal) and arg.kind == "string":
        return arg.value.strip().lower()
    if isinstance(arg, Literal):
        return str(arg.value).strip().lower()
    if isinstance(arg, FieldRef):
        return arg.name.strip().lower()
    return ""


def _interval(part: str, amount_sql: str, dialect: str) -> str:
    unit = {
        "year": "YEAR",
        "yy": "YEAR",
        "yyyy": "YEAR",
        "quarter": "MONTH",
        "qq": "MONTH",
        "month": "MONTH",
        "mm": "MONTH",
        "week": "DAY",
        "wk": "DAY",
        "day": "DAY",
        "dd": "DAY",
        "hour": "HOUR",
        "hh": "HOUR",
        "minute": "MINUTE",
        "mi": "MINUTE",
        "second": "SECOND",
        "ss": "SECOND",
    }.get(part, "DAY")
    factor = "3 * " if part in {"quarter", "qq"} else ("7 * " if part in {"week", "wk"} else "")
    if dialect == "snowflake":
        snow_unit = "QUARTER" if part in {"quarter", "qq"} else unit
        if part in {"week", "wk"}:
            return f"DATEADD(day, 7 * ({amount_sql}), {{date}})"
        return f"DATEADD({snow_unit.lower()}, {amount_sql}, {{date}})"
    if dialect == "tsql":
        tsql_unit = "quarter" if part in {"quarter", "qq"} else unit.lower()
        if part in {"week", "wk"}:
            return f"DATEADD(day, 7 * ({amount_sql}), {{date}})"
        if part in {"quarter", "qq"}:
            return f"DATEADD(month, 3 * ({amount_sql}), {{date}})"
        return f"DATEADD({tsql_unit}, {amount_sql}, {{date}})"
    if dialect in {"postgres", "duckdb"}:
        return f"{{date}} + ({factor}{amount_sql}) * INTERVAL '1 {unit.lower()}'"
    # ansi — interval arithmetic
    return f"{{date}} + ({factor}{amount_sql}) * INTERVAL '1' {unit}"


class Emitter:
    def __init__(self, ctx: FormulaContext) -> None:
        self.ctx = ctx
        self.target = ctx.target
        self.dialect = ctx.dialect.lower()
        if self.dialect == "dax":
            self.target = "dax"
        self.partial = False
        self.unknown_functions: list[str] = []
        self.lod_cte = False

    def emit(self, node: Node) -> str:
        if self.target == "dax":
            return self.emit_dax(node)
        return self.emit_sql(node)

    def emit_sql(self, node: Node) -> str:
        if isinstance(node, Literal):
            if node.kind == "string":
                return sql_string(node.value)
            if node.kind == "bool":
                if self.dialect == "tsql":
                    return "1" if node.value == "TRUE" else "0"
                return "TRUE" if node.value == "TRUE" else "FALSE"
            if node.kind == "null":
                return "NULL"
            if node.kind == "number":
                return normalize_number_literal(node.value)
            return node.value
        if isinstance(node, FieldRef):
            return self._sql_field(node)
        if isinstance(node, ParamRef):
            return self._sql_param(node)
        if isinstance(node, UnaryOp):
            inner = self.emit_sql(node.expr)
            if node.op == "NOT":
                return f"NOT {inner}"
            return f"{node.op}{inner}"
        if isinstance(node, BinaryOp):
            op = node.op
            return f"{self.emit_sql(node.left)} {op} {self.emit_sql(node.right)}"
        if isinstance(node, Paren):
            return f"({self.emit_sql(node.expr)})"
        if isinstance(node, IfExpr):
            parts = ["CASE"]
            for c, t in node.branches:
                parts.append(f"WHEN {self.emit_sql(c)} THEN {self.emit_sql(t)}")
            if node.else_expr is not None:
                parts.append(f"ELSE {self.emit_sql(node.else_expr)}")
            parts.append("END")
            return " ".join(parts)
        if isinstance(node, CaseExpr):
            parts = ["CASE"]
            if node.expr is not None:
                parts.append(self.emit_sql(node.expr))
            for c, t in node.branches:
                parts.append(f"WHEN {self.emit_sql(c)} THEN {self.emit_sql(t)}")
            if node.else_expr is not None:
                parts.append(f"ELSE {self.emit_sql(node.else_expr)}")
            parts.append("END")
            return " ".join(parts)
        if isinstance(node, LodExpr):
            return self._sql_lod(node)
        if isinstance(node, Call):
            return self._sql_call(node)
        raise TranspileFormulaError(f"Unsupported node {type(node)!r}")

    def emit_dax(self, node: Node) -> str:
        if isinstance(node, Literal):
            if node.kind == "string":
                return dax_string(node.value)
            if node.kind == "bool":
                return "TRUE" if node.value == "TRUE" else "FALSE"
            if node.kind == "null":
                return "BLANK()"
            if node.kind == "number":
                return normalize_number_literal(node.value)
            return node.value
        if isinstance(node, FieldRef):
            return self._dax_field(node)
        if isinstance(node, ParamRef):
            return self._dax_param(node)
        if isinstance(node, UnaryOp):
            inner = self.emit_dax(node.expr)
            if node.op == "NOT":
                return f"NOT({inner})"
            return f"{node.op}{inner}"
        if isinstance(node, BinaryOp):
            if node.op == "/":
                return f"DIVIDE({self.emit_dax(node.left)}, {self.emit_dax(node.right)})"
            op = {"AND": "&&", "OR": "||", "<>": "<>"}.get(node.op, node.op)
            return f"{self.emit_dax(node.left)} {op} {self.emit_dax(node.right)}"
        if isinstance(node, Paren):
            return f"({self.emit_dax(node.expr)})"
        if isinstance(node, IfExpr):
            return self._dax_if(node.branches, node.else_expr)
        if isinstance(node, CaseExpr):
            return self._dax_case(node)
        if isinstance(node, LodExpr):
            return self._dax_lod(node)
        if isinstance(node, Call):
            return self._dax_call(node)
        raise TranspileFormulaError(f"Unsupported node {type(node)!r}")

    def _sql_field(self, node: FieldRef) -> str:
        key = node.name
        if key not in self.ctx.current_keys and key in self.ctx.calc_sql:
            status = self.ctx.calc_status.get(key)
            if status is not None and status != ParseStatus.ok:
                raise TranspileFormulaError(f"Dependency failed: {key}")
            return f"({self.ctx.calc_sql[key]})"
        return quote_ident(node.name, self.dialect)

    def _dax_table(self) -> str:
        return self.ctx.dax_table or self.ctx.source_table or "Orders"

    def _dax_col(self, name: str) -> str:
        return f"{self._dax_table()}[{name}]"

    def _dax_field(self, node: FieldRef) -> str:
        key = node.name
        if key not in self.ctx.current_keys and key in self.ctx.calc_dax:
            status = self.ctx.calc_dax_status.get(key)
            if status is None:
                status = self.ctx.calc_status.get(key)
            if status is not None and status != ParseStatus.ok:
                raise TranspileFormulaError(f"Dependency failed: {key}")
            kind = self.ctx.calc_dax_kind.get(key, "measure")
            if kind == "measure":
                return f"[{key}]"
            return self._dax_col(key)
        return self._dax_col(node.name)

    def _lookup_param(self, name: str) -> ParameterInfo | None:
        key = name.strip()
        bare = key[1:-1] if key.startswith("[") and key.endswith("]") else key
        params = self.ctx.parameters
        for candidate in (bare, f"[{bare}]", key):
            if candidate in params:
                return params[candidate]
        lower = bare.lower()
        for p in params.values():
            if p.caption.lower() == lower or p.name.strip("[]").lower() == lower:
                return p
        return None

    def _param_literal(self, info: ParameterInfo) -> tuple[str, str]:
        """Return (sql_literal, dax_literal) with no inline comments in expressions."""
        return _param_values(info)

    def _sql_param(self, node: ParamRef) -> str:
        info = self._lookup_param(node.name)
        if info is None:
            raise TranspileFormulaError(f"Unknown parameter {node.name}")
        sql, _ = self._param_literal(info)
        return sql

    def _dax_param(self, node: ParamRef) -> str:
        info = self._lookup_param(node.name)
        if info is None:
            raise TranspileFormulaError(f"Unknown parameter {node.name}")
        return f"[{param_measure_name(info.caption)}]"

    def _dax_if(self, branches: list[tuple[Node, Node]], else_expr: Node | None) -> str:
        if not branches:
            return self.emit_dax(else_expr) if else_expr is not None else "BLANK()"
        cond, then = branches[0]
        rest = self._dax_if(branches[1:], else_expr)
        return f"IF({self.emit_dax(cond)}, {self.emit_dax(then)}, {rest})"

    def _dax_case(self, node: CaseExpr) -> str:
        else_part = self.emit_dax(node.else_expr) if node.else_expr is not None else "BLANK()"
        if node.expr is None:
            parts = ["SWITCH(TRUE()"]
            for cond, res in node.branches:
                parts.append(f"{self.emit_dax(cond)}, {self.emit_dax(res)}")
            parts.append(else_part)
            return ", ".join(parts) + ")"
        parts = [f"SWITCH({self.emit_dax(node.expr)}"]
        for val, res in node.branches:
            parts.append(f"{self.emit_dax(val)}, {self.emit_dax(res)}")
        parts.append(else_part)
        return ", ".join(parts) + ")"

    def _sql_call(self, node: Call) -> str:
        fn = node.name.upper()
        if fn in AGG_FUNCS and node.args:
            inner = node.args[0]
            while isinstance(inner, Paren):
                inner = inner.expr
            if isinstance(inner, LodExpr):
                return self._grouped_agg_over_lod(fn, inner)
        args = [self.emit_sql(a) for a in node.args]
        original = node.name
        if fn == "IIF":
            if len(args) != 3:
                raise TranspileFormulaError("IIF expects 3 arguments")
            return f"CASE WHEN {args[0]} THEN {args[1]} ELSE {args[2]} END"
        if fn == "ZN":
            if len(args) != 1:
                raise TranspileFormulaError("ZN expects 1 argument")
            return f"COALESCE({args[0]}, 0)"
        if fn == "IFNULL":
            if len(args) != 2:
                raise TranspileFormulaError("IFNULL expects 2 arguments")
            return f"COALESCE({args[0]}, {args[1]})"
        if fn == "ISNULL":
            if len(args) != 1:
                raise TranspileFormulaError("ISNULL expects 1 argument")
            return f"({args[0]} IS NULL)"
        if fn == "CONTAINS":
            return self._like(args, prefix="", suffix="", wrap_both=True, fn="CONTAINS")
        if fn == "STARTSWITH":
            return self._like(args, prefix="", suffix="%", wrap_both=False, fn="STARTSWITH")
        if fn == "ENDSWITH":
            return self._like(args, prefix="%", suffix="", wrap_both=False, fn="ENDSWITH")
        if fn == "LEN":
            if len(args) != 1:
                raise TranspileFormulaError("LEN expects 1 argument")
            return f"LENGTH({args[0]})"
        if fn == "COUNTD":
            if len(args) != 1:
                raise TranspileFormulaError("COUNTD expects 1 argument")
            return f"COUNT(DISTINCT {args[0]})"
        if fn == "ATTR":
            if len(args) != 1:
                raise TranspileFormulaError("ATTR expects 1 argument")
            return f"MIN({args[0]})"
        if fn in AGG_FUNCS or fn in PASSTHROUGH:
            return f"{fn}({', '.join(args)})"
        if fn == "TODAY":
            return "CURRENT_DATE"
        if fn == "NOW":
            return "CURRENT_TIMESTAMP"
        date_sql = self._sql_date(fn, node, args)
        if date_sql is not None:
            return date_sql
        if any(fn.startswith(p) or fn == p.rstrip("_") for p in TABLE_CALC_PREFIXES):
            call = f"{original}({', '.join(args)})"
            self.partial = True
            self.unknown_functions.append(fn)
            return f"/* TODO: Tableau table calc {fn}: {self.ctx.original_formula or call} */ NULL"
        call = f"{original}({', '.join(args)})"
        self.partial = True
        self.unknown_functions.append(fn)
        return f"/* TODO: unmapped Tableau function {fn}: {self.ctx.original_formula or call} */ {call}"

    def _dax_call(self, node: Call) -> str:
        fn = node.name.upper()
        if fn in AGG_FUNCS and node.args:
            inner = node.args[0]
            while isinstance(inner, Paren):
                inner = inner.expr
            if isinstance(inner, LodExpr):
                return self._dax_agg_over_lod(fn, inner)
        args = [self.emit_dax(a) for a in node.args]
        original = node.name
        if fn == "IIF" or fn == "IF":
            if len(args) != 3:
                raise TranspileFormulaError("IIF expects 3 arguments")
            return f"IF({args[0]}, {args[1]}, {args[2]})"
        if fn == "ZN":
            if len(args) != 1:
                raise TranspileFormulaError("ZN expects 1 argument")
            return f"IF(ISBLANK({args[0]}), 0, {args[0]})"
        if fn == "IFNULL":
            if len(args) != 2:
                raise TranspileFormulaError("IFNULL expects 2 arguments")
            return f"IF(ISBLANK({args[0]}), {args[1]}, {args[0]})"
        if fn == "ISNULL":
            if len(args) != 1:
                raise TranspileFormulaError("ISNULL expects 1 argument")
            return f"ISBLANK({args[0]})"
        if fn == "CONTAINS":
            if len(args) != 2:
                raise TranspileFormulaError("CONTAINS expects 2 arguments")
            return f"CONTAINSSTRING({args[0]}, {args[1]})"
        if fn == "STARTSWITH":
            if len(args) != 2:
                raise TranspileFormulaError("STARTSWITH expects 2 arguments")
            return f"LEFT({args[0]}, LEN({args[1]})) = {args[1]}"
        if fn == "ENDSWITH":
            if len(args) != 2:
                raise TranspileFormulaError("ENDSWITH expects 2 arguments")
            return f"RIGHT({args[0]}, LEN({args[1]})) = {args[1]}"
        if fn == "LEN":
            if len(args) != 1:
                raise TranspileFormulaError("LEN expects 1 argument")
            return f"LEN({args[0]})"
        if fn == "COUNTD":
            if len(args) != 1:
                raise TranspileFormulaError("COUNTD expects 1 argument")
            return f"DISTINCTCOUNT({args[0]})"
        if fn in AGG_FUNCS or fn in PASSTHROUGH:
            return f"{fn}({', '.join(args)})"
        if fn == "TODAY":
            return "TODAY()"
        if fn == "NOW":
            return "NOW()"
        date_dax = self._dax_date(fn, node, args)
        if date_dax is not None:
            return date_dax
        if any(fn.startswith(p) or fn == p.rstrip("_") for p in TABLE_CALC_PREFIXES):
            self.partial = True
            self.unknown_functions.append(fn)
            return f"/* TODO: Tableau table calc {fn}: {self.ctx.original_formula} */ BLANK()"
        call = f"{original}({', '.join(args)})"
        self.partial = True
        self.unknown_functions.append(fn)
        return f"/* TODO: unmapped Tableau function {fn}: {self.ctx.original_formula or call} */ {call}"

    def _sql_date(self, fn: str, node: Call, args: list[str]) -> str | None:
        dialect = self.dialect
        if fn in {"YEAR", "MONTH", "DAY", "QUARTER"}:
            if len(args) != 1:
                raise TranspileFormulaError(f"{fn} expects 1 argument")
            if dialect == "tsql":
                return f"DATEPART({fn.lower()}, {args[0]})"
            if dialect == "snowflake":
                return f"{fn}({args[0]})"
            return f"EXTRACT({fn} FROM {args[0]})"
        if fn == "DATEPART":
            if len(args) != 2:
                raise TranspileFormulaError("DATEPART expects 2 arguments")
            part = _norm_part(node.args[0]) or "day"
            unit = part.upper()
            if dialect == "tsql":
                return f"DATEPART({part}, {args[1]})"
            if dialect == "snowflake":
                return f"DATE_PART('{part}', {args[1]})"
            return f"EXTRACT({unit} FROM {args[1]})"
        if fn == "DATETRUNC":
            if len(args) != 2:
                raise TranspileFormulaError("DATETRUNC expects 2 arguments")
            part = _norm_part(node.args[0]) or "day"
            if dialect == "tsql":
                return f"DATETRUNC({part}, {args[1]})"
            if dialect == "snowflake":
                return f"DATE_TRUNC('{part}', {args[1]})"
            return f"DATE_TRUNC('{part}', {args[1]})"
        if fn == "DATEDIFF":
            if len(args) != 3:
                raise TranspileFormulaError("DATEDIFF expects 3 arguments")
            part = _norm_part(node.args[0]) or "day"
            start, end = args[1], args[2]
            if dialect == "snowflake":
                return f"DATEDIFF('{part}', {start}, {end})"
            if dialect == "duckdb":
                return f"DATE_DIFF('{part}', {start}, {end})"
            if dialect == "tsql":
                return f"DATEDIFF({part}, {start}, {end})"
            if dialect == "postgres":
                if part in {"day", "dd"}:
                    return f"({end}::date - {start}::date)"
                return f"(EXTRACT({part.upper()} FROM {end}) - EXTRACT({part.upper()} FROM {start}))"
            # ansi — portable date subtraction (no vendor DATEDIFF)
            if part in {"day", "dd"}:
                return f"(CAST({end} AS DATE) - CAST({start} AS DATE))"
            return (
                f"(EXTRACT({part.upper()} FROM CAST({end} AS TIMESTAMP)) - "
                f"EXTRACT({part.upper()} FROM CAST({start} AS TIMESTAMP)))"
            )
        if fn == "DATEADD":
            if len(args) != 3:
                raise TranspileFormulaError("DATEADD expects 3 arguments")
            part = _norm_part(node.args[0]) or "day"
            template = _interval(part, args[1], dialect)
            return template.replace("{date}", args[2])
        if fn == "MAKEDATE":
            if len(args) != 3:
                raise TranspileFormulaError("MAKEDATE expects 3 arguments")
            if dialect == "tsql":
                return f"DATEFROMPARTS({args[0]}, {args[1]}, {args[2]})"
            if dialect == "snowflake":
                return f"DATE_FROM_PARTS({args[0]}, {args[1]}, {args[2]})"
            return f"MAKE_DATE({args[0]}, {args[1]}, {args[2]})"
        return None

    def _dax_interval(self, node: Call, index: int = 0) -> str:
        part = _norm_part(node.args[index]) if node.args else "day"
        return DATE_INTERVAL.get(part, (part or "DAY").upper())

    def _dax_date(self, fn: str, node: Call, args: list[str]) -> str | None:
        if fn in {"YEAR", "MONTH", "DAY"}:
            if len(args) != 1:
                raise TranspileFormulaError(f"{fn} expects 1 argument")
            return f"{fn}({args[0]})"
        if fn == "QUARTER":
            if len(args) != 1:
                raise TranspileFormulaError("QUARTER expects 1 argument")
            return f"ROUNDUP(MONTH({args[0]}) / 3, 0)"
        if fn == "DATEDIFF":
            if len(args) != 3:
                raise TranspileFormulaError("DATEDIFF expects 3 arguments")
            return f"DATEDIFF({args[1]}, {args[2]}, {self._dax_interval(node)})"
        if fn == "DATEADD":
            if len(args) != 3:
                raise TranspileFormulaError("DATEADD expects 3 arguments")
            part = self._dax_interval(node)
            if part == "MONTH":
                return f"EDATE({args[2]}, {args[1]})"
            return f"DATEADD({args[2]}, {args[1]}, {part})"
        if fn == "DATETRUNC":
            if len(args) != 2:
                raise TranspileFormulaError("DATETRUNC expects 2 arguments")
            part = self._dax_interval(node)
            date = args[1]
            if part == "MONTH":
                return f"DATE(YEAR({date}), MONTH({date}), 1)"
            if part == "YEAR":
                return f"DATE(YEAR({date}), 1, 1)"
            if part == "DAY":
                return date
            return f"DATEADD({date}, 0, {part})"
        if fn == "MAKEDATE":
            if len(args) != 3:
                raise TranspileFormulaError("MAKEDATE expects 3 arguments")
            return f"DATE({args[0]}, {args[1]}, {args[2]})"
        if fn == "DATEPART":
            if len(args) != 2:
                raise TranspileFormulaError("DATEPART expects 2 arguments")
            return f"{self._dax_interval(node)}({args[1]})"
        return None

    def _like(
        self,
        args: list[str],
        *,
        prefix: str,
        suffix: str,
        wrap_both: bool,
        fn: str,
    ) -> str:
        if len(args) != 2:
            raise TranspileFormulaError(f"{fn} expects 2 arguments")
        target, pattern = args
        op = like_op(self.dialect)
        if pattern.startswith("'") and pattern.endswith("'") and len(pattern) >= 2:
            inner = pattern[1:-1].replace("''", "'")
            if wrap_both:
                lit = f"%{inner}%"
            else:
                lit = f"{prefix}{inner}{suffix}"
            return f"{target} {op} {sql_string(lit)}"
        if wrap_both:
            return f"{target} {op} '%' || {pattern} || '%'"
        if prefix and not suffix:
            return f"{target} {op} '%' || {pattern}"
        if suffix and not prefix:
            return f"{target} {op} {pattern} || '%'"
        return f"{target} {op} {pattern}"

    def _dim_name(self, node: Node) -> str:
        if isinstance(node, FieldRef):
            return node.name
        if isinstance(node, Paren):
            return self._dim_name(node.expr)
        emitted = self.emit_sql(node)
        return emitted.strip().strip('"')

    def _lod_grain(self, lod: LodExpr) -> list[str]:
        dim_names = [self._dim_name(d) for d in lod.dims]
        kind = lod.kind.upper()
        if kind == "FIXED":
            return dim_names
        if self.ctx.view_dims is None:
            raise IncludeExcludeGrainError()
        view = list(self.ctx.view_dims)
        if kind == "INCLUDE":
            grain = list(view)
            for name in dim_names:
                if name not in grain:
                    grain.append(name)
            return grain
        if kind == "EXCLUDE":
            excl = set(dim_names)
            return [name for name in view if name not in excl]
        return dim_names

    def _wrap_aggs(self, node: Node, over: str) -> str:
        if isinstance(node, Call) and node.name.upper() in AGG_FUNCS:
            inner = self._sql_call(node)
            return f"{inner} {over}"
        if isinstance(node, LodExpr):
            return self._sql_lod(node)
        if isinstance(node, Paren):
            return f"({self._wrap_aggs(node.expr, over)})"
        if isinstance(node, UnaryOp):
            inner = self._wrap_aggs(node.expr, over)
            if node.op == "NOT":
                return f"NOT {inner}"
            return f"{node.op}{inner}"
        if isinstance(node, BinaryOp):
            return f"{self._wrap_aggs(node.left, over)} {node.op} {self._wrap_aggs(node.right, over)}"
        if isinstance(node, IfExpr):
            parts = ["CASE"]
            for c, t in node.branches:
                parts.append(f"WHEN {self.emit_sql(c)} THEN {self._wrap_aggs(t, over)}")
            if node.else_expr is not None:
                parts.append(f"ELSE {self._wrap_aggs(node.else_expr, over)}")
            parts.append("END")
            return " ".join(parts)
        if isinstance(node, CaseExpr):
            parts = ["CASE"]
            if node.expr is not None:
                parts.append(self.emit_sql(node.expr))
            for c, t in node.branches:
                parts.append(f"WHEN {self.emit_sql(c)} THEN {self._wrap_aggs(t, over)}")
            if node.else_expr is not None:
                parts.append(f"ELSE {self._wrap_aggs(node.else_expr, over)}")
            parts.append("END")
            return " ".join(parts)
        return self.emit_sql(node)

    def _unwrap(self, node: Node) -> Node:
        while isinstance(node, Paren):
            node = node.expr
        return node

    def _sql_lod(self, lod: LodExpr) -> str:
        grain = self._lod_grain(lod)
        grain_sql = [quote_ident(g, self.dialect) for g in grain]
        partition = f"PARTITION BY {', '.join(grain_sql)}" if grain_sql else ""
        over = f"OVER ({partition})" if partition else "OVER ()"
        nested = _has_lod(lod.agg)
        countd = _has_countd(lod.agg)
        if nested:
            self.lod_cte = True
            return self._nested_lod_subquery(lod, grain)
        if countd:
            self.lod_cte = True
            return self._lod_subquery(lod, grain_sql)
        return self._wrap_aggs(lod.agg, over)

    def _grouped_agg_over_lod(
        self,
        outer_fn: str,
        lod: LodExpr,
        *,
        correlate_dims: list[str] | None = None,
    ) -> str:
        """GROUP BY LOD grain, then apply outer agg — one value per group, not row-weighted.

        Nested FIXED example: ``{FIXED [Region]: AVG({FIXED [Customer Name]: SUM([Sales])})}``
        groups the inner FIXED by Customer Name (all-region totals), restricts to customers
        present in the outer Region via IN-select, then AVG of those customer totals.
        Matches DAX ``AVERAGEX(VALUES(Customer Name), ...)`` — not Tableau row-weighted AVG.
        """

        table = quote_table_ref(self.ctx.source_table or "Orders", self.dialect)
        alias = "_lod"
        kind = lod.kind.upper()
        own_dims = [self._dim_name(d) for d in lod.dims]
        if kind == "FIXED":
            group_dims = own_dims
            if correlate_dims is None:
                correlate_dims = list(own_dims)
        else:
            group_dims = self._lod_grain(lod)
            if correlate_dims is None:
                correlate_dims = list(self.ctx.view_dims or [])
        extra_outer = [d for d in correlate_dims if d not in group_dims]
        eq_corr = [d for d in correlate_dims if d in group_dims]
        group_sql = [quote_ident(g, self.dialect) for g in group_dims]
        inner_agg = self.emit_sql(lod.agg)
        if kind == "FIXED" and not group_dims:
            return f"(SELECT {inner_agg} FROM {table})"
        where_parts: list[str] = []
        if eq_corr:
            corr_sql = [quote_ident(g, self.dialect) for g in eq_corr]
            where_parts.append(" AND ".join(f"{alias}.{col} = {table}.{col}" for col in corr_sql))
        if extra_outer and group_dims:
            g_alias = "_g"
            extra_sql = [quote_ident(g, self.dialect) for g in extra_outer]
            g_preds = " AND ".join(f"{g_alias}.{col} = {table}.{col}" for col in extra_sql)
            in_left = ", ".join(f"{alias}.{col}" for col in group_sql)
            in_right = ", ".join(f"{g_alias}.{col}" for col in group_sql)
            left_expr = f"({in_left})" if len(group_sql) > 1 else in_left
            right_expr = in_right
            where_parts.append(
                f"{left_expr} IN (SELECT {right_expr} FROM {table} AS {g_alias} WHERE {g_preds})"
            )
        where = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        group = ""
        if group_sql:
            group = " GROUP BY " + ", ".join(f"{alias}.{col}" for col in group_sql)
        if outer_fn == "COUNTD":
            outer_expr = "COUNT(DISTINCT lod_inner)"
        elif outer_fn == "ATTR":
            outer_expr = "MIN(lod_inner)"
        else:
            outer_expr = f"{outer_fn}(lod_inner)"
        inner_select = f"SELECT {inner_agg} AS lod_inner FROM {table} AS {alias}{where}{group}"
        return f"(SELECT {outer_expr} FROM ({inner_select}) AS _lod_n)"

    def _nested_lod_subquery(self, lod: LodExpr, outer_grain: list[str]) -> str:
        agg = lod.agg
        if isinstance(agg, Call) and agg.args:
            inner = self._unwrap(agg.args[0])
            if isinstance(inner, LodExpr):
                return self._grouped_agg_over_lod(
                    agg.name.upper(), inner, correlate_dims=outer_grain
                )
        grain_sql = [quote_ident(g, self.dialect) for g in outer_grain]
        return self._lod_subquery(lod, grain_sql)

    def _dax_summarize(self, dims: list[str]) -> str:
        table = self._dax_table()
        if not dims:
            return table
        if len(dims) == 1:
            return f"VALUES({self._dax_col(dims[0])})"
        cols = ", ".join(self._dax_col(d) for d in dims)
        return f"SUMMARIZE({table}, {cols})"

    def _dax_agg_expr(self, node: Node) -> str:
        if isinstance(node, Call) and node.name.upper() == "COUNTD" and node.args:
            return f"DISTINCTCOUNT({self.emit_dax(node.args[0])})"
        return self.emit_dax(node)

    def _dax_agg_over_lod(self, outer_fn: str, lod: LodExpr) -> str:
        kind = lod.kind.upper()
        inner = self._dax_agg_expr(lod.agg)
        table = self._dax_table()
        dim_names = [self._dim_name(d) for d in lod.dims]
        if kind == "FIXED":
            if not dim_names:
                return f"CALCULATE({inner}, REMOVEFILTERS({table}))"
            excepts = ", ".join(self._dax_col(d) for d in dim_names)
            calc = f"CALCULATE({inner}, ALLEXCEPT({table}, {excepts}))"
            if outer_fn in {"AVG", "MIN", "MAX", "COUNT"}:
                iter_fn = DAX_ITER.get(outer_fn, f"{outer_fn}X")
                table_expr = self._dax_summarize(dim_names)
                return f"{iter_fn}({table_expr}, {calc})"
            return calc
        if kind == "EXCLUDE":
            if not dim_names:
                return inner
            rf = ", ".join(self._dax_col(d) for d in dim_names)
            return f"CALCULATE({inner}, REMOVEFILTERS({rf}))"
        grain = self._lod_grain(lod)
        iter_fn = DAX_ITER.get(outer_fn, f"{outer_fn}X")
        table_expr = self._dax_summarize(grain)
        return f"{iter_fn}({table_expr}, CALCULATE({inner}))"

    def _dax_lod(self, lod: LodExpr) -> str:
        table = self._dax_table()
        kind = lod.kind.upper()
        agg = lod.agg
        if isinstance(agg, Call) and agg.args and isinstance(self._unwrap(agg.args[0]), LodExpr):
            inner_lod = self._unwrap(agg.args[0])
            body = self._dax_agg_over_lod(agg.name.upper(), inner_lod)
            if kind == "FIXED":
                dims = [self._dim_name(d) for d in lod.dims]
                if not dims:
                    return f"CALCULATE({body}, REMOVEFILTERS({table}))"
                excepts = ", ".join(self._dax_col(d) for d in dims)
                return f"CALCULATE({body}, ALLEXCEPT({table}, {excepts}))"
            return body
        inner = self._dax_agg_expr(lod.agg)
        dim_names = [self._dim_name(d) for d in lod.dims]
        if kind == "FIXED":
            if not dim_names:
                return f"CALCULATE({inner}, REMOVEFILTERS({table}))"
            excepts = ", ".join(self._dax_col(d) for d in dim_names)
            return f"CALCULATE({inner}, ALLEXCEPT({table}, {excepts}))"
        if kind == "INCLUDE":
            grain = self._lod_grain(lod)
            if not grain:
                return f"CALCULATE({inner}, REMOVEFILTERS({table}))"
            excepts = ", ".join(self._dax_col(d) for d in grain)
            return f"CALCULATE({inner}, ALLEXCEPT({table}, {excepts}))"
        if kind == "EXCLUDE":
            if not dim_names:
                return inner
            rf = ", ".join(self._dax_col(d) for d in dim_names)
            return f"CALCULATE({inner}, REMOVEFILTERS({rf}))"
        return inner

    def _lod_subquery(self, lod: LodExpr, grain_sql: list[str]) -> str:
        table = quote_table_ref(self.ctx.source_table or "Orders", self.dialect)
        alias = "_lod"
        agg_sql = self._sql_call(lod.agg) if isinstance(lod.agg, Call) else self.emit_sql(lod.agg)
        if grain_sql:
            outer = quote_table_ref(self.ctx.source_table or "Orders", self.dialect)
            preds = " AND ".join(f"{alias}.{col} = {outer}.{col}" for col in grain_sql)
            return f"(SELECT {agg_sql} FROM {table} AS {alias} WHERE {preds})"
        return f"(SELECT {agg_sql} FROM {table} AS {alias})"


def parse_formula(formula: str, *, dialect: str = "ansi") -> Node:
    tokens = tokenize(formula)
    return Parser(tokens, dialect=dialect).parse()


def _todo_sql(formula: str, message: str) -> str:
    return f"-- TODO: {message}: {formula}"


def _todo_dax(formula: str, message: str) -> str:
    return f"// TODO: {message}: {formula}"


def transpile_tree(
    node: Node,
    *,
    ctx: FormulaContext,
) -> tuple[str, ParseStatus, str | None, Emitter]:
    emitter = Emitter(ctx)
    try:
        text = emitter.emit(node)
        if ctx.target == "sql" and is_bool_sql_node(node):
            text = wrap_bool_sql(text, ctx.dialect)
        if emitter.partial:
            return (
                text,
                ParseStatus.unparseable,
                "Unmapped Tableau function: " + ", ".join(emitter.unknown_functions),
                emitter,
            )
        return text, ParseStatus.ok, None, emitter
    except IncludeExcludeGrainError as exc:
        raw = ctx.original_formula
        if ctx.target == "dax":
            return _todo_dax(raw, "Review LOD logic"), ParseStatus.unparseable, exc.message, emitter
        return _todo_sql(raw, "Review LOD logic"), ParseStatus.unparseable, exc.message, emitter
    except LodFallback as exc:
        raw = exc.raw.strip() or ctx.original_formula
        if ctx.target == "dax":
            return _todo_dax(raw, "Review LOD logic"), ParseStatus.unparseable, exc.reason, emitter
        return _todo_sql(raw, "Review LOD logic"), ParseStatus.unparseable, exc.reason, emitter
    except TranspileFormulaError as exc:
        raw = ctx.original_formula
        if ctx.target == "dax":
            return _todo_dax(raw, f"Failed to transpile ({exc.message})"), ParseStatus.error, exc.message, emitter
        return _todo_sql(raw, f"Failed to transpile ({exc.message})"), ParseStatus.error, exc.message, emitter


def transpile_formula(
    formula: str,
    *,
    dialect: str = "ansi",
    target: str = "sql",
    context: FormulaContext | None = None,
) -> tuple[str, ParseStatus, str | None]:
    text = formula.strip()
    if not text:
        empty = "-- empty formula" if target != "dax" else "// empty formula"
        return empty, ParseStatus.error, "Empty formula"
    dialect_l = dialect.lower()
    target_l = "dax" if dialect_l == "dax" or target.lower() == "dax" else "sql"
    ctx = context or FormulaContext(dialect=dialect_l if dialect_l != "dax" else "ansi", target=target_l)
    ctx.target = target_l
    ctx.original_formula = text
    if dialect_l != "dax":
        ctx.dialect = dialect_l
    try:
        tree = parse_formula(text, dialect=ctx.dialect)
    except TranspileFormulaError as exc:
        if target_l == "dax":
            return _todo_dax(text, f"Failed to transpile ({exc.message})"), ParseStatus.error, exc.message
        return _todo_sql(text, f"Failed to transpile ({exc.message})"), ParseStatus.error, exc.message
    except Exception as exc:  # pragma: no cover
        if target_l == "dax":
            return _todo_dax(text, f"Failed to transpile ({exc})"), ParseStatus.error, str(exc)
        return _todo_sql(text, f"Failed to transpile ({exc})"), ParseStatus.error, str(exc)
    out, status, err, _emitter = transpile_tree(tree, ctx=ctx)
    return out, status, err


def view_dims_for_calc(
    raw_name: str,
    caption: str,
    worksheets: list[WorksheetInfo],
) -> list[str] | None:
    keys = {raw_name.strip("[]"), caption, raw_name}
    matching = [
        ws
        for ws in worksheets
        if any(used in keys or used.strip("[]") in keys for used in ws.used_fields)
    ]
    if not matching:
        named = [ws for ws in worksheets if ws.name.lower() in {"sheet 1", "sheet1"}]
        if named:
            return list(named[0].dimensions)
        return None
    matching.sort(key=lambda ws: (ws.name.lower() != "sheet 1", ws.name))
    return list(matching[0].dimensions)
