"""Typed identifiers.

Every id in this system is a UUID. That means nothing stops you writing:

    repo.get_report(profile_id)      # wrong id, same type, compiles fine

``NewType`` fixes that at zero runtime cost. At runtime a ``ProfileId`` *is* a plain
UUID — no wrapper object, no allocation. But mypy treats it as a distinct type, so
passing a ``ProfileId`` where a ``ReportId`` is expected fails the type check.

This is the cheapest safety you will ever buy: one line each, no performance cost,
and it removes an entire category of bug that is otherwise found only in production.
"""

from typing import NewType
from uuid import UUID

UserId = NewType("UserId", UUID)
ProfileId = NewType("ProfileId", UUID)
ReportId = NewType("ReportId", UUID)
ObservationId = NewType("ObservationId", UUID)

# Not a UUID: the canonical test id is a human-readable slug like "alt" or "hba1c".
# It is a stable key we own, and it appears in URLs, cache keys and the dictionary
# tables, so readability matters more than opacity here.
CanonicalTestId = NewType("CanonicalTestId", str)
