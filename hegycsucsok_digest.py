#!/usr/bin/env python3
"""
Hegyvidéki napi hír-digest (hegycsucsok.hu)
=============================================
Források: orf.at, derstandard.at, euronews.com, travelbook.de,
          traveltomorrow.com, siviaggia.it, bbc.com/travel, wantedinrome.com
          (ugyanaz a 8 forrás, mint a tourism_digest.py / vizpartok_digest.py-
          nál), PLUSZ ÚJ: reuters.com (lásd lent, technikai megjegyzés).

Ez a script a vizpartok_digest.py (KÉSZ, ÉLESBEN MŰKÖDŐ) mintáját követi
1:1 arányban, a hegyvidéki témára szabva:

1. A válogatás (select_and_rank) Claude-promptja KIFEJEZETTEN HEGYVIDÉKI,
   HOBBI-JELLEGŰ relevanciát követel meg (sí, sífutás, szánkó, felvonók,
   túrázás, hegyi állatok/növények, gleccserek stb.) - versenysport-
   eredmények KIZÁRVA, de a felvonó/pálya-infrastruktúra hírek NEM
   sporthírnek számítanak. Balesetek NEM automatikusan kizártak, csak a
   véres/riasztó részletek kerülendők.
2. Az összefoglalás (summarize_article) egy HEGYVIDÉKI FÓKUSZÚ promptot
   kap, ugyanazzal az elvvel, mint a vizpartok.hu-nál: ha a forráscikk egy
   általánosabb listás cikk, csak a hegyvidéki részre koncentrálunk.
3. A kész magyar összefoglalón lefuttatjuk a mountain_keyword_scorer.py-t
   minőségbiztosítási/bizalmi jelzőként (nem dönt a be/ki-kerülésről).
4. A WordPress-feltöltés a hegycsucsok.hu SIMA "post" post type-jára megy.
   FONTOS(!): a hegycsucsok.hu WP-struktúrája (ACF mezők, egyéni
   taxonómiák stb.) MÉG NINCS FELTÉRKÉPEZVE - ezért egyelőre a
   vizpartok.hu-nál bevált, ACF NÉLKÜLI, natív mezőket használó mintát
   követjük (categories/tags taxonómia, natív featured_media kép). Ha
   István elküldi a tényleges oldalstruktúrát, ezt a részt (wp_create_draft)
   érdemes lesz felülvizsgálni.

ÚJ a vizpartok_digest.py-hoz képest (közös kérés mindkét új digesthez):
- Mivel a hegycsucsok.hu-n JELENLEG NINCSENEK ACF mezők, a képek forrását
  NEM egy ACF mezőbe írjuk, hanem a cikk törzsének VÉGÉRE, egy "Képek
  forrása: ..." szövegű bekezdésbe (lásd render_content_html()).
- A szövegben a forrásra való hivatkozást (pl. "Az orf.at cikke szerint...")
  automatikusan LINKKEL látjuk el, ami a forrás FŐOLDALÁRA mutat (nem a
  konkrét cikkre) - lásd linkify_source_mention() és SOURCE_HOMEPAGES.

TECHNIKAI MEGJEGYZÉS A REUTERS.COM FORRÁSRÓL:
A Reuters 2020 óta NEM ad ki saját, közvetlen RSS-feedet. Emiatt egy Google
News-alapú proxy-feedet használunk (site:reuters.com szűréssel, hegyvidéki
kulcsszavakkal szűkítve) - ez működik, de a linkek Google News-átirányításon
mennek keresztül, ami néha nem enged teljes cikkszöveget letölteni. A
meglévő "ha nincs elég tartalom, a cikk kimarad" védőháló ezt kezeli, de
érdemes az első pár éles futás után megnézni, mennyi valódi találat
születik ebből a forrásból - ha túl kevés/megbízhatatlan, érdemes lehet
kivenni vagy másik megoldást keresni.

A Facebook-posztolás NEM ennek a scriptnek a feladata (a projekt jelenlegi
állása szerint egyelőre nem is kérték, csak később, opcionálisan).

Beállítás: lásd README.md.
"""

import os
import re
import json
import time
import base64
import smtplib
import mimetypes
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import urljoin, quote

import feedparser
import requests
from bs4 import BeautifulSoup
import anthropic

from mountain_keyword_scorer import score_article

# ---------------------------------------------------------------------------
# BEÁLLÍTÁSOK
# ---------------------------------------------------------------------------

MAX_AGE_HOURS = 24
PURE_SOURCE_DAILY_LIMIT = 2

# A felhasználó kérése szerint napi 6 hír a cél
DAILY_TOTAL_LIMIT = 6
PROCESSING_BUFFER = 10

MODEL = "claude-sonnet-4-6"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HegycsucsokDigestBot/1.0; +personal use)"
}

# Lásd a vizpartok_digest.py-ban tanult leckét: a WordPress-hívásokhoz
# SZÁNDÉKOSAN nem az önmagát botnak jelentő HEADERS-t használjuk, hanem
# valódi böngésző-szerű fejléceket (Imunify360 WAF-blokkolás elkerülése).
WP_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.8",
}

ARTICLE_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8,hu;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

ARTICLE_SESSION = requests.Session()
ARTICLE_SESSION.headers.update(ARTICLE_FETCH_HEADERS)

# A Reuters-hez Google News proxy-feedet használunk (lásd fenti technikai
# megjegyzés a docstringben) - hegyvidéki kulcsszavakkal szűkítve, hogy ne
# az összes Reuters-hír kerüljön be jelöltként.
_REUTERS_QUERY = (
    'site:reuters.com (ski OR skiing OR mountain OR hiking OR alpine '
    'OR glacier OR avalanche OR "mountain resort" OR cable car)'
)
REUTERS_FEED_URL = (
    f"https://news.google.com/rss/search?q={quote(_REUTERS_QUERY)}"
    f"&hl=en-US&gl=US&ceid=US:en"
)

