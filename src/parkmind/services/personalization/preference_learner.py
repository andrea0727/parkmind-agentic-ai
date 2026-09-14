"""
NOT IN MONTH-1 SCOPE. Documented stub only — see docs/decisions/scope.md.

Where a learned guest-preference model (logistic regression or a simple
bandit over accept/reject signals) would go, bootstrapped via synthetic
personas from services/evaluation/persona_generator.py to solve
cold-start.

Rationale for leaving it unbuilt: a half-trained model shown live is worse
than a well-documented design with no live demo of it. Revisit only if
everything else finishes early.
"""


class PreferenceLearner:
    def update(self, guest_id: str, history) -> None:
        raise NotImplementedError

    def predict_weights(self, guest_id: str) -> dict[str, float]:
        raise NotImplementedError
