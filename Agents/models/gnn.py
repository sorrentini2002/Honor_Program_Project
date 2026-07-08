import torch
import torch.nn as nn
import torch.nn.functional as F

class WaterNetworkGNN(nn.Module):
    """
    GNN leggera per sintetizzare lo stato della rete idrica.
    Prende in input le feature dei nodi e la matrice di adiacenza,
    e restituisce una rappresentazione aggiornata (Belief State) di tutti i nodi.
    """
    def __init__(self, in_features=4, hidden_features=16, out_features=2):
        super(WaterNetworkGNN, self).__init__()
        # CORREZIONE BUG: l'input è in_features * 2 a causa del torch.cat nel forward
        self.lin1 = nn.Linear(in_features * 2, hidden_features)
        # Layer 2: Produce l'output finale (es. [pressione_stimata, deficit_stimato])
        self.lin2 = nn.Linear(hidden_features, out_features)

    def forward(self, x, adj_matrix):
        """
        x: Tensor [num_nodes, in_features] (Feature attuali + maschera di validità)
        adj_matrix: Tensor [num_nodes, num_nodes] (Matrice di adiacenza normalizzata)
        """
        # 1. Message Passing: ogni nodo aggrega le feature dei suoi vicini
        neighbor_agg = torch.matmul(adj_matrix, x)
        
        # 2. Combinazione: feature del nodo + feature dei vicini (Concatena sull'asse delle feature)
        combined = torch.cat([x, neighbor_agg], dim=1)
        
        # 3. Trasformazione non lineare
        h = F.relu(self.lin1(combined))
        out = self.lin2(h)
        
        return out