RSS_FEEDS = {
    "orf.at": [
        "https://rss.orf.at/news.xml",
        "https://rss.orf.at/vorarlberg.xml",
        "https://rss.orf.at/tirol.xml",
        "https://rss.orf.at/salzburg.xml",
        "https://rss.orf.at/kaernten.xml",
        "https://rss.orf.at/steiermark.xml",
        "https://rss.orf.at/ooe.xml",
        "https://rss.orf.at/noe.xml",
        "https://rss.orf.at/wien.xml",
        "https://rss.orf.at/burgenland.xml",
    ],
    "derstandard.at": [
        "https://www.derstandard.at/rss/lifestyle",
        "https://www.derstandard.at/rss/wirtschaft",
    ],
    "euronews.com": [
        "https://www.euronews.com/rss",
    ],
    "travelbook.de": [
        "https://www.travelbook.de/feed",
    ],
    "traveltomorrow.com": [
        "https://traveltomorrow.com/feed/",
    ],
    "siviaggia.it": [
        "https://siviaggia.it/feed/",
    ],
    "bbc.com/travel": [
        "https://www.bbc.com/travel/feed.rss",
    ],
    "wantedinrome.com": [
        "https://www.wantedinrome.com/news?format=rss",
    ],
    "reuters.com": [
        REUTERS_FEED_URL,
    ],
}

# A forrás FŐOLDALÁRA mutató URL-ek - ide linkeljük a szövegben lévő
# forrásra való hivatkozást (lásd linkify_source_mention()).
SOURCE_HOMEPAGES = {
    "orf.at": "https://www.orf.at/",
    "derstandard.at": "https://www.derstandard.at/",
    "euronews.com": "https://www.euronews.com/",
    "travelbook.de": "https://www.travelbook.de/",
    "traveltomorrow.com": "https://traveltomorrow.com/",
    "siviaggia.it": "https://siviaggia.it/",
    "bbc.com/travel": "https://www.bbc.com/travel",
    "wantedinrome.com": "https://www.wantedinrome.com/",
    "reuters.com": "https://www.reuters.com/",
}

KEYWORD_FILTERED_SOURCES = {"orf.at", "derstandard.at", "euronews.com", "wantedinrome.com", "reuters.com"}
PURE_TRAVEL_SOURCES = {"travelbook.de", "traveltomorrow.com", "siviaggia.it", "bbc.com/travel"}

# FONTOS: ez a KEYWORD_FILTERED_SOURCES-hez tartozó ELŐSZŰRŐ, NEM a végső
# hegyvidéki-relevancia döntés (azt a select_and_rank() Claude-hívása dönti
# el). Ez csak azért kell, hogy ne pazaroljunk válogatási helyet
# nyilvánvalóan irreleváns tartalmakon egy vegyes feedből (pl. derstandard.at
# gazdasági híre). Általános utazási szavak + kifejezetten hegyvidéki
# szavak (német/angol/olasz) is szerepelnek benne.
KEYWORDS = [
    "tourism", "tourismus", "touris",
    "reise", "reisen", "urlaub", "urlauber",
    "hotel", "destination", "ausflugsziel", "ausflugsziele",
    "travel", "traveler", "traveller", "vacation", "holiday", "holidays",
    "trip", "ferien", "route", "geheimtipp",
    # Kifejezetten hegyvidéki kulcsszavak (német)
    "berg", "berge", "gebirge", "gipfel", "alm", "hütte", "berghütte",
    "alpenverein", "skigebiet", "seilbahn", "sessellift", "lift",
    "piste", "loipe", "rodeln", "schneeschuh", "gletscher",
    "wandern", "wanderweg", "wandertour", "klettern", "bergsteigen",
    "bergtour", "bergrettung", "lawine",
    # Angol
    "mountain", "mountains", "peak", "summit", "ski", "skiing",
    "hiking", "trail", "trekking", "glacier", "alpine", "cable car",
    "chairlift", "avalanche", "cross-country skiing", "sledding",
    "snowshoe",
    # Olasz
    "montagna", "montagne", "vetta", "ghiacciaio", "sci", "sciare",
    "escursione", "escursionismo", "rifugio", "funivia", "seggiovia",
    "slittino", "valanga",
]

# A wantedinrome.com /news feedjéhez hasonlóan az euronews.com-nál is van
# egy útvonal-alapú kiegészítő szűrő
PATH_FILTER_SOURCES = {"euronews.com": "/travel/"}

EXCLUDE_KEYWORDS = [
    "schneehöhe", "schneehöhen", "schneelage", "aktuelle schneehöhen",
    "stau an der grenze", "verkehrsmeldung",
    "urteil", "gericht", "klage", "versicherung",
    "deal", "angebot", "rabatt", "gutschein", "sale", "% off",
    "quiz", "gewinnspiel", "preisrätsel",
    "opinion", "kolumne", "kommentar:",
    "offerta", "sconto", "codice sconto", "black friday",
    "oroscopo", "sondaggio",
]

FREE_LICENSE_KEYWORDS = [
    "wikimedia commons", "gemeinfrei", "public domain", "creative commons",
    "cc by", "cc0", "pixabay", "unsplash", "gnu free documentation",
]

OPENVERSE_ALLOWED_LICENSES = "cc0,pdm,by,by-sa"
OPENVERSE_API_URL = "https://api.openverse.org/v1/images/"

MAX_ARTICLE_CHARS = 6000


# ---------------------------------------------------------------------------
# 1. RSS BEGYŰJTÉS (azonos logika, mint a vizpartok_digest.py-nál)
# ---------------------------------------------------------------------------

