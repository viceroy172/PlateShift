# SPDX-License-Identifier: AGPL-3.0-or-later
"""Evaluate OrcaSlicer's compatible_printers_condition expressions.

They look like:  printer_model == "X" and nozzle_diameter[0] == 0.4
                 not (printer_notes =~ /.*AD5M.*/ or printer_variant == "0.25")

Supported: == != < > <= >=, =~ /regex/, and or not, parentheses, "strings",
numbers, setting names with an optional [index]. Anything else is an error,
which callers treat as "not compatible": better to hide a preset than to offer
one meant for a different printer.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r'''
    \s*(?:
      (?P<num>-?\d+(?:\.\d+)?)
    | "(?P<str>(?:[^"\\]|\\.)*)"
    | /(?P<re>(?:[^/\\]|\\.)*)/
    | (?P<op>==|!=|<=|>=|=~|<|>|\(|\)|\[|\])
    | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    )''', re.VERBOSE)


class ConditionError(ValueError):
    pass


def _tokens(text: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ConditionError(f"can't read condition at: {text[pos:pos + 20]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return out


def evaluate(text: str, settings: dict) -> bool:
    """True when the printer described by `settings` meets the condition"""
    if not text or not text.strip():
        return True
    tokens = _tokens(text)
    pos = 0

    def peek(value=None):
        if pos >= len(tokens):
            return None
        kind, v = tokens[pos]
        return v if value is None or v == value else None

    def take():
        nonlocal pos
        if pos >= len(tokens):
            raise ConditionError("condition ends early")
        pos += 1
        return tokens[pos - 1]

    def value():
        kind, v = take()
        if kind == "num":
            return float(v)
        if kind == "str":
            return v.replace('\\"', '"')
        if kind == "op" and v == "(":
            result = either()
            if take() != ("op", ")"):
                raise ConditionError("missing )")
            return result
        if kind == "name":
            if v in ("true", "false"):
                return v == "true"
            setting = settings.get(v)
            if peek("["):
                take()
                _, index = take()
                if take() != ("op", "]"):
                    raise ConditionError("missing ]")
                setting = setting[int(float(index))] if isinstance(setting, list) else setting
            elif isinstance(setting, list):
                setting = setting[0] if setting else ""
            return "" if setting is None else setting
        raise ConditionError(f"unexpected {v!r}")

    def compare():
        left = value()
        op = peek()
        if op in ("==", "!=", "<", ">", "<=", ">=", "=~"):
            take()
            if op == "=~":
                kind, pattern = take()
                if kind != "re":
                    raise ConditionError("=~ needs a /regex/")
                return re.search(pattern, str(left)) is not None
            right = value()
            a, b = _same_kind(left, right)
            return {"==": a == b, "!=": a != b, "<": a < b, ">": a > b,
                    "<=": a <= b, ">=": a >= b}[op]
        return bool(left)

    def negation():
        if peek("not"):
            take()
            return not negation()
        return compare()

    def both():
        result = negation()
        while peek("and"):
            take()
            result = negation() and result
        return result

    def either():
        result = both()
        while peek("or"):
            take()
            result = both() or result
        return result

    result = either()
    if pos != len(tokens):
        raise ConditionError(f"unexpected {tokens[pos][1]!r}")
    return result


def _same_kind(a, b):
    """compare numbers as numbers, everything else as text"""
    try:
        return float(a), float(b)
    except (TypeError, ValueError):
        return str(a), str(b)
