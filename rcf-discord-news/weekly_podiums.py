#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RCF weekly podiums (ZwiftPower) -> Discord

- Hakee RCF-tiimin (ZwiftPower) tuoreet tulokset viimeisen 7 päivän ajalta
- Suodattaa podium-sijat (1–3), ryhmittelee kisakohtaisesti
- Postaa sunnuntai-iltana yhteenvedon Discordiin
- Pitää "weekly_seen.json" -tiedostoa, ettei samoja podiumeja posteta uudelleen
- DEBUG-moodi ja selkeä virheilmoitus, jos cookie ohjaa login-sivulle
- ALWAYS_POST=1: tekee testipostauksen, vaikka podiumeja ei löytyisi
- IGNORE-LISTA: suodata tietyt nimet pois (ignore_list.json)
- EMOJIT: 🥇🥈🥉 podium-sijoituksiin
- OTSIKON PÄIVÄMÄÄRÄVÄLI: esim. "1.–7. syyskuuta 2025" (Helsingin aika)
- SATUNNAINEN ONNENTOIVOTUS
- VAIN KISAT: Haetaan event-tyyppi tapahtumasivulta ja suodatetaan (Race/TT)
"""

from __future__ import annotations

import os
import re
import json
import random
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional

import requests
from bs4 import BeautifulSoup

# --- Asetukset / polut ---
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "weekly_seen.json"
EVENT_TYPE_CACHE_FILE = SCRIPT_DIR / "event_type_cache.json"
REQUEST_TIMEOUT = 20

TEAM_ID = os.environ.get("ZWIFTPOWER_TEAM_ID", "20561").strip()
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
COOKIE = os.environ.get("ZWIFTPOWER_COOKIE", "").strip()
DEBUG = os.environ.get("DEBUG", "0") == "1"
ALWAYS_POST = os.environ.get("ALWAYS_POST", "0") == "1"

BASE = "https://zwiftpower.com"
TEAM_URL = f"{BASE}/team.php?id={TEAM_ID}"
TZ_HKI = ZoneInfo("Europe/Helsinki")

# Kuukaudet suomeksi genetiivissä (1-indeksoitu)
FI_MONTHS_GEN = [
    "", "tammikuuta", "helmikuuta", "maaliskuuta", "huhtikuuta", "toukokuuta",
    "kesäkuuta", "heinäkuuta", "elokuuta", "syyskuuta", "lokakuuta", "marraskuuta", "joulukuuta"
]

# Satunnaiset onnentoivotukset
WISHES = [
    "Hyvää treeniviikkoa kaikille! 🚴‍♂️💨",
    "Onnea podium-sijoittajille – ja tsemppiä ensi viikkoon! 🔥",
    "Hienoa ajoa, jatketaan samaan malliin! 👏",
    "Respect kaikille RCF-kuskeille, podiumilla tai ei 💙",
    "Muistakaa nauttia ajamisesta – kisat jatkuu taas ensi viikolla! 🚴‍♀️🌟",
    "Kova työ palkitaan – pidetään polkimet pyörimässä! 💪",
    "Yhdessä ajetaan pidemmälle – kiitos kaikille mukana olleille! 🤝",
    "Mahtava meininki, jatketaan treenejä hyvällä fiiliksellä! 😎",
    "Uudet kisat, uudet mahdollisuudet – kohti seuraavaa podiumia! 🏁",
    "Pidetään pyöräily iloisena ja yhteisöllisenä – hyvä RCF! 🎉",
    "The difference between try and triumph is just a little umph! 💥",
    "Well, nobody’s perfect. 🙃",
    "And that’s how you do it, folks! 🎤",
    "I guess practice does make perfect! 📈",
    "We are the champions, my friends! 🏆",
    "A win-win situation — for me, at least! 😏",
]

# Event-tyypit: mitä sallitaan ja mitä ei
ALLOWED_EVENT_TYPES = {
    "race", "time trial", "tt", "road race", "criterium", "crit", "scratch"
}
DENY_EVENT_TYPES = {
    "group ride", "workout", "group workout", "training", "pace partner",
    "social ride", "fondo", "tour", "badge hunt"
}

def logd(*a):
    if DEBUG:
        print("[DEBUG]", *a)

def load_seen() -> Set[str]:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return set(data if isinstance(data, list) else [])
        except Exception as e:
            print(f"[WARN] Failed to read {STATE_FILE.name}: {e}")
    return set()

def save_seen(s: Set[str]) -> None:
    try:
        STATE_FILE.write_text(
            json.dumps(sorted(list(s)), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[WARN] Failed to write {STATE_FILE.name}: {e}")

def load_ignore_names(path: Path = SCRIPT_DIR / "ignore_list.json") -> Set[str]:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            ignore = set(map(str.strip, data.get("ignore", [])))
            return {n for n in ignore if n}
    except Exception as e:
        print(f"[WARN] Failed to load ignore_list.json: {e}")
    return set()

def fetch(url: str) -> Optional[str]:
    headers = {
        "User-Agent": "RCF Podiums Bot",
        "Accept": "text/html,application/xhtml+xml",
        "Cookie": COOKIE,
    }
    r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)

    if r.status_code >= 400:
        print(f"[WARN] ZwiftPower HTTP {r.status_code} for {url}")
        return None

    sc = r.headers.get("Set-Cookie")
    if sc:
        logd("response Set-Cookie:", sc)

    text = r.text or ""
    low = text.lower()

    if ("login" in low and "password" in low) or "ucp.php?mode=login" in low:
        print("[ERROR] ZwiftPower returned login page -> cookie invalid/expired.")
        return None

    if DEBUG and "team.php" in url:
        try:
            (SCRIPT_DIR / "last_team_page.html").write_text(text, encoding="utf-8")
            logd("Saved last_team_page.html for inspection.")
        except Exception as e:
            print(f"[WARN] Could not write last_team_page.html: {e}")

    return text

def parse_team_results(html: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict] = []

    tables = soup.find_all("table")
    for tbl in tables:
        rows = tbl.find_all("tr")
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue

            pos: Optional[int] = None
            for td in tds:
                m = re.match(r"^\s*(\d+)\s*$", td.get_text(" ", strip=True))
                if m:
                    try:
                        pos = int(m.group(1))
                        break
                    except Exception:
                        pass
            if not pos:
                continue

            dt_text: Optional[str] = None
            for td in tds:
                txt = td.get_text(" ", strip=True)
                if re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", txt):
                    dt_text = txt
                    break
            if not dt_text:
                continue

            ev_name, ev_link = None, None
            a = tr.find("a", href=True)
            if a and "events.php" in a["href"]:
                ev_name = a.get_text(" ", strip=True)
                ev_link = BASE + "/" + a["href"].lstrip("/")
            else:
                continue

            rider = None
            a2 = tr.find("a", href=re.compile(r"profile\.php\?z=\d+"))
            if a2:
                rider = a2.get_text(" ", strip=True)

            cat = None
            for td in tds:
                m = re.search(r"\b([ABCD])\b", td.get_text(" ", strip=True))
                if m:
                    cat = m.group(1)
                    break

            when = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
                try:
                    when = datetime.strptime(dt_text, fmt).replace(tzinfo=timezone.utc)
                    break
                except Exception:
                    pass
            if not when:
                continue

            results.append(
                {
                    "event": ev_name,
                    "date": when,
                    "rider": rider or "Unknown",
                    "pos": pos,
                    "category": cat or "?",
                    "link": ev_link,
                }
            )

    return results

def format_finnish_date_range(start_date, end_date) -> str:
    if start_date.year == end_date.year:
        if start_date.month == end_date.month:
            month = FI_MONTHS_GEN[end_date.month]
            return f"{start_date.day}.–{end_date.day}. {month} {end_date.year}"
        else:
            m1 = FI_MONTHS_GEN[start_date.month]
            m2 = FI_MONTHS_GEN[end_date.month]
            return f"{start_date.day}. {m1} – {end_date.day}. {m2} {end_date.year}"
    else:
        m1 = FI_MONTHS_GEN[start_date.month]
        m2 = FI_MONTHS_GEN[end_date.month]
        return f"{start_date.day}. {m1} {start_date.year} – {end_date.day}. {m2} {end_date.year}"

# --- Event type detection & cache ---

def _load_event_type_cache() -> Dict[str, str]:
    if EVENT_TYPE_CACHE_FILE.exists():
        try:
            return json.loads(EVENT_TYPE_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WARN] Failed to read {EVENT_TYPE_CACHE_FILE.name}: {e}")
    return {}

def _save_event_type_cache(cache: Dict[str, str]) -> None:
    try:
        EVENT_TYPE_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[WARN] Failed to write {EVENT_TYPE_CACHE_FILE.name}: {e}")

def _normalize_event_type(text: str) -> str:
    t = text.strip().lower()
    # yhtenäistä yleisiä muotoja
    t = t.replace("time-trial", "time trial")
    t = t.replace("tt race", "time trial")
    return t

def detect_event_type_from_html(html: str) -> str:
    """
    Yrittää päätellä tapahtuman tyypin sivun tekstistä.
    Palauttaa esim. 'race', 'time trial', 'group ride', 'workout', 'fondo', 'tour', 'unknown'
    """
    txt = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()

    # Ensisijaiset kiellot
    for kw in DENY_EVENT_TYPES:
        if kw in txt:
            return kw

    # Sallitut / kisat
    if "time trial" in txt or re.search(r"\btt\b", txt):
        return "time trial"
    if "criterium" in txt or "crit" in txt:
        return "crit"
    if "race" in txt or "road race" in txt or "scratch" in txt:
        return "race"

    # Muita yleisiä
    if "group ride" in txt or "social ride" in txt:
        return "group ride"
    if "workout" in txt or "group workout" in txt or "training" in txt:
        return "workout"
    if "pace partner" in txt:
        return "pace partner"
    if "fondo" in txt:
        return "fondo"
    if "tour" in txt:
        return "tour"
    if "badge hunt" in txt:
        return "badge hunt"

    return "unknown"

def get_event_type(ev_link: str, cache: Dict[str, str]) -> str:
    """
    Palauttaa normalized event-tyypin välimuistista tai hakee sivun.
    """
    if ev_link in cache:
        return cache[ev_link]

    html = fetch(ev_link)
    if not html:
        etype = "unknown"
    else:
        etype = detect_event_type_from_html(html)

    etype = _normalize_event_type(etype)
    cache[ev_link] = etype
    logd(f"event type: {etype} <- {ev_link}")
    return etype

def is_allowed_event_type(etype: str) -> bool:
    e = _normalize_event_type(etype)
    if e in DENY_EVENT_TYPES:
        return False
    if e in ALLOWED_EVENT_TYPES:
        return True
    # If unknown, be conservative -> do NOT include
    return False

# --- Discord build & post ---

def build_discord_embed(podiums: List[Dict]) -> Dict:
    by_event: Dict[Tuple[str, str], List[Dict]] = {}
    for r in podiums:
        by_event.setdefault((r["event"], r["link"]), []).append(r)

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines: List[str] = []
    for (ename, elink), items in sorted(by_event.items(), key=lambda x: x[0][0].lower()):
        items_sorted = sorted(items, key=lambda r: r["pos"])
        row = "\n".join([
            f"{medals.get(it['pos'], f'#{it['pos']}')} — {it['rider']} (Cat {it['category']})"
            for it in items_sorted
        ])
        lines.append(f"**[{ename}]({elink})**\n{row}")

    now_hki = datetime.now(TZ_HKI)
    week_end = now_hki.date()
    week_start = (now_hki - timedelta(days=6)).date()
    date_range = format_finnish_date_range(week_start, week_end)

    desc = "\n\n".join(lines) if lines else "Ei podiumeja tällä viikolla."
    wish = random.choice(WISHES)
    desc = f"{desc}\n\n_{wish}_"

    title = f"RCF – Viikon podiumit ({date_range})"

    embed = {
        "type": "rich",
        "title": title,
        "description": desc[:3900],
        "color": int("0x00BC8C", 16),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Ride Club Finland"},
    }
    return embed

def post_to_discord(embed: Dict) -> None:
    if not WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK_URL puuttuu.")
    payload = {"embeds": [embed]}
    r = requests.post(WEBHOOK, json=payload, timeout=REQUEST_TIMEOUT)
    if r.status_code >= 300:
        raise RuntimeError(f"Discord POST failed: {r.status_code} {r.text}")

# --- Main ---

def main() -> None:
    if not COOKIE:
        raise SystemExit("ZWIFTPOWER_COOKIE puuttuu (kirjautuneen istunnon cookie).")

    print(f"[INFO] Fetching team page: {TEAM_URL}")
    html = fetch(TEAM_URL)
    if not html:
        print("[ERROR] ZwiftPower fetch failed (cookie/verkko?).")
        return

    all_results = parse_team_results(html)
    logd("parsed results:", len(all_results))

    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    seen = load_seen()
    ignore_names = load_ignore_names()
    if DEBUG:
        logd("ignore_names:", sorted(ignore_names))

    # Lataa event-tyyppien välimuisti
    etype_cache = _load_event_type_cache()

    podiums: List[Dict] = []
    new_ids: Set[str] = set()

    # Muista jo tarkistetut event-linkit -> vähentää kutsuja
    checked_event_types: Dict[str, str] = {}

    for r in all_results:
        if r["date"] < week_ago:
            continue
        if r["pos"] > 3:
            continue
        if r["rider"] in ignore_names:
            continue

        # Tarkista event-tyyppi vain kerran per linkki
        ev_link = r["link"]
        etype = checked_event_types.get(ev_link)
        if not etype:
            etype = get_event_type(ev_link, etype_cache)
            checked_event_types[ev_link] = etype

        if not is_allowed_event_type(etype):
            logd(f"skip non-race event type '{etype}' for {ev_link}")
            continue

        uid = f"{ev_link}|{r['rider']}|{r['pos']}|{r['date'].isoformat()}"
        if uid in seen:
            continue

        podiums.append(r)
        new_ids.add(uid)

    # Tallenna mahdollisesti päivittynyt välimuisti
    _save_event_type_cache(etype_cache)

    logd("weekly podiums (races only):", len(podiums))

    if podiums or ALWAYS_POST:
        embed = build_discord_embed(podiums)
        try:
            post_to_discord(embed)
        except Exception as e:
            print(f"[ERROR] {e}")
            return
        if podiums:
            seen |= new_ids
            save_seen(seen)
        print("[INFO] Posted weekly podiums to Discord.")
    else:
        print("[INFO] No new podiums to post this week.")


if __name__ == "__main__":
    main()
