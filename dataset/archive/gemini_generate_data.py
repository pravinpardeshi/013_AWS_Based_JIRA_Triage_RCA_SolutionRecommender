import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta

# Set seed for reproducibility
np.random.seed(42)
random.seed(42)

# Define components and categories
components = ['Authentication', 'Billing & Payments', 'User Dashboard', 'API Gateway', 'Database Service', 'Notification Engine', 'Search & Filtering', 'Reporting Service']
environments = ['Production', 'Staging', 'UAT', 'Development']
priorities = ['P1', 'P2', 'P3', 'P4']
priority_weights = [0.10, 0.25, 0.45, 0.20] # More P3/P2, fewer P1

statuses = ['Resolved', 'Closed', 'In Progress']
assignees = ['dev_lead_alice', 'backend_bob', 'sre_charlie', 'sec_danielle', 'qa_eve', 'frontend_frank']

# Templates for generation
issue_templates = {
    'Authentication': [
        ("Users experiencing 500 error when logging in via OAuth2 Google provider.", "OAuth token endpoint timeout due to misconfigured upstream pool timeout in Redis session store.", "Increased Redis connection pool size and adjusted timeout threshold from 2s to 5s in API gateway configuration."),
        ("Password reset emails are delayed by over 30 minutes.", "SMTP relay queue bottleneck caused by burst traffic hitting SendGrid rate limits.", "Migrated to a dedicated IP pool and implemented exponential backoff queue retry logic in notification worker."),
        ("Multi-Factor Authentication (MFA) SMS codes failing for international numbers.", "Twilio API failing due to outdated country code mapping in user profile service.", "Updated country code regex parser and added fallback route to alternate SMS aggregator (Plivo)."),
        ("Session tokens expiring prematurely after 15 minutes instead of 8 hours.", "JWT expiration time (exp) claim hardcoded incorrectly in auth middleware during last hotfix.", "Fixed token generation payload logic in auth-service v2.4.1 and deployed hotfix.")
    ],
    'Billing & Payments': [
        ("Credit card processing failing with 'Gateway Timeout' status code.", "Stripe webhook listener crashing on malformed JSON payload from recurring subscription events.", "Added robust JSON schema validation and error-handling try-catch blocks in billing webhook handler."),
        ("Duplicate charges observed for 14 users during peak checkout window.", "Database deadlocks in transaction table due to concurrent API retry attempts without idempotency keys.", "Implemented idempotency header enforcement and optimized database isolation level from Serializable to Read Committed."),
        ("Invoice PDF generation failing to render currency symbols correctly.", "Missing TrueType fonts (DejaVuSans) in the containerized microservice base image.", "Updated Dockerfile to include core-fonts package and rebuilt container image."),
        ("Promo code discount not applying when combined with seasonal sale items.", "Discount calculation precedence bug in cart pricing engine where seasonal rule overwrote coupon rule.", "Refactored pricing precedence hierarchy in billing engine to evaluate promotional coupons after seasonal markdowns.")
    ],
    'User Dashboard': [
        ("Dashboard widgets failing to load with infinite spinner.", "GraphQL query timeout fetching unindexed user activity logs across massive tables.", "Added composite index on (user_id, timestamp) and implemented query pagination with cursor-based fetching."),
        ("User avatar image uploads failing with 413 Payload Too Large.", "Nginx client_max_body_size set to 1MB, blocking high-res profile pictures.", "Increased Nginx client_max_body_size to 10M and added client-side image compression in React frontend."),
        ("Dark mode theme preference resetting to light mode on page refresh.", "Local storage key mismatch between legacy frontend module and modern React context provider.", "Unified storage key nomenclature to 'app_theme_pref' across all frontend bundles."),
        ("Export to CSV button unresponsive on Safari browser.", "Blob URL download method blocked by Safari strict security sandbox restrictions for cross-origin frames.", "Replaced Blob URL download mechanism with standard base64 data-URI anchor tag fallback for Safari.")
    ],
    'API Gateway': [
        ("API Gateway returning 502 Bad Gateway intermittently under high load.", "Upstream worker pods exhausting file descriptors due to unclosed socket connections in gRPC client.", "Fixed connection pooling in gRPC client and increased OS file descriptor limit (nofile) in Kubernetes deployment manifest."),
        ("Rate limiting rules bypassing for whitelisted corporate IPs.", "CIDR mask evaluation error in Envoy proxy Lua rate-limiting filter script.", "Corrected bitwise CIDR matching logic in Lua filter and added unit tests for IP whitelist validation."),
        ("CORS headers missing on preflight OPTIONS requests for mobile client endpoints.", "CORS middleware placed after authentication interceptor, causing unauthenticated preflight requests to be dropped.", "Reordered middleware stack in API gateway to evaluate CORS headers prior to authentication checks."),
        ("Request payload logging leaking sensitive API authorization tokens.", "Plaintext header logging enabled in production debug interceptor.", "Configured log sanitizer to redact Authorization, Cookie, and X-API-Key headers before writing to log aggregator.")
    ],
    'Database Service': [
        ("High CPU utilization spikes on primary PostgreSQL cluster during nightly batch jobs.", "Unoptimized table scan running on audit_logs table lacking an index on created_at.", "Created a BRIN index on audit_logs(created_at) and rescheduled batch job to run during low-traffic window (03:00 UTC)."),
        ("Database connection pool exhaustion causing service-wide outage.", "Application instances failing to release connections back to pool upon encountering unhandled database exceptions.", "Implemented automatic connection release in finally blocks across all database repository interfaces."),
        ("Replication lag exceeding 45 seconds between primary and read-replica nodes.", "Heavy write throughput saturating network bandwidth and replica I/O subsystem.", "Upgraded EBS volume IOPS on replica instance from 3000 to 10000 and enabled synchronous commit optimization."),
        ("Schema migration failing halfway through execution during CI/CD deployment.", "Non-idempotent migration script attempting to create an existing table without 'IF NOT EXISTS' clause.", "Modified migration script to use safe DDL syntax ('CREATE TABLE IF NOT EXISTS') and rolled back corrupted state.")
    ],
    'Notification Engine': [
        ("Push notifications failing to deliver to iOS devices after APNs certificate renewal.", "Expired Apple Push Notification service .p8 auth key loaded into notification service secret store.", "Rotated APNs auth key in AWS Secrets Manager and restarted notification worker pods."),
        ("Users receiving duplicate email alerts for critical incident triggers.", "Kafka consumer group offset commit failing due to rebalancing loop timeout.", "Tuned Kafka consumer session.timeout.ms and max.poll.interval.ms properties to prevent false-positive rebalance loops."),
        ("Webhook payloads failing delivery to third-party subscriber endpoints.", "DNS resolution failure within container resolver when target endpoint uses internal private domains.", "Configured custom upstream DNS resolver in Kubernetes CoreDNS configmap for outbound webhook traffic.")
    ],
    'Search & Filtering': [
        ("Elasticsearch cluster yellow health status due to unassigned replica shards.", "Node disk watermarks exceeded (95% full), preventing shard allocation.", "Scaled up EBS storage volume on Elasticsearch nodes and purged outdated log indices older than 30 days."),
        ("Search autocomplete returning stale results after new product catalog ingestion.", "Search index cache invalidation hook failing to trigger after nightly bulk import.", "Added explicit cache flush command to the end of the nightly catalog synchronization cron job script.")
    ],
    'Reporting Service': [
        ("Monthly executive PDF reports timing out after 120 seconds.", "Puppeteer headless browser instance hanging due to memory exhaustion while rendering high-res charts.", "Optimized Chart.js rendering options to disable animations and increased container memory limit from 512MB to 2GB."),
        ("Exported Excel spreadsheet showing numerical values stored as text strings.", "Pandas dataframe export missing explicit dtype specification for financial columns.", "Explicitly cast financial columns to float64 before exporting to Excel workbook generator.")
    ]
}

