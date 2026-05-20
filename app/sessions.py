"""Market session computation — UTC open/close times with countdowns."""

from datetime import datetime, timedelta

from app.config import MARKET_SESSIONS


def compute_session_data(utc_now, tz_offset_hours):
    """Return session state dicts with countdown in seconds."""
    sessions = []
    for s in MARKET_SESSIONS:
        # Build today's open/close as UTC datetimes
        open_dt = utc_now.replace(hour=s["open_utc_hour"],
                                  minute=s["open_utc_minute"],
                                  second=0, microsecond=0)
        close_dt = utc_now.replace(hour=s["close_utc_hour"],
                                   minute=s["close_utc_minute"],
                                   second=0, microsecond=0)

        # If session already closed today, shift to tomorrow
        if utc_now >= close_dt:
            open_dt += timedelta(days=1)
            close_dt += timedelta(days=1)

        is_open = open_dt <= utc_now < close_dt
        time_until_open = (max(0, (open_dt - utc_now).total_seconds())
                           if not is_open else 0)
        time_until_close = (max(0, (close_dt - utc_now).total_seconds())
                            if is_open else 0)

        # Convert to local time
        local_open = open_dt + timedelta(hours=tz_offset_hours)
        local_close = close_dt + timedelta(hours=tz_offset_hours)

        sessions.append({
            "id": s["id"],
            "name": s["name"],
            "emoji": s["emoji"],
            "is_open": is_open,
            "time_until_open": int(time_until_open),
            "time_until_close": int(time_until_close),
            "open_utc": open_dt.strftime("%H:%M UTC"),
            "close_utc": close_dt.strftime("%H:%M UTC"),
            "open_local": local_open.strftime("%H:%M"),
            "close_local": local_close.strftime("%H:%M"),
            "open_iso": open_dt.isoformat(),
            "close_iso": close_dt.isoformat(),
        })
    return sessions
