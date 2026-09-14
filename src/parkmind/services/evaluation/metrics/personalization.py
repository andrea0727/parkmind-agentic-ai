"""
The headline metrics for the demo — see docs/evaluation/README.md for the
full methodology.

Month-1 scope, build these two for real:
- preference_alignment_score: how closely a plan matches a guest's stated
  preferences.
- personalization_lift: Jaccard distance between itineraries generated for
  two opposite profiles on the same day/park/constraints. THIS IS THE
  NUMBER THAT PROVES THE THESIS ("ParkMind personalizes").

Everything else (acceptance rate, rejection reason distribution, group
conflict resolution rate) is documented but not required for the demo.
"""


def preference_alignment_score(plan, guest_profile) -> float:
    raise NotImplementedError


def personalization_lift(plan_a, plan_b) -> float:
    """Jaccard distance over the set of stops between two plans generated
    for opposite profiles under identical constraints."""
    raise NotImplementedError
