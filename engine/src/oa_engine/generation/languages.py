"""Which sandbox language a concept node is assessed in.

Most nodes are tied to one language (``sql.joins`` is SQL). DSA nodes are not: the same
skill (``dsa.arrays.two_pointers``) can be tested in Python or in Java, so the language
is chosen per question rather than read off the node.
"""

from __future__ import annotations

__all__ = ["DSA_LANGUAGES", "LANGUAGE_DOMAINS", "resolve_language"]

#: Domains whose nodes are assessed in the language of the same name.
LANGUAGE_DOMAINS = frozenset({"python", "sql", "java"})
#: Languages a DSA question may be written in.
DSA_LANGUAGES = ("python", "java")


def resolve_language(node_id: str, requested: str | None = None) -> str:
    """Return the language to author ``node_id`` in.

    Raises:
        ValueError: The node's domain is not supported, or ``requested`` contradicts
            a node that is tied to a different language.
    """
    domain = node_id.split(".", maxsplit=1)[0]
    if domain in LANGUAGE_DOMAINS:
        if requested not in (None, domain):
            raise ValueError(f"{node_id} is a {domain} node; it cannot be authored in {requested}")
        return domain
    if domain == "dsa":
        language = requested or "python"
        if language not in DSA_LANGUAGES:
            raise ValueError(f"DSA questions can be authored in {DSA_LANGUAGES}, not {language!r}")
        return language
    raise ValueError(f"generation is not implemented for the {domain!r} domain")
