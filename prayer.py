#!/usr/bin/env python3
"""Prayer-time reminders to a Telegram group (stdlib only).

Times come from the official DUMK schedule (namaz.muftyat.kz).
For each of the five daily prayers it sends two messages:
  - 30 minutes before the prayer time: "<prayer> намазына 30 минут қалды (HH:MM)"
  - 30 minutes after the prayer time:  "<prayer> намазын оқыдың ба?"

GitHub starts scheduled workflows irregularly (often 40-90 minutes apart), so the
workflow runs this script with --loop: one job stays alive for several hours and
checks the clock every 30 seconds. Sent reminders are written to a state file
after every send, so a following job never repeats them.

Environment: BOT_TOKEN, CHAT_ID (required); LAT, LNG (default: Astana);
STATE_FILE (default: state.json next to this script); TEST=1 sends a test message.
"""
import argparse, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=5))  # Kazakhstan
LAT = os.environ.get("LAT", "51.133333")
LNG = os.environ.get("LNG", "71.433333")
STATE_FILE = os.environ.get("STATE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json"))
PRAYERS = [("fajr", "Таң"), ("dhuhr", "Бесін"), ("asr", "Екінті"), ("maghrib", "Ақшам"), ("isha", "Құптан")]
BEFORE = timedelta(minutes=30)
AFTER = timedelta(minutes=30)
LATE_LIMIT = timedelta(minutes=20)  # a reminder later than this is skipped as useless
_times_cache = {}


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "prayer-reminder/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def times_for(day_dt):
    day = day_dt.strftime("%Y-%m-%d")
    if day in _times_cache:
        return _times_cache[day]
    for url in (f"https://namaz.muftyat.kz/kk/api/times/{day_dt.year}/{LAT}/{LNG}",
                f"https://api.muftyat.kz/prayer-times/{day_dt.year}/{LAT}/{LNG}"):
        try:
            data = get_json(url)
        except Exception as e:  # try the next source
            print(f"::warning::prayer source failed: {urllib.parse.urlsplit(url).hostname}: {type(e).__name__}: {e}", flush=True)
            continue
        for row in data.get("result") or []:
            if (row.get("Date") or row.get("date")) == day:
                _times_cache.clear()
                _times_cache[day] = row
                return row
    raise RuntimeError("No prayer times found for " + day)


def send(text):
    token, chat = os.environ["BOT_TOKEN"], os.environ["CHAT_ID"]
    body = json.dumps({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("ok")


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def tick(state):
    """Send every reminder that is due now. Returns the (possibly new) state."""
    now = datetime.now(TZ)
    day = now.strftime("%Y-%m-%d")
    if state.get("day") != day:
        state = {"day": day, "sent": []}
        save_state(state)
    times = times_for(now)
    for key, name in PRAYERS:
        hhmm = times.get(key)
        if not hhmm:
            continue
        h, m = map(int, hhmm.split(":"))
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        jobs = [
            (f"{key}-before", t - BEFORE, f"{name} намазына 30 минут қалды. Намаз уақыты: {hhmm}."),
            (f"{key}-after", t + AFTER, f"{name} намазын оқыдың ба? (уақыты {hhmm} кірген)\nОқысаң, осы жерге «оқыдым» деп жаз."),
        ]
        for job_id, at, text in jobs:
            if job_id in state["sent"] or now < at:
                continue
            if now - at > LATE_LIMIT:
                print(f"::warning::skipped {job_id}: {int((now - at).total_seconds() // 60)} min late", flush=True)
                state["sent"].append(job_id)
                save_state(state)
                continue
            if send(text):
                print(now.strftime("%H:%M"), "sent", job_id, flush=True)
                state["sent"].append(job_id)
                save_state(state)
    return state


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--loop", type=float, default=0, help="keep checking for this many minutes")
    args = p.parse_args()
    for var in ("BOT_TOKEN", "CHAT_ID"):
        if not os.environ.get(var):
            raise RuntimeError(f"secret {var} is empty or missing")
    if os.environ.get("TEST"):
        times = times_for(datetime.now(TZ))
        lines = [f"{name}: {times.get(key, '-')}" for key, name in PRAYERS]
        send("Намаз еске салу жұмыс істеп тұр. Бүгінгі уақыттар (Астана):\n" + "\n".join(lines))
        print("test message sent")
        return
    state = load_state()
    deadline = time.monotonic() + args.loop * 60
    errors = 0
    while True:
        try:
            state = tick(state)
            errors = 0
        except Exception as e:  # network hiccup: report and keep going
            errors += 1
            detail = ""
            if isinstance(e, urllib.error.HTTPError):
                try:
                    detail = json.load(e).get("description", "")
                except Exception:
                    pass
            print(f"::warning::{type(e).__name__}: {e} {detail}".strip(), flush=True)
            if not args.loop or errors >= 20:
                raise
        if time.monotonic() >= deadline:
            break
        time.sleep(30)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"::error::{type(e).__name__}: {e}")
        sys.exit(1)
