from .base_crisis import BaseCrisis
import math

class PumpTestCrisis(BaseCrisis):
    """
    Crisis model for testing pumps.
    Phase 1: Linear decay until recovery_hour.
    Phase 2: Recovery (instant or gradual) to full pressure (1.0).
    Phase 3: Maintain full pressure for recovery_duration_hours.
    Phase 4: Repeat.
    """
    def __init__(self, decay_rate=0.1, min_ratio=0.025, 
                 recovery_hour=21.0, recovery_duration_hours=12.0, 
                 crisis_start_hour=3.0, step_min=5,
                 recovery_type='instant', recovery_rate=0.1):
        super().__init__(decay_rate)
        self.min_ratio = min_ratio
        
        # Calcolo degli step relativi basandoci sulle ore fornite
        # Quante ore passano dall'inizio della crisi (crisis_start_hour) al momento del recupero (recovery_hour)?
        hours_in_crisis = max(0, recovery_hour - crisis_start_hour)
        self.steps_to_recovery = int(hours_in_crisis * 60 / step_min)
        self.recovery_duration_steps = int(recovery_duration_hours * 60 / step_min)
        
        self.recovery_type = recovery_type
        self.recovery_rate = recovery_rate

    def get_ratio(self, steps):
        # Valore raggiunto alla fine della fase di crisi
        ratio_at_end_of_crisis = max(self.min_ratio, 1.0 - (self.steps_to_recovery * self.decay_rate))
        
        # Step necessari per tornare a 1.0
        if self.recovery_type == 'instant':
            steps_to_full = 0
        else:
            gap = 1.0 - ratio_at_end_of_crisis
            if self.recovery_rate > 0:
                steps_to_full = math.ceil(gap / self.recovery_rate)
            else:
                steps_to_full = 0
            
        # Durata totale del ciclo
        total_cycle = self.steps_to_recovery + steps_to_full + self.recovery_duration_steps
        local_step = steps % total_cycle
        
        if local_step < self.steps_to_recovery:
            # Fase 1: Calo lineare
            return max(self.min_ratio, 1.0 - (local_step * self.decay_rate))
        elif local_step < self.steps_to_recovery + steps_to_full:
            # Fase 2: Recupero graduale
            steps_since_recovery_start = local_step - self.steps_to_recovery
            return min(1.0, ratio_at_end_of_crisis + (steps_since_recovery_start * self.recovery_rate))
        else:
            # Fase 3: Mantenimento pressione piena
            return 1.0
