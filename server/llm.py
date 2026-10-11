"""Optional LLM tier (Claude). Used only when the fast path is not confident.

The model does NOT answer questions or touch data. It acts as a *request normaliser*: it picks an intent and rewrites the
user's words into an explicit command in the app's vocabulary ("why did revenue fall in March 2026"). That rewrite goes
back through the same deterministic parser, validators and guardrails, so a model mistake can at worst cause a
clarification — never an invented number or an unvalidated write.

Enabled when ANTHROPIC_API_KEY is set. Model: BIZPILOT_LLM_MODEL (default claude-haiku-5-5, for latency).
"""
import json
import os
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("BIZPILOT_LLM_MODEL", "claude-haiku-5-5")
TIMEOUT = float(os.environ.get("BIZPILOT_LLM_TIMEOUT", "8"))


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _post(url, headers, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def route(message: str, page: str | None, as_of: str, intents: dict, transport=_post):
    """-> {"intent": str, "rewrite": str} or None on any failure / invalid output."""
    tool = {"name": "route_request", "description": "Choose the intent for the user's request and rewrite it as an explicit command.",
            "input_schema": {"type": "object", "properties": {"intent": {"type": "string", "enum": list(intents)},
                                                              "rewrite": {"type": "string", "description": "Short explicit command with explicit periods (e.g. 'March 2026'), metric names and entity names exactly as the user gave them."}},
                             "required": ["intent", "rewrite"]}}
    system = ("You route requests for an in-app assistant of a supply-chain / business-operations application. Today is " + as_of + ". The user is on the page: "
              + str(page) + ". Intents:\n" + "\n".join(f"- {k}: {v['desc']}" for k, v in intents.items())
              + "\nNever invent entity names or numbers. If the request fits no intent, choose 'help'.")
    body = {"model": MODEL, "max_tokens": 300, "system": system, "tools": [tool], "tool_choice": {"type": "tool", "name": "route_request"},
            "messages": [{"role": "user", "content": message[:500]}]}
    headers = {"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01", "content-type": "application/json"}
    try:
        out = transport(API_URL, headers, body, TIMEOUT)
        for block in out.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "route_request":
                inp = block.get("input") or {}
                if inp.get("intent") in intents and isinstance(inp.get("rewrite"), str) and 0 < len(inp["rewrite"]) <= 300:
                    return {"intent": inp["intent"], "rewrite": inp["rewrite"]}
    except Exception:
        return None
    return None
