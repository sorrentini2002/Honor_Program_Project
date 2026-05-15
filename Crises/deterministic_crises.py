from .base_crisis import BaseCrisis
import math

class LinearCrisis(BaseCrisis):
    def __init__(self, decay_rate=0.1, min_ratio=0.0, **kwargs):
        super().__init__(decay_rate, **kwargs)
        self.min_ratio = min_ratio

    def get_ratio(self, steps):
        # Calo lineare che si ferma al valore minimo desiderato
        return max(self.min_ratio, 1.0 - (steps * self.decay_rate))

class ExponentialCrisis(BaseCrisis):
    def __init__(self, decay_rate=0.9, min_ratio=0.0, **kwargs):
        super().__init__(decay_rate, **kwargs)
        self.min_ratio = min_ratio

    def get_ratio(self, steps):
        # Calo esponenziale che non scende mai sotto il min_ratio
        val = self.decay_rate ** steps
        return max(self.min_ratio, val)

class InstantCrisis(BaseCrisis):
    def __init__(self, decay_rate=0.0, min_ratio=0.0, **kwargs):
        # In questo caso decay_rate è ignorato, min_ratio è il valore post-crisi
        super().__init__(decay_rate, **kwargs)
        self.min_ratio = min_ratio

    def get_ratio(self, steps):
        # Salto immediato al valore min_ratio
        return self.min_ratio if steps >= 0 else 1.0

class LogarithmicCrisis(BaseCrisis):
    def __init__(self, decay_rate=0.1, min_ratio=0.0, **kwargs):
        super().__init__(decay_rate, **kwargs)
        self.min_ratio = min_ratio

    def get_ratio(self, steps):
        if steps <= 0: return 1.0
        # Calo logaritmico frenato dal min_ratio
        return max(self.min_ratio, 1.0 - self.decay_rate * math.log(steps + 1))
