"""Deterministic evaluation helpers for meeting-intelligence fixtures."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    cases: int
    true_positive: int
    false_positive: int
    false_negative: int
    duplicate_count: int
    unsupported_count: int
    precision: float
    recall: float
    f1: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or not value.get("case_id"):
                raise ValueError(f"invalid evaluation row at line {line_number}")
            rows.append(value)
    ids = [row["case_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("evaluation case_id values must be unique")
    return rows


def validate_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text("utf-8"))
    corpus_path = manifest_path.parent / manifest["corpus_file"]
    if sha256_file(corpus_path) != manifest["sha256"]:
        raise ValueError("evaluation corpus checksum does not match manifest")
    rows = load_jsonl(corpus_path)
    if len(rows) != manifest["case_count"]:
        raise ValueError("evaluation corpus case count does not match manifest")
    return manifest


def _concepts(row: dict[str, Any], gold: bool) -> list[dict[str, Any]]:
    value = row.get("gold", {}).get("concepts", []) if gold else row.get("concepts", [])
    if not isinstance(value, list):
        raise ValueError("concepts must be a list")
    return value


def evaluate_rows(corpus: Iterable[dict[str, Any]], results: Iterable[dict[str, Any]]) -> EvaluationMetrics:
    expected_rows = {row["case_id"]: row for row in corpus}
    actual_rows = {row["case_id"]: row for row in results}
    if set(actual_rows) - set(expected_rows):
        raise ValueError("results contain unknown case IDs")
    tp = fp = fn = duplicates = unsupported = 0
    for case_id, expected in expected_rows.items():
        expected_ids = {item["concept_id"] for item in _concepts(expected, True)}
        actual_items = _concepts(actual_rows.get(case_id, {"concepts": []}), False)
        actual_ids = [item["concept_id"] for item in actual_items]
        actual_unique = set(actual_ids)
        tp += len(expected_ids & actual_unique)
        fp += len(actual_unique - expected_ids)
        fn += len(expected_ids - actual_unique)
        duplicates += len(actual_ids) - len(actual_unique)
        unsupported += sum(1 for item in actual_items if not item.get("evidence_sequences"))
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return EvaluationMetrics(
        cases=len(expected_rows), true_positive=tp, false_positive=fp, false_negative=fn,
        duplicate_count=duplicates, unsupported_count=unsupported,
        precision=round(precision, 6), recall=round(recall, 6), f1=round(f1, 6),
    )


def deterministic_reference_results(corpus: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build evaluator self-test results; this is explicitly not a provider baseline."""
    return [
        {
            "case_id": row["case_id"],
            "concepts": [
                {**concept, "evidence_sequences": concept.get("evidence_sequences", [row["turns"][-1]["sequence"]])}
                for concept in row["gold"].get("concepts", [])
            ],
        }
        for row in corpus
    ]
