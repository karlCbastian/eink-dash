#!/usr/bin/env python3
"""E-ink dashboard, 800x480 Waveshare 7.5in V2.

Kör med --preview för att spara en PNG istället för att rita på panelen.
Det gör iterationen snabb: du slipper vänta 5 sekunder per refresh.
"""
import os
import sys
import argparse
import re
import feedparser
from datetime import datetime, timedelta, time, date
from zoneinfo import ZoneInfo
from pathlib import Path
import recurring_ical_events
from icalendar import Calendar

import requests
from PIL import Image, ImageDraw, ImageFont

W, H = 800, 480
LAT, LON = 59.43, 17.95  # Sollentuna

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def font(size, bold=False):
    return ImageFont.truetype(FONT_B if bold else FONT, size)


# --- källor ---------------------------------------------------------------

WSYMB = {
    1: "Klart", 2: "Lätt molnighet", 3: "Halvklart", 4: "Molnigt",
    5: "Mycket moln", 6: "Mulet", 7: "Dimma", 8: "Lätt regnskur",
    9: "Regnskur", 10: "Kraftig regnskur", 11: "Åskskur",
    12: "Lätt by av regn och snö", 13: "By av regn och snö",
    14: "Kraftig by av regn och snö", 15: "Lätt snöby", 16: "Snöby",
    17: "Kraftig snöby", 18: "Lätt regn", 19: "Regn", 20: "Kraftigt regn",
    21: "Åska", 22: "Lätt snöblandat regn", 23: "Snöblandat regn",
    24: "Kraftigt snöblandat regn", 25: "Lätt snöfall", 26: "Snöfall",
    27: "Kraftigt snöfall",
}

def load_env():
    """Läser .env bredvid scriptet. Gör att systemd-timern beter sig
    likadant som när du kör för hand."""
    f = Path(__file__).with_name(".env")
    if not f.exists():
        return
    for rad in f.read_text().splitlines():
        rad = rad.strip()
        if not rad or rad.startswith("#") or "=" not in rad:
            continue
        k, v = rad.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
def fit(d, text, f, maxbredd):
    """Kapar text som inte får plats. Bättre en trunkerad rad än
    bokstäver som rinner ut över kanten."""
    if d.textlength(text, font=f) <= maxbredd:
        return text
    while text and d.textlength(text + "…", font=f) > maxbredd:
        text = text[:-1]
    return text + "…"

def get_weather():
    """SMHI SNOW1gv1. Ersatte pmp3g som stängdes 2026-03-31."""
    url = ("https://opendata-download-metfcst.smhi.se/api/category/snow1g/"
           f"version/1/geotype/point/lon/{LON:.6f}/lat/{LAT:.6f}/data.json")
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    series = r.json()["timeSeries"]

    horizon = datetime.now(TZ) + timedelta(hours=12)
    upcoming = [
        s for s in series
        if datetime.fromisoformat(s["time"].replace("Z", "+00:00")) <= horizon
    ] or series[:1]
    temps = [s["data"]["air_temperature"] for s in upcoming]

    return {
        "temp": series[0]["data"]["air_temperature"],
        "desc": WSYMB.get(series[0]["data"].get("symbol_code"), "—"),
        "low": min(temps),
        "high": max(temps),
    }

TZ = ZoneInfo("Europe/Stockholm")
DAGAR = ["Mån", "Tis", "Ons", "Tor", "Fre", "Lör", "Sön"]

_cal = None


def _calendar():
    """Hämtas en gång per körning — båda rutorna delar samma feed."""
    global _cal
    if _cal is None:
        r = requests.get(os.environ["ICS_URL"], timeout=30)
        r.raise_for_status()
        _cal = Calendar.from_ical(r.text)
    return _cal

def _allday(e):
    return not isinstance(e["DTSTART"].dt, datetime)

def _start(ev):
    dt = ev["DTSTART"].dt
    if not isinstance(dt, datetime):          # heldagshändelse
        return datetime.combine(dt, time.min, tzinfo=TZ)
    return dt.astimezone(TZ) if dt.tzinfo else dt.replace(tzinfo=TZ)


def _between(days):
    now = datetime.now(TZ)
    evs = recurring_ical_events.of(_calendar()).between(now, now + timedelta(days=days))
    return sorted(evs, key=_start)



MATCHORD = ("match", "cup", "serie", "turnering", "sollentunafemman")

BARN = {"a": "Alfred", "f&t": "Folke & Ture", "t&f": "Folke & Ture"}
MARKOR = re.compile(r"\(\s*(a|f\s*&\s*t|t\s*&\s*f)\s*\)", re.IGNORECASE)

def _who(e):
    m = MARKOR.search(str(e.get("SUMMARY", "")))
    return BARN[m.group(1).replace(" ", "").lower()] if m else None

def _summary(e):
    s = str(e.get("SUMMARY", "")).strip()
    if s.lower().startswith("private appointment"):
        return "Upptaget"
    return MARKOR.sub("", s).strip(" -–—:")

def get_events():
    slut = datetime.now(TZ).replace(hour=23, minute=59)
    ut = []
    for e in _between(1):
        if _start(e) > slut:
            continue
        vem, namn = _who(e), _summary(e)
        tid = "Heldag" if _allday(e) else _start(e).strftime("%H:%M")
        ut.append((tid, f"{vem}: {namn}" if vem else namn))
    return ut


