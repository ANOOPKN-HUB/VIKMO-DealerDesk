# VIKMO DealerDesk

**Track: AI/ML**

DealerDesk is a motorcycle-parts dealer portal with a Django REST API, PostgreSQL data store, and React frontend. It lets dealers browse products and current inventory, prepare orders, ask an assistant to search parts and stock, and review four-week demand forecasts and reorder suggestions.

## Features completed

- Product catalogue, dealer records, inventory updates, and paginated API responses.
- Draft, confirm, and cancel order flows with database transactions, stock checks, and decimal GST calculations.
- Gemini assistant with `search_parts`, `check_stock`, and `create_draft_order` tools; database-grounded price and stock replies; and a clearly identified local fallback.
- Four-week forecast CSV and API-backed reorder suggestions, using historical demand, planned promotions, and a Diwali adjustment.
- Optional paginated supplier sync with retries, validation, and transactional updates; a local supplier mock is included.
- React/Vite dealer portal.

## Requirements

- Python 3.11 or newer
- PostgreSQL, listening on the host and port configured in `DATABASE_URL` (SQLite is deliberately rejected)
- Node.js/npm for the optional React portal
- A Google Gemini API key for live assistant calls. Without a key, the assistant uses the local database fallback.

## Installation and setup (Windows PowerShell)

From the repository root, create the virtual environment, install Python dependencies, and create a local environment file if needed:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

If `.venv` already exists, activate it and skip environment creation. If the Python launcher does not have 3.11, use an installed Python 3.11+ version. Start PostgreSQL and create the database and role named in your `DATABASE_URL`. Edit `.env` to match those local credentials. The example URL in `.env.example` is only a local template; use a password that matches your PostgreSQL setup. Set `GEMINI_API_KEY` for live Gemini calls and optionally set `GEMINI_MODEL`. Keep `.env` private and out of Git.

## Run the project

With PostgreSQL running and `.env` configured, from the repository root:

```powershell
python manage.py migrate --noinput
python manage.py seed_data
python manage.py runserver 0.0.0.0:8000
```

The API health endpoint should return `{"status":"ok"}` at `http://127.0.0.1:8000/api/health/`.

For the React portal, open a second terminal:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Open the local URL printed by Vite (usually `http://localhost:5173`). The Vite development server proxies `/api` to `http://127.0.0.1:8000`; keep the Django server running too.

The optional supplier mock runs in another terminal from the repository root:

```powershell
python supplier_mock/mock_supplier.py
```

Set `SUPPLIER_API_URL` and `SUPPLIER_API_KEY` in `.env` to the URL and key printed by the mock before calling the supplier sync endpoint.

## LLM provider and model

The assistant uses the Google Gemini REST API. The default model is `gemini-3-flash-preview`; override it with a model available to your Gemini API key through `GEMINI_MODEL` and provide `GEMINI_API_KEY` for live calls. The model can call `search_parts`, `check_stock`, and `create_draft_order`. Server-side handlers validate arguments, query PostgreSQL, and create drafts only. The project retries transient provider errors and labels its local database fallback. If the model is unavailable, check `GEMINI_MODEL`, confirm the Gemini API is enabled for the Google Cloud project, and use an API key from that project. The app does not print or expose the key. A fallback response demonstrates database search, not a live Gemini tool call. Confirm a successful live call before describing the provider as verified in a demo.

## Tests and evaluation

From the repository root with PostgreSQL configured:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test core
python smoke_test.py --base-url http://127.0.0.1:8000 --track aiml
python ml/forecast_model.py --evaluate
```

The smoke test writes a test order, so use a disposable database when you do not want test data in your working database. Regenerate the committed forecast with `python ml/forecast_model.py --write`. The recorded verification run passed 21 Django tests and all 10 smoke checks against an isolated PostgreSQL database; the detailed record is in `PROJECT_AUDIT.md`.

## Video walkthrough link

Demo video walkthrough: [Watch the Loom walkthrough](https://www.loom.com/share/e155c0bf43df4f21b7f95b49c0d61b7b)

## Assumptions and limitations

- PostgreSQL is required; SQLite is not supported. The app and API expect their configured local services to be running.
- Product retrieval is lexical and explainable, with no embedding model or vector index. It may miss synonyms, misspellings, and paraphrases; retrieval recall has not been measured on a labeled query set.
- Gemini availability depends on the configured key, model access, and provider service load. The local fallback keeps search usable but does not prove a live LLM tool call.
- Forecast evidence is limited to three held-out rolling origins. The selected event-adjusted blend only slightly improves on the recent-average baseline. Rare-demand SKUs receive no dedicated intermittent-demand method, and a SKU with no history forecasts zero rather than borrowing from similar products.
- Promotion uplift is a bounded global estimate and Diwali receives a fixed 5% adjustment. These are planning heuristics, not purchasing guarantees.

## Project notes

- Endpoint requirements: [API_CONTRACT.md](API_CONTRACT.md)
- Technical decisions and forecast metrics: [DECISIONS.md](DECISIONS.md)
- Verification status: [PROJECT_AUDIT.md](PROJECT_AUDIT.md)
- Code tour: [PROJECT_TUTORIAL.md](PROJECT_TUTORIAL.md)
- Walkthrough outline: [VIDEO_WALKTHROUGH.md](VIDEO_WALKTHROUGH.md)
- Implementation log: [CODEX_FIX_LOG.md](CODEX_FIX_LOG.md)
