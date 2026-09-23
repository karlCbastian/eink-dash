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
DIVIDER_X = 360  # vänster: text, höger: väderbild

# Kalender/match-funktionaliteten hålls igång i collect() men ritas inte just
# nu — layouten har ingen plats reserverad för dem. Sätt True för att slå på.
SHOW_CALENDAR = False
SHOW_MATCHES = False

# Tillfällig testkrok: satt till ett SMHI-väderkod visar den bilden på nästa
# körning utan att behöva ssha in och köra --force-code manuellt. Nolla till
# None igen när du sett hur den ser ut på panelen.
DEBUG_FORCE_CODE = 21

IMAGES_DIR = Path(__file__).with_name("images")

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

def wrap(d, text, f, maxw, maxlines=2):
    """Dela upp text i rader som ryms inom maxw. Kapar vid maxlines rader."""
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
            if len(lines) >= maxlines - 1:
                break
    if cur:
        lines.append(" ".join(cur))
    return lines or [""]

_WSYMB_SEVERITY = {
    1: 1, 2: 2, 3: 2, 4: 3, 5: 3, 6: 3, 7: 4,
    8: 5, 9: 6, 10: 7, 11: 9,
    12: 5, 13: 6, 14: 7,
    15: 5, 16: 6, 17: 7,
    18: 6, 19: 7, 20: 8, 21: 9,
    22: 6, 23: 7, 24: 8,
    25: 6, 26: 7, 27: 8,
}

_WEATHER_IMAGE = {
    1: "01_soligt",
    2: "02_delvis_molnigt", 3: "02_delvis_molnigt",
    4: "03_mulet", 5: "03_mulet", 6: "03_mulet",
    7: "07_dimma",
    8: "04_regn", 9: "04_regn", 10: "04_regn",
    11: "06_aska",
    12: "05_sno", 13: "05_sno", 14: "05_sno",
    15: "05_sno", 16: "05_sno", 17: "05_sno",
    18: "04_regn", 19: "04_regn", 20: "04_regn",
    21: "06_aska",
    22: "05_sno", 23: "05_sno", 24: "05_sno",
    25: "05_sno", 26: "05_sno", 27: "05_sno",
}

_WHITE_CLIP = 235  # gråvärden över detta blir rent vitt innan dithering

