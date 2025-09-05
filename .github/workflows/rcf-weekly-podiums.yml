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

ENV (GitHub Actions → Secrets / env):
  DISCORD_WEBHOOK_URL  (pakollinen)
  ZWIFTPOWER_COOKIE    (pakollinen; esim. "phpbb3_xxx_sid=...; phpbb3_xxx_u=...; phpbb3_xxx_k=")
  ZWIFTPOWER_TEAM_ID   (oletus 20561 – RCF)
  DEBUG                ("1" näyttää diagnostiikan)
  ALWAYS_POST          ("1" pakottaa postauksen testissä)
"""

import os
import re
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def logd(*a):
    if DEBUG:
        print("[DEBUG]", *a)


def load_seen():
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def save_seen(s):
    STATE_FILE.write_text(
        json.dumps(sorted(list(s)), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch(url: str) -> str | None:
    headers = {
        "User-Agent": "RCF Podiums Bot",
        "Accept": "text/html,application/xhtml+xml",
        "Cookie": COOKIE,
    }
    r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)

    # HTTP-virhe
    if r.status_code >= 400:
        print(f"[WARN] ZwiftPower HTTP {r.status_code} for {url}")
        return None

    text = r.text or ""
    low = text.lower()

    # Jos cookie ei kelpaa, ZwiftPower palauttaa login-sivun -> kerrotaan siitä selvästi
    if ("login" in low and "password" in low) or "ucp.php?mode=login" in low:
        print("[ERROR] ZwiftPower returned login page -> cookie invalid/expired.")
        return None

    # Jos ohjattiin muualle (esim. 302 login), paluupuolella login-sivu näkyy myös
    if "set-cookie" in (r.headers or {}):
        logd("response Set-Cookie:", r.headers.get("set-cookie"))

    return text


def parse_team_results(html: str) -> list[dict]:
    """
    Palauttaa listan tuloksista:
      { 'event': 'Event name', 'date': datetime, 'rider': 'Name', 'pos': 1,
        'category': 'B', 'link': 'https://...' }

    Parsinta on tehty väljästi (ZwiftPowerin HTML voi elää).
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    tables = soup.find_all("table")
    for tbl in tables:
        rows = tbl.find_all("tr")
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue

            # Position (sija)
            pos = None
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

            # Date
            dt_text = None
            for td in tds:
                txt = td.get_text(" ", strip=True)
                if re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", txt):
                    dt_text = txt
                    break
            if not dt_text:
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
            a2 = tr.find("a", href=re.compile(r"profile\.php\?z=\d+"))
            if a2:
                rider = a2.get_text(" ", strip=True)

            # Category (heuristiikka: A/B/C/D jossain solussa)
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

    if lines:
        desc = "\n\n".join(lines)
        title = "RCF – Viikon podiumit (ZwiftPower)"
    else:
        desc = "Ei podiumeja tällä viikolla."
        title = "RCF – Viikon podiumit"

    embed = {
        "type": "rich",
        "title": title,
        "description": desc[:3900],
        "color": int("0x00BC8C", 16),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Ride Club Finland"},
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
    podiums = []
    new_ids = set()

    for r in all_results:
        if r["date"] < week_ago:
            continue
        if r["pos"] > 3:
            continue

        uid = f"{r['link']}|{r['rider']}|{r['pos']}|{r['date'].isoformat()}"
        if uid in seen:
            continue

        podiums.append(r)
        new_ids.add(uid)

    logd("weekly podiums:", len(podiums))

    if podiums or ALWAYS_POST:
        embed = build_discord_embed(podiums)
        post_to_discord(embed)
        if podiums:
            seen |= new_ids
            save_seen(seen)
        print("[INFO] Posted weekly podiums to Discord.")
    else:
        print("[INFO] No new podiums to post this week.")


if __name__ == "__main__":
    main()
