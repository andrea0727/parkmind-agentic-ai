"""``data.*`` tools (P0-25; Architecture section 29).

``get_live_waits``, ``get_attraction_status``, ``get_schedule``,
``get_showtimes``, ``get_weather``, ``get_walking_time``,
``get_attraction_info``. Each handler is one call to ``ParkDataQueries``; the
answers are section 33 contracts, so no provider format crosses the MCP
contract. Snapshot-backed answers carry the snapshot id, its age and a
``stale`` flag in their provenance. All of them are read-only.
"""

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from parkmind.core.contracts import (
    PARK_TZ,
    Attraction,
    AttractionStatus,
    DataSource,
    Park,
    WaitEstimate,
    WeatherHour,
)
from parkmind.services.use_cases.park_data_queries import ParkDataQueries, SnapshotStamp
from parkmind.tools.contracts import ToolProvenance, ToolResult
from parkmind.tools.spec import ToolContext, ToolSpec

NodeId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]
NodeIds = Annotated[list[NodeId], Field(min_length=1, max_length=200)]

ROUTING_SOURCE = "parkmind_routing"


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- requests -------------------------------------------------------------------------


class AttractionIdsRequest(_Request):
    attraction_ids: NodeIds | None = Field(
        default=None,
        description="Catalog node ids (see data.get_attraction_info). Omit for every one.",
    )


class ShowIdsRequest(_Request):
    show_ids: NodeIds | None = Field(
        default=None,
        description="Catalog node ids of shows and meet-and-greets. Omit for every scheduled one.",
    )


class ScheduleRequest(_Request):
    service_date: date | None = Field(
        default=None, description="Park calendar date (YYYY-MM-DD). Omit for today."
    )


class WeatherRequest(_Request):
    start: AwareDatetime | None = Field(
        default=None,
        description="Start of the interval (ISO 8601 with offset). Omit for now.",
    )
    end: AwareDatetime | None = Field(
        default=None, description="End of the interval. Omit for 12 hours after start."
    )

    @model_validator(mode="after")
    def _ordered(self) -> "WeatherRequest":
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("end must not be before start")
        return self


class WalkingTimeRequest(_Request):
    origin_node_id: NodeId
    destination_node_id: NodeId


# -- responses ------------------------------------------------------------------------


class LiveWaitsData(BaseModel):
    waits: list[WaitEstimate]
    unknown_ids: list[str] = Field(description="Not in the park catalog.")
    no_reading_ids: list[str] = Field(
        description="In the catalog, but no standby wait in this snapshot."
    )


class AttractionStatusData(BaseModel):
    statuses: dict[str, AttractionStatus]
    unknown_ids: list[str]
    no_reading_ids: list[str]


class ScheduleData(BaseModel):
    park: Park


class ShowtimesData(BaseModel):
    showtimes: dict[str, list[datetime]] = Field(
        description="Scheduled performances only: availability windows and ticketed events are excluded."
    )
    unknown_ids: list[str]
    no_reading_ids: list[str]


class WeatherData(BaseModel):
    hours: list[WeatherHour]


class WalkingTimeData(BaseModel):
    minutes: float | None = Field(
        description="None when there is no basis for an estimate."
    )
    basis: Literal["identity", "curated", "estimated", "unknown"]


class AttractionInfoData(BaseModel):
    attractions: list[Attraction]
    unknown_ids: list[str]


# -- tools ----------------------------------------------------------------------------


def _from_snapshot(stamp: SnapshotStamp, source: DataSource) -> ToolProvenance:
    degraded = None
    if source not in stamp.data_sources:
        degraded = f"{source.value}_not_in_snapshot"
    return ToolProvenance(
        source=source.value,
        snapshot_id=stamp.snapshot_id,
        retrieved_at=stamp.retrieved_at,
        age_seconds=stamp.age.total_seconds(),
        stale=stamp.stale,
        strategy=stamp.origin,
        degraded=degraded,
    )


