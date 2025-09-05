#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RCF weekly podiums (ZwiftPower) -> Discord

- Hakee RCF-tiimin (ZwiftPower) tuoreet tulokset viimeisen 7 päivän ajalta
- Suodattaa podium-sijat (1–3), ryhmittelee kisakohtaisesti
- Postaa sunnuntai-iltana yhteenvedon Discordiin
- Pitää "seen" -tiedostoa, ettei samoja podiumeja postata uudelleen

ENV:
  DISCORD_WEBHOOK_URL  (pakollinen)
  ZWIFTPOWER_COOKIE    (pakollinen; esim. "PHPSESSID=abc...; <muu_cookie>=...")
  ZWIFTPOWER_TEAM_ID   (oletus 20561 – RCF)
  DEBUG                ("1" näyttää diagnostiikan)
"""

import os, re, json, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "weekly_seen.json"
REQUEST_TIMEOUT = 20
TEAM_ID = os.environ.get("ZWIFTPOWER_TEAM_ID", "20561").strip()
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
COOKIE = os.environ.get("ZWIFTPOWER_COOKIE", "").strip()
DEBUG = os.environ.get("DEBUG", "0") == "1"

BASE = "https://zwiftpower.com"
TEAM_URL = f"{BASE}/team.php?id={TEAM_ID}"

def logd(*a): 
    if DEBUG: 
        print("[DEBUG]", *a)

def load_seen():
    if STATE_FILE.exists():
        try: return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except: pass
    return set()

def save_seen(s):
    STATE_FILE.write_text(json.dumps(sorted(list(s)), ensure_ascii=False, indent=2), encoding="utf-8")

def fetch(url:str) -> str|None:
    headers = {
        "User-Agent": "RCF Podiums Bot",
        "Accept": "text/html,application/xhtml+xml",
        "Cookie": COOKIE
    }
    r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    if r.status_code >= 400: 
        print(f"[WARN] ZwiftPower HTTP {r.status_code} for {url}")
        return None
    return r.text

def parse_team_results(html:str) -> list[dict]:
    """
    Palauttaa listan tuloksista:
      { 'event': 'Event name', 'date': datetime, 'rider': 'Name', 'pos': 1, 'category': 'B', 'link': 'https://...' }
    Toteutus parsii tiimisivun "Recent Results" -osuutta; varmistamme selektorit varovasti.
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Yritetään löytää "Recent Results" -taulukko. ZwiftPowerin HTML muuttuu ajoittain,
    # joten etsimme rivejä, joissa on position-numero + linkki eventtiin + rider-nimi.
    tables = soup.find_all("table")
    for tbl in tables:
        rows = tbl.find_all("tr")
        # Heuristiikka: rivillä on 4–8 <td>, joista yhdessä on #/sija, yhdessä päivämäärä, yhdessä event linkki, yhdessä kuski
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 4: 
                continue

            # Löydä position
            pos = None
            for td in tds:
                m = re.match(r"^\s*(\d+)\s*$", td.get_text(" ", strip=True))
                if m:
                    pos = int(m.group(1))
                    break
            if not pos: 
                continue

            # Date
            dt = None
            for td in tds:
                txt = td.get_text(" ", strip=True)
                # ZwiftPower käyttää usein muotoja: 2025-09-04 18:45:00 / 04/09/2025 ...
                if re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", txt):
                    dt = txt
                    break
            if not dt:
                continue

            # Event + link
            ev_name, ev_link = None, None
            a = tr.find("a", href=True)
            if a and "events.php" in a["href"]:
                ev_name = a.get_text(" ", strip=True)
                ev_link = BASE + "/" + a["href"].lstrip("/")
            else:
                continue

            # Rider
            rider = None
            # Etsi linkkejä profile.php?z=...
            a2 = tr.find("a", href=re.compile(r"profile\.php\?z=\d+"))
            if a2:
                rider = a2.get_text(" ", strip=True)

            # Category (heikko heuristiikka: etsi ' A ' / ' B ' / ' C ' / ' D ' jostain solusta)
            cat = None
            for td in tds:
                m = re.search(r"\b([ABCD])\b", td.get_text(" ", strip=True))
                if m:
                    cat = m.group(1)
                    break

            # Päiväyksen parserointi
            when = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
                try:
                    when = datetime.strptime(dt, fmt).replace(tzinfo=timezone.utc)
                    break
                except: 
                    pass
            if not when:
                continue

            results.append({
                "event": ev_name,
                "date": when,
                "rider": rider or "Unknown",
                "pos": pos,
                "category": cat or "?",
                "link": ev_link
            })
    return results

def build_discord_embed(podiums: list[dict]) -> dict:
    # Ryhmittele eventeittäin
    by_event = {}
    for r in podiums:
        by_event.setdefault((r["event"], r["link"]), []).append(r)

    lines = []
    for (ename, elink), items in sorted(by_event.items(), key=lambda x: x[0][0].lower()):
        items_sorted = sorted(items, key=lambda r: r["pos"])
        row = "\n".join([f"#{it['pos']} — {it['rider']} (Cat {it['category']})" for it in items_sorted])
        lines.append(f"**[{ename}]({elink})**\n{row}")

    desc = "\n\n".join(lines) if lines else "Ei podiumeja tällä viikolla."
    embed = {
        "type": "rich",
        "title": "RCF – Viikon podiumit (ZwiftPower)",
        "description": desc[:3900],
        "color": int("0x00BC8C", 16),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    return embed

def post_to_discord(embed: dict):
    if not WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK_URL puuttuu.")
    payload = {"embeds": [embed]}
    r = requests.post(WEBHOOK, json=payload, timeout=REQUEST_TIMEOUT)
    if r.status_code >= 300:
        raise RuntimeError(f"Discord POST failed: {r.status_code} {r.text}")

def main():
    if not COOKIE:
        raise SystemExit("ZWIFTPOWER_COOKIE puuttuu (kirjautuneen istunnon cookie).")

    seen = load_seen()
    html = fetch(TEAM_URL)
    if not html:
        print("[WARN] ZwiftPower fetch failed.")
        return

    all_results = parse_team_results(html)
    logd("parsed results:", len(all_results))

    # Viimeisen 7 päivän tulokset ja podiumit 1–3
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    podiums = []
    new_ids = set()

    for r in all_results:
        if r["date"] < week_ago: 
            continue
        if r["pos"] > 3:
            continue
        # yksilöllinen avain (event link + rider + pos + date)
        uid = f"{r['link']}|{r['rider']}|{r['pos']}|{r['date'].isoformat()}"
        if uid in seen:
            continue
        podiums.append(r)
        new_ids.add(uid)

    logd("weekly podiums:", len(podiums))

    if podiums:
        embed = build_discord_embed(podiums)
        post_to_discord(embed)
        # merkitse nähdyiksi
        seen |= new_ids
        save_seen(seen)
    else:
        logd("no new podiums to post")

if __name__ == "__main__":
    main()
