import csv
import json
import re
import sqlite3
from pathlib import Path
from datetime import datetime
from difflib import SequenceMatcher


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_DIR = BASE_DIR / "db"
DB_PATH = DB_DIR / "consultbae.db"
SCHEMA_PATH = DB_DIR / "schema.sql"


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_email(email):
    if not email:
        return None

    email = str(email).strip().lower()

    return email if email else None


def normalize_phone(phone):
    if not phone:
        return None

    digits = re.sub(r"\D", "", str(phone))

    if digits.startswith("91") and len(digits) > 10:
        digits = digits[2:]

    if digits.startswith("0") and len(digits) > 10:
        digits = digits[1:]

    if len(digits) >= 10:
        return digits[-10:]

    return None


def normalize_name(name):
    if not name:
        return None

    name = str(name).strip().lower()
    name = re.sub(r"\s+", " ", name)

    return name if name else None


CITY_ALIASES = {
    "gurgaon": "gurugram",
    "gurugram": "gurugram",
    "bangalore": "bengaluru",
    "bengaluru": "bengaluru",
    "new delhi": "delhi",
    "delhi ncr": "delhi",
    "delhi": "delhi",
}


def normalize_city(city):
    if not city:
        return None

    city = str(city).strip().lower()
    city = re.sub(r"\s+", " ", city)

    return CITY_ALIASES.get(city, city)


def normalize_skill(skill):
    if not skill:
        return None

    skill = skill.strip().lower()
    skill = re.sub(r"\s+", " ", skill)

    return skill


# ============================================================
# CSV LOADING
# ============================================================

