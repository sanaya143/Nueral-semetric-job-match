"""Local file storage for raw CVs (data/uploads/<tenant>/...).

Production replaces this with encrypted object storage behind the same two functions.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from nsm.config import get_settings

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def save_upload(tenant_id: str, filename: str, data: bytes) -> Path:
    base = get_settings().uploads_dir / _SAFE.sub("_", tenant_id)
    base.mkdir(parents=True, exist_ok=True)
    name = _SAFE.sub("_", Path(filename).name)[-120:] or "file"
    path = base / f"{uuid.uuid4().hex[:12]}_{name}"
    path.write_bytes(data)
    return path


def delete_file(path: str | Path) -> bool:
    p = Path(path)
    try:
        uploads = get_settings().uploads_dir.resolve()
        if uploads not in p.resolve().parents:
            return False  # never delete outside the uploads folder
        p.unlink(missing_ok=True)
        return True
    except OSError:
        return False
