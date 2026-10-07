"""Deterministic verifier ``pactmesh.stats`` version ``1.0``.

Fixed rules (agreed before contracting):

* input: CSV with header; the requested column is parsed with ``Decimal``;
  empty cells and ``NA`` are treated as missing and excluded;
  any other non-numeric cell makes the dataset invalid.
* count: number of non-missing values.
* mean: arithmetic mean.
* median: percentile 50 with the rule below.
* stdev: *sample* standard deviation (n - 1).
* percentiles: linear interpolation between closest ranks
  (``rank = p/100 * (n - 1)``, equivalent to NumPy's default "linear").
* every statistic is a decimal string rounded half-even to 6 places,
  which keeps reports canonical (no floats in signed objects).
* tolerance: absolute difference <= 0.000001 per field.
"""

from __future__ import annotations

import csv
import io
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, getcontext

from .canonical import hash_obj, sha256_hex

NAME = "pactmesh.stats"
VERSION = "1.0"
VERIFIER = {"name": NAME, "version": VERSION}
PLACES = Decimal("0.000001")
TOLERANCE = Decimal("0.000001")
MISSING = {"", "NA"}

getcontext().prec = 50


class DatasetError(ValueError):
    pass


def _q(x: Decimal) -> str:
    return str(x.quantize(PLACES, rounding=ROUND_HALF_EVEN))


def load_column(csv_bytes: bytes, column: str) -> list[Decimal]:
    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
    if reader.fieldnames is None or column not in reader.fieldnames:
        raise DatasetError(f"column {column!r} not found")
    values = []
    for row in reader:
        cell = (row[column] or "").strip()
        if cell in MISSING:
            continue
        try:
            d = Decimal(cell)
        except InvalidOperation as e:
            raise DatasetError(f"non-numeric value {cell!r}") from e
        if not d.is_finite():
            raise DatasetError(f"non-finite value {cell!r}")
        values.append(d)
    if len(values) < 2:
        raise DatasetError("need at least two values")
    return values


def _percentile(sorted_vals: list[Decimal], p: int) -> Decimal:
    rank = Decimal(p) / 100 * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def compute_report(csv_bytes: bytes, column: str, percentiles: list[int]) -> dict:
    vals = load_column(csv_bytes, column)
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    sv = sorted(vals)
    return {
        "verifier": VERIFIER,
        "dataset_sha256": sha256_hex(csv_bytes),
        "column": column,
        "count": n,
        "mean": _q(mean),
        "median": _q(_percentile(sv, 50)),
        "stdev": _q(var.sqrt()),
        "percentiles": {f"p{p}": _q(_percentile(sv, p)) for p in percentiles},
    }


def report_hash(report: dict) -> str:
    return hash_obj(report)


def verify_report(report: dict, csv_bytes: bytes, column: str, percentiles: list[int]) -> tuple[bool, list[str]]:
    """Compare a delivered report against the buyer's reference computation."""
    errors: list[str] = []
    if not isinstance(report, dict):
        return False, ["report is not a JSON object"]
    expected = compute_report(csv_bytes, column, percentiles)
    expected_keys = set(expected)
    if set(report) != expected_keys:
        errors.append(f"fields differ: expected {sorted(expected_keys)}")
        return False, errors
    if report["verifier"] != VERIFIER:
        errors.append("verifier name/version does not match agreement")
    if report["dataset_sha256"] != expected["dataset_sha256"]:
        errors.append("dataset hash mismatch")
    if report["column"] != column:
        errors.append("column mismatch")
    if report["count"] != expected["count"]:
        errors.append("count mismatch")

    def close(field: str, got, want) -> None:
        try:
            if abs(Decimal(got) - Decimal(want)) > TOLERANCE:
                errors.append(f"{field}: got {got}, expected {want}")
        except (InvalidOperation, TypeError):
            errors.append(f"{field}: not a decimal string")

    for f in ("mean", "median", "stdev"):
        close(f, report[f], expected[f])
    got_p = report["percentiles"]
    if not isinstance(got_p, dict) or set(got_p) != set(expected["percentiles"]):
        errors.append("percentile keys mismatch")
    else:
        for k, v in expected["percentiles"].items():
            close(k, got_p[k], v)
    return not errors, errors
