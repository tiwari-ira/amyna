"""[3.3] The language-engine eval harness is enforced, not just written."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from eval_language import evaluate  # noqa: E402


def test_the_default_engine_passes_the_eval_harness() -> None:
    """The bar any future model-backed engine must clear (>=85% + invariants)."""
    report = evaluate()
    assert report["pass"], report
    assert report["accuracy"] >= 0.85
    assert report["invariants"] == []
    assert not any(i["unsafe"] for i in report["injections"])
