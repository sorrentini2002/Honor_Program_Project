from .deterministic_crises import LinearCrisis, ExponentialCrisis, InstantCrisis, LogarithmicCrisis

# Mappa di convenienza per accedere ai modelli di crisi tramite stringa
CRISIS_MAP = {
    'linear': LinearCrisis,
    'exponential': ExponentialCrisis,
    'instant': InstantCrisis,
    'logarithmic': LogarithmicCrisis,

}