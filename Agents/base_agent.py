class BaseAgent:
    """Abstract Base Class for all Crisis Management Agents."""
    def __init__(self, water_net, lora_net, threshold=0.90, aggression=1.5, alpha = 0.5):
        self.water_net = water_net
        self.lora_net = lora_net
        self.threshold = threshold
        self.aggression = aggression
        self.alpha = alpha
        self.gamma = 1.0 - alpha  
        self.opened_count = 0

    def calculate_current_satisfaction(self, sim):
            """Calculates current network satisfaction ratio (Actual/Expected)."""
            res = sim.node_res
            vals = []
            
            for j in res['expected_demand']:
                if len(res['expected_demand'][j]) > 0 and res['expected_demand'][j][-1] > 0:
                    exp = res['expected_demand'][j][-1]
                    act = res['demand'][j][-1]
                    sat = act / exp
                    vals.append(sat)
                    
                    # --- TRUCCO DA DETECTIVE ---
                    # Stampiamo a schermo chi sta soffrendo prima della crisi (es. nei primi 15 step)
                    current_step = len(res['expected_demand'][j])
                    if sat < 0.95 and current_step < 15:
                        node = sim._wn.get_node(j)
                        # Calcoliamo la pressione reale (Head - Elevazione)
                        press_m = res['head'][j][-1] - node.elevation
                        print(f"[ALLARME NODO {j}] Step {current_step} -> Pressione: {press_m:.2f}m, Soddisfazione: {sat*100:.1f}%")
                    # ---------------------------

            return sum(vals) / len(vals) if vals else 1.0

    def compute_objective(self, s, tx_interval):
        """Calculates the current performance reward (F)."""
        frequency_cost = 60.0 / tx_interval
        return (self.alpha * s) - (self.gamma * frequency_cost)

    def decide_action(self, step, t, s):
        """Must be implemented by child classes."""
        raise NotImplementedError("Each agent must implement decide_action")

    def apply_mitigation(self, action, sim, lora_net):
        """Must be implemented by child classes."""
        raise NotImplementedError("Each agent must implement apply_mitigation")
