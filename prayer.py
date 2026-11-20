#!/usr/bin/env python3
"""Prayer-time reminders to a Telegram group (stdlib only).

Times come from the official DUMK schedule (namaz.muftyat.kz).
For each of the five daily prayers it sends two messages:
  - 30 minutes before the prayer time: "<prayer> намазына 30 минут қалды (HH:MM)"
  - 30 minutes after the prayer time:  "<prayer> намазын оқыдың ба?"
Run it every 10 minutes (GitHub Actions). Sent reminders are remembered in a
state file so nothing is sent twice even if a scheduled run is late.

Environment: BOT_TOKEN, CHAT_ID (required); LAT, LNG (default: Astana);
STATE_FILE (default: state.json next to this script).
"""
import json, os, sys, urllib.request
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=5))  # Kazakhstan
LAT = os.environ.get("LAT", "51.133333")
LNG = os.environ.get("LNG", "71.433333")
STATE_FILE = os.environ.get("STATE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json"))
PRAYERS = [("fajr", "Таң"), ("dhuhr", "Бесін"), ("asr", "Екінті"), ("maghrib", "Ақшам"), ("isha", "Құптан")]
BEFORE = timedelta(minutes=30)
AFTER = timedelta(minutes=30)
LATE_LIMIT = timedelta(minutes=45)  # skip a reminder if the run is this late


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "prayer-reminder/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def today_times(now):
    year, day = now.year, now.strftime("%Y-%m-%d")
    for url in (f"https://namaz.muftyat.kz/kk/api/times/{year}/{LAT}/{LNG}",
                f"https://api.muftyat.kz/prayer-times/{year}/{LAT}/{LNG}"):
        try:
            data = get_json(url)
        except Exception as e:  # try the next source
            print("source failed:", url, e)
            continue
        for row in data.get("result") or []:
            if (row.get("Date") or row.get("date")) == day:
                return row
    sys.exit("No prayer times found for " + day)


def send(text):
    token, chat = os.environ["BOT_TOKEN"], os.environ["CHAT_ID"]
    body = json.dumps({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("ok")


def main():
    now = datetime.now(TZ)
    day = now.strftime("%Y-%m-%d")
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    if state.get("day") != day:
        state = {"day": day, "sent": []}
    times = today_times(now)
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
                state["sent"].append(job_id)  # too late to be useful, skip quietly
                continue
            if send(text):
                print("sent", job_id)
                state["sent"].append(job_id)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
