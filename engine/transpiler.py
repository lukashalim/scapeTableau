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
    "CASE",
    "WHEN",
}

AGG_FUNCS = {"SUM", "AVG", "COUNT", "MIN", "MAX", "COUNTD", "ATTR"}
PASSTHROUGH = {"TRIM", "UPPER", "LOWER", "ABS", "ROUND"}
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
SQL_DIALECTS = {"ansi", "snowflake", "postgres", "duckdb", "bigquery"}

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
    view_dims: list[str] | None = None
    source_table: str = "Orders"
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
    escaped = cleaned.replace('"', '""')
    return f'"{escaped}"'


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def dax_string(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def like_op(dialect: str) -> str:
    if dialect.lower() in ILIKE_DIALECTS:
        return "ILIKE"
    return "LIKE"


def normalize_table_name(raw: str | None) -> str:
    if not raw:
        return ""
    table = raw.strip()
    if table.startswith("[") and table.endswith("]"):
        table = table[1:-1]
    if table.endswith("$"):
        table = table[:-1]
    return table


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
        if tok.kind == "NUMBER":
            return Literal("number", self.advance().value)
        if tok.kind == "STRING":
            return Literal("string", self.advance().value)
        if tok.kind == "FIELD":
            return self.parse_field()
        if tok.kind == "IDENT":
            name = self.advance().value
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
    if dialect in {"postgres", "duckdb"}:
        return f"{{date}} + ({factor}{amount_sql}) * INTERVAL '1 {unit.lower()}'"
    return f"DATEADD('{unit.lower()}', {factor}{amount_sql}, {{date}})"


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
                return "TRUE" if node.value == "TRUE" else "FALSE"
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
            op = {"AND": "&&", "OR": "||", "<>": "<>"}.get(node.op, node.op)
            return f"{self.emit_dax(node.left)} {op} {self.emit_dax(node.right)}"
        if isinstance(node, Paren):
            return f"({self.emit_dax(node.expr)})"
        if isinstance(node, IfExpr):
            return self._dax_if(node.branches, node.else_expr)
        if isinstance(node, CaseExpr):
            return self._dax_case(node)
        if isinstance(node, LodExpr):
            raw = self.ctx.original_formula or "{LOD}"
            raise LodFallback(raw)
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

    def _dax_field(self, node: FieldRef) -> str:
        key = node.name
        if key not in self.ctx.current_keys and key in self.ctx.calc_dax:
            status = self.ctx.calc_status.get(key)
            if status is not None and status != ParseStatus.ok:
                raise TranspileFormulaError(f"Dependency failed: {key}")
            return f"({self.ctx.calc_dax[key]})"
        return f"[{node.name}]"

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
        raw = info.value if info.value not in (None, "") else (info.formula or "NULL")
        dt = (info.datatype or "").lower()
        sql: str
        dax: str
        if dt in {"string", "str"}:
            sql, dax = sql_string(str(raw).strip("'\"" )), dax_string(str(raw).strip("'\""))
        elif dt in {"boolean", "bool"}:
            flag = str(raw).strip().lower() in {"true", "1", "yes"}
            sql = "TRUE" if flag else "FALSE"
            dax = sql
        else:
            text = str(raw).strip().strip('"')
            if re.fullmatch(r"-?\d+(\.\d+)?", text):
                sql = dax = text
            elif dt in {"date", "datetime"} or (text.startswith("#") and text.endswith("#")):
                inner = text.strip("#")
                sql = sql_string(inner)
                dax = f"DATEVALUE({dax_string(inner)})"
            else:
                sql = text
                dax = text
        comment = f"/* [Parameters].[{info.caption}] */"
        return f"{sql} {comment}", f"{dax} {comment}"

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
        _, dax = self._param_literal(info)
        return dax

    def _dax_if(self, branches: list[tuple[Node, Node]], else_expr: Node | None) -> str:
        if not branches:
            return self.emit_dax(else_expr) if else_expr is not None else "BLANK()"
        cond, then = branches[0]
        rest = self._dax_if(branches[1:], else_expr)
        return f"IF({self.emit_dax(cond)}, {self.emit_dax(then)}, {rest})"

    def _dax_case(self, node: CaseExpr) -> str:
        if node.expr is None:
            branches = node.branches
        else:
            left = node.expr
            branches = [(BinaryOp("=", left, val), res) for val, res in node.branches]
        return self._dax_if(branches, node.else_expr)

    def _sql_call(self, node: Call) -> str:
        fn = node.name.upper()
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
        date_dax = self._dax_date(fn, args)
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
            if dialect == "snowflake":
                return f"{fn}({args[0]})"
            return f"EXTRACT({fn} FROM {args[0]})"
        if fn == "DATEPART":
            if len(args) != 2:
                raise TranspileFormulaError("DATEPART expects 2 arguments")
            part = _norm_part(node.args[0]) or "day"
            unit = part.upper()
            if dialect == "snowflake":
                return f"DATE_PART('{part}', {args[1]})"
            return f"EXTRACT({unit} FROM {args[1]})"
        if fn == "DATETRUNC":
            if len(args) != 2:
                raise TranspileFormulaError("DATETRUNC expects 2 arguments")
            part = _norm_part(node.args[0]) or "day"
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
            if dialect == "postgres":
                if part in {"day", "dd"}:
                    return f"({end}::date - {start}::date)"
                return f"(EXTRACT({part.upper()} FROM {end}) - EXTRACT({part.upper()} FROM {start}))"
            return f"DATEDIFF('{part}', {start}, {end})"
        if fn == "DATEADD":
            if len(args) != 3:
                raise TranspileFormulaError("DATEADD expects 3 arguments")
            part = _norm_part(node.args[0]) or "day"
            template = _interval(part, args[1], dialect)
            return template.replace("{date}", args[2])
        if fn == "MAKEDATE":
            if len(args) != 3:
                raise TranspileFormulaError("MAKEDATE expects 3 arguments")
            if dialect == "snowflake":
                return f"DATE_FROM_PARTS({args[0]}, {args[1]}, {args[2]})"
            return f"MAKE_DATE({args[0]}, {args[1]}, {args[2]})"
        return None

    def _dax_date(self, fn: str, args: list[str]) -> str | None:
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
            return f"DATEDIFF({args[1]}, {args[2]}, {args[0].strip(chr(34))})"
        if fn == "DATEADD":
            if len(args) != 3:
                raise TranspileFormulaError("DATEADD expects 3 arguments")
            return f"DATEADD({args[2]}, {args[1]}, {args[0].strip(chr(34))})"
        if fn == "DATETRUNC":
            if len(args) != 2:
                raise TranspileFormulaError("DATETRUNC expects 2 arguments")
            return f"DATEADD({args[1]}, 0, {args[0].strip(chr(34))})"
        if fn == "MAKEDATE":
            if len(args) != 3:
                raise TranspileFormulaError("MAKEDATE expects 3 arguments")
            return f"DATE({args[0]}, {args[1]}, {args[2]})"
        if fn == "DATEPART":
            if len(args) != 2:
                raise TranspileFormulaError("DATEPART expects 2 arguments")
            return f"DATEPART({args[1]}, {args[0]})"
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
            return self._nested_lod_subquery(lod, grain_sql)
        if countd:
            self.lod_cte = True
            return self._lod_subquery(lod, grain_sql)
        return self._wrap_aggs(lod.agg, over)

    def _nested_lod_subquery(self, lod: LodExpr, grain_sql: list[str]) -> str:
        agg = lod.agg
        table = quote_ident(self.ctx.source_table or "Orders", self.dialect)
        alias = "_lod"
        if isinstance(agg, Call) and agg.args:
            inner = self._unwrap(agg.args[0])
            if isinstance(inner, LodExpr):
                inner_grain = [quote_ident(g, self.dialect) for g in self._lod_grain(inner)]
                inner_agg = self.emit_sql(inner.agg)
                where = ""
                if grain_sql:
                    outer = quote_ident(self.ctx.source_table or "Orders", self.dialect)
                    preds = " AND ".join(f"{alias}.{col} = {outer}.{col}" for col in grain_sql)
                    where = f" WHERE {preds}"
                group = ""
                if inner_grain:
                    group = " GROUP BY " + ", ".join(f"{alias}.{col}" for col in inner_grain)
                fn = agg.name.upper()
                if fn == "COUNTD":
                    outer = "COUNT(DISTINCT lod_inner)"
                elif fn == "ATTR":
                    outer = "MIN(lod_inner)"
                else:
                    outer = f"{fn}(lod_inner)"
                inner_select = f"SELECT {inner_agg} AS lod_inner FROM {table} AS {alias}{where}{group}"
                return f"(SELECT {outer} FROM ({inner_select}) AS _lod_n)"
        return self._lod_subquery(lod, grain_sql)

    def _lod_subquery(self, lod: LodExpr, grain_sql: list[str]) -> str:
        table = quote_ident(self.ctx.source_table or "Orders", self.dialect)
        alias = "_lod"
        agg_sql = self._sql_call(lod.agg) if isinstance(lod.agg, Call) else self.emit_sql(lod.agg)
        if grain_sql:
            outer = quote_ident(self.ctx.source_table or "Orders", self.dialect)
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
