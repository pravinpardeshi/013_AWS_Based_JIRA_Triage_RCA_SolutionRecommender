import json
import hashlib
import time
import boto3
from botocore.config import Config
from config import (
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION,
    BEDROCK_REGION, BEDROCK_CHAT_MODEL, BEDROCK_EMBEDDING_MODEL,
)
from metrics import metrics
from dynamodb_cache import embedding_cache, chat_cache

BEDROCK_REQUEST_TIMEOUT = 120

_bedrock_runtime = None


def _get_bedrock_runtime():
    global _bedrock_runtime
    if _bedrock_runtime is None:
        session = boto3.Session(
            aws_access_key_id=AWS_ACCESS_KEY_ID or None,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY or None,
            region_name=BEDROCK_REGION or AWS_DEFAULT_REGION,
        )
        _bedrock_runtime = session.client(
            "bedrock-runtime",
            config=Config(
                read_timeout=BEDROCK_REQUEST_TIMEOUT,
                connect_timeout=10,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
    return _bedrock_runtime


class ChatResult:
    __slots__ = ("content", "prompt_tokens", "completion_tokens", "total_tokens", "duration_ms", "cached")

    def __init__(self, content: str, prompt_tokens: int = 0, completion_tokens: int = 0,
                 total_tokens: int = 0, duration_ms: float = 0, cached: bool = False):
        self.content = content
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        self.duration_ms = duration_ms
        self.cached = cached


class EmbeddingResult:
    __slots__ = ("embedding", "duration_ms")

    def __init__(self, embedding: list, duration_ms: float = 0):
        self.embedding = embedding
        self.duration_ms = duration_ms


def _hash_key(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class BedrockError(Exception):
    pass


class BedrockTimeoutError(BedrockError):
    pass


class BedrockUnavailableError(BedrockError):
    pass


async def get_embedding(text: str, use_cache: bool = True) -> EmbeddingResult:
    key = _hash_key(text)

    # Check DynamoDB cache (L1 local + DynamoDB)
    if use_cache:
        cached = embedding_cache.get(key)
        if cached:
            return EmbeddingResult(
                embedding=cached["embedding"],
                duration_ms=cached.get("duration_ms", 0),
            )

    metrics.record_cache_miss()
    start = time.perf_counter()

    try:
        runtime = _get_bedrock_runtime()
        body = json.dumps({
            "texts": [text],
            "input_type": "search_document",
        })
        response = runtime.invoke_model(
            modelId=BEDROCK_EMBEDDING_MODEL,
            contentType="application/json",
            accept="application/json",
            body=body,
        )
        result_body = json.loads(response["body"].read())
        embedding = result_body["embeddings"][0]
    except Exception as e:
        raise BedrockError(f"Bedrock embedding failed: {str(e)}")

    elapsed = (time.perf_counter() - start) * 1000
    result = EmbeddingResult(embedding, elapsed)

    # Store in DynamoDB cache
    if use_cache:
        embedding_cache.put(key, {
            "embedding": embedding,
            "duration_ms": elapsed,
        })

    metrics.embedding_duration.observe(elapsed / 1000, model=BEDROCK_EMBEDDING_MODEL)
    metrics.embedding_requests.inc(model=BEDROCK_EMBEDDING_MODEL)

    return result


def _claude_messages_to_body(messages: list[dict], temperature: float = 0.3,
                             max_tokens: int = 4096) -> dict:
    system_msg = ""
    bedrock_messages = []

    for msg in messages:
        if msg["role"] == "system":
            system_msg = msg["content"]
        else:
            bedrock_messages.append({"role": msg["role"], "content": msg["content"]})

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": bedrock_messages,
    }
    if system_msg:
        body["system"] = system_msg

    return body


async def chat_completion(messages: list[dict], temperature: float = 0.3,
                          use_cache: bool = True) -> ChatResult:
    from guardrails import circuit_breaker

    cache_key = _hash_key(json.dumps(messages, sort_keys=True), temperature)

    # Check DynamoDB cache
    if use_cache:
        cached = chat_cache.get(cache_key)
        if cached:
            result = ChatResult(
                content=cached["content"],
                prompt_tokens=cached.get("prompt_tokens", 0),
                completion_tokens=cached.get("completion_tokens", 0),
                total_tokens=cached.get("total_tokens", 0),
                duration_ms=cached.get("duration_ms", 0),
                cached=True,
            )
            return result

    if not circuit_breaker.allow_request():
        raise BedrockUnavailableError("Circuit breaker is open — Bedrock is unresponsive")

    metrics.record_cache_miss()
    start = time.perf_counter()

    try:
        runtime = _get_bedrock_runtime()
        body = _claude_messages_to_body(messages, temperature)
        response = runtime.invoke_model(
            modelId=BEDROCK_CHAT_MODEL,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
        result_body = json.loads(response["body"].read())
        circuit_breaker.record_success()
    except Exception as e:
        circuit_breaker.record_failure()
        raise BedrockError(f"Bedrock chat failed: {str(e)}")

    elapsed = (time.perf_counter() - start) * 1000

    content = ""
    for block in result_body.get("content", []):
        if block.get("type") == "text":
            content += block.get("text", "")

    usage = result_body.get("usage", {})
    prompt_tokens = usage.get("input_tokens", 0)
    completion_tokens = usage.get("output_tokens", 0)
    total_tokens = prompt_tokens + completion_tokens

    result = ChatResult(
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        duration_ms=round(elapsed, 1),
    )

    # Store in DynamoDB cache
    if use_cache:
        chat_cache.put(cache_key, {
            "content": content,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "duration_ms": round(elapsed, 1),
        })

    metrics.llm_duration.observe(elapsed / 1000, operation="chat", model=BEDROCK_CHAT_MODEL)
    metrics.llm_requests.inc(operation="chat", model=BEDROCK_CHAT_MODEL)
    metrics.llm_tokens.inc(value=prompt_tokens, model=BEDROCK_CHAT_MODEL, type="prompt")
    metrics.llm_tokens.inc(value=completion_tokens, model=BEDROCK_CHAT_MODEL, type="completion")

    return result


async def chat_completion_stream(messages: list[dict], temperature: float = 0.3):
    from guardrails import circuit_breaker

    if not circuit_breaker.allow_request():
        raise BedrockUnavailableError("Circuit breaker is open — Bedrock is unresponsive")

    try:
        runtime = _get_bedrock_runtime()
        body = _claude_messages_to_body(messages, temperature)
        body["stream"] = True

        response = runtime.invoke_model_with_response_stream(
            modelId=BEDROCK_CHAT_MODEL,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
        circuit_breaker.record_success()

        for event in response["body"]:
            chunk = json.loads(event["bytes"])
            if chunk.get("type") == "content_block_delta":
                delta = chunk.get("delta", {})
                if delta.get("type") == "text_delta":
                    yield delta.get("text", "")
    except Exception as e:
        circuit_breaker.record_failure()
        raise BedrockError(f"Bedrock stream failed: {str(e)}")


async def chat_completion_stream_sse(messages: list[dict], temperature: float = 0.3):
    from guardrails import circuit_breaker

    start = time.perf_counter()
    full_content = ""
    prompt_tokens = 0
    completion_tokens = 0

    if not circuit_breaker.allow_request():
        yield f"event: error\ndata: {json.dumps({'detail': 'Circuit breaker is open — Bedrock is unresponsive'})}\n\n"
        return

    try:
        runtime = _get_bedrock_runtime()
        body = _claude_messages_to_body(messages, temperature)
        body["stream"] = True

        response = runtime.invoke_model_with_response_stream(
            modelId=BEDROCK_CHAT_MODEL,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
        circuit_breaker.record_success()

        for event in response["body"]:
            chunk = json.loads(event["bytes"])
            chunk_type = chunk.get("type")

            if chunk_type == "content_block_delta":
                delta = chunk.get("delta", {})
                if delta.get("type") == "text_delta":
                    token = delta.get("text", "")
                    if token:
                        full_content += token
                        yield f"event: token\ndata: {json.dumps({'content': token})}\n\n"

            elif chunk_type == "message_delta":
                usage = chunk.get("usage", {})
                completion_tokens = usage.get("output_tokens", 0)

            elif chunk_type == "message_start":
                usage = chunk.get("message", {}).get("usage", {})
                prompt_tokens = usage.get("input_tokens", 0)

        elapsed = round((time.perf_counter() - start) * 1000, 1)
        total_tokens = prompt_tokens + completion_tokens

        metrics.llm_duration.observe(elapsed / 1000, operation="chat_stream", model=BEDROCK_CHAT_MODEL)
        metrics.llm_requests.inc(operation="chat_stream", model=BEDROCK_CHAT_MODEL)
        metrics.llm_tokens.inc(value=prompt_tokens, model=BEDROCK_CHAT_MODEL, type="prompt")
        metrics.llm_tokens.inc(value=completion_tokens, model=BEDROCK_CHAT_MODEL, type="completion")

        done_payload = {
            "total_ms": elapsed,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"

    except BedrockError:
        raise
    except Exception as e:
        circuit_breaker.record_failure()
        yield f"event: error\ndata: {json.dumps({'detail': str(e)})}\n\n"


def cache_chat_result(key: str, content: str, cache_metrics: dict):
    """Store a streaming result in DynamoDB cache for future use."""
    chat_cache.put(key, {
        "content": content,
        "prompt_tokens": cache_metrics.get("prompt_tokens", 0),
        "completion_tokens": cache_metrics.get("completion_tokens", 0),
        "total_tokens": cache_metrics.get("total_tokens", 0),
        "duration_ms": cache_metrics.get("total_ms", 0),
    })


def get_cached_chat(messages: list[dict], temperature: float) -> ChatResult | None:
    cache_key = _hash_key(json.dumps(messages, sort_keys=True), temperature)
    cached = chat_cache.get(cache_key)
    if cached:
        return ChatResult(
            content=cached["content"],
            prompt_tokens=cached.get("prompt_tokens", 0),
            completion_tokens=cached.get("completion_tokens", 0),
            total_tokens=cached.get("total_tokens", 0),
            duration_ms=cached.get("duration_ms", 0),
            cached=True,
        )
    return None


def cache_stats() -> dict:
    return {
        "embedding_cache_size": embedding_cache._size(),
        "chat_cache_size": chat_cache._size(),
    }