def draw_weather_image(img, box, code):
    """Fyller box (x0,y0,x1,y1) med väderbilden för koden, beskuren (inte
    utsträckt) så proportionerna hålls. Dithras till 1-bit med
    Floyd-Steinberg — panelen ser bara svart/vitt. Nästan-vita partier
    (himmel, papperstextur i källbilderna) klipps till rent vitt först,
    annars prickar dithringen ner dem i onödan."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    stam = _WEATHER_IMAGE.get(code, "01_soligt")
    src = Image.open(IMAGES_DIR / f"{stam}.jpg").convert("L")
    scale = max(bw / src.width, bh / src.height)
    src = src.resize((round(src.width * scale), round(src.height * scale)), Image.LANCZOS)
    # högerjusterad beskärning — motivet (träd/klippor) sitter på högerkanten
    # i alla bilderna, en centrerad beskärning skulle tappa det
    left, top = src.width - bw, (src.height - bh) // 2
    src = src.crop((left, top, left + bw, top + bh))
    src = src.point(lambda p: 255 if p >= _WHITE_CLIP else p)
    img.paste(src.convert("1"), (x0, y0))


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


GATOR = Path(__file__).with_name("gator.txt")

def get_gata():
    """Dagens gåta ur gator.txt. En 'fråga|svar' per rad, roterar på dagnummer
    så listan aldrig tar slut. Returnerar (fråga, svar) eller None."""
    rader = [
        r.strip() for r in GATOR.read_text(encoding="utf-8").splitlines()
        if r.strip() and not r.lstrip().startswith("#")
    ]
    if not rader:
        return None
    rad = rader[datetime.now(TZ).timetuple().tm_yday % len(rader)]
    fraga, _, svar = rad.partition("|")
    return fraga.strip(), svar.strip()
# --- rendering ------------------------------------------------------------

STROKE_W = 3  # vit kontur runt texten, så den syns oavsett vad som ligger bakom

def render(data):
    # Mode "1" ger osuddig text. Renderar du i "L" och konverterar sedan
    # får du dithering på bokstäverna och allt ser grumligt ut.
    img = Image.new("1", (W, H), 255)

    # väderbild, hela panelen. Ritas före texten så texten hamnar ovanpå —
    # texten konturas (vit kant) så den syns även mot mörka partier i bilden.
    w = data["weather"]
    draw_weather_image(img, (0, 0, W, H), w.get("code", 0))

    d = ImageDraw.Draw(img)
    textw = DIVIDER_X - 24 - 24  # bredd att radbryta text emot i vänsterspalten

    def txt(xy, s, f, **kw):
        d.text(xy, s, font=f, fill=0, stroke_width=STROKE_W, stroke_fill=255, **kw)

    def rule(y):
        # vit halo under den svarta linjen, annars försvinner den mot mörka partier
        d.line([(24, y), (DIVIDER_X - 24, y)], fill=255, width=2 + 2 * STROKE_W)
        d.line([(24, y), (DIVIDER_X - 24, y)], fill=0, width=2)

    # header
    now = datetime.now(TZ)
    dagar = ["Måndag", "Tisdag", "Onsdag", "Torsdag", "Fredag", "Lördag", "Söndag"]
    txt((24, 14), f"{dagar[now.weekday()].upper()} {now.day}/{now.month}", font(28, True))
    txt((W - 24, 20), now.strftime("uppd %H:%M"), font(20), anchor="ra")
    rule(58)

    # väder
    temp_f = font(72, True)
    temp_str = f"{w['temp']:.0f}°"
    txt((24, 74), temp_str, temp_f)
    temp_w = int(d.textlength(temp_str, font=temp_f))
    wx_f = font(28, True)
    wx_x = 24 + temp_w + 14
    wx_str = fit(d, WSYMB.get(w.get("code", 0), ""), wx_f, (DIVIDER_X - 24) - wx_x)
    txt((wx_x, 112), wx_str, wx_f)
    minmax_f = font(20)
    range_str = f"{w['low']:.0f}° / {w['high']:.0f}°"
    txt((24, 162), range_str, minmax_f)
    range_w = int(d.textlength(range_str, font=minmax_f))
    forecast_wx = WSYMB.get(w.get("forecast_code", 0), "")
    label = f"kommande 12h · {forecast_wx}" if forecast_wx else "kommande 12h"
    label_x = 24 + range_w + 10
    # får gå ut över bilden — texten är konturerad och läsbar ändå
    txt((label_x, 162), fit(d, label, minmax_f, (W - 24) - label_x), minmax_f)

    rule(200)

    # skolmaten
    etikett, meny = data["lunch"]
    if meny:
        txt((24, 210), f"SKOLMATEN {etikett}".strip(), font(18, True))
        meny_f = font(20)
        my = 236
        for line in wrap(d, meny, meny_f, textw, maxlines=4):
            if my >= 332:
                break
            txt((24, my), fit(d, line, meny_f, textw), meny_f)
            my += 24

    rule(344)

    # dagens gåta. Frågan hela dagen, svaret först efter kl 14 — barnen får klura.
    if data["gata"]:
        fraga, svar = data["gata"]
        txt((24, 354), "DAGENS GÅTA", font(18, True))
        gata_f = font(20)
        y = 378
        for line in wrap(d, fraga, gata_f, textw, maxlines=2):
            txt((24, y), line, gata_f)
            y += 24
        if svar and now.hour >= 14:
            txt((24, y + 4), fit(d, f"Svar: {svar}", font(18, True), textw), font(18, True))

    if SHOW_CALENDAR:
        ev_f = font(20)
        ev_fb = font(20, True)

        def cal_section(label, events, max_rows, y_max):
            nonlocal y
            txt((24, y), label, font(18, True))
            y += 24
            for tid, text in events[:max_rows]:
                if y >= y_max:
                    break
                lines = wrap(d, text, ev_f, textw)
                txt((24, y), tid, ev_fb)
                for line in lines:
                    txt((108, y), line, ev_f)
                    y += 22
                y += 4
            if not events:
                txt((24, y), "Inget inbokat", ev_f)
                y += 26

        y = 78
        cal_section("IDAG", data["events"], 3, y_max=185)
        y += 8
        cal_section("IMORGON", data["tomorrow"], 2, y_max=290)

    if SHOW_MATCHES:
        txt((24, 306), "NÄSTA MATCH", font(18, True))
        y = 328
        for vem, text in data["matches"]:
            txt((24, y), vem, font(18, True))
            txt((160, y), fit(d, text, font(18), textw - 136), font(18))
            y += 26

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
        "gata": safe(get_gata, None),
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
    ap.add_argument("--force-code", type=int,
                     help="tvinga ett SMHI-väderkod för test, t.ex. 21 för åska")
    args = ap.parse_args()

    load_env()
    data = collect()
    force_code = args.force_code if args.force_code is not None else DEBUG_FORCE_CODE
    if force_code is not None:
        data["weather"]["code"] = force_code
        data["weather"]["forecast_code"] = force_code
    img = render(data)
    if args.preview:
        img.save("preview.png")
        print("preview.png")
    else:
        push(img)