def collect_candidates():
    candidates = []
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=MAX_AGE_HOURS)

    for source_name, urls in RSS_FEEDS.items():
        for feed_url in urls:
            try:
                parsed = feedparser.parse(feed_url)
            except Exception as e:
                print(f"  Hiba a feed beolvasásakor ({source_name}, {feed_url}): {e}")
                continue

            for entry in parsed.entries:
                title = entry.get("title", "")
                summary = entry.get("summary", "") or entry.get("description", "")
                link = entry.get("link", "")

                rss_full_content = ""
                if entry.get("content"):
                    rss_full_content = entry["content"][0].get("value", "")

                if entry.get("published_parsed"):
                    published = datetime(*entry.published_parsed[:6])
                    if published < cutoff:
                        continue
                else:
                    published = None

                haystack = f"{title} {summary}".lower()

                if source_name in PURE_TRAVEL_SOURCES:
                    if any(kw in haystack for kw in EXCLUDE_KEYWORDS):
                        continue
                    include = True
                elif source_name in KEYWORD_FILTERED_SOURCES:
                    keyword_match = any(kw in haystack for kw in KEYWORDS)
                    path_needle = PATH_FILTER_SOURCES.get(source_name)
                    path_match = bool(path_needle) and path_needle in link
                    include = keyword_match or path_match
                else:
                    include = True

                if include:
                    candidates.append({
                        "source": source_name,
                        "title": title,
                        "summary": summary,
                        "link": link,
                        "published": published,
                        "rss_full_content": rss_full_content,
                    })

    seen = set()
    unique = []
    for c in candidates:
        if c["link"] not in seen:
            seen.add(c["link"])
            unique.append(c)
    return unique


# ---------------------------------------------------------------------------
# 2. AI-VÁLOGATÁS - HEGYVIDÉKI, HOBBI-JELLEGŰ RELEVANCIA-KÖVETELMÉNYNYEL
# ---------------------------------------------------------------------------

HEGYCSUCSOK_SELECTION_SYSTEM_PROMPT = """Turisztikai hírszerkesztő vagy a \
hegycsucsok.hu magyar hegyvidéki témájú, HOBBI-KÖZPONTÚ magazin számára. \
Egy JSON tömböt kapsz jelölt cikkekkel (index, forrás, cím, rövid leírás, \
megjelenés dátuma - NÉMET, ANGOL vagy OLASZ nyelvűek lehetnek). A feladatod, \
hogy kiválaszd és rangsorold a legjobbakat az alábbi szabályok szerint:

1. HEGYVIDÉKI, HOBBI-JELLEGŰ RELEVANCIA (LEGFONTOSABB SZŰRŐ): a cikknek \
TÉNYLEGESEN kapcsolódnia kell hegyvidéki hobbitevékenységhez vagy \
természeti témához: síelés, sífutás, szánkózás, snowboard, hódeszkázás, \
felvonók/kötélpályák (akár csak infrastruktúra-hír, pl. "megnyílt egy új \
felvonó" - ez NEM sporthír, tehát bekerülhet), téli/nyári kirándulás, \
gyalogtúrázás, hegymászás mint hobbi (nem versenysport), menedékházak, \
hegyi szállások, hegyi állatok (pl. zerge, kőszáli sas, marmota), hegyi \
növényvilág, gleccserek (állapotuk, olvadásuk, látogathatóságuk), \
hegycsúcsok, hágók, hegyvidéki barlangok/vízesések.

2. VERSENYSPORT KIZÁRVA: SOHA ne válassz olyan cikket, aminek fő témája \
versenysport-eredmény, világkupa-forduló, olimpiai kvalifikáció, \
csapat-összeállítás vagy sportolói teljesítmény-hír. A hegycsucsok.hu \
KIZÁRÓLAG hobbiként közelíti meg a hegyvidéki tevékenységeket, nem \
versenysportként. KIVÉTEL: egy felvonó/pálya/infrastruktúra-hír (pl. \
"megnyílt/bezárt a sípálya", "felújították a kötélpályát") bekerülhet, \
még akkor is, ha egy síközpontban zajlik, ahol egyébként versenyeket is \
rendeznek - a hangsúly a hobbi-turisztikai vonatkozáson van.

3. BALESETEK: NEM KIZÁRTAK AUTOMATIKUSAN - egy gyakorlati, biztonsági \
szempontból hasznos hír (pl. lavina miatti pályazár, mentőakció rövid \
ténybeli összefoglalása) bekerülhet, DE kerüld azokat a cikkeket, \
amiknek a fő fókusza a baleset RÉSZLETES, riasztó/véres leírása - ezeket \
NE válaszd ki, mert nem illenek egy magazinos, hobbi-központú digestbe.

4. FÖLDRAJZI FÓKUSZ: az Alpok egésze, az Appenninek, a Pireneusok, a \
Kárpátok (beleértve a Tátrát, Fátrát, Mátrát, Bakonyt, Bükköt, Zemplént \
is), és általánosságban minden európai hegyvidék a Kárpátoktól nyugatra - \
gyakorlatilag Európa teljes hegyvidéki térsége beleértendő. Nem-európai \
hegyvidékekről (Himalája, Andok stb.) szóló cikkeket dobd el, kivéve ha \
kifejezetten az európai utazókat érintő gyakorlati hírről van szó.

5. PORTÁLONKÉNTI LIMIT: az alábbi négy forrásból ("travelbook.de", \
"traveltomorrow.com", "siviaggia.it", "bbc.com/travel") forrásonként \
MAXIMUM {pure_limit} cikket válassz ki összesen. A többi forrásra nincs \
ilyen limit.

6. MINŐSÉG: részesítsd előnyben az egyedi, látványos, gyakorlati \
információt is hordozó (pl. hogyan közelíthető meg egy túraútvonal, mikor \
érdemes menni) hegyvidéki cikkeket.

POLITIKAI/KATONAI KONFLIKTUS KIZÁRVA: SOHA ne válassz olyan cikket, \
aminek fő témája politikai vagy katonai konfliktus, háború, geopolitikai \
feszültség - EZ AKKOR IS IGAZ, ha a cikk formálisan érinti a hegyvidéket.

7. VÉGSŐ LIMIT: a fentiek után válaszd ki és rangsorold (legjobb elöl) \
összesen MAXIMUM {total_limit} cikket. Ha egyetlen jelölt sem felel meg a \
hegyvidéki, hobbi-jellegű relevancia-követelménynek, adj vissza egy ÜRES \
JSON tömböt ([]) - jobb egy üres nap, mint egy erőltetett, irreleváns \
válogatás.

VÁLASZ FORMÁTUMA: KIZÁRÓLAG egy JSON tömböt adj vissza a kiválasztott \
cikkek eredeti "index" mezőjével, rangsorolva (legjobb elöl). Semmi mást \
ne írj a JSON tömbön kívül.
Példa: [4, 12, 0, 7]"""


