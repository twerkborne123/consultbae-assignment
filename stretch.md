# Task 5 — Stretch: Launching to 5,000 gig workers over a weekend

No code changes made here — this is a review of `audio_app/app.py` as it stands today, called
out against what a real weekend spike would do to it.

## What breaks first

**1. The single-threaded Flask dev server, immediately, within the first few concurrent users.**
`app.run(host="127.0.0.1", port=5000, debug=True)` uses Werkzeug's development server, which is
not meant for concurrent traffic. Every audio submission runs `AudioSegment.from_file()` plus a
separate `ffprobe` subprocess call *synchronously, inside the request*, before the response is
sent. With one worker handling one request at a time, the second person to submit while the first
person's audio is still being processed just sits there waiting — at 5,000 people hitting this
over a weekend, the queue backs up almost instantly and most people see a hung page or a timeout,
not an error message.

**2. SQLite write contention, shortly after.** Every submission does an `INSERT INTO
audio_submission`. SQLite allows only one writer at a time; under concurrent load, competing
writes start throwing `database is locked` instead of queuing cleanly, so some submissions would
fail outright rather than just being slow.

**3. Local disk storage, less immediately but just as fatal.** Audio files save to
`audio_app/uploads/` on whatever single machine is running the app. If this is deployed on
Render/Railway free tier (as the assignment brief suggests), that disk is typically ephemeral —
a restart or redeploy during the weekend silently wipes every submission collected so far, audio
files and all.

**4. The hardcoded FFmpeg path, before any of the above even matters.** `FFMPEG_DIR` points at a
specific WinGet install path on one Windows machine. Deployed anywhere else (Render, Railway,
a Linux container), the app fails at import time — `FileNotFoundError` — before it serves a single
request. This has to be fixed just to deploy, not just to scale.

## What I'd change before launch, in priority order

1. **Swap the dev server for a real WSGI server** (gunicorn/waitress) with multiple workers, so
   requests aren't serialized behind one Python process.
2. **Move audio processing off the request path.** Have `/submit` save the file, insert a row with
   `quality_note = "processing"`, and hand the actual `pydub`/`ffprobe` work to a background
   queue (RQ/Celery, or even a simple polling worker). The person gets an immediate "received"
   response instead of waiting on ffmpeg.
3. **Move storage to cloud object storage** (S3, Cloudflare R2, Backblaze) instead of local disk —
   decouples file storage from wherever the app process happens to be running, and survives
   restarts/redeploys.
4. **Move off SQLite to Postgres**, or at minimum enable WAL mode as a stopgap — SQLite's
   single-writer model is the wrong fit for concurrent submissions at this volume.
5. **Fix FFmpeg to be environment-portable** — install it via a Dockerfile/buildpack instead of a
   hardcoded Windows path, so the app can actually run on the hosting platform at all.
6. **Add file size and duration caps** on upload — nothing currently stops a very large or very
   long file from being accepted, which is both a storage-cost risk and a processing-time risk
   (a 2-hour file makes one worker block for a long time).
7. **Add basic rate limiting per phone number / IP** — nothing currently stops one person (or a
   script) from hammering `/submit` repeatedly.
8. **Decide what "duplicate" means and enforce it.** `find_person` matches by phone then name, but
   nothing stops the same matched person from submitting five times in a row. For a one-time
   weekend collection, I'd add a check like "flag or block a second submission from the same
   `person_id` within N hours" rather than silently accepting unlimited resubmissions.
9. **Add minimal monitoring** — even just error logging to a service (Sentry or similar) — so
   failures during an unattended weekend launch surface in real time instead of being discovered
   Monday morning in the submissions table.

## Storage & cost, roughly

5,000 short voice recordings (say 30–90 seconds each, compressed) is on the order of a few GB —
storage itself isn't the expensive part. The real cost risk is compute: if processing stays
synchronous and in-request, handling the spike means paying for a much bigger single server just
to survive Saturday morning, when the same volume spread across a background queue could run on
a small server plus a couple of cheap workers. Async processing isn't just about correctness here,
it's the cheaper way to handle a weekend spike too.
