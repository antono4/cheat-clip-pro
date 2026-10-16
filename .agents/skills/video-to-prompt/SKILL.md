---
name: video-to-prompt
description: This skill should be used when working with Cheat Clip PRO's video-to-prompt capability — extracting a reusable text-to-video prompt from a YouTube, Google Drive, or uploaded video, or turning such a prompt back into a clip with Google Veo. Use when the user asks to "extract a prompt from a video", "turn a video into a prompt", "generate a video from a prompt", "recreate a video with AI", or asks about the /api/generate-prompt, /api/generate-video, or /api/generate-prompts-batch endpoints.
---

# Video to Prompt (Cheat Clip PRO)

Cheat Clip PRO can analyse a source video and write a portable text-to-video
prompt that recreates it, then feed that prompt to Google Veo to render a new
clip. The two directions are independent: prompt extraction works with either
provider, video rendering only works with Google.

## When to use what

| The user wants to | Use |
| --- | --- |
| Get a text prompt out of an existing video | `POST /api/generate-prompt` |
| Get prompts out of several videos at once | `POST /api/generate-prompts-batch` |
| Render a new clip from a prompt | `POST /api/generate-video` |
| See which video models are available | `GET /api/video-models` |

## Provider constraint (the most common source of confusion)

Prompt extraction runs on **either** Google Gemini **or** Moonshot/Kimi.
Video rendering runs on **Google Veo only** — there is no OpenAI-compatible
equivalent for video, so a `sk-...` key can write prompts but cannot render.
The API returns HTTP 400 with an explicit message if you try. Do not work
around this by silently swapping the key; tell the user they need a Gemini key
(`AIza...`) to render.

The provider is inferred from the key shape and the model name:

- key starts with `sk-`, or model matches `kimi-*` → Moonshot/Kimi
- key starts with `AIza`, or any Gemini model name → Google
- model `mock` is rejected by `/api/generate-prompt`; it is only for tests

## Prompt extraction

`POST /api/generate-prompt` accepts:

| Field | Required | Notes |
| --- | --- | --- |
| `url` | yes | YouTube URL, Google Drive link, or an uploaded-video reference |
| `api_key` | yes | Gemini or Kimi key; falls back to `GEMINI_API_KEY` for Gemini |
| `model` | no | Preferred model; the provider is inferred from it |
| `style_hint` | no | Visual style to steer the prompt, e.g. `anime` |
| `extra_instructions` | no | Free-form direction, e.g. "keep it under 10 seconds" |
| `proxy`, `cookies` | no | YouTube access workarounds |
| `subtitles`, `subtitles_filename` | no | Manual SRT/TXT instead of auto transcription |

`url` accepts three source shapes, detected in this order:

1. a Google Drive link → the file is downloaded first
2. `upload_<name>`, `/api/video/<name>`, or `file://<path>` → a local upload,
   which is transcribed with Whisper
3. anything else → treated as a YouTube URL and must parse as one

The response is a structured object, not a single string. Read `prompt` for the
ready-to-paste text, and the individual fields (`style`, `subject`, `action`,
`setting`, `camera`, `lighting`, `audio`, `negative_prompt`, `shot_list`) when
you need to show or edit the parts. `transcript` echoes the flattened source
transcript and is capped at 60 000 characters.

Two failure modes are worth surfacing verbatim to the user rather than
retrying blindly:

- HTTP 400 *"No spoken words were detected"* — the source has no usable speech.
  Suggest uploading subtitles or choosing a different video.
- HTTP 400 *"Could not retrieve a transcript"* — YouTube blocked the fetch.
  Suggest cookies or a manual subtitle upload.

Both mean the transcript is missing, which is not something a retry fixes.

## Batch extraction

`POST /api/generate-prompts-batch` takes the same fields plus `urls` (a list)
instead of `url`. It returns a `batch_id` immediately and processes the videos
in the background; poll
`GET /api/generate-prompts-batch-status/{batch_id}` for per-item progress. Each
item carries its own `status`, `prompt`, and `error`, so one bad URL does not
fail the batch. The overall `status` becomes `completed` once no item is still
pending, and the batch is reported as `error` only when every item failed.

## Video generation

`POST /api/generate-video` starts a Veo job and returns a `job_id` right away;
poll `GET /api/generate-video-status/{job_id}` until `status` is `completed` or
`failed`. Veo jobs routinely take a few minutes and the server gives up after
900 seconds, so poll patiently rather than resubmitting.

Constraints the server enforces, so validate before calling:

- `aspect_ratio`: `16:9` or `9:16`
- `resolution`: `720p`, `1080p`, or `4k`
- `duration_seconds`: 4, 6, or 8
- `number_of_videos`: 1 to 4

Finished files are served from `/api/download-generated/<filename>`; use the
`download_url` in the status response rather than constructing a path.

## Driving it from the command line

`scripts/video_to_prompt.py` calls a running backend and prints the result. It
is useful for a quick end-to-end check without the UI:

```bash
python3 .agents/skills/video-to-prompt/scripts/video_to_prompt.py \
  --url "https://www.youtube.com/watch?v=..." \
  --api-key "$GEMINI_API_KEY" \
  --style "cinematic realism"
```

Pass `--json` to get the raw response for further processing. The script talks
to `http://localhost:8000` by default; override with `--base-url`.

## Testing without credentials

`tests/test_video_gen.py` runs the real request-building and parsing code
against a local OpenAI-compatible HTTP server, and the real Veo polling loop
against a fake `genai.Client`. Follow that pattern rather than mocking
`generate_video_prompt` itself — mocking the function under test proves nothing
about the prompt it builds. Run the suite with:

```bash
python3 -m pytest tests/test_video_gen.py -q
```
