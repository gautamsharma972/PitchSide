from .simulate import generate_match
from .analytics import enrich, key_moments, match_facts, build_moment


def load_match(seed: int = 3):
    """Swap this for a loader of the official dataset (same event schema)."""
    return enrich(generate_match(seed))
