"""Alarm manager đơn giản cho nhiều camera."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List


class AlarmManager:
    def __init__(self, event_logger=None) -> None:
        self.active: Dict[str, Dict] = {}
        self.history: List[Dict] = []
        self.event_logger = event_logger

    def update_camera(self, info: Dict) -> None:
        cid = info.get("id", "UNKNOWN")
        if not info.get("enabled", False):
            self.clear(f"{cid}_CAMERA")
            self.clear(f"{cid}_LOW")
            self.clear(f"{cid}_HIGH")
            return

        if not info.get("online", False):
            self.add(f"{cid}_CAMERA", cid, "Mất kết nối camera", "HIGH")
        else:
            self.clear(f"{cid}_CAMERA")

        status = info.get("status")
        if status == "LOW":
            self.add(f"{cid}_LOW", cid, "Mức nước thấp", "WARNING")
        else:
            self.clear(f"{cid}_LOW")

        if status == "HIGH":
            self.add(f"{cid}_HIGH", cid, "Mức nước cao", "HIGH")
        else:
            self.clear(f"{cid}_HIGH")

    def add(self, code: str, source: str, message: str, severity: str) -> None:
        if code in self.active:
            return
        item = {
            "code": code,
            "source": source,
            "message": message,
            "severity": severity,
            "time": datetime.now().isoformat(timespec="seconds"),
            "ack": False,
        }
        self.active[code] = item
        self.history.append(item)
        if self.event_logger:
            self.event_logger("alarm", source, message, severity, None, code)

    def clear(self, code: str) -> None:
        item = self.active.pop(code, None)
        if item and self.event_logger:
            self.event_logger("alarm_clear", item.get("source", "SYSTEM"), item.get("message", code), "INFO", None, code)

    def get_active(self) -> List[Dict]:
        return list(self.active.values())

    def get_history(self) -> List[Dict]:
        return self.history[-500:]
