class BasePlacementStrategy:
    """Base class for all tank placement strategies."""
    def __init__(self, wn):
        self.wn = wn

    def get_nodes(self, n_tanks):
        """Returns a list of junction names where tanks should be placed."""
        raise NotImplementedError("Each strategy must implement the get_nodes method")