def select_and_rank(candidates):
    if not candidates:
        return []

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    slim = []
    for i, c in enumerate(candidates):
        slim.append({
            "index": i,
            "source": c["source"],
            "title": c["title"],
            "summary": (c["summary"] or "")[:300],
            "published": c["published"].isoformat() if c["published"] else None,
        })

    system_prompt = HEGYCSUCSOK_SELECTION_SYSTEM_PROMPT.format(
        pure_limit=PURE_SOURCE_DAILY_LIMIT,
        total_limit=DAILY_TOTAL_LIMIT + PROCESSING_BUFFER,
    )

    user_prompt = "Jelölt cikkek:\n" + json.dumps(slim, ensure_ascii=False, indent=2)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = "".join(b.text for b in response.content if b.type == "text").strip()
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        indices = json.loads(raw)
        selected = [candidates[i] for i in indices if isinstance(i, int) and 0 <= i < len(candidates)]
        return selected
    except Exception as e:
        print(f"  Hiba a válogatás során: {e}")
        # Ugyanaz az elv, mint a vizpartok_digest.py-nál: a hegyvidéki
        # relevancia eldöntése kifejezetten Claude-ítéletet igényel, ezért
        # hiba esetén üres listát adunk vissza (jobb egy kimaradt nap, mint
        # egy vaktában összeállított, irreleváns válogatás).
        return []


# ---------------------------------------------------------------------------
# 3. CIKKSZÖVEG + SZABADON FELHASZNÁLHATÓ KÉP LETÖLTÉSE
#    (azonos logika, mint a vizpartok_digest.py-nál)
# ---------------------------------------------------------------------------

def _is_free_caption(text):
    t = text.lower()
    return any(kw in t for kw in FREE_LICENSE_KEYWORDS)


def _resolve_image_src(img_tag, base_url):
    src = img_tag.get("src") or img_tag.get("data-src")
    if not src:
        return None
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return urljoin(base_url, src)
    return src


def _extract_credit(caption):
    lower = caption.lower()
    idx = lower.find("foto:")
    if idx == -1:
        return caption
    return "Fotó:" + caption[idx + len("foto:"):].strip()


def find_free_image(soup, base_url):
    for fig in soup.find_all("figure"):
        img = fig.find("img")
        caption_el = fig.find("figcaption")
        caption = caption_el.get_text(" ", strip=True) if caption_el else ""
        if img and caption and _is_free_caption(caption):
            src = _resolve_image_src(img, base_url)
            if src:
                return src, _extract_credit(caption)

    for em in soup.find_all(["em", "i"]):
        text = em.get_text(" ", strip=True)
        if "foto:" in text.lower() and _is_free_caption(text):
            img = em.find_previous("img")
            if img:
                src = _resolve_image_src(img, base_url)
                if src:
                    return src, _extract_credit(text)

    return None, None


def fetch_article_content(url, rss_fallback_html=""):
    html_for_image_search = ""
    text = ""
    fetch_error = None

    try:
        resp = ARTICLE_SESSION.get(url, timeout=15)
        resp.raise_for_status()
        html_for_image_search = resp.text
        soup = BeautifulSoup(resp.text, "html.parser")
        paragraphs = soup.find_all("p")
        text = "\n".join(p.get_text(" ", strip=True) for p in paragraphs)
        text = re.sub(r"\n{2,}", "\n", text).strip()
    except Exception as e:
        fetch_error = str(e)

    MIN_USABLE_CHARS = 200
    if rss_fallback_html and len(text) < MIN_USABLE_CHARS:
        fallback_soup = BeautifulSoup(rss_fallback_html, "html.parser")
        fallback_paragraphs = fallback_soup.find_all("p")
        if fallback_paragraphs:
            fallback_text = "\n".join(
                p.get_text(" ", strip=True) for p in fallback_paragraphs
            )
        else:
            fallback_text = fallback_soup.get_text(" ", strip=True)
        fallback_text = re.sub(r"\n{2,}", "\n", fallback_text).strip()

        if len(fallback_text) > len(text):
            text = fallback_text
            if not html_for_image_search:
                html_for_image_search = rss_fallback_html

    if not text:
        if fetch_error:
            text = f"[Nem sikerült letölteni: {fetch_error}]"
        else:
            text = "[Nem található kinyerhető szöveg.]"
    text = text[:MAX_ARTICLE_CHARS]

    image_url, image_credit = (None, None)
    if html_for_image_search:
        image_soup = BeautifulSoup(html_for_image_search, "html.parser")
        image_url, image_credit = find_free_image(image_soup, url)

    return {"text": text, "image_url": image_url, "image_credit": image_credit}


