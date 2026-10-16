# AGENTS.md

Repository notes for agents working on Cheat Clip PRO (Vite + React frontend,
FastAPI backend, ffmpeg for rendering).

## Layout

- `src/` — React app. `src/components/VideoGeneratorModal.tsx` owns the
  Video → Prompt / Prompt → Video UI; `src/locales/en.ts` and `src/locales/id.ts`
  hold every user-facing string.
- `backend/routers/` — HTTP endpoints. `backend/services/` — logic behind them,
  including the `TTLCache` registries used for batch job state.
- `backend/schemas/` — pydantic models, re-exported from `backend/schemas/__init__.py`.
- `tests/` — pytest. `tests/test_video_gen.py` covers prompt extraction and Veo
  generation.

## Commands

```bash
# Backend + frontend together
npm run dev

# Frontend only, with hot reload (proxies /api to 127.0.0.1:8000)
npx vite

# Backend only
python -m uvicorn backend.main:app --port 8000

# Checks
python -m pytest tests/ -q     # full suite
npx tsc -b                     # typecheck
npm run build                  # production build
npm run lint                   # eslint (has pre-existing failures, see below)
```

## Conventions

- Every locale key must exist in **both** `en.ts` and `id.ts`. `tsc` enforces
  nested key types, so a missing translation is a build error.
- Keep user-facing copy out of components; add keys to both locale files.
- Batch endpoints return an id immediately and run in the background. State
  lives in a `TTLCache` registry in `backend/services/`, and a separate status
  endpoint polls it. Follow that shape rather than blocking the request.
- Each item in a batch must fail independently; only report the whole batch as
  failed when every item failed.
- Tests must exercise real request building and parsing. `tests/test_video_gen.py`
  runs against a local HTTP server plus a fake `genai.Client`; mocking the
  function under test proves nothing about what it produces.

## Gotchas

- `npm run lint` currently reports ~87 errors in files unrelated to most
  changes (`ThemeContext.tsx`, `utils/api.ts`, `vite.config.ts`, ...). Check
  only the files you touched (`npx eslint <files>`) rather than the whole repo.
- Installing the backend for tests needs no torch or Whisper unless you are
  testing transcription; skipping them keeps the venv small.
- Serving the Vite dev server behind a proxy host requires
  `server.allowedHosts` in `vite.config.ts`, otherwise Vite answers
  "Blocked request. This host is not allowed."
- `/api/generate-prompt` rejects the `mock` api key; it exists only for tests.
- Prompt extraction works with a Gemini **or** Kimi key, but Veo video
  rendering needs a Gemini key. Do not silently swap keys to work around this.
