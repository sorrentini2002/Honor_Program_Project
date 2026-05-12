from .random_strategy import RandomStrategy
from .demand_strategy import DemandStrategy
from .pressure_strategy import PressureStrategy

# Convenience map to access strategies by name
STRATEGY_MAP = {
    'random': RandomStrategy,
    'demand': DemandStrategy,
    'pressure': PressureStrategy
}
