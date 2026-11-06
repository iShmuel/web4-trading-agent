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
            if result:
                _LLM_CACHE[cache_key] = (time.time(), result)
            return result
    except (urllib.error.URLError, TimeoutError, Exception) as e:
        logger.debug(f"[LLMClient] Local Ollama call notice ({e}). Using deterministic fallback.")
        return None


def clear_llm_cache():
    """Clear in-memory inference cache."""
    global _LLM_CACHE
    _LLM_CACHE.clear()
