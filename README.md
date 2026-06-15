# ⚽ World Cup Prediction Bot

Bot Telegram: `/analyze ARG vs FRA` → previsioni per i 4 risultati finali più probabili (con quote bookmaker), risultati 1°/2° tempo, marcatori, primo cartellino e prima sostituzione (i 2 giocatori più probabili **per squadra**). **Dati reali prima, AI solo come fallback.**

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

**Con il piano Pro di API-Football** il bot usa le quote reali dei bookmaker per i gol attesi λ (1X2 + over/under) e per le quote dei singoli risultati esatti (mercato **Correct Score**). Il messaggio mostra i **4 risultati finali più probabili** con la quota reale del bookmaker accanto a ciascuno; se un risultato esatto non è quotato, viene usata la quota equa del modello (segnata con `*`). I **risultati di 1° e 2° tempo** usano le quote 1H winner del bookmaker quando disponibili, altrimenti uno split calibrato sulla quota-gol storica di ogni squadra.

**Cartellini e sostituzioni**: il bot mostra i **2 giocatori più probabili per squadra** (non più solo i più probabili in assoluto), così vedi i candidati di entrambe le formazioni.

**Stats giocatori** (ordine reale: API-Football `/fixtures/events` → ESPN → Sofascore): API-Football è la fonte primaria; **ESPN** è il fallback gratuito più affidabile (API pubbliche, nessuna chiave). **Sofascore** è l'ultimo fallback, attivo solo con `SOFASCORE_ENABLED=true`: spesso bloccato (TLS fingerprinting; usa `curl_cffi` e in caso di blocco totale si auto-disabilita per 30 min).

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
| `web.py` | Dashboard web (Flask): lista partite + predizioni, login con `ACCESS_CODE` |
| `static/index.html` | Frontend del dashboard (HTML/CSS/JS, nessun build) |
| `api_football.py` | Quote + calendario da API-Football (fonte primaria) |
| `espn.py` | Stats giocatori da ESPN (fallback gratuito) |
| `sofascore.py` | Stats giocatori da Sofascore (ultimo fallback, opt-in) |
| `model.py` | Modello Poisson: λ dalle quote → probabilità per ogni mercato |
| `analyzer.py` | Orchestrazione pipeline + fallback GPT-4o (`analyze()` / `run_analysis()`) |
| `scraper.py` | Scraping Betexplorer (contesto per l'AI) |
| `config.py` | Configurazione + codici squadre |
| `Dockerfile` | Per il deploy su Railway |

## Setup

1. **API-Football key (gratis, 2 min)**: registrati su [dashboard.api-football.com](https://dashboard.api-football.com), copia la key nel `.env` (`API_FOOTBALL_KEY=...`). Tier gratuito: 100 richieste/giorno ≈ 25 analisi/giorno.
2. La OpenAI key è già nel `.env`.

### Locale

```
pip install -r requirements.txt
python bot.py          # bot Telegram
python web.py          # dashboard web su http://localhost:8000
```

## Dashboard web

Interfaccia web minimale per usare le predizioni senza Telegram: si apre il sito,
si inserisce l'`ACCESS_CODE`, si sceglie una partita dalla lista di quelle in
programma e si ottengono le predizioni (risultati finali con quote, tempi,
marcatori, cartellini e sostituzioni per squadra).

- Backend: `web.py` (Flask) — riusa la stessa pipeline del bot (`analyzer.analyze`).
- Frontend: `static/index.html` — nessun framework, nessun build.
- Accesso protetto: login con `ACCESS_CODE` (lo stesso del bot), sessione via cookie firmato (`FLASK_SECRET`).
- API: `GET /api/matches` (lista partite) e `GET /api/predict?home=&away=` (predizione).
- In produzione gira con gunicorn: `gunicorn web:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`.

### Deploy su Railway (24/7, ~5$/mese)

1. Crea un repo GitHub **privato** con questa cartella (il `.gitignore` esclude già `.env`).
2. Vai su [railway.app](https://railway.app) → New Project → Deploy from GitHub repo → seleziona il repo. Railway rileva il `Dockerfile` automaticamente.
3. Nel servizio → tab **Variables** → aggiungi:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `OPENAI_API_KEY`
   - `API_FOOTBALL_KEY`
   - `ACCESS_CODE`
   - `FLASK_SECRET` (stringa lunga e casuale; serve al sito per firmare i cookie)
4. Deploy. Il bot parte e resta in polling 24/7. Log e restart dalla dashboard Railway (funziona anche da browser mobile).
5. Ogni `git push` rideploya automaticamente.

> Nota: il servizio bot non espone porte HTTP — è un worker in polling, Railway lo gestisce così com'è.

#### Aggiungere il dashboard web (secondo servizio)

Bot e sito **condividono lo stesso repo**, cambiano solo il comando d'avvio:

1. Nel progetto Railway → **New** → **GitHub Repo** → seleziona lo **stesso** repo
   (crea un secondo servizio dallo stesso codice).
2. Nel nuovo servizio → **Settings** → **Deploy** → **Custom Start Command**:
   ```
   gunicorn web:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
   ```
   (sovrascrive il `CMD` del Dockerfile, che resta `python bot.py` per il bot).
3. **Variables**: copia le stesse del bot (almeno `ACCESS_CODE`, `API_FOOTBALL_KEY`,
   `OPENAI_API_KEY`, `FLASK_SECRET`). Suggerimento: con i *Shared Variables* di
   Railway le definisci una volta sola per entrambi i servizi.
4. **Settings** → **Networking** → **Generate Domain**: ottieni l'URL pubblico del
   sito (HTTPS automatico). `$PORT` è fornito da Railway, gunicorn ci si lega da solo.
5. Apri l'URL, inserisci l'`ACCESS_CODE` e seleziona una partita.

> Costo: i due servizi stanno comodamente nel piano Hobby (~5$/mese di utilizzo
> incluso) per un uso personale.

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

- I mercati "primo marcatore/cartellino/sostituzione" non hanno quote pubbliche: le percentuali vengono **calcolate** dalle statistiche reali dei giocatori (frequenze empiriche su API-Football/ESPN), non dai bookmaker.
- ESPN/Sofascore sono endpoint non ufficiali: se cambiano, il bot degrada con grazia verso il fallback AI.
- Formazioni ufficiali escono ~1h prima del match: analisi più affidabili a ridosso della partita.
- Un'analisi completa costa ~15 richieste API-Football e richiede pochi secondi (di più solo se si attiva Sofascore, che aggiunge pause di cortesia).

## Sicurezza

`.env` contiene token e key: mai committarlo (già escluso da `.gitignore` e `.dockerignore`). Su Railway le credenziali vanno nelle Variables, non nel repo.
