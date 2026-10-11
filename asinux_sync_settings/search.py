def matches(query: str, *texts: str) -> bool:
    """Every word of the query appears somewhere in the texts, ignoring case."""
    haystack = " ".join(texts).casefold()
    return all(word in haystack for word in query.casefold().split())
