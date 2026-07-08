import random
from typing import List
from .base_strategy import BasePlacementStrategy

class RandomStrategy(BasePlacementStrategy):
    """Posiziona le cisterne idriche in nodi di giunzione casuali all'interno della rete."""
    
    def get_nodes(self, n_tanks: int) -> List[str]:
        """
        Seleziona un campione casuale di nomi di nodi dalla rete idrica.
        
        Args:
            n_tanks (int): Numero di cisterne da posizionare.
            
        Returns:
            List[str]: Lista dei nomi dei nodi selezionati casualmente.
        """
        # Selezioniamo primariamente i nodi con una domanda effettiva (base_value > 0)
        junctions = [
            j for j in self.wn.junction_name_list
            if self.wn.get_node(j).demand_timeseries_list and 
               self.wn.get_node(j).demand_timeseries_list[0].base_value > 0
        ]
        
        # Fallback se la rete non ha domande attive
        if not junctions:
            junctions = list(self.wn.junction_name_list)
        
        # Evita errori nel caso in cui n_tanks sia maggiore dei nodi disponibili
        return random.sample(junctions, min(n_tanks, len(junctions)))