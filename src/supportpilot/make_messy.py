"""Make a harder eval set: Gemini rewrites each template ticket the way a real customer might.

Run with:  uv run python -m supportpilot.make_messy
Writes data/messy_tickets.jsonl. About 100 calls, so roughly 9 minutes on the free tier.
A person must read a sample of the output before it is trusted (see docs).

Each rewrite keeps the SAME complaint, the SAME order number and the SAME item, so the label
from labels.jsonl stays correct. Only the style changes.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from supportpilot.llm import GeminiClient, LLMClient
from supportpilot.seed import LABELS_PATH

MESSY_PATH = Path(__file__).resolve().parents[2] / "data" / "messy_tickets.jsonl"

# One style per ticket, cycled, so the set has an even mix.
STYLES = [
    "very short and angry, a few words, no punctuation, with spelling mistakes",
    "Hinglish: Hindi written in English letters mixed with English words, informal",
    "long and rambling, with irrelevant details about their day before getting to the point",
    "polite and formal, like an email, with a greeting and a thank-you",
    "SMS style abbreviations, lowercase, typos, emojis allowed",
    "vague: the complaint is hinted at but the exact problem is not stated outright",
]

SYSTEM = """\
You rewrite customer support tickets for a food-delivery app. The input is a ticket inside
<ticket> tags. Rewrite it in the requested style the way a real customer would write it.

Rules:
- Keep the same complaint. Do not turn it into a different kind of problem.
- Keep every fact: the order number if one is given, the dish names, and any amounts.
- Do not add new facts, new dishes, new amounts or new accusations.
- Do not say what category the complaint belongs to.
- Output only the rewritten ticket text.
"""


class Rewrite(BaseModel):
    text: str


def rewrite(llm: LLMClient, text: str, style: str) -> str:
    prompt = f"Style: {style}\n\n<ticket>\n{text}\n</ticket>"
    result, _ = llm.generate_structured(system=SYSTEM, prompt=prompt, schema=Rewrite)
    return result.text.strip()


ORDER_REFERENCE = re.compile(r"(#|order\s+(?:number\s+|no\.?\s*)?)(\d+)", re.IGNORECASE)


def fix_order_numbers(original: str, rewritten: str, order_id: int | None) -> str:
    """Replace order numbers the model invented with the real one.

    Gemini sometimes makes up an order number (we saw #12345). A wrong number would send the agent
    looking at the wrong order and spoil the eval. A number is kept if the original ticket had it.
    """
    if order_id is None:
        return rewritten
    allowed = set(re.findall(r"\d+", original))

    def swap(match: re.Match) -> str:
        return match.group(0) if match.group(2) in allowed else f"{match.group(1)}{order_id}"

    return ORDER_REFERENCE.sub(swap, rewritten)


def main() -> None:
    rows = [json.loads(line) for line in LABELS_PATH.read_text().splitlines()]
    llm = GeminiClient()

    def work(item: tuple[int, dict]) -> dict:
        i, row = item
        style = STYLES[i % len(STYLES)]
        return {
            **row,
            "original_text": row["text"],
            "style": style,
            "text": rewrite(llm, row["text"], style),
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        out = list(pool.map(work, enumerate(rows)))

    MESSY_PATH.write_text("".join(json.dumps(r) + "\n" for r in out))
    print(f"Wrote {len(out)} messy tickets to {MESSY_PATH}")


if __name__ == "__main__":
    main()