def data_tools(ctx: ToolContext) -> list[ToolSpec]:
    queries = ParkDataQueries(ctx.deps_factory)

    def get_live_waits(request: AttractionIdsRequest) -> ToolResult[LiveWaitsData]:
        answer = queries.live_waits(request.attraction_ids, ctx.clock())
        lookup = answer.value
        return ToolResult[LiveWaitsData](
            data=LiveWaitsData(
                waits=lookup.found,
                unknown_ids=lookup.unknown_ids,
                no_reading_ids=lookup.no_reading_ids,
            ),
            provenance=_from_snapshot(answer.stamp, DataSource.THEMEPARKS_WIKI),
        )

    def get_attraction_status(
        request: AttractionIdsRequest,
    ) -> ToolResult[AttractionStatusData]:
        answer = queries.attraction_status(request.attraction_ids, ctx.clock())
        lookup = answer.value
        return ToolResult[AttractionStatusData](
            data=AttractionStatusData(
                statuses=lookup.found,
                unknown_ids=lookup.unknown_ids,
                no_reading_ids=lookup.no_reading_ids,
            ),
            provenance=_from_snapshot(answer.stamp, DataSource.THEMEPARKS_WIKI),
        )

    def get_schedule(request: ScheduleRequest) -> ToolResult[ScheduleData]:
        on_date = request.service_date or ctx.clock().astimezone(PARK_TZ).date()
        return ToolResult[ScheduleData](
            data=ScheduleData(park=queries.schedule(on_date)),
            provenance=ToolProvenance(source=DataSource.THEMEPARKS_WIKI.value),
        )

    def get_showtimes(request: ShowIdsRequest) -> ToolResult[ShowtimesData]:
        answer = queries.showtimes(request.show_ids, ctx.clock())
        lookup = answer.value
        return ToolResult[ShowtimesData](
            data=ShowtimesData(
                showtimes=lookup.found,
                unknown_ids=lookup.unknown_ids,
                no_reading_ids=lookup.no_reading_ids,
            ),
            provenance=_from_snapshot(answer.stamp, DataSource.THEMEPARKS_WIKI),
        )

    def get_weather(request: WeatherRequest) -> ToolResult[WeatherData]:
        answer = queries.weather(ctx.clock(), request.start, request.end)
        return ToolResult[WeatherData](
            data=WeatherData(hours=answer.value),
            provenance=_from_snapshot(answer.stamp, DataSource.OPEN_METEO),
        )

    def get_walking_time(request: WalkingTimeRequest) -> ToolResult[WalkingTimeData]:
        estimate = queries.walking_time(
            request.origin_node_id, request.destination_node_id
        )
        return ToolResult[WalkingTimeData](
            data=WalkingTimeData(minutes=estimate.minutes, basis=estimate.basis),
            provenance=ToolProvenance(source=ROUTING_SOURCE, strategy=estimate.basis),
        )

    def get_attraction_info(
        request: AttractionIdsRequest,
    ) -> ToolResult[AttractionInfoData]:
        lookup = queries.attraction_info(request.attraction_ids)
        return ToolResult[AttractionInfoData](
            data=AttractionInfoData(
                attractions=lookup.found, unknown_ids=lookup.unknown_ids
            ),
            provenance=ToolProvenance(
                source=DataSource.THEMEPARKS_WIKI.value, strategy="catalog"
            ),
        )

    def spec(
        name: str,
        description: str,
        request: type[BaseModel],
        data: type[BaseModel],
        handler: Callable[[Any], ToolResult[Any]],
    ) -> ToolSpec:
        return ToolSpec("data", name, description, request, data, handler)

    return [
        spec(
            "get_live_waits",
            "Current standby wait (minutes) and status per attraction, from the latest park snapshot.",
            AttractionIdsRequest,
            LiveWaitsData,
            get_live_waits,
        ),
        spec(
            "get_attraction_status",
            "Current status per attraction: OPERATING, DOWN, CLOSED or REFURBISHMENT.",
            AttractionIdsRequest,
            AttractionStatusData,
            get_attraction_status,
        ),
        spec(
            "get_schedule",
            "The park's operating hours on a date.",
            ScheduleRequest,
            ScheduleData,
            get_schedule,
        ),
        spec(
            "get_showtimes",
            "Today's scheduled performance start times per show (not open windows or ticketed events).",
            ShowIdsRequest,
            ShowtimesData,
            get_showtimes,
        ),
        spec(
            "get_weather",
            "Hourly weather (condition, temperature, chance of rain) over an interval of the park day.",
            WeatherRequest,
            WeatherData,
            get_weather,
        ),
        spec(
            "get_walking_time",
            "Walking minutes between two park nodes, with how it was obtained; unknown instead of a guess.",
            WalkingTimeRequest,
            WalkingTimeData,
            get_walking_time,
        ),
        spec(
            "get_attraction_info",
            "Catalog metadata per attraction: name, category, land, height limit, typical wait, outdoor.",
            AttractionIdsRequest,
            AttractionInfoData,
            get_attraction_info,
        ),
    ]
