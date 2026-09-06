#!/usr/bin/env python3
"""
JIRA Ticket Ingestion Pipeline (AWS)
=====================================
Batch process CSV files from the dataset/ folder into AWS RDS PostgreSQL + pgvector.
Uses Amazon Bedrock for embedding generation.

Usage:
    python ingest_pipeline.py                    # Ingest all CSVs from dataset/
    python ingest_pipeline.py --file data.csv    # Ingest a specific file
    python ingest_pipeline.py --clear            # Clear all tickets and re-ingest
    python ingest_pipeline.py --dry-run          # Validate CSVs without ingesting
    python ingest_pipeline.py --stats            # Show current DB stats
"""

import pandas as pd
import asyncio
import argparse
import os
import sys
import glob
import time
import json
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from database import get_db, init_db
from bedrock_client import get_embedding

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATASET_DIR = os.path.join(os.path.dirname(__file__), "dataset")
EMBEDDING_BATCH_SIZE = 10  # Concurrent embedding requests

# Required columns for a valid CSV
REQUIRED_COLUMNS = {"ticket_id", "summary"}
RECOMMENDED_COLUMNS = {
    "issue_description", "issue_type", "priority", "component",
    "technology", "root_cause", "solution", "root_cause_category"
}

# All known columns that map to DB columns
KNOWN_COLUMNS = {
    "ticket_id", "application", "issue_type", "priority", "environment",
    "component", "summary", "created_date", "resolved_date", "status",
    "technology", "version", "region", "issue_description", "root_cause",
    "solution", "tags", "service", "dependency", "estimated_resolution_time",
    "business_impact", "key_metric", "root_cause_category"
}

INSERT_COLUMNS = [
    "ticket_id", "application", "issue_type", "priority", "environment",
    "component", "summary", "created_date", "resolved_date", "status",
    "technology", "version", "region", "issue_description", "root_cause",
    "solution", "tags", "service", "dependency", "estimated_resolution_time",
    "business_impact", "key_metric", "root_cause_category", "embedding"
]


# ---------------------------------------------------------------------------
# Text builder for embedding
# ---------------------------------------------------------------------------

def build_ticket_text(row) -> str:
    """Build a rich text representation of a ticket for embedding."""
    parts = [
        f"Ticket: {row.get('ticket_id', '')}",
        f"Summary: {row.get('summary', '')}",
        f"Description: {row.get('issue_description', '')}",
        f"Issue Type: {row.get('issue_type', '')}",
        f"Priority: {row.get('priority', '')}",
        f"Component: {row.get('component', '')}",
        f"Technology: {row.get('technology', '')}",
        f"Root Cause: {row.get('root_cause', '')}",
        f"Solution: {row.get('solution', '')}",
        f"Tags: {row.get('tags', '')}",
        f"Service: {row.get('service', '')}",
        f"Dependency: {row.get('dependency', '')}",
        f"Root Cause Category: {row.get('root_cause_category', '')}",
        f"Business Impact: {row.get('business_impact', '')}",
    ]
    return " | ".join(p for p in parts if p.split(": ", 1)[-1].strip())


# ---------------------------------------------------------------------------
# CSV validation
# ---------------------------------------------------------------------------

def validate_csv(filepath: str) -> dict:
    """Validate a CSV file and return status info."""
    result = {
        "file": os.path.basename(filepath),
        "valid": False,
        "rows": 0,
        "errors": [],
        "warnings": []
    }

    try:
        df = pd.read_csv(filepath, nrows=0)
    except Exception as e:
        result["errors"].append(f"Cannot read CSV: {e}")
        return result

    columns = set(df.columns)
    missing_required = REQUIRED_COLUMNS - columns
    missing_recommended = RECOMMENDED_COLUMNS - columns

    if missing_required:
        result["errors"].append(f"Missing required columns: {', '.join(missing_required)}")
        return result

    if missing_recommended:
        result["warnings"].append(f"Missing recommended columns: {', '.join(missing_recommended)}")

    df_full = pd.read_csv(filepath)
    result["rows"] = len(df_full)
    result["columns"] = list(columns)
    result["valid"] = True

    if "ticket_id" in df_full.columns:
        dupes = df_full["ticket_id"].duplicated().sum()
        if dupes > 0:
            result["warnings"].append(f"{dupes} duplicate ticket_id(s) found")

    return result


# ---------------------------------------------------------------------------
# Ingestion logic
# ---------------------------------------------------------------------------