def load_csv(filename):
    file_path = DATA_DIR / filename

    with open(file_path, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    print(f"{filename}: {len(rows)} rows")

    return rows


# ============================================================
# DATA QUALITY HELPERS
# ============================================================

def is_empty_row(row):
    return all(
        not value or not str(value).strip()
        for value in row.values()
    )


def is_repeated_header(row, expected_headers):
    row_values = {
        str(value).strip().lower()
        for value in row.values()
        if value is not None
    }

    expected_values = {
        str(value).strip().lower()
        for value in expected_headers
    }

    return row_values == expected_values


def repair_gig_worker_row(row):
    """
    Repairs the known shifted Isha Chopra row.
    """

    if (
        row.get("worker_name") == "ISHA.CHOPRA95@MAILTEST.EXAMPLE.ORG"
        and row.get("rate") == "Isha Chopra"
        and row.get("location") == "1406/hr"
        and row.get("status") == "Pune"
    ):
        repaired_row = {
            "email_id": row["worker_name"],
            "worker_name": row["rate"],
            "rate": row["location"],
            "location": row["status"],
            "status": row["skill_tags"],
            "skill_tags": row["email_id"],
        }

        return repaired_row, "repaired_shifted_columns"

    return row, None


# ============================================================
# SOURCE-SPECIFIC CLEANERS
# ============================================================

def clean_naukri_row(row):
    return {
        "name": normalize_name(row.get("Full Name")),
        "email": normalize_email(row.get("Email")),
        "phone": normalize_phone(row.get("Phone")),
        "city": normalize_city(row.get("City")),
        "skills": row.get("Skills"),
        "raw": row,
    }


def clean_gig_worker_row(row):
    return {
        "name": normalize_name(row.get("worker_name")),
        "email": normalize_email(row.get("email_id")),
        "phone": None,
        "city": normalize_city(row.get("location")),
        "skills": row.get("skill_tags"),
        "raw": row,
    }


def clean_cbnexus_row(row):
    return {
        "name": normalize_name(row.get("Name")),
        "email": None,
        "phone": normalize_phone(row.get("Phone Number")),
        "city": normalize_city(row.get("City")),
        "skills": None,
        "raw": row,
    }


# ============================================================
# SOURCE PIPELINES
# ============================================================

def clean_naukri(rows):
    cleaned_rows = []

    for row in rows:
        if is_empty_row(row):
            continue

        cleaned_rows.append(clean_naukri_row(row))

    return cleaned_rows


def clean_gig_workers(rows):
    cleaned_rows = []
    issues = []

    for row_number, row in enumerate(rows, start=2):

        if is_empty_row(row):
            issues.append({
                "source": "gig_workers",
                "row": row_number,
                "issue": "blank_row",
                "action": "skipped",
            })
            continue

        row, issue = repair_gig_worker_row(row)

        if issue:
            issues.append({
                "source": "gig_workers",
                "row": row_number,
                "issue": issue,
                "action": "repaired",
            })

        cleaned_rows.append(clean_gig_worker_row(row))

    return cleaned_rows, issues


def clean_cbnexus(rows):
    cleaned_rows = []
    issues = []

    expected_headers = [
        "Name",
        "Phone Number",
        "City",
        "Verified",
        "Projects Completed",
    ]

    for row_number, row in enumerate(rows, start=2):

        if is_empty_row(row):
            issues.append({
                "source": "cbnexus",
                "row": row_number,
                "issue": "blank_row",
                "action": "skipped",
            })
            continue

        if is_repeated_header(row, expected_headers):
            issues.append({
                "source": "cbnexus",
                "row": row_number,
                "issue": "repeated_header",
                "action": "skipped",
            })
            continue

        cleaned_rows.append(clean_cbnexus_row(row))

    return cleaned_rows, issues


# ============================================================
# SKILLS
# ============================================================

def extract_skills(record):
    skills = record.get("skills")

    if not skills:
        return []

    return [
        normalize_skill(skill)
        for skill in str(skills).split(",")
        if normalize_skill(skill)
    ]


# ============================================================
# MATCHING HELPERS
# ============================================================

def similarity(a, b):
    if not a or not b:
        return 0.0

    return SequenceMatcher(None, a, b).ratio()


def strong_match(a, b):
    """
    Compare a source record against a canonical person.
    Returns:
        True/False, match method
    """

    # Exact email
    if a["email"] and b["canonical_email"]:
        if a["email"] == b["canonical_email"]:
            return True, "exact_email"

    # Exact phone
    if a["phone"] and b["canonical_phone"]:
        if a["phone"] == b["canonical_phone"]:
            return True, "exact_phone"

    # Exact name + city
    if (
        a["name"]
        and b["canonical_name"]
        and a["city"]
        and b["city"]
        and a["name"] == b["canonical_name"]
        and a["city"] == b["city"]
    ):
        return True, "name_city"

    return False, None


def probable_match(a, b):
    """
    Conservative fuzzy matching.
    Used only when strong identifiers are unavailable.
    """

    if not a["name"] or not b["canonical_name"]:
        return False, None

    name_score = similarity(
        a["name"],
        b["canonical_name"]
    )

    city_match = (
        a["city"]
        and b["city"]
        and a["city"] == b["city"]
    )

    if name_score >= 0.90 and city_match:
        return True, "fuzzy_name_city"

    return False, None


# ============================================================
# ENTITY RESOLUTION
# ============================================================

def resolve_people(records):
    """
    Groups source records into canonical people.

    Returns:
        people: list of canonical people
        assignments: list mapping source records to person_id
        match_log: list describing how records were matched
    """

    people = []
    assignments = []
    match_log = []

    for source_record_id, record in enumerate(records, start=1):

        matched_person = None
        match_method = None

        # ----------------------------------------------------
        # First: strong matching
        # ----------------------------------------------------

        for person in people:

            is_match, method = strong_match(record, person)

            if is_match:
                matched_person = person
                match_method = method
                break

        # ----------------------------------------------------
        # Second: conservative fuzzy matching
        # ----------------------------------------------------

        if matched_person is None:

            for person in people:

                is_match, method = probable_match(record, person)

                if is_match:
                    matched_person = person
                    match_method = method
                    break

        # ----------------------------------------------------
        # Existing person
        # ----------------------------------------------------

        if matched_person is not None:

            person_id = matched_person["person_id"]

            # Fill missing canonical information.
            if not matched_person["canonical_email"]:
                matched_person["canonical_email"] = record["email"]

            if not matched_person["canonical_phone"]:
                matched_person["canonical_phone"] = record["phone"]

            if not matched_person["city"]:
                matched_person["city"] = record["city"]

            assignments.append({
                "source_record_id": source_record_id,
                "person_id": person_id,
                "record": record,
            })

            match_log.append({
                "source_record_id": source_record_id,
                "person_id": person_id,
                "method": match_method,
                "confidence": "high"
                if match_method in ("exact_email", "exact_phone")
                else "medium",
            })

        # ----------------------------------------------------
        # New person
        # ----------------------------------------------------

        else:

            person_id = len(people) + 1

            new_person = {
                "person_id": person_id,
                "canonical_name": record["name"],
                "canonical_email": record["email"],
                "canonical_phone": record["phone"],
                "city": record["city"],
            }

            people.append(new_person)

            assignments.append({
                "source_record_id": source_record_id,
                "person_id": person_id,
                "record": record,
            })

            match_log.append({
                "source_record_id": source_record_id,
                "person_id": person_id,
                "method": "new_person",
                "confidence": "new",
            })

    return people, assignments, match_log


# ============================================================
# SQLITE
# ============================================================

def initialize_database():
    DB_DIR.mkdir(exist_ok=True)

    # Delete old generated DB so the pipeline is reproducible.
    if DB_PATH.exists():
        DB_PATH.unlink()

    connection = sqlite3.connect(DB_PATH)

    with open(SCHEMA_PATH, "r", encoding="utf-8") as file:
        schema = file.read()

    connection.executescript(schema)

    return connection


def insert_people(connection, people):
    for person in people:
        connection.execute(
            """
            INSERT INTO person (
                person_id,
                canonical_name,
                canonical_email,
                canonical_phone,
                city,
                match_confidence,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                person["person_id"],
                person["canonical_name"],
                person["canonical_email"],
                person["canonical_phone"],
                person["city"],
                "high",
                datetime.now().isoformat(),
            ),
        )


def insert_source_records(connection, assignments):
    for assignment in assignments:

        record = assignment["record"]

        connection.execute(
            """
            INSERT INTO source_record (
                record_id,
                person_id,
                source_name,
                raw_json,
                ingested_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                assignment["source_record_id"],
                assignment["person_id"],
                record["source"],
                json.dumps(record["raw"]),
                datetime.now().isoformat(),
            ),
        )


def insert_skills(connection, assignments):
    seen = set()

    for assignment in assignments:

        person_id = assignment["person_id"]
        record = assignment["record"]
        source = record["source"]

        for skill in extract_skills(record):

            key = (person_id, skill, source)

            if key in seen:
                continue

            seen.add(key)

            connection.execute(
                """
                INSERT INTO skill (
                    person_id,
                    skill,
                    source_name
                )
                VALUES (?, ?, ?)
                """,
                (
                    person_id,
                    skill,
                    source,
                ),
            )


# ============================================================
# PREPARE RECORDS
# ============================================================

def add_source(records, source_name):
    enriched = []

    for record in records:
        record = record.copy()
        record["source"] = source_name
        enriched.append(record)

    return enriched


# ============================================================
# VALIDATION
# ============================================================

def validate_database(connection):
    cursor = connection.cursor()

    cursor.execute("SELECT COUNT(*) FROM person")
    people_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM source_record")
    source_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM skill")
    skill_count = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM source_record
        WHERE person_id IS NULL
        """
    )
    orphan_records = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM person
        WHERE canonical_email IS NOT NULL
        GROUP BY canonical_email
        HAVING COUNT(*) > 1
        """
    )
    duplicate_emails = len(cursor.fetchall())

    print("\n--- Database Validation ---")
    print(f"Canonical people: {people_count}")
    print(f"Source records: {source_count}")
    print(f"Skills: {skill_count}")
    print(f"Orphan source records: {orphan_records}")
    print(f"Duplicate canonical emails: {duplicate_emails}")


