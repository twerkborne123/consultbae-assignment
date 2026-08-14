PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS person (
    person_id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT,
    canonical_email TEXT,
    canonical_phone TEXT,
    city TEXT,
    match_confidence TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS source_record (
    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER,
    source_name TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    ingested_at TEXT,
    
    FOREIGN KEY (person_id)
        REFERENCES person(person_id)
);

CREATE TABLE IF NOT EXISTS skill (
    person_id INTEGER,
    skill TEXT,
    source_name TEXT,

    FOREIGN KEY (person_id)
        REFERENCES person(person_id)
);

CREATE TABLE IF NOT EXISTS audio_submission (
    submission_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER,
    name TEXT,
    phone TEXT,
    file_path TEXT,
    duration_sec REAL,
    sample_rate_hz INTEGER,
    bitrate_kbps REAL,
    loudness_db REAL,
    quality_note TEXT,
    submitted_at TEXT,

    FOREIGN KEY (person_id)
        REFERENCES person(person_id)
);