async def ingest_file(filepath: str, dry_run: bool = False) -> dict:
    """Ingest a single CSV file. Returns stats."""
    stats = {"file": os.path.basename(filepath), "total": 0, "ingested": 0, "skipped": 0, "errors": 0, "time_s": 0}
    start = time.perf_counter()

    try:
        df = pd.read_csv(filepath)
    except Exception as e:
        stats["errors"] = 1
        stats["error_msg"] = str(e)
        return stats

    stats["total"] = len(df)

    if dry_run:
        stats["time_s"] = round(time.perf_counter() - start, 2)
        return stats

    conn = get_db()
    cur = conn.cursor()

    for idx, row in df.iterrows():
        ticket_id = str(row.get("ticket_id", "")).strip()
        if not ticket_id:
            stats["errors"] += 1
            continue

        # Check duplicate
        cur.execute("SELECT id FROM jira_tickets WHERE ticket_id = %s", (ticket_id,))
        if cur.fetchone():
            stats["skipped"] += 1
            continue

        try:
            text = build_ticket_text(row)
            embedding = await get_embedding(text)
            embedding_str = "[" + ",".join(str(x) for x in embedding.embedding) + "]"

            cur.execute("""
                INSERT INTO jira_tickets (
                    ticket_id, application, issue_type, priority, environment,
                    component, summary, created_date, resolved_date, status,
                    technology, version, region, issue_description, root_cause,
                    solution, tags, service, dependency, estimated_resolution_time,
                    business_impact, key_metric, root_cause_category, embedding
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
            """, (
                ticket_id,
                row.get("application"), row.get("issue_type"), row.get("priority"),
                row.get("environment"), row.get("component"), row.get("summary"),
                row.get("created_date"), row.get("resolved_date"), row.get("status"),
                row.get("technology"), row.get("version"), row.get("region"),
                row.get("issue_description"), row.get("root_cause"), row.get("solution"),
                row.get("tags"), row.get("service"), row.get("dependency"),
                row.get("estimated_resolution_time"), row.get("business_impact"),
                row.get("key_metric"), row.get("root_cause_category"), embedding_str
            ))
            conn.commit()
            stats["ingested"] += 1
            print(f"  [{stats['ingested']}/{stats['total']}] {ticket_id}")
        except Exception as e:
            stats["errors"] += 1
            print(f"  ERROR {ticket_id}: {e}")

    cur.close()
    conn.close()
    stats["time_s"] = round(time.perf_counter() - start, 2)
    return stats


async def ingest_all(files: list[str] = None, dry_run: bool = False) -> list[dict]:
    """Ingest all CSV files from dataset/ or a specific list."""
    if files is None:
        files = sorted(glob.glob(os.path.join(DATASET_DIR, "*.csv")))

    if not files:
        print("No CSV files found in dataset/")
        return []

    results = []
    for filepath in files:
        print(f"\n{'='*60}")
        print(f"Processing: {os.path.basename(filepath)}")
        print(f"{'='*60}")

        result = await ingest_file(filepath, dry_run=dry_run)
        results.append(result)

        status = "DRY RUN" if dry_run else "DONE"
        print(f"  {status}: {result['ingested']} ingested, {result['skipped']} skipped, {result['errors']} errors ({result['time_s']}s)")

    return results


def clear_all_tickets():
    """Remove all tickets from the database."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM jira_tickets")
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return deleted


def get_stats() -> dict:
    """Get current database statistics."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS total FROM jira_tickets")
    total = cur.fetchone()["total"]
    cur.execute("SELECT root_cause_category, COUNT(*) AS cnt FROM jira_tickets GROUP BY root_cause_category ORDER BY cnt DESC LIMIT 10")
    categories = cur.fetchall()
    cur.execute("SELECT priority, COUNT(*) AS cnt FROM jira_tickets GROUP BY priority ORDER BY cnt DESC")
    priorities = cur.fetchall()
    cur.close()
    conn.close()
    return {"total_tickets": total, "categories": categories, "priorities": priorities}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="JIRA Ticket Ingestion Pipeline (AWS)")
    parser.add_argument("--file", "-f", help="Specific CSV file to ingest")
    parser.add_argument("--clear", action="store_true", help="Clear all tickets before ingesting")
    parser.add_argument("--dry-run", action="store_true", help="Validate CSVs without ingesting")
    parser.add_argument("--stats", action="store_true", help="Show database statistics")
    parser.add_argument("--validate", action="store_true", help="Validate CSV files only")
    args = parser.parse_args()

    print("=" * 60)
    print("JIRA Ticket Ingestion Pipeline (AWS)")
    print("=" * 60)

    if args.stats:
        stats = get_stats()
        print(f"\nTotal tickets: {stats['total_tickets']}")
        print("\nBy category:")
        for cat, cnt in stats["categories"]:
            print(f"  {cat or 'N/A':40s} {cnt}")
        print("\nBy priority:")
        for pri, cnt in stats["priorities"]:
            print(f"  {pri or 'N/A':40s} {cnt}")
        return

    if args.clear:
        deleted = clear_all_tickets()
        print(f"Cleared {deleted} tickets from database")

    if args.validate:
        files = [args.file] if args.file else sorted(glob.glob(os.path.join(DATASET_DIR, "*.csv")))
        for f in files:
            result = validate_csv(f)
            status = "VALID" if result["valid"] else "INVALID"
            print(f"\n{result['file']}: {status} ({result['rows']} rows)")
            for e in result.get("errors", []):
                print(f"  ERROR: {e}")
            for w in result.get("warnings", []):
                print(f"  WARN: {w}")
        return

    # Initialize DB
    init_db()

    # Determine files to process
    if args.file:
        if not os.path.exists(args.file):
            print(f"File not found: {args.file}")
            sys.exit(1)
        files = [args.file]
    else:
        files = sorted(glob.glob(os.path.join(DATASET_DIR, "*.csv")))

    # Run ingestion
    start = time.perf_counter()
    results = asyncio.run(ingest_all(files, dry_run=args.dry_run))
    total_time = round(time.perf_counter() - start, 2)

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    total_ingested = sum(r["ingested"] for r in results)
    total_skipped = sum(r["skipped"] for r in results)
    total_errors = sum(r["errors"] for r in results)
    print(f"Files processed:  {len(results)}")
    print(f"Tickets ingested: {total_ingested}")
    print(f"Tickets skipped:  {total_skipped} (duplicates)")
    print(f"Errors:           {total_errors}")
    print(f"Total time:       {total_time}s")


if __name__ == "__main__":
    main()
