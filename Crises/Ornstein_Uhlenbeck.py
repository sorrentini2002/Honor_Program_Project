from .base_crisis import BaseCrisis
import random

class OrnsteinUhlenbeck(BaseCrisis):
    """
    Ornstein-Uhlenbeck Process con Target Fisso (mu).
    Simula una crisi che tende a stabilizzarsi attorno a un valore di arrivo.
    """
    def __init__(self, volatility=0.03, reversion_speed=0.1, mu=0.5, **kwargs):
        # Recuperiamo decay_rate se presente, altrimenti 0.0
        decay_rate = kwargs.get('decay_rate', 0.0)
        super().__init__(decay_rate)
        
        # Parametri OU
        self.volatility = volatility
        self.reversion_speed = reversion_speed
        
        # ALIAS: Accettiamo sia 'mu' che 'min_ratio' per il valore di arrivo
        self.mu = kwargs.get('min_ratio', mu)
        
        self.last_ratio = 1.0
        self.last_time = -1.0

    def get_ratio(self, time_hours):
        if time_hours <= 0:
            self.last_ratio = 1.0
            self.last_time = -1.0
            return 1.0
        
        if time_hours > self.last_time:
            # Calcolo del dt in ore, partendo da 0
            if self.last_time < 0:
                dt = time_hours
            else:
                dt = time_hours - self.last_time
                
            # Il sistema cerca di tornare verso il valore 'mu' (o 'min_ratio')
            drift = self.reversion_speed * (self.mu - self.last_ratio) * dt
            
            # Shock casuale scalato per sqrt(dt)
            import math
            shock = random.gauss(0, self.volatility) * math.sqrt(dt) if dt > 0 else 0
            
            # Aggiornamento dello stato
            new_ratio = self.last_ratio + drift + shock
            
            # Clamp per evitare valori fisicamente impossibili (sotto lo zero o sopra 1)
            self.last_ratio = max(0.0, min(1.0, new_ratio))
            self.last_time = time_hours
            
        return self.last_ratio
