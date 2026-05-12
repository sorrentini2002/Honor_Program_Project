from .deterministic_crises import LinearCrisis, ExponentialCrisis, InstantCrisis, LogarithmicCrisis
from .Ornstein_Uhlenbeck import OrnsteinUhlenbeck

CRISIS_MAP = {
    'linear': LinearCrisis,
    'exponential': ExponentialCrisis,
    'instant': InstantCrisis,
    'logarithmic': LogarithmicCrisis,
    'ornstein_uhlenbeck': OrnsteinUhlenbeck
}
