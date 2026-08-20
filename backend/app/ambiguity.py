"""Code-level guarantees on top of the LLM's own confidence/ambiguities
judgment, so the review UI can trust these fields regardless of what the
model reported.
"""

from typing import List, Literal, Tuple

Confidence = Literal["high", "medium", "low"]


def finalize_confidence_and_ambiguities(
    confidence: Confidence,
    ambiguities: List[str],
    date_resolved: bool,
    date_phrase: str,
) -> Tuple[Confidence, List[str]]:
    ambiguities = list(ambiguities)

    if not date_resolved:
        note = f'could not resolve a specific date from "{date_phrase}"'
        if note not in ambiguities:
            ambiguities.append(note)
        return "low", ambiguities

    if ambiguities and confidence == "high":
        confidence = "medium"

    return confidence, ambiguities
