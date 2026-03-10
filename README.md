# Structured Data API

An x402-gated FastAPI service that extracts structured JSON from any text or URL using GPT-4o-mini via OpenRouter.

## How it works

1. Send a `POST /api/extract` with either a `url` or `text` + a `schema` (JSON Schema dict).
2. If a URL is provided, it fetches and cleans the page content first.
3. GPT-4o-mini extracts the data into your requested schema.
4. Returns structured JSON.

**Price:** $0.10 per extraction (100,000 USDC units on Base).

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in OPENROUTER_API_KEY and WALLET_ADDRESS
uvicorn main:app --reload
```

## API

### `GET /health`
Returns `{ "status": "ok", "version": "1.0.0" }`.

### `POST /api/extract`

**Headers:**
- `X-Payment: <x402-payment-token>` (required in production)
- `Content-Type: application/json`

**Body:**
```json
{
  "url": "https://example.com/article",
  "schema": {
    "type": "object",
    "properties": {
      "title": { "type": "string" },
      "author": { "type": "string" },
      "summary": { "type": "string" }
    }
  }
}
```

Or with raw text:
```json
{
  "text": "Apple reported revenue of $90B in Q4 2024. Tim Cook commented...",
  "schema": {
    "type": "object",
    "properties": {
      "company": { "type": "string" },
      "revenue": { "type": "string" },
      "quarter": { "type": "string" }
    }
  }
}
```

**Response:**
```json
{
  "data": {
    "title": "Example Article",
    "author": "Jane Doe",
    "summary": "..."
  },
  "meta": {
    "model": "openai/gpt-4o-mini",
    "input_chars": 4200,
    "processing_time_ms": 1234.5,
    "source": "url"
  }
}
```

## x402 Payment Flow

If `PAYMENT_REQUIRED=true` and no `X-Payment` header is present, the API returns:

```json
HTTP 402 Payment Required
{
  "x402Version": 1,
  "accepts": [{
    "scheme": "exact",
    "network": "eip155:8453",
    "maxAmountRequired": "100000",
    "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "payTo": "<WALLET_ADDRESS>",
    ...
  }]
}
```

## Deploy to Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template)

Set environment variables:
- `OPENROUTER_API_KEY`
- `WALLET_ADDRESS`
- `SERVICE_URL` (your Railway domain)

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | ✅ | OpenRouter API key |
| `WALLET_ADDRESS` | ✅ | Base wallet to receive USDC payments |
| `SERVICE_URL` | ✅ | Public URL (for 402 response resource field) |
| `PAYMENT_REQUIRED` | No | Set `false` to disable payment gate (default: `true`) |

## Limits

- Max input: **20,000 characters**
- Max output tokens: **2,048**
- Model: **openai/gpt-4o-mini** via OpenRouter
