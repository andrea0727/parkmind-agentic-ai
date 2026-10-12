"""
Plan Explanation Generator module.

Orchestrates rationale building, narrative formatting, and strict numerical
grounding validation with a fail-closed fallback.
"""

from __future__ import annotations

import logging
from typing import Mapping

from parkmind.core.contracts import ExplainRequest, PlanExplanation
from parkmind.services.explanation.attribution_mapper import AttributionMapper
from parkmind.services.explanation.grounding_validator import (
    GroundingContext,
    NumericalGroundingValidator,
)
from parkmind.services.explanation.rationale_builder import RationaleBuilder

logger = logging.getLogger(__name__)


class PlanExplanationGenerator:
    """Generates grounded explanations for guest plans."""

    def __init__(
        self,
        validator: NumericalGroundingValidator | None = None,
        rationale_builder: RationaleBuilder | None = None,
    ) -> None:
        self._validator = validator or NumericalGroundingValidator()
        self._rationale_builder = rationale_builder or RationaleBuilder()

    def generate(self, request: ExplainRequest) -> PlanExplanation:
        """
        Generate a validated PlanExplanation payload.

        If numerical grounding fails, falls back to a deterministic, safe output.
        """
        plan = request.plan
        catalog = request.catalog_names or {}

        # 1. Build Rationales for each stop
        rationales = self._rationale_builder.build_rationales(
            plan=plan,
            constraints=request.constraints,
            provenance=plan.provenance,
            catalog_names=catalog,
        )

        # 2. Build Candidate Narrative Text
        summary = (
            f"Your itinerary includes {len(plan.stops)} stops with a total expected wait time "
            f"of {round(plan.total_wait_minutes)} minutes and {round(plan.total_walking_minutes)} minutes of walking."
        )

        diff_summary: str | None = None
        if request.diff:
            diff_summary = (
                f"Replan adjusted {len(request.diff.stops_added)} added, "
                f"{len(request.diff.stops_removed)} removed, and "
                f"{len(request.diff.stops_reordered)} reordered stops."
            )

        unmet_summary: str | None = None
        if plan.unmet_must_do:
            unmet_summary = (
                f"Note: Could not schedule {len(plan.unmet_must_do)} must-do attractions "
                "due to constraints."
            )

        # Assemble full text
        text_parts = [summary]
        if diff_summary:
            text_parts.append(diff_summary)
        if unmet_summary:
            text_parts.append(unmet_summary)

        for r in rationales:
            name = catalog.get(r.node_id, r.node_id)
            arr = r.arrival_time.strftime("%H:%M")
            dep = r.departure_time.strftime("%H:%M")
            text_parts.append(
                f"- {name} ({arr} - {dep}): {r.primary_reason} "
                f"(Wait: {round(r.wait_minutes)} min, Walk: {round(r.walk_minutes)} min)"
            )

        candidate_full_text = "\n".join(text_parts)

        # 3. Perform Grounding Validation
        grounding_context = GroundingContext.from_inputs(
            plan=plan,
            diff=request.diff,
            context=request.live_context,
        )

        is_valid, ungrounded_tokens = self._validator.validate(
            text=candidate_full_text,
            context=grounding_context,
        )

        if is_valid:
            return PlanExplanation(
                summary=summary,
                rationales=rationales,
                diff_summary=diff_summary,
                unmet_must_do_summary=unmet_summary,
                grounding_verified=True,
                fallback_used=False,
                full_text=candidate_full_text,
            )

        # 4. Fail-Closed Fallback on Grounding Violation
        logger.warning(
            "Ungrounded tokens detected in generated explanation: %s. Using deterministic fallback.",
            ungrounded_tokens,
        )
        return self._build_deterministic_fallback(request, rationales)

    def _build_deterministic_fallback(
        self,
        request: ExplainRequest,
        rationales: list,
    ) -> PlanExplanation:
        """Constructs a minimal deterministic explanation guaranteed to pass grounding."""
        plan = request.plan
        summary = (
            f"Plan with {len(plan.stops)} stops, {round(plan.total_wait_minutes)} min wait, "
            f"and {round(plan.total_walking_minutes)} min walk."
        )

        fallback_text = f"Fallback Summary: {summary}"
        return PlanExplanation(
            summary=summary,
            rationales=rationales,
            diff_summary=None,
            unmet_must_do_summary=None,
            grounding_verified=True,
            fallback_used=True,
            full_text=fallback_text,
        )