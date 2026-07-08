"""
CoSimulationEngine — Motore principale di co-simulazione cyber-idrica.

Orchestrazione tra:
  - Rete idraulica (mwntr / MWNTRInteractiveSimulator)
  - Rete di comunicazione LoRaWAN (lora_simplus)
  - Agente di controllo ibrido (PriorityAgent / GNN)
"""

import os
import sys
import json
import logging
import datetime
import argparse
import importlib
from pathlib import Path

import numpy as np

# ── mwntr path setup ────────────────────────────────────────────────────────
_dyn_wntr_path = Path('Dyn-WNTR')
for _p in [_dyn_wntr_path, _dyn_wntr_path / 'mwntr']:
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import mwntr
from mwntr.sim.interactive_network_simulator import MWNTRInteractiveSimulator

from Strategies import STRATEGY_MAP
from Agents import AGENT_MAP
from Crises import CRISIS_MAP

from Network.lora_system import LoRaSystem, calculate_gateway_pos
from Network.water_manager import WaterNetworkManager, _is_real_user_node


# ────────────────────────────────────────────────────────────────────────────
# Logging setup
# ────────────────────────────────────────────────────────────────────────────

def setup_logging(log_dir: Path, level: int = logging.INFO) -> logging.Logger:
    """
    Configura un logger centralizzato che scrive sia su console che su file.
    Tutti i moduli del progetto ottengono automaticamente questo handler.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"simulation_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%H:%M:%S"
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # File handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)   # File riceve tutto, console solo INFO+
    fh.setFormatter(fmt)
    root.addHandler(fh)

    return logging.getLogger("cosim")


logger = logging.getLogger("cosim")


def _normalize_config_name(config_name: str) -> str:
    name = config_name.strip()
    if name.lower().startswith("config_"):
        name = name[len("config_"):]
    return name.upper()


def load_config_module(config_name: str):
    normalized_name = _normalize_config_name(config_name)
    module_name = f"Configurations.config_{normalized_name}"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise ValueError(
            f"Configurazione non valida: {config_name}. "
            f"Usa una tra: CSA, NET30"
        ) from exc


# ────────────────────────────────────────────────────────────────────────────
# CoSimulationEngine
# ────────────────────────────────────────────────────────────────────────────

class CoSimulationEngine:
    """
    Motore che orchestra la co-simulazione tra rete idraulica e rete LoRaWAN.

    Tutti i parametri operativi sono iniettati dall'esterno (da config.py tramite
    create_engine()) — nessun valore hardcoded all'interno di questo costruttore.
    """

    def __init__(
        self,
        network_file,
        duration_hours: int = 24,
        step_min: int = 5,
        remove_tanks: bool = False,
        crisis_mode: str = 'pressure',
        decay_type: str = 'linear',
        decay_rate: float = 0.1,
        avg_demand: float = 15.0,
        dist_type: str = 'normal',
        pattern_mode: str = 'random',
        n_tanks: int = 3,
        strategy_name: str = 'random',
        crisis_start_hour: float = 2.0,
        gateway_mode: str = 'center',
        agent_name: str = 'heuristic',
        agent_threshold: float = 0.90,
        agent_aggression: float = 5.0,
        enable_pumps: bool = True,
        lora_mode: str = 'multihop',
        n_gateways: int = 1,
        min_boost: float = 10.0,
        gateway_offset: float = 0.0,
        sf_mode: str = 'distance',
        fixed_sf: int = 10,
        crisis_params: dict = None,
        agent_alpha: float = 0.8,
        target_head: float = 200.0,
        preserve_demand_patterns: bool = True,
        required_pressure: float = 35.0,
        minimum_pressure: float = 0.0,
        min_exp_threshold: float = 1e-4,
        log_dir: Path = None,
        isolation_pipes: list = None,
    ):
        self.timestep_s = step_min * 60
        self.n_steps = int((duration_hours * 3600) / self.timestep_s)
        self.crisis_start_step = int((crisis_start_hour * 60) / step_min)
        self.min_boost = min_boost
        self.avg_demand = avg_demand
        self.target_head = target_head
        self.required_pressure = required_pressure
        self.minimum_pressure = minimum_pressure
        self.min_exp_threshold = min_exp_threshold
        self.log_dir = Path(log_dir) if log_dir else Path("Log_review")
        self.log_dir.mkdir(parents=True, exist_ok=True)

        logger.info("=== CoSimulationEngine initializing ===")
        logger.info(
            "Duration: %dh | Step: %dmin | Steps: %d | Crisis @h%d",
            duration_hours, step_min, self.n_steps, crisis_start_hour
        )

        # ── 1. Setup Idraulico ──────────────────────────────────────────────
        logger.info("[1/5] Building water network from: %s", network_file)
        self.water_net = WaterNetworkManager(network_file)

        self.water_net.activate_network_demands(
            avg_demand=avg_demand, dist_type=dist_type,
            pattern_mode=pattern_mode, preserve_patterns=preserve_demand_patterns
        )

        # FIX: REMOVE_TANKS ora rispettato — remove_existing_tanks() chiamato se True
        if remove_tanks:
            logger.info("  Removing original tanks from network...")
            self.water_net.remove_existing_tanks()

        if n_tanks > 0:
            self.water_net.add_iot_tanks(
                n_tanks=n_tanks, strategy_name=strategy_name,
                min_boost=self.min_boost, use_pumps=enable_pumps
            )

        # ── Valvole di Isolamento Intelligenti ──────────────────────────────
        # Strumenta come TCV agent-controllate i pipe indicati in isolation_pipes.
        # Per reti dove le TCV sono già nel file .inp (es. NET30), registra
        # direttamente le valvole esistenti senza rimuoverle/riaggiungerle.
        if isolation_pipes:
            _pipes_to_convert = []
            _existing_valves  = []
            for name in isolation_pipes:
                if name in self.water_net.wn.pipe_name_list:
                    _pipes_to_convert.append(name)
                elif name in self.water_net.wn.valve_name_list:
                    _existing_valves.append(name)
                else:
                    logger.warning("isolation_pipes: link '%s' not found in network, skipped.", name)

            if _pipes_to_convert:
                logger.info(
                    "  Converting %d pipe(s) to agent-controlled TCV valves: %s",
                    len(_pipes_to_convert), _pipes_to_convert
                )
                self.water_net.instrument_selected_pipes_as_valves(_pipes_to_convert)
            if _existing_valves:
                logger.info(
                    "  Registering %d existing TCV valve(s) as agent-controlled: %s",
                    len(_existing_valves), _existing_valves
                )
                if not hasattr(self.water_net, 'controllable_isolation_valves'):
                    self.water_net.controllable_isolation_valves = []
                self.water_net.controllable_isolation_valves.extend(_existing_valves)

        self.water_net.fix_reservoir_head(target_head=target_head)
        self.water_net.set_simulation_options(
            self.timestep_s,
            required_pressure=required_pressure,
            minimum_pressure=minimum_pressure,
        )

        # ── 2. Setup Crisi ──────────────────────────────────────────────────
        logger.info("[2/5] Configuring crisis model: %s / %s", crisis_mode, decay_type)
        crisis_class = CRISIS_MAP.get(decay_type, CRISIS_MAP['linear'])
        if crisis_params is None:
            crisis_params = {'decay_rate': decay_rate}
        crisis_params['crisis_start_hour'] = crisis_start_hour
        crisis_params['step_min'] = step_min

        self.crisis_model = crisis_class(**crisis_params)
        self.crisis_mode_name = crisis_mode

        # ── 3. Inizializzazione Agente ──────────────────────────────────────
        logger.info("[3/5] Initializing simulator and agent: %s", agent_name)
        self.sim = MWNTRInteractiveSimulator(self.water_net.wn)
        agent_class = AGENT_MAP.get(agent_name, AGENT_MAP['priority'])

        # Prima istanza senza lora_net per estrarre la topologia dei sensori
        self.agent = agent_class(
            self.water_net, None,
            threshold=agent_threshold,
            aggression=agent_aggression,
            alpha=agent_alpha,
            crisis_start_time_s=self.crisis_start_step * self.timestep_s,
        )

        # Se tutti i junction attivi sono prioritari, non li strumentiamo con sensori LoRa.
        junction_names = list(self.water_net.wn.junction_name_list)
        active_junctions = [
            j for j in junction_names
            if self.water_net.wn.get_node(j).demand_timeseries_list and 
               self.water_net.wn.get_node(j).demand_timeseries_list[0].base_value > 0
        ]
        if not active_junctions:
            active_junctions = junction_names

        all_junctions_priority = bool(active_junctions) and (
            len(self.agent.priority_nodes) >= len(active_junctions)
        )
        priority_sensor_nodes = [] if all_junctions_priority else self.agent.priority_nodes

        # Definizione topologica dei sensori LoRa (ordine deterministico)
        self.all_lora_sensors = list(dict.fromkeys(
            self.water_net.iot_valves
            + priority_sensor_nodes
            + self.agent.isolation_valves
        ))
        logger.info(
            "  LoRa sensors registered: %d (priority junction sensors %s)",
            len(self.all_lora_sensors),
            "disabled" if all_junctions_priority else "enabled",
        )

        # ── 4. Setup LoRa ───────────────────────────────────────────────────
        logger.info("[4/5] Setting up LoRa network (mode=%s, sf=%s)", lora_mode, sf_mode)
        self.lora_net = LoRaSystem()
        self.agent.lora_net = self.lora_net

        gw_pos = calculate_gateway_pos(
            self.water_net.wn, mode=gateway_mode,
            offset_dist=gateway_offset, sensors_list=self.all_lora_sensors,
            n_gateways=n_gateways
        )
        self.lora_net.setup_gateways(
            gw_pos if isinstance(gw_pos, list) else [gw_pos]
        )
        self.lora_net.register_iot_sensors(
            self.all_lora_sensors, self.water_net.wn,
            mode=lora_mode, sf_mode=sf_mode, fixed_sf=fixed_sf
        )

        # ── 5. PDA e parametri idraulici finali ────────────────────────────
        logger.info("[5/5] Finalizing PDA options and log files")
        self.water_net.wn.options.hydraulic.demand_model = 'PDA'
        self.water_net.wn.options.hydraulic.minimum_pressure = minimum_pressure
        self.water_net.wn.options.hydraulic.required_pressure = required_pressure

        # ── Log e statistiche ───────────────────────────────────────────────
        self._init_log_files(crisis_mode, gateway_mode)
        self.stats = {
            'time': [], 'satisfaction': [], 'satisfaction_priority': [],
            'packet_loss': [], 'tanks': [], 'tank_activation_ever': [],
            'tank_activity_steps': [], 'reward': [], 'tank_levels': [],
        }
        self._ever_opened_valves: set = set()

        logger.info("=== Engine ready — %d steps to simulate ===", self.n_steps)

    # ────────────────────────────────────────────────────────────────────────
    # Inizializzazione file di log
    # ────────────────────────────────────────────────────────────────────────

    def _init_log_files(self, crisis_mode: str, gateway_mode: str):
        self.perf_log = self.log_dir / "main_performance.txt"
        self.valve_csv = self.log_dir / "valve_commands.csv"
        self.valve_settings_csv = self.log_dir / "valve_settings.csv"

        self.perf_log.write_text(
            "STEP | EXPECTED | ACTUAL | DIFF | SATISFACTION | TX_INT | OBJECTIVE\n"
            + "-" * 80 + "\n"
        )
        self.valve_csv.write_text("step,time_hours,valve_name,commanded_level\n")
        self.valve_settings_csv.write_text("step,time_hours,valve_name,initial_setting,status\n")

        with (self.log_dir / "crisis_status.txt").open("w") as f:
            f.write(f"SIMULATION START: {datetime.datetime.now()}\n")
            f.write(f"MODE: {crisis_mode.upper()} | GATEWAY: {gateway_mode}\n")
            f.write("-" * 65 + "\n")
            f.write("MODE | STEP | RATIO | VALUE (Head/Coeff) | REDUCTION\n")
            f.write("-" * 65 + "\n")

    # ────────────────────────────────────────────────────────────────────────
    # Loop principale di simulazione
    # ────────────────────────────────────────────────────────────────────────

    def run_simulation(self):
        """
        Esegue il loop step-by-step della co-simulazione.

        Flusso per ogni step:
          1. Avanzamento del tempo + aggiornamento crisi idraulica
          2. Avanzamento del simulatore idraulico (step_sim)
          3. Raccolta dati dai sensori e trasmissione uplink LoRa
          4. Decisione agente basata sulla telemetria ricevuta
          5. Trasmissione comandi downlink LoRa
          6. Applicazione mitigation sull'idraulica (solo comandi confermati)
          7. Calcolo e log delle metriche
        """
        # Preserva i valori stocastici assegnati al setup
        saved_stochastic_demands = {}
        for j_name in self.water_net.wn.junction_name_list:
            node = self.water_net.wn.get_node(j_name)
            if node.demand_timeseries_list:
                original_val = node.demand_timeseries_list[0].base_value
                saved_stochastic_demands[j_name] = original_val
                node.demand_timeseries_list[0].base_value = original_val
        # Apertura valvola principale se presente
        try:
            main_valve = self.water_net.wn.get_link("Main_Control_Valve")
            main_valve.initial_status = mwntr.network.elements.LinkStatus.Open
            main_valve.initial_setting = 0.0
        except KeyError:
            pass

        self.sim.init_simulation()

        # Export topologia per il dashboard
        dashboard_data = self._export_topology_js()

        t = 0.0
        demand_log = (self.log_dir / "demand_distribution.csv").open("w")
        demand_log.write("step,time_hours,expected_demand,actual_demand,satisfaction_pct\n")

        try:
            for step in range(self.n_steps):
                t += self.timestep_s
                self.sim._currentTime = int(t)

                # ── Sync domande stocastiche nel simulatore ──────────────────
                if hasattr(self.sim, '_wn'):
                    self.sim._wn.options.hydraulic.demand_model = 'PDA'
                    self.sim._wn.options.hydraulic.required_pressure = self.required_pressure
                    self.sim._wn.options.hydraulic.minimum_pressure = self.minimum_pressure
                    if step == 0:
                        for j_name, val in saved_stochastic_demands.items():
                            sim_node = self.sim._wn.get_node(j_name)
                            if sim_node.demand_timeseries_list:
                                sim_node.demand_timeseries_list[0].base_value = val

                # ── Applicazione crisi idraulica ─────────────────────────────
                crisis_start_time_s = self.crisis_start_step * self.timestep_s
                current_ratio = 1.0
                if t >= crisis_start_time_s:
                    time_elapsed_h = (t - crisis_start_time_s) / 3600.0
                    current_ratio = self.crisis_model.get_ratio(time_elapsed_h)
                    self.water_net.apply_crisis_reduction(
                        self.sim, current_ratio, step, mode=self.crisis_mode_name
                    )

                # ── Diagnostica periodica ─────────────────────────────────────
                if step % 10 == 0:
                    self._log_step_diagnostics(step, t, current_ratio)

                # ── Raccolta telemetria sensori → payload LoRa ────────────────
                sim_time_h = t / 3600.0
                priority_nodes_set = set(self.agent.priority_nodes)
                self._collect_sensor_payloads(sim_time_h, priority_nodes_set)

                # ── Uplink: sensori → gateway ─────────────────────────────────
                received_uplink = self.lora_net.step(t, self.timestep_s)

                # ── Decisione agente (basata su telemetria confermata) ─────────
                # FIX: singola chiamata decide_action con received_uplink (list[dict])
                action = self.agent.decide_action(step, t, received_uplink, sim=self.sim)
                
                if 'tx_interval' in action:
                    self.lora_net.tx_interval_s = action['tx_interval']

                # ── Downlink: gateway → sensori (comandi di controllo) ─────────
                downlink_commands = self.agent.format_downlink_commands(action)
                received_downlink = self.lora_net.step_downlink(
                    downlink_commands, t, self.timestep_s
                )

                # ── Applicazione mitigazione (solo comandi confermati) ─────────
                # FIX: apply_mitigation riceve received_downlink {sensor_id: cmd_payload}
                self.agent.apply_mitigation(received_downlink, self.sim, self.lora_net, t)

                # ── Avanzamento idraulico ─────────────────────────────────────
                self.sim.step_sim()

                # ── Metriche ──────────────────────────────────────────────────
                step_metrics = self._compute_step_metrics(step, t, current_ratio, action)
                demand_log.write(
                    f"{step},{t / 3600:.2f},"
                    f"{step_metrics['exp_t']:.4f},"
                    f"{step_metrics['act_t']:.4f},"
                    f"{step_metrics['sat_pct']:.2f}\n"
                )

                self._update_stats(step_metrics)
                dashboard_data.append(
                    self._build_step_data(step, t, current_ratio, step_metrics, action)
                )

        except Exception as exc:
            logger.exception("Simulation crashed at step=%d t=%.1fs: %s", step, t, exc)
            raise
        finally:
            demand_log.close()

        # Scrittura finale dati dashboard
        with (Path("Dashboard") / "data.js").open("a") as js_file:
            js_file.write("window.simData = " + json.dumps(dashboard_data, indent=2) + ";\n")

        logger.info("✓ Simulation complete. Dashboard/data.js written.")
        return self.sim.get_results()

    # ────────────────────────────────────────────────────────────────────────
    # Helpers privati
    # ────────────────────────────────────────────────────────────────────────

    def _log_step_diagnostics(self, step: int, t: float, current_ratio: float):
        """Log diagnostico step-by-step: stato cisterne e crisi."""
        active_wn = getattr(self.sim, '_wn', self.water_net.wn)
        tank_info = []
        for t_name in active_wn.tank_name_list:
            try:
                tank = active_wn.get_node(t_name)
                tank_info.append(f"{t_name}={tank.level:.2f}m")
            except Exception:
                tank_info.append(f"{t_name}=?")

        sat = (self.stats['satisfaction'][-1] if self.stats['satisfaction'] else 100.0)
        logger.info(
            "[STEP %3d] t=%5.2fh | crisis=%.3f | sat=%.1f%% | tanks=[%s]",
            step, t / 3600.0, current_ratio, sat, ", ".join(tank_info)
        )

    def _collect_sensor_payloads(self, sim_time_h: float, priority_nodes_set: set):
        """
        Costruisce il payload per ogni sensore LoRa e lo deposita nel buffer
        `lora_net.sensors[sensor_id]['data']`.

        Chiavi allineate con priority_agent.py:
          - 'is_priority_node'    (non 'is_priority')
          - 'served_priority_node' (non 'served_priority')
        """
        for sensor_id in self.all_lora_sensors:
            try:
                payload = {'sim_time_h': round(sim_time_h, 2)}

                if sensor_id in self.water_net.iot_valves:
                    valve = self.sim._wn.get_link(sensor_id)
                    start_node = self.sim._wn.get_node(valve.start_node_name)
                    end_node = self.sim._wn.get_node(valve.end_node_name)
                    is_prio = end_node.name in priority_nodes_set
                    payload.update({
                        'type': 'IOT_TANK',
                        'v_status': str(getattr(valve, 'status', 'UNKNOWN')).upper(),
                        'v_setting': round(float(
                            getattr(valve, '_setting',
                                    getattr(valve, 'initial_setting', 0.0))), 2),
                        'tank_lvl': round(float(
                            getattr(start_node, 'level', 0.0)), 2)
                            if start_node.node_type == 'Tank' else 0.0,
                        'node_p': self._get_node_pressure(end_node.name),
                        # FIX: chiavi allineate con priority_agent.py
                        'is_priority_node': is_prio,
                        'served_priority_node': end_node.name if is_prio else None,
                    })

                elif sensor_id in self.agent.priority_nodes:
                    payload.update({
                        'type': 'PRIORITY_NODE',
                        'is_priority_node': True,
                        'tank_lvl': 0.0,
                        'v_status': 'N/A',
                        'v_setting': 0.0,
                        'node_p': self._get_node_pressure(sensor_id),
                    })

                elif sensor_id in self.agent.isolation_valves:
                    valve = self.sim._wn.get_link(sensor_id)
                    payload.update({
                        'type': 'ISOLATION_VALVE',
                        'v_status': str(getattr(valve, 'status', 'UNKNOWN')).upper(),
                        'v_setting': round(float(
                            getattr(valve, '_setting',
                                    getattr(valve, 'initial_setting', 0.0))), 2),
                        'tank_lvl': 0.0,
                        'is_priority_node': False,
                        'node_p': self._get_node_pressure(valve.end_node_name),
                    })
                    
                if self.lora_net.tx_interval_s <= 300:
                    import random
                    # Aggiunge un array pesante per simulare diagnostica ad alta frequenza
                    payload['diagnostic_dump'] = [round(random.random(), 4) for _ in range(30)]

                self.lora_net.sensors[sensor_id]['data'] = payload

            except Exception as exc:
                logger.debug("Payload error for sensor %s: %s", sensor_id, exc)
                self.lora_net.sensors[sensor_id]['data'] = {
                    'error': str(exc), 'sim_time_h': round(sim_time_h, 2)
                }

    def _get_node_pressure(self, node_name: str) -> float:
        """Legge l'ultima pressione registrata per un nodo dal simulatore."""
        try:
            pressure_dict = self.sim.node_res.get('pressure', {})
            vec = pressure_dict.get(node_name, [])
            return round(float(vec[-1]), 2) if vec else 0.0
        except Exception:
            return 0.0

    def _compute_step_metrics(self, step: int, t: float, current_ratio: float,
                               action: dict) -> dict:
        """Calcola soddisfazione globale e prioritaria per lo step corrente."""
        active_wn = getattr(self.sim, '_wn', self.water_net.wn)
        sim_nodes_src = self.sim.node_res
        current_hour_int = int(t / 3600) % 24
        real_user_nodes = [
            j for j in self.water_net.wn.junction_name_list
            if _is_real_user_node(j, self.water_net.wn)
        ]

        node_demands_dict = {}
        for j_name in real_user_nodes:
            exp_val, act_val = 0.0, 0.0
            try:
                node_obj = (self.sim._wn.get_node(j_name)
                            if hasattr(self.sim, '_wn')
                            else self.water_net.wn.get_node(j_name))
                if node_obj.demand_timeseries_list:
                    base_dem = node_obj.demand_timeseries_list[0].base_value
                    pattern_obj = node_obj.demand_timeseries_list[0].pattern
                    if pattern_obj and hasattr(pattern_obj, 'multipliers'):
                        multipliers = pattern_obj.multipliers
                        if multipliers is not None and len(multipliers) > 0:
                            current_mult = multipliers[current_hour_int % len(multipliers)]
                            exp_val = base_dem * current_mult
                        else:
                            exp_val = base_dem
                    else:
                        exp_val = base_dem
            except Exception as exc:
                # Cambiato in ERROR per esporre eventuali bug futuri nella console
                logger.error("Expected demand error for %s: %s", j_name, exc)
            if 'demand' in sim_nodes_src and j_name in sim_nodes_src['demand']:
                vec = sim_nodes_src['demand'][j_name]
                if vec:
                    calc = vec[-1]
                    act_val = min(calc, exp_val) if calc > 0 and exp_val > 0 else 0.0

            node_demands_dict[j_name] = {'expected': float(exp_val), 'actual': float(act_val)}

        exp_t = sum(v['expected'] for v in node_demands_dict.values())
        act_t = sum(v['actual'] for v in node_demands_dict.values())

        priority_nodes = self.water_net._get_priority_nodes()
        exp_p = sum(node_demands_dict[n]['expected']
                    for n in priority_nodes if n in node_demands_dict)
        act_p = sum(node_demands_dict[n]['actual']
                    for n in priority_nodes if n in node_demands_dict)

        thr = self.min_exp_threshold
        sat_pct = min((act_t / exp_t) * 100.0, 100.0) if exp_t > thr else 100.0
        sat_pct_prio = min((act_p / exp_p) * 100.0, 100.0) if exp_p >= thr else 100.0

        reward = self.agent.compute_objective(sat_pct / 100.0, self.lora_net.tx_interval_s)

        # Log performance
        with self.perf_log.open("a") as f:
            f.write(
                f"{step:<4} | {exp_t:<8.4f} | {act_t:<8.4f} | {exp_t - act_t:<8.4f} | "
                f"{sat_pct:<8.2f}% | {self.lora_net.tx_interval_s} | {reward:.4f}\n"
            )

        return {
            'node_demands': node_demands_dict,
            'exp_t': exp_t, 'act_t': act_t,
            'sat_pct': sat_pct, 'sat_pct_prio': sat_pct_prio,
            'reward': reward,
        }

    def _update_stats(self, metrics: dict):
        t = self.stats['time'][-1] if self.stats['time'] else 0
        self.stats['time'].append(t)
        self.stats['satisfaction'].append(metrics['sat_pct'])
        self.stats['satisfaction_priority'].append(metrics['sat_pct_prio'])
        self.stats['packet_loss'].append(self.lora_net.get_packet_loss_rate())
        self.stats['reward'].append(metrics['reward'])

        current_open = {
            v for v, lvl in getattr(self.agent, 'current_valve_levels', {}).items()
            if float(lvl) > 0.0
        }
        self._ever_opened_valves.update(current_open)
        self.stats['tanks'].append(self.agent.opened_count)
        self.stats['tank_activation_ever'].append(len(self._ever_opened_valves))
        self.stats['tank_activity_steps'].append(
            {v: float(lvl)
             for v, lvl in getattr(self.agent, 'current_valve_levels', {}).items()}
        )

        active_wn = getattr(self.sim, '_wn', self.water_net.wn)
        node_list = list(active_wn.node_name_list)
        press_dict = self.sim.node_res.get('pressure', {})
        idx_last = max(0, len(press_dict.get(node_list[0], [])) - 1) if node_list else 0

        levels, levels_dict = [], {}
        for t_name in active_wn.tank_name_list:
            vec = press_dict.get(t_name, [])
            raw = vec[idx_last] if len(vec) > idx_last else 0.0
            clipped = max(0.0, min(raw, getattr(active_wn.get_node(t_name), 'max_level', 10.0)))
            levels.append(clipped)
            levels_dict[t_name] = float(clipped)
        self.stats['tank_levels'].append(levels)

    def _build_step_data(self, step: int, t: float, current_ratio: float,
                          metrics: dict, action) -> dict:
        """Assembla il record JSON per il dashboard per questo step."""
        active_wn = getattr(self.sim, '_wn', self.water_net.wn)
        press_dict = self.sim.node_res.get('pressure', {})
        flow_dict = self.sim.link_res.get('flow', {})
        node_list = list(active_wn.node_name_list)
        idx_last = max(0, len(press_dict.get(node_list[0], [])) - 1) if node_list else 0
        pl = self.lora_net.get_packet_loss_rate()
        fa = metrics.get('reward', 0.0)

        pipe_flows = {}
        for lk in active_wn.link_name_list:
            flow_val = 0.0
            if lk in flow_dict:
                f_data = flow_dict[lk]
                if len(f_data) > idx_last:
                    flow_val = f_data[idx_last]
                elif len(f_data) > 0:
                    flow_val = f_data[-1] 
            else:
                try:
                    link_obj = active_wn.get_link(lk)
                    if hasattr(link_obj, 'flow'):
                        flow_val = link_obj.flow
                except Exception:
                    flow_val = 0.0
            
            pipe_flows[lk] = float(flow_val)

        valves_status = {}
        for lk in active_wn.link_name_list:
            if lk in active_wn.valve_name_list or lk in active_wn.pump_name_list:
                try:
                    valves_status[lk] = str(active_wn.get_link(lk).status.name).upper()
                except Exception:
                    valves_status[lk] = "OPEN"
            else:
                valves_status[lk] = "OPEN"

        levels_dict = {}
        for t_name in active_wn.tank_name_list:
            vec = press_dict.get(t_name, [])
            raw = vec[idx_last] if len(vec) > idx_last else 0.0
            levels_dict[t_name] = float(
                max(0.0, min(raw, getattr(active_wn.get_node(t_name), 'max_level', 10.0)))
            )
            
        sensor_plr = {}
        for s_id, s_data in self.lora_net.sensors.items():
            tx = s_data.get('tx_count', 0)
            rx = s_data.get('rx_count', 0)
            plr = ((tx - rx) / tx * 100.0) if tx > 0 else 0.0
            sensor_plr[s_id] = round(plr, 2)

        return {
            "step": step,
            "time_hours": float(t / 3600),
            "global_metrics": {
                "satisfaction_pct": float(metrics['sat_pct']),
                "satisfaction_priority_pct": float(metrics.get('sat_pct_prio', 0.0)), 
                "crisis_ratio": float(current_ratio),
                "source_head": float(self.target_head * current_ratio),
                "active_tanks": int(self.agent.opened_count),
                "packet_loss": float(pl),
                "objective": float(fa)
            },
            "nodes": metrics['node_demands'],
            "pipes": pipe_flows,
            "tanks": levels_dict,
            
            "sensor_packet_loss": sensor_plr,
            
            "valves": valves_status,
            "valve_commands": getattr(self.agent, 'current_valve_levels', {}),
            "valve_settings": {
                lk: float(getattr(active_wn.get_link(lk), 'initial_setting', float('nan')))
                for lk in active_wn.valve_name_list
                if lk in active_wn.link_name_list
            },
            "isolation_valve_settings": {
                lk: {
                    "setting": float(getattr(active_wn.get_link(lk), '_setting',
                                             getattr(active_wn.get_link(lk), 'initial_setting', 0.0))),
                    "status": str(getattr(
                        getattr(active_wn.get_link(lk), '_user_status',
                                getattr(active_wn.get_link(lk), 'status', 'OPEN')),
                        'name', 'OPEN'
                    )).upper(),
                }
                for lk in getattr(self.water_net, 'controllable_isolation_valves', [])
                if lk in active_wn.link_name_list
            },
        }

    def _write_valve_logs(self, step: int, t: float):
        """Appende i comandi e le impostazioni delle valvole ai CSV di log."""
        active_wn = getattr(self.sim, '_wn', self.water_net.wn)
        try:
            with self.valve_csv.open("a") as vf:
                for vname, lvl in getattr(self.agent, 'current_valve_levels', {}).items():
                    vf.write(f"{step},{t / 3600:.3f},{vname},{float(lvl):.6f}\n")
        except Exception as exc:
            logger.debug("valve_csv write error: %s", exc)

        try:
            with self.valve_settings_csv.open("a") as vf2:
                for vname in active_wn.valve_name_list:
                    try:
                        vobj = active_wn.get_link(vname)
                        setting = float(getattr(vobj, '_setting',
                                                getattr(vobj, 'initial_setting', float('nan'))))
                        status = getattr(vobj, '_user_status',
                                         getattr(vobj, 'status', 'UNKNOWN'))
                        status_str = (status.name.upper()
                                      if hasattr(status, 'name') else str(status))
                        vf2.write(f"{step},{t / 3600:.3f},{vname},{setting:.6f},{status_str}\n")
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("valve_settings_csv write error: %s", exc)

    def _export_topology_js(self) -> list:
        """Esporta la topologia della rete nel file Dashboard/data.js."""
        topology = {"nodes": [], "links": [], "gateways": [],"sensors": [], "priority_nodes": []}
        all_coords = [
            node.coordinates
            for _, node in self.water_net.wn.nodes()
            if getattr(node, 'coordinates', None)
        ]

        if all_coords:
            all_x = [c[0] for c in all_coords]
            all_y = [c[1] for c in all_coords]
            min_x, max_x = min(all_x), max(all_x)
            min_y, max_y = min(all_y), max(all_y)
            range_x = max_x - min_x or 1
            range_y = max_y - min_y or 1
        else:
            min_x = min_y = 0
            range_x = range_y = 1

        node_coords_map = {
            n: (getattr(node, 'coordinates', None) or (0, 0))
            for n, node in self.water_net.wn.nodes()
        }

        for n_name, node in self.water_net.wn.nodes():
            n_type = (
                "Reservoir" if n_name in self.water_net.wn.reservoir_name_list else
                "Tank" if n_name in self.water_net.wn.tank_name_list else
                "Junction"
            )
            coords = node_coords_map[n_name]
            topology["nodes"].append({
                "id": n_name, "type": n_type,
                "x": float((coords[0] - min_x) / range_x * 1000),
                "y": float((coords[1] - min_y) / range_y * 1000),
            })

        for l_name, link in self.water_net.wn.links():
            l_type = (
                "Pump" if l_name in self.water_net.wn.pump_name_list else
                "Valve" if l_name in self.water_net.wn.valve_name_list else
                "Pipe"
            )
            topology["links"].append({
                "id": l_name, "type": l_type,
                "source": link.start_node_name, "target": link.end_node_name,
            })
            
        for gw in self.lora_net.gateways:
            topology["gateways"].append({
                "id": gw['id'],
                "x": float((gw['x'] - min_x) / range_x * 1000) if range_x else 0,
                "y": float((gw['y'] - min_y) / range_y * 1000) if range_y else 0,
            })
            
        topology["sensors"] = list(self.lora_net.sensors.keys())
        topology["priority_nodes"] = [
            j for j in self.water_net.wn.junction_name_list
            if getattr(self.water_net.wn.get_node(j), 'tag', None) == 'USER_1_P'
        ]
        topology["isolation_valves"] = list(
            getattr(self.water_net, 'controllable_isolation_valves', [])
        )

        Path("Dashboard").mkdir(exist_ok=True)
        with (Path("Dashboard") / "data.js").open("w") as js:
            js.write("// Auto-generated by main.py\n\n")
            js.write("window.topology = " + json.dumps(topology, indent=2) + ";\n\n")
            js.write("window.simData = null; // Populated after simulation\n")

        return []


