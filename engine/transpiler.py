"""Tableau formula tokenizer and recursive-descent transpiler to ANSI SQL."""

from __future__ import annotations

import re
from dataclasses import dataclass

from engine.schema import ParseStatus

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
}

AGG_FUNCS = {"SUM", "AVG", "COUNT", "MIN", "MAX"}
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

ILIKE_DIALECTS = {"snowflake", "postgres", "duckdb"}


@dataclass
class Token:
    kind: str
    value: str
    pos: int


class TranspileFormulaError(Exception):
    def __init__(self, message: str, pos: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.pos = pos


def quote_ident(name: str, dialect: str = "ansi") -> str:
    cleaned = name.strip()
    if dialect == "snowflake":
        escaped = cleaned.replace('"', '""')
        return f'"{escaped}"'
    escaped = cleaned.replace('"', '""')
    return f'"{escaped}"'


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def like_op(dialect: str) -> str:
    if dialect.lower() in ILIKE_DIALECTS:
        return "ILIKE"
    return "LIKE"


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
            start = i
            depth = 1
            i += 1
            while i < n and depth:
                if formula[i] == "{":
                    depth += 1
                elif formula[i] == "}":
                    depth -= 1
                i += 1
            tokens.append(Token("LOD", formula[start:i], start))
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
        if ch in "+-*/=<>":
            tokens.append(Token("OP", ch, i))
            i += 1
            continue
        tokens.append(Token("OP", ch, i))
        i += 1
    tokens.append(Token("EOF", "", n))
    return tokens


class Parser:
    def __init__(self, tokens: list[Token], dialect: str = "ansi") -> None:
        self.tokens = tokens
        self.i = 0
        self.dialect = dialect.lower()
        self.unknown_functions: list[str] = []
        self.partial = False

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

    def parse(self) -> str:
        if any(t.kind == "LOD" for t in self.tokens):
            raw = "".join(t.value for t in self.tokens if t.kind != "EOF")
            lod = next(t.value for t in self.tokens if t.kind == "LOD")
            raise LodFallback(lod or raw)
        expr = self.parse_or()
        if self.peek().kind not in {"EOF"}:
            leftover = self.peek()
            if leftover.kind == "KEYWORD" and leftover.value in {"THEN", "ELSEIF", "ELSE", "END"}:
                raise TranspileFormulaError(f"Unexpected {leftover.value}", leftover.pos)
            raise TranspileFormulaError(
                f"Unexpected token {leftover.kind} {leftover.value!r}", leftover.pos
            )
        return expr

    def parse_or(self) -> str:
        left = self.parse_and()
        while self.match("KEYWORD", "OR"):
            right = self.parse_and()
            left = f"{left} OR {right}"
        return left

    def parse_and(self) -> str:
        left = self.parse_not()
        while self.match("KEYWORD", "AND"):
            right = self.parse_not()
            left = f"{left} AND {right}"
        return left

    def parse_not(self) -> str:
        if self.match("KEYWORD", "NOT"):
            return f"NOT {self.parse_not()}"
        return self.parse_comparison()

    def parse_comparison(self) -> str:
        left = self.parse_add()
        tok = self.peek()
        if tok.kind == "OP" and tok.value in {"=", "<>", "!=", "<", ">", "<=", ">="}:
            op = self.advance().value
            if op == "!=":
                op = "<>"
            right = self.parse_add()
            return f"{left} {op} {right}"
        return left

    def parse_add(self) -> str:
        left = self.parse_mul()
        while self.peek().kind == "OP" and self.peek().value in {"+", "-"}:
            op = self.advance().value
            right = self.parse_mul()
            left = f"{left} {op} {right}"
        return left

    def parse_mul(self) -> str:
        left = self.parse_unary()
        while self.peek().kind == "OP" and self.peek().value in {"*", "/"}:
            op = self.advance().value
            right = self.parse_unary()
            left = f"{left} {op} {right}"
        return left

    def parse_unary(self) -> str:
        if self.peek().kind == "OP" and self.peek().value == "-":
            self.advance()
            return f"-{self.parse_unary()}"
        if self.peek().kind == "OP" and self.peek().value == "+":
            self.advance()
            return self.parse_unary()
        return self.parse_primary()

    def parse_primary(self) -> str:
        tok = self.peek()
        if tok.kind == "KEYWORD" and tok.value == "IF":
            return self.parse_if()
        if tok.kind == "KEYWORD" and tok.value in {"TRUE", "FALSE"}:
            self.advance()
            return "TRUE" if tok.value == "TRUE" else "FALSE"
        if tok.kind == "NUMBER":
            return self.advance().value
        if tok.kind == "STRING":
            return sql_string(self.advance().value)
        if tok.kind == "FIELD":
            return quote_ident(self.advance().value, self.dialect)
        if tok.kind == "IDENT":
            name = self.advance().value
            if self.peek().kind == "LPAREN":
                return self.parse_call(name)
            return quote_ident(name, self.dialect)
        if tok.kind == "LPAREN":
            self.advance()
            expr = self.parse_or()
            self.expect("RPAREN")
            return f"({expr})"
        if tok.kind == "LOD":
            raise LodFallback(tok.value)
        raise TranspileFormulaError(f"Unexpected token {tok.kind} {tok.value!r}", tok.pos)

    def parse_if(self) -> str:
        self.expect("KEYWORD", "IF")
        branches: list[tuple[str, str]] = []
        cond = self.parse_or()
        self.expect("KEYWORD", "THEN")
        then_expr = self.parse_or()
        branches.append((cond, then_expr))
        else_expr: str | None = None
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
        parts = ["CASE"]
        for c, t in branches:
            parts.append(f"WHEN {c} THEN {t}")
        if else_expr is not None:
            parts.append(f"ELSE {else_expr}")
        parts.append("END")
        return " ".join(parts)

    def parse_args(self) -> list[str]:
        self.expect("LPAREN")
        args: list[str] = []
        if self.peek().kind != "RPAREN":
            args.append(self.parse_or())
            while self.match("COMMA"):
                args.append(self.parse_or())
        self.expect("RPAREN")
        return args

    def parse_call(self, name: str) -> str:
        fn = name.upper()
        args = self.parse_args()
        return self.map_function(fn, name, args)

    def map_function(self, fn: str, original: str, args: list[str]) -> str:
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
        if fn in AGG_FUNCS or fn in PASSTHROUGH:
            return f"{fn}({', '.join(args)})"
        if fn in {"TODAY"}:
            return "CURRENT_DATE"
        if fn in {"NOW"}:
            return "CURRENT_TIMESTAMP"
        if any(fn.startswith(p) or fn == p.rstrip("_") for p in TABLE_CALC_PREFIXES):
            call = f"{original}({', '.join(args)})"
            self.partial = True
            self.unknown_functions.append(fn)
            return f"/* TODO: Tableau table calc {fn}: {call} */ NULL"
        call = f"{original}({', '.join(args)})"
        self.partial = True
        self.unknown_functions.append(fn)
        return f"/* TODO: unmapped Tableau function {fn}: {call} */ {call}"

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
        raw_tokens_pattern = pattern
        if raw_tokens_pattern.startswith("'") and raw_tokens_pattern.endswith("'") and len(raw_tokens_pattern) >= 2:
            inner = raw_tokens_pattern[1:-1].replace("''", "'")
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


class LodFallback(Exception):
    def __init__(self, raw: str) -> None:
        super().__init__(raw)
        self.raw = raw


def transpile_formula(formula: str, *, dialect: str = "ansi") -> tuple[str, ParseStatus, str | None]:
    text = formula.strip()
    if not text:
        return "-- empty formula", ParseStatus.error, "Empty formula"
    try:
        tokens = tokenize(text)
        parser = Parser(tokens, dialect=dialect)
        sql = parser.parse()
        if parser.partial:
            return sql, ParseStatus.unparseable, (
                "Unmapped Tableau function: " + ", ".join(parser.unknown_functions)
            )
        return sql, ParseStatus.ok, None
    except LodFallback as exc:
        raw = exc.raw.strip()
        return f"-- TODO: Review LOD logic: {raw}", ParseStatus.unparseable, "LOD expression"
    except TranspileFormulaError as exc:
        return f"-- TODO: Failed to transpile: {exc.message}", ParseStatus.error, exc.message
    except Exception as exc:  # pragma: no cover - defensive
        return f"-- TODO: Failed to transpile: {exc}", ParseStatus.error, str(exc)
