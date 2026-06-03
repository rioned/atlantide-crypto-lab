"""Stock market session times with timezone-aware open/close computation.

Defines 7 major global exchanges and computes current status
(open/closed/lunch break) with countdown to next event.
"""

from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
import threading

# ─── Market Definitions ──────────────────────────────────────────────────────
# Each exchange: name, city, IANA timezone, trading sessions (local time), flags
# Some exchanges have a lunch break (SSE, TSE), others run continuously.

MARKETS = [
    {
        "name": "NYSE / NASDAQ",
        "city": "New York",
        "country": "United States",
        "flag": "🇺🇸",
        "tz": "America/New_York",
        "sessions": [
            ("09:30", "16:00"),  # regular session (no lunch break)
        ],
    },
    {
        "name": "Shanghai (SSE)",
        "city": "Shanghai",
        "country": "China",
        "flag": "🇨🇳",
        "tz": "Asia/Shanghai",
        "sessions": [
            ("09:30", "11:30"),  # morning session
            ("13:00", "15:00"),  # afternoon session
        ],
    },
    {
        "name": "Tokyo (TSE)",
        "city": "Tokyo",
        "country": "Japan",
        "flag": "🇯🇵",
        "tz": "Asia/Tokyo",
        "sessions": [
            ("09:00", "11:30"),  # morning session
            ("12:30", "15:00"),  # afternoon session
        ],
    },
    {
        "name": "London (LSE)",
        "city": "London",
        "country": "United Kingdom",
        "flag": "🇬🇧",
        "tz": "Europe/London",
        "sessions": [
            ("08:00", "16:30"),  # regular session
        ],
    },
    {
        "name": "ASX",
        "city": "Sydney",
        "country": "Australia",
        "flag": "🇦🇺",
        "tz": "Australia/Sydney",
        "sessions": [
            ("10:00", "16:00"),  # regular session
        ],
    },
    {
        "name": "JSE",
        "city": "Johannesburg",
        "country": "South Africa",
        "flag": "🇿🇦",
        "tz": "Africa/Johannesburg",
        "sessions": [
            ("09:00", "17:00"),  # regular session
        ],
    },
    {
        "name": "B3",
        "city": "São Paulo",
        "country": "Brazil",
        "flag": "🇧🇷",
        "tz": "America/Sao_Paulo",
        "sessions": [
            ("10:00", "17:30"),  # regular session
        ],
    },
]

# Alert threshold: 10 minutes in seconds
ALERT_SECONDS = 600

# Track which alerts have already been fired (market_name -> set of event types)
_alerts_fired = {}
_alerts_lock = threading.Lock()


def _parse_time(s: str) -> dtime:
    """Parse 'HH:MM' string to datetime.time."""
    parts = s.split(":")
    return dtime(int(parts[0]), int(parts[1]))


def compute_session_status(tz_name: str, sessions: list) -> dict:
    """Compute current status for one exchange.

    Returns:
        dict with: is_open, current_session_idx, next_event, 
        next_event_time (ISO), seconds_until_next, session_name
    """
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)

    # Build all open/close events for today as sorted list
    today = now_local.date()
    events = []
    for idx, (open_str, close_str) in enumerate(sessions):
        open_dt = datetime.combine(today, _parse_time(open_str), tzinfo=tz)
        close_dt = datetime.combine(today, _parse_time(close_str), tzinfo=tz)
        events.append({
            "type": "open",
            "dt": open_dt,
            "session_idx": idx,
        })
        events.append({
            "type": "close",
            "dt": close_dt,
            "session_idx": idx,
        })
    events.sort(key=lambda e: e["dt"])

    # Determine if currently in a session
    is_open = False
    current_session_idx = -1
    for idx, (open_str, close_str) in enumerate(sessions):
        open_dt = datetime.combine(today, _parse_time(open_str), tzinfo=tz)
        close_dt = datetime.combine(today, _parse_time(close_str), tzinfo=tz)
        if open_dt <= now_local < close_dt:
            is_open = True
            current_session_idx = idx
            break

    # Find next event
    next_event = None
    next_event_dt = None
    for e in events:
        if e["dt"] > now_local:
            # For close events during the current session, skip if we already
            # passed that close time
            if e["type"] == "close" and e["session_idx"] == current_session_idx:
                pass  # this is the upcoming close — good
            next_event = e
            next_event_dt = e["dt"]
            break

    # Handle overnight: if no more events today, first event tomorrow
    if next_event is None:
        next_day = today + timedelta(days=1)
        # Check if next day is weekend — skip to Monday
        while next_day.weekday() >= 5:  # Saturday=5, Sunday=6
            next_day += timedelta(days=1)
        first_open = _parse_time(sessions[0][0])
        next_event_dt = datetime.combine(next_day, first_open, tzinfo=tz)
        next_event = {"type": "open", "dt": next_event_dt, "session_idx": 0}

    seconds_until = int((next_event_dt - now_local).total_seconds())
    if seconds_until < 0:
        seconds_until = 0

    # Session context name
    session_name = ""
    if is_open:
        open_str, close_str = sessions[current_session_idx]
        session_name = f"{open_str}–{close_str}"
    elif current_session_idx < 0:
        # Before first session
        session_name = "Pre-market"
    # else between sessions (lunch break)

    return {
        "is_open": is_open,
        "current_session_idx": current_session_idx,
        "next_event_type": next_event["type"],
        "next_event_time": next_event_dt.isoformat(),
        "seconds_until_next": seconds_until,
        "session_name": session_name,
        "local_time": now_local.strftime("%H:%M"),
    }


def get_all_market_statuses() -> list:
    """Return status for all defined markets."""
    results = []
    now_utc = datetime.now(ZoneInfo("UTC"))
    for m in MARKETS:
        status = compute_session_status(m["tz"], m["sessions"])
        results.append({
            "name": m["name"],
            "city": m["city"],
            "country": m["country"],
            "flag": m["flag"],
            "tz": m["tz"],
            **status,
        })
    return results


def check_alerts() -> list:
    """Check if any market is within 10 minutes of open/close.

    Returns list of alert dicts with market name, event type, and seconds until.
    Each market-event pair is only reported once (de-duplicated).
    """
    alerts = []
    now_utc = datetime.now(ZoneInfo("UTC"))
    with _alerts_lock:
        for m in MARKETS:
            status = compute_session_status(m["tz"], m["sessions"])
            secs = status["seconds_until_next"]
            evt = status["next_event_type"]
            key = f"{m['name']}:{evt}"

            if 0 < secs <= ALERT_SECONDS and key not in _alerts_fired:
                alerts.append({
                    "market": m["name"],
                    "city": m["city"],
                    "flag": m["flag"],
                    "event_type": evt,
                    "seconds_until": secs,
                })
                _alerts_fired[key] = True

            # Clean up stale alert markers: allow re-arm once event has passed
            if secs > ALERT_SECONDS + 60 or secs == 0:
                _alerts_fired.pop(key, None)

    return alerts


def reset_alerts():
    """Reset all alert tracking (called on server restart)."""
    with _alerts_lock:
        _alerts_fired.clear()
