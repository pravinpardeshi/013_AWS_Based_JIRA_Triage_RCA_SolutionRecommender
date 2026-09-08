#!/usr/bin/env python3
"""
JIRA Daily Incremental Ingestion Pipeline
==========================================
Fetches yesterday's tickets from JIRA REST API and upserts them into
RDS PostgreSQL (pgvector) with Bedrock embeddings.

Designed for daily cron execution:
    0 6 * * * cd /path/to/019_AWS_JIRA_Ticket_Solution && python jira_daily_ingest.py >> logs/ingest.log 2>&1

Usage:
    python jira_daily_ingest.py                    # Ingest yesterday's tickets
    python jira_daily_ingest.py --days 3           # Ingest last 3 days
    python jira_daily_ingest.py --date 2026-09-07  # Ingest a specific date
    python jira_daily_ingest.py --dry-run          # Preview without ingesting
    python jira_daily_ingest.py --stats            # Show DB stats
    python jira_daily_ingest.py --cron             # Run as daemon (daily loop)
"""

import asyncio
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from config import (
    JIRA_BASE_URL,
    JIRA_API_TOKEN,
    JIRA_USER_EMAIL,
)
from database import get_db, init_db
from bedrock_client import get_embedding

# ---------------------------------------------------------------------------
# JIRA field mapping
# ---------------------------------------------------------------------------
# JIRA REST API field name → (DB column, transform)
# Some JIRA fields are nested (e.g. fields.priority.name)

JIRA_FIELD_MAP = {
    "key":                "ticket_id",
    "fields.summary":     "summary",
    "fields.description": "issue_description",
    "fields.issuetype.name":   "issue_type",
    "fields.priority.name":    "priority",
    "fields.status.name":      "status",
    "fields.components[].name": "component",
    "fields.labels":           "tags",
    "fields.created":          "created_date",
    "fields.resolutiondate":   "resolved_date",
    "fields.assignee.displayName": None,  # not in DB schema, skip
}

# Flat field names (no nested access needed)
FLAT_JIRA_FIELDS = ["summary", "description", "status", "assignee", "reporter",
                    "priority", "issuetype", "labels", "components",
                    "created", "resolutiondate"]


