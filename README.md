# ⚽ World Cup Prediction Bot

Bot Telegram: `/analyze ARG vs FRA` → previsioni per risultati 1°/2° tempo, marcatori, primo cartellino, prima sostituzione. **Dati reali prima, AI solo come fallback.**

## Pipeline dati (in ordine di priorità)

```
1. API-Football  → quote pre-match (media di decine di bookmaker)
2. Sofascore     → stats giocatori dalle ultime 6 partite (gol, gialli, minuti sostituzione)
3. Modello Poisson (model.py) → probabilità calcolate, zero AI
4. Betexplorer scraping → contesto extra solo se 1-2 falliscono
5. GPT-4o        → SOLO per riempire sezioni che i dati non coprono
```

Il footer del messaggio dichiara sempre le fonti usate. Se compare "AI (fallback)", quei numeri sono stime, non quote.

## File

| File | Ruolo |
|---|---|
| `bot.py` | Bot Telegram, `/analyze`, polling continuo |
| `api_football.py` | Quote da API-Football (fonte primaria) |
| `sofascore.py` | Stats giocatori da Sofascore (fonte secondaria) |
| `model.py` | Modello Poisson: λ dalle quote → probabilità per ogni mercato |
| `analyzer.py` | Orchestrazione pipeline + fallback GPT-4o |
| `scraper.py` | Scraping Betexplorer (fallback) |
| `config.py` | Configurazione + codici squadre |
| `Dockerfile` | Per il deploy su Railway |

## Setup

1. **API-Football key (gratis, 2 min)**: registrati su [dashboard.api-football.com](https://dashboard.api-football.com), copia la key nel `.env` (`API_FOOTBALL_KEY=...`). Tier gratuito: 100 richieste/giorno ≈ 25 analisi/giorno.
2. La OpenAI key è già nel `.env`.

### Locale

```
pip install -r requirements.txt
python bot.py
```

### Deploy su Railway (24/7, ~5$/mese)

1. Crea un repo GitHub **privato** con questa cartella (il `.gitignore` esclude già `.env`).
2. Vai su [railway.app](https://railway.app) → New Project → Deploy from GitHub repo → seleziona il repo. Railway rileva il `Dockerfile` automaticamente.
3. Nel servizio → tab **Variables** → aggiungi:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `OPENAI_API_KEY`
   - `API_FOOTBALL_KEY`
4. Deploy. Il bot parte e resta in polling 24/7. Log e restart dalla dashboard Railway (funziona anche da browser mobile).
5. Ogni `git push` rideploya automaticamente.

> Nota: nessuna porta HTTP da esporre — è un worker in polling, Railway lo gestisce così com'è.

## Uso (da Telegram, ovunque tu sia)

```
/analyze ARG vs FRA
/analyze Brazil v Germany
/analyze MEX - USA
```

## Test rapidi senza bot

```
python -c "from analyzer import run_analysis; print(run_analysis('Argentina','France'))"
python scraper.py Argentina France
```

## Limiti noti

- I mercati "primo marcatore/cartellino/sostituzione" non hanno quote pubbliche gratuite: le percentuali vengono **calcolate** dalle statistiche reali Sofascore (frequenze empiriche), non dai bookmaker.
- Sofascore è un endpoint non ufficiale: se cambia, il bot degrada con grazia verso il fallback AI.
- Formazioni ufficiali escono ~1h prima del match: analisi più affidabili a ridosso della partita.
- Un'analisi completa richiede ~30-60s (≈16 chiamate Sofascore con pause di cortesia).

## Sicurezza

`.env` contiene token e key: mai committarlo (già escluso da `.gitignore` e `.dockerignore`). Su Railway le credenziali vanno nelle Variables, non nel repo.
