"""SessionStore -- where ``AccessibilityRequirements`` live (section 12, 33 [C15, C19]).

The graph state carries only ``accessibility_ref`` (guest ids, section 34); the
requirements are loaded per run from this store, which honors
``retention_policy``:

* ``session_only`` -- held for the session only and never written to any
  persisted table, checkpoint tables included;
* ``persisted`` -- requires explicit consent, stored as the derived flags only.

``session_id`` is the LangGraph ``thread_id`` of the run.
"""

from typing import Protocol

from parkmind.core.contracts import AccessibilityRequirements


class SessionStore(Protocol):
    def put(self, session_id: str, requirements: AccessibilityRequirements) -> None:
        """Hold ``requirements`` for the session according to its retention policy.

        Raises ``ConsentRequiredError`` when a ``persisted`` record has no
        consent, ``NotFoundError`` when persisting for an unknown guest.
        """
        ...

    def get(self, session_id: str, guest_id: str) -> AccessibilityRequirements | None:
        """The guest's requirements for this session, or ``None``.

        A session-scoped record wins over a persisted one for the same guest.
        """
        ...

    def end_session(self, session_id: str) -> None:
        """Drop everything held for the session. Persisted records are unaffected."""
        ...
