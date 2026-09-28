from .open_meteo_client import (
    OpenMeteoClient,
    OpenMeteoClientError,
    OpenMeteoNotFoundError,
    OpenMeteoSchemaError,
    OpenMeteoUnavailableError,
)
from .routing_client import (
    InvalidRouteError,
    RouteNotFoundError,
    RoutingClient,
    RoutingClientError,
    RoutingError,
)
from .themeparks_client import (
    ThemeParksClient,
    ThemeParksClientError,
    ThemeParksNotFoundError,
    ThemeParksSchemaError,
    ThemeParksUnavailableError,
)

# Routing exceptions that live at port level — re-exported here for convenience
from parkmind.services.ports import (
    RoutingNotFoundError,
    RoutingSchemaError,
    RoutingUnavailableError,
)

__all__ = [
    "InvalidRouteError",
    "OpenMeteoClient",
    "OpenMeteoClientError",
    "OpenMeteoNotFoundError",
    "OpenMeteoSchemaError",
    "OpenMeteoUnavailableError",
    "RouteNotFoundError",
    "RoutingClient",
    "RoutingClientError",
    "RoutingError",
    "RoutingNotFoundError",
    "RoutingSchemaError",
    "RoutingUnavailableError",
    "ThemeParksClient",
    "ThemeParksClientError",
    "ThemeParksNotFoundError",
    "ThemeParksSchemaError",
    "ThemeParksUnavailableError",
]