records = []
start_date = datetime(2026, 1, 15)

for i in range(1, 51):
    ticket_id = f"APP-{1000 + i}"
    component = random.choice(components)
    env = random.choice(environments)
    priority = np.random.choice(priorities, p=priority_weights)
    status = random.choice(statuses)
    assignee = random.choice(assignees)
    
    # Pick a template based on component
    template_list = issue_templates[component]
    template = random.choice(template_list)
    
    desc = template[0]
    rc = template[1]
    sol = template[2]
    
    # Add some variation or ticket title
    summary = f"[{component}] {desc[:60]}..."
    
    # Dates
    created_offset = random.randint(1, 40)
    created_date = start_date + timedelta(days=created_offset)
    resolved_date = created_date + timedelta(hours=random.randint(2, 72)) if status in ['Resolved', 'Closed'] else np.nan
    
    # Additional AI-friendly metadata fields
    # Symptom categories for triage
    symptom_category = component
    # Error codes
    error_codes = random.choice(['ERR-500', 'ERR-401', 'ERR-TIMEOUT', 'ERR-DB-LOCK', 'ERR-413', 'NONE'])
    # Impacted service
    impacted_service = f"microservice-{component.lower().replace(' & ', '-').replace(' ', '-')}"
    
    records.append({
        'ticket_id': ticket_id,
        'summary': summary,
        'component': component,
        'environment': env,
        'priority': priority,
        'status': status,
        'assignee': assignee,
        'created_date': created_date.strftime('%Y-%m-%d %H:%M:%S'),
        'resolved_date': resolved_date.strftime('%Y-%m-%d %H:%M:%S') if pd.notna(resolved_date) else '',
        'symptom_category': symptom_category,
        'error_code': error_codes,
        'impacted_service': impacted_service,
        'issue_description': desc,
        'root_cause': rc,
        'solution': sol,
        'resolution_notes': f"Root cause identified and resolved via standard protocol. Verified in {env}."
    })

df = pd.DataFrame(records)
csv_filename = 'jira_tickets_rag_dataset.csv'
df.to_csv(csv_filename, index=False)
print(f"Successfully generated {len(df)} records in {csv_filename}")


