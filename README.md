# hegycsucsok-digest

Napi hegyvidéki (hobbi-jellegű) hír-digest a hegycsucsok.hu oldalhoz. A
`vizpartok_digest.py` (vizpartok.hu) bevált mintáját követi - lásd a
`hegycsucsok_digest.py` fájl fejlécében a részletes leírást.

## Fájlok

- `hegycsucsok_digest.py` - fő script (RSS-begyűjtés → AI-válogatás →
  összefoglalás → WP-piszkozat → email)
- `mountain_keyword_scorer.py` - utólagos minőségbiztosítási pontozó
- `hegycsucsok-daily-digest.yml` - GitHub Actions workflow (`.github/workflows/`
  alá másolandó)
- `requirements.txt` - Python-függőségek

## Beállítás

1. Hozz létre egy Application Password-öt a hegycsucsok.hu WordPress
   admin felületén (Felhasználók → Profil → Application Passwords).
2. Állítsd be a GitHub Secrets-eket (Settings → Secrets and variables →
   Actions), **10 db**:
   - `ANTHROPIC_API_KEY`
   - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`
   - `HEGYCSUCSOK_EMAIL_TO`
   - `HEGYCSUCSOK_WP_SITE_URL`
   - `HEGYCSUCSOK_WP_USER`
   - `HEGYCSUCSOK_WP_APP_PASSWORD`
3. Másold a `hegycsucsok-daily-digest.yml`-t a `.github/workflows/`
   könyvtárba.
4. Első futtatás: GitHub Actions fülön "Run workflow" (workflow_dispatch) -
   ne várd meg a napi cron-t, hogy gyorsan lásd, működik-e minden.

## Nyitott pontok, amiket érdemes az első pár futás után átnézni

- **reuters.com forrás**: a Reuters 2020 óta nem ad ki saját RSS-t, ezért
  egy Google News-alapú proxy-feedet használunk (`REUTERS_FEED_URL` a
  scriptben). Érdemes megnézni a futási logban, hogy hoz-e egyáltalán
  releváns, letölthető cikkeket - ha nem, kivehető a `RSS_FEEDS`
  szótárból.
- **WordPress-struktúra**: a hegycsucsok.hu ACF-mezői/egyéni taxonómiái
  MÉG NINCSENEK feltérképezve. A `wp_create_draft()` jelenleg a
  vizpartok.hu-nál bevált, ACF nélküli mintát követi (sima "posts"
  végpont, kategória + címke taxonómia, natív featured_media kép). Ha
  kiderül, hogy vannak/lesznek ACF mezők, ezt a függvényt érdemes
  felülvizsgálni.
- **DAILY_TOTAL_LIMIT/PURE_SOURCE_DAILY_LIMIT**: a vizpartok.hu-nál bevált
  6/2 értékkel indulunk - finomítható az első pár hetes tapasztalat
  alapján.
- **mountain_keyword_scorer.py küszöbértéke**: placeholder, valós
  cikkeken tesztelve finomítandó (mint a water_keyword_scorer.py-nál).

## Facebook-posztolás

Jelenleg NEM része ennek a projektnek - a felhasználó kérése szerint
opcionálisan, később kerülhet szóba (a vizpartok.hu-s Facebook-modul
mintájára).
