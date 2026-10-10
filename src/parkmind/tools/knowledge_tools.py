"""``knowledge.*`` tools (P0-26; Architecture section 30).

``search_policies``, ``find_similar_attractions``, ``check_accessibility``. Each
handler is one call to ``KnowledgeQueries``. Search answers carry the corpus
version, the ranking strategy and -- when the keyword fallback answered -- the
reason (section 43). ``check_accessibility`` takes a guest's *derived* flags
(closed ``RideRestriction`` values, section 30's input), never their
``AccessibilityRequirements`` (C19), and keeps the fail-closed result of P0-26a:
no notice on file means not eligible. All of them are read-only.
"""

from collections.abc import Callable
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from parkmind.core.contracts import AccessibilityCheck, RideRestriction
from parkmind.services.use_cases.knowledge_queries import KnowledgeHit, KnowledgeQueries
from parkmind.tools.contracts import ToolProvenance, ToolResult
from parkmind.tools.spec import ToolContext, ToolSpec

NodeId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]
CORPUS_SOURCE = "knowledge_corpus"
NOTICE_SOURCE = "park_safety_notice"


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- requests -------------------------------------------------------------------------


class SearchPoliciesRequest(_Request):
    query: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=2, max_length=500)
    ] = Field(
        description="A question in any language, e.g. 'Can a 100 cm child ride Space Mountain?'"
    )
    k: int = Field(default=5, ge=1, le=20, description="How many passages to return.")


class FindSimilarRequest(_Request):
    attraction_id: NodeId | None = Field(
        default=None, description="Find attractions like this one (catalog node id)."
    )
    text: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=2, max_length=500)
        ]
        | None
    ) = Field(default=None, description="Or: describe what the guest likes.")
    k: int = Field(default=5, ge=1, le=20)
    less_intense: bool = Field(
        default=False,
        description="Only attractions with a lower intensity tier (from the park's safety notices).",
    )

    @model_validator(mode="after")
    def _one_reference(self) -> "FindSimilarRequest":
        if (self.attraction_id is None) == (self.text is None):
            raise ValueError("give exactly one of attraction_id or text")
        return self


class CheckAccessibilityRequest(_Request):
    attraction_ids: Annotated[list[NodeId], Field(min_length=1, max_length=100)]
    flags: list[RideRestriction] = Field(
        default_factory=list,
        description="The guest's derived flags (closed set). Empty means no restriction.",
    )
    guest_id: Annotated[str, StringConstraints(min_length=1, max_length=64)] = Field(
        default="guest", description="An opaque label echoed in the results."
    )


# -- responses ------------------------------------------------------------------------


class Passage(BaseModel):
    chunk_id: str
    kind: Literal["policy", "faq", "accessibility", "attraction_profile"]
    title: str
    text: str = Field(description="Quoted from the official park page (Spanish).")
    source_url: str
    reviewed_on: date
    attraction_id: str | None
    score: float


class SearchPoliciesData(BaseModel):
    passages: list[Passage]


class SimilarAttractionData(BaseModel):
    attraction_id: str
    name: str
    intensity: Literal["low", "moderate", "high", "unknown"]
    profile: str
    source_url: str
    score: float


class FindSimilarData(BaseModel):
    attractions: list[SimilarAttractionData]
    reference_intensity: Literal["low", "moderate", "high", "unknown"] | None


class CheckAccessibilityData(BaseModel):
    checks: list[AccessibilityCheck]


# -- tools ----------------------------------------------------------------------------


def _passage(hit: KnowledgeHit) -> Passage:
    chunk = hit.chunk
    return Passage(
        chunk_id=chunk.chunk_id,
        kind=chunk.kind,
        title=chunk.title,
        text=chunk.body,
        source_url=chunk.source_url,
        reviewed_on=chunk.reviewed_on,
        attraction_id=chunk.attraction_id,
        score=hit.score,
    )


def knowledge_tools(ctx: ToolContext) -> list[ToolSpec]:
    queries = KnowledgeQueries(ctx.deps_factory)

    def search_policies(
        request: SearchPoliciesRequest,
    ) -> ToolResult[SearchPoliciesData]:
        answer = queries.search_policies(request.query, request.k)
        return ToolResult[SearchPoliciesData](
            data=SearchPoliciesData(passages=[_passage(h) for h in answer.hits]),
            provenance=ToolProvenance(
                source=CORPUS_SOURCE,
                version=answer.corpus_version,
                strategy=answer.strategy,
                degraded=answer.degraded,
            ),
        )

    def find_similar_attractions(
        request: FindSimilarRequest,
    ) -> ToolResult[FindSimilarData]:
        answer = queries.find_similar_attractions(
            attraction_id=request.attraction_id,
            text=request.text,
            k=request.k,
            less_intense=request.less_intense,
        )
        return ToolResult[FindSimilarData](
            data=FindSimilarData(
                attractions=[
                    SimilarAttractionData(
                        attraction_id=a.hit.chunk.attraction_id or "",
                        name=a.hit.chunk.title,
                        intensity=a.intensity,
                        profile=a.hit.chunk.body,
                        source_url=a.hit.chunk.source_url,
                        score=a.hit.score,
                    )
                    for a in answer.attractions
                ],
                reference_intensity=answer.reference_intensity,
            ),
            provenance=ToolProvenance(
                source=CORPUS_SOURCE,
                version=answer.corpus_version,
                strategy=answer.strategy,
                degraded=answer.degraded,
            ),
        )

    def check_accessibility(
        request: CheckAccessibilityRequest,
    ) -> ToolResult[CheckAccessibilityData]:
        answer = queries.check_accessibility(
            request.attraction_ids, request.flags, request.guest_id
        )
        return ToolResult[CheckAccessibilityData](
            data=CheckAccessibilityData(checks=answer.checks),
            provenance=ToolProvenance(
                source=NOTICE_SOURCE, version=answer.notice_corpus_version
            ),
        )

    def spec(
        name: str,
        description: str,
        request: type[BaseModel],
        data: type[BaseModel],
        handler: Callable[[Any], ToolResult[Any]],
    ) -> ToolSpec:
        return ToolSpec("knowledge", name, description, request, data, handler)

    return [
        spec(
            "search_policies",
            "Passages of the park's published policies and accessibility information that answer "
            "a question (quoted, with their source page).",
            SearchPoliciesRequest,
            SearchPoliciesData,
            search_policies,
        ),
        spec(
            "find_similar_attractions",
            "Attractions like a given one, or like a description; optionally only less intense ones.",
            FindSimilarRequest,
            FindSimilarData,
            find_similar_attractions,
        ),
        spec(
            "check_accessibility",
            "Whether a guest with these restriction flags may ride each attraction, per the park's "
            "safety notices; fails closed when no notice is on file.",
            CheckAccessibilityRequest,
            CheckAccessibilityData,
            check_accessibility,
        ),
    ]