# ============================================================
# MAIN PIPELINE
# ============================================================

def main():

    print("=== CONSULTBAE INGESTION PIPELINE ===\n")

    # --------------------------------------------------------
    # 1. Load
    # --------------------------------------------------------

    naukri = load_csv("source1_naukri_applicants.csv")
    gig_workers = load_csv("source2_gig_workers.csv")
    cbnexus = load_csv("source3_cbnexus_contacts.csv")

    # --------------------------------------------------------
    # 2. Clean
    # --------------------------------------------------------

    naukri_cleaned = clean_naukri(naukri)

    gig_cleaned, gig_issues = clean_gig_workers(gig_workers)

    cbnexus_cleaned, cbnexus_issues = clean_cbnexus(cbnexus)

    print("\n--- Cleaning Summary ---")
    print(f"Naukri: {len(naukri_cleaned)} records")
    print(f"Gig Workers: {len(gig_cleaned)} records")
    print(f"CBNexus: {len(cbnexus_cleaned)} records")

    print("\n--- Issues ---")

    for issue in gig_issues:
        print(issue)

    for issue in cbnexus_issues:
        print(issue)

    # --------------------------------------------------------
    # 3. Add source information
    # --------------------------------------------------------

    naukri_cleaned = add_source(
        naukri_cleaned,
        "naukri"
    )

    gig_cleaned = add_source(
        gig_cleaned,
        "gig_workers"
    )

    cbnexus_cleaned = add_source(
        cbnexus_cleaned,
        "cbnexus"
    )

    all_records = (
        naukri_cleaned
        + gig_cleaned
        + cbnexus_cleaned
    )

    print(f"\nTotal usable source records: {len(all_records)}")

    # --------------------------------------------------------
    # 4. Entity resolution
    # --------------------------------------------------------

    people, assignments, match_log = resolve_people(
        all_records
    )

    print(f"Canonical people created: {len(people)}")

    # --------------------------------------------------------
    # 5. Database
    # --------------------------------------------------------

    connection = initialize_database()

    try:

        insert_people(
            connection,
            people
        )

        insert_source_records(
            connection,
            assignments
        )

        insert_skills(
            connection,
            assignments
        )

        connection.commit()

        validate_database(connection)

    finally:
        connection.close()

    # --------------------------------------------------------
    # 6. Match report
    # --------------------------------------------------------

    print("\n--- Match Summary ---")

    method_counts = {}

    for item in match_log:
        method = item["method"]
        method_counts[method] = method_counts.get(method, 0) + 1

    for method, count in method_counts.items():
        print(f"{method}: {count}")

    print(f"\nDatabase created: {DB_PATH}")


if __name__ == "__main__":
    main()