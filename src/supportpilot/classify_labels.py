"""Run the classifier over every labeled ticket and report accuracy, cost and the misses.

Run with:  uv run python -m supportpilot.classify_labels

This reads data/labels.jsonl, so run `supportpilot.seed` first. It only READS the database
and the labels file. It spends a little of your Gemini quota (about 100 calls).
"""

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from supportpilot.classifier import classify
from supportpilot.llm import GeminiClient
from supportpilot.seed import LABELS_PATH


def main() -> None:
    rows = [json.loads(line) for line in LABELS_PATH.read_text().splitlines()]
    llm = GeminiClient()

    # Threads overlap the network waiting. The LLM client's rate limiter keeps the total
    # number of requests per minute under the free-tier limit, however many threads we use.
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda row: classify(llm, row["text"]), rows))

    correct = 0
    cost = 0.0
    misses = []
    for row, (result, usage) in zip(rows, results, strict=True):
        cost += usage.cost_usd
        if result.category == row["category"]:
            correct += 1
        else:
            misses.append((row, result))

    print(f"Accuracy: {correct}/{len(rows)} = {correct / len(rows):.1%}")
    print(f"Total cost: ${cost:.5f}  (${cost / len(rows):.6f} per ticket)")
    print(f"Priority mix: {dict(Counter(r.priority for r, _ in results))}")
    if misses:
        print("\nMisses (expected -> predicted):")
        for row, result in misses:
            print(f"  #{row['ticket_id']} {row['category']} -> {result.category}: {row['text']}")


if __name__ == "__main__":
    main()
