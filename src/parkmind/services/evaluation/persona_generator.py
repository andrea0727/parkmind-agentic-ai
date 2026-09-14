"""
Generates synthetic GuestProfiles — including deliberately opposite pairs
(low-intensity family vs. thrill-seeking maximizer) so personalization_lift()
has something meaningful to compare.

Does NOT feed preference_learner.py this month (that's out of scope) — only
feeds baseline/metric comparisons.
"""


def generate_opposite_profile_pair() -> tuple:
    raise NotImplementedError
