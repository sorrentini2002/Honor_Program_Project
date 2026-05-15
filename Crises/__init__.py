from .deterministic_crises import LinearCrisis, ExponentialCrisis, InstantCrisis, LogarithmicCrisis
from .Ornstein_Uhlenbeck import OrnsteinUhlenbeck
from .test_crises import PumpTestCrisis

CRISIS_MAP = {
    'linear': LinearCrisis,
    'exponential': ExponentialCrisis,
    'instant': InstantCrisis,
    'logarithmic': LogarithmicCrisis,
    'ornstein_uhlenbeck': OrnsteinUhlenbeck,
    'pump_test': PumpTestCrisis
}
