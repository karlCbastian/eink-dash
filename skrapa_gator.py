#!/usr/bin/env python3
"""Skrapa gåtor från en sida till en textfil.

Körs lokalt. Kontrollera själv att sidans villkor/robots.txt tillåter det.

    pip install requests beautifulsoup4
    python skrapa_gator.py URL --inspect            # hitta rätt selektor
    python skrapa_gator.py URL -s ".gata" -o gator.txt
"""

import argparse
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; personal-scraper/1.0)"
KANDIDATER = [
    "article", ".entry-content p", ".post-content p", ".content li",
    "main li", "main p", "li", "p",
]


def hamta(url: str, delay: float = 1.0) -> BeautifulSoup:
    time.sleep(delay)
    r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return BeautifulSoup(r.text, "html.parser")


def texter(soup: BeautifulSoup, selector: str, minlen: int) -> list[str]:
    ut, sedda = [], set()
    for el in soup.select(selector):
        t = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if len(t) < minlen or t in sedda:
            continue
        sedda.add(t)
        ut.append(t)
    return ut


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("-s", "--selector", help="CSS-selektor för varje gåta")
    p.add_argument("-o", "--out", default="gator.txt")
    p.add_argument("--minlen", type=int, default=15, help="min tecken per träff")
    p.add_argument("--inspect", action="store_true", help="visa kandidatselektorer")
    a = p.parse_args()

    soup = hamta(a.url)

    if a.inspect or not a.selector:
        for sel in KANDIDATER:
            traffar = texter(soup, sel, a.minlen)
            print(f"{sel:<22} {len(traffar):>4} träffar")
            for t in traffar[:2]:
                print(f"   ex: {t[:90]}")
        print("\nVälj en och kör om med -s '<selektor>'")
        return 0

    rader = texter(soup, a.selector, a.minlen)
    if not rader:
        print("Inga träffar — prova --inspect", file=sys.stderr)
        return 1

    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(rader) + "\n")
    print(f"{len(rader)} rader → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())