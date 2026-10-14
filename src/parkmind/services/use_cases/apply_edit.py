"""Use case: ``apply_edits`` -- the guests' edit of a proposal, as constraints (section 23).

An edit never touches the plan itself: no time, wait or walk comes from the guests.
It says which stops to drop and which attractions to add; ``execute`` turns that
into the party's constraints (a dropped stop is avoided, an added attraction is a
must-do) and the graph plans again from those -- the optimizer or the replanner
builds the new candidate, the ConstraintChecker validates it and it is proposed as
a new PENDING proposal. The edited proposal itself never activates a plan.

``execute`` only reads (the catalog), so the graph validates an edit before the
decision is stored: an edit it refuses changes nothing.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from parkmind.core.contracts import (
    PartyConstraints,
    Plan,
    PlanExecutionState,
    StopKind,
)
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    load_catalog,
    open_deps,
)

_EDITABLE = frozenset({StopKind.ATTRACTION, StopKind.SHOW})


class PlanEdit(BaseModel):
    """The resume value's ``"edit"``: node ids, nothing else."""

    model_config = ConfigDict(extra="forbid")

    remove: list[str] = Field(default_factory=list)
    """Attraction or show stops of the proposed plan to drop."""
    add: list[str] = Field(default_factory=list)
    """Catalog attractions to fit in."""


class EditProblem(str, Enum):
    EMPTY = "EMPTY"
    REMOVED_AND_ADDED = "REMOVED_AND_ADDED"
    NOT_IN_PLAN = "NOT_IN_PLAN"
    NOT_AN_ATTRACTION_STOP = "NOT_AN_ATTRACTION_STOP"
    ALREADY_DONE = "ALREADY_DONE"
    UNKNOWN_ATTRACTION = "UNKNOWN_ATTRACTION"
    ALREADY_IN_PLAN = "ALREADY_IN_PLAN"


class InvalidEditError(ValueError):
    """The edit cannot be applied to this plan; nothing was changed."""

    def __init__(self, problem: EditProblem, message: str) -> None:
        self.problem = problem
        super().__init__(message)


@dataclass(frozen=True)
class AppliedEdit:
    constraints: PartyConstraints
    """The party's constraints with the edit applied, one version later."""
    removed: list[str]
    """The stops the edit dropped (a rejection, for the behavior log)."""
    added: list[str]
    """The attractions the edit asked for (interest, for the behavior log)."""


def _unique(ids: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(ids))


class ApplyEditUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def execute(
        self,
        constraints: PartyConstraints,
        plan: Plan,
        edit: PlanEdit,
        *,
        execution_state: PlanExecutionState | None = None,
    ) -> AppliedEdit:
        """``InvalidEditError`` names the first thing wrong with the edit."""
        remove, add = _unique(edit.remove), _unique(edit.add)
        if not remove and not add:
            raise InvalidEditError(
                EditProblem.EMPTY, "the edit must remove or add at least one attraction"
            )
        if both := sorted(set(remove) & set(add)):
            raise InvalidEditError(
                EditProblem.REMOVED_AND_ADDED,
                f"{', '.join(both)} cannot be both removed and added",
            )
        stops = {stop.node_id: stop for stop in plan.stops}
        completed = (
            set(execution_state.completed_stop_ids) if execution_state else set()
        )
        for node_id in remove:
            stop = stops.get(node_id)
            if stop is None:
                raise InvalidEditError(
                    EditProblem.NOT_IN_PLAN,
                    f"{node_id} is not a stop of the proposed plan",
                )
            if stop.kind not in _EDITABLE:
                raise InvalidEditError(
                    EditProblem.NOT_AN_ATTRACTION_STOP,
                    f"{node_id} is a {stop.kind.value.lower()} stop; only attractions "
                    "and shows can be removed",
                )
            if node_id in completed:
                raise InvalidEditError(
                    EditProblem.ALREADY_DONE, f"{node_id} is already done"
                )
        if add:
            with open_deps(self._deps_factory) as deps:
                known = {attraction.node_id for attraction in load_catalog(deps)}
            for node_id in add:
                if node_id not in known:
                    raise InvalidEditError(
                        EditProblem.UNKNOWN_ATTRACTION,
                        f"{node_id} is not an attraction of this park",
                    )
                if node_id in stops:
                    raise InvalidEditError(
                        EditProblem.ALREADY_IN_PLAN,
                        f"{node_id} is already in the proposed plan",
                    )
        removed, added = set(remove), set(add)
        edited = constraints.model_copy(
            update={
                "must_do": [n for n in constraints.must_do if n not in removed]
                + [n for n in add if n not in constraints.must_do],
                "avoid": [n for n in constraints.avoid if n not in added]
                + [n for n in remove if n not in constraints.avoid],
                "constraints_version": constraints.constraints_version + 1,
            }
        )
        return AppliedEdit(edited, remove, add)
