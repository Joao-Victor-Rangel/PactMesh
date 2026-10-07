"""Deterministic JSON serialization (RFC 8785 subset).

PactMesh never puts floating point numbers in signed objects: money is an
integer string in minimal units and statistics are fixed-precision decimal
strings. Floats are therefore rejected instead of being canonicalized.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_SAFE_INT = 2**53 - 1


class CanonicalError(ValueError):
    pass


def _encode(obj: Any, out: list[str]) -> None:
    if obj is None:
        out.append("null")
    elif obj is True:
        out.append("true")
    elif obj is False:
        out.append("false")
    elif isinstance(obj, int):
        if abs(obj) > MAX_SAFE_INT:
            raise CanonicalError("integer outside IEEE-754 safe range; use a string")
        out.append(str(obj))
    elif isinstance(obj, float):
        raise CanonicalError("floats are not allowed in canonical objects")
    elif isinstance(obj, str):
        out.append(json.dumps(obj, ensure_ascii=False))
    elif isinstance(obj, (list, tuple)):
        out.append("[")
        for i, item in enumerate(obj):
            if i:
                out.append(",")
            _encode(item, out)
        out.append("]")
    elif isinstance(obj, dict):
        for k in obj:
            if not isinstance(k, str):
                raise CanonicalError("object keys must be strings")
        out.append("{")
        # RFC 8785: members sorted by the UTF-16 code units of their names.
        for i, k in enumerate(sorted(obj, key=lambda s: s.encode("utf-16-be"))):
            if i:
                out.append(",")
            out.append(json.dumps(k, ensure_ascii=False))
            out.append(":")
            _encode(obj[k], out)
        out.append("}")
    else:
        raise CanonicalError(f"unsupported type {type(obj).__name__}")


def canonical(obj: Any) -> bytes:
    out: list[str] = []
    _encode(obj, out)
    return "".join(out).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_obj(obj: Any) -> str:
    return sha256_hex(canonical(obj))
