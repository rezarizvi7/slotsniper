"""Per-target memory of what was open last poll, so we alert on transitions, not every poll."""
from __future__ import annotations

import json
from pathlib import Path

from .extract import Slot


class State:
    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text()) if path.exists() else {}
        self.open: dict[str, bool] = data.get("open", {})
        self.booked: list[str] = data.get("booked", [])

    def newly_open(self, slots: list[Slot]) -> list[Slot]:
        fresh = [s for s in slots if s.available and not self.open.get(s.id)]
        self.open = {s.id: s.available for s in slots}
        return fresh

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"open": self.open, "booked": self.booked}, indent=2))
