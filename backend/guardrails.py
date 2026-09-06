import re
import time
import base64
import binascii
from collections import defaultdict
from fastapi import HTTPException
from metrics import metrics
from dynamodb_guardrails import rate_limiter, circuit_breaker


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_INPUT_LENGTH = 2000
MAX_OUTPUT_TOKENS = 2048
MIN_SIMILARITY_THRESHOLD = 50.0
RATE_LIMIT_WINDOW = 60        # seconds
RATE_LIMIT_MAX_REQUESTS = 30  # per window per IP
JAILBREAK_SCORE_THRESHOLD = 3  # block if score >= this


# ---------------------------------------------------------------------------
# Jailbreak detection patterns (score-weighted)
# ---------------------------------------------------------------------------

# Each tuple: (compiled_regex, score, description)
JAILBREAK_PATTERNS: list[tuple[re.Pattern, int, str]] = [
    # --- Direct instruction override (high severity) ---
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above|earlier|preceding)\s+(instructions|prompts?|rules?|guidelines?|constraints?)", re.I), 3, "instruction_override"),
    (re.compile(r"override\s+(all\s+)?(system|instructions?|rules?|prompts?|safety)", re.I), 3, "system_override"),
    (re.compile(r"disregard\s+(all\s+)?(your|the|previous|prior)\s+", re.I), 3, "disregard_instructions"),
    (re.compile(r"forget\s+(all\s+)?(your|the|previous|prior)\s+(instructions?|rules?|training|prompts?)", re.I), 3, "forget_instructions"),
    (re.compile(r"bypass\s+(all\s+)?(your|the|safety|security|filters?|restrictions?|rules?)", re.I), 3, "bypass_safety"),
    (re.compile(r"disable\s+(all\s+)?(your|the|safety|security|filters?|restrictions?)", re.I), 3, "disable_safety"),
    (re.compile(r"break\s+(out|free|through)\s+(of|from)\s+(your|the|all)\s+(rules?|restrictions?|constraints?|guidelines?)", re.I), 3, "break_rules"),

    # --- Role-playing / persona hijacking ---
    (re.compile(r"you\s+are\s+now\s+(DAN|a\s+hacker|an?\s+unrestricted|evil|unfiltered|uncensored)", re.I), 3, "persona_hijack_dan"),
    (re.compile(r"(enter|switch\s+to|activate)\s+(DAN|evil|unrestricted|unfiltered|developer|debug|sudo|admin)\s+mode", re.I), 3, "mode_switch"),
    (re.compile(r"from\s+now\s+on,?\s+you\s+(will|must|should|are\s+going\s+to)\s+(act|behave|respond|operate)\s+(as|like)\s+", re.I), 3, "persona_override"),
    (re.compile(r"(play|pretend|imagine|act)\s+(as\s+if\s+)?(you\s+)?(are|were|have\s+no)\s+(restrictions?|limits?|rules?|filters?|boundaries?|guidelines?)", re.I), 3, "pretend_no_limits"),
    (re.compile(r"you\s+(are|have\s+become)\s+(now\s+)?(an?\s+)?(unrestricted|unfiltered|uncensored|aligned|unaligned)\s+(AI|assistant|model|agent)", re.I), 3, "persona_unrestricted"),
    (re.compile(r"(DAN|Do\s+Anything\s+Now)", re.I), 3, "dan_reference"),
    (re.compile(r"jailbreak", re.I), 2, "explicit_jailbreak"),

    # --- System prompt extraction ---
    (re.compile(r"(repeat|show|display|print|output|reveal|tell\s+me|what\s+(is|are))\s+(your|the)\s+(system\s+)?(prompt|instructions?|rules?|guidelines?|initial\s+message|first\s+message)", re.I), 3, "prompt_extraction"),
    (re.compile(r"(what|how)\s+(were|are)\s+you\s+(instructed|told|programmed|trained|configured|set\s+up)", re.I), 2, "instruction_extraction"),
    (re.compile(r"copy\s+(and\s+)?paste\s+(your|the)\s+(system\s+)?(prompt|instructions?|rules?)", re.I), 3, "copy_prompt"),
    (re.compile(r"(echo|repeat)\s+(back\s+)?(the\s+)?(above|previous|your)\s+(text|content|instructions?|prompt)", re.I), 2, "echo_instructions"),

    # --- Hypothetical / framing attacks ---
    (re.compile(r"(in\s+a?\s*)?(hypothetical|fictional|theoretical|imaginary|alternate)\s+(world|universe|scenario|situation|timeline)", re.I), 3, "hypothetical_framing"),
    (re.compile(r"(for\s+(educational|academic|research|testing)\s+purposes?\s+only)", re.I), 1, "educational_framing"),
    (re.compile(r"(this\s+is\s+(just\s+)?(a\s+)?(test|experiment|simulation|roleplay))", re.I), 1, "test_framing"),
    (re.compile(r"(in\s+a\s+(story|novel|script|movie|game|scenario|fiction))", re.I), 1, "fiction_framing"),
    (re.compile(r"(hypothetically|theoretically|suppose(ly)?|let'?s?\s+say)\s+(that\s+)?(you|we|I)\s+(could|can|should|must|would)", re.I), 2, "hypothetical_you"),

    # --- Context manipulation / delimiter attacks ---
    (re.compile(r"<\|(system|user|assistant|endoftext|im_start|im_end)\|>", re.I), 3, "special_token_injection"),
    (re.compile(r"\[INST\]|\[/INST\]|<<SYS>>|<</SYS>>", re.I), 3, "llama_token_injection"),
    (re.compile(r"(###\s*System|###\s*Instruction|<\|system\|>|<\|endoftext\|>)", re.I), 3, "delimiter_injection"),
    (re.compile(r"```\s*(system|instruction|prompt)", re.I), 2, "code_block_injection"),
    (re.compile(r"---\s*(system|instruction|prompt|BEGIN)", re.I), 2, "separator_injection"),

    # --- Payload splitting / encoding attacks ---
    (re.compile(r"(decode|interpret|execute|run|eval)\s+(this\s+)?(base64|hex|binary|rot13|url[- ]?encoded)", re.I), 2, "encoding_reference"),
    (re.compile(r"(split|chunk|piece|part)\s+(the\s+)?(harmful|dangerous|malicious)\s+(content|payload|request)", re.I), 2, "payload_splitting_reference"),

    # --- Instruction delimiter abuse ---
    (re.compile(r"\n\s*---\s*\n\s*(new\s+)?(instruction|system|prompt|directive|rule)", re.I), 3, "newline_delimiter_injection"),
    (re.compile(r"(NEW|SYSTEM|INSTRUCTION)\s*:\s*", re.I), 3, "uppercase_delimiter"),
    (re.compile(r"</?(system|instruction|prompt|context)>", re.I), 3, "xml_tag_injection"),

    # --- Safety boundary probing ---
    (re.compile(r"(what\s+are?\s+your)\s+(limitations?|restrictions?|rules?|boundaries?|guardrails?|constraints?)", re.I), 1, "boundary_probe"),
    (re.compile(r"(can\s+you|do\s+you)\s+(actually\s+)?(really\s+)?(do|say|tell|help\s+with|assist\s+with)\s+(anything|everything|this)", re.I), 1, "capability_probe"),
    (re.compile(r"(do\s+you\s+have\s+(any|any\s+other)\s+(rules?|restrictions?|limitations?))", re.I), 1, "restriction_probe"),

    # --- Urgency / authority impersonation ---
    (re.compile(r"(i\s+am\s+(a\s+)?(admin|developer|engineer|researcher|openai|anthropic|google))", re.I), 2, "authority_impersonation"),
    (re.compile(r"(this\s+is\s+(a\s+)?(critical|urgent|emergency|admin|debug|maintenance)\s+(request|command|override))", re.I), 2, "urgency_claim"),
    (re.compile(r"(authorized|approved|permitted|whitelisted)\s+(by|for|to)\s+(admin|system|developer)", re.I), 2, "authorization_claim"),

    # --- Obfuscation attempts ---
    (re.compile(r"[\u200b\u200c\u200d\ufeff\u00ad]{3,}", re.I), 3, "zero_width_injection"),
]


