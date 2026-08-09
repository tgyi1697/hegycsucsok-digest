# -*- coding: utf-8 -*-
"""
mountain_keyword_scorer.py
---------------------------
Hegyvidéki téma-pontozó a hegycsucsok.hu digest projekthez.

Ugyanaz a szerep, mint a vizpartok.hu-s water_keyword_scorer.py-nak: a
hegycsucsok_digest.py egy UTÓFELDOLGOZÓ lépéseként fut le a Claude-generálta
kész magyar cikkszövegen (cím + lead + törzsszöveg), és pontozza, mennyire
"hegyvidéki/hegyi hobbi" témájú a hír. NEM dönt a be/ki-kerülésről (azt már
a select_and_rank AI-válogatás eldöntötte) - csak egy minőségbiztosítási/
bizalmi jelzőt csatol, amit a szerkesztő lát az e-mailben.

A súlyok és a küszöbérték EZ IS egy első minta, mint a vízpartinál - valós
cikkeken tesztelve finomítandó.
"""

import re
from dataclasses import dataclass, field


KEYWORDS = {
    # A hegyvidék/hegység maga - a legerősebb jel, hogy a cikk ténylegesen
    # hegyi témájú
    "hegyvidek": {
        "weight": 3,
        "patterns": [
            r"\bhegy\w*", r"\bhegység\w*", r"\bcsúcs\w*", r"\bgerinc\w*",
            r"\bhágó\w*", r"\balpok\b", r"\balpesi\w*", r"\bappennin\w*",
            r"\bpireneus\w*", r"\bkárpát\w*", r"\btátra\w*", r"\bfátra\w*",
            r"\bmátra\w*", r"\bbakony\w*", r"\bbükk\w*", r"\bzemplén\w*",
            r"\bgleccser\w*", r"\bhavas\w*", r"\bfennsík\w*",
        ],
    },
    # Hobbi-tevékenységek és infrastruktúra - önmagában gyengébb jel (pl.
    # egy "felvonó" szó más kontextusban is előfordulhatna), közepes súly
    "aktivitas": {
        "weight": 2,
        "patterns": [
            r"\bsí\b", r"\bsíel\w*", r"\bsífutás\w*", r"\bsípálya\w*",
            r"\bszánkó\w*", r"\bsnowboard\w*", r"\bhódeszk\w*",
            r"\bfelvonó\w*", r"\bkötélpálya\w*", r"\bsíliftek?\b",
            r"\bhegymászás\w*", r"\btúráz\w*", r"\bkirándul\w*",
            r"\bmenedékház\w*", r"\bkilátó\w*", r"\bösvény\w*",
        ],
    },
    # Természeti elemek - általánosabb, kontextusfüggő, legkisebb súly
    "termeszet": {
        "weight": 1,
        "patterns": [
            r"\bzerge\w*", r"\bmarmota\w*", r"\bkőszáli sas\w*",
            r"\bhavasi gyopár\w*", r"\bvadvilág\w*", r"\bélővilág\w*",
            r"\bhegyi növény\w*", r"\bhegyi állat\w*",
        ],
    },
}

DEFAULT_THRESHOLD = 3


@dataclass
class ScoreResult:
    total_score: int
    matched: dict = field(default_factory=dict)
    is_mountain_related: bool = False
    title_or_lead_match: bool = False
    title_matches: list = field(default_factory=list)
    lead_matches: list = field(default_factory=list)


def score_article(title: str, lead: str, content: str,
                   threshold: int = DEFAULT_THRESHOLD) -> ScoreResult:
    """Azonos logika, mint a water_keyword_scorer.score_article()-nél -
    lásd ott a részletes docstringet. Egy kulcsszó csak egyszer számít,
    a title_or_lead_match bizalmi jelző, nem befolyásolja a be/ki-kerülést."""
    full_text = " ".join([title or "", lead or "", content or ""])

    matched = {}
    total = 0
    title_matches = []
    lead_matches = []

    for category, cfg in KEYWORDS.items():
        weight = cfg["weight"]
        hits = []
        for pattern in cfg["patterns"]:
            if re.search(pattern, full_text, flags=re.IGNORECASE):
                hits.append(pattern)
                if re.search(pattern, title or "", flags=re.IGNORECASE):
                    title_matches.append(pattern)
                if re.search(pattern, lead or "", flags=re.IGNORECASE):
                    lead_matches.append(pattern)
        if hits:
            matched[category] = hits
            total += weight * len(hits)

    return ScoreResult(
        total_score=total,
        matched=matched,
        is_mountain_related=(total >= threshold),
        title_or_lead_match=bool(title_matches or lead_matches),
        title_matches=title_matches,
        lead_matches=lead_matches,
    )


if __name__ == "__main__":
    samples = [
        {
            "label": "Alpesi gleccser-túra (minta)",
            "title": "Látványos gleccsertúra nyílt a Zugspitzén",
            "lead": "Az új ösvény a csúcs alatt húzódó gleccser peremén vezet.",
            "content": "A hegymászók és túrázók számára új, biztonságos "
                       "ösvényt alakítottak ki a gleccser mellett, ahonnan "
                       "kilátás nyílik a környező hegyvidékre.",
        },
        {
            "label": "Autókölcsönzős hír (nem hegyvidéki, kontroll-teszt)",
            "title": "Új szabályok léptek életbe az autókölcsönzőknél",
            "lead": "Januártól szigorúbb feltételek mellett bérelhetünk autót.",
            "content": "A biztosítási díjak is emelkedtek.",
        },
    ]
    for sample in samples:
        result = score_article(sample["title"], sample["lead"], sample["content"])
        print(f"\n=== {sample['label']} ===")
        print(f"Pontszám: {result.total_score} (küszöb: {DEFAULT_THRESHOLD}) "
              f"-> Hegyvidéki: {result.is_mountain_related}")
        for category, hits in result.matched.items():
            print(f"  [{category}] találatok: {hits}")
