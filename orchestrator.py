"""
The orchestrator: chief editor of the digest.

Multi-agent pattern used here: "agents as tools". The orchestrator is itself
an agent with a ReAct loop - but its tools are not web_search or fetch_rss.
Its tools ARE the specialists. When it calls consult_news_specialist, a whole
second agent (with its own loop and tools) runs to completion, and its final
answer comes back as a tool result.

So the hierarchy is:

    orchestrator (decides WHAT is needed, writes the final digest)
      ├── news_specialist   (own loop, tools: fetch_rss, web_search, scrape_url)
      └── social_specialist (own loop, tools: reddit_hot, x_search, scrape_url)

The API layer (main.py) only calls generate_digest() - it has no idea how
many agents are behind it. That's the point of layering.
"""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from specialists import news_specialist, social_specialist

MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 3000
MAX_TURNS = 6  # orchestrator turns; each turn may trigger whole specialist runs

ORCHESTRATOR_SYSTEM_PROMPT = """You are the chief editor of a daily Bitcoin news \
digest for a German YouTube creator. You do NOT research anything yourself - \
you have two specialists you can consult:

- consult_news_specialist: researches current Bitcoin news from news sites \
(RSS feeds, web search, article reading). Give it a clear task describing what \
to look into.
- consult_social_specialist: analyzes what the Bitcoin community on Reddit and \
X is discussing and the mood around it. Give it a clear task.

How to work:
1. Think about what today's digest needs, then consult BOTH specialists. \
Give each a specific, focused task (mention the requested focus topic if any). \
You may consult a specialist a second time if its answer leaves an important \
gap - but be economical.
2. Cross-check: where news and community sentiment touch the same topic, \
connect them. Where they contradict, say so.
3. Then write the final digest yourself. Use ONLY material the specialists \
reported, with their source URLs. Never add facts of your own.

Deliver the final digest as plain text (no tool call), in German, in exactly \
this markdown structure:

# Bitcoin News Digest - <today's date>

## Kurzueberblick
2-3 sentences: the overall picture of the day.

## Top-Themen
- **<Headline>** - 1-2 sentences of substance. (Quelle: <URL>)
- ... (3-6 items)

## Community-Stimmung
2-4 sentences: what Reddit/X are discussing, the dominant mood, and whether it \
matches or contradicts the news. Include 1-2 example links.

## Video-Ideen
- 1-3 concrete video ideas that combine today's news with the community mood.

Keep it concise and factual."""


SPECIALIST_SCHEMAS = [
    {
        "name": "consult_news_specialist",
        "description": (
            "Delegate a research task to the news specialist. It reads RSS "
            "feeds and articles and returns a sourced fact list about current "
            "Bitcoin news."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Clear research task, e.g. 'Find the most important Bitcoin news of the last 24-48 hours, focus on regulation.'",
                }
            },
            "required": ["task"],
        },
    },
    {
        "name": "consult_social_specialist",
        "description": (
            "Delegate an analysis task to the social specialist. It checks "
            "Reddit and X and returns the dominant community topics and mood, "
            "with sources."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Clear analysis task, e.g. 'What is the Bitcoin community discussing today and what is the mood?'",
                }
            },
            "required": ["task"],
        },
    },
]


def load_api_key() -> str:
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return api_key


def generate_digest(topic: str | None = None, client=None, on_event=None) -> dict:
    """Run the orchestrator (which runs the specialists) and return the digest.

    Return shape: {markdown, model, generated_at, focus}

    `on_event` is an optional callback that receives a dict for every step of
    every agent. Pass None (the API does) and nothing is recorded; pass a
    printer (run_digest.py does) and you can watch the whole thing work.
    """
    from anthropic import Anthropic

    if client is None:
        client = Anthropic(api_key=load_api_key())

    def emit(**event):
        if on_event:
            on_event({"agent": "orchestrator", **event})

    # The specialists need the client too - we close over it here so the
    # orchestrator loop below can dispatch to them like normal functions.
    # The trace callback is passed down, so nested agents report too.
    specialist_functions = {
        "consult_news_specialist": lambda task: news_specialist(task, client, on_event),
        "consult_social_specialist": lambda task: social_specialist(task, client, on_event),
    }

    focus = topic.strip() if topic and topic.strip() else "general Bitcoin news of the last 24-48 hours"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    user_request = f"Create today's Bitcoin news digest. Today is {today}. Focus: {focus}."

    messages = [{"role": "user", "content": user_request}]
    emit(type="task", text=user_request)

    for turn in range(1, MAX_TURNS + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=ORCHESTRATOR_SYSTEM_PROMPT,
            tools=SPECIALIST_SCHEMAS,
            messages=messages,
        )
        usage = getattr(response, "usage", None)
        emit(
            type="llm_call",
            turn=turn,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            stop_reason=getattr(response, "stop_reason", None),
        )
        messages.append({"role": "assistant", "content": response.content})

        thinking = "".join(b.text for b in response.content if b.type == "text").strip()
        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if not tool_uses:
            emit(type="final", turn=turn, text=thinking)
            return {
                "markdown": thinking,
                "model": MODEL,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "focus": focus,
            }

        if thinking:
            emit(type="thinking", turn=turn, text=thinking)

        tool_results = []
        for block in tool_uses:
            func = specialist_functions.get(block.name)
            emit(type="delegate", turn=turn, to=block.name, args=block.input)
            if func is None:
                result = f"Unknown specialist: {block.name}"
            else:
                try:
                    # This single line runs an ENTIRE specialist agent loop.
                    result = func(**block.input)
                except Exception as exc:
                    result = f"Specialist '{block.name}' failed: {exc}"
            emit(type="delegate_result", turn=turn, of=block.name, result=result)
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": result}
            )
        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Orchestrator did not finish within {MAX_TURNS} turns.")