def get_matches():
    """Nästa match per barn. Returnerar en rad var, i BARN-ordning."""
    hittade = {}
    for e in _between(60):
        namn = _summary(e)
        if not any(o in namn.lower() for o in MATCHORD):
            continue
        vem = _who(e)
        if vem is None or vem in hittade:
            continue
        d = _start(e)
        plats = str(e.get("LOCATION", "")).strip()
        när = f"{DAGAR[d.weekday()]} {d.day}/{d.month}"
        if not _allday(e):
            när += f" {d:%H:%M}"
        rad = f"{DAGAR[d.weekday()]} {d.day}/{d.month} {d:%H:%M} — {namn}"
        hittade[vem] = f"{rad}, {plats}" if plats else rad
        if len(hittade) == len(BARN):
            break
    ordning = list(dict.fromkeys(BARN.values()))
    return [(v, hittade.get(v, "Ingen match inbokad")) for v in ordning]
def _lunch_days():
    """Feeden är en vecka. Bygg en karta datum -> rätter."""
    feed = feedparser.parse(os.environ["LUNCH_URL"])
    dagar = {}
    for e in feed.entries:
        pub = e.get("published_parsed")
        if not pub:
            continue
        text = re.sub(r"<[^>]+>", "\n", e.get("summary", ""))
        rätter = [r.strip() for r in text.splitlines() if r.strip()]
        if rätter:
            dagar[date(*pub[:3])] = " · ".join(rätter)
    return dagar


def get_lunch():
    """Före 14: dagens lunch. Efter: nästa dag som har en meny."""
    dagar = _lunch_days()
    nu = datetime.now(TZ)
    idag = nu.date()

    if nu.hour < 14 and idag in dagar:
        return "IDAG", dagar[idag]

    for i in range(1, 5):
        d = idag + timedelta(days=i)
        if d in dagar:
            etikett = "IMORGON" if i == 1 else DAGAR_LANG[d.weekday()].upper()
            return etikett, dagar[d]

    return "SKOLMATEN", "Ingen meny"
# --- rendering ------------------------------------------------------------

def render(data):
    # Mode "1" ger osuddig text. Renderar du i "L" och konverterar sedan
    # får du dithering på bokstäverna och allt ser grumligt ut.
    img = Image.new("1", (W, H), 255)
    d = ImageDraw.Draw(img)

    def rule(y):
        d.line([(24, y), (W - 24, y)], fill=0, width=2)

    # header
    now = datetime.now()
    dagar = ["Måndag", "Tisdag", "Onsdag", "Torsdag", "Fredag", "Lördag", "Söndag"]
    d.text((24, 14), dagar[now.weekday()].upper(), font=font(28, True), fill=0)
    d.text((W - 24, 20), now.strftime("uppd %H:%M"),
           font=font(20), fill=0, anchor="ra")
    rule(58)

    # väder, vänster
    w = data["weather"]
    d.text((24, 78), f"{w['temp']:.0f}°", font=font(96, True), fill=0)
    d.text((24, 190), w["desc"], font=font(24), fill=0)
    d.text((24, 226), f"{w['low']:.0f}° / {w['high']:.0f}°  kommande 12h",
           font=font(20), fill=0)
    d.line([(320, 70), (320, 290)], fill=0, width=1)

    # kalender, höger
    d.text((348, 78), "IDAG", font=font(20, True), fill=0)
    y = 112
    for tid, text in data["events"][:5]:
        d.text((348, y), tid, font=font(22, True), fill=0)
        d.text((424, y), text, font=font(22), fill=0)
        y += 34
    if not data["events"]:
        d.text((348, y), "Inget inbokat", font=font(22), fill=0)

    rule(300)

    # nästa match, en rad per barn
    d.text((24, 290), "NÄSTA MATCH", font=font(18, True), fill=0)
    y = 316
    for vem, text in data["matches"]:
        d.text((24, y), vem, font=font(22, True), fill=0)
        d.text((196, y), fit(d, text, font(22), W - 220), font=font(22), fill=0)
        y += 32

    rule(392)

    # skolmaten
    etikett, meny = data["lunch"]
    if meny:
        d.text((24, 408), f"SKOLMATEN {etikett}".strip(), font=font(20, True), fill=0)
        d.text((24, 436), fit(d, meny, font(26), W - 48), font=font(26), fill=0)

    return img


def collect():
    """Varje källa failar tyst. En trasig RSS ska inte svälja hela skärmen."""
    def safe(fn, fallback):
        try:
            return fn()
        except Exception as e:
            print(f"{fn.__name__}: {e}", file=sys.stderr)
            return fallback

    return {
        "weather": safe(get_weather, {"temp": 0, "desc": "—", "low": 0, "high": 0}),
        "events": safe(get_events, []),
	"matches": safe(get_matches, [(v, "—") for v in dict.fromkeys(BARN.values())]),
        "lunch": safe(get_lunch, "—"),
    }


def push(img):
    sys.path.append(os.path.expanduser("~/e-Paper/RaspberryPi_JetsonNano/python/lib"))
    from waveshare_epd import epd7in5_V2

    epd = epd7in5_V2.EPD()
    try:
        epd.init()
        epd.display(epd.getbuffer(img))
    finally:
        # Aldrig hoppa över. Panelen tar skada av att stå kvar i drivet läge.
        epd.sleep()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="spara PNG, rita inte")
    args = ap.parse_args()

    load_env()
    img = render(collect())
    if args.preview:
        img.save("preview.png")
        print("preview.png")
    else:
        push(img)
