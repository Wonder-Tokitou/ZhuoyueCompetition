"""Stable student display order and receipts from the frozen session snapshot.

Display letters are NOT option identifiers. Never use the display index to select
a result, or read the mutable teacher node when rendering a historical choice.
"""
import hashlib
from .student_labels import student_label


def ordered_choices(session_id, node_id, options):
    # Keep the existing shuffle algorithm exactly: historical letters stay stable.
    return sorted(options, key=lambda o: hashlib.sha256(f'{session_id}:{node_id}:{o["key"]}'.encode()).hexdigest())


def choice_receipt(session, node_id, key):
    snap = getattr(session, "case_snapshot_json", None) or {}
    node = next((n for n in snap.get("nodes", []) if n.get("id") == node_id), {})
    for index, option in enumerate(ordered_choices(getattr(session, "id", None), node_id, node.get("options", []))):
        if option.get("key") == key:
            return {"display_option": chr(65 + index), "chosen_label": student_label(option.get("label", ""), key)}
    # Legacy records without snapshots must not invent a display letter or label.
    return {"display_option": None, "chosen_label": None}
