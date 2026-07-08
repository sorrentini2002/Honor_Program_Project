import json
import datetime
import math
import logging
import numpy as np
import sys
from pathlib import Path

_dyn_wntr_path = Path('Dyn-WNTR')
for _p in [_dyn_wntr_path, _dyn_wntr_path / 'mwntr']:
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
        
import mwntr
from Strategies import STRATEGY_MAP

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Funzione di utilità a livello modulo (accessibile via import diretto)
# ─────────────────────────────────────────────────────────────────────────────

def _is_real_user_node(node_name: str, wn=None) -> bool:
    """
    Verifica se un nodo è un nodo utente reale controllando se ha una domanda idrica non nulla.
    In assenza di questa informazione, fa fallback sul controllo del tag 'USER_1' / 'USER_1_P'.
    
    Args:
        node_name: Nome del nodo da verificare.
        wn:        Istanza WaterNetworkModel.
        
    Returns:
        True se il nodo ha una domanda base > 0 o tag associato, False altrimenti.
    """
    if wn is None:
        return False
    try:
        node = wn.get_node(node_name)
        if hasattr(node, 'demand_timeseries_list') and node.demand_timeseries_list:
            if node.demand_timeseries_list[0].base_value > 0:
                return True
        return getattr(node, 'tag', None) in ('USER_1', 'USER_1_P')
    except (KeyError, AttributeError):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Manager della rete idrica
# ─────────────────────────────────────────────────────────────────────────────

