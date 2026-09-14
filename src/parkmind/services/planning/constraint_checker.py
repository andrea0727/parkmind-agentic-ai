"""
ConstraintChecker — the functional safety gate. Every candidate plan must
pass through here before it can be proposed to the guest.

Month-1 scope, 10 rules:
 1. Park opening hours          6. Walking limit
 2. Height constraints          7. Lunch window
 3. Show schedule windows       8. Departure time
 4. Must-do attractions         9. Accessibility — mobility requirements
 5. Avoid attractions          10. Accessibility — walking limit (hard rule)

Rules 9-10 must NEVER become utility penalties in the optimizer.
"""

from parkmind.models.plan import Plan


class CheckResult:
    def __init__(self, valid: bool, violations: list[dict]):
        self.valid = valid
        self.violations = violations


class ConstraintChecker:
    def check(self, plan: Plan, constraints) -> CheckResult:
        raise NotImplementedError
