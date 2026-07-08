import math
from .base_crisis import BaseCrisis

class LinearCrisis(BaseCrisis):
    """Simula un calo lineare della pressione/capacità idrica alla sorgente."""
    
    def __init__(self, decay_rate: float = 0.1, min_ratio: float = 0.0, **kwargs):
        super().__init__(decay_rate, **kwargs)
        self.min_ratio = min_ratio

    def get_ratio(self, time_hours: float) -> float:
        # Calo lineare che si arresta al valore minimo di sicurezza desiderato
        return max(self.min_ratio, 1.0 - (time_hours * self.decay_rate))


class ExponentialCrisis(BaseCrisis):
    """Simula un calo esponenziale della pressione/capacità idrica alla sorgente."""
    
    def __init__(self, decay_rate: float = 0.9, min_ratio: float = 0.0, **kwargs):
        super().__init__(decay_rate, **kwargs)
        self.min_ratio = min_ratio

    def get_ratio(self, time_hours: float) -> float:
        # Calo esponenziale attenuato che non scende mai sotto il min_ratio
        val = self.decay_rate ** time_hours
        return max(self.min_ratio, val)


class InstantCrisis(BaseCrisis):
    """Simula un crollo immediato e istantaneo a gradino della capacità idrica."""
    
    def __init__(self, decay_rate: float = 0.0, min_ratio: float = 0.0, **kwargs):
        # Il parametro decay_rate viene ignorato nelle crisi istantanee
        super().__init__(decay_rate, **kwargs)
        self.min_ratio = min_ratio

    def get_ratio(self, time_hours: float) -> float:
        # Salto immediato al valore post-crisi (min_ratio) appena inizia l'evento
        return self.min_ratio if time_hours >= 0 else 1.0


class LogarithmicCrisis(BaseCrisis):
    """Simula un calo logaritmico della pressione/capacità idrica alla sorgente."""
    
    def __init__(self, decay_rate: float = 0.1, min_ratio: float = 0.0, **kwargs):
        super().__init__(decay_rate, **kwargs)
        self.min_ratio = min_ratio

    def get_ratio(self, time_hours: float) -> float:
        if time_hours <= 0: 
            return 1.0
        # Calo logaritmico (basato su logaritmo naturale) frenato dal min_ratio
        return max(self.min_ratio, 1.0 - self.decay_rate * math.log(time_hours + 1))