def _jira_date(dt_str: str) -> str | None:
    """Convert JIRA ISO datetime to string for DB."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return dt_str


def _extract_field(fields: dict, dotpath: str) -> str | None:
    """Safely extract a value from nested dict using dot notation."""
    parts = dotpath.split(".")
    current = fields
    for part in parts:
        if part.startswith("fields"):
            # already using dotpath starting with "fields"
            current = current.get(part, {}) if isinstance(current, dict) else None
        else:
            current = current.get(part, {}) if isinstance(current, dict) else None
        if current is None:
            return None
    if isinstance(current, list):
        return ", ".join(str(x.get("name", x)) if isinstance(x, dict) else str(x) for x in current)
    if isinstance(current, dict):
        return current.get("name", str(current))
    return str(current) if current else None


def jira_response_to_row(issue: dict) -> dict:
    """Map a JIRA REST API issue object to a DB row dict."""
    fields = issue.get("fields", {})
    key = issue.get("key", "")

    row = {"ticket_id": key}
    row["summary"] = fields.get("summary") or ""
    row["issue_description"] = fields.get("description") or ""
    row["issue_type"] = (fields.get("issuetype") or {}).get("name") or ""
    row["priority"] = (fields.get("priority") or {}).get("name") or ""
    row["status"] = (fields.get("status") or {}).get("name") or ""
    row["created_date"] = _jira_date(fields.get("created"))
    row["resolved_date"] = _jira_date(fields.get("resolutiondate"))

    # Components
    components = fields.get("components") or []
    row["component"] = ", ".join(c.get("name", "") for c in components if c.get("name")) or ""

    # Labels → tags
    labels = fields.get("labels") or []
    row["tags"] = ", ".join(labels) if labels else ""

    # Custom fields — try common JIRA custom field IDs
    # These vary per JIRA instance; user may need to configure
    row["technology"] = _extract_custom(fields, "technology", "customfield_10001")
    row["version"] = _extract_custom(fields, "version", "customfield_10002")
    row["region"] = _extract_custom(fields, "region", "customfield_10003")
    row["environment"] = _extract_custom(fields, "environment", "customfield_10004")
    row["application"] = _extract_custom(fields, "application", "customfield_10005")
    row["service"] = _extract_custom(fields, "service", "customfield_10006")
    row["dependency"] = _extract_custom(fields, "dependency", "customfield_10007")
    row["estimated_resolution_time"] = _extract_custom(fields, "estimated_resolution_time", "customfield_10008")
    row["business_impact"] = _extract_custom(fields, "business_impact", "customfield_10009")
    row["key_metric"] = _extract_custom(fields, "key_metric", "customfield_10010")

    # Root cause / solution / category — typically filled after resolution
    row["root_cause"] = _extract_custom(fields, "root_cause", "customfield_10011")
    row["solution"] = _extract_custom(fields, "solution", "customfield_10012")
    row["root_cause_category"] = _extract_custom(fields, "root_cause_category", "customfield_10013")

    return row


def _extract_custom(fields: dict, friendly_name: str, field_id: str) -> str:
    """Try to extract a custom field value by known ID."""
    val = fields.get(field_id)
    if val is None:
        return ""
    if isinstance(val, dict):
        return val.get("name", str(val))
    if isinstance(val, list):
        return ", ".join(str(v.get("name", v)) if isinstance(v, dict) else str(v) for v in val)
    return str(val)


# ---------------------------------------------------------------------------
# JIRA API client
# ---------------------------------------------------------------------------

def _jira_headers() -> dict:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _jira_auth() -> tuple[str, str]:
    return (JIRA_USER_EMAIL, JIRA_API_TOKEN)


async def fetch_jira_issues(
    client: httpx.AsyncClient,
    jql: str,
    max_results: int = 200,
    start_at: int = 0,
) -> list[dict]:
    """Fetch issues from JIRA REST API v2."""
    url = f"{JIRA_BASE_URL.rstrip('/')}/rest/api/2/search"
    params = {
        "jql": jql,
        "startAt": start_at,
        "maxResults": min(max_results, 100),  # JIRA caps at 100 per request
        "fields": "*all",
    }

    all_issues = []
    while True:
        resp = await client.get(url, params=params, auth=_jira_auth(), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        issues = data.get("issues", [])
        all_issues.extend(issues)

        total = data.get("total", 0)
        start_at += len(issues)
        if start_at >= total or not issues:
            break
        params["startAt"] = start_at
        params["maxResults"] = min(max_results, 100)

    return all_issues


# ---------------------------------------------------------------------------
# Embedding + ingestion
# ---------------------------------------------------------------------------

def build_ticket_text(row: dict) -> str:
    """Build rich text representation for embedding."""
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


async def ingest_tickets(rows: list[dict], dry_run: bool = False) -> dict:
    """Upsert tickets into DB with Bedrock embeddings."""
    stats = {"total": len(rows), "ingested": 0, "skipped": 0, "errors": 0, "time_s": 0}
    start = time.perf_counter()

    if not rows:
        stats["time_s"] = round(time.perf_counter() - start, 2)
        return stats

    conn = get_db()
    cur = conn.cursor()

    for idx, row in enumerate(rows, 1):
        ticket_id = row.get("ticket_id", "").strip()
        if not ticket_id:
            stats["errors"] += 1
            continue

        # Check if ticket already exists
        cur.execute("SELECT id FROM jira_tickets WHERE ticket_id = %s", (ticket_id,))
        if cur.fetchone():
            stats["skipped"] += 1
            continue

        if dry_run:
            stats["ingested"] += 1
            print(f"  [{idx}/{stats['total']}] DRY RUN: {ticket_id}")
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
            print(f"  [{idx}/{stats['total']}] {ticket_id} - ingested")
        except Exception as e:
            stats["errors"] += 1
            print(f"  [{idx}/{stats['total']}] {ticket_id} - ERROR: {e}")

    cur.close()
    conn.close()
    stats["time_s"] = round(time.perf_counter() - start, 2)
    return stats


# ---------------------------------------------------------------------------
# Date range logic
# ---------------------------------------------------------------------------

def get_date_range(days_back: int = 1, specific_date: str = None) -> tuple[str, str]:
    """Return (start_date, end_date) in JIRA JQL date format (YYYY-MM-DD)."""
    if specific_date:
        dt = datetime.strptime(specific_date, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%d")

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = today - timedelta(days=1)
    start_date = today - timedelta(days=days_back)
    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")


def build_jql(start_date: str, end_date: str, project: str = None) -> str:
    """Build JQL query for incremental fetch."""
    parts = [f'created >= "{start_date}" AND created <= "{end_date}"']
    if project:
        parts.append(f'project = "{project}"')
    return " AND ".join(parts)


# ---------------------------------------------------------------------------
# DB stats
# ---------------------------------------------------------------------------

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
    cur.execute("SELECT MAX(created_date) AS latest FROM jira_tickets")
    latest = cur.fetchone()["latest"]
    cur.execute("SELECT MIN(created_date) AS earliest FROM jira_tickets")
    earliest = cur.fetchone()["earliest"]
    cur.close()
    conn.close()
    return {"total_tickets": total, "categories": categories, "priorities": priorities,
            "latest_ticket": str(latest) if latest else "N/A",
            "earliest_ticket": str(earliest) if earliest else "N/A"}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_ingest(start_date: str, end_date: str, project: str = None, dry_run: bool = False) -> dict:
    """Run the full ingestion pipeline for a date range."""
    jql = build_jql(start_date, end_date, project)
    print(f"  JQL: {jql}")

    if not JIRA_BASE_URL or not JIRA_API_TOKEN or not JIRA_USER_EMAIL:
        print("\n  ERROR: JIRA credentials not configured.")
        print("  Set JIRA_BASE_URL, JIRA_API_TOKEN, and JIRA_USER_EMAIL in backend/.env")
        return {"error": "missing_credentials"}

    async with httpx.AsyncClient() as client:
        print("  Fetching issues from JIRA...")
        issues = await fetch_jira_issues(client, jql)
        print(f"  Found {len(issues)} issues")

        if not issues:
            return {"total": 0, "ingested": 0, "skipped": 0, "errors": 0, "time_s": 0}

        rows = [jira_response_to_row(issue) for issue in issues]
        stats = await ingest_tickets(rows, dry_run=dry_run)
        return stats


def main():
    parser = argparse.ArgumentParser(
        description="JIRA Daily Incremental Ingestion Pipeline (AWS)"
    )
    parser.add_argument("--days", type=int, default=1,
                        help="Number of days back to fetch (default: 1 = yesterday)")
    parser.add_argument("--date", type=str, default=None,
                        help="Specific date to fetch (YYYY-MM-DD)")
    parser.add_argument("--project", type=str, default=None,
                        help="JIRA project key (e.g. INFRA, OPS)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview issues without ingesting")
    parser.add_argument("--stats", action="store_true",
                        help="Show database statistics")
    parser.add_argument("--cron", action="store_true",
                        help="Run as daemon, ingesting daily at a fixed interval")
    args = parser.parse_args()

    print("=" * 60)
    print("JIRA Daily Incremental Ingestion Pipeline (AWS)")
    print("=" * 60)

    if args.stats:
        stats = get_stats()
        print(f"\nTotal tickets:  {stats['total_tickets']}")
        print(f"Latest ticket:  {stats['latest_ticket']}")
        print(f"Earliest:       {stats['earliest_ticket']}")
        print("\nBy category:")
        for cat, cnt in stats["categories"]:
            print(f"  {cat or 'N/A':40s} {cnt}")
        print("\nBy priority:")
        for pri, cnt in stats["priorities"]:
            print(f"  {pri or 'N/A':40s} {cnt}")
        return

    if args.cron:
        print("\nStarting cron daemon mode (ingest daily)...")
        print("Press Ctrl+C to stop.\n")
        while True:
            try:
                start_date, end_date = get_date_range(args.days, args.date)
                print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                      f"Fetching tickets from {start_date} to {end_date}")
                init_db()
                stats = asyncio.run(run_ingest(start_date, end_date, args.project, args.dry_run))
                print(f"  Summary: {stats.get('ingested', 0)} ingested, "
                      f"{stats.get('skipped', 0)} skipped, "
                      f"{stats.get('errors', 0)} errors ({stats.get('time_s', 0)}s)")

                # Sleep until next day's run (e.g. 6 AM)
                now = datetime.now()
                next_run = now.replace(hour=6, minute=0, second=0, microsecond=0)
                if next_run <= now:
                    next_run += timedelta(days=1)
                sleep_secs = (next_run - now).total_seconds()
                print(f"  Sleeping until {next_run.strftime('%H:%M')} ({int(sleep_secs)}s)")
                time.sleep(sleep_secs)
            except KeyboardInterrupt:
                print("\nStopped.")
                break
            except Exception as e:
                print(f"  ERROR: {e}")
                print("  Retrying in 5 minutes...")
                time.sleep(300)
        return

    # Single run
    start_date, end_date = get_date_range(args.days, args.date)
    print(f"\nFetching tickets from {start_date} to {end_date}")
    print(f"Project filter: {args.project or 'ALL'}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}\n")

    init_db()
    start = time.perf_counter()
    stats = asyncio.run(run_ingest(start_date, end_date, args.project, args.dry_run))
    total_time = round(time.perf_counter() - start, 2)

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Date range:     {start_date} to {end_date}")
    print(f"Tickets found:  {stats.get('total', 0)}")
    print(f"Ingested:       {stats.get('ingested', 0)}")
    print(f"Skipped:        {stats.get('skipped', 0)} (duplicates)")
    print(f"Errors:         {stats.get('errors', 0)}")
    print(f"Total time:     {total_time}s")


if __name__ == "__main__":
    main()