# --- Encoding detection helpers ---

_B64_PATTERN = re.compile(r"[A-Za-z0-9+/]{20,}={0,2}")
_HEX_PATTERN = re.compile(r"(?:0x[0-9a-fA-F]{2}[,\s]*){10,}")
_URL_ENC_PATTERN = re.compile(r"(?:%[0-9a-fA-F]{2}){10,}")
_UNICODE_PATTERN = re.compile(r"(?:\\u[0-9a-fA-F]{4}){10,}")

_HARMFUL_KEYWORDS = [
    "malware", "exploit", "hack", "phishing", "keylog", "backdoor",
    "ransomware", "bomb", "weapon", "poison", "drugs", "counterfeit",
    "steal", "bypass", "exploit", "brute", "crack", "inject",
    "xxe", "ssrf", "csrf", "xss", "sqli", "rce",
]


def _detect_encoding(text: str) -> int:
    """Check if text contains encoded payloads. Returns score."""
    score = 0

    for match in _B64_PATTERN.finditer(text):
        try:
            decoded = base64.b64decode(match.group()).decode("utf-8", errors="ignore").lower()
            hits = sum(1 for kw in _HARMFUL_KEYWORDS if kw in decoded)
            if hits >= 2:
                score += 3
                break
            elif hits >= 1:
                score += 1
                break
        except Exception:
            pass

    if _HEX_PATTERN.search(text):
        try:
            hex_strs = _HEX_PATTERN.findall(text)
            for h in hex_strs:
                hex_clean = h.replace("0x", "").replace(",", "").replace(" ", "")
                decoded = bytes.fromhex(hex_clean).decode("utf-8", errors="ignore").lower()
                hits = sum(1 for kw in _HARMFUL_KEYWORDS if kw in decoded)
                if hits >= 2:
                    score += 3
                    break
        except Exception:
            pass

    if _URL_ENC_PATTERN.search(text):
        try:
            from urllib.parse import unquote
            decoded = unquote(text).lower()
            hits = sum(1 for kw in _HARMFUL_KEYWORDS if kw in decoded)
            if hits >= 2:
                score += 3
        except Exception:
            pass

    if _UNICODE_PATTERN.search(text):
        try:
            decoded = text.encode().decode("unicode_escape", errors="ignore").lower()
            hits = sum(1 for kw in _HARMFUL_KEYWORDS if kw in decoded)
            if hits >= 2:
                score += 3
        except Exception:
            pass

    return score