def search_openverse_image(query):
    if not query:
        return None

    try:
        resp = requests.get(
            OPENVERSE_API_URL,
            params={
                "q": query,
                "license": OPENVERSE_ALLOWED_LICENSES,
                "page_size": 5,
            },
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as e:
        print(f"    Openverse keresési hiba ('{query}'): {e}")
        return None

    for result in results:
        image_url = result.get("url")
        if not image_url:
            continue
        creator = result.get("creator") or "ismeretlen szerző"
        license_name = (result.get("license") or "").upper()
        license_version = result.get("license_version") or ""
        license_full = f"{license_name} {license_version}".strip()
        credit = f"{creator} / {license_full} (Openverse)"
        location = result.get("title") or query
        return {
            "image_url": image_url,
            "image_credit": credit,
            "image_location": location,
        }

    return None


# ---------------------------------------------------------------------------
# 4. STRUKTURÁLT (JSON) ÖSSZEFOGLALÁS - HEGYVIDÉKI FÓKUSSZAL
# ---------------------------------------------------------------------------

HEGYCSUCSOK_SUMMARY_SYSTEM_PROMPT = """Turisztikai hírszerkesztő vagy a \
hegycsucsok.hu magyar HEGYVIDÉKI, HOBBI-KÖZPONTÚ magazin számára, amely 9 \
forrásból (orf.at, derstandard.at - német; euronews.com, bbc.com/travel, \
traveltomorrow.com, wantedinrome.com, reuters.com - angol; travelbook.de - \
német; siviaggia.it - olasz) dolgozik. A bemeneti cikkszöveg német, angol \
vagy olasz nyelvű lehet - magyarra fordítva, saját szavaiddal összefoglalva \
dolgozz.

KRITIKUS: HEGYVIDÉKI, HOBBI-FÓKUSZ. Ha a forráscikk egy általánosabb témájú \
cikk (pl. egy listás cikk, aminek csak egy pontja hegyvidéki), az \
összefoglalódnak NE a teljes cikket kelljen lefednie - koncentrálj \
KIFEJEZETTEN a hegyvidéki/hegyi hobbi részre. Ha a forráscikk versenysport-\
eredményről szól, de van benne egy hobbi-turisztikai szál is (pl. egy \
sípálya megnyitása egy versenyhír mellett), KIZÁRÓLAG a hobbi-turisztikai \
részre fókuszálj, a versenyeredményeket ne emeld ki.

BALESETEK KEZELÉSE: ha a cikk balesetről/mentőakcióról is szól, csak a \
TÉNYSZERŰ, gyakorlati információt közöld (pl. útlezárás, biztonsági \
figyelmeztetés, mikor nyit újra egy pálya) - SOHA ne írj le véres, \
riasztó, az áldozatok szenvedésére fókuszáló részleteket.

STÍLUS: magazinos, közérthető, olvasmányos, sztorizó megfogalmazás - de \
mindig a tényeken belül maradva. Kerüld a száraz, hivatalos stílust.

MAGÁZÁS, NE TEGEZÉS: mindig magázó formában szólítsd meg az olvasót.

HOSSZ: írj 4-6 bekezdést, összesen kb. 300-450 szó terjedelemben.

FORRÁSMEGJELÖLÉS A SZÖVEGBEN - KRITIKUS SZABÁLY: a cikk elején vagy \
második bekezdésében természetes módon utalj a forrásra, ÉS ehhez PONTOSAN \
a következő szöveget használd szó szerint, változtatás nélkül: \
"{source_name}" (pl. "Az {source_name} beszámolója szerint..." vagy "Az \
{source_name} cikke szerint..."). Ez azért kritikusan fontos, mert a \
rendszer ez alapján a PONTOS szöveg alapján fogja automatikusan linkkel \
ellátni a forrás nevét a cikk publikálásakor - ha átírod vagy más \
elnevezést használsz (pl. "ORF Tirol" a puszta "orf.at" helyett), a \
linkelés nem fog működni.

KRITIKUS SZABÁLY - HIÁNYOS TARTALOM: ha a letöltött cikkszöveg + az \
RSS-leírás együtt sem elég egy értelmes, tényszerű összefoglalóhoz, VAGY \
ha a cikk hegyvidéki tartalma túl kevés/felszínes ahhoz, hogy önálló \
cikket lehessen belőle írni, állítsd az "usable" mezőt false-ra, és a \
többi mezőt hagyd üresen. Ha van elég hegyvidéki tartalom, "usable": true.

SZERZŐI JOG: SOHA ne idézz szó szerint 15 szónál hosszabban.

A submit_summary eszközzel add át az elkészült összefoglalót. Ha nem \
kaptál szabadon felhasználható kép adatot, az image_url/image_credit/ \
image_location mezőket hagyd üresen - SOHA ne találj ki képet. Ilyenkor \
mindig adj meg egy image_search_query-t (lehetőleg a konkrét hegyvidéki \
helyszínre vonatkozót, pl. "Zugspitze glacier" ne csak "Alps")."""

SUMMARY_TOOL = {
    "name": "submit_summary",
    "description": "Az elkészült magyar nyelvű, hegyvidéki fókuszú cikk-összefoglaló beküldése.",
    "input_schema": {
        "type": "object",
        "properties": {
            "usable": {
                "type": "boolean",
                "description": "Volt-e elég HEGYVIDÉKI tartalom egy értelmes, önálló cikkhez.",
            },
            "title": {"type": "string", "description": "Magyar, figyelemfelkeltő cím."},
            "lead": {"type": "string", "description": "1-2 mondatos lead."},
            "paragraphs": {
                "type": "string",
                "description": (
                    "A cikk törzse, 4-6 bekezdés EGYETLEN string-ben, "
                    "a bekezdéseket két egymást követő sortöréssel "
                    "(\\n\\n) elválasztva - VALÓDI PRÓZA, nem JSON-tömb!"
                ),
            },
            "image_url": {"type": ["string", "null"]},
            "image_credit": {"type": ["string", "null"]},
            "image_location": {"type": ["string", "null"]},
            "image_search_query": {
                "type": ["string", "null"],
                "description": "1-3 szavas angol keresőkifejezés a konkrét hegyvidéki helyszínhez.",
            },
            "source_name": {"type": "string"},
            "published_date": {"type": ["string", "null"], "description": "ÉÉÉÉ-HH-NN"},
            "country": {"type": ["string", "null"], "description": "Az érintett ország magyar neve."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1-3 releváns magyar kulcsszó kisbetűvel.",
            },
        },
        "required": ["usable", "paragraphs", "tags"],
    },
}


def _ensure_string_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return [value] if value else []
    return []


def summarize_article(article, article_content):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    published_str = (
        article["published"].strftime("%Y-%m-%d")
        if article["published"] else None
    )

    if article_content["image_url"]:
        image_block = (
            f"Szabadon felhasználható kép:\n"
            f"URL: {article_content['image_url']}\n"
            f"Hitelesítés: {article_content['image_credit']}"
        )
    else:
        image_block = "Szabadon felhasználható kép: nincs"

    user_prompt = f"""Forrás: {article['source']}
Eredeti cím: {article['title']}
Megjelenés: {published_str or "ismeretlen"}
URL: {article['link']}

RSS-leírás:
{article['summary']}

Letöltött cikkszöveg (részleges lehet):
{article_content['text']}

{image_block}

Készítsd el a HEGYVIDÉKI FÓKUSZÚ összefoglalót a submit_summary eszközzel. \
Ne felejtsd: a forrásra hivatkozáskor PONTOSAN a "{article['source']}" \
szöveget használd."""

    system_prompt = HEGYCSUCSOK_SUMMARY_SYSTEM_PROMPT.format(source_name=article["source"])

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=system_prompt,
        tools=[SUMMARY_TOOL],
        tool_choice={"type": "tool", "name": "submit_summary"},
        messages=[{"role": "user", "content": user_prompt}],
    )

    tool_use_block = next(
        (b for b in response.content if b.type == "tool_use"), None
    )
    if tool_use_block is None:
        raise ValueError("A Claude válasza nem tartalmazott tool_use blokkot.")

    data = tool_use_block.input

    raw_paragraphs = data.get("paragraphs")
    if isinstance(raw_paragraphs, list):
        data["paragraphs"] = raw_paragraphs
    elif isinstance(raw_paragraphs, str):
        stripped = raw_paragraphs.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                data["paragraphs"] = [str(p).strip() for p in parsed if str(p).strip()]
            else:
                data["paragraphs"] = [p.strip() for p in raw_paragraphs.split("\n\n") if p.strip()]
        else:
            data["paragraphs"] = [p.strip() for p in raw_paragraphs.split("\n\n") if p.strip()]
    else:
        data["paragraphs"] = _ensure_string_list(raw_paragraphs)

    data["tags"] = _ensure_string_list(data.get("tags"))

    if not data.get("usable", False):
        return None

    data.setdefault("source_name", article["source"])
    if published_str:
        data["published_date"] = published_str
    else:
        data.setdefault("published_date", None)
    data["link"] = article["link"]
    # Az eredeti forráskulcsot (pl. "orf.at") KÜLÖN is eltároljuk - erre lesz
    # szükség a wp_create_draft()-ban a linkify_source_mention()-höz, mert
    # a Claude a "source_name" mezőbe esetleg egy kicsit más formában írhatja
    # (pl. nagybetűsítve), miközben a szövegben a pontos "{source}" alakot
    # kértük.
    data["source_key"] = article["source"]

    if not data.get("image_url"):
        query = data.get("image_search_query") or data.get("country") or ""
        found = search_openverse_image(query)
        if found:
            data["image_url"] = found["image_url"]
            data["image_credit"] = found["image_credit"]
            data["image_location"] = found["image_location"]

    # --- MINŐSÉGBIZTOSÍTÁSI LÉPÉS: mountain_keyword_scorer a KÉSZ magyar
    # szövegen. NEM dönt a be/ki-kerülésről, csak egy pontszámot csatol.
    score_result = score_article(
        data.get("title", ""), data.get("lead", ""),
        " ".join(data.get("paragraphs", [])),
    )
    data["hegy_pontszam"] = score_result.total_score
    data["hegy_cim_lead_talalat"] = score_result.title_or_lead_match
    if score_result.total_score == 0:
        print(f"  FIGYELEM: '{data.get('title')}' hegyvidéki pontszáma 0 a "
              f"kész magyar szövegen - érdemes emberi felülvizsgálatra.")

    return data


