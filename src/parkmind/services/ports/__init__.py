"""services/ports — Protocol definitions only.

Convention (P0-07 establishes this; P0-08 adds WeatherPort, P0-09 adds
RoutingPort): one Protocol per module, named after the port (park_data.py,
weather.py, routing.py, ...), re-exported here so callers do
`from parkmind.services.ports import ParkDataPort`. No implementation code
lives in this package — it is the seam .importlinter's core-forbidden-imports
contract protects.
"""

from .park_data import ParkDataPort

__all__ = ["ParkDataPort"]
