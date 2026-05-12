from .base_strategy import BasePlacementStrategy

class PressureStrategy(BasePlacementStrategy):
    """Places tanks at junctions with high elevation (typically low pressure zones)."""
    def get_nodes(self, n_tanks):
        # Sort by elevation (high elevation often correlates with pressure vulnerability)
        sorted_juncs = sorted(self.wn.junction_name_list, 
                              key=lambda j: self.wn.get_node(j).elevation, 
                              reverse=True)
        return sorted_juncs[:n_tanks]
