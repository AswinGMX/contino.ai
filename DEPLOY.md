# Deploying contino.ai (frontend and backend separately)

## What runs where

```
 Browser ──► Frontend (Vercel, project root: web/)
               │  VITE_API_URL=https://<api>.vercel.app
               ▼
             API (Vercel, project root: api/)  ───────┐
                                                      ├──► Postgres (Neon)  +  Cloudflare R2 (images)
             (optional) Worker  ──────────────────────┘     shared by both
```

| Piece | Host | Why |
|---|---|---|
| **Frontend** | Vercel | Plain static site, a perfect fit. |
| **API** | Vercel (Python serverless) | Short requests only: read, approve, upload, queue jobs. |
| **Jobs (pictures)** | Inside the Vercel API | No worker needed. While a project is open, the browser tab asks the API to run the next job (`POST /api/projects/{id}/jobs/run`); each call draws one picture in its own function (up to 300 s). A database lock makes sure a job runs only once. If the tab is closed, queued jobs wait until the project is opened again. |
| **Worker (optional)** | An always-on host | Only if you want jobs to keep running with every tab closed. See step 3. |
| **Data** | Neon Postgres + Cloudflare R2 | All data lives in PostgreSQL and every file in R2, so nothing depends on a server's disk. |

> **Vercel request limit is about 4.5 MB.** Shot Planner PDFs and uploaded pictures must be smaller than that when the API runs on Vercel.

---

## Step 1 · Create the database and the image bucket

1. **Postgres:** create a free database at [neon.tech](https://neon.tech), or via Vercel's Neon integration.
   - Copy the **pooled** connection string, the host with `-pooler`, as `DATABASE_URL`.
   - The app creates its tables on first start.
2. **Cloudflare R2:** create a bucket, e.g. `contino-images`, and keep it **private**.
   - Create an R2 API token with read/write access to the bucket.
   - Note the **Account ID**, **Access Key ID** and **Secret Access Key**.

## Step 2 · Deploy the API on Vercel

1. **New Project** in Vercel → import the repo → **Root Directory: `api`** → **Framework Preset: Other** (no build or output settings).
   - `api/` is self-contained: it holds `index.py`, `app/`, `prompts/` and `config.yaml`.
   - `api/vercel.json` builds `index.py` with Python and sends every path to it.
   - `api/.vercelignore` keeps tests and local files out of the upload.
2. **Environment variables** (Settings → Environment Variables):

   | Name | Value |
   |---|---|
   | `OPENAI_API_KEY` | your team key |
   | `IMAGE_MODEL` / `TEXT_MODEL` / `REVIEW_MODEL` | the model names your key allows |
   | `DATABASE_URL` | Neon pooled connection string |
   | `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET` | from step 1 |
   | `CORS_ORIGINS` | the frontend URL, e.g. `https://contino.vercel.app` (fill in after step 4) |
   | `CORS_ORIGIN_REGEX` | optional, to allow preview URLs: `https://contino-.*\.vercel\.app` |
   | `COOKIE_CROSS_SITE` | `true` (frontend and API are on different domains) |
   | `EMBEDDED_WORKER` | `false` |
   | `SESSION_SECRET` | a long random string, the same on the API and the worker: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
   | `DB_POOL_MIN` / `DB_POOL_MAX` | `0` / `5` (serverless-friendly) |
   | `API_TOKEN` | optional: a team access code people must enter when they sign in |

3. **Deploy**, then open `https://<api>.vercel.app/api/healthz`. It should return `{"ok": true}`.
   - `/api/readyz` also checks the database and the R2 bucket.

## Step 3 · (Optional) Run an always-on worker

Skip this step if the in-API jobs are enough. Add a worker only when pictures must keep being drawn with every browser tab closed; then also set `REQUEST_JOBS=false` on the API.

Use the existing `api/Dockerfile` with the repo root as build context (the same as `docker-compose.yml`).

**Railway or Render:**
1. New service from the repo → **Dockerfile path:** `api/Dockerfile` (build context: repo root).
2. **Start command:** `python -m app.worker`
3. **Environment variables:** the **same** values as the API: `OPENAI_API_KEY`, `SESSION_SECRET`, `*_MODEL`, `DATABASE_URL` and the `R2_*` values.
   - The worker doesn't need `CORS_*` or `COOKIE_*`.
4. **Scaling:** one instance is enough for a hackathon. Add more instances to make images faster.
   - Jobs are claimed with database locks, so two workers never do the same job.

**Any Linux VM:**

```bash
docker build -f api/Dockerfile -t contino-worker .
docker run --env-file .env contino-worker python -m app.worker
```

## Step 4 · Deploy the frontend on Vercel

1. **New Project** → same repo → **Root Directory: `web`**.
   - The framework (Vite) is detected; `web/vercel.json` adds the single-page-app rewrite.
2. **Environment variable:**

   | Name | Value |
   |---|---|
   | `VITE_API_URL` | the API URL from step 2, e.g. `https://contino-api.vercel.app` (no trailing slash) |

3. **Deploy.**
4. Put this frontend URL into the API's `CORS_ORIGINS`, then **redeploy the API**.

## Step 5 · Check it end to end

1. **Open the frontend URL.** The budget card in the sidebar should load. If it doesn't, check `CORS_ORIGINS` on the API.
2. **Upload a small planner PDF** (under 4.5 MB). Within a minute the plan should appear.
   - If it stays on "Reading your Shot Planner…", check the API's function logs in Vercel.
3. **Create a character picture.** It appears in about a minute while the project is open. Images are served from R2 through short-lived links.

---

## How the frontend finds the API

- **In production:** `VITE_API_URL` is baked into the frontend at build time. Every request goes to `VITE_API_URL + /api/...`, and so do image and download links.
- **In local dev:** leave `VITE_API_URL` empty. The Vite dev server forwards `/api` to `http://localhost:8000`, so nothing changes locally.
- **After changing `VITE_API_URL` in Vercel:** redeploy the frontend, because the value is fixed at build time.

## Settings reference (deployment only)

| Variable | Where | What it does |
|---|---|---|
| `VITE_API_URL` | frontend | Backend URL the browser calls |
| `CORS_ORIGINS` | API | Comma-separated frontend URLs allowed to call the API |
| `CORS_ORIGIN_REGEX` | API | Pattern for extra allowed origins (e.g. Vercel previews) |
| `COOKIE_CROSS_SITE` | API | `true` → sign-in cookie uses `SameSite=None; Secure`, needed across domains |
| `EMBEDDED_WORKER` | API | `false` on Vercel. It's also ignored there automatically. |
| `REQUEST_JOBS` | API | Leave unset: on Vercel, the open tab runs jobs through the API. `false` = a separate worker runs them. |
| `DB_POOL_MIN` / `DB_POOL_MAX` | API, worker | Database connections per process |
| `OUTPUT_DIR` | API | Where exports are written. It defaults to `/tmp/output` on Vercel. |

## Alternative: everything except the frontend on one host

If you'd rather avoid serverless limits, the brief's own setup works too:
- **Frontend:** Vercel (as in step 4).
- **API + worker:** one Railway, Render or Fly service.
  - Use `api/Dockerfile` with the default command, which starts the API.
  - Set `EMBEDDED_WORKER=true` so it also runs the worker.
- Point `VITE_API_URL` at that service.
