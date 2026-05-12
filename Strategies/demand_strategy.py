from .base_strategy import BasePlacementStrategy

class DemandStrategy(BasePlacementStrategy):
    """Places tanks near junctions with the highest base water demand."""
    def get_nodes(self, n_tanks):
        # Sort junctions by base demand in descending order
        sorted_juncs = sorted(self.wn.junction_name_list, 
                              key=lambda j: self.wn.get_node(j).base_demand, 
                              reverse=True)
        return sorted_juncs[:n_tanks]
