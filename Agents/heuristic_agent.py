import os
import math
import mwntr
from mwntr.network.controls import (
    Control, ControlAction, SimTimeCondition, ControlPriority
)
from mwntr.network.elements import LinkStatus
from .base_agent import BaseAgent


class HeuristicAgent(BaseAgent):
    """
    Agente idraulico definitivo per Dyn-WNTR.
    - Valvola fisicamente CLOSED fino all'apertura tramite Control WNTR.
    - NESSUN rebuild del modello idraulico.
    - Priorità VERY_HIGH per evitare conflitti con WNTR.
    """

    def __init__(self, water_net, lora_net,
                 threshold: float = 0.90,
                 aggression: float = 1.0,
                 alpha: float = 0.80,
                 crisis_start_time_s: float = 0.0):
        super().__init__(water_net, lora_net, threshold, aggression, alpha)
        self.crisis_start_time_s = crisis_start_time_s

        # ── PARAMETRI IDRAULICI CORRETTI ──
        self.VALVE_MAX_OPENING = 1.0
        # K=50 permette un flusso di ~15-20 L/s con 30m di dislivello
        # K=100000 è sufficientemente chiuso per evitare perdite
        self.LOSS_COEFF_MIN = 50.0        # ← ABBASSATO: era 500 (troppo strozzato)
        self.LOSS_COEFF_MAX = 50000.0     # ← ABBASSATO: era 1e8 (causava singolarità)
        
        self.TX_INTERVAL_NOMINAL = 3600
        self.TX_INTERVAL_ALERT = 300

        self.current_valve_level = 0.0
        self.current_valve_levels = {}
        self.opened_count = 0

        # Log
        self.log_path = "Log_review/agent_performance.txt"
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "w") as f:
            f.write("STEP | SAT | DEFICIT | OPENING | TX_INT | LOSS_COEFF\n")
            f.write("-" * 70 + "\n")

    def decide_action(self, step: int, t: float, s_current: float, sim=None) -> dict:
        # ── PRE-CRISI: valvola rimane fisicamente CLOSED ──
        if t < self.crisis_start_time_s:
            return {
                "level": 0.0,
                "tx_interval": self.TX_INTERVAL_NOMINAL,
                "loss_coeff": self.LOSS_COEFF_MAX,
                "open_valve": False,
                "pump_speed": 0.0,
                "step": step,
            }

        # ── DURANTE LA CRISI: calcola apertura ──
        deficit = max(0.0, self.threshold - s_current)

        if deficit > 0:
            deficit_norm = min(1.0, deficit / self.threshold)
            self.current_valve_level = deficit_norm * self.aggression * self.VALVE_MAX_OPENING
            tx_interval = self.TX_INTERVAL_ALERT
            open_valve = True
        else:
            self.current_valve_level = 0.0
            tx_interval = self.TX_INTERVAL_NOMINAL
            open_valve = False

        # ── SOFT-EMPTY PROTECTION (SEMPLIFICATA E MENO AGGRESSIVA) ──
        # Interviene SOLO se il tank è davvero quasi vuoto (< 15% utile)
        # Non strozza gradualmente, chiude solo in emergenza per evitare sfarfallii
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
                        # Soglia di sicurezza abbassata al 15% per evitare interventi prematuri
                        if fill_ratio < 0.15:
                            open_valve = False
                            self.current_valve_level = 0.0
                            break
                except Exception:
                    pass


        # ── MAPPATURA LOGARITMICA: Apertura -> Loss Coefficient ──
        # Questo evita il problema dei valori estremi del mapping lineare
        if self.current_valve_level > 0.001:
            ratio = self.current_valve_level / self.VALVE_MAX_OPENING
            ratio = max(0.001, min(1.0, ratio))
            # Interpolazione logaritmica: K diminuisce esponenzialmente con l'apertura
            log_min = math.log(self.LOSS_COEFF_MIN)
            log_max = math.log(self.LOSS_COEFF_MAX)
            loss_coeff = math.exp(log_max - ratio * (log_max - log_min))
        else:
            loss_coeff = self.LOSS_COEFF_MAX

        loss_coeff = max(self.LOSS_COEFF_MIN, min(self.LOSS_COEFF_MAX, loss_coeff))

        with open(self.log_path, "a") as f:
            f.write(f"{step:4d} | {s_current:.4f} | {deficit:.4f} | "
                    f"{self.current_valve_level:.4f} | {tx_interval} | "
                    f"{loss_coeff:.2f}\n")

        return {
            "level": self.current_valve_level,
            "tx_interval": tx_interval,
            "loss_coeff": loss_coeff,
            "open_valve": open_valve,
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
        loss_coeff = action.get("loss_coeff", self.LOSS_COEFF_MAX)
        open_valve = action.get("open_valve", False)

        self.opened_count = 0
        g = 9.81  # accelerazione gravitazionale

        # ── SET DIRETTO DEGLI ATTRIBUTI (bypass SimTimeCondition) ──
        for v_name in self.water_net.iot_valves:
            valve = sim._wn.get_link(v_name)

            if open_valve and loss_coeff < self.LOSS_COEFF_MAX:
                valve._user_status = LinkStatus(1)        # Open
                valve._internal_status = LinkStatus(1)    # Open
                valve._setting = loss_coeff
                self.current_valve_levels[v_name] = action.get("level", 0.0)
                self.opened_count += 1
            else:
                valve._user_status = LinkStatus(0)        # Closed
                valve._internal_status = LinkStatus(0)    # Closed
                valve._setting = self.LOSS_COEFF_MAX
                self.current_valve_levels[v_name] = 0.0

        # ── AGGIORNA IL MODELLO AML TRAMITE MODEL_UPDATER ──
        for v_name in self.water_net.iot_valves:
            valve = sim._wn.get_link(v_name)
            
            # 1. Aggiorna tramite model_updater (gestisce status, setting, _is_isolated)
            try:
                sim._model_updater.update(sim._model, sim._wn, valve, 'status')
                sim._model_updater.update(sim._model, sim._wn, valve, 'setting')
            except Exception:
                pass
            
            # 2. Aggiorna DIRETTAMENTE i parametri AML (fallback garantito)
            try:
                # valve_setting (il K della TCV)
                if hasattr(sim._model, 'valve_setting') and v_name in sim._model.valve_setting:
                    sim._model.valve_setting[v_name].value = valve._setting
                
                # tcv_resistance (il parametro che il solver usa VERAMENTE)
                # r = 8 * K / (g * pi^2 * D^4)
                if hasattr(sim._model, 'tcv_resistance') and v_name in sim._model.tcv_resistance:
                    K = valve._setting
                    D = valve.diameter
                    r = 8.0 * K / (g * math.pi**2 * D**4)
                    sim._model.tcv_resistance[v_name].value = r
            except Exception:
                pass

        # ── POMPE SEMPRE SPENTE ──
        for p_name in self.water_net.iot_pumps:
            pump = sim._wn.get_link(p_name)
            pump._user_status = LinkStatus(0)
            pump._internal_status = LinkStatus(0)
            try:
                sim._model_updater.update(sim._model, sim._wn, pump, 'status')
            except Exception:
                pass