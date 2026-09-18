# Bitcoin News Digest API

[![CI](https://github.com/duxxxl/bitcoin-news-digest-api/actions/workflows/ci.yml/badge.svg)](https://github.com/duxxxl/bitcoin-news-digest-api/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-Docker-009688)

**Live:** https://bitcoin-news-digest-api.onrender.com · [API-Doku (`/docs`)](https://bitcoin-news-digest-api.onrender.com/docs) · [Health](https://bitcoin-news-digest-api.onrender.com/health)

> Gehostet im Free-Tier von Render: Nach Inaktivität schläft der Container ein, die erste Anfrage kann daher ~1 Minute dauern.

Eine REST-API, die mit einem **Multi-Agent-System** einen kurzen, täglichen **Bitcoin-News-Digest** erzeugt: Ein Orchestrator-Agent delegiert an spezialisierte Sub-Agenten (News-Recherche, Community-Stimmung), prüft deren Ergebnisse gegeneinander und schreibt daraus einen strukturierten Digest mit Quellenangaben und Video-Ideen.

Projekt 4 meiner AI-Engineering-Roadmap — Fokus: **Deployment / MLOps** (FastAPI → Docker → CI → Hosting) plus **Multi-Agent-Architektur**. Baut auf dem [research-agent](https://github.com/duxxxl/research-agent) auf und ist zugleich der erste Baustein einer Bitcoin-YouTube-Automatisierung.

## Was es kann

- `GET /health` – Health-Check
- `GET /` – Info über den Service und seine Endpunkte
- `POST /digest` – erzeugt einen frischen Digest (optional mit `topic`-Fokus)
- `GET /digest/latest` – gibt den zuletzt erzeugten Digest zurück
- `GET /docs` – interaktive API-Oberfläche (automatisch von FastAPI)

## Aufbau (Multi-Agent, Pattern: "Agents as Tools")

```
main.py           FastAPI-App (HTTP-Schicht: Endpunkte, Speichern/Laden)
orchestrator.py   Chefredakteur-Agent: delegiert, prüft, schreibt den Digest
specialists.py    Die Sub-Agenten mit je eigener ReAct-Schleife
tools.py          Die Werkzeuge, gruppiert pro Spezialist
.env              Konfiguration: API-Key + beobachtete Accounts (nicht im Git)
tests/            Tests ohne API-Key/Netz (LLM- und HTTP-Calls sind gemockt)
Dockerfile        Container-Image (python:3.11-slim, uvicorn)
.dockerignore     Hält venv, .env, Tests-Cache & Co. aus dem Image
.github/workflows/ci.yml   GitHub Actions: pytest bei jedem Push/PR
```

Hierarchie zur Laufzeit:

```
API (main.py)  ->  generate_digest()
                       │
                 Orchestrator          Tools: die 2 Spezialisten
                   ├── News-Spezialist    Tools: fetch_rss, web_search, scrape_url
                   └── Social-Spezialist  Tools: bluesky_user_posts, nostr_user_posts,
                                                 mastodon_user_posts, reddit_hot, scrape_url
```

Der Kniff: Für den Orchestrator sind die Spezialisten einfach "Tools" — er gibt ihnen eine Aufgabe in Textform und bekommt ihr Endergebnis als Tool-Result zurück. Jeder Spezialist ist dabei selbst ein vollwertiger Agent mit eigenem Systemprompt, eigenen Werkzeugen und eigener Schleife. Die API-Schicht weiß von alledem nichts — sie ruft nur `generate_digest()` auf.

Datenquellen: RSS-Feeds (Bitcoin Magazine, CoinDesk, Cointelegraph, Decrypt), DuckDuckGo-Websuche, Reddit (öffentlicher JSON-Endpunkt) und offene Social-Protokolle.

`fetch_rss` akzeptiert dabei nicht nur feste Feed-URLs, sondern auch reine Domains: Ist kein Feed-Pfad angegeben, wird die Seite geladen und der Feed aus dem `<link rel="alternate" type="application/rss+xml">`-Tag im HTML-Kopf ausgelesen — so finden auch Feedreader ihre Feeds. Schlägt das fehl, werden die üblichen Pfade (`/feed`, `/rss`, `/feed.xml`, …) durchprobiert.

### Social-Quellen: offene Protokolle statt X

Beobachtete Accounts werden in `.env` konfiguriert (`BLUESKY_ACCOUNTS`, `NOSTR_ACCOUNTS`, `MASTODON_ACCOUNTS`) und zur Laufzeit in den Prompt des Social-Spezialisten injiziert:

| Quelle | Zugang | Warum |
|---|---|---|
| **Bluesky** | Öffentliche AT-Protocol-API, kein Key | Zuverlässig, ein HTTP-Aufruf pro Account |
| **Nostr** | WebSocket zu Relays, keine Keys | Heimat großer Teile der Bitcoin-Community |
| **Mastodon** | Öffentlicher RSS-Feed pro Account | Kein Key, nutzt dieselbe Technik wie die News-Feeds |
| **Reddit** | Öffentlicher JSON-Endpunkt | Stimmungsbild mit Score/Kommentarzahlen |

Alle Quellen liefern dieselbe Post-Struktur und durchlaufen denselben **Frische-Filter**: nach Datum sortieren, alles älter als `max_age_days` (Standard 7) verwerfen. Bleibt nichts übrig, bekommt der Agent eine explizite Warnung statt der alten Posts — veraltete Daten sind schlimmer als gar keine, weil sie unbemerkt als „aktuell" im Digest landen.

### Warum X/Twitter nicht genutzt wird

X hat den freien Zugang faktisch geschlossen. Beide verbliebenen Wege wurden implementiert und getestet, beide scheiterten:

- **Syndication-Endpunkt**: liefert zwar 100 Posts, aber nur einen eingefrorenen Cache — bei einem täglich postenden Account war der neueste Beitrag über ein Jahr alt.
- **Nitter-Mirrors** (xcancel, nitter.net, nitter.poast.org): antworten, liefern aber keine Feed-Einträge mehr.

Der offizielle Weg wäre die kostenpflichtige X-API. Für dieses Projekt war die bessere Entscheidung, auf offene Protokolle zu wechseln. Der Code (`x_user_posts` mit Strategie-Kette, `x_debug_timeline` zur Diagnose) bleibt im Repo erhalten, wird dem Agenten aber nicht mehr als Werkzeug angeboten — er würde nur verlässlich einen Zug verschwenden.

Diagnose-Werkzeug, falls sich die Lage ändert:

```bash
python -c "from tools import x_debug_timeline; print(x_debug_timeline('blocktrainer'))"
```

## Ablauf beobachten (Debugging)

Die API gibt nur das Endergebnis zurück. Um zu sehen, **wie** die Agenten arbeiten — welches Tool mit welchen Argumenten aufgerufen wird, was zurückkommt, was jeder Agent daraus schließt, plus Tokenverbrauch und Kostenschätzung:

```bash
python run_digest.py                 # voller Ablauf im Terminal
python run_digest.py "ETF flows"     # mit Fokus-Thema
python run_digest.py --full          # Tool-Ergebnisse ungekürzt
python run_digest.py --quiet         # nur der fertige Digest
```

Technisch läuft das über einen optionalen `on_event`-Callback, den `generate_digest()` an alle Agenten durchreicht. Die Agenten selbst enthalten **kein einziges `print()`** — sie melden nur Ereignisse. Wer zuhört, entscheidet, was damit passiert: im Terminal wird gedruckt, in der API wird nichts übergeben und damit nichts aufgezeichnet. Genau diese Trennung braucht man später für echtes Monitoring (Phase 7 der Roadmap).

## Lokal starten

```bash
# 1. virtuelle Umgebung
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# 2. Abhängigkeiten
pip install -r requirements.txt

# 3. API-Key hinterlegen
copy .env.example .env       # Windows (cp auf macOS/Linux)
# dann ANTHROPIC_API_KEY in .env eintragen

# 4. Server starten
uvicorn main:app --reload
```

Dann `http://127.0.0.1:8000/docs` im Browser öffnen und `POST /digest` ausprobieren.

Digest per Kommandozeile anfordern:

```bash
curl -X POST http://127.0.0.1:8000/digest -H "Content-Type: application/json" -d "{\"topic\": \"ETF flows\"}"
```

## Tests

```bash
pytest
```

21 Tests (API-Endpunkte + Tools). Sie brauchen keinen API-Key und keinen Netzzugang — LLM- und HTTP-Aufrufe sind durch Fakes ersetzt. Deshalb laufen sie auch ohne Secrets in der CI.

## Deployment

Der Weg vom Code zur öffentlich erreichbaren API:

```
git push  ->  GitHub Actions (pytest)  ->  Render baut das Dockerfile  ->  https://bitcoin-news-digest-api.onrender.com
```

### 1. Docker

```bash
docker build -t bitcoin-news-digest-api .
docker run --rm -p 8000:8000 --env-file .env bitcoin-news-digest-api
```

Das Dockerfile kopiert zuerst nur `requirements.txt` und installiert die Abhängigkeiten, erst danach den restlichen Code. So bleibt der langsame `pip install`-Schritt im Build-Cache, solange sich die Abhängigkeiten nicht ändern. Der Port kommt aus `$PORT` (setzen Hoster wie Render selbst), lokal fällt er auf 8000 zurück. Secrets landen **nicht** im Image — `.env` steht in `.dockerignore` und wird zur Laufzeit übergeben.

### 2. CI mit GitHub Actions

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) läuft bei jedem Push auf `main` und bei jedem Pull Request: Python 3.11 aufsetzen, Abhängigkeiten installieren (mit pip-Cache), `pytest -q`. Der Status steht im Badge oben.

### 3. Hosting auf Render

Render ist mit dem Repo verbunden und baut bei jedem Push auf `main` das Dockerfile neu. Konfiguration:

| Einstellung | Wert |
|---|---|
| Runtime | Docker (Dockerfile im Repo-Root) |
| Plan | Free |
| Health-Check | `/health` |
| Environment | `ANTHROPIC_API_KEY` (plus optional die `*_ACCOUNTS`-Variablen aus `.env.example`) |

Bekannte Einschränkungen des Free-Setups:

- **Cold Start:** Der Container schläft nach Inaktivität ein; die erste Anfrage weckt ihn auf.
- **Kein dauerhafter Speicher:** `data/latest_digest.json` liegt im Container-Dateisystem und ist nach jedem Neustart/Deploy weg. `GET /digest/latest` liefert dann 404, bis wieder ein Digest erzeugt wurde.
- **`POST /digest` braucht einen gültigen API-Key** in den Render-Umgebungsvariablen; ohne ihn antwortet der Endpunkt mit 502. `/health`, `/` und `/docs` funktionieren immer.

## Nächste Schritte

- [x] Dockerfile hinzufügen und Container lokal testen
- [x] GitHub Actions: `pytest` bei jedem Push automatisch ausführen
- [x] Auf Render hosten
- [ ] Scheduler: einmal täglich automatisch einen Digest erzeugen
- [ ] Persistenz: `latest_digest.json` durch PostgreSQL ersetzen (später optional Redis als Cache)
- [ ] Zum Lernen: dieselbe API auf AWS (ECS Fargate + Application Load Balancer) deployen
