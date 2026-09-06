"""
Run the digest from the command line and watch every step live.

This is the debugging view: you see which agent is working, which tool it
calls with which arguments, what came back, and what each agent concluded -
plus token usage and a cost estimate at the end.

Usage:
    python run_digest.py                    # general digest, full trace
    python run_digest.py "ETF flows"        # with a focus topic
    python run_digest.py --quiet            # only the final digest
    python run_digest.py --full             # don't truncate tool results
"""

import sys
import time

from dotenv import load_dotenv

from orchestrator import generate_digest

# Rough Claude Sonnet pricing per million tokens (USD) - for a ballpark only.
PRICE_IN_PER_MTOK = 3.00
PRICE_OUT_PER_MTOK = 15.00

INDENT = {"orchestrator": "", "news": "    ", "social": "    "}
LABEL = {"orchestrator": "ORCHESTRATOR", "news": "NEWS-SPEZIALIST", "social": "SOCIAL-SPEZIALIST"}


class Tracer:
    """Prints agent events as they happen and totals up token usage."""

    def __init__(self, quiet=False, truncate=400):
        self.quiet = quiet
        self.truncate = truncate
        self.input_tokens = 0
        self.output_tokens = 0
        self.tool_calls = 0
        self.started = time.time()

    def _short(self, text):
        text = " ".join(str(text).split())
        if self.truncate and len(text) > self.truncate:
            return text[: self.truncate] + f" ... [+{len(text) - self.truncate} Zeichen]"
        return text

    def __call__(self, event):
        agent = event.get("agent", "?")
        pad = INDENT.get(agent, "  ")
        kind = event["type"]

        if kind == "llm_call":
            self.input_tokens += event.get("input_tokens") or 0
            self.output_tokens += event.get("output_tokens") or 0
        if kind == "tool_call":
            self.tool_calls += 1

        if self.quiet:
            return

        if kind == "task":
            print(f"\n{pad}=== {LABEL.get(agent, agent)} ===")
            print(f"{pad}Auftrag: {self._short(event['text'])}")

        elif kind == "llm_call":
            tin, tout = event.get("input_tokens"), event.get("output_tokens")
            print(f"{pad}[Runde {event['turn']}] LLM-Aufruf  (Tokens rein: {tin}, raus: {tout})")

        elif kind == "thinking":
            print(f"{pad}  Überlegung: {self._short(event['text'])}")

        elif kind == "tool_call":
            args = ", ".join(f"{k}={v!r}" for k, v in (event.get("args") or {}).items())
            print(f"{pad}  -> Tool {event['tool']}({self._short(args)})")
            print(f"{pad}     Ergebnis: {self._short(event['result'])}")

        elif kind == "delegate":
            args = (event.get("args") or {}).get("task", "")
            print(f"{pad}  -> delegiert an {event['to']}")
            print(f"{pad}     Aufgabe: {self._short(args)}")

        elif kind == "delegate_result":
            print(f"{pad}  <- Antwort von {event['of']}: {self._short(event['result'])}")

        elif kind == "final":
            print(f"{pad}[Runde {event['turn']}] FERTIG ({len(event['text'])} Zeichen)")

        elif kind == "limit":
            print(f"{pad}!! Rundenlimit ({event['turns']}) erreicht, ohne fertig zu werden")

    def summary(self):
        cost = (
            self.input_tokens / 1_000_000 * PRICE_IN_PER_MTOK
            + self.output_tokens / 1_000_000 * PRICE_OUT_PER_MTOK
        )
        return (
            f"Dauer: {time.time() - self.started:.0f}s | "
            f"Tool-Aufrufe: {self.tool_calls} | "
            f"Tokens rein: {self.input_tokens:,} raus: {self.output_tokens:,} | "
            f"geschätzte Kosten: ${cost:.3f}"
        )


def main():
    load_dotenv()

    args = [a for a in sys.argv[1:]]
    quiet = "--quiet" in args
    full = "--full" in args
    topic = " ".join(a for a in args if not a.startswith("--")).strip() or None

    tracer = Tracer(quiet=quiet, truncate=None if full else 400)

    print(f"Starte Digest{f' zum Thema: {topic}' if topic else ''} ...")
    try:
        digest = generate_digest(topic=topic, on_event=tracer)
    except Exception as exc:
        print(f"\nFEHLGESCHLAGEN: {exc}")
        print(tracer.summary())
        sys.exit(1)

    print("\n" + "=" * 70)
    print(digest["markdown"])
    print("=" * 70)
    print(tracer.summary())


if __name__ == "__main__":
    main()