# ────────────────────────────────────────────────────────────────────────────
# Factory
# ────────────────────────────────────────────────────────────────────────────

def create_engine(config_name: str) -> CoSimulationEngine:
    """
    Factory function: istanzia CoSimulationEngine leggendo i parametri dal modulo
    di configurazione selezionato.
    Unico punto di accoppiamento tra configurazione e motore.
    """
    config = load_config_module(config_name)

    return CoSimulationEngine(
        network_file=config.NETWORK_FILE,
        duration_hours=config.DURATION_HOURS,
        step_min=config.STEP_MIN,
        remove_tanks=config.REMOVE_TANKS,
        crisis_mode=config.CRISIS_MODE,
        crisis_start_hour=config.CRISIS_START_HOUR,   
        decay_type=config.DECAY_TYPE,
        decay_rate=config.DECAY_RATE,
        avg_demand=config.AVG_DEMAND,
        dist_type=config.DIST_TYPE,
        pattern_mode=config.PATTERN_MODE,
        n_tanks=config.N_TANKS,
        strategy_name=config.STRATEGY_NAME,
        agent_name=config.AGENT_NAME,
        agent_threshold=config.AGENT_THRESHOLD,
        agent_aggression=config.AGENT_AGGRESSION,
        enable_pumps=config.ENABLE_PUMPS,
        lora_mode=config.LORA_MODE,
        gateway_mode=config.GATEWAY_MODE,
        n_gateways=getattr(config, 'N_GATEWAYS', 1),
        min_boost=config.MIN_BOOST,
        gateway_offset=config.GATEWAY_OFFSET,
        sf_mode=config.SF_MODE,
        fixed_sf=config.FIXED_SF,
        crisis_params=dict(config.CRISIS_PARAMS),  # copia difensiva
        agent_alpha=config.AGENT_ALPHA,
        target_head=config.TARGET_HEAD,
        preserve_demand_patterns=config.PRESERVE_DEMAND_PATTERNS,
        # Parametri aggiunti in config.py (P2 fix)
        required_pressure=config.REQUIRED_PRESSURE,
        minimum_pressure=config.MINIMUM_PRESSURE,
        min_exp_threshold=config.MIN_EXP_THRESHOLD,
        log_dir=config.LOG_DIR,
        isolation_pipes=getattr(config, 'ISOLATION_PIPES', []),
    )


# ────────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Avvia la co-simulazione scegliendo il modulo di configurazione."
    )
    parser.add_argument(
        "config_tag",
        help="Tag della configurazione da usare dopo 'config_' (es. CSA, NET30, altro).",
    )
    args = parser.parse_args()

    config = load_config_module(args.config_tag)
    cosim_logger = setup_logging(config.LOG_DIR)
    cosim_logger.info("Working directory: %s", Path.cwd())
    cosim_logger.info("Using configuration: %s", _normalize_config_name(args.config_tag))

    engine = create_engine(args.config_tag)
    results = engine.run_simulation()

    cosim_logger.info(
        "Simulation completed: %d nodes | LoRa log: %s",
        len(results.node['pressure'].columns),
        engine.lora_net.log_path,
    )

    from simulation_utils.visualization import generate_simulation_plots
    generate_simulation_plots(engine)