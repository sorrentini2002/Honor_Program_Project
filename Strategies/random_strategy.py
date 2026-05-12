import random
from .base_strategy import BasePlacementStrategy

class RandomStrategy(BasePlacementStrategy):
    """Places tanks at random junctions in the network."""
    def get_nodes(self, n_tanks):
        junctions = self.wn.junction_name_list
        # Ensure we don't try to sample more nodes than available
        return random.sample(junctions, min(n_tanks, len(junctions)))