# ---------------------------------------------------------------------------
# 5. WORDPRESS PISZKOZAT-LÉTREHOZÁS (hegycsucsok.hu - sima "post" post type,
#    ACF MEZŐK NÉLKÜL - lásd a modul-docstringet)
# ---------------------------------------------------------------------------

_term_cache = {}


def wp_headers(user, app_password, with_content_type=False):
    token = base64.b64encode(f"{user}:{app_password}".encode("utf-8")).decode("ascii")
    headers = dict(WP_API_HEADERS)
    headers["Authorization"] = f"Basic {token}"
    headers["Accept"] = "application/json"
    if with_content_type:
        headers["Content-Type"] = "application/json"
    return headers


def wp_get_or_create_term(taxonomy_rest_base, name, wp_user, wp_app_password, site_url):
    if not name:
        return None

    cache_key = (taxonomy_rest_base, name.strip().lower())
    if cache_key in _term_cache:
        return _term_cache[cache_key]

    base = f"{site_url}/wp-json/wp/v2/{taxonomy_rest_base}"

    resp = None
    try:
        resp = requests.get(
            base,
            params={"search": name, "per_page": 100},
            headers=wp_headers(wp_user, wp_app_password),
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()
        if not isinstance(results, list):
            raise ValueError(f"Váratlan válaszformátum (nem lista): {results}")
        for term in results:
            if term["name"].strip().lower() == name.strip().lower():
                _term_cache[cache_key] = term["id"]
                return term["id"]
    except Exception as e:
        detail = resp.text[:300] if resp is not None else ""
        print(f"    Hiba a(z) '{name}' term keresésekor ({taxonomy_rest_base}): {e} | Válasz: {detail}")

    resp = None
    try:
        resp = requests.post(
            base,
            json={"name": name},
            headers=wp_headers(wp_user, wp_app_password, with_content_type=True),
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        if not isinstance(result, dict) or "id" not in result:
            raise ValueError(f"Váratlan válaszformátum (nincs 'id'): {result}")
        term_id = result["id"]
        _term_cache[cache_key] = term_id
        return term_id
    except Exception as e:
        detail = resp.text[:300] if resp is not None else ""
        print(f"    Hiba a(z) '{name}' term létrehozásakor ({taxonomy_rest_base}): {e} | Válasz: {detail}")
        return None


def _ascii_safe_filename(url, content_type):
    parsed_path = re.sub(r"[?#].*$", "", url.rsplit("/", 1)[-1])
    base_ascii = re.sub(r"[^A-Za-z0-9._-]", "", parsed_path)
    if not base_ascii or "." not in base_ascii:
        ext = mimetypes.guess_extension((content_type or "").split(";")[0].strip()) or ".jpg"
        base_ascii = f"image{ext}"
    return base_ascii[:100]


def wp_upload_media(image_url, alt_text, caption, wp_user, wp_app_password, site_url):
    if not image_url:
        return None

    try:
        img_resp = requests.get(image_url, headers=ARTICLE_FETCH_HEADERS, timeout=20)
        img_resp.raise_for_status()
        image_bytes = img_resp.content
        content_type = img_resp.headers.get("Content-Type", "").split(";")[0].strip() or "image/jpeg"
    except Exception as e:
        print(f"    Hiba a kép letöltésekor ({image_url}): {e}")
        return None

    filename = _ascii_safe_filename(image_url, content_type)

    headers = wp_headers(wp_user, wp_app_password)
    headers["Content-Type"] = content_type
    headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    resp = None
    try:
        resp = requests.post(
            f"{site_url}/wp-json/wp/v2/media",
            data=image_bytes,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        if not isinstance(result, dict) or "id" not in result:
            raise ValueError(f"Váratlan válaszformátum (nincs 'id'): {result}")
        media_id = result["id"]
    except Exception as e:
        detail = resp.text[:300] if resp is not None else ""
        print(f"    Hiba a kép feltöltésekor a médiatárba: {e} | Válasz: {detail}")
        return None

    if alt_text or caption:
        media_meta = {"alt_text": (alt_text or "")[:200]}
        if alt_text:
            media_meta["title"] = alt_text[:200]
        if caption:
            media_meta["caption"] = f"Forrás: {caption}"
        try:
            requests.post(
                f"{site_url}/wp-json/wp/v2/media/{media_id}",
                json=media_meta,
                headers=wp_headers(wp_user, wp_app_password, with_content_type=True),
                timeout=15,
            )
        except Exception as e:
            print(f"    Figyelmeztetés: nem sikerült beállítani a kép alt-szövegét/forrását: {e}")

    return media_id


def linkify_source_mention(paragraphs, source_key):
    """Megkeresi a bekezdésekben az ELSŐ előfordulását a pontos forrás-
    kulcsnak (pl. "orf.at"), és HTML linkkel látja el, ami a forrás
    FŐOLDALÁRA mutat (SOURCE_HOMEPAGES-ből, NEM a konkrét cikkre). Csak az
    ELSŐ találatot linkeli - ha a forrás neve véletlenül többször is
    szerepelne a szövegben, a többi előfordulás sima szöveg marad, hogy ne
    legyen a cikk tele ismételt linkekkel.

    Ha nem talál egyezést (pl. a Claude mégsem a kért pontos formában írta
    be a forrás nevét), a bekezdések VÁLTOZATLANUL kerülnek vissza - ez
    esetben csak egy figyelmeztetést írunk a logba, a piszkozat-létrehozás
    nem hiúsul meg emiatt."""
    homepage_url = SOURCE_HOMEPAGES.get(source_key)
    if not homepage_url or not source_key:
        return paragraphs

    pattern = re.compile(re.escape(source_key), re.IGNORECASE)
    result = []
    linked = False
    for p in paragraphs:
        if not linked:
            m = pattern.search(p)
            if m:
                link_html = (
                    f'<a href="{homepage_url}" target="_blank" '
                    f'rel="noopener">{m.group(0)}</a>'
                )
                p = p[:m.start()] + link_html + p[m.end():]
                linked = True
        result.append(p)

    if not linked:
        print(f"    Figyelmeztetés: a(z) '{source_key}' forrásnév szó szerinti "
              f"formában nem található a cikkszövegben - a forráshivatkozás "
              f"linkelése ezúttal kimaradt.")

    return result


def render_content_html(article_json):
    """Összeállítja a poszt HTML-tartalmát: a bekezdéseket (a forrás-
    hivatkozás linkelésével), majd - ha van kép-hitelesítés - a cikk
    VÉGÉRE fűz egy "Képek forrása: ..." bekezdést. Ez azért kell a natív
    médiatár-caption ÉS az esetleges jövőbeli ACF mező mellett/helyett,
    mert a hegycsucsok.hu-n JELENLEG NINCSENEK ACF mezők, tehát a
    forrásmegjelölésnek a törzsszövegben is meg kell jelennie."""
    paragraphs = linkify_source_mention(
        article_json.get("paragraphs", []),
        article_json.get("source_key", ""),
    )

    html_parts = [f"<p>{p}</p>" for p in paragraphs]

    image_credit = article_json.get("image_credit")
    if image_credit:
        html_parts.append(f"<p><em>Képek forrása: {image_credit}</em></p>")

    return "\n".join(html_parts)


def wp_create_draft(article_json):
    """Piszkozatot hoz létre a hegycsucsok.hu SIMA 'posts' végpontján.
    ACF mezőket NEM használunk (lásd modul-docstring) - a kép-hitelesítés
    a törzsszöveg végén, a forrás-hivatkozás pedig a szövegbe szőtt linkkel
    jelenik meg."""
    site_url = os.environ["HEGYCSUCSOK_WP_SITE_URL"].rstrip("/")
    wp_user = os.environ["HEGYCSUCSOK_WP_USER"]
    wp_app_password = os.environ["HEGYCSUCSOK_WP_APP_PASSWORD"]

    tag_ids = []
    for tag_name in article_json.get("tags") or []:
        term_id = wp_get_or_create_term("tags", tag_name, wp_user, wp_app_password, site_url)
        if term_id:
            tag_ids.append(term_id)

    category_ids = []
    country = article_json.get("country")
    if country:
        term_id = wp_get_or_create_term("categories", country, wp_user, wp_app_password, site_url)
        if term_id:
            category_ids.append(term_id)

    content_html = render_content_html(article_json)

    featured_media_id = None
    image_url = article_json.get("image_url")
    if image_url:
        alt_text = article_json.get("image_location") or article_json.get("title") or ""
        caption = article_json.get("image_credit") or ""
        featured_media_id = wp_upload_media(
            image_url, alt_text, caption, wp_user, wp_app_password, site_url
        )
        if featured_media_id is None:
            print(f"    Figyelmeztetés: nem sikerült feltölteni a kiemelt képet "
                  f"('{article_json.get('title')}') - a poszt kép nélkül jön létre.")

    payload = {
        "title": article_json["title"],
        "content": content_html,
        "excerpt": article_json.get("lead", ""),
        "status": "draft",
        "tags": tag_ids,
        "categories": category_ids,
    }
    if featured_media_id:
        payload["featured_media"] = featured_media_id

    resp = None
    try:
        resp = requests.post(
            f"{site_url}/wp-json/wp/v2/posts",
            json=payload,
            headers=wp_headers(wp_user, wp_app_password, with_content_type=True),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or "id" not in data:
            raise ValueError(f"Váratlan válaszformátum (nincs 'id') - valószínűleg "
                              f"hosting-szintű blokkolás, nem valódi WP-válasz: {data}")
        return data.get("link") or f"{site_url}/wp-admin/post.php?post={data['id']}&action=edit"
    except Exception as e:
        detail = resp.text[:300] if resp is not None else ""
        print(f"  Hiba a WordPress-piszkozat létrehozásakor ('{article_json.get('title')}'): {e} | Válasz: {detail}")
        return None


# ---------------------------------------------------------------------------
# 6. EMAIL ÖSSZEÁLLÍTÁS ÉS KÜLDÉS
# ---------------------------------------------------------------------------

def render_email_section(article_json, wp_link):
    lines = [f"## {article_json['title']}", "", f"*{article_json.get('lead', '')}*", ""]
    lines.extend(article_json.get("paragraphs", []))
    lines.append("")

    if article_json.get("image_url"):
        caption = article_json.get("image_credit") or ""
        lines.append(f"**Kép:** {article_json['image_url']}")
        if caption:
            lines.append(f"*{caption}*")
        lines.append("")

    published = article_json.get("published_date") or "dátum ismeretlen"
    lines.append(f"**Forrás:** {article_json.get('source_name', '')}, megjelent {published}")

    conf = "magas (cím/lead-ben is van találat)" if article_json.get("hegy_cim_lead_talalat") else "közepes (csak törzsszövegben)"
    lines.append(f"**Hegyvidéki pontszám:** {article_json.get('hegy_pontszam', 0)}  (bizalmi szint: {conf})")

    if wp_link:
        lines.append(f"\n📤 **Piszkozatként feltöltve:** {wp_link}")

    recovery_data = {
        "title": article_json.get("title"),
        "lead": article_json.get("lead"),
        "paragraphs": article_json.get("paragraphs", []),
        "image_url": article_json.get("image_url"),
        "image_credit": article_json.get("image_credit"),
        "image_location": article_json.get("image_location"),
        "source_name": article_json.get("source_name"),
        "source_key": article_json.get("source_key"),
        "published_date": article_json.get("published_date"),
        "country": article_json.get("country"),
        "tags": article_json.get("tags", []),
        "link": article_json.get("link"),
        "hegy_pontszam": article_json.get("hegy_pontszam", 0),
        "hegy_cim_lead_talalat": article_json.get("hegy_cim_lead_talalat", False),
        "already_uploaded": bool(wp_link),
    }
    lines.append("")
    lines.append(f"[ADATOK: {json.dumps(recovery_data, ensure_ascii=False)}]")

    return "\n".join(lines)


def send_email(subject, body_markdown):
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_password = os.environ["SMTP_PASSWORD"]
    email_from = os.environ.get("EMAIL_FROM", smtp_user)
    email_to = os.environ["HEGYCSUCSOK_EMAIL_TO"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to
    msg.attach(MIMEText(body_markdown, "plain", "utf-8"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(email_from, email_to.split(","), msg.as_string())


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    candidates = collect_candidates()
    print(f"Talált (előszűrt) jelölt: {len(candidates)}")

    ranked = select_and_rank(candidates)
    print(f"Hegyvidéki AI-válogatás után megmaradt: {len(ranked)}")

    summaries = []
    for i, article in enumerate(ranked):
        if len(summaries) >= DAILY_TOTAL_LIMIT:
            print(f"Elértük a napi limitet ({DAILY_TOTAL_LIMIT} sikeres cikk) - "
                  f"a maradék {len(ranked) - i} tartalék cikket nem dolgozzuk fel.")
            break

        print(f"Feldolgozás ({i + 1}/{len(ranked)}, eddig {len(summaries)}/{DAILY_TOTAL_LIMIT} sikeres): "
              f"[{article['source']}] {article['title']}")
        try:
            content = fetch_article_content(
                article["link"],
                rss_fallback_html=article.get("rss_full_content", ""),
            )
            summary = summarize_article(article, content)
            if summary is None:
                print(f"  Kihagyva: nem volt elég hegyvidéki tartalom egy önálló cikkhez.")
                continue
            summaries.append(summary)
        except Exception as e:
            print(f"  Hiba az összefoglalásnál: {e}")
        time.sleep(1)

    print(f"\nVégeredmény: {len(summaries)}/{DAILY_TOTAL_LIMIT} sikeres cikk.")

    wp_links = {}
    for summary in summaries:
        link = wp_create_draft(summary)
        if link:
            wp_links[summary["link"]] = link
            print(f"  Piszkozat létrehozva: {link}")

    today_str = datetime.now().strftime("%Y. %m. %d.")

    if not summaries:
        body = f"Ma ({today_str}) nem találtunk hegyvidéki témájú cikket a beállított források friss híranyagában."
    else:
        sections = [
            render_email_section(s, wp_links.get(s["link"]))
            for s in summaries
        ]
        body = f"# Napi hegyvidéki hír-digest - {today_str}\n\n" + "\n\n---\n\n".join(sections)

    subject = f"Hegyvidéki hírek (hegycsucsok.hu) - {today_str}"
    send_email(subject, body)
    print("Email elküldve.")


if __name__ == "__main__":
    main()
