"""The capture station (ESP32): live status and commands from the site.

The ESP32 checks in about once a second (POST /api/station/poll) with its state
and takes any waiting command in the reply. The server never connects to the
ESP32, so this works whatever address it has. Kept in memory: a restart only
forgets the last status for a second.
"""
import threading
import time

ONLINE_SECONDS = 5           # no check-in for this long = offline
COMMAND_TTL = 20             # a command not picked up in time is dropped
COMMANDS = ("capture", "abort")

_lock = threading.Lock()
_state: dict = {"seen": 0.0, "status": {}, "ip": ""}
_pending: dict = {"command": None, "at": 0.0}


def checked_in(status: dict, ip: str) -> str | None:
    """Records the station's status and hands over a waiting command, once."""
    with _lock:
        _state.update(seen=time.time(), status=status, ip=ip)
        cmd = _pending["command"]
        if cmd and time.time() - _pending["at"] <= COMMAND_TTL:
            _pending["command"] = None
            return cmd
        _pending["command"] = None
        return None


def request(command: str):
    if command not in COMMANDS:
        raise ValueError("Unknown command")
    with _lock:
        _pending.update(command=command, at=time.time())


def view() -> dict:
    with _lock:
        age = time.time() - _state["seen"] if _state["seen"] else None
        waiting = _pending["command"] if _pending["command"] and time.time() - _pending["at"] <= COMMAND_TTL else None
        return {"online": age is not None and age < ONLINE_SECONDS, "last_seen_seconds": None if age is None else round(age, 1),
                "status": _state["status"], "ip": _state["ip"], "pending": waiting}
