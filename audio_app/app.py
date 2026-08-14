import os

# ============================================================
# FFmpeg configuration
# ============================================================

FFMPEG_DIR = (
    r"C:\Users\admin\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg.Shared_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-9.0.1-full_build-shared\bin"
)

FFMPEG_PATH = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
FFPROBE_PATH = os.path.join(FFMPEG_DIR, "ffprobe.exe")

# Verify FFmpeg actually exists
if not os.path.exists(FFMPEG_PATH):
    raise FileNotFoundError(f"FFmpeg not found: {FFMPEG_PATH}")

if not os.path.exists(FFPROBE_PATH):
    raise FileNotFoundError(f"FFprobe not found: {FFPROBE_PATH}")

# Add FFmpeg directory BEFORE importing pydub
os.environ["PATH"] = (
    FFMPEG_DIR
    + os.pathsep
    + os.environ.get("PATH", "")
)

# ============================================================
# Imports
# ============================================================

import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_from_directory,
)

from pydub import AudioSegment

# Explicitly configure pydub
AudioSegment.converter = FFMPEG_PATH
AudioSegment.ffprobe = FFPROBE_PATH


# ============================================================
# Project paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DB_PATH = BASE_DIR / "db" / "consultbae.db"

UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"

UPLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# Flask application
# ============================================================

app = Flask(__name__)

app.secret_key = "consultbae-assignment"


# ============================================================
# Database
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# Audio metadata
# ============================================================

