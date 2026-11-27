# Deploying contino.ai (frontend and backend separately)

## What runs where

```
 Browser ──► Frontend (Vercel, project root: web/)
               │  VITE_API_URL=https://<api>.vercel.app
               ▼
             API (Vercel, project root: repo root)  ──┐
                                                      ├──► Postgres (Neon)  +  Cloudflare R2 (images)
             Worker (Railway / Render / Fly)  ────────┘     shared by both
             runs: python -m app.worker
```

| Piece | Host | Why |
|---|---|---|
| **Frontend** | Vercel | Plain static site, a perfect fit. |
| **API** | Vercel (Python serverless) | Short requests only: read, approve, upload, queue jobs. |
| **Worker** | An always-on host (Railway, Render, Fly or a VM) | Draws the images. One image takes 30–90 s, and a background worker must stay running. Vercel functions are frozen between requests, so the worker **cannot** run on Vercel. |
| **Data** | Neon Postgres + Cloudflare R2 | Vercel has no permanent disk, so the app runs in **cloud mode** (`STORAGE_MODE=cloud`). |

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

1. **New Project** in Vercel → import the repo → **Root Directory: the repo root** (leave as `/`).
   - `vercel.json` at the root builds `api/index.py` with Python.
   - `.vercelignore` keeps `web/` and data out of this project.
2. **Environment variables** (Settings → Environment Variables):

   | Name | Value |
   |---|---|
   | `STORAGE_MODE` | `cloud` |
   | `OPENAI_API_KEY` | your team key |
   | `IMAGE_MODEL` / `TEXT_MODEL` / `REVIEW_MODEL` | the model names your key allows |
   | `DATABASE_URL` | Neon pooled connection string |
   | `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET` | from step 1 |
   | `CORS_ORIGINS` | the frontend URL, e.g. `https://contino.vercel.app` (fill in after step 4) |
   | `CORS_ORIGIN_REGEX` | optional, to allow preview URLs: `https://contino-.*\.vercel\.app` |
   | `COOKIE_CROSS_SITE` | `true` (frontend and API are on different domains) |
   | `EMBEDDED_WORKER` | `false` |
   | `DB_POOL_MIN` / `DB_POOL_MAX` | `0` / `5` (serverless-friendly) |
   | `API_TOKEN` | optional: a password for the app |

3. **Deploy**, then open `https://<api>.vercel.app/api/healthz`. It should return `{"ok": true}`.
   - `/api/readyz` also checks the database and the R2 bucket.

## Step 3 · Run the worker (always-on)

Use the existing `api/Dockerfile`. Its build context is the repo root, because it needs `prompts/` and `config.yaml`.

**Railway or Render:**
1. New service from the repo → **Dockerfile path:** `api/Dockerfile` (build context: repo root).
2. **Start command:** `python -m app.worker`
3. **Environment variables:** the **same** values as the API: `STORAGE_MODE`, `OPENAI_API_KEY`, `*_MODEL`, `DATABASE_URL` and the `R2_*` values.
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
   - If it stays on "Reading your Shot Planner…", the worker isn't running or has different env values.
3. **Create a character picture.** It appears after the worker draws it. Images are served from R2 through short-lived links.

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
| `DB_POOL_MIN` / `DB_POOL_MAX` | API, worker | Database connections per process |
| `OUTPUT_DIR` | API | Where exports are written. It defaults to `/tmp/output` on Vercel. |

## Alternative: everything except the frontend on one host

If you'd rather avoid serverless limits, the brief's own setup works too:
- **Frontend:** Vercel (as in step 4).
- **API + worker:** one Railway, Render or Fly service.
  - Use `api/Dockerfile` with the default command, which starts the API.
  - Set `EMBEDDED_WORKER=true` so it also runs the worker.
  - With a persistent volume, it can even stay in `STORAGE_MODE=local`.
- Point `VITE_API_URL` at that service.
