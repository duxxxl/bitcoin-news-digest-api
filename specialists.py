"""
The specialist agents.

Each specialist is a full little agent: it has its own system prompt (its
"job description"), its own set of tools, and its own ReAct loop. The
orchestrator never sees these tools - it only sees the specialists themselves
and gives them a task in plain language.

Both specialists share one generic loop, run_agent_loop(). That's the key
insight of this file: an "agent" is just (system prompt + tools + loop), so
we can stamp out as many specialists as we want from the same machinery.
"""

from tools import (
    NEWS_TOOL_FUNCTIONS,
    NEWS_TOOL_SCHEMAS,
    SOCIAL_TOOL_FUNCTIONS,
    SOCIAL_TOOL_SCHEMAS,
)

MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 2048
MAX_TURNS = 8  # per specialist, so one stuck specialist can't loop forever


NEWS_SYSTEM_PROMPT = """You are a Bitcoin news researcher. You receive a research \
task and must gather solid, current facts.

How to work:
1. Start with fetch_rss on 2-3 of the known feeds (bitcoinmagazine, coindesk, \
cointelegraph, decrypt) - that gives you the freshest headlines with dates.
2. Use web_search only for things the feeds don't cover (e.g. a specific event \
or a price question).
3. For the 2-4 most important stories, use scrape_url to read the actual \
article before reporting on it. Headlines alone are not enough.
4. Never invent facts. Every claim in your answer must come from a source you \
actually fetched, and you must include the source URL next to it.

Answer with a compact fact list in markdown: one bullet per story, each with \
1-3 sentences of substance, the publication date if known, and the source URL. \
No introduction, no conclusion - just the findings."""


_SOCIAL_SYSTEM_PROMPT_BASE = """You are a crypto community analyst. You receive a task \
and must report what the Bitcoin community is currently talking about.

How to work:
1. Use reddit_hot (r/Bitcoin, optionally also r/CryptoCurrency) to see what's \
being discussed and how strongly (scores, comment counts are your signal).
2. {x_instructions}
3. If a Reddit thread looks important, you may scrape_url its permalink to \
read the discussion.
4. Distinguish clearly between facts and sentiment/opinions. Never present \
community claims as verified facts.

Answer with a compact markdown summary: the 3-5 dominant discussion topics, \
each with the mood around it (bullish/bearish/angry/excited/...), evidence \
(scores, example posts), and source URLs. No introduction, no conclusion."""


def _watched(env_var: str) -> list[str]:
    """Read a comma-separated account list from an env variable."""
    import os

    raw = os.environ.get(env_var, "")
    return [a.strip().lstrip("@") for a in raw.split(",") if a.strip()]


def social_system_prompt() -> str:
    """Build the social specialist's prompt, injecting the watched accounts per
    platform. Built at call time (not import time) so .env is already loaded."""
    blocks = []

    bluesky = _watched("BLUESKY_ACCOUNTS")
    if bluesky:
        blocks.append(
            "Bluesky accounts to check with bluesky_user_posts: "
            + ", ".join(bluesky)
            + "."
        )

    nostr = _watched("NOSTR_ACCOUNTS")
    if nostr:
        blocks.append(
            "Nostr accounts to check with nostr_user_posts (this is where most "
            "Bitcoiners post - weight it highly): " + ", ".join(nostr) + "."
        )

    mastodon = _watched("MASTODON_ACCOUNTS")
    if mastodon:
        blocks.append(
            "Mastodon accounts to check with mastodon_user_posts: "
            + ", ".join(mastodon)
            + "."
        )

    if blocks:
        instructions = (
            "The creator watches specific accounts. Check EACH of them:\n      "
            + "\n      ".join(blocks)
            + "\n   IMPORTANT: always call these tools with max_age_days=7. People "
            "post irregularly - a 1-2 day window would wrongly drop active accounts. "
            "The digest is daily, but community MOOD builds over a week.\n"
            "   If a tool reports an account has no recent posts, skip it - never "
            "present old posts as current. Always state a post's date when you cite "
            "it. If account data is thin overall, say so plainly and lean on Reddit."
        )
    else:
        instructions = (
            "No specific accounts are configured. Rely on reddit_hot for the "
            "community signal and say plainly that account-level data was "
            "unavailable."
        )
    return _SOCIAL_SYSTEM_PROMPT_BASE.format(x_instructions=instructions)


def run_agent_loop(
    client,
    system_prompt,
    user_task,
    tool_schemas,
    tool_functions,
    agent="agent",
    on_event=None,
) -> str:
    """Generic ReAct loop: ask Claude, run requested tools, feed results back,
    repeat until Claude answers with plain text. Returns that final text.

    `on_event` is an optional callback that receives a dict for every step
    (LLM call, tool call, final answer). This is how the tracing in
    run_digest.py works - the loop itself stays free of print statements, so
    the same code runs unchanged inside the API where printing would be wrong.
    """

    def emit(**event):
        if on_event:
            on_event({"agent": agent, **event})

    messages = [{"role": "user", "content": user_task}]
    emit(type="task", text=user_task)

    for turn in range(1, MAX_TURNS + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            tools=tool_schemas,
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

        # Any plain text Claude wrote alongside tool calls = its reasoning.
        thinking = "".join(b.text for b in response.content if b.type == "text").strip()

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            emit(type="final", turn=turn, text=thinking)
            return thinking

        if thinking:
            emit(type="thinking", turn=turn, text=thinking)

        tool_results = []
        for block in tool_uses:
            func = tool_functions.get(block.name)
            if func is None:
                result = f"Unknown tool: {block.name}"
            else:
                try:
                    result = str(func(**block.input))
                except Exception as exc:
                    result = f"Tool '{block.name}' failed: {exc}"
            emit(
                type="tool_call",
                turn=turn,
                tool=block.name,
                args=block.input,
                result=result,
            )
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": result}
            )
        messages.append({"role": "user", "content": tool_results})

    emit(type="limit", turns=MAX_TURNS)
    return "(Specialist stopped after reaching the turn limit without a final answer.)"


def news_specialist(task: str, client, on_event=None) -> str:
    """Research current Bitcoin news for the given task."""
    return run_agent_loop(
        client,
        NEWS_SYSTEM_PROMPT,
        task,
        NEWS_TOOL_SCHEMAS,
        NEWS_TOOL_FUNCTIONS,
        agent="news",
        on_event=on_event,
    )


def social_specialist(task: str, client, on_event=None) -> str:
    """Analyze current Bitcoin community sentiment for the given task."""
    return run_agent_loop(
        client,
        social_system_prompt(),
        task,
        SOCIAL_TOOL_SCHEMAS,
        SOCIAL_TOOL_FUNCTIONS,
        agent="social",
        on_event=on_event,
    )
