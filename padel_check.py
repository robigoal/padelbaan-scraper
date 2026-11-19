#!/usr/bin/env python3
"""
Checkt bij Racketcentrum De Geeren (Baarn) via KNLTB Meet & Play of er een
indoor padelbaan voor 4 personen (dubbelspel, geen single court) vrij is op
woensdag- of donderdagavond met begintijd 20:00, 20:30 of 21:00.
Nieuwe vrije banen worden gemeld via een Slack Incoming Webhook.

Gebruik:
  python3 padel_check.py              # check en meld nieuwe vrije banen in Slack
  python3 padel_check.py --dry-run    # check en print alleen, geen Slack, state ongewijzigd
  python3 padel_check.py --test-slack # stuur een testbericht naar Slack

Configuratie staat in .env naast dit script (SLACK_WEBHOOK_URL=...).
Alleen standaardbibliotheek, geen pip install nodig.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import http.cookiejar
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

# ---- Instellingen ---------------------------------------------------------
CLUB_URL = "https://meetandplay.nl/club/88192"   # Racketcentrum De Geeren
UPDATE_URL = "https://meetandplay.nl/livewire/update"
WEEKDAYS = {2: "woensdag", 3: "donderdag"}       # Python: ma=0 ... zo=6
START_TIMES = ["20:00", "20:30", "21:00"]
DAYS_AHEAD = 10                                  # club: reserveren kan 10 dagen vooruit
SPORT_PADEL = 2
ROOF = "INDOOR"
FAILURES_BEFORE_ALERT = 3                        # na zoveel mislukte runs op rij: melding

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "state.json"
SLOTS_FILE = HERE / "docs" / "slots.json"       # gelezen door de PWA
PWA_URL = "https://robigoal.github.io/padelbaan-scraper/"
ENV_FILE = HERE / ".env"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) padel-check/1.0"}
NL_MONTHS = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_env() -> None:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ---- Meet & Play (Livewire) ----------------------------------------------
class MeetAndPlay:
    """Praat met de Livewire-component 'club.v2.court-show' op de clubpagina."""

    def __init__(self) -> None:
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        page = self._get(CLUB_URL)
        m = re.search(r'data-csrf="([^"]+)"', page)
        if not m:
            raise RuntimeError("CSRF-token niet gevonden op clubpagina")
        self.csrf = m.group(1)
        snaps = [html.unescape(s) for s in re.findall(r'wire:snapshot="([^"]*)"', page)]
        snaps = [s for s in snaps if '"club.v2.court-show"' in s]
        if not snaps:
            raise RuntimeError("Livewire-component 'club.v2.court-show' niet gevonden")
        self.snapshot = snaps[0]

    def _get(self, url: str) -> str:
        req = urllib.request.Request(url, headers=UA)
        with self.opener.open(req, timeout=30) as r:
            return r.read().decode("utf-8")

    def update(self, updates: dict) -> str:
        body = {"_token": self.csrf,
                "components": [{"snapshot": self.snapshot, "updates": updates, "calls": []}]}
        req = urllib.request.Request(
            UPDATE_URL, data=json.dumps(body).encode(),
            headers={**UA, "Content-Type": "application/json", "X-Livewire": "true",
                     "X-CSRF-TOKEN": self.csrf, "Referer": CLUB_URL})
        with self.opener.open(req, timeout=30) as r:
            comp = json.loads(r.read())["components"][0]
        self.snapshot = comp["snapshot"]
        return comp["effects"].get("html", "")

    @property
    def state(self) -> dict:
        return json.loads(self.snapshot)["data"]


def parse_slots(fragment: str) -> list[dict]:
    """Haal de getoonde tijdsloten uit de HTML, met baantype (bv. 'Binnen • Dubbelspel')."""
    slots = []
    for block in re.split(r'(?=<div class="mp-court-type")', fragment)[1:]:
        tm = re.search(r"<b>(.*?)</b>", block, re.S)
        court_type = html.unescape(tm.group(1)).strip() if tm else ""
        for m in re.finditer(
                r'selectSlot\((\d+)\).*?timeslot-time">\s*([^<]+?)\s*<small>([^<]+)</small>'
                r'.*?timeslot-name">\s*([^<]+?)\s*<.*?timeslot-price">(.*?)</div>',
                block, re.S):
            price = re.sub(r"<[^>]+>|\s+", " ", m.group(5)).strip()
            slots.append({
                "id": m.group(1),
                "time": m.group(2).strip(),
                "duration": m.group(3).strip(),
                "court": html.unescape(m.group(4)).strip(),
                "type": court_type,
                "price": html.unescape(price),
            })
    return slots


def is_double_court(slot: dict) -> bool:
    t, c = slot["type"].lower(), slot["court"].lower()
    return "dubbel" in t and "enkel" not in t and "single" not in c and "enkel" not in c


def booking_url(date_str: str) -> str:
    """Deeplink: opent Meet & Play op Padel, juiste datum, dagdeel avond."""
    return f"{CLUB_URL}?date={date_str}&sport=padel&dayPart=evening"


def find_available() -> tuple[list[dict], list[dict]]:
    mp = MeetAndPlay()
    now = dt.datetime.now()
    found, days = [], []
    for offset in range(DAYS_AHEAD + 1):
        day = now.date() + dt.timedelta(days=offset)
        if day.weekday() not in WEEKDAYS:
            continue
        date_str = day.strftime("%d-%m-%Y")
        days.append({"date": date_str, "iso": day.isoformat(), "weekday": WEEKDAYS[day.weekday()],
                     "booking_url": booking_url(date_str)})
        frag = mp.update({"tenantSportId": SPORT_PADEL, "roofType": ROOF,
                          "dayPart": "evening", "date": date_str})
        if mp.state.get("date") != date_str:
            log(f"{date_str}: datum niet geaccepteerd (buiten boekingsvenster?)")
            continue
        offered = re.findall(r'wire:key="start-time-([\d:]+)"', frag)
        wanted = [t for t in START_TIMES if t in offered]
        log(f"{date_str} ({WEEKDAYS[day.weekday()]}): starttijden {offered or '-'}; relevant {wanted or '-'}")
        for t in wanted:
            frag = mp.update({"time": t})
            if mp.state.get("time") != t:   # site valt terug op eerste tijd als t niet kan
                continue
            for s in parse_slots(frag):
                if s["time"].startswith(t) and is_double_court(s):
                    s.update(date=date_str, weekday=WEEKDAYS[day.weekday()], start=t)
                    found.append(s)
    return found, days


# ---- State & Slack --------------------------------------------------------
def slot_key(s: dict) -> str:
    return f'{s["date"]} {s["start"]} {s["court"]} {s["duration"]}'


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def send_slack(text: str) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        raise RuntimeError("SLACK_WEBHOOK_URL ontbreekt (zet hem in .env)")
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status != 200:
            raise RuntimeError(f"Slack gaf status {r.status}")


def nice_date(date_str: str) -> str:
    d = dt.datetime.strptime(date_str, "%d-%m-%Y")
    return f"{d.day} {NL_MONTHS[d.month - 1]}"


def sort_key(s: dict):
    return (dt.datetime.strptime(s["date"], "%d-%m-%Y"), s["start"], s["court"])


def format_message(slots: list[dict]) -> str:
    lines = [":tennis: *Padelbaan vrij bij De Geeren!*"]
    for s in sorted(slots, key=sort_key):
        lines.append(f'• *{s["weekday"].capitalize()} {nice_date(s["date"])}* {s["time"]} '
                     f'({s["duration"]}) – {s["court"]} – {s["price"]} – <{booking_url(s["date"])}|boek>')
    lines.append(f"<{PWA_URL}|Open de padel-app>")
    return "\n".join(lines)


def write_slots_file(slots: list[dict], days: list[dict]) -> None:
    """Schrijf docs/slots.json voor de PWA (alleen als de inhoud verandert)."""
    out = {"club_url": CLUB_URL, "start_times": START_TIMES, "days": []}
    for d in days:
        day_slots = [{k: s[k] for k in ("start", "time", "duration", "court", "price")}
                     for s in sorted(slots, key=sort_key) if s["date"] == d["date"]]
        out["days"].append({**d, "slots": day_slots})
    text = json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    if not SLOTS_FILE.exists() or SLOTS_FILE.read_text() != text:
        SLOTS_FILE.parent.mkdir(exist_ok=True)
        SLOTS_FILE.write_text(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="niet naar Slack sturen, state niet aanpassen")
    ap.add_argument("--test-slack", action="store_true", help="stuur een testbericht naar Slack")
    args = ap.parse_args()
    load_env()

    if args.test_slack:
        send_slack(":white_check_mark: Testbericht van de padelbaan-checker voor De Geeren.")
        log("Testbericht verstuurd")
        return 0

    state = load_state()
    try:
        slots, days = find_available()
    except Exception as e:  # netwerk- of site-wijziging
        log(f"FOUT: {e!r}")
        if args.dry_run:
            return 1
        state["failures"] = state.get("failures", 0) + 1
        if state["failures"] == FAILURES_BEFORE_ALERT:
            try:
                send_slack(f":warning: Padelbaan-checker faalt al {FAILURES_BEFORE_ALERT}x op rij: `{e!r}`")
            except Exception as e2:
                log(f"Slack-waarschuwing mislukt: {e2!r}")
        save_state(state)
        return 1

    current = {slot_key(s): s for s in slots}
    seen = set(state.get("available", []))
    new = [s for k, s in current.items() if k not in seen]
    log(f"{len(current)} geschikte baan/banen vrij, waarvan {len(new)} nieuw")
    for s in slots:
        log(f"  {s['weekday']} {s['date']} {s['time']} {s['court']} ({s['type']}, {s['price']})")

    if args.dry_run:
        if new:
            print("\n--- Slack-bericht (niet verstuurd) ---\n" + format_message(new))
        return 0

    write_slots_file(slots, days)
    if new:
        send_slack(format_message(new))
        log("Slack-melding verstuurd")
    # Onthoud wat nu vrij is; een baan die weer bezet raakt en later vrijkomt wordt opnieuw gemeld.
    state["available"] = sorted(current)
    state["failures"] = 0
    state.pop("last_run", None)  # niet opslaan: zou elke run een commit opleveren
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
