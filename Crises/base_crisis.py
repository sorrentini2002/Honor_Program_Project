import math

class BaseCrisis:
    """Base class for all Crisis Models."""
    def __init__(self, decay_rate=0.1, **kwargs):
        self.decay_rate = decay_rate
        self.current_ratio = 1.0

    def get_ratio(self, steps_since_start):
        """Must be implemented by child classes."""
        raise NotImplementedError("Each crisis must implement get_ratio")

    def reset(self):
        self.current_ratio = 1.0
