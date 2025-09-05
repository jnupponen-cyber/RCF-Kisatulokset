#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RCF weekly podiums (ZwiftPower) -> Discord

- Hakee RCF-tiimin (ZwiftPower) tuoreet tulokset viimeisen 7 päivän ajalta
- Suodattaa podium-sijat (1–3), ryhmittelee kisakohtaisesti
- Postaa sunnuntai-iltana yhteenvedon Discordiin
- Pitää "weekly_seen.json" -tiedostoa, ettei samoja podiumeja postata uudelleen
- DEBUG-moodi ja selkeä virheilmoitus, jos cookie ohjaa login-sivulle
- ALWAYS_POST=1: tekee testipostauksen, vaikka podiumeja ei löytyisi
- IGNORE-LISTA: suodata tietyt nimet pois (ignore_list.json)
- EMOJIT: 🥇🥈🥉 podium-sijoituksiin
- OTSIKON PÄIVÄMÄÄRÄVÄLI: esim. "1.–7. syyskuuta 2025" (Helsingin aika)
- SATUNNAINEN ONNENTOIVOTUS: lisätään viestin loppuun
"""

from __future__ import annotations

import os
import re
import json
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional

import requests
from bs4 import BeautifulSoup

# --- Asetukset / polut ---
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "weekly_seen.json"
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

WISHES = [
    "Hyvää treeniviikkoa kaikille! 🚴‍♂️💨",
    "Onnea podium-sijoituksista ja tsemppiä ensi viikkoon! 🔥",
    "Hienoa, jatketaan samaan malliin! 👏",
    "Respect kaikille RCF-kuskeille, podiumilla tai ei 💙",
    "Hienosti meni! 🚴‍♀️🌟",
    "Tärkeintä ei ole voitto, vaan murskavoitto! 💪",
    "The difference between try and triumph is just a little umph! 💥",
    "Winning isn’t everything; it’s the only thing. 😎",
    "And that’s how you do it, folks! 🔥",
    "Well, nobody’s perfect. 🙃"
    "I guess practice does make perfect! 📈"
    "We are the champions, my friends! 🏆"
    "Second place is just the first loser. 😏",
    "Go hard or go home... well, see you at home then! 🛋️",
    "If you can’t win fair, draft better! 🚴‍♂️💨",
    "Podium today, excuses tomorrow. 🤷",
    "Winning isn’t everything… it’s just highly recommended. 😉",
    "No watts, no glory. ⚡",
    "It’s not about how you start, it’s about how you blame the trainer. 🔧",
    "Legs are temporary, pride is permanent. 💪",
    "Why ride smart when you can ride hard? 🤔"
]

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

    if DEBUG:
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

    podiums: List[Dict] = []
    new_ids: Set[str] = set()

    for r in all_results:
        if r["date"] < week_ago:
            continue
        if r["pos"] > 3:
            continue
        if r["rider"] in ignore_names:
            continue

        uid = f"{r['link']}|{r['rider']}|{r['pos']}|{r['date'].isoformat()}"
        if uid in seen:
            continue

        podiums.append(r)
        new_ids.add(uid)

    logd("weekly podiums:", len(podiums))

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
