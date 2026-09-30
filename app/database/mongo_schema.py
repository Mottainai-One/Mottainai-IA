"""Canonical enums mirroring MongoDB $jsonSchema validators applied to
collections in the deployed database (the validators themselves live in
the Mongo deployment, not in this repo's source — this module exists so
app code, tests, and the static AI checklist share one definition instead
of duplicating the hardcoded set, as happened before)."""

# messages.sources[].type — allowed by the `messages` collection's schema.
SOURCE_TYPES = frozenset({"rag", "sql", "api", "manual", "url", "other"})
