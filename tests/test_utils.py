import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent.utils import escape_dollars  # noqa: E402


def test_escape_dollars_prevents_latex_rendering():
    assert escape_dollars("extraction $1.46, investigation $3.50") == \
        r"extraction \$1.46, investigation \$3.50"


def test_escape_dollars_noop_on_text_without_dollars():
    assert escape_dollars("no currency here") == "no currency here"
