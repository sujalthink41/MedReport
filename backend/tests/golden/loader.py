"""Loading the golden set from disk."""

import json
from dataclasses import dataclass
from pathlib import Path

from tests.golden.scoring import TruthRow

GOLDEN_DIR = Path(__file__).parent
REPORTS_DIR = GOLDEN_DIR / "reports"
TRUTH_DIR = GOLDEN_DIR / "truth"


@dataclass(frozen=True)
class GoldenPage:
    page: int
    rows: list[TruthRow]


@dataclass(frozen=True)
class GoldenReport:
    name: str
    path: Path
    source: str
    lab_name: str | None
    collected_at: str | None
    pages: list[GoldenPage]


def load_all() -> list[GoldenReport]:
    """Every report that has both a file and a truth file.

    A report with no truth file is skipped silently rather than failing: half the
    set being unlabelled is a normal state while someone is working through them,
    and it must not block running the evals on the rest.
    """
    if not TRUTH_DIR.exists():
        return []

    reports: list[GoldenReport] = []
    for truth_path in sorted(TRUTH_DIR.glob("*.json")):
        document = _find_document(truth_path.stem)
        if document is None:
            continue
        reports.append(_parse(truth_path, document))
    return reports


def _find_document(stem: str) -> Path | None:
    for suffix in (".pdf", ".jpg", ".jpeg", ".png", ".heic"):
        candidate = REPORTS_DIR / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _parse(truth_path: Path, document: Path) -> GoldenReport:
    data = json.loads(truth_path.read_text(encoding="utf-8"))
    return GoldenReport(
        name=truth_path.stem,
        path=document,
        source=data.get("source", ""),
        lab_name=data.get("lab_name"),
        collected_at=data.get("collected_at"),
        pages=[
            GoldenPage(
                page=int(page["page"]),
                rows=[
                    TruthRow(
                        raw_test_name=row["raw_test_name"],
                        value_text=row.get("value_text"),
                        unit=row.get("unit"),
                        ref_low=row.get("ref_low"),
                        ref_high=row.get("ref_high"),
                    )
                    for row in page["rows"]
                ],
            )
            for page in data["pages"]
        ],
    )
