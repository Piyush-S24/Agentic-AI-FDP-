"""
utils.py
========
Groq SDK wrappers, model routing, and resume text utilities for CareerForge.

Design goals
------------
* A *single* place that talks to the LLM provider, so retries, JSON parsing,
  and error handling live in one auditable spot.
* Model routing constants so the pipeline can use a large model for deep
  reasoning (Steps 1 & 4) and a fast model for interactive rounds (Steps 2 & 3).
* No secrets in source: the API key is read from the ``GROQ_API_KEY``
  environment variable.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

# Load a local .env (walking up from the CWD) so keys are picked up
# automatically whether you run from the repo root or 02_career_forge_agent/.
try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
except ImportError:  # python-dotenv optional; env vars may be set another way
    pass

try:
    from groq import Groq, GroqError
except ImportError as exc:  # pragma: no cover - dependency guard
    raise ImportError(
        "The 'groq' package is required. Install it with `pip install groq`."
    ) from exc

# OpenRouter is OpenAI-compatible; the optional `openai` SDK talks to it.
try:
    from openai import OpenAI, OpenAIError
except ImportError:  # OpenRouter support is optional
    OpenAI = None
    OpenAIError = Exception


# --------------------------------------------------------------------------- #
# Model routing
# --------------------------------------------------------------------------- #
# Deep parsing / synthesis (Step 1 & Step 4): favor reasoning quality.
MODEL_DEEP = os.getenv("CAREERFORGE_MODEL_DEEP", "llama-3.3-70b-versatile")
# Fast interactive rounds (Step 2 & Step 3): favor latency.
MODEL_FAST = os.getenv("CAREERFORGE_MODEL_FAST", "llama-3.1-8b-instant")

# OpenRouter — an independent, optional provider (its own key + base URL).
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MODEL_OPENROUTER = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct")


# --------------------------------------------------------------------------- #
# Custom exceptions
# --------------------------------------------------------------------------- #
class CareerForgeError(Exception):
    """Base class for all application-level errors."""


class LLMConfigError(CareerForgeError):
    """Raised when the LLM client cannot be configured (e.g. missing key)."""


class LLMInvocationError(CareerForgeError):
    """Raised when a completion call fails at the provider."""


class LLMParseError(CareerForgeError):
    """Raised when the model output cannot be parsed into the expected schema."""


class ResumeParseError(CareerForgeError):
    """Raised when an uploaded resume (e.g. PDF) cannot be read/extracted."""


# --------------------------------------------------------------------------- #
# Client factory
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def get_groq_client() -> Groq:
    """Return a cached Groq client.

    Raises
    ------
    LLMConfigError
        If ``GROQ_API_KEY`` is not present in the environment.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise LLMConfigError(
            "GROQ_API_KEY environment variable is not set. "
            "Create a .env file (see .env.example) or export it in your shell."
        )
    return Groq(api_key=api_key)


@lru_cache(maxsize=1)
def get_openrouter_client() -> "OpenAI":
    """Return a cached OpenRouter client (OpenAI-compatible).

    Kept entirely separate from the Groq client: its own key
    (``OPENROUTER_API_KEY``), its own base URL, its own call method. This lets
    you route specific tasks to OpenRouter without disturbing the Groq path.

    Raises
    ------
    LLMConfigError
        If the ``openai`` SDK is missing or ``OPENROUTER_API_KEY`` is not set.
    """
    if OpenAI is None:
        raise LLMConfigError(
            "The 'openai' package is required for OpenRouter. "
            "Install it with `pip install openai`."
        )
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise LLMConfigError(
            "OPENROUTER_API_KEY environment variable is not set. "
            "Add it to your .env file (see .env.example)."
        )
    return OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)


# --------------------------------------------------------------------------- #
# Core completion helpers
# --------------------------------------------------------------------------- #
def chat_completion(
    messages: List[Dict[str, str]],
    model: str,
    *,
    temperature: float = 0.3,
    json_mode: bool = False,
    max_tokens: int = 2048,
) -> str:
    """Thin wrapper around Groq chat completions.

    Parameters
    ----------
    messages:
        OpenAI-style message list.
    model:
        A Groq model id (use ``MODEL_DEEP`` or ``MODEL_FAST``).
    json_mode:
        When ``True``, requests strict JSON via ``response_format``.

    Returns
    -------
    str
        The raw assistant message content.
    """
    client = get_groq_client()
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        completion = client.chat.completions.create(**kwargs)
    except GroqError as exc:  # network / auth / rate limit
        raise LLMInvocationError(f"Groq completion failed: {exc}") from exc

    content = completion.choices[0].message.content
    if content is None:
        raise LLMInvocationError("Groq returned an empty completion.")
    return content


# Explicit alias so call sites can read as "the Groq path" when both providers
# are in use side by side.
groq_chat_completion = chat_completion


