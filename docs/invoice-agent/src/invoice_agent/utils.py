"""Small shared helpers with no other home."""
import json
import os
import tempfile
from pathlib import Path


def atomic_write_json(path, data) -> None:
    """Write JSON so a crash mid-write can never leave a corrupt/partial file.

    Writes to a temp file in the same directory (so the final os.replace is
    on the same filesystem and therefore atomic), then renames over the
    target. A reader either sees the old complete file or the new complete
    file, never a half-written one.
    """
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise
