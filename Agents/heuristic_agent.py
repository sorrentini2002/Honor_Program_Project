import os
import wntr
from .base_agent import BaseAgent

class HeuristicAgent(BaseAgent):
    """A PI Controller agent that smoothly adjusts emergency reservoirs and pump power."""
    
    def __init__(self, water_net, lora_net, threshold=0.90, aggression=5.0, alpha=0.8):
        # Inizializza la classe base con i nuovi parametri (alpha e gamma)
        super().__init__(water_net, lora_net, threshold, aggression, alpha)
        
        # Parametri del regolatore PI
        # Kp reagisce all'errore istantaneo, Ki corregge l'errore nel tempo
        self.Kp = aggression * 0.1
        self.Ki = aggression * 0.01
        self.integral_error = 0.0
        self.current_level = 0.0
        
        # Setup del file di log per la performance dell'agente
        self.log_path = "Log_review/agent_performance.txt"
        log_dir = os.path.dirname(self.log_path)
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("STEP | SATISFACTION | TX_INT | REWARD | ACTION | V_LVL | P_SPD\n")
            f.write("-" * 75 + "\n")
    
    def decide_action(self, step, t, s):
        # 1. Calcolo dell'errore (Deficit rispetto alla soglia desiderata)
        error = self.threshold - s
        
        # 2. Analisi temporale per la ricarica intelligente (Pump Fill)
        hour_of_day = (t % (24 * 3600)) / 3600.0
        is_night = (hour_of_day >= 23 or hour_of_day <= 6)
        
        # 3. Logica PI per il controllo delle valvole (Uscita serbatoi)
        if error > 0:
            self.integral_error += error
        else:
            # Scarichiamo l'integrale gradualmente se la situazione torna normale
            self.integral_error = max(0.0, self.integral_error + error * 0.5) 
            
        pi_output = (self.Kp * max(0, error)) + (self.Ki * self.integral_error)
        target_level = max(0.0, min(1.0, pi_output))
        
        # Smoothing (0.7/0.3) per evitare variazioni idrauliche troppo brusche
        self.current_level = (0.7 * self.current_level) + (0.3 * target_level) 

        # 4. Logica per la potenza delle pompe (Fill Control)
        if error > 0:
            # Durante la crisi spegniamo le pompe per proteggere la pressione di rete
            pump_speed = 0.0
            action_type = "MITIGATE"
        else:
            # Stato normale: ricarichiamo basandoci sull'orario
            action_type = "FILL"
            pump_speed = 1.0 if is_night else 0.3

        # 5. Calcolo intervallo radio LoRa (Cyber-Dynamic)
        # Più l'agente è attivo (valvole aperte o pompe accese), più trasmette spesso
        activity = max(self.current_level, pump_speed * 0.5)
        tx_interval = int(max(300, 3600 - (3300 * activity)))

        # 6. Calcolo della funzione obiettivo (Reward F)
        # s è la soddisfazione (0.0-1.0), tx_interval è il periodo radio
        reward = self.compute_objective(s, tx_interval)

        # 7. LOGGING DELLA DECISIONE
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"{step:<4} | {s*100:<11.1f}% | {tx_interval:<6} | {reward:+.4f} | "
                    f"{action_type:<6} | {self.current_level:<5.2f} | {pump_speed:<5.2f}\n")

        return {
            "type": action_type, 
            "level": self.current_level, 
            "pump_speed": pump_speed,
            "tx_interval": tx_interval
        }

    def apply_mitigation(self, action, sim, lora_net,t):
        # Sincronizzazione parametri radio LoRa
        lora_net.tx_interval_s = action.get("tx_interval", 3600)
        changed = False
        
        # Gestione fisica delle Valvole (Uscita)
        level = action.get("level", 0.0)
        total_tanks = len(self.water_net.iot_valves)
        target_open = int(round(total_tanks * level))
        self.opened_count = target_open

        current_time_s = int(t)
        
        for i, v_name in enumerate(self.water_net.iot_valves):
            valve = sim._wn.get_link(v_name)
            if i < target_open:
                target_setting = wntr.network.elements.LinkStatus.Open
            else:
                target_setting = wntr.network.elements.LinkStatus.Closed
            if getattr(valve, 'initial_setting', None) != target_setting:
                valve._current_logic_setting = target_setting
                changed = True

                control_name = f"AgentCtrl_Valve_{v_name}_{current_time_s}"
                ctrl_action = wntr.network.controls.ControlAction(valve, 'setting', target_setting)
                condition = wntr.network.controls.SimTimeCondition(sim._wn, '=', current_time_s)
                ctrl = wntr.network.controls.Control(condition, ctrl_action, name=control_name)
                sim._wn.add_control(control_name, ctrl)
                
        # Gestione fisica delle Pompe (Ingresso)
        pump_speed = action.get("pump_speed", 0.0)
        for p_name in self.water_net.iot_pumps:
            pump = sim._wn.get_link(p_name)
            if abs(getattr(pump, 'initial_speed', 0.0) - pump_speed) > 0.05:
                pump._current_logic_speed = pump_speed
                changed = True
                control_name = f"AgentCtrl_Pump_{p_name}_{current_time_s}"
                if pump_speed == 0.0:
                    ctrl_action = wntr.network.controls.ControlAction(pump, 'status', wntr.network.elements.LinkStatus.Closed)
                else:
                    ctrl_action = wntr.network.controls.ControlAction(pump, 'setting', pump_speed)

                condition = wntr.network.controls.SimTimeCondition(sim._wn, '=', current_time_s)
                ctrl = wntr.network.controls.Control(condition, action, name=control_name)
                sim._wn.add_control(control_name, ctrl)