class WaterNetworkManager:
    """Facade che gestisce la rete idrica mwntr e tutte le operazioni di setup."""

    def __init__(self, wn_model):
        self.network_path = Path(wn_model) if isinstance(wn_model, (str, Path)) else None
        self.wn = (
            mwntr.network.WaterNetworkModel(str(self.network_path))
            if isinstance(wn_model, (str, Path))
            else wn_model
        )
        self.iot_tanks: dict = {}
        self.iot_valves: list = []
        self.iot_pumps: list = []
        self._tag_user_nodes()

    def _get_network_key(self) -> str:
        """Restituisce la chiave di rete usata per filtrare i tank config."""
        source = self.network_path or getattr(self.wn, 'filename', None)
        if not source:
            return ''
        return Path(source).stem

    def _filter_tank_configs_for_network(self, all_configs: dict) -> dict:
        """Filtra i tank config in base al nome del file .inp in uso."""
        network_key = self._get_network_key()
        network_key_lower = network_key.lower()
        filtered = {
            name: cfg for name, cfg in all_configs.items()
            if name.lower() in network_key_lower
        }

        if not filtered:
            logger.warning(
                "No tank configs matched filename '%s'; falling back to all configs.",
                network_key,
            )
            return dict(all_configs)
        return filtered

    # ─────────────────────── Tagging ────────────────────────
    def _tag_user_nodes(self):
        """
        Inizializzazione unica: identifica i nodi con pattern di domanda e li
        tagga come 'USER_1'. Inoltre legge i tag espliciti dalla sezione [TAGS]
        del file .inp, inclusi i nodi prioritari con tag 'USER_1_P'.
        """
        try:
            inp_file = getattr(self.wn, 'filename', None)
            if inp_file and Path(inp_file).exists():
                user_nodes: set = set()
                explicit_tags: dict = {}
                with Path(inp_file).open('r', encoding='utf-8', errors='replace') as f:
                    in_junctions = False
                    in_tags = False
                    for line in f:
                        stripped = line.strip()
                        upper = stripped.upper()

                        if upper.startswith('[JUNCTIONS]'):
                            in_junctions = True
                            in_tags = False
                            continue
                        elif upper.startswith('[TAGS]'):
                            in_junctions = False
                            in_tags = True
                            continue
                        elif stripped.startswith('['):
                            in_junctions = False
                            in_tags = False
                            continue

                        if in_junctions and stripped and not stripped.startswith(';'):
                            parts = stripped.split()
                            if len(parts) >= 2:
                                node_id = parts[0]
                                has_pattern = (
                                    len(parts) >= 4
                                    and parts[3]
                                    and not parts[3].startswith(';')
                                )
                                try:
                                    if int(node_id) >= 9 or has_pattern:
                                        user_nodes.add(node_id)
                                except ValueError:
                                    if has_pattern:
                                        user_nodes.add(node_id)

                        elif in_tags and stripped and not stripped.startswith(';'):
                            parts = stripped.split()
                            if len(parts) >= 3 and parts[0].upper() == 'NODE':
                                explicit_tags[parts[1]] = parts[2]

                for j_name in self.wn.junction_name_list:
                    if j_name in explicit_tags:
                        self.wn.get_node(j_name).tag = explicit_tags[j_name]
                    elif j_name in user_nodes:
                        self.wn.get_node(j_name).tag = 'USER_1'
        except Exception as exc:
            logger.warning("Tag user nodes failed (non-critical): %s", exc)

    # ──────────────────── Demand Setup ──────────────────────
    def activate_network_demands(self, avg_demand: float = 15.0,
                                  dist_type: str = 'normal',
                                  pattern_mode: str = 'random',
                                  fixed_pattern_id=None,
                                  log_filename: str = "water_network_setup.txt",
                                  preserve_patterns: bool = True):

        log_dir = Path("Log_review")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / log_filename

        pattern_names = []
        try:
            with Path('Network/patterns.json').open('r') as f:
                patterns_dict = json.load(f)
            pattern_names = list(patterns_dict.keys())
            for name, multipliers in patterns_dict.items():
                if name not in self.wn.pattern_name_list:
                    self.wn.add_pattern(name, multipliers)
        except Exception as exc:
            logger.warning("Could not load patterns.json: %s — using [None]", exc)
            pattern_names = [None]

        with log_path.open("w", encoding="utf-8") as log_file:
            log_file.write("=" * 60 + "\n")
            log_file.write(f"  WATER NETWORK DEMAND SETUP: {datetime.datetime.now()}\n")
            log_file.write("=" * 60 + "\n")
            log_file.write(
                f"Config: avg_demand={avg_demand}, dist={dist_type}, "
                f"mode={pattern_mode}, preserve_patterns={preserve_patterns}\n\n"
            )
            log_file.write(f"{'JUNCTION_ID':<20} | {'BASE_DEMAND':<12} | {'PATTERN_NAME':<15}\n")
            log_file.write("-" * 60 + "\n")

            if preserve_patterns:
                log_file.write("[MODE: PRESERVE DEMAND PATTERNS]\n\n")
                has_user_tags = any(
                    self.wn.get_node(n).tag in ('USER_1', 'USER_1_P')
                    for n in self.wn.junction_name_list
                )
                for j_name in self.wn.junction_name_list:
                    junction = self.wn.get_node(j_name)
                    is_user = (
                        (junction.tag in ('USER_1', 'USER_1_P'))
                        if has_user_tags else True
                    )
                    if not is_user:
                        junction.demand_timeseries_list.clear()
                        junction.add_demand(base=0.0, pattern_name=None)
                        log_file.write(f"{j_name:<20} | {'0.0000':<12} | {'PASS_THROUGH':<15}\n")
                        continue

                    if junction.demand_timeseries_list:
                        orig = junction.demand_timeseries_list[0]
                        base_val = orig.base_value
                        pattern_to_use = orig.pattern_name
                    else:
                        base_val = 0.0
                        pattern_to_use = None

                    junction.demand_timeseries_list.clear()
                    junction.add_demand(base=base_val, pattern_name=pattern_to_use)
                    log_file.write(f"{j_name:<20} | {base_val:<12.4f} | {str(pattern_to_use):<15}\n")

            else:
                log_file.write("[MODE: STOCHASTIC RANDOMIZATION]\n\n")
                LOGNORMAL_SIGMA = 0.25
                NORMAL_STD_RATIO = 0.2
                pattern_idx = 0 if pattern_mode == 'sequential' else None

                for i, j_name in enumerate(self.wn.junction_name_list):
                    junction = self.wn.get_node(j_name)
                    if dist_type == 'normal':
                        base_val = max(0.0, np.random.normal(avg_demand, avg_demand * NORMAL_STD_RATIO))
                    elif dist_type == 'lognormal':
                        mu = np.log(avg_demand) - 0.5 * LOGNORMAL_SIGMA ** 2
                        base_val = np.random.lognormal(mu, LOGNORMAL_SIGMA)
                    elif dist_type == 'uniform':
                        base_val = np.random.uniform(avg_demand * 0.5, avg_demand * 1.5)
                    else:
                        base_val = (
                            junction.demand_timeseries_list[0].base_value
                            if junction.demand_timeseries_list else avg_demand
                        )
                    base_val = max(0.0, base_val)

                    if pattern_names and pattern_names[0] is not None:
                        if pattern_mode == 'sequential':
                            pattern_to_use = pattern_names[pattern_idx % len(pattern_names)]
                            pattern_idx += 1
                        elif pattern_mode == 'random':
                            pattern_to_use = np.random.choice(pattern_names)
                        elif pattern_mode == 'single' and fixed_pattern_id is not None:
                            pattern_to_use = pattern_names[fixed_pattern_id % len(pattern_names)]
                        else:
                            pattern_to_use = None
                    else:
                        pattern_to_use = None

                    junction.demand_timeseries_list.clear()
                    junction.add_demand(base=base_val, pattern_name=pattern_to_use)
                    log_file.write(f"{j_name:<20} | {base_val:<12.4f} | {str(pattern_to_use):<15}\n")

            log_file.write("\n" + "=" * 60 + "\n")
            log_file.write(f"Setup completed for {len(self.wn.junction_name_list)} nodes.\n")

    # ──────────────────── Tank Management ───────────────────
    def remove_existing_tanks(self, log_filename: str = "water_network_setup.txt"):
        """Rimuove tutte le cisterne originali e i loro link dalla rete."""
        log_path = Path("Log_review") / log_filename
        tanks = [name for name, node in self.wn.nodes() if node.node_type == 'Tank']

        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n[SETUP] REMOVING EXISTING TANKS ({len(tanks)} found)\n")
            f.write("-" * 60 + "\n")
            if not tanks:
                f.write("  No existing tanks found to remove.\n")
            else:
                for t_name in tanks:
                    connected_links = [
                        l_name for l_name, link in self.wn.links()
                        if link.start_node_name == t_name or link.end_node_name == t_name
                    ]
                    num_links = len(connected_links)
                    f.write(f"  - Node {t_name}: Found {num_links} connected links.\n")

                    if num_links == 1:
                        self.wn.remove_link(connected_links[0])
                        self.wn.remove_node(t_name, with_control=True)
                        f.write(f"    * Removed leaf node + link {connected_links[0]}\n")
                    elif num_links == 2:
                        l1_name, l2_name = connected_links
                        l1 = self.wn.get_link(l1_name)
                        l2 = self.wn.get_link(l2_name)
                        n1 = l1.start_node_name if l1.end_node_name == t_name else l1.end_node_name
                        n2 = l2.start_node_name if l2.end_node_name == t_name else l2.end_node_name
                        new_name = f"Merged_{n1}_{n2}"
                        new_diam = (l1.diameter + l2.diameter) / 2
                        new_len = l1.length + l2.length
                        new_rough = l1.roughness
                        self.wn.remove_link(l1_name)
                        self.wn.remove_link(l2_name)
                        self.wn.remove_node(t_name, with_control=True)
                        self.wn.add_pipe(new_name, n1, n2, length=new_len,
                                         diameter=new_diam, roughness=new_rough)
                        f.write(f"    * Merged {n1} <-> {n2} via {new_name}\n")
                    else:
                        for l_name in connected_links:
                            self.wn.remove_link(l_name)
                        self.wn.remove_node(t_name, with_control=True)
                        f.write(f"    * Removed node + all {num_links} links\n")

                f.write(f"  Successfully processed {len(tanks)} original tanks.\n")
            f.write("-" * 60 + "\n")

    def add_iot_tanks(self, n_tanks: int = 3, strategy_name: str = 'random',
                      min_boost: float = 15.0, use_pumps: bool = True,
                      log_filename: str = "water_network_setup.txt"):
        """Piazza le cisterne IoT e registra i dettagli nel log."""
        log_path = Path("Log_review") / log_filename

        try:
            with Path('Strategies/tank_configs.json').open('r') as f:
                all_configs = json.load(f)
            filtered_configs = self._filter_tank_configs_for_network(all_configs)
            tank_types = list(filtered_configs.keys())
        except Exception as exc:
            logger.error("Cannot load tank_configs.json: %s", exc)
            return []

        strategy_class = STRATEGY_MAP.get(strategy_name, STRATEGY_MAP['random'])
        target_nodes = strategy_class(self.wn).get_nodes(n_tanks)

        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n[SETUP] DEPLOYING NEW IoT TANKS\n")
            f.write(f"Strategy used: {strategy_name.upper()}\n")
            f.write("-" * 60 + "\n")
            f.write(f"{'TANK_NAME':<20} | {'NODE':<10} | {'ELEV':<7} | "
                    f"{'T_ELEV':<7} | {'DEMAND':<8} | {'STATUS':<10}\n")
            f.write("-" * 60 + "\n")

            for i, junc_name in enumerate(target_nodes):
                junc_node = self.wn.get_node(junc_name)
                t_type = tank_types[i % len(tank_types)]
                cfg = filtered_configs[t_type]
                total_height = min_boost + cfg['max_level']
                t_name = f"IoT_Tank_{t_type}_{i}"
                base_dem = sum(d.base_value for d in junc_node.demand_timeseries_list)

                try:
                    self.wn.add_tank(
                        name=t_name,
                        elevation=junc_node.elevation + min_boost,
                        init_level=cfg['init_level'],
                        min_level=cfg['min_level'],
                        max_level=cfg['max_level'],
                        diameter=cfg['tank_diameter'],
                    )
                    self.wn.get_node(t_name).coordinates = junc_node.coordinates
                    self._add_iot_control_to_tank(
                        junc_name, t_name, f"New_{i}",
                        cfg['pipe_diameter'], total_height, use_pumps=use_pumps
                    )
                    status = "SUCCESS"
                except Exception as exc:
                    status = f"ERROR: {str(exc)[:20]}"
                    logger.error("IoT tank %s at node %s: %s", t_name, junc_name, exc)

                f.write(f"{t_name:<20} | {junc_name:<10} | {junc_node.elevation:<7.1f} | "
                        f"{junc_node.elevation + min_boost:<7.1f} | {base_dem:<8.4f} | {status:<10}\n")
            f.write("-" * 60 + "\n")

        return self.iot_valves

    # ──────────────────── Reservoir Setup ───────────────────
    def fix_reservoir_head(self, target_head: float = 100.0,
                            log_filename: str = "water_network_setup.txt"):
        """Imposta una head fissa sui reservoir e rimuove eventuali pattern interferenti."""
        log_path = Path("Log_review") / log_filename
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n[SETUP] FIXING RESERVOIR HEADS (Target: {target_head})\n")
            f.write("-" * 60 + "\n")
            for res_name in self.wn.reservoir_name_list:
                res = self.wn.get_node(res_name)
                old_head = res.head_timeseries.base_value
                res.head_timeseries.base_value = target_head
                res.head_timeseries.pattern_name = None
                f.write(f"  - {res_name}: {old_head:.1f} -> {target_head:.1f} m, pattern cleared\n")
            f.write("-" * 60 + "\n")

    def get_main_link(self, log_filename: str = "water_network_setup.txt"):
        """Identifica il tubo principale connesso a un reservoir (massimo diametro)."""
        log_path = Path("Log_review") / log_filename
        source_links = []
        for res_name in self.wn.reservoir_name_list:
            for l_name in self.wn.get_links_for_node(res_name):
                source_links.append(self.wn.get_link(l_name))
        if not source_links:
            return None
        main_link = max(source_links, key=lambda l: l.diameter)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n[SETUP] MAIN SOURCE: {main_link.name} (D={main_link.diameter})\n")
            f.write("-" * 60 + "\n")
        return main_link.name

    def instrument_source_with_valve(self):
        """Sostituisce il tubo principale con una TCV per la crisi in modalità flow."""
        main_link_name = self.get_main_link()
        if not main_link_name:
            return
        link = self.wn.get_link(main_link_name)
        n1, n2 = link.start_node_name, link.end_node_name
        self.wn.remove_link(main_link_name)
        self.wn.add_valve(
            name='Main_Control_Valve', start_node_name=n1, end_node_name=n2,
            diameter=link.diameter, valve_type='TCV',
            initial_setting=1000.0, initial_status='CLOSED'
        )

    def instrument_existing_tanks(self, use_pumps: bool = True, min_boost: float = 10.0,
                                   log_filename: str = "water_network_setup.txt"):
        """Strumenta le cisterne esistenti per controllo IoT (retrofit)."""
        log_path = Path("Log_review") / log_filename
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n[SETUP] INSTRUMENTING EXISTING TANKS (Retrofit IoT)\n")
            f.write("-" * 60 + "\n")
            tanks = self.wn.tank_name_list
            if not tanks:
                f.write("  No existing tanks to instrument.\n")
            for tank_name in tanks:
                connected_links = [
                    l for l in self.wn.link_name_list
                    if (self.wn.get_link(l).start_node_name == tank_name
                        or self.wn.get_link(l).end_node_name == tank_name)
                ]
                for l_name in connected_links:
                    link = self.wn.get_link(l_name)
                    junc_name = (link.start_node_name
                                 if link.end_node_name == tank_name
                                 else link.end_node_name)
                    tank_node = self.wn.get_node(tank_name)
                    junc_node = self.wn.get_node(junc_name)
                    height_diff = (tank_node.elevation - junc_node.elevation) + tank_node.max_level
                    boost = max(min_boost, height_diff)
                    self.wn.remove_link(l_name)
                    self._add_iot_control_to_tank(junc_name, tank_name, junc_name,
                                                   link.diameter, boost, use_pumps=use_pumps)
                    f.write(f"    * Retrofitted {l_name} -> Node {junc_name} (Boost: {boost:.2f}m)\n")
            f.write("-" * 60 + "\n")

    # ──────────────────── Crisis Application ────────────────
    def apply_crisis_reduction(self, sim, ratio: float, step: int,
                                mode: str = 'flow',
                                log_filename: str = "crisis_status.txt"):
        """
        Applica la riduzione della crisi alla rete e logga l'evento fisico.

        FIX: il blocco `if mode ==` è ora al livello del metodo (era erroneamente
        dentro il blocco `except AttributeError`).
        FIX: rimossa l'importazione errata di `wntr` (si usa `mwntr` già importato).
        """
        log_dir = Path("Log_review")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / log_filename

        if step == 0:
            with log_path.open("w") as f:
                f.write("MODE | STEP | RATIO | VALUE (Head/Coeff) | REDUCTION\n")
                f.write("-" * 65 + "\n")

        # Recupero tempo di simulazione — robusto a diverse implementazioni del simulatore
        try:
            current_time_s = int(sim.time)
        except AttributeError:
            current_time_s = int(getattr(sim, '_currentTime', step * 300))

        # ── BLOCCO MODE: correttamente al livello del metodo ──────────────────
        if mode == 'pressure':
            # Apertura valvola principale se presente (compatibilità con modalità flow)
            try:
                v_link = sim._wn.get_link('Main_Control_Valve')
                v_link._user_status = mwntr.network.elements.LinkStatus.Open
                v_link._internal_status = mwntr.network.elements.LinkStatus.Open
                v_link._setting = 0.0
            except KeyError:
                pass  # Valvola non presente in questa configurazione

            for res_name in self.wn.reservoir_name_list:
                res = sim._wn.get_node(res_name)
                if not hasattr(res, '_original_head'):
                    res._original_head = res.head_timeseries.base_value

                new_head = res._original_head * ratio

                if abs(getattr(res, '_last_ratio', 1.0) - ratio) > 0.005:
                    with log_path.open("a") as f:
                        f.write(f"PRES | {step:<4} | {ratio:<5.2f} | "
                                f"Head: {new_head:<10.2f} | -{(1 - ratio) * 100:.1f}%\n")
                    res._last_ratio = ratio
                    res.head_timeseries.base_value = new_head

                    # Aggiornamento diretto del modello AML (solver Newton)
                    try:
                        if hasattr(sim, '_model') and hasattr(sim._model, 'source_head'):
                            if res_name in sim._model.source_head:
                                sim._model.source_head[res_name].value = new_head
                    except Exception as exc:
                        logger.debug("AML model update skipped: %s", exc)

        elif mode == 'flow':
            loss_coeff = max(1.0, 500_000.0 * (1.0 - ratio) ** 2)
            try:
                valve = sim._wn.get_link('Main_Control_Valve')
            except KeyError:
                logger.warning("Main_Control_Valve not found — flow crisis skipped at step %d", step)
                return ratio

            if abs(getattr(valve, '_last_ratio', 1.0) - ratio) > 0.01:
                with log_path.open("a") as f:
                    f.write(f"FLOW | {step:<4} | {ratio:<5.2f} | "
                            f"Coeff: {loss_coeff:<10.2f} | -{(1 - ratio) * 100:.1f}%\n")

                # Pulizia vecchi controlli crisi
                for mgr in [sim._presolve_controls, sim._postsolve_controls,
                             sim._rules, sim._feasibility_controls]:
                    to_remove = [
                        c for c in mgr._controls
                        if hasattr(c, '_name') and c._name and c._name.startswith("CrisisCtrl_")
                    ]
                    for ctrl in to_remove:
                        mgr.deregister(ctrl)
                        try:
                            sim._change_tracker.deregister(ctrl)
                        except Exception:
                            pass

                for n in [n for n in sim._wn.control_name_list if n.startswith("CrisisCtrl_")]:
                    sim._wn.remove_control(n)

                next_fire = sim.get_sim_time() + sim.hydraulic_timestep()
                control_name = f"CrisisCtrl_Valve_{int(next_fire)}"
                action = mwntr.network.controls.ControlAction(valve, 'setting', loss_coeff)
                condition = mwntr.network.controls.SimTimeCondition(sim._wn, '=', next_fire)
                ctrl = mwntr.network.controls.Control(
                    condition, action, name=control_name,
                    priority=mwntr.network.controls.ControlPriority.high
                )
                sim._wn.add_control(control_name, ctrl)
                sim._add_control(ctrl)
                sim._register_controls_with_observers()
                valve._last_ratio = ratio

        return ratio

    # ──────────────────── IoT Wiring ────────────────────────
    def _add_iot_control_to_tank(self, junc_name: str, tank_name: str,
                                  tank_id: str, diameter: float,
                                  boost_head: float, use_pumps: bool = True):
        v_name = f"IoT_Valve_{tank_id}"
        p_name = f"IoT_Pump_{tank_id}"
        curve_name = f"Curve_{p_name}"
        log_path = Path("Log_review") / "water_network_setup.txt"

        self.wn.add_valve(
            name=v_name, start_node_name=tank_name, end_node_name=junc_name,
            diameter=diameter, valve_type='TCV',
            initial_setting=1000.0, initial_status='CLOSED'
        )
        self.iot_valves.append(v_name)

        if use_pumps:
            self.wn.add_curve(curve_name, 'HEAD', [
                (0.0,   boost_head * 1.2),
                (0.005, boost_head * 1.1),
                (0.01,  float(boost_head)),
            ])
            self.wn.add_pump(
                name=p_name, start_node_name=junc_name, end_node_name=tank_name,
                pump_type='HEAD', pump_parameter=curve_name, initial_status='CLOSED'
            )
            self.iot_pumps.append(p_name)

        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"  [HARDWARE DEPLOYED] {tank_name} <--> {junc_name}\n")
            f.write(f"    - Valve: {v_name} (Initial: CLOSED, K=1000)\n")
            if use_pumps:
                f.write(f"    - Pump:  {p_name}\n")
            f.write("-" * 50 + "\n")

    # ──────────────────── Simulation Options ────────────────
    def set_simulation_options(self, timestep_s: int = 300,
                                required_pressure: float = 35.0,
                                minimum_pressure: float = 0.0):
        """Configura mwntr per simulazione PDA."""
        self.wn.options.time.duration = timestep_s
        self.wn.options.time.hydraulic_timestep = timestep_s
        self.wn.options.time.report_timestep = timestep_s
        self.wn.options.hydraulic.demand_model = 'PDA'
        self.wn.options.hydraulic.minimum_pressure = minimum_pressure
        self.wn.options.hydraulic.required_pressure = required_pressure

    # ──────────────────── Priority Nodes ────────────────────
    def _get_priority_nodes(self) -> list:
        """Restituisce i nodi con tag 'USER_1_P' (nodi prioritari)."""
        return [
            j for j in self.wn.junction_name_list
            if self.wn.get_node(j).tag == 'USER_1_P'
        ]

    # ──────────────────── Agent-Controlled Valves ──────────────
    def instrument_selected_pipes_as_valves(self, target_pipe_names: list,
                                            log_filename: str = "water_network_setup.txt"):
        """
        Sostituisce pipe specifiche con valvole TCV controllabili dall'agente.
        
        Questo metodo consente di genericizzare la selezione delle valvole di isolamento
        per qualsiasi rete, indipendentemente dal nome dei link.
        
        Args:
            target_pipe_names: Lista dei nomi delle pipe da convertire in valvole TCV.
            log_filename: File di log per tracciare le operazioni.
        """
        log_path = Path("Log_review") / log_filename
        self.controllable_isolation_valves = []
        
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n[SETUP] INSTRUMENT SELECTED PIPES AS CONTROLLABLE VALVES\n")
            f.write("-" * 60 + "\n")
            
            for pipe_name in target_pipe_names:
                try:
                    pipe = self.wn.get_link(pipe_name)
                    start_node = pipe.start_node_name
                    end_node = pipe.end_node_name
                    diameter = pipe.diameter
                    
                    self.wn.remove_link(pipe_name)
                    
                    valve_name = f"AgentCtrl_{pipe_name}"
                    self.wn.add_valve(
                        name=valve_name,
                        start_node_name=start_node,
                        end_node_name=end_node,
                        diameter=diameter,
                        valve_type='TCV',
                        initial_setting=1000.0,
                        initial_status='CLOSED'
                    )
                    self.controllable_isolation_valves.append(valve_name)
                    f.write(f"  ✓ {pipe_name}: {start_node} → {end_node}\n")
                    f.write(f"    → Valve: {valve_name} (TCV, Initial: CLOSED)\n")
                    
                except KeyError:
                    f.write(f"  ✗ {pipe_name}: NOT FOUND in network\n")
                except Exception as exc:
                    f.write(f"  ✗ {pipe_name}: ERROR - {str(exc)[:60]}\n")
            
            f.write("-" * 60 + "\n")
            f.write(f"Total controllable isolation valves: {len(self.controllable_isolation_valves)}\n")