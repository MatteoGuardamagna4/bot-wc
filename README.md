# ⚽ World Cup Prediction Bot

Bot Telegram: `/analyze ARG vs FRA` → previsioni per risultati 1°/2° tempo, marcatori, primo cartellino, prima sostituzione. **Dati reali prima, AI solo come fallback.**

## Pipeline dati (in ordine di priorità)

```
1. API-Football (piano Pro) — FONTE PRINCIPALE PER TUTTO:
   • quote pre-match: 1X2, over/under, primo tempo (media bookmaker)
   • stats giocatori: /fixtures/events delle ultime 5 partite
     (gol con minuto, gialli, sostituzioni)
2. ESPN          → stats giocatori di riserva (gratis, nessuna chiave)
3. Sofascore     → solo se SOFASCORE_ENABLED=true (spesso blocca via IP)
4. Modello Poisson (model.py) → probabilità calcolate, zero AI
5. GPT-4o        → SOLO per riempire sezioni che i dati non coprono
```

Costo per analisi: ~15 richieste API-Football (su 7.500/giorno del Pro).

**Con il piano Pro di API-Football** il bot usa le quote reali dei bookmaker per: risultato finale (1X2 + over/under → gol attesi λ) e **primo tempo** (quote 1H winner → split 1T/2T calibrato dal mercato invece che da medie storiche).

**Stats giocatori**: Sofascore viene tentato per primo ma è spesso bloccato (TLS fingerprinting; il bot usa `curl_cffi` e in caso di blocco totale si auto-disabilita per 30 min). **ESPN è la fonte effettiva** nella maggior parte dei casi: API pubbliche, nessuna chiave, dati per giocatore (gol, gialli, minuti di sostituzione) dalle ultime 5 partite.

## Accesso privato

Il bot risponde **solo agli utenti autorizzati**:

- Il tuo chat ID (`TELEGRAM_CHAT_ID` nel `.env`) è sempre autorizzato.
- Altri utenti devono inviare `/login CODICE` con il codice impostato in `ACCESS_CODE` nel `.env` (**cambia quello di default!**).
- Gli autorizzati vengono salvati in `authorized.json` (su Railway il file si azzera a ogni redeploy: gli ospiti dovranno rifare `/login`).
- Tentativi non autorizzati vengono loggati e ricevono un messaggio di rifiuto.

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
   - `ACCESS_CODE`
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
python sofascore.py Canada          # test stats giocatori (il fix più importante)
python scraper.py Argentina France  # test scraping Betexplorer
python -c "from analyzer import run_analysis; print(run_analysis('Argentina','France'))"
```

## Limiti noti

- I mercati "primo marcatore/cartellino/sostituzione" non hanno quote pubbliche gratuite: le percentuali vengono **calcolate** dalle statistiche reali Sofascore (frequenze empiriche), non dai bookmaker.
- Sofascore è un endpoint non ufficiale: se cambia, il bot degrada con grazia verso il fallback AI.
- Formazioni ufficiali escono ~1h prima del match: analisi più affidabili a ridosso della partita.
- Un'analisi completa richiede ~30-60s (≈16 chiamate Sofascore con pause di cortesia).

## Sicurezza

`.env` contiene token e key: mai committarlo (già escluso da `.gitignore` e `.dockerignore`). Su Railway le credenziali vanno nelle Variables, non nel repo.
