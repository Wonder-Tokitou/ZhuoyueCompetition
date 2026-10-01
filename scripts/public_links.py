"""Publish an atomic, explicit lifecycle state for public access links."""
import json
from pathlib import Path
import tempfile


def write_link_state(folder: Path, *, status: str, origin: str | None = None) -> Path:
    if status not in {"starting", "ready", "stopped", "failed"}:
        raise ValueError("Invalid public link state")
    if status == "ready" and not origin:
        raise ValueError("Ready public link state requires an origin")
    links = {
        "status": status,
        "teacher": f"{origin}/teacher/login" if origin else None,
        "student": f"{origin}/student" if origin else None,
        "switch_space": f"{origin}/access/logout" if origin else None,
    }
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "links.json"
    handle, temporary = tempfile.mkstemp(prefix="links-", suffix=".tmp", dir=folder)
    try:
        with open(handle, "w", encoding="utf-8", closefd=True) as file:
            json.dump(links, file, ensure_ascii=False, indent=2)
            file.flush()
        Path(temporary).replace(target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target
