"""Small shared helpers with no other home."""
import json
import os
import tempfile
import time
from pathlib import Path


def atomic_write_json(path, data) -> None:
    """Write JSON so a crash mid-write can never leave a corrupt/partial file.

    Writes to a temp file in the same directory (so the final os.replace is
    on the same filesystem and therefore atomic), then renames over the
    target. A reader either sees the old complete file or the new complete
    file, never a half-written one.

    On Windows, os.replace can raise a transient PermissionError if another
    process (antivirus, a file indexer, an editor's file watcher) briefly
    holds the destination open -- hit this for real while generating 200+
    files in a row (invoice #66/210). A short retry with backoff is standard
    practice for exactly this; it's not papering over a real bug, since the
    lock is never ours and always releases within milliseconds.
    """
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        for attempt in range(5):
            try:
                os.replace(tmp_name, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (2 ** attempt))
    except Exception:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise
