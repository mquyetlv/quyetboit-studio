"""Pair display/spoken clauses without inventing word timestamps."""

import re


def paired_clauses(content: str, spoken: str) -> list[tuple[str, str]]:
    # The current JSON has no word mapping. Use punctuation only when both sides
    # agree; otherwise keep the complete segment so no text is guessed or lost.
    for pattern in (r"(?<=[.!?…;,])\s+", r"(?<=[.!?…])\s+"):
        display = [part.strip() for part in re.split(pattern, content) if part.strip()]
        voice = [part.strip() for part in re.split(pattern, spoken) if part.strip()]
        if len(display) == len(voice):
            return list(zip(display, voice))
    return [(content.strip(), spoken.strip())]
