"""
Lightweight Prometheus-compatible metrics collector.

Collects counters, histograms, and gauges in-memory.
Exposed via /metrics endpoint in Prometheus text exposition format.
"""

import time
import math
import threading
from collections import defaultdict


# ---------------------------------------------------------------------------
# Default histogram buckets (in seconds)
# ---------------------------------------------------------------------------

DEFAULT_DURATION_BUCKETS = (
    0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, float("inf")
)

TOKEN_BUCKETS = (
    10, 25, 50, 100, 250, 500, 1000, 2000, 4000, 8000, float("inf")
)


# ---------------------------------------------------------------------------
# Metric primitives
# ---------------------------------------------------------------------------

class Counter:
    """Monotonically increasing counter with label support."""

    def __init__(self, name: str, help_text: str = ""):
        self.name = name
        self.help_text = help_text
        self._values: dict[tuple, float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, value: float = 1, **labels):
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._values[key] += value

    def get(self, **labels) -> float:
        key = tuple(sorted(labels.items()))
        return self._values.get(key, 0.0)

    def render(self) -> str:
        lines = []
        if self.help_text:
            lines.append(f"# HELP {self.name} {self.help_text}")
        lines.append(f"# TYPE {self.name} counter")
        with self._lock:
            for key, value in sorted(self._values.items()):
                label_str = ",".join(f'{k}="{v}"' for k, v in key) if key else ""
                suffix = f"{{{label_str}}}" if label_str else ""
                lines.append(f"{self.name}{suffix} {self._format_value(value)}")
        return "\n".join(lines)

    @staticmethod
    def _format_value(v: float) -> str:
        if v == int(v):
            return str(int(v))
        return f"{v:.6g}"


class Histogram:
    """Histogram with configurable buckets for tracking distributions."""

    def __init__(self, name: str, help_text: str = "", buckets: tuple = DEFAULT_DURATION_BUCKETS):
        self.name = name
        self.help_text = help_text
        self.buckets = buckets
        self._data: dict[tuple, list] = defaultdict(list)
        self._lock = threading.Lock()

    def observe(self, value: float, **labels):
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._data[key].append(value)

    def render(self) -> str:
        lines = []
        if self.help_text:
            lines.append(f"# HELP {self.name} {self.help_text}")
        lines.append(f"# TYPE {self.name} histogram")

        with self.lock_held():
            for key, values in sorted(self._data.items()):
                label_str = ",".join(f'{k}="{v}"' for k, v in key) if key else ""

                count = len(values)
                total = sum(values)

                # Bucket counts (skip +Inf since _count serves that role)
                for bucket in self.buckets:
                    if bucket == float("inf"):
                        continue
                    bucket_count = sum(1 for v in values if v <= bucket)
                    le_str = f"{self.name}{{{label_str + ',' if label_str else ''}le=\"{_fmt_bucket(bucket)}\"}}"
                    lines.append(f"{le_str} {bucket_count}")

                # +Inf bucket (equals count)
                if label_str:
                    lines.append(f"{self.name}{{{label_str},le=\"+Inf\"}} {count}")
                    lines.append(f"{self.name}_sum{{{label_str}}} {_fmt_value(total)}")
                    lines.append(f"{self.name}_count{{{label_str}}} {count}")
                else:
                    lines.append(f"{self.name}{{le=\"+Inf\"}} {count}")
                    lines.append(f"{self.name}_sum {_fmt_value(total)}")
                    lines.append(f"{self.name}_count {count}")

        return "\n".join(lines)

    def lock_held(self):
        """Context manager for thread-safe access during render."""
        return _LockCtx(self._lock, self._data)

    def p50(self, **labels) -> float:
        key = tuple(sorted(labels.items()))
        with self._lock:
            vals = self._data.get(key, [])
        if not vals:
            return 0.0
        vals_sorted = sorted(vals)
        idx = len(vals_sorted) // 2
        return vals_sorted[idx]

    def p95(self, **labels) -> float:
        key = tuple(sorted(labels.items()))
        with self._lock:
            vals = self._data.get(key, [])
        if not vals:
            return 0.0
        vals_sorted = sorted(vals)
        idx = int(math.ceil(0.95 * len(vals_sorted))) - 1
        return vals_sorted[min(idx, len(vals_sorted) - 1)]

    def p99(self, **labels) -> float:
        key = tuple(sorted(labels.items()))
        with self._lock:
            vals = self._data.get(key, [])
        if not vals:
            return 0.0
        vals_sorted = sorted(vals)
        idx = int(math.ceil(0.99 * len(vals_sorted))) - 1
        return vals_sorted[min(idx, len(vals_sorted) - 1)]

    def avg(self, **labels) -> float:
        key = tuple(sorted(labels.items()))
        with self._lock:
            vals = self._data.get(key, [])
        if not vals:
            return 0.0
        return sum(vals) / len(vals)


class _LockCtx:
    def __init__(self, lock, data):
        self._lock = lock
        self._data = data
    def __enter__(self):
        self._lock.acquire()
        return self._data
    def __exit__(self, *args):
        self._lock.release()


