"""Hermes-independent outbound attachment safety checks."""

from __future__ import annotations

import os
import stat
from typing import Any, Optional


def validate_outbound_attachment_path(path: Any) -> Optional[str]:
    """Return an error when *path* is missing or not a regular file."""
    text = str(path)
    try:
        file_stat = os.lstat(text)
    except OSError:
        return "imsg attachment not found"
    if stat.S_ISLNK(file_stat.st_mode):
        return "imsg attachment must be a regular file, not a symlink"
    if not stat.S_ISREG(file_stat.st_mode):
        return "imsg attachment must be a regular file"
    return None
