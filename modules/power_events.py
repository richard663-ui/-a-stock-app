"""Read Windows standby history and power-button/lid settings (read-only).

The collection gaps of Oct 2026 were caused by Modern Standby entered via the
power button. Desktop apps cannot veto user-initiated standby, so the monitor
must (1) warn when the configured actions would stop collection and (2)
attribute every gap to its real cause from the System event log.
"""
from __future__ import annotations

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List, Optional

NS = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
# POWER_MONITOR_REQUEST_REASON (winnt.h) for the values that matter here.
REASONS = {
    0: "Unknown", 1: "Power Button", 4: "User Input", 7: "System State",
    8: "SetThreadExecutionState", 10: "Session Unlock", 11: "Screen Off Request",
    12: "Idle Timeout", 13: "Policy Change", 14: "Sleep Button", 15: "Lid",
    23: "Thermal Standby", 31: "Input Keyboard", 32: "Input Mouse", 33: "Input Touchpad",
    35: "Input Accelerometer", 53: "Battery Pre-Critical",
}
# User-initiated reasons that apps cannot override on Modern Standby systems.
USER_INITIATED = {"Power Button", "Sleep Button", "Lid"}

SUB_BUTTONS = "4f971e89-eebd-4455-a8de-9e59040e7347"
SETTINGS = {
    "7648efa3-dd9c-4e3e-b566-50f929386280": "power_button",
    "5ca83367-6e45-459f-a27b-476b1d01c936": "lid_close",
    "96996bc0-ad50-47ec-923b-6f41874dd9eb": "sleep_button",
}
ACTIONS = {0: "do_nothing", 1: "sleep", 2: "hibernate", 3: "shut_down", 4: "turn_off_display"}
STOPS_COLLECTION = {"sleep", "hibernate", "shut_down"}


def reason_name(code: Optional[int]) -> str:
    if code is None:
        return "Unknown"
    # Large flagged values (e.g. 16777220) are brief system/maintenance transitions.
    return REASONS.get(code, f"code {code}")


