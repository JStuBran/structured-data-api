"""
Structured Data API — x402 FastAPI Service
Extract structured JSON from any text or URL using GPT-4o-mini via OpenRouter.
"""

import json
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import OpenAI
from pydantic import BaseModel, Field, validator

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PAYMENT_REQUIRED = os.getenv("PAYMENT_REQUIRED", "true").lower() == "true"
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "0x0000000000000000000000000000000000000000")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
VERSION = "1.0.0"
MAX_INPUT_CHARS = 20_000
PRICE_USDC_UNITS = "100000"  # $0.10 in USDC (6 decimals)
MODEL = "openai/gpt-4o-mini"
SERVICE_URL = os.getenv("SERVICE_URL", "https://structured-data-api.up.railway.app")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("structured-data-api")

# ---------------------------------------------------------------------------
# OpenRouter client (OpenAI-compatible)
# ---------------------------------------------------------------------------

openrouter_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ExtractRequest(BaseModel):
    url: Optional[str] = Field(default=None, description="URL to fetch and extract from")
    text: Optional[str] = Field(default=None, description="Raw text to extract from")
    schema: Dict[str, Any] = Field(..., description="JSON Schema describing the output structure")

    @validator("text")
    def check_text_length(cls, v):
        if v and len(v) > MAX_INPUT_CHARS:
            raise ValueError(f"text must be {MAX_INPUT_CHARS} characters or fewer.")
        return v

    @validator("url")
    def check_url_or_text(cls, v, values):
        if v is None and values.get("text") is None:
            raise ValueError("Either 'url' or 'text' must be provided.")
        return v


class ExtractMeta(BaseModel):
    model: str
    input_chars: int
    processing_time_ms: float
    source: str  # "url" | "text"


class ExtractResponse(BaseModel):
    data: Dict[str, Any]
    meta: ExtractMeta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def fetch_url_text(url: str) -> str:
    """Fetch a URL and return clean readable text."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; StructuredDataAPI/1.0; +https://github.com/JStuBran/structured-data-api)"
        )
    }
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = client.get  # ensure async usage below
        response = await client.get(url, headers=headers)
        response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    raw = response.text

    if "html" in content_type:
        soup = BeautifulSoup(raw, "html.parser")
        # Remove scripts, styles, nav, footer
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        # Try to find main content
        main = soup.find("main") or soup.find("article") or soup.find("body") or soup
        text = main.get_text(separator="\n", strip=True)
    else:
        text = raw

    # Truncate to limit
    return text[:MAX_INPUT_CHARS]


def extract_with_llm(text: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    """Call OpenRouter GPT-4o-mini to extract data matching schema from text."""
    schema_str = json.dumps(schema, indent=2)

    system_prompt = (
        "You are a precise data extraction assistant. "
        "Extract information from the provided text and return ONLY valid JSON "
        "that matches the given JSON Schema. Do not include markdown, code fences, "
        "or any explanation — just the raw JSON object."
    )

    user_prompt = (
        f"JSON Schema to match:\n{schema_str}\n\n"
        f"Text to extract from:\n{text}\n\n"
        "Return only the JSON object conforming to the schema."
    )

    response = openrouter_client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
        max_tokens=2048,
    )

    raw_output = response.choices[0].message.content.strip()

    # Strip accidental markdown fences
    if raw_output.startswith("```"):
        lines = raw_output.splitlines()
        lines = [l for l in lines if not l.startswith("```")]
        raw_output = "\n".join(lines).strip()

    return json.loads(raw_output)


# ---------------------------------------------------------------------------
# x402 payment middleware
# ---------------------------------------------------------------------------

X402_RESPONSE = {
    "x402Version": 1,
    "accepts": [
        {
            "scheme": "exact",
            "network": "eip155:8453",
            "maxAmountRequired": PRICE_USDC_UNITS,
            "resource": f"{SERVICE_URL}/api/extract",
            "description": "Structured data extraction ($0.10)",
            "mimeType": "application/json",
            "payTo": WALLET_ADDRESS,
            "maxTimeoutSeconds": 300,
            "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "extra": {"name": "USDC", "decimals": 6},
        }
    ],
}


async def payment_middleware(request: Request, call_next):
    if PAYMENT_REQUIRED and request.url.path.startswith("/api/"):
        payment_header = request.headers.get("X-Payment")
        if not payment_header:
            logger.info("402 — missing X-Payment header for %s", request.url.path)
            return JSONResponse(status_code=402, content=X402_RESPONSE)
    return await call_next(request)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Structured Data API",
    description=(
        "x402 extraction API for AI agents — "
        "turn any text or URL into structured JSON using GPT-4o-mini via OpenRouter."
    ),
    version=VERSION,
)

app.middleware("http")(payment_middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok", "version": VERSION}


@app.post("/api/extract", response_model=ExtractResponse)
async def extract(body: ExtractRequest):
    start = time.time()

    # Determine source
    if body.url:
        source = "url"
        logger.info("extract — fetching URL: %s", body.url)
        try:
            content = await fetch_url_text(body.url)
        except httpx.HTTPStatusError as e:
            return JSONResponse(
                status_code=422,
                content={"error": f"Failed to fetch URL: HTTP {e.response.status_code}"},
            )
        except Exception as e:
            return JSONResponse(
                status_code=422,
                content={"error": f"Failed to fetch URL: {str(e)}"},
            )
    else:
        source = "text"
        content = body.text

    input_chars = len(content)
    logger.info("extract — source=%s chars=%d", source, input_chars)

    # LLM extraction
    try:
        extracted = extract_with_llm(content, body.schema)
    except json.JSONDecodeError as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"LLM returned invalid JSON: {str(e)}"},
        )
    except Exception as e:
        logger.exception("extract — LLM error")
        return JSONResponse(
            status_code=500,
            content={"error": f"Extraction failed: {str(e)}"},
        )

    elapsed_ms = (time.time() - start) * 1000
    logger.info("extract — done in %.1f ms", elapsed_ms)

    return ExtractResponse(
        data=extracted,
        meta=ExtractMeta(
            model=MODEL,
            input_chars=input_chars,
            processing_time_ms=round(elapsed_ms, 2),
            source=source,
        ),
    )


if __name__ == "__main__":
    import uvicorn
    import os
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
