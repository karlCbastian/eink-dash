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

if sys.platform == "win32":
    FONT = "C:/Windows/Fonts/arial.ttf"
    FONT_B = "C:/Windows/Fonts/arialbd.ttf"
else:
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

def wrap(d, text, f, maxw):
    """Dela upp text i rader som ryms inom maxw. Max 2 rader."""
    words = text.split()
    lines, cur = [], []
    for w in words:
        test = " ".join(cur + [w])
        if d.textlength(test, font=f) <= maxw:
            cur.append(w)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [w]
            if len(lines) >= 1:
                break
    if cur:
        lines.append(" ".join(cur))
    return lines or [""]

FONT_WX = str(Path(__file__).with_name("fonts") / "weathericons.ttf")

_WSYMB_GLYPH = {
    1:  "",                        # day-sunny
    2:  "",  3:  "",         # day-sunny-overcast
    4:  "",  5:  "",  6:  "",  # cloudy
    7:  "",                        # fog
    8:  "",  9:  "",  10: "",  # day-showers
    11: "",                        # day-thunderstorm
    12: "",  13: "",  14: "",  # day-sleet
    15: "",  16: "",  17: "",  # day-snow
    18: "",  19: "",  20: "",  # rain
    21: "",                        # thunderstorm
    22: "",  23: "",  24: "",  # sleet
    25: "",  26: "",  27: "",  # snow
}

def draw_weather_icon(img, x, y, sz, code):
    glyph = _WSYMB_GLYPH.get(code, "")
    f = ImageFont.truetype(FONT_WX, sz)
    # Rendera i gråskala och tröskling → skarpare 1-bit än direkt läge "1"
    tmp = Image.new("L", (sz + 20, sz + 20), 255)
    ImageDraw.Draw(tmp).text((10, 4), glyph, font=f, fill=0)
    bw = tmp.point(lambda p: 0 if p < 128 else 255, "1")
    img.paste(bw, (x - 10, y - 4))

_WSYMB_SEVERITY = {
    1: 1, 2: 2, 3: 2, 4: 3, 5: 3, 6: 3, 7: 4,
    8: 5, 9: 6, 10: 7, 11: 9,
    12: 5, 13: 6, 14: 7,
    15: 5, 16: 6, 17: 7,
    18: 6, 19: 7, 20: 8, 21: 9,
    22: 6, 23: 7, 24: 8,
    25: 6, 26: 7, 27: 8,
}

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

    code = series[0]["data"].get("symbol_code", 0)
    codes = [s["data"].get("symbol_code", 0) for s in upcoming]
    forecast_code = max(codes, key=lambda c: _WSYMB_SEVERITY.get(c, 0)) if codes else 0
    return {
        "temp": series[0]["data"]["air_temperature"],
        "code": code,
        "forecast_code": forecast_code,
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


def get_tomorrow_events():
    now = datetime.now(TZ)
    imorgon = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    slut = imorgon.replace(hour=23, minute=59)
    evs = recurring_ical_events.of(_calendar()).between(imorgon, slut)
    ut = []
    for e in sorted(evs, key=_start):
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
    temp_f = font(96, True)
    temp_str = f"{w['temp']:.0f}°"
    d.text((24, 78), temp_str, font=temp_f, fill=0)
    icon_x = 24 + int(d.textlength(temp_str, font=temp_f)) + 10
    draw_weather_icon(img, icon_x, 84, 72, w.get("code", 0))
    minmax_f = font(20)
    minmax_str = f"{w['low']:.0f}° / {w['high']:.0f}°  kommande 12h"
    d.text((24, 226), minmax_str, font=minmax_f, fill=0)
    fc_x = 24 + int(d.textlength(minmax_str, font=minmax_f)) + 18
    draw_weather_icon(img, fc_x, 218, 32, w.get("forecast_code", 0))
    d.line([(320, 70), (320, 290)], fill=0, width=1)

    # kalender, höger
    ev_f = font(20)
    ev_fb = font(20, True)
    maxw = W - 444

    def cal_section(label, events, max_rows, y_max):
        nonlocal y
        d.text((348, y), label, font=font(18, True), fill=0)
        y += 24
        for tid, text in events[:max_rows]:
            if y >= y_max:
                break
            lines = wrap(d, text, ev_f, maxw)
            d.text((348, y), tid, font=ev_fb, fill=0)
            for line in lines:
                d.text((432, y), line, font=ev_f, fill=0)
                y += 22
            y += 4
        if not events:
            d.text((348, y), "Inget inbokat", font=ev_f, fill=0)
            y += 26

    y = 78
    cal_section("IDAG", data["events"], 3, y_max=185)
    y += 8
    cal_section("IMORGON", data["tomorrow"], 2, y_max=290)

    rule(300)

    # nästa match, en rad per barn
    d.text((24, 306), "NÄSTA MATCH", font=font(18, True), fill=0)
    y = 328
    for vem, text in data["matches"]:
        d.text((24, y), vem, font=font(18, True), fill=0)
        d.text((160, y), fit(d, text, font(18), W - 184), font=font(18), fill=0)
        y += 26

    rule(392)

    # skolmaten
    etikett, meny = data["lunch"]
    if meny:
        d.text((24, 398), f"SKOLMATEN {etikett}".strip(), font=font(18, True), fill=0)
        meny_f = font(20)
        forsta = meny.split(" · ")[0]
        d.text((24, 424), fit(d, forsta, meny_f, W - 48), font=meny_f, fill=0)

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
        "weather": safe(get_weather, {"temp": 0, "code": 0, "forecast_code": 0, "low": 0, "high": 0}),
        "events": safe(get_events, []),
        "tomorrow": safe(get_tomorrow_events, []),
	"matches": safe(get_matches, [(v, "—") for v in dict.fromkeys(BARN.values())]),
        "lunch": safe(get_lunch, ("", "—")),
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