def _run(args: List[str], timeout: float = 20.0) -> str:
    if sys.platform != "win32":
        return ""
    proc = subprocess.run(args, capture_output=True, timeout=timeout)
    raw = proc.stdout or b""
    for encoding in ("utf-8", "mbcs", "gbk"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def parse_standby_events(xml_text: str) -> List[Dict]:
    """Parse Kernel-Power 506 (enter) / 507 (exit) events into dicts."""
    if not xml_text.strip():
        return []
    root = ET.fromstring("<Events>" + xml_text + "</Events>")
    events: List[Dict] = []
    for node in root.findall("e:Event", NS):
        event_id = int(node.findtext("e:System/e:EventID", default="0", namespaces=NS))
        created = node.find("e:System/e:TimeCreated", NS)
        stamp = created.get("SystemTime") if created is not None else None
        if not stamp:
            continue
        when = datetime.fromisoformat(stamp[:26].rstrip("Z") + "+00:00") if "." in stamp else \
            datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        data = {d.get("Name"): (d.text or "") for d in node.findall("e:EventData/e:Data", NS)}
        try:
            code = int(data.get("Reason", ""))
        except ValueError:
            code = None
        def seconds(name: str) -> Optional[float]:
            try:
                return round(int(data.get(name, "")) / 1e6, 1)
            except ValueError:
                return None

        events.append({
            "event_id": event_id, "time": when.astimezone(timezone.utc),
            "kind": "enter" if event_id == 506 else "exit",
            "reason_code": code, "reason": reason_name(code),
            "lid_open": data.get("LidOpenState"), "on_ac": data.get("PowerStateAc"),
            # On exit: how long the CPU stayed active versus in low-power DRIPS.
            "active_seconds": seconds("ActiveResidencyInUs"), "drips_seconds": seconds("DripsResidencyInUs"),
        })
    return sorted(events, key=lambda item: item["time"])


def standby_intervals(start: datetime, end: datetime) -> List[Dict]:
    """Standby periods overlapping [start, end] (aware datetimes)."""
    since = (start.astimezone(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    query = ("*[System[Provider[@Name='Microsoft-Windows-Kernel-Power'] and "
             f"(EventID=506 or EventID=507) and TimeCreated[@SystemTime>='{since}']]]")
    try:
        events = parse_standby_events(_run(["wevtutil", "qe", "System", f"/q:{query}", "/f:xml", "/c:5000"]))
    except (subprocess.TimeoutExpired, OSError, ET.ParseError):
        return []
    return pair_intervals(events, start, end)


def pair_intervals(events: List[Dict], start: datetime, end: datetime) -> List[Dict]:
    intervals: List[Dict] = []
    open_item: Optional[Dict] = None
    for event in events:
        if event["kind"] == "enter":
            open_item = {"entered": event["time"], "exited": None, "reason": event["reason"],
                         "reason_code": event["reason_code"], "lid_open": event["lid_open"],
                         "user_initiated": event["reason"] in USER_INITIATED}
        elif open_item is not None:
            open_item["exited"] = event["time"]
            open_item["exit_reason"] = event["reason"]
            open_item["active_seconds"] = event.get("active_seconds")
            open_item["drips_seconds"] = event.get("drips_seconds")
            intervals.append(open_item)
            open_item = None
    if open_item is not None:
        intervals.append(open_item)
    lo, hi = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    out = []
    for item in intervals:
        exited = item["exited"] or hi
        if exited >= lo and item["entered"] <= hi:
            item["seconds"] = round((exited - item["entered"]).total_seconds(), 1)
            out.append(item)
    return out


def parse_boot_events(xml_text: str) -> List[Dict]:
    """Kernel-General 12 (OS started) / 13 (OS shut down) and Kernel-Power 41 (unexpected)."""
    if not xml_text.strip():
        return []
    root = ET.fromstring("<Events>" + xml_text + "</Events>")
    out = []
    for node in root.findall("e:Event", NS):
        event_id = int(node.findtext("e:System/e:EventID", default="0", namespaces=NS))
        provider = node.find("e:System/e:Provider", NS)
        name = provider.get("Name") if provider is not None else ""
        created = node.find("e:System/e:TimeCreated", NS)
        stamp = created.get("SystemTime") if created is not None else None
        if not stamp:
            continue
        when = datetime.fromisoformat(stamp[:26].rstrip("Z") + "+00:00") if "." in stamp else \
            datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        kind = {("Microsoft-Windows-Kernel-General", 12): "boot",
                ("Microsoft-Windows-Kernel-General", 13): "shutdown",
                ("Microsoft-Windows-Kernel-Power", 41): "unexpected_restart"}.get((name, event_id))
        if kind:
            out.append({"time": when.astimezone(timezone.utc), "kind": kind})
    return sorted(out, key=lambda item: item["time"])


def boot_events(start: datetime) -> List[Dict]:
    since = (start.astimezone(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    query = ("*[System[((Provider[@Name='Microsoft-Windows-Kernel-General'] and (EventID=12 or EventID=13)) or "
             "(Provider[@Name='Microsoft-Windows-Kernel-Power'] and EventID=41)) and "
             f"TimeCreated[@SystemTime>='{since}']]]")
    try:
        return parse_boot_events(_run(["wevtutil", "qe", "System", f"/q:{query}", "/f:xml", "/c:2000"]))
    except (subprocess.TimeoutExpired, OSError, ET.ParseError):
        return []


def power_off_intervals(start: datetime, end: datetime, events: Optional[List[Dict]] = None) -> List[Dict]:
    """Periods the PC was shut down (shutdown -> next boot) overlapping [start, end]."""
    events = boot_events(start) if events is None else events
    intervals, off_since = [], None
    for event in events:
        if event["kind"] == "shutdown":
            off_since = event["time"]
        elif event["kind"] == "boot":
            if off_since is not None:
                intervals.append({"entered": off_since, "exited": event["time"], "reason": "PC shut down",
                                  "user_initiated": True})
            else:
                # Boot without a clean shutdown record: crash, power loss or forced off.
                intervals.append({"entered": event["time"], "exited": event["time"],
                                  "reason": "PC restarted (no clean shutdown logged)", "user_initiated": False})
            off_since = None
    lo, hi = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    return [i for i in intervals if i["exited"] >= lo and i["entered"] <= hi]


def parse_powercfg_buttons(text: str) -> Dict[str, Dict[str, Optional[str]]]:
    """Parse ``powercfg /qh SCHEME_CURRENT SUB_BUTTONS`` (any UI language)."""
    result: Dict[str, Dict[str, Optional[str]]] = {}
    current = None
    for line in text.splitlines():
        guid = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", line)
        if guid and guid.group(1) in SETTINGS:
            current = SETTINGS[guid.group(1)]
            result[current] = {"ac": None, "dc": None}
            continue
        if guid and guid.group(1) != SUB_BUTTONS:
            current = None
        value = re.search(r"0x([0-9a-f]{8})\s*$", line.strip())
        if current and value:
            slot = "ac" if result[current]["ac"] is None else "dc"
            result[current][slot] = ACTIONS.get(int(value.group(1), 16), f"code {int(value.group(1), 16)}")
    return result


def power_settings() -> Dict:
    """Current-scheme power button / lid / sleep button actions on AC and battery."""
    if sys.platform != "win32":
        return {"available": False}
    try:
        buttons = parse_powercfg_buttons(_run(["powercfg", "/qh", "SCHEME_CURRENT", SUB_BUTTONS]))
    except (subprocess.TimeoutExpired, OSError):
        return {"available": False}
    risks = [f"{name} on AC = {values.get('ac')}" for name, values in buttons.items()
             if values.get("ac") in STOPS_COLLECTION]
    return {"available": True, "buttons": buttons, "ac_risks": risks,
            "note": "On Modern Standby laptops, any of these actions suspends all recorders."}