def openrouter_chat_completion(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    *,
    temperature: float = 0.3,
    json_mode: bool = False,
    max_tokens: int = 2048,
) -> str:
    """Independent chat-completion call routed through OpenRouter.

    Mirrors the signature of :func:`chat_completion` (the Groq path) so the two
    providers are drop-in interchangeable, but uses the OpenRouter client and a
    separate model namespace. ``model`` defaults to ``MODEL_OPENROUTER``.
    """
    client = get_openrouter_client()
    kwargs: Dict[str, Any] = {
        "model": model or MODEL_OPENROUTER,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        # Optional attribution headers OpenRouter recommends (safe to omit).
        "extra_headers": {
            "HTTP-Referer": os.getenv("OPENROUTER_APP_URL", "http://localhost"),
            "X-Title": "CareerForge",
        },
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        completion = client.chat.completions.create(**kwargs)
    except OpenAIError as exc:  # network / auth / rate limit
        raise LLMInvocationError(f"OpenRouter completion failed: {exc}") from exc

    content = completion.choices[0].message.content
    if content is None:
        raise LLMInvocationError("OpenRouter returned an empty completion.")
    return content


T = TypeVar("T", bound=BaseModel)


def structured_completion(
    system_prompt: str,
    user_prompt: str,
    schema: Type[T],
    *,
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    retries: int = 2,
) -> T:
    """Run a JSON-mode completion and validate it against a Pydantic schema.

    The schema's JSON structure is injected into the system prompt so the model
    knows the exact shape to emit. Output is parsed defensively and validated.

    Smaller/faster models occasionally emit JSON that does not match the schema.
    To keep the pipeline robust we retry up to ``retries`` extra times; on a bad
    response the offending output is fed back with a correction instruction so
    the model can self-heal.

    Raises
    ------
    LLMParseError
        If, after all attempts, the response cannot be parsed/validated.
    """
    schema_hint = json.dumps(schema.model_json_schema(), indent=2)
    system = (
        f"{system_prompt}\n\n"
        "You MUST respond with a single JSON object that conforms to this "
        f"JSON Schema:\n{schema_hint}\n"
        "Do not wrap the JSON in markdown fences. Do not add commentary."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ]

    last_error: Optional[Exception] = None
    for _attempt in range(retries + 1):
        raw = chat_completion(
            messages,
            model=model,
            temperature=temperature,
            json_mode=True,
            max_tokens=max_tokens,
        )
        try:
            payload = _extract_json(raw)
            return schema.model_validate(payload)
        except (LLMParseError, ValidationError) as exc:
            last_error = exc
            # Feed the model its own bad output plus the error, and ask again.
            messages = messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "That response was not valid for the required schema: "
                        f"{exc}. Reply again with ONLY a corrected JSON object "
                        "that strictly matches the schema."
                    ),
                },
            ]

    raise LLMParseError(
        f"Model output did not match {schema.__name__} after "
        f"{retries + 1} attempts: {last_error}"
    )


def _extract_json(raw: str) -> Dict[str, Any]:
    """Parse a JSON object from raw model text, tolerating stray fences."""
    text = raw.strip()
    # Strip ```json ... ``` fences if the model added them despite instructions.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last resort: grab the outermost { ... } span.
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise LLMParseError(f"Could not parse JSON from model: {exc}") from exc
        raise LLMParseError("Model response contained no JSON object.")


# --------------------------------------------------------------------------- #
# Resume utilities
# --------------------------------------------------------------------------- #
def extract_text_from_pdf(data: bytes) -> str:
    """Extract plain text from an uploaded PDF's bytes.

    Uses ``pypdf`` (pure-Python, no system deps). Note that scanned/image-only
    PDFs contain no text layer and will raise ``ResumeParseError`` — those would
    need OCR (e.g. Tesseract), which is out of scope here.

    Raises
    ------
    ResumeParseError
        If the bytes are not a readable PDF or contain no extractable text.
    """
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise LLMConfigError(
            "The 'pypdf' package is required for PDF upload. "
            "Install it with `pip install pypdf`."
        ) from exc

    import io

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, Exception) as exc:  # noqa: BLE001 - surface as domain error
        raise ResumeParseError(f"Could not read the PDF: {exc}") from exc

    text = "\n".join(pages).strip()
    if not text:
        raise ResumeParseError(
            "No text could be extracted from this PDF. It may be a scanned "
            "image; please upload a text-based PDF."
        )
    return clean_resume_text(text)


def clean_resume_text(raw: str) -> str:
    """Normalize whitespace and strip control characters from resume text.

    In a real system you would first extract text from PDF/DOCX (e.g. with
    ``pdfplumber`` / ``python-docx``); here we assume plain text is provided and
    focus on normalization so the LLM sees clean, token-efficient input.
    """
    if not raw or not raw.strip():
        raise CareerForgeError("Resume text is empty.")
    # Collapse runs of whitespace, keep paragraph breaks.
    text = re.sub(r"[ \t]+", " ", raw)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "".join(ch for ch in text if ch == "\n" or ch >= " ")
    return text.strip()


def truncate_for_context(text: str, max_chars: int = 12000) -> str:
    """Guard against oversized inputs blowing the context window."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"
