"""Local LLM Client for Strategic & Forensic Agent Reasoning.

Connects to local Ollama (phi4-mini / qwen3 / gemma2) with LRU query caching,
resource-conserving token bounds, and graceful deterministic fallback.
"""

import hashlib
import json
import logging
import time
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "phi4-mini:latest"

# In-memory query cache to eliminate redundant GPU/CPU forward passes
# Key: SHA256(model + system_prompt + prompt), Value: (timestamp, response_text)
_LLM_CACHE: Dict[str, Tuple[float, str]] = {}
CACHE_TTL_SECONDS = 3600.0  # 1 hour cache TTL for identical queries
MAX_CACHE_ENTRIES = 256  # Bounded LRU capacity to prevent unbounded memory growth


def _compute_cache_key(model: str, prompt: str, system_prompt: Optional[str]) -> str:
    content = f"{model}::{system_prompt or ''}::{prompt}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def query_local_llm(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 200,  # Clamped to 200 tokens to conserve CPU/GPU resources
    timeout_seconds: float = 8.0,
    endpoint: str = DEFAULT_OLLAMA_URL,
    use_cache: bool = True,
) -> Optional[str]:
    """
    Query local Ollama instance with LRU caching, bounded tokens, and deterministic fallback.
    Prevents unnecessary resource consumption by returning cached results when prompts repeat.
    """
    cache_key = _compute_cache_key(model, prompt, system_prompt)

    # Check cache first (0ms latency, zero GPU/CPU cycles)
    if use_cache and cache_key in _LLM_CACHE:
        ts, cached_resp = _LLM_CACHE[cache_key]
        if (time.time() - ts) < CACHE_TTL_SECONDS:
            logger.debug("[LLMClient] Returning cached inference (0ms latency).")
            return cached_resp

    full_prompt = f"System: {system_prompt}\n\nUser: {prompt}" if system_prompt else prompt

    payload = {
        "model": model,
        "prompt": full_prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "top_p": 0.9,
        },
    }

    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result = data.get("response", "").strip()
            if result and use_cache:
                now_ts = time.time()
                expired = [k for k, (t_val, _) in _LLM_CACHE.items() if (now_ts - t_val) >= CACHE_TTL_SECONDS]
                for k in expired:
                    _LLM_CACHE.pop(k, None)
                while len(_LLM_CACHE) >= MAX_CACHE_ENTRIES:
                    oldest_key = min(_LLM_CACHE, key=lambda k: _LLM_CACHE[k][0])
                    _LLM_CACHE.pop(oldest_key, None)
                _LLM_CACHE[cache_key] = (now_ts, result)
            return result
    except (urllib.error.URLError, TimeoutError, Exception) as e:
        logger.debug(f"[LLMClient] Local Ollama call notice ({e}). Using deterministic fallback.")
        return None


def query_local_llm_with_metrics(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 350,
    timeout_seconds: float = 12.0,
    endpoint: str = DEFAULT_OLLAMA_URL,
    use_cache: bool = True,
    json_mode: bool = False,
) -> Dict[str, Any]:
    """Query local Ollama instance and return response text plus latency and token metrics."""
    cache_key = _compute_cache_key(f"{model}::json={json_mode}", prompt, system_prompt)
    t0 = time.perf_counter()

    if use_cache and cache_key in _LLM_CACHE:
        ts, cached_resp = _LLM_CACHE[cache_key]
        if (time.time() - ts) < CACHE_TTL_SECONDS:
            est_prompt = max(1, len((system_prompt or "") + prompt) // 4)
            est_comp = max(1, len(cached_resp) // 4)
            return {
                "response": cached_resp,
                "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                "prompt_tokens": est_prompt,
                "completion_tokens": est_comp,
                "total_tokens": est_prompt + est_comp,
                "cached": True,
                "success": True,
                "error": None,
            }

    full_prompt = f"System: {system_prompt}\n\nUser: {prompt}" if system_prompt else prompt
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": full_prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "top_p": 0.9,
        },
    }
    if json_mode:
        payload["format"] = "json"

    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            t1 = time.perf_counter()
            result = data.get("response", "").strip()
            p_tok = int(data.get("prompt_eval_count") or max(1, len(full_prompt) // 4))
            c_tok = int(data.get("eval_count") or max(1, len(result) // 4))
            if result and use_cache:
                now_ts = time.time()
                expired = [k for k, (t_val, _) in _LLM_CACHE.items() if (now_ts - t_val) >= CACHE_TTL_SECONDS]
                for k in expired:
                    _LLM_CACHE.pop(k, None)
                while len(_LLM_CACHE) >= MAX_CACHE_ENTRIES:
                    oldest_key = min(_LLM_CACHE, key=lambda k: _LLM_CACHE[k][0])
                    _LLM_CACHE.pop(oldest_key, None)
                _LLM_CACHE[cache_key] = (now_ts, result)
            return {
                "response": result if result else None,
                "latency_ms": round((t1 - t0) * 1000.0, 2),
                "prompt_tokens": p_tok,
                "completion_tokens": c_tok,
                "total_tokens": p_tok + c_tok,
                "cached": False,
                "success": bool(result),
                "error": None if result else "Empty LLM response",
            }
    except (urllib.error.URLError, TimeoutError, Exception) as e:
        t1 = time.perf_counter()
        logger.debug(f"[LLMClient] Local Ollama call notice ({e}).")
        return {
            "response": None,
            "latency_ms": round((t1 - t0) * 1000.0, 2),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached": False,
            "success": False,
            "error": str(e),
        }


def clear_llm_cache():
    """Clear in-memory inference cache."""
    global _LLM_CACHE
    _LLM_CACHE.clear()