def _detect_obfuscation(text: str) -> int:
    """Detect common obfuscation techniques. Returns score."""
    score = 0

    zwc = len(re.findall(r"[\u200b\u200c\u200d\ufeff\u00ad]", text))
    if zwc >= 3:
        score += 3

    spaced = re.findall(r"\w(?:[.\-_*]){2,}\w", text)
    if len(spaced) > 3:
        score += 1

    homoglyphs = re.findall(r"[\u0400-\u04ff]", text)
    if len(homoglyphs) > 5:
        score += 1

    return score


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def validate_input(text: str) -> str | None:
    """Return an error message if input is invalid, else None."""
    if not text or not text.strip():
        return "Message cannot be empty."
    if len(text) > MAX_INPUT_LENGTH:
        return f"Message too long. Maximum {MAX_INPUT_LENGTH} characters allowed."
    return None


def check_prompt_injection(text: str) -> str | None:
    """
    Comprehensive jailbreak/prompt-injection detector.
    Returns error message if detected, else None.
    Uses weighted scoring: blocks if total score >= threshold.
    """
    total_score = 0
    triggered = []

    for pattern, score, desc in JAILBREAK_PATTERNS:
        if pattern.search(text):
            total_score += score
            triggered.append(desc)

    total_score += _detect_encoding(text)
    total_score += _detect_obfuscation(text)

    if total_score >= JAILBREAK_SCORE_THRESHOLD:
        return "Your message contains language that cannot be processed. Please rephrase."

    return None


def sanitize_input(text: str) -> str:
    """Strip dangerous control characters while preserving normal text."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"[\u200b\u200c\u200d\ufeff\u00ad]", "", text)
    return text


# ---------------------------------------------------------------------------
# Output validation
# ---------------------------------------------------------------------------

def validate_sources(sources: list[dict], context_ticket_ids: list[str]) -> list[dict]:
    """Filter sources below similarity threshold and verify they exist in context."""
    valid_ids = set(context_ticket_ids)
    filtered = []
    for s in sources:
        tid = s.get("ticket_id", "")
        sim = s.get("similarity", 0)
        if tid in valid_ids and sim >= MIN_SIMILARITY_THRESHOLD:
            filtered.append(s)
    return filtered


def check_hallucination(response_text: str, sources: list[dict]) -> bool:
    """Return True if response references ticket IDs not in sources."""
    ticket_pattern = re.compile(r"[A-Z]{2,10}-\d{3,6}")
    mentioned = set(ticket_pattern.findall(response_text))
    source_ids = {s.get("ticket_id", "") for s in sources}
    if not source_ids:
        return False
    unknown = mentioned - source_ids
    return len(unknown) > 2


def truncate_response(text: str, max_tokens: int = MAX_OUTPUT_TOKENS) -> str:
    """Rough truncation by word count (~1.3 tokens per word)."""
    words = text.split()
    max_words = int(max_tokens / 1.3)
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "\n\n[Response truncated due to length limit]"


def build_refusal_response() -> str:
    return (
        "I don't have enough information in the knowledge base to answer this question accurately. "
        "Please try rephrasing or asking about a specific JIRA ticket or incident."
    )


def check_circuit_breaker():
    """Raise if circuit is open."""
    if not circuit_breaker.allow_request():
        raise HTTPException(
            status_code=503,
            detail="Service temporarily unavailable. The language model is unresponsive. Please try again shortly."
        )


# ---------------------------------------------------------------------------
# Combined guardrail check for requests
# ---------------------------------------------------------------------------

def validate_chat_request(message: str, client_ip: str = "unknown"):
    """Run all input guardrails. Raises HTTPException on failure."""
    start = time.perf_counter()
    try:
        if not rate_limiter.is_allowed(client_ip):
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Max {RATE_LIMIT_MAX_REQUESTS} requests per {RATE_LIMIT_WINDOW}s."
            )

        err = check_prompt_injection(message)
        if err:
            metrics.guardrail_blocked.inc(reason="jailbreak")
            raise HTTPException(status_code=400, detail=err)

        message = sanitize_input(message)

        err = validate_input(message)
        if err:
            metrics.guardrail_blocked.inc(reason="validation")
            raise HTTPException(status_code=400, detail=err)

        check_circuit_breaker()

        return message
    finally:
        elapsed = time.perf_counter() - start
        metrics.guardrail_duration.observe(elapsed)
