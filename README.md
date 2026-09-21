# The Colour Works Insights

A production-oriented web app for securely uploading Insights Discovery PDFs, extracting participant profile data, and delivering private token pages for Learning Experience activities.

## What It Does

- Uploads and validates Insights Discovery PDF files.
- Extracts and stores the participant name, conscious colour dynamics, wheel position, key sections, summaries, raw source excerpts, confidence scores, and parse issues.
- Creates a secure private URL for each participant.
- Shows a TCW-inspired participant page with a branded colour bar graph, an original 72-position wheel plot, expandable profile sections, and deployed activities.
- Provides admin sign-in for uploads, cohort bulk upload, parse visibility, and Learning Experience management.
- Provides facilitator sign-in tied to a single Learning Experience.
- Includes the first facilitator activity: a colour card game with 200 replaceable seed cards, 16 unique cards per participant, keep/give choices, same-cohort transfer enforcement, and persisted received cards.

## Important Brand And IP Notes

The repository was empty when this app was built, so there were no licensed logo files or brand font files to reuse. The UI uses a text lockup and TCW-inspired colours consistent with the nearby TCW project palette:

- Navy `#123c69`
- Teal `#0f5e73`
- Red `#d92d20`
- Yellow `#f7b500`
- Green `#16803c`
- Blue `#2563eb`

To add official assets later, replace the text lockup in `insights_app/web.py` and update the palette/fonts in `insights_app/static/app.css`.

The 72 Type Wheel display is a clean original 72-position visualization. It deliberately does not copy proprietary Insights wheel artwork.

## Setup

1. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Create your environment file:

   ```bash
   cp .env.example .env
   ```

3. Generate the admin password hash:

   ```bash
   python scripts/hash_password.py
   ```

   Put the printed value into `ADMIN_PASSWORD_HASH` in `.env`. The admin username is always `admin`; the plaintext password is never stored in code.

4. Generate a secret key:

   ```bash
   python - <<'PY'
   import secrets
   print(secrets.token_urlsafe(32))
   PY
   ```

   Put the printed value into `SECRET_KEY`.

5. Initialize the database:

   ```bash
   python scripts/init_db.py
   ```

6. Run locally:

   ```bash
   python scripts/run_dev.py
   ```

   Open `http://127.0.0.1:8000/admin`.

## Environment Variables

- `ADMIN_PASSWORD_HASH`: required for admin login.
- `SECRET_KEY`: required for sessions, CSRF, and participant action tokens.
- `APP_BASE_URL`: public base URL used when admin pages show participant links.
- `APP_SECURE_COOKIES`: set to `true` behind HTTPS.
- `DATABASE_PATH`: SQLite database path. Use durable storage in production.
- `UPLOAD_DIR`: folder for uploaded PDFs. Use durable private storage in production.
- `MAX_UPLOAD_BYTES`: default 20 MB.
- `SESSION_HOURS`: default 12.

## Deployment

The app exposes a WSGI callable at `wsgi:application` and includes:

- `Procfile` for platforms that run Gunicorn.
- `Dockerfile` for container deployment.
- `.python-version` for Python platform selection.
- `app.py`, `vercel.json`, and `api/index.py` for Vercel Python deployments.

### Vercel

When importing into Vercel, use the repository root as the Root Directory. Vercel should auto-detect Flask from the top-level `app.py`; if it asks for a framework, choose Flask/Python, or Other if Flask is not offered. The committed `vercel.json` rewrites all traffic to the Flask wrapper, which passes requests into the Insights app.

Set these Vercel Environment Variables before deploying:

- `ADMIN_PASSWORD_HASH`
- `SECRET_KEY`
- `APP_BASE_URL`
- `APP_SECURE_COOKIES=true`

Vercel preview deployments can start with the default `/tmp/insights` data directory, but that storage is temporary. For real participant use, move the database and PDF uploads to durable services such as managed Postgres plus private object storage before sending links to clients.

For production:

- Serve only over HTTPS.
- Set `APP_SECURE_COOKIES=true`.
- Mount `DATABASE_PATH` and `UPLOAD_DIR` on persistent private storage.
- Back up the SQLite database and uploads directory together.
- Do not commit real participant PDFs or the `.env` file.

## Validating PDF Extraction

Run the parser against a profile:

```bash
python scripts/parse_pdf.py path/to/profile.pdf
```

Automated tests generate a sample-like PDF and check the required example values:

- Blue `0.20` / `3%`
- Green `2.80` / `47%`
- Yellow `4.60` / `77%`
- Red `5.73` / `95%`
- Wheel position `24: Directing Motivator (Classic)`

To validate a real attached sample without committing it:

```bash
INSIGHTS_SAMPLE_PDF="/private/path/to/sample.pdf" python -m unittest tests.test_pdf_parser
```

## Running Tests

```bash
python -m unittest discover
```

The suite covers:

- sample-PDF-style extraction
- participant token authorization
- admin/facilitator role boundaries
- bulk upload
- card deployment idempotency
- no duplicate cards per participant
- card transfer rules
- participant action-token protection

## Data Model

The SQLite schema lives in `migrations/001_schema.sql`. It includes:

- users and sessions
- participants and uploads
- raw excerpts, normalized profile data, parse confidence, and parse issues
- learning experiences and memberships
- activities, deployments, deployment audit events
- card bank, card assignments, and card transfers

## Current Limitations

- The seeded card wording is placeholder content and is marked replaceable in the database.
- The app uses deterministic PDF text extraction. OCR is not bundled because the sample profile text extracts cleanly; add OCR only if scanned PDFs become a real requirement.
- The participant token is a private capability URL. Treat it like sensitive personal data and share it only with the participant.
