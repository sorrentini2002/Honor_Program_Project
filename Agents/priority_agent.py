import os
import math
import mwntr
from mwntr.network.controls import (
    Control, ControlAction, SimTimeCondition, ControlPriority
)
from mwntr.network.elements import LinkStatus
from .base_agent import BaseAgent

class PriorityAgent(BaseAgent):
    """
    Agente idraulico basato sulla priorità (Priority-Driven).
    - Gestisce cisterne IoT e valvole di isolamento (TCV) simultaneamente.
    - Priorità ai nodi con tag USER_1_P.
    - In crisi: apre cisterne e chiude gradualmente (o nettamente) le valvole di isolamento.
    """
    
    def __init__(self, water_net, lora_net,
                 threshold: float = 0.90,
                 aggression: float = 1.0,
                 alpha: float = 0.80,
                 crisis_start_time_s: float = 0.0):
        super().__init__(water_net, lora_net, threshold, aggression, alpha)
        self.crisis_start_time_s = crisis_start_time_s
        
        # ── NODI PRIORITARI E VALVOLE DI ISOLAMENTO ──
        self.priority_nodes = ["8640", "8696", "8642"]
        self.isolation_valves = ["10147", "10193", "10203"]
        
        # ── PARAMETRI IDRAULICI CISTERNE IOT ─
        self.VALVE_MAX_OPENING = 1.0
        self.LOSS_COEFF_MIN_IOT = 50.0
        self.LOSS_COEFF_MAX_IOT = 50000.0
        
        # ── PARAMETRI IDRAULICI VALVOLE ISOLAMENTO (TCV) ──
        self.K_ISO_MIN = 0.0    
        self.K_ISO_MAX = 50000.0
        # Soglia per la chiusura netta (replica la logica delle cisterne)
        # Se k_val >= 10000, la valvola viene fisicamente chiusa (LinkStatus(0))
        self.K_ISO_CLOSE_THRESHOLD = 10000.0 

        self.TX_INTERVAL_NOMINAL = 3600
        self.TX_INTERVAL_ALERT = 300

        self.current_valve_level = 0.0
        self.current_valve_levels = {}
        self.opened_count = 0

        # Log
        self.log_path = "Log_review/agent_performance.txt"
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "w") as f:
            f.write("STEP | SAT_PRI | DEFICIT | IOT_OPEN | ISO_K | TX_INT | IOT_LOSS\n")
            f.write("-" * 80 + "\n")

    def decide_action(self, step: int, t: float, s_current: float, sim=None) -> dict:
        # ── PRE-CRISI: Cisterne CHIUSE, Valvole Isolamento APERTE (K=0) ──
        if t < self.crisis_start_time_s:
            # Resetta lo stato di memoria
            self._valves_closed_since = None
            return {
                "level": 0.0,
                "tx_interval": self.TX_INTERVAL_NOMINAL,
                "loss_coeff": self.LOSS_COEFF_MAX_IOT,
                "open_valve": False,
                "isolation_valves": {v: self.K_ISO_MIN for v in self.isolation_valves},
                "pump_speed": 0.0,
                "step": step,
            }

        # ── DURANTE LA CRISI: Calcola deficit sui nodi prioritari ──
        deficit = max(0.0, self.threshold - s_current)
        
        # ─ ISTERESI: Soglia di disattivazione più bassa ──
        # Se le valvole sono già chiuse, usa una soglia più bassa per riaprirle
        # Questo evita lo sfarfallio
        DEACTIVATION_THRESHOLD = 0.95  # Soglia più alta per "smettere di agire"
        
        # Inizializza la variabile di memoria se non esiste
        if not hasattr(self, '_valves_closed_since'):
            self._valves_closed_since = None
        
        # Calcola se c'è deficit considerando l'isteresi
        if self._valves_closed_since is not None:
            # Valvole già chiuse: usa soglia più alta per mantenerle chiuse
            effective_threshold = self.threshold * 1.05  # 5% di margine
        else:
            # Valvole aperte: usa soglia normale
            effective_threshold = self.threshold
        
        deficit = max(0.0, effective_threshold - s_current)

        if deficit > 0:
            deficit_norm = min(1.0, deficit / self.threshold)
            
            # 1. Apertura Cisterne IoT
            self.current_valve_level = deficit_norm * self.aggression * self.VALVE_MAX_OPENING
            tx_interval = self.TX_INTERVAL_ALERT
            open_valve = True
            
            # 2. Chiusura Graduale Valvole Isolamento con aggravamento temporale
            k_iso = self.K_ISO_MIN + deficit_norm * (self.K_ISO_MAX - self.K_ISO_MIN)
            
            # Aggravamento temporale: più passa il tempo, più k_iso si avvicina a K_ISO_MAX
            crisis_hours = max(0.0, (t - self.crisis_start_time_s) / 3600.0)
            time_factor = min(1.0, crisis_hours / 3.0)  # 3 ore per chiusura completa
            k_iso = k_iso + (self.K_ISO_MAX - k_iso) * time_factor
            
            # Marca il momento in cui le valvole sono state chiuse
            if self._valves_closed_since is None:
                self._valves_closed_since = t
        else:
            # ── LOGICA DI DISATTIVAZIONE CON MEMORIA ──
            # Se le valvole sono state chiuse, mantienile chiuse per almeno 2 ore
            MIN_CLOSED_DURATION_HOURS = 2.0
            
            if self._valves_closed_since is not None:
                hours_since_closed = (t - self._valves_closed_since) / 3600.0
                
                if hours_since_closed < MIN_CLOSED_DURATION_HOURS:
                    # Mantieni le valvole chiuse anche se il deficit è zero
                    # Usa un deficit fittizio per mantenere k_iso alto
                    deficit_norm = 0.5  # Valore intermedio
                    self.current_valve_level = deficit_norm * self.aggression * self.VALVE_MAX_OPENING * 0.3  # Riduci apertura cisterne
                    tx_interval = self.TX_INTERVAL_ALERT
                    open_valve = True
                    k_iso = self.K_ISO_MAX * 0.7  # Mantieni valvole quasi chiuse
                else:
                    # Puoi riaprire le valvole
                    self.current_valve_level = 0.0
                    tx_interval = self.TX_INTERVAL_NOMINAL
                    open_valve = False
                    k_iso = self.K_ISO_MIN
                    self._valves_closed_since = None  # Resetta la memoria
            else:
                # Valvole già aperte, nessun deficit
                self.current_valve_level = 0.0
                tx_interval = self.TX_INTERVAL_NOMINAL
                open_valve = False
                k_iso = self.K_ISO_MIN

        # ── SOFT-EMPTY PROTECTION (Cisterne) ──
        if sim is not None and hasattr(sim, '_wn') and open_valve:
            for t_name in sim._wn.tank_name_list:
                try:
                    tank = sim._wn.get_node(t_name)
                    current_level = tank.head - tank.elevation
                    min_level = tank.min_level
                    max_level = tank.max_level
                    usable_range = max_level - min_level
                    
                    if usable_range > 0:
                        fill_ratio = (current_level - min_level) / usable_range
                        if fill_ratio < 0.15:
                            open_valve = False
                            self.current_valve_level = 0.0
                            # k_iso rimane invariato (valvole di isolamento restano chiuse)
                            break
                except Exception:
                    pass

        # ── MAPPATURA LOGARITMICA: Apertura Cisterna -> Loss Coefficient ──
        if self.current_valve_level > 0.001:
            ratio = self.current_valve_level / self.VALVE_MAX_OPENING
            ratio = max(0.001, min(1.0, ratio))
            log_min = math.log(self.LOSS_COEFF_MIN_IOT)
            log_max = math.log(self.LOSS_COEFF_MAX_IOT)
            loss_coeff = math.exp(log_max - ratio * (log_max - log_min))
        else:
            loss_coeff = self.LOSS_COEFF_MAX_IOT

        loss_coeff = max(self.LOSS_COEFF_MIN_IOT, min(self.LOSS_COEFF_MAX_IOT, loss_coeff))

        # Log
        with open(self.log_path, "a") as f:
            f.write(f"{step:4d} | {s_current:.4f} | {deficit:.4f} | "
                    f"{self.current_valve_level:.4f} | {k_iso:.2f} | {tx_interval} | "
                    f"{loss_coeff:.2f} | {self._valves_closed_since}\n")

        # ── DIZIONARIO AZIONE ──
        return {
            "level": self.current_valve_level,
            "tx_interval": tx_interval,
            "loss_coeff": loss_coeff,
            "open_valve": open_valve,
            "isolation_valves": {v: k_iso for v in self.isolation_valves},
            "pump_speed": 0.0,
            "step": step,
        }

    def compute_action(self, state: dict, t: float = 0.0) -> dict:
        step = state.get("step", 0)
        s_current = state.get("satisfaction", 1.0)
        return self.decide_action(step, t, s_current)

    def _cleanup_agent_controls(self, sim):
        """Rimuove vecchi controlli AgentCtrl_ da WN e dai checker."""
        for mgr in [sim._presolve_controls, sim._postsolve_controls,
                    sim._rules, sim._feasibility_controls]:
            to_remove = [c for c in mgr._controls
                         if hasattr(c, '_name') and c._name
                         and c._name.startswith("AgentCtrl_")]
            for ctrl in to_remove:
                mgr.deregister(ctrl)
                try:
                    sim._change_tracker.deregister(ctrl)
                except Exception:
                    pass

        old_names = [n for n in sim._wn.control_name_list
                     if n.startswith("AgentCtrl_")]
        for name in old_names:
            sim._wn.remove_control(name)

    def apply_mitigation(self, action: dict, sim, lora_net, t=None):
        import math
        lora_net.tx_interval_s = action.get("tx_interval", self.TX_INTERVAL_NOMINAL)
        loss_coeff = action.get("loss_coeff", self.LOSS_COEFF_MAX_IOT)
        open_valve = action.get("open_valve", False)
        isolation_settings = action.get("isolation_valves", {})

        self.opened_count = 0
        g = 9.81

        # ── 1. GESTIONE CISTERNE IOT (Logica binaria OPEN/CLOSED) ──
        for v_name in self.water_net.iot_valves:
            valve = sim._wn.get_link(v_name)

            if open_valve and loss_coeff < self.LOSS_COEFF_MAX_IOT:
                valve._user_status = LinkStatus(1)
                valve._internal_status = LinkStatus(1)
                valve._setting = loss_coeff
                self.current_valve_levels[v_name] = action.get("level", 0.0)
                self.opened_count += 1
            else:
                valve._user_status = LinkStatus(0) # Closed
                valve._internal_status = LinkStatus(0)
                valve._setting = self.LOSS_COEFF_MAX_IOT
                self.current_valve_levels[v_name] = 0.0

            # Aggiorna AML per cisterne
            try:
                sim._model_updater.update(sim._model, sim._wn, valve, 'status')
                sim._model_updater.update(sim._model, sim._wn, valve, 'setting')
            except Exception:
                pass
            
            try:
                if hasattr(sim._model, 'valve_setting') and v_name in sim._model.valve_setting:
                    sim._model.valve_setting[v_name].value = valve._setting
                if hasattr(sim._model, 'tcv_resistance') and v_name in sim._model.tcv_resistance:
                    K = valve._setting
                    D = valve.diameter
                    r = 8.0 * K / (g * math.pi**2 * D**4)
                    sim._model.tcv_resistance[v_name].value = r
            except Exception:
                pass

        # ── 2. GESTIONE VALVOLE DI ISOLAMENTO (TCV) - LOGICA AGGIORNATA ──
        # Replica la dinamica delle cisterne: se k_val è troppo alto, chiudi fisicamente la valvola
        for v_name, k_val in isolation_settings.items():
            try:
                valve = sim._wn.get_link(v_name)
                
                # LOGICA BINARIA OPEN/CLOSED
                if k_val < self.K_ISO_CLOSE_THRESHOLD:
                    # Throttling graduale: Valvola APERTA con resistenza K
                    valve._user_status = LinkStatus(1)
                    valve._internal_status = LinkStatus(1)
                    valve._setting = k_val
                else:
                    # Isolamento totale: Valvola CHIUSA fisicamente (flusso = 0)
                    valve._user_status = LinkStatus(0)
                    valve._internal_status = LinkStatus(0)
                    valve._setting = self.K_ISO_MAX # Valore fittizio alto

                # Aggiorna AML per valvole di isolamento
                try:
                    sim._model_updater.update(sim._model, sim._wn, valve, 'status')
                    sim._model_updater.update(sim._model, sim._wn, valve, 'setting')
                except Exception:
                    pass
                
                try:
                    if hasattr(sim._model, 'valve_setting') and v_name in sim._model.valve_setting:
                        sim._model.valve_setting[v_name].value = valve._setting
                    
                    if hasattr(sim._model, 'tcv_resistance') and v_name in sim._model.tcv_resistance:
                        D = valve.diameter
                        if D > 0:
                            # Se chiusa, usa una resistenza altissima per il solver
                            effective_K = 1e8 if valve._user_status == LinkStatus(0) else k_val
                            r = 8.0 * effective_K / (g * math.pi**2 * D**4)
                            sim._model.tcv_resistance[v_name].value = r
                except Exception:
                    pass
            except Exception:
                pass

        # ─ 3. POMPE SEMPRE SPENTE ─
        for p_name in self.water_net.iot_pumps:
            pump = sim._wn.get_link(p_name)
            pump._user_status = LinkStatus(0)
            pump._internal_status = LinkStatus(0)
            try:
                sim._model_updater.update(sim._model, sim._wn, pump, 'status')
            except Exception:
                pass