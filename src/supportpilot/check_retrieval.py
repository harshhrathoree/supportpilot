"""Check that policy search finds the right policy for customer-style wording.

Run with:  uv run python -m supportpilot.check_retrieval

Needs the policies loaded first (`supportpilot.policies`). Reads only. Spends about 18 embedding
calls, so with the rate limiter it takes about 4 minutes.

Caution: these queries were written by the same person who wrote the policies, so the score is
optimistic. The messy-ticket eval in Step 8 is the stricter test.
"""

from supportpilot.db import get_session
from supportpilot.embeddings import GeminiEmbedder
from supportpilot.policies import search_policies

# (customer wording, the policy that SHOULD be found)
CASES = [
    ("my order came 45 minutes after the time you promised", "late-delivery"),
    ("delivery was super delayed, i want compensation", "late-delivery"),
    ("the raita wasn't in the bag but i paid for it", "missing-item"),
    ("one dish absent from my parcel", "missing-item"),
    ("i got a burger when i ordered a pizza", "wrong-item"),
    ("the biryani was stale and tasteless", "food-quality"),
    ("food arrived freezing cold", "cold-food"),
    ("found a cockroach in my soup", "food-safety-escalation"),
    ("i think i got food poisoning from your restaurant", "food-safety-escalation"),
    ("money was deducted two times for one order", "double-charge"),
    ("coupon code did not work and i paid full price", "coupon-not-applied"),
    ("where is my money back for the cancelled order", "cancelled-order-refund"),
    ("the delivery guy shouted at me", "rider-misconduct"),
    ("app shows delivered but nothing arrived at my door", "order-not-delivered"),
    ("who can approve a 400 rupee refund", "refund-approval-limits"),
    ("how long until the refund reaches my upi account", "refund-timelines"),
    ("how do i change my phone number", "account-and-app-help"),
    ("i was charged but never got an order confirmation", "payment-no-confirmation"),
]


def main() -> None:
    emb = GeminiEmbedder()
    top1 = top3 = 0
    with get_session() as s:
        for query, expected in CASES:
            hits = search_policies(s, emb, query, top_k=3)
            slugs = [h.slug for h in hits]
            top1 += slugs[0] == expected
            top3 += expected in slugs
            mark = "OK  " if slugs[0] == expected else ("top3" if expected in slugs else "MISS")
            print(f"{mark} {hits[0].score:.2f} want={expected:<24} got={slugs}")
    print(f"\ntop-1: {top1}/{len(CASES)}   top-3: {top3}/{len(CASES)}")


if __name__ == "__main__":
    main()