class Gauge:
    """Single-value gauge with label support."""

    def __init__(self, name: str, help_text: str = ""):
        self.name = name
        self.help_text = help_text
        self._values: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def set(self, value: float, **labels):
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._values[key] = value

    def inc(self, value: float = 1, **labels):
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + value

    def dec(self, value: float = 1, **labels):
        self.inc(-value, **labels)

    def get(self, **labels) -> float:
        key = tuple(sorted(labels.items()))
        return self._values.get(key, 0.0)

    def render(self) -> str:
        lines = []
        if self.help_text:
            lines.append(f"# HELP {self.name} {self.help_text}")
        lines.append(f"# TYPE {self.name} gauge")
        with self._lock:
            for key, value in sorted(self._values.items()):
                label_str = ",".join(f'{k}="{v}"' for k, v in key) if key else ""
                suffix = f"{{{label_str}}}" if label_str else ""
                lines.append(f"{self.name}{suffix} {_fmt_value(value)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_value(v: float) -> str:
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return f"{v:.6g}"


def _fmt_bucket(v: float) -> str:
    if v == float("inf"):
        return "+Inf"
    if v == int(v):
        return str(int(v))
    return str(v)


# ---------------------------------------------------------------------------
# MetricsCollector — singleton registry
# ---------------------------------------------------------------------------

class MetricsCollector:
    """Central metrics registry. All metrics are registered and accessed here."""

    def __init__(self):
        # --- HTTP request metrics ---
        self.http_requests = Counter("http_requests_total", "Total HTTP requests")
        self.http_request_duration = Histogram(
            "http_request_duration_seconds", "HTTP request latency in seconds"
        )
        self.http_request_errors = Counter("http_request_errors_total", "Total HTTP request errors by type")

        # --- LLM metrics ---
        self.llm_duration = Histogram(
            "llm_duration_seconds", "LLM inference latency in seconds"
        )
        self.llm_tokens = Counter("llm_tokens_total", "Total LLM tokens consumed")
        self.llm_requests = Counter("llm_requests_total", "Total LLM requests")

        # --- Embedding metrics ---
        self.embedding_duration = Histogram(
            "embedding_duration_seconds", "Embedding generation latency in seconds"
        )
        self.embedding_requests = Counter("embedding_requests_total", "Total embedding requests")

        # --- Database metrics ---
        self.db_query_duration = Histogram(
            "db_query_duration_seconds", "Database query latency in seconds"
        )
        self.db_query_total = Counter("db_query_total", "Total database queries")

        # --- Cache metrics ---
        self.cache_operations = Counter("cache_operations_total", "Cache operations by type")
        self.cache_size = Gauge("cache_size", "Current cache size")
        self.cache_hit_rate = Gauge("cache_hit_rate", "Cache hit rate (rolling)")

        # --- Circuit breaker metrics ---
        self.circuit_breaker_state = Gauge("circuit_breaker_state", "Circuit breaker state: 0=closed, 1=open, 2=half_open")
        self.circuit_breaker_events = Counter("circuit_breaker_events_total", "Circuit breaker state transitions")

        # --- Rate limiter metrics ---
        self.rate_limit_rejected = Counter("rate_limit_rejected_total", "Rate limited requests")
        self.rate_limit_allowed = Counter("rate_limit_allowed_total", "Rate limit allowed requests")

        # --- Guardrail metrics ---
        self.guardrail_blocked = Counter("guardrail_blocked_total", "Requests blocked by guardrails")
        self.guardrail_duration = Histogram(
            "guardrail_duration_seconds", "Guardrail validation latency"
        )

        # --- Vector search metrics ---
        self.vector_search_duration = Histogram(
            "vector_search_duration_seconds", "pgvector similarity search latency"
        )

        # --- Application gauges ---
        self.active_sessions = Gauge("active_sessions", "Number of active chat sessions")
        self.total_tickets = Gauge("total_tickets", "Total tickets in knowledge base")
        self.uptime_seconds = Gauge("uptime_seconds", "Application uptime in seconds")

        # --- Triage ticket metrics ---
        self.triage_tickets_created = Counter("triage_tickets_created_total", "Total triage tickets created")
        self.triage_tickets_closed = Counter("triage_tickets_closed_total", "Total triage tickets closed/resolved")
        self.triage_feedback_submitted = Counter("triage_feedback_submitted_total", "Total feedback submissions")
        self.triage_feedback_rating = Histogram(
            "triage_feedback_rating", "Triage feedback rating distribution",
            buckets=(1, 2, 3, 4, 5)
        )

        # --- AgentCore Runtime metrics ---
        self.agentcore_invocations = Counter(
            "agentcore_invocations_total", "Total AgentCore Runtime invocations"
        )
        self.agentcore_invocation_duration = Histogram(
            "agentcore_invocation_duration_seconds", "AgentCore Runtime invocation latency"
        )
        self.agentcore_invocation_errors = Counter(
            "agentcore_invocation_errors_total", "AgentCore Runtime invocation errors"
        )
        self.agentcore_active_sessions = Gauge(
            "agentcore_active_sessions", "Active AgentCore sessions"
        )

        # --- AgentCore Memory metrics ---
        self.agentcore_memory_reads = Counter(
            "agentcore_memory_reads_total", "Total AgentCore Memory read operations"
        )
        self.agentcore_memory_writes = Counter(
            "agentcore_memory_writes_total", "Total AgentCore Memory write operations"
        )
        self.agentcore_memory_errors = Counter(
            "agentcore_memory_errors_total", "Total AgentCore Memory errors"
        )
        self.agentcore_memory_latency = Histogram(
            "agentcore_memory_latency_seconds", "AgentCore Memory operation latency"
        )

        # --- AgentCore Gateway metrics ---
        self.agentcore_gateway_requests = Counter(
            "agentcore_gateway_requests_total", "Total AgentCore Gateway tool requests"
        )
        self.agentcore_gateway_duration = Histogram(
            "agentcore_gateway_duration_seconds", "AgentCore Gateway tool invocation latency"
        )
        self.agentcore_gateway_errors = Counter(
            "agentcore_gateway_errors_total", "Total AgentCore Gateway errors"
        )

        # --- AgentCore Identity metrics ---
        self.agentcore_auth_success = Counter(
            "agentcore_auth_success_total", "Successful AgentCore authentication"
        )
        self.agentcore_auth_failure = Counter(
            "agentcore_auth_failure_total", "Failed AgentCore authentication"
        )

        # --- Cache hit/miss counters for rate calculation ---
        self._cache_hit_count = 0
        self._cache_miss_count = 0
        self._cache_lock = threading.Lock()
        self._start_time = time.time()

    def record_cache_hit(self):
        with self._cache_lock:
            self._cache_hit_count += 1
        self.cache_operations.inc(operation="hit")
        self._update_cache_hit_rate()

    def record_cache_miss(self):
        with self._cache_lock:
            self._cache_miss_count += 1
        self.cache_operations.inc(operation="miss")
        self._update_cache_hit_rate()

    def record_cache_evict(self):
        self.cache_operations.inc(operation="evict")

    def _update_cache_hit_rate(self):
        with self._cache_lock:
            total = self._cache_hit_count + self._cache_miss_count
            if total > 0:
                self.cache_hit_rate.set(self._cache_hit_count / total)

    def update_uptime(self):
        self.uptime_seconds.set(time.time() - self._start_time)

    def render_prometheus(self) -> str:
        """Render all metrics in Prometheus text exposition format."""
        self.update_uptime()
        sections = []
        for attr in vars(self):
            obj = getattr(self, attr)
            if hasattr(obj, "render") and callable(obj.render) and attr.startswith("_") is False:
                rendered = obj.render()
                if rendered:
                    sections.append(rendered)
        return "\n\n".join(sections) + "\n"

    def get_summary(self) -> dict:
        """Return a JSON-friendly summary of key metrics."""
        return {
            "http": {
                "total_requests": sum(self.http_requests._values.values()),
                "error_rate": sum(self.http_request_errors._values.values()),
            },
            "llm": {
                "total_requests": sum(self.llm_requests._values.values()),
                "total_tokens": sum(self.llm_tokens._values.values()),
            },
            "cache": {
                "hit_rate": self.cache_hit_rate.get(),
                "embedding_size": self.cache_size.get(cache="embedding"),
                "chat_size": self.cache_size.get(cache="chat"),
            },
            "circuit_breaker": {
                "state": self.circuit_breaker_state.get(),
            },
            "agentcore": {
                "runtime": {
                    "total_invocations": sum(self.agentcore_invocations._values.values()),
                    "error_count": sum(self.agentcore_invocation_errors._values.values()),
                    "avg_duration_ms": round(self.agentcore_invocation_duration.avg() * 1000, 2) if self.agentcore_invocation_duration.avg() > 0 else 0,
                    "active_sessions": self.agentcore_active_sessions.get(),
                },
                "memory": {
                    "total_reads": sum(self.agentcore_memory_reads._values.values()),
                    "total_writes": sum(self.agentcore_memory_writes._values.values()),
                    "error_count": sum(self.agentcore_memory_errors._values.values()),
                    "avg_latency_ms": round(self.agentcore_memory_latency.avg() * 1000, 2) if self.agentcore_memory_latency.avg() > 0 else 0,
                },
                "gateway": {
                    "total_requests": sum(self.agentcore_gateway_requests._values.values()),
                    "error_count": sum(self.agentcore_gateway_errors._values.values()),
                    "avg_duration_ms": round(self.agentcore_gateway_duration.avg() * 1000, 2) if self.agentcore_gateway_duration.avg() > 0 else 0,
                },
                "identity": {
                    "auth_success": sum(self.agentcore_auth_success._values.values()),
                    "auth_failure": sum(self.agentcore_auth_failure._values.values()),
                },
            },
            "uptime_seconds": time.time() - self._start_time,
        }


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

metrics = MetricsCollector()
