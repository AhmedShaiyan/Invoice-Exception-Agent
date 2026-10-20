"""Coverage guard: every code in ALL_CODES must have a plain-English label
and a technical description, or a future UI page silently falls back to
showing a raw code string to a human reviewer."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent import codes  # noqa: E402


def test_every_code_has_a_plain_label():
    missing = [c for c in codes.ALL_CODES if c not in codes.PLAIN_LABELS]
    assert not missing, f"codes missing a PLAIN_LABELS entry: {missing}"


def test_every_code_has_a_description():
    missing = [c for c in codes.ALL_CODES if c not in codes.DESCRIPTIONS]
    assert not missing, f"codes missing a DESCRIPTIONS entry: {missing}"


def test_every_code_has_a_severity():
    missing = [c for c in codes.ALL_CODES if c not in codes.SEVERITY]
    assert not missing, f"codes missing a SEVERITY entry: {missing}"
