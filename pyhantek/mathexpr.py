"""Safe math-channel expressions such as ``ch1 - ch2`` or ``lowpass(diff(ch1), 10e3)``.

Expressions are parsed with :mod:`ast` and evaluated by walking the tree, so
only whitelisted names, numbers, operators and functions are allowed.
"""

from __future__ import annotations

import ast
import math
import operator
from dataclasses import dataclass

import numpy as np

# (label shown in the UI, expression, unit)
PRESETS: list[tuple[str, str, str]] = [
    ("CH1 + CH2", "ch1 + ch2", "V"),
    ("CH1 − CH2", "ch1 - ch2", "V"),
    ("CH2 − CH1", "ch2 - ch1", "V"),
    ("CH1 × CH2 (power)", "ch1 * ch2", "V²"),
    ("CH1 ÷ CH2", "ch1 / ch2", ""),
    ("d/dt CH1", "diff(ch1)", "V/s"),
    ("d/dt CH1 (smoothed)", "diff(lowpass(ch1, fs / 50))", "V/s"),
    ("∫ CH1 dt", "integ(ch1)", "V·s"),
    ("|CH1|", "abs(ch1)", "V"),
    ("CH1 low-pass 10 kHz", "lowpass(ch1, 10e3)", "V"),
    ("CH1 high-pass 100 Hz", "highpass(ch1, 100)", "V"),
    ("CH1 moving average 10", "avg(ch1, 10)", "V"),
    ("CH1 > CH2 (logic)", "ch1 > ch2", ""),
    ("Vector magnitude", "sqrt(ch1**2 + ch2**2)", "V"),
]

FUNCTIONS_HELP = (
    "Variables: ch1, ch2 (volts), t (seconds from the trigger), fs (sample rate), pi, e\n"
    "Operators: + - * / ** %, comparisons (< > <= >= == !=) give 0/1, & | for logic\n"
    "Functions: abs sqrt exp log log10 sin cos tan sign floor ceil, min(a,b) max(a,b),\n"
    "clip(x,lo,hi), diff(x), integ(x), avg(x,n), lowpass(x,fc), highpass(x,fc),\n"
    "mean(x), rms(x), dc(x) (removes the mean)"
)


class MathError(ValueError):
    pass


def _arr(x):
    return np.asarray(x, dtype=np.float64)


def _diff(x, fs):
    x = _arr(x)
    return np.gradient(x) * fs if x.ndim and len(x) > 1 else x * 0


def _integ(x, fs):
    x = _arr(x)
    if not x.ndim:
        return x
    x = np.nan_to_num(x)
    return np.concatenate(([0.0], np.cumsum((x[1:] + x[:-1]) / 2))) / fs


def _avg(x, n):
    x = _arr(x)
    n = max(1, int(n))
    if not x.ndim or n == 1:
        return x
    k = np.ones(n) / n
    return np.convolve(np.nan_to_num(x), k, mode="same")


def _lowpass(x, fc, fs):
    """Zero-phase low-pass with the response of a single-pole RC filter run forwards and backwards."""
    x = _arr(x)
    if not x.ndim or fc <= 0 or len(x) < 4:
        return x
    x = np.nan_to_num(x)
    # mirror-extend to suppress wrap-around at the edges
    ext = np.concatenate((x[::-1], x, x[::-1]))
    f = np.fft.rfftfreq(len(ext), 1.0 / fs)
    h = 1.0 / (1.0 + (f / float(fc)) ** 2)
    y = np.fft.irfft(np.fft.rfft(ext) * h, len(ext))
    return y[len(x) : 2 * len(x)]


FUNCS = {
    "abs": lambda x: np.abs(_arr(x)),
    "sqrt": lambda x: np.sqrt(np.abs(_arr(x))),
    "exp": lambda x: np.exp(np.clip(_arr(x), -700, 700)),
    "log": lambda x: np.log(np.maximum(np.abs(_arr(x)), 1e-300)),
    "log10": lambda x: np.log10(np.maximum(np.abs(_arr(x)), 1e-300)),
    "sin": lambda x: np.sin(_arr(x)),
    "cos": lambda x: np.cos(_arr(x)),
    "tan": lambda x: np.tan(_arr(x)),
    "sign": lambda x: np.sign(_arr(x)),
    "floor": lambda x: np.floor(_arr(x)),
    "ceil": lambda x: np.ceil(_arr(x)),
    "min": lambda a, b: np.minimum(_arr(a), _arr(b)),
    "max": lambda a, b: np.maximum(_arr(a), _arr(b)),
    "clip": lambda x, lo, hi: np.clip(_arr(x), lo, hi),
    "mean": lambda x: np.full_like(_arr(x), np.nanmean(_arr(x))),
    "rms": lambda x: np.full_like(_arr(x), np.sqrt(np.nanmean(_arr(x) ** 2))),
    "dc": lambda x: _arr(x) - np.nanmean(_arr(x)),
}
NEEDS_FS = {"diff": _diff, "integ": _integ, "lowpass": _lowpass, "highpass": None}
ARITY = {"min": 2, "max": 2, "clip": 3, "avg": 2, "lowpass": 2, "highpass": 2}

BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: lambda a, b: np.divide(a, b, out=np.full(np.broadcast(a, b).shape, np.nan), where=_arr(b) != 0)
    if np.ndim(a) or np.ndim(b)
    else (a / b if b else math.nan),
    ast.Pow: lambda a, b: np.power(np.abs(a), b) * (np.sign(a) if float(b).is_integer() and int(b) % 2 else 1)
    if np.ndim(b) == 0
    else np.power(a, b),
    ast.Mod: np.mod,
    ast.BitAnd: lambda a, b: ((_arr(a) != 0) & (_arr(b) != 0)).astype(float),
    ast.BitOr: lambda a, b: ((_arr(a) != 0) | (_arr(b) != 0)).astype(float),
    ast.BitXor: lambda a, b: ((_arr(a) != 0) ^ (_arr(b) != 0)).astype(float),
}
CMPOPS = {
    ast.Lt: operator.lt,
    ast.Gt: operator.gt,
    ast.LtE: operator.le,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}
CONSTS = {"pi": math.pi, "e": math.e}


@dataclass
class Compiled:
    expr: str
    tree: ast.AST
    uses: set[str]


def compile_expr(expr: str) -> Compiled:
    """Parse and validate an expression. Raises MathError with a readable message."""
    text = expr.strip()
    if not text:
        raise MathError("empty expression")
    if len(text) > 300:
        raise MathError("expression too long")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise MathError(f"syntax error: {exc.msg}") from None
    uses: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load, ast.operator, ast.unaryop, ast.cmpop)):
            continue
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
                raise MathError("only numbers are allowed as constants")
        elif isinstance(node, ast.Name):
            if node.id in ("ch1", "ch2", "t", "fs"):
                uses.add(node.id)
            elif node.id not in CONSTS and node.id not in FUNCS and node.id not in NEEDS_FS and node.id != "avg":
                raise MathError(f"unknown name '{node.id}'")
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.keywords:
                raise MathError("only simple function calls are allowed")
            name = node.func.id
            if name not in FUNCS and name not in NEEDS_FS and name != "avg":
                raise MathError(f"unknown function '{name}'")
            want = ARITY.get(name, 1)
            if len(node.args) != want:
                raise MathError(f"{name}() takes {want} argument{'s' if want > 1 else ''}")
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in BINOPS:
                raise MathError("operator not allowed")
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.USub, ast.UAdd, ast.Not, ast.Invert)):
                raise MathError("operator not allowed")
        elif isinstance(node, ast.Compare):
            if any(type(op) not in CMPOPS for op in node.ops):
                raise MathError("comparison not allowed")
        elif isinstance(node, ast.IfExp):
            pass
        else:
            raise MathError(f"'{type(node).__name__}' is not allowed")
    return Compiled(text, tree, uses)


def evaluate(compiled: Compiled | str, ch1, ch2, fs: float, t=None) -> np.ndarray:
    """Evaluate for arrays ch1/ch2 (None if unavailable). Returns an array the length of the inputs."""
    c = compile_expr(compiled) if isinstance(compiled, str) else compiled
    n = None
    for a in (ch1, ch2):
        if a is not None:
            n = len(a) if n is None else min(n, len(a))
    if n is None:
        raise MathError("no channel data")
    env = {"fs": float(fs), **CONSTS}
    if "ch1" in c.uses:
        if ch1 is None:
            raise MathError("CH1 is not available")
        env["ch1"] = _arr(ch1)[:n]
    if "ch2" in c.uses:
        if ch2 is None:
            raise MathError("CH2 is not available")
        env["ch2"] = _arr(ch2)[:n]
    env["t"] = _arr(t)[:n] if t is not None else np.arange(n) / fs
    with np.errstate(all="ignore"):
        out = _eval(c.tree.body, env)
    out = _arr(out)
    if out.ndim == 0:
        out = np.full(n, float(out))
    return out


def _eval(node, env):
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.BinOp):
        return BINOPS[type(node.op)](_eval(node.left, env), _eval(node.right, env))
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env)
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, (ast.Not, ast.Invert)):
            return (_arr(v) == 0).astype(float)
        return v
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        result = None
        for op, right_node in zip(node.ops, node.comparators):
            right = _eval(right_node, env)
            r = CMPOPS[type(op)](left, right)
            result = r if result is None else (result & r)
            left = right
        return _arr(result).astype(float)
    if isinstance(node, ast.IfExp):
        cond = _arr(_eval(node.test, env)) != 0
        return np.where(cond, _eval(node.body, env), _eval(node.orelse, env))
    if isinstance(node, ast.Call):
        name = node.func.id
        args = [_eval(a, env) for a in node.args]
        fs = env["fs"]
        if name == "diff":
            return _diff(args[0], fs)
        if name == "integ":
            return _integ(args[0], fs)
        if name == "lowpass":
            return _lowpass(args[0], float(np.mean(args[1])), fs)
        if name == "highpass":
            x = _arr(args[0])
            return x - _lowpass(x, float(np.mean(args[1])), fs)
        if name == "avg":
            return _avg(args[0], float(np.mean(args[1])))
        return FUNCS[name](*args)
    raise MathError("unsupported expression")


def unit_for(expr: str) -> str:
    for _, e, unit in PRESETS:
        if e.replace(" ", "") == expr.replace(" ", ""):
            return unit
    return "V" if ("ch1" in expr or "ch2" in expr) and not any(op in expr for op in "*/") else ""


def label_for(expr: str) -> str:
    for label, e, _ in PRESETS:
        if e.replace(" ", "") == expr.replace(" ", ""):
            return label
    return expr