def get_audio_metadata(file_path):
    """
    Extract:

    - duration
    - sample rate
    - bitrate
    - loudness
    - quality note
    """

    # Load audio using pydub / FFmpeg
    audio = AudioSegment.from_file(file_path)

    # --------------------------------------------------------
    # Duration
    # --------------------------------------------------------

    duration_sec = len(audio) / 1000

    # --------------------------------------------------------
    # Sample rate
    # --------------------------------------------------------

    sample_rate_hz = audio.frame_rate

    # --------------------------------------------------------
    # Bitrate
    # --------------------------------------------------------

    bitrate_kbps = None

    try:

        result = subprocess.run(
            [
                os.path.join(FFMPEG_DIR, "ffprobe.exe"),
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=bit_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        value = result.stdout.strip()

        if value and value.isdigit():
            bitrate_kbps = round(
                int(value) / 1000,
                2
            )

    except FileNotFoundError:

        bitrate_kbps = None

    # --------------------------------------------------------
    # Loudness
    # --------------------------------------------------------

    loudness_db = None

    if audio.rms:
        loudness_db = round(
            audio.dBFS,
            2
        )

    # --------------------------------------------------------
    # Quality assessment
    # --------------------------------------------------------

    quality_note = get_quality_note(
        duration_sec,
        sample_rate_hz,
        loudness_db,
    )

    return {
        "duration_sec": round(
            duration_sec,
            2
        ),

        "sample_rate_hz": sample_rate_hz,

        "bitrate_kbps": bitrate_kbps,

        "loudness_db": loudness_db,

        "quality_note": quality_note,
    }


# ============================================================
# Audio quality rules
# ============================================================

def get_quality_note(
    duration,
    sample_rate,
    loudness
):

    notes = []

    # Very short recording
    if duration < 1:
        notes.append(
            "Very short recording"
        )

    # Long recording
    if duration > 120:
        notes.append(
            "Long recording"
        )

    # Low sample rate
    if sample_rate < 16000:
        notes.append(
            "Low sample rate"
        )

    # Loudness
    if loudness is not None:

        if loudness < -35:
            notes.append(
                "Very quiet"
            )

        elif loudness > -3:
            notes.append(
                "Very loud"
            )

    if not notes:
        return "OK"

    return "; ".join(notes)


# ============================================================
# Find canonical person
# ============================================================

def find_person(name, phone):
    """
    Match the submitted person against Task 1's
    canonical person table.

    Phone is preferred because it is a stronger
    identifier than name.
    """

    conn = get_db()

    # --------------------------------------------------------
    # Normalize phone
    # --------------------------------------------------------

    normalized_phone = "".join(
        character
        for character in phone
        if character.isdigit()
    )

    if len(normalized_phone) >= 10:
        normalized_phone = normalized_phone[-10:]
    else:
        normalized_phone = ""

    # --------------------------------------------------------
    # Normalize name
    # --------------------------------------------------------

    normalized_name = " ".join(
        name.strip().lower().split()
    )

    person = None

    # --------------------------------------------------------
    # First try phone
    # --------------------------------------------------------

    if normalized_phone:

        person = conn.execute(
            """
            SELECT *
            FROM person
            WHERE canonical_phone = ?
            LIMIT 1
            """,
            (normalized_phone,),
        ).fetchone()

    # --------------------------------------------------------
    # If phone didn't match, try name
    # --------------------------------------------------------

    if person is None and normalized_name:

        person = conn.execute(
            """
            SELECT *
            FROM person
            WHERE canonical_name = ?
            LIMIT 1
            """,
            (normalized_name,),
        ).fetchone()

    conn.close()

    return person


# ============================================================
# Home page
# ============================================================

@app.route("/")
def index():

    return render_template(
        "index.html"
    )


# ============================================================
# Submit audio
# ============================================================

@app.route(
    "/submit",
    methods=["POST"]
)
def submit_audio():

    # --------------------------------------------------------
    # Read form data
    # --------------------------------------------------------

    name = request.form.get(
        "name",
        ""
    ).strip()

    phone = request.form.get(
        "phone",
        ""
    ).strip()

    audio_file = request.files.get(
        "audio"
    )

    # --------------------------------------------------------
    # Validate name and phone
    # --------------------------------------------------------

    if not name or not phone:

        flash(
            "Name and phone are required."
        )

        return redirect(
            url_for("index")
        )

    # --------------------------------------------------------
    # Validate audio
    # --------------------------------------------------------

    if (
        not audio_file
        or not audio_file.filename
    ):

        flash(
            "Please select an audio file."
        )

        return redirect(
            url_for("index")
        )

    # --------------------------------------------------------
    # Find person in canonical database
    # --------------------------------------------------------

    person = find_person(
        name,
        phone
    )

    if person is None:

        flash(
            "No matching person found in "
            "the Task 1 database. "
            "Please check the name and phone number."
        )

        return redirect(
            url_for("index")
        )

    # --------------------------------------------------------
    # Validate audio extension
    # --------------------------------------------------------

    original_name = Path(
        audio_file.filename
    ).name

    extension = Path(
        original_name
    ).suffix.lower()

    allowed_extensions = {
        ".wav",
        ".mp3",
        ".ogg",
        ".webm",
        ".m4a",
        ".flac",
    }

    if extension not in allowed_extensions:

        flash(
            "Unsupported audio format."
        )

        return redirect(
            url_for("index")
        )

    # --------------------------------------------------------
    # Generate unique filename
    # --------------------------------------------------------

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    output_name = (
        f"{person['person_id']}_"
        f"{timestamp}"
        f"{extension}"
    )

    output_path = (
        UPLOAD_DIR
        / output_name
    )

    # --------------------------------------------------------
    # Save uploaded audio
    # --------------------------------------------------------

    audio_file.save(
        str(output_path)
    )

    # --------------------------------------------------------
    # Extract metadata
    # --------------------------------------------------------

    try:

        metadata = get_audio_metadata(
            output_path
        )

    except Exception as error:

        # Try to remove failed file.
        # On Windows, FFmpeg may briefly hold
        # the file, so don't let cleanup itself
        # crash the application.

        try:

            if output_path.exists():
                output_path.unlink()

        except PermissionError:

            pass

        flash(
            f"Could not process the audio file: {error}"
        )

        return redirect(
            url_for("index")
        )

    # --------------------------------------------------------
    # Save submission to database
    # --------------------------------------------------------

    conn = get_db()

    try:

        conn.execute(
            """
            INSERT INTO audio_submission (
                person_id,
                name,
                phone,
                file_path,
                duration_sec,
                sample_rate_hz,
                bitrate_kbps,
                loudness_db,
                quality_note,
                submitted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                person["person_id"],

                name,

                phone,

                # Store ONLY the filename.
                # This prevents Windows path problems.
                output_name,

                metadata["duration_sec"],

                metadata["sample_rate_hz"],

                metadata["bitrate_kbps"],

                metadata["loudness_db"],

                metadata["quality_note"],

                datetime.now().isoformat(
                    timespec="seconds"
                ),
            ),
        )

        conn.commit()

    except Exception:

        conn.rollback()

        raise

    finally:

        conn.close()

    # --------------------------------------------------------
    # Success
    # --------------------------------------------------------

    flash(
        "Audio submission saved successfully."
    )

    return redirect(
        url_for("submissions")
    )


# ============================================================
# Submissions page
# ============================================================

@app.route("/submissions")
def submissions():

    conn = get_db()

    rows = conn.execute(
        """
        SELECT
            a.*,
            p.canonical_name
        FROM audio_submission a
        LEFT JOIN person p
            ON a.person_id = p.person_id
        ORDER BY a.submitted_at DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "submissions.html",
        submissions=rows,
    )


# ============================================================
# Serve uploaded audio files
# ============================================================

@app.route(
    "/uploads/<path:filename>"
)
def uploaded_file(filename):

    return send_from_directory(
        UPLOAD_DIR,
        filename
    )


# ============================================================
# Run application
# ============================================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
    )