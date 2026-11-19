#!/usr/bin/env python3
"""
Checkt bij padelclubs rond Soest of er een indoor dubbelbaan (geen single court)
vrij is op woensdag- of donderdagavond met begintijd 20:00, 20:30 of 21:00.
Nieuwe vrije banen worden gemeld via een Slack Incoming Webhook, en
docs/slots.json wordt bijgewerkt voor de PWA.

Automatisch gecheckt:
  - Racketcentrum De Geeren, Baarn   (KNLTB Meet & Play)
  - Peakz Padel Amersfoort           (Foys, openbare API)
Alleen als snelkoppeling in de app (Playtomic blokkeert geautomatiseerde toegang):
  - Plaza Padel Amersfoort, Plaza Padel Laren

Gebruik:
  python3 padel_check.py              # check en meld nieuwe vrije banen in Slack
  python3 padel_check.py --dry-run    # check en print alleen, geen Slack, niets opslaan
  python3 padel_check.py --test-slack # stuur een testbericht naar Slack

Configuratie: SLACK_WEBHOOK_URL in .env naast dit script (of als omgevingsvariabele).
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
import urllib.parse
import urllib.request
from pathlib import Path

# ---- Instellingen ---------------------------------------------------------
WEEKDAYS = {2: "woensdag", 3: "donderdag"}       # Python: ma=0 ... zo=6
START_TIMES = ["20:00", "20:30", "21:00"]
FAILURES_BEFORE_ALERT = 3                        # na zoveel mislukte runs op rij: melding

CLUBS = [
    {   # reserveren kan 10 dagen vooruit
        "key": "degeeren", "name": "De Geeren Baarn", "provider": "meetandplay",
        "days_ahead": 10, "club_url": "https://meetandplay.nl/club/88192",
    },
    {   # Peakz Padel CUPRA Padel Dome, Middelhoefseweg 10
        "key": "peakz-amersfoort", "name": "Peakz Amersfoort", "provider": "foys",
        "days_ahead": 14,
        "organisation_id": "df82f4dd-fd87-4af5-9c2f-656fe1a44357",
        "location_id": "527bd7b9-d8d3-4c43-a2cb-997e5baa0527",
        "location_slug": "cupra-padel-dome",
        "reservation_type_id": 6,   # "Baanreservering"
    },
]
# Niet automatisch te checken; wel een snelkoppeling in de app.
LINK_ONLY_CLUBS = [
    {"key": "plaza-amersfoort", "name": "Plaza Padel Amersfoort",
     "url": "https://playtomic.com/clubs/plaza-padel-amersfoort"},
    {"key": "plaza-laren", "name": "Plaza Padel Laren",
     "url": "https://playtomic.com/clubs/plaza-padel-laren"},
]

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


def candidate_days(days_ahead: int) -> list[dt.date]:
    today = dt.date.today()
    return [d for d in (today + dt.timedelta(days=i) for i in range(days_ahead + 1))
            if d.weekday() in WEEKDAYS]


def euro(amount: float) -> str:
    return f"€ {amount:.2f}".replace(".", ",")


def booking_url(club: dict, day: dt.date) -> str:
    if club["provider"] == "meetandplay":
        # opent Meet & Play op Padel, juiste datum, dagdeel avond
        return f'{club["club_url"]}?date={day:%d-%m-%Y}&sport=padel&dayPart=evening'
    if club["provider"] == "foys":
        return ("https://www.peakzpadel.nl/reserveren/court-booking/reservation?"
                + urllib.parse.urlencode({"daypart": "---", "date": day.isoformat(),
                                          "location": club["location_slug"]}))
    raise ValueError(club["provider"])


# ---- Meet & Play (Livewire) ----------------------------------------------
class MeetAndPlay:
    """Praat met de Livewire-component 'club.v2.court-show' op de clubpagina."""
    UPDATE_URL = "https://meetandplay.nl/livewire/update"

    def __init__(self, club_url: str) -> None:
        self.club_url = club_url
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with self.opener.open(urllib.request.Request(club_url, headers=UA), timeout=30) as r:
            page = r.read().decode("utf-8")
        m = re.search(r'data-csrf="([^"]+)"', page)
        if not m:
            raise RuntimeError("CSRF-token niet gevonden op clubpagina")
        self.csrf = m.group(1)
        snaps = [html.unescape(s) for s in re.findall(r'wire:snapshot="([^"]*)"', page)]
        snaps = [s for s in snaps if '"club.v2.court-show"' in s]
        if not snaps:
            raise RuntimeError("Livewire-component 'club.v2.court-show' niet gevonden")
        self.snapshot = snaps[0]

    def update(self, updates: dict) -> str:
        body = {"_token": self.csrf,
                "components": [{"snapshot": self.snapshot, "updates": updates, "calls": []}]}
        req = urllib.request.Request(
            self.UPDATE_URL, data=json.dumps(body).encode(),
            headers={**UA, "Content-Type": "application/json", "X-Livewire": "true",
                     "X-CSRF-TOKEN": self.csrf, "Referer": self.club_url})
        with self.opener.open(req, timeout=30) as r:
            comp = json.loads(r.read())["components"][0]
        self.snapshot = comp["snapshot"]
        return comp["effects"].get("html", "")

    @property
    def state(self) -> dict:
        return json.loads(self.snapshot)["data"]


def parse_meetandplay_slots(fragment: str) -> list[dict]:
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
                "time": m.group(2).strip(),
                "duration": int(re.sub(r"\D", "", m.group(3)) or 0),
                "court": html.unescape(m.group(4)).strip(),
                "type": court_type,
                "price": html.unescape(price),
            })
    return slots


def is_double_meetandplay(slot: dict) -> bool:
    t, c = slot["type"].lower(), slot["court"].lower()
    return "dubbel" in t and "enkel" not in t and "single" not in c and "enkel" not in c


def check_meetandplay(club: dict) -> list[dict]:
    mp = MeetAndPlay(club["club_url"])
    found = []
    for day in candidate_days(club["days_ahead"]):
        date_str = day.strftime("%d-%m-%Y")
        frag = mp.update({"tenantSportId": 2, "roofType": "INDOOR",   # 2 = padel
                          "dayPart": "evening", "date": date_str})
        if mp.state.get("date") != date_str:
            log(f"  {date_str}: datum niet geaccepteerd (buiten boekingsvenster?)")
            continue
        offered = re.findall(r'wire:key="start-time-([\d:]+)"', frag)
        wanted = [t for t in START_TIMES if t in offered]
        log(f"  {date_str}: starttijden {offered or '-'}; relevant {wanted or '-'}")
        for t in wanted:
            frag = mp.update({"time": t})
            if mp.state.get("time") != t:   # site valt terug op eerste tijd als t niet kan
                continue
            for s in parse_meetandplay_slots(frag):
                if s["time"].startswith(t) and is_double_meetandplay(s):
                    found.append({"day": day, "start": t, "time": s["time"], "court": s["court"],
                                  "duration": s["duration"], "price": s["price"]})
    return found


# ---- Foys (Peakz) ---------------------------------------------------------
def check_foys(club: dict) -> list[dict]:
    base = "https://api.foys.io/court-booking/public/api/v1/locations/search"
    headers = {**UA, "Accept": "application/json",
               "X-FederationID": club["organisation_id"],
               "X-OrganisationId": club["organisation_id"]}
    found = []
    for day in candidate_days(club["days_ahead"]):
        qs = urllib.parse.urlencode([
            ("reservationTypeId", club["reservation_type_id"]),
            ("locationId", club["location_id"]),
            ("playingTimes[]", 60), ("playingTimes[]", 90), ("playingTimes[]", 120),
            ("date", f"{day.isoformat()}T00:00:00.000Z"),
        ])
        with urllib.request.urlopen(urllib.request.Request(f"{base}?{qs}", headers=headers),
                                    timeout=30) as r:
            locations = json.loads(r.read())
        n_courts = n_free = 0
        for loc in locations:
            for court in loc.get("inventoryItemsTimeSlots", []):
                ctype = (court.get("type") or court.get("inventoryItemType", {}).get("name") or "").lower()
                if "double" not in ctype or "indoor" not in ctype:
                    continue
                n_courts += 1
                for ts in court.get("timeSlots", []):
                    start = ts["startTime"][11:16]
                    if ts["startTime"][:10] != day.isoformat() or start not in START_TIMES \
                            or not ts.get("isAvailable"):
                        continue
                    n_free += 1
                    found.append({"day": day, "start": start,
                                  "time": f'{start} - {ts["endTime"][11:16]}',
                                  "court": court["name"], "duration": ts["duration"],
                                  "price": euro(ts["price"])})
        log(f"  {day:%d-%m-%Y}: {n_courts} dubbelbanen, {n_free} vrije slot(s) op relevante tijden")
    return found


PROVIDERS = {"meetandplay": check_meetandplay, "foys": check_foys}


def merge_durations(raw: list[dict], club: dict) -> list[dict]:
    """Zelfde baan + starttijd met verschillende duur samenvoegen tot één slot."""
    grouped: dict[tuple, list[dict]] = {}
    for s in raw:
        grouped.setdefault((s["day"], s["start"], s["court"]), []).append(s)
    out = []
    for (day, start, court), items in grouped.items():
        items.sort(key=lambda s: s["duration"])
        first = items[0]
        out.append({
            "club": club["name"], "club_key": club["key"],
            "date": day.strftime("%d-%m-%Y"), "iso": day.isoformat(),
            "weekday": WEEKDAYS[day.weekday()], "start": start,
            "time": first["time"], "court": court,
            "durations": "/".join(str(s["duration"]) for s in items) + " min",
            "price": first["price"] + (f' ({first["duration"]} min)' if len(items) > 1 else ""),
            "booking_url": booking_url(club, day),
        })
    return out


# ---- State, Slack en slots.json ------------------------------------------
def slot_key(s: dict) -> str:
    return f'{s["club_key"]} {s["date"]} {s["start"]} {s["court"]}'


def sort_key(s: dict):
    return (s["iso"], s["start"], s["club"], s["court"])


def load_state() -> dict:
    try:
        state = json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    if not isinstance(state.get("failures"), dict):   # oud formaat (één teller)
        state["failures"] = {}
    # Oud formaat (alleen De Geeren): "25-11-2026 21:00 Baan 60 minuten" -> "degeeren 25-11-2026 21:00 Baan"
    keys = {c["key"] for c in CLUBS}
    state["available"] = [
        k if k.split(" ", 1)[0] in keys else "degeeren " + re.sub(r" \d+ minuten$", "", k)
        for k in state.get("available", [])]
    return state


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def send_slack(text: str) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        raise RuntimeError("SLACK_WEBHOOK_URL ontbreekt (zet hem in .env)")
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status != 200:
            raise RuntimeError(f"Slack gaf status {r.status}")


def nice_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.day} {NL_MONTHS[d.month - 1]}"


def format_message(slots: list[dict]) -> str:
    lines = [":tennis: *Padelbaan vrij!*"]
    for club in [c["name"] for c in CLUBS]:
        club_slots = sorted((s for s in slots if s["club"] == club), key=sort_key)
        if not club_slots:
            continue
        lines.append(f"*{club}*")
        for s in club_slots:
            lines.append(f'• {s["weekday"].capitalize()} {nice_date(s["iso"])} {s["time"]} '
                         f'({s["durations"]}) – {s["court"]} – {s["price"]} – <{s["booking_url"]}|boek>')
    lines.append(f"<{PWA_URL}|Open de padel-app>")
    return "\n".join(lines)


def write_slots_file(slots: list[dict], failed: list[str]) -> None:
    """Schrijf docs/slots.json voor de PWA (alleen als de inhoud verandert)."""
    max_ahead = max(c["days_ahead"] for c in CLUBS)
    out = {"start_times": START_TIMES, "errors": failed, "days": []}
    for day in candidate_days(max_ahead):
        links = [{"club": c["name"], "url": booking_url(c, day), "auto": True}
                 for c in CLUBS if day in candidate_days(c["days_ahead"])]
        links += [{"club": c["name"], "url": c["url"], "auto": False} for c in LINK_ONLY_CLUBS]
        out["days"].append({
            "date": day.strftime("%d-%m-%Y"), "iso": day.isoformat(),
            "weekday": WEEKDAYS[day.weekday()],
            "slots": [{k: s[k] for k in ("club", "start", "time", "durations", "court", "price",
                                         "booking_url")}
                      for s in sorted(slots, key=sort_key) if s["iso"] == day.isoformat()],
            "links": links,
        })
    text = json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    if not SLOTS_FILE.exists() or SLOTS_FILE.read_text() != text:
        SLOTS_FILE.parent.mkdir(exist_ok=True)
        SLOTS_FILE.write_text(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="niet naar Slack sturen, niets opslaan")
    ap.add_argument("--test-slack", action="store_true", help="stuur een testbericht naar Slack")
    args = ap.parse_args()
    load_env()

    if args.test_slack:
        send_slack(":white_check_mark: Testbericht van de padelbaan-checker.")
        log("Testbericht verstuurd")
        return 0

    state = load_state()
    seen = set(state.get("available", []))
    slots: list[dict] = []
    failed: list[str] = []
    keep: set[str] = set()     # bekende vrije banen van clubs die nu faalden
    alerts: list[str] = []
    for club in CLUBS:
        log(f"{club['name']}:")
        try:
            club_slots = merge_durations(PROVIDERS[club["provider"]](club), club)
        except Exception as e:  # netwerk- of site-wijziging; andere clubs gaan gewoon door
            log(f"  FOUT: {e!r}")
            failed.append(club["name"])
            n = state["failures"].get(club["key"], 0) + 1
            state["failures"][club["key"]] = n
            if n == FAILURES_BEFORE_ALERT:
                alerts.append(f":warning: Check bij {club['name']} faalt al {n}x op rij: `{e!r}`")
            keep |= {k for k in seen if k.startswith(club["key"] + " ")}
            continue
        state["failures"].pop(club["key"], None)
        slots.extend(club_slots)

    current = {slot_key(s): s for s in slots}
    new_keys = set(current) - seen
    new = [current[k] for k in new_keys]
    log(f"Totaal {len(current)} geschikte baan/banen vrij, waarvan {len(new)} nieuw")
    for s in sorted(slots, key=sort_key):
        log(f"  {s['club']}: {s['weekday']} {s['date']} {s['time']} {s['court']} "
            f"({s['durations']}, {s['price']}){'  [NIEUW]' if slot_key(s) in new_keys else ''}")

    if args.dry_run:
        if new:
            print("\n--- Slack-bericht (niet verstuurd) ---\n" + format_message(new))
        for a in alerts:
            print(a)
        return 0

    write_slots_file(slots, failed)
    if new:
        send_slack(format_message(new))
        log("Slack-melding verstuurd")
    for a in alerts:
        try:
            send_slack(a)
        except Exception as e:
            log(f"Slack-waarschuwing mislukt: {e!r}")
    # Onthoud wat nu vrij is; een baan die weer bezet raakt en later vrijkomt wordt opnieuw gemeld.
    state["available"] = sorted(set(current) | keep)
    state.pop("last_run", None)
    save_state(state)
    return 1 if len(failed) == len(CLUBS) else 0


if __name__ == "__main__":
    sys.exit(main())
