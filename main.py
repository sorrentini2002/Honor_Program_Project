import os
import sys
import random
import math
import subprocess
import importlib
from pathlib import Path
from typing import Dict, List, Set, Any, Optional
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
import json
import datetime


dyn_wntr_path = 'Dyn-WNTR'
lorasim_pkg = 'LoRaSim-master'

for path in [dyn_wntr_path, os.path.join(dyn_wntr_path, 'mwntr'), lorasim_pkg]:
    if os.path.exists(path) and path not in sys.path:
        sys.path.insert(0, path)

print(f"✓ Working directory: {os.getcwd()}")

import importlib
import mwntr
from mwntr.network import LinkStatus
from mwntr.sim.interactive_network_simulator import MWNTRInteractiveSimulator
from LoRaSim.MarkovChain import MarkovChain
import Strategies
import Agents
import Agents.heuristic_agent
import Crises

importlib.reload(Strategies)
importlib.reload(Agents.heuristic_agent)
importlib.reload(Agents)
importlib.reload(Crises)

from Strategies import STRATEGY_MAP
from Agents import AGENT_MAP
from Crises import CRISIS_MAP

import os
import math
import random

class LoRaSystem:
    """Simulates LoRaWAN communication with integrated real-time file logging."""
    
    def __init__(self, models_dir=None, log_filename="latest_simulation_log.txt",config_params=None):
        self.gateway_pos = (0, 0)
        self.sensors = {}
        self.tx_interval_s = 1800
        self.total_transmissions = 0
        self.total_collisions = 0
        
        self.history = [] 
        self.debug_log = []
        
        # Gestione File di Log
        log_dir = "Log_review"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        self.log_path = os.path.join(log_dir, log_filename)
        
        # Sovrascriviamo il file all'inizio di ogni nuova istanza (nuova run)
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("="*60 + "\n")
            f.write(f"  LORA CO-SIMULATION SESSION: {datetime.datetime.now()}\n")
            f.write("="*60 + "\n")
            if config_params:
                f.write("\n[CONFIG] Simulation Parameters:\n")
                for k, v in config_params.items():
                    f.write(f"  > {k:20}: {v}\n")
                f.write("-" * 60 + "\n\n")

        # Percorso dei modelli Markoviani
        if models_dir:
            self.models_dir = models_dir
        else:
            self.models_dir = os.path.join('LoRaSim-master', 'LoRaSim', 'Models')
    
    def _log(self, message, level="INFO"):
        """Centralized logging: updates memory list and writes to physical file."""
        formatted_msg = f"[{level:5}] {message}"
        self.debug_log.append(formatted_msg)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(formatted_msg + "\n")

    def setup_gateway(self, pos):
        self.gateway_pos = pos
        self._log(f"Gateway set at {pos}")

    def _get_best_model(self, dist_km, sf):
        try:
            dist_str = "650m" if dist_km <= 1.0 else "2km"
            dr_str = f"DR{max(0, min(6, 12 - sf))}"

            if not os.path.exists(self.models_dir):
                self._log(f"CRITICAL: Models directory NOT FOUND at {self.models_dir}")
                return None

            available = [f for f in os.listdir(self.models_dir) if f.endswith('.ini')]
            matches = [m for m in available if dist_str in m and dr_str in m]
            
            if matches:
                best = matches[0]
                self._log(f"Model selection: {best} for dist_km={dist_km:.2f}, sf={sf}")
            else:
                best = available[0] if available else None
                if best:
                    self._log(f"WARNING: No exact match for {dist_str}/{dr_str}. Fallback: {best}")
                else:
                    self._log(f"ERROR: No .ini files in {self.models_dir}")
                    return None

            model = MarkovChain()
            model.loadFromFile(os.path.join(self.models_dir, best))
            return model
        except Exception as e:
            self._log(f"EXCEPTION in _get_best_model (dist={dist_km}, sf={sf}): {str(e)}")
            return None

    def register_iot_sensors(self, valves, wn, mode='simple', sf_mode='sequential', fixed_sf=10):
        self._log(f"NETWORK TOPOLOGY: {len(valves)} sensors registered", level="SETUP")
        available_sfs = [7, 8, 9, 10, 11, 12]

        for i, v_name in enumerate(valves):
            # 1. Coordinate
            node_name = v_name.replace("IoT_Valve_", "")
            
            # Versione alternativa equivalente
            if node_name in wn.nodes:
                node = wn.get_node(node_name)
            else:
                # Se il nome non è un nodo (magari è il nome della valvola stessa), prendiamo il nodo di inizio del link
                node = wn.get_node(wn.get_link(v_name).start_node_name)

            
            tx_x, tx_y = node.coordinates
            gw_x, gw_y = self.gateway_pos
            real_dist = math.sqrt((tx_x - gw_x)**2 + (tx_y - gw_y)**2) / 1000.0
            

            # 2. Assegnazione SF
            if sf_mode == 'random': sf = random.choice(available_sfs)
            elif sf_mode == 'fixed': sf = fixed_sf
            elif sf_mode == 'sequential': sf = available_sfs[i % len(available_sfs)]
            else: # distance
                if real_dist < 0.5: sf = 7
                elif real_dist < 1.0: sf = 8
                elif real_dist < 1.5: sf = 9
                elif real_dist < 2.0: sf = 10
                elif real_dist < 3.0: sf = 11
                else: sf = 12

            # 3. Hops
            hop_models = []
            if mode == 'multihop':
                remaining = real_dist
                hop_count = 0
                while remaining > 0:
                    hop_count += 1
                    d_hop = min(remaining, 2.0)
                    model = self._get_best_model(d_hop, sf)
                    hop_models.append({'model': model, 'dist': d_hop, 'state': 1})
                    self._log(f"  └─ HOP {hop_count}: Dist={d_hop:.2f}km, SF={sf}")
                    remaining -= 2.0
            elif mode == "simple":
                d_eff = 0.65 if real_dist < 1.3 else 2.0
                model = self._get_best_model(d_eff, sf)
                hop_models.append({'model': model, 'dist': d_eff, 'state': 1})
                self._log(f"  └─ SINGLE-HOP: Dist_Eff={d_eff:.2f}km, SF={sf}")

            self.sensors[v_name] = {
                'distance': real_dist, 'sf': sf, 'hop_models': hop_models,
                'last_tx_time': -9999.0, 'data': {}
            }

            self._log(f"NODE {v_name:15} | SF{sf:2} | Dist: {real_dist:5.2f}km | Hops: {len(hop_models)}", level="REG")
            
    def get_packet_loss_rate(self):
        if self.total_transmissions == 0: return 0.0
        plr = (self.total_collisions / self.total_transmissions) * 100.0
        self._log(f"STATS CHECK: PLR={plr:.2f}% | Total TX={self.total_transmissions}")
        return plr

    def step(self, current_time, timestep_s):
        received = []
        for s_id, s_node in self.sensors.items():
            if current_time - s_node['last_tx_time'] >= self.tx_interval_s:
                self.total_transmissions += 1
                s_node['last_tx_time'] = current_time
                success = True
                hops_results = []
                
                for i, hop in enumerate(s_node['hop_models']):
                    if hop['model']:
                        if hop['state'] == 1:
                            if random.random() <= hop['model'].p10: hop['state'] = 0
                        else:
                            if random.random() <= hop['model'].p01: hop['state'] = 1
                        if hop['state'] == 0: success = False
                    else:
                        success = False
                    hops_results.append("OK" if success else "LOST")
                
                status_str = "SUCCESS" if success else "FAILED"
                self._log(f"TX @{current_time:8.1f}s | {s_id:15} | SF{s_node['sf']:2} | "
                    f"{status_str:7} | Path: [{'->'.join(hops_results)}]", 
                    level="COMM"
                )
                
                if success:
                    received.append({'id': s_id, 'data': s_node.get('data', {})})
                else:
                    self.total_collisions += 1
        return received

class WaterNetworkManager:
    def __init__(self, wn_model):
        self.wn = mwntr.network.WaterNetworkModel(wn_model) if isinstance(wn_model, str) else wn_model
        self.iot_tanks = {}
        self.iot_valves = []
        self.iot_pumps = []

    def activate_network_demands(self, avg_demand=15.0, dist_type='normal', 
                                 pattern_mode='random', fixed_pattern_id=None,
                                 log_filename="water_network_setup.txt"):

            # Percorso del log dedicato all'idraulica
            log_dir = "Log_review"
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            log_path = os.path.join(log_dir, log_filename)
            pattern_names = []
            try:
                with open('Network/patterns.json', 'r') as f:
                    patterns_dict = json.load(f)
                pattern_names = list(patterns_dict.keys())

                for name, multipliers in patterns_dict.items():
                    if name not in self.wn.pattern_name_list:
                        self.wn.add_pattern(name, multipliers)
            except Exception as e:
                pattern_names = [None]

            # Inizializziamo il file di log con l'intestazione
            with open(log_path, "w", encoding="utf-8") as log_file:
                log_file.write("="*60 + "\n")
                log_file.write(f"  WATER NETWORK DEMAND SETUP: {datetime.datetime.now()}\n")
                log_file.write("="*60 + "\n")
                log_file.write(f"Config: avg_demand={avg_demand}, dist={dist_type}, mode={pattern_mode}\n\n")
                log_file.write(f"{'JUNCTION_ID':<20} | {'BASE_DEMAND':<12} | {'PATTERN_NAME':<15}\n")
                log_file.write("-" * 60 + "\n")

                for i, j_name in enumerate(self.wn.junction_name_list):
                    junction = self.wn.get_node(j_name)

                    if dist_type == 'original':
                        # RECUPERO DATI ORIGINALI DAL FILE .INP
                        if junction.demand_timeseries_list:
                            # Prendiamo la prima domanda definita nel file
                            orig_demand = junction.demand_timeseries_list[0]
                            base_val = orig_demand.base_value
                            pattern_to_use = orig_demand.pattern_name
                        else:
                            base_val = 0.0
                            pattern_to_use = None
                    else:
                        # LOGICA STOCASTICA (Normal, Lognormal, Uniform)
                        if dist_type == 'normal':
                            base = np.random.normal(avg_demand, avg_demand * 0.2)
                        elif dist_type == 'lognormal'and pattern_names:
                            sigma = 0.25
                            mu = np.log(avg_demand) - 0.5 * sigma**2
                            base = np.random.lognormal(mu, sigma)
                        elif dist_type == 'uniform'and pattern_names:
                            base = np.random.uniform(avg_demand * 0.5, avg_demand * 1.5)

                        base_val = max(0.1, base)

                    # Selezione del pattern
                    if pattern_mode == 'single' and fixed_pattern_id:
                        pattern_to_use = str(fixed_pattern_id)
                    elif pattern_mode == 'random':
                        pattern_to_use = random.choice(pattern_names)
                    elif pattern_mode=='sequential':
                        pattern_to_use = pattern_names[i % len(pattern_names)]

                    # Applicazione al simulatore
                    junction.demand_timeseries_list.clear()
                    junction.add_demand(base=base_val, pattern_name=pattern_to_use)

                    # Scrittura nel LOG dedicato
                    log_file.write(f"{j_name:<20} | {base_val:<12.4f} | {pattern_to_use:<15}\n")

                log_file.write("\n" + "="*60 + "\n")
                log_file.write(f"Setup completed for {len(self.wn.junction_name_list)} nodes.\n")
                




    def remove_existing_tanks(self, log_filename="water_network_setup.txt"):
        """Removes all original tanks and their connected links from the network."""
        log_path = os.path.join("Log_review", log_filename)
        
        tanks = [name for name, node in self.wn.nodes() if node.node_type == 'Tank']
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n[SETUP] REMOVING EXISTING TANKS ({len(tanks)} found)\n")
            f.write("-" * 60 + "\n")
            
            if not tanks:
                f.write("  No existing tanks found to remove.\n")
            else:
                for t_name in tanks:
                    # 1. Identifichiamo i link collegati a questo nodo
                    connected_links = [l_name for l_name, link in self.wn.links() 
                                      if link.start_node_name == t_name or link.end_node_name == t_name]
                    
                    f.write(f"  - Node {t_name}: Removing {len(connected_links)} connected links first...\n")
                    
                    # 2. Rimuoviamo i link
                    for l_name in connected_links:
                        f.write(f"    * Removing link: {l_name}\n")
                        self.wn.remove_link(l_name)
                    
                    # 3. Ora possiamo rimuovere il nodo in sicurezza
                    f.write(f"    * Removing node: {t_name}\n")
                    self.wn.remove_node(t_name, with_control=True)
                
                f.write(f"  Successfully removed all {len(tanks)} original tanks and their connections.\n")
                
            f.write("-" * 60 + "\n")

    def instrument_existing_tanks(self, use_pumps=True, min_boost=10.0, log_filename="water_network_setup.txt"):
        """Closes existing tank valves, adds them to IoT control and logs details."""
        log_path = os.path.join("Log_review", log_filename)
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n[SETUP] INSTRUMENTING EXISTING TANKS (Retrofit IoT)\n")
            f.write("-" * 60 + "\n")
            
            tanks = self.wn.tank_name_list
            if not tanks:
                f.write("  No existing tanks to instrument.\n")
            
            for tank_name in tanks:
                connected_links = [l for l in self.wn.link_name_list if
                                   self.wn.get_link(l).start_node_name == tank_name or
                                   self.wn.get_link(l).end_node_name == tank_name]

                if connected_links:
                    link = self.wn.get_link(connected_links[0])
                    junc_name = link.start_node_name if link.end_node_name == tank_name else link.end_node_name

                    tank_node = self.wn.get_node(tank_name)
                    junc_node = self.wn.get_node(junc_name)

                    # Calcolo altezza relativa
                    height_diff = (tank_node.elevation - junc_node.elevation) + tank_node.max_level
                    boost = max(min_boost, height_diff)

                    f.write(f"  - Retrofitting Tank: {tank_name}\n")
                    f.write(f"    * Elevation: Tank={tank_node.elevation:.2f}m, Junction={junc_node.elevation:.2f}m\n")
                    f.write(f"    * Max Head required (Boost): {boost:.2f}m\n")

                    try:
                        # Rimuoviamo il link vecchio e aggiungiamo il controllo IoT
                        self.wn.remove_link(connected_links[0])
                        self._add_iot_control_to_tank(junc_name, tank_name, tank_name, link.diameter, boost, use_pumps=use_pumps)
                        f.write(f"    * Status: SUCCESS (IoT links and pumps installed)\n")
                    except Exception as e:
                        f.write(f"    * Status: FAILED Error: {str(e)}\n")

            f.write("-" * 60 + "\n")


    def add_iot_tanks(self, n_tanks=3, strategy_name='random', min_boost=15.0, use_pumps=True, log_filename="water_network_setup.txt"):
        """Adds IoT tanks and logs their placement details, including node demand and strategy."""
        import json
        import os
        log_path = os.path.join("Log_review", log_filename)
        
        try:
            with open('Strategies/tank_configs.json', 'r') as f:
                all_configs = json.load(f)
            tank_types = list(all_configs.keys())
        except Exception as e:
            print(f"⚠️ Error loading tank configs: {e}")
            return []

        # Identificazione strategia
        strategy_class = STRATEGY_MAP.get(strategy_name, STRATEGY_MAP['random'])
        target_nodes = strategy_class(self.wn).get_nodes(n_tanks)

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n[SETUP] DEPLOYING NEW IoT TANKS\n")
            f.write(f"Strategy used: {strategy_name.upper()}\n")
            f.write("-" * 60 + "\n")
            f.write(f"{'TANK_NAME':<20} | {'NODE':<10} | {'ELEV':<7} | {'T_ELEV':<7} | {'DEMAND':<8} | {'STATUS':<10}\n")
            f.write("-" * 60 + "\n")

            for i, junc_name in enumerate(target_nodes):
                junc_node = self.wn.get_node(junc_name)
                t_type = tank_types[i % len(tank_types)]
                cfg = all_configs[t_type]
                
                # Usiamo lo stesso valore di 'min_boost' scelto nell'engine
                total_height = min_boost + cfg['max_level']
                t_name = f"IoT_Tank_{t_type}_{i}"
                
                # Otteniamo la domanda base per il log
                base_dem = sum(d.base_value for d in junc_node.demand_timeseries_list)

                try:
                    # Creazione fisica del serbatoio
                    self.wn.add_tank(name=t_name, 
                                     elevation=junc_node.elevation + min_boost,
                                     init_level=cfg['init_level'], 
                                     min_level=cfg['min_level'],
                                     max_level=cfg['max_level'], 
                                     diameter=cfg['tank_diameter'])
                    
                    self.wn.get_node(t_name).coordinates = junc_node.coordinates

                    # Connessione IoT
                    self._add_iot_control_to_tank(junc_name, t_name, f"New_{i}", 
                                                   cfg['pipe_diameter'], total_height, 
                                                   use_pumps=use_pumps)
                    
                    status = "SUCCESS"
                except Exception as e:
                    status = f"ERROR: {str(e)[:10]}"

                f.write(f"{t_name:<20} | {junc_name:<10} | {junc_node.elevation:<7.1f} | {junc_node.elevation + min_boost:<7.1f} | {base_dem:<8.2f} | {status:<10}\n")

            f.write("-" * 60 + "\n")
        return self.iot_valves

    def fix_reservoir_head(self, target_head=100.0, log_filename="water_network_setup.txt"):
        """Ensures reservoirs have a realistic head and no interfering patterns."""
        import os
        log_path = os.path.join("Log_review", log_filename)
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n[SETUP] FIXING RESERVOIR HEADS (Target: {target_head})\n")
            f.write("-" * 60 + "\n")
            
            for res_name in self.wn.reservoir_name_list:
                res = self.wn.get_node(res_name)
                old_head = res.head_timeseries.base_value
                old_pattern = res.head_timeseries.pattern_name
                
                # Applichiamo la correzione: Head fisso e niente pattern
                res.head_timeseries.base_value = target_head
                res.head_timeseries.pattern_name = None 
                
                f.write(f"  - Reservoir {res_name}: Head {old_head} -> {target_head}, Pattern {old_pattern} -> None\n")
            
            f.write("-" * 60 + "\n")

    def get_main_link(self, log_filename="water_network_setup.txt"):
        """Identifies the largest pipe connected to any reservoir."""
        import os
        log_path = os.path.join("Log_review", log_filename)
        source_links = []
        
        for res_name in self.wn.reservoir_name_list:
            links = self.wn.get_links_for_node(res_name)
            for l_name in links:
                source_links.append(self.wn.get_link(l_name))
        
        if not source_links:
            return None
            
        # Troviamo quello con diametro massimo
        main_link = max(source_links, key=lambda l: l.diameter)
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n[SETUP] MAIN SOURCE IDENTIFIED\n")
            f.write(f"  - Reservoir Source: {main_link.start_node_name}\n")
            f.write(f"  - Main Pipe: {main_link.name} (Diameter: {main_link.diameter})\n")
            f.write("-" * 60 + "\n")
            
        return main_link.name

    def instrument_source_with_valve(self):
        """Replaces the main source pipe with a Control Valve for flow-based crisis."""
        main_link_name = self.get_main_link()
        if not main_link_name:
            return

        link = self.wn.get_link(main_link_name)
        n1, n2 = link.start_node_name, link.end_node_name
        
        # Sostituiamo il tubo con una valvola (TCV - Throttle Control Valve)
        self.wn.remove_link(main_link_name)
        self.wn.add_valve(name='Main_Control_Valve', 
                          start_node_name=n1, 
                          end_node_name=n2,
                          diameter=link.diameter, 
                          valve_type='TCV', 
                          initial_setting=1000.0,
                          initial_status='OPEN')

    def apply_crisis_reduction(self, sim, ratio, step, mode='flow', log_filename="crisis_status.txt"):
        """Applies ratio and logs the physical drop for both pressure and flow modes."""
        import os
        import wntr
        
        # Assicuriamoci che la cartella esista
        log_dir = "Log_review"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        log_path = os.path.join(log_dir, log_filename)
        
        # All'inizio della simulazione (step 0), puliamo il file e scriviamo l'intestazione
        if step == 0:
            with open(log_path, "w") as f:
                f.write(f"MODE | STEP | RATIO | VALUE (Head/Coeff) | REDUCTION\n")
                f.write("-" * 65 + "\n")

        # Recupero del tempo di simulazione (usa try-except per supportare diverse implementazioni del simulatore)
        try:
            current_time_s = int(sim.time)
        except AttributeError:
            current_time_s = int(getattr(sim, '_currentTime', step * 300)) # Fallback a 300s se non trovato

        if mode == 'pressure':
            print("ATTENZIONE: La modifica dinamica della 'base_head' dei Reservoir in tempo reale non è supportata dai Control di WNTR.")
            print("La pressione non cambierà. Ti preghiamo di usare mode='flow' per simulare la crisi agendo sulla Main_Control_Valve.")
            
            # Manteniamo la vecchia logica di calcolo solo per i log, ma non avrà impatto idraulico dinamico
            for res_name in self.wn.reservoir_name_list:
                res = self.wn.get_node(res_name)
                if not hasattr(res, '_original_head'):
                    res._original_head = res.head_timeseries.base_value
                
                new_head = res._original_head * ratio
                if abs(getattr(res, '_last_ratio', 1.0) - ratio) > 0.01:
                    with open(log_path, "a") as f:
                        f.write(f"PRES | {step:<4} | {ratio:<5.2f} | Head: {new_head:<10.2f} | -{(1-ratio)*100:.1f}%\n")
                    res._last_ratio = ratio

        elif mode == 'flow':

            loss_coeff = max(60.0 * ratio, 1.0)
            
            valve = sim._wn.get_link('Main_Control_Valve')
            
            # Interveniamo solo se c'è stata una reale variazione di ratio
            if abs(getattr(valve, '_last_ratio', 1.0) - ratio) > 0.01:
                with open(log_path, "a") as f:
                    f.write(f"FLOW | {step:<4} | {ratio:<5.2f} | Coeff: {loss_coeff:<10.2f} | -{(1-ratio)*100:.1f}%\n")
                
                # --- INIEZIONE DEL CONTROLLO WNTR PER LA CRISI ---
                control_name = f"CrisisCtrl_Valve_{current_time_s}"
                action = wntr.network.controls.ControlAction(valve, 'setting', loss_coeff)
                
                # Usiamo la condizione infallibile: scatta subito
                condition = wntr.network.controls.SimTimeCondition(sim._wn, '>=', 0)
                
                ctrl = wntr.network.controls.Control(condition, action, name=control_name)
                sim._wn.add_control(control_name, ctrl)
                # ---------------------------------------------------
                
                valve._last_ratio = ratio

        # Rimosso: sim.rebuild_hydraulic_model = True. Non serve più e rallentava tutto.
        return ratio

    def _add_iot_control_to_tank(self, junc_name, tank_name, tank_id, diameter, boost_head, use_pumps=True):
        """Install control hardware (Valve & Pump) and log the deployment."""
        import os
        v_name = f"IoT_Valve_{tank_id}"
        p_name = f"IoT_Pump_{tank_id}"
        curve_name = f"Curve_{p_name}"
        log_path = os.path.join("Log_review", "water_network_setup.txt")

        # 1. Installazione Valvola (Hardware di scarico)
        self.wn.add_valve(name=v_name, start_node_name=tank_name, end_node_name=junc_name,
                          diameter=diameter, valve_type='TCV', initial_setting=1.0)
        self.iot_valves.append(v_name)

        # 2. Installazione Pompa (Hardware di ricarica)
        if use_pumps:
            self.wn.add_curve(curve_name, 'HEAD', [(0.0, boost_head * 1.2), (1.0, boost_head * 1.1), (2.0, float(boost_head))])
            self.wn.add_pump(name=p_name, start_node_name=junc_name, end_node_name=tank_name,
                             pump_type='HEAD', pump_parameter=curve_name)
            self.iot_pumps.append(p_name)

        # 3. Log di conferma creazione
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"  [HARDWARE DEPLOYED] {tank_name} <--> {junc_name}\n")
            f.write(f"    - Valve created: {v_name} (Initial: CLOSED)\n")
            if use_pumps:
                f.write(f"    - Pump created:  {p_name} (Ready for control)\n")
            f.write("-" * 50 + "\n")

    def set_simulation_options(self, timestep_s=300):
        """Configures WNTR for PDA simulation."""
        self.wn.options.time.duration = timestep_s
        self.wn.options.time.hydraulic_timestep = timestep_s
        self.wn.options.time.report_timestep = timestep_s
        self.wn.options.hydraulic.demand_model = 'PDA'
        self.wn.options.hydraulic.minimum_pressure = 0.0
        self.wn.options.hydraulic.required_pressure = 0.1


from Agents import AGENT_MAP

def calculate_gateway_pos(wn, mode='center', offset_dist=0.0):
    import random
    import math
    
    # Estrae tutte le coordinate x e y (filtrando i nodi senza coordinate)
    x_coords = [node.coordinates[0] for _, node in wn.nodes() if node.coordinates is not None]
    y_coords = [node.coordinates[1] for _, node in wn.nodes() if node.coordinates is not None]
    
    center_x = sum(x_coords) / len(x_coords)
    center_y = sum(y_coords) / len(y_coords)
    
    if mode == 'center':
        pos = (center_x, center_y)
    elif mode == 'random_offset':
        angle = random.uniform(0, 2 * math.pi)
        pos = (center_x + offset_dist * math.cos(angle), 
               center_y + offset_dist * math.sin(angle))
    else:
        # Seleziona un nodo a caso tra quelli esistenti
        node_name = random.choice(list(wn.node_name_list))
        pos = wn.get_node(node_name).coordinates

    return pos


class CoSimulationEngine:
    def __init__(self, network_file, duration_hours=24, step_min=5,
                 remove_tanks=False, crisis_mode='pressure', decay_type='linear',
                 decay_rate=0.1, avg_demand=15.0, dist_type='normal',
                 pattern_mode='random', n_tanks=3, strategy_name='random',
                 crisis_start_step=20,
                 agent_name='heuristic', agent_threshold=0.90, agent_aggression=5.0,
                 enable_pumps=True, lora_mode='multihop',
                 gateway_mode='center', min_boost=10.0, gateway_offset=0.0, 
                 sf_mode='distance', fixed_sf=10, crisis_params=None, crisis_start_hour=2.0, agent_alpha=0.8, target_head=200):

        self.timestep_s = step_min * 60
        self.n_steps = int((duration_hours * 3600) / self.timestep_s)
        self.crisis_start_step = int((crisis_start_hour * 60) / step_min)
        self.water_net = WaterNetworkManager(network_file)
        self.min_boost = min_boost
        self.avg_demand = avg_demand
        self.target_head = target_head

        # 1. Setup Idraulico
        self.water_net.activate_network_demands(avg_demand=avg_demand, dist_type=dist_type, pattern_mode=pattern_mode)
        self.water_net.fix_reservoir_head(target_head=target_head) 

        if remove_tanks:
            self.water_net.remove_existing_tanks()
        else:
            self.water_net.instrument_existing_tanks(use_pumps=enable_pumps, min_boost=self.min_boost)

        if crisis_mode == 'flow':
            self.water_net.instrument_source_with_valve()

        if n_tanks > 0:
            self.water_net.add_iot_tanks(n_tanks=n_tanks, strategy_name=strategy_name, 
                                         min_boost=self.min_boost, use_pumps=enable_pumps)

        # 2. Setup Crisi
        crisis_class = CRISIS_MAP.get(decay_type, CRISIS_MAP['linear'])
        if crisis_params:
            self.crisis_model = crisis_class(**crisis_params)
        else:
            self.crisis_model = crisis_class(decay_rate=decay_rate)
        self.crisis_mode_name = crisis_mode
        self.water_net.set_simulation_options(self.timestep_s)

        # 3. Setup LoRa (Cyber) & Gateway Logging
        self.lora_net = LoRaSystem()
        gw_pos = calculate_gateway_pos(self.water_net.wn, mode=gateway_mode, offset_dist=gateway_offset)
        self.lora_net.setup_gateway(gw_pos)
        self.lora_net.register_iot_sensors(self.water_net.iot_valves, self.water_net.wn,
                                            mode=lora_mode, sf_mode=sf_mode, fixed_sf=fixed_sf)

        # LOG DETTAGLIATO LORA
        gw_x, gw_y = gw_pos
        distances = []
        for sensor_id, sensor in self.lora_net.sensors.items():
                # 1. Recuperiamo la VALVOLA (Link)
                valve = self.water_net.wn.get_link(sensor_id)
                # 2. Prendiamo il NODO a cui è attaccata la valvola
                node_name = valve.start_node_name
                node = self.water_net.wn.get_node(node_name)
                
                # 3. Calcoliamo la distanza
                s_x, s_y = node.coordinates
                dist = math.sqrt((gw_x - s_x)**2 + (gw_y - s_y)**2)
                distances.append(dist)
        
        avg_dist = sum(distances) / len(distances) if distances else 0
        
        with open("Log_review/water_network_setup.txt", "a", encoding="utf-8") as f:
            f.write(f"\n[LoRa NETWORK SETUP]\n")
            f.write(f"  - Gateway Position: ({gw_x:.2f}, {gw_y:.2f}) [Mode: {gateway_mode}]\n")
            f.write(f"  - Total Sensors:    {len(distances)}\n")
            f.write(f"  - Average Distance: {avg_dist:.2f} meters\n")
            f.write(f"  - Max Distance:     {max(distances) if distances else 0:.2f} meters\n")
            f.write("-" * 60 + "\n")

        # 4. Inizializzazione Simulatore e Agente
        self.sim = MWNTRInteractiveSimulator(self.water_net.wn)
        agent_class = AGENT_MAP.get(agent_name, AGENT_MAP['heuristic'])
        self.agent = agent_class(self.water_net, self.lora_net, 
                                 threshold=agent_threshold, 
                                 aggression=agent_aggression,
                                 alpha=agent_alpha)
                            
        self.perf_log = "Log_review/agent_performance.txt"
        with open(self.perf_log, "w") as f:
            f.write("STEP | EXPECTED | ACTUAL | DIFF | SATISFACTION | TX_INT | OBJECTIVE\n")
            f.write("-" * 80 + "\n")

        # 5. Statistiche e Log di Crisi
        self.stats = {'time': [], 'satisfaction': [], 'packet_loss': [], 'tanks': [], 'reward': []}
        log_dir = "Log_review"
        if not os.path.exists(log_dir): os.makedirs(log_dir)
        with open(os.path.join(log_dir, "crisis_status.txt"), "w") as f:
            f.write(f"SIMULATION START: {datetime.datetime.now()}\n")
            f.write(f"MODE: {self.crisis_mode_name.upper()} | GATEWAY: {gateway_mode}\n")
            f.write("-" * 65 + "\n")
            f.write(f"MODE | STEP | RATIO | VALUE (Head/Coeff) | REDUCTION\n")
            f.write("-" * 65 + "\n")

    def run_simulation(self):

        saved_stochastic_demands = {}

        for j_name in self.water_net.wn.junction_name_list:
            node = self.water_net.wn.get_node(j_name)
            if node.demand_timeseries_list:
                # Salviamo il valore che activate_network_demands ha creato
                saved_stochastic_demands[j_name] = node.demand_timeseries_list[0].base_value
                # Azzeriamo SOLO per permettere il login della simulazione (Soft Start)
                node.demand_timeseries_list[0].base_value = 0.0

        try:
            main_valve = self.water_net.wn.get_link("Main_Control_Valve")
            # Inizializzala come completamente aperta
            main_valve.initial_status = mwntr.network.elements.LinkStatus.Open
            main_valve.initial_setting = 0.0
        except KeyError:
            pass # Se la valvola si chiama diversamente o non esiste, ignora

        self.sim.init_simulation()
        t = 0.0
        crisis_step = None

        demand_log_path = "Log_review/demand_distribution.csv"
        with open(demand_log_path, "w") as f:
            f.write("step,time_hours,expected_demand,actual_demand,satisfaction_pct\n")

        for step in range(self.n_steps):
            t += self.timestep_s


            self.sim._currentTime = int(t)
            # --- RIPRISTINO DOMANDA (Dallo step 1 in poi) ---
            if step == 1:
                for j_name, val in saved_stochastic_demands.items():
                    node = self.water_net.wn.get_node(j_name)
                    node.demand_timeseries_list[0].base_value = val

            if step == self.crisis_start_step:
                crisis_step = step

            if crisis_step is not None:
                steps_passed = step - crisis_step
                ratio = self.crisis_model.get_ratio(steps_passed)
                self.water_net.apply_crisis_reduction(self.sim, ratio, step, mode=self.crisis_mode_name)

            if hasattr(self.sim, '_wn'):
                old_controls = [c_name for c_name in self.sim._wn.control_name_list 
                                if c_name.startswith("AgentCtrl_") or c_name.startswith("CrisisCtrl_")]
                for c_name in old_controls:
                    self.sim._wn.remove_control(c_name)

            # Il cuore della co-simulazione
            s_current = self.agent.calculate_current_satisfaction(self.sim)
            pl = self.lora_net.get_packet_loss_rate()
            act = self.agent.decide_action(step, t, s_current)
            self.agent.apply_mitigation(act, self.sim, self.lora_net, t)
            
            self.sim.step_sim()

            # --- DEBUG ROBUSTO ---
            print(f"\n--- DEBUG STEP {step} ---")
            # 1. Verifica la fonte principale
            source_id = 'Source_Pump' # <-- Assicurati che questo ID esista nel tuo file .inp!
            if source_id in self.water_net.wn.link_name_list:
                s_link = self.water_net.wn.get_link(source_id)
                # Controllo se ci sono dati disponibili
                flow_data = self.sim.node_res['flow'].get(source_id, [])
                s_flow = flow_data[-1] if len(flow_data) > 0 else 0
                print(f"FONTE [{source_id}]: Stato={s_link.status}, Portata={s_flow:.4f}")
            # 2. Verifica i Serbatoi
            tank_links = [l for l in self.water_net.wn.link_name_list if 'tank' in l.lower() or 'cistern' in l.lower()]
            print(f"CISTERNE ATTIVE (Agente): {self.agent.opened_count}")
            for t_id in tank_links:
                t_link = self.water_net.wn.get_link(t_id)
                f_data = self.sim.node_res['flow'].get(t_id, [])
                t_flow = f_data[-1] if len(f_data) > 0 else 0
                print(f"  -> Link {t_id}: Stato={t_link.status}, Portata={t_flow:.4f}")
                print(f"DEBUG FISICO -> Link {t_id}: Stato={t_link.status}")
            # 3. Verifica Pressione Media (con controllo lista vuota)
            pressures = []
            for n in self.sim.node_res['pressure']:
                p_list = self.sim.node_res['pressure'][n]
                if len(p_list) > 0:
                    pressures.append(p_list[-1])
            if pressures:
                avg_p = sum(pressures) / len(pressures)
                print(f"PRESSIONE MEDIA RETE: {avg_p:.2f}")
            else:
                print("ATTENZIONE: Nessun dato di pressione disponibile! Il simulatore potrebbe essere bloccato.")
            print("------------------------")

            self.lora_net.step(t, self.timestep_s)

            # Raccolta metriche
            current_tx = self.lora_net.tx_interval_s

            res = self.sim.node_res

            # Nota: prendiamo l'ultimo valore [-1] che è quello appena calcolato dallo step
            exp_t = sum(res['expected_demand'][j][-1] for j in res['expected_demand'] if len(res['expected_demand'][j]) > 0)
            act_t = sum(res['demand'][j][-1] for j in res['demand'] if len(res['demand'][j]) > 0)
            sat_p = (act_t / exp_t * 100) if exp_t > 0 else 100.0
            
            # Scrittura su file
            with open("Log_review/demand_distribution.csv", "a") as f:
                f.write(f"{step},{t/3600:.2f},{exp_t:.2f},{act_t:.2f},{sat_p:.2f}\n")

            exp_total = sum(res['expected_demand'][j][-1] for j in res['expected_demand'] if len(res['expected_demand'][j]) > 0)
            act_total = sum(res['demand'][j][-1] for j in res['demand'] if len(res['demand'][j]) > 0)
            diff = exp_total - act_total

            s_real = self.agent.calculate_current_satisfaction(self.sim)
            fa = self.agent.compute_objective(s_real, self.lora_net.tx_interval_s)

            with open(self.perf_log, "a") as f:
                f.write(f"{step:<4} | {exp_total:<8.2f} | {act_total:<6.2f} | {diff:<4.2f} | {s_real*100:<11.1f}% | {current_tx:<6} | {fa:.4f}\n")


            self.stats['time'].append(t)
            self.stats['satisfaction'].append(s_real * 100)
            self.stats['packet_loss'].append(pl)
            self.stats['reward'].append(fa)
            self.stats['tanks'].append(self.agent.opened_count)

        return self.sim.get_results()


# Choice the network file:
network_file = 'Network/NET_30_PERFECT.inp'

    
    # Inizializzazione pulita del motore
    # NOTA: CoSimulationEngine si occupa internamente di caricare il file e aggiungere i tank.
engine = CoSimulationEngine(
        network_file,                   # selezione della rete
        duration_hours=3,                  # selezione della durata della simulazione
        step_min=2.5,                         # selezione dello step di aggiornamento
        remove_tanks=True,             # se non vogliamo mantenere i serbaoti esistenti          
        crisis_mode='flow',       # 'pressure' or 'flow' --> decide se la crisi è simulata tramite un cale di pressione della fonte o tramite una perdita del tubo a maggior portata
        decay_type='instant',          # 'linear','exponential','instant', 'logarithmic' or 'ornstein_uhlenbeck'
        crisis_params={
        #'decay_rate':0.2,        # necessario solo per i casi di exponential, logarithmic e linear.
        'min_ratio':0.5,          # indica la pressione finale che la fonte ha una volta che la crisi è partita - il mu per il ou model
        #'reversion_speed': 0.3,     # elocità con cui "cade" verso il 50%
        #'volatility': 0.05            # Intensità delle oscillazioni attorno al 50%
        },
        avg_demand=0.25,               # in caso siano simulate identificano la media di esse
        dist_type='lognormal',         # 'normal', 'lognormal', 'original' or 'uniform'  --> distribuzione domanda idrica oppure se si prendono i valori originli
        pattern_mode='random',          # 'random', 'single' or 'sequential' ---> scelta del pattern di consumo tra quelli esistenti
        min_boost = 150,                 # altezza minima delle cisterne sia caso già esistenti che caso di nuove
        n_tanks=15,                      # numero di serbatoi da aggiungere
        strategy_name='demand',      # 'random','demand','pressure' 
        crisis_start_hour=1.0,      # identifica a che ora la crisi inizia
        agent_name='heuristic',      # identifica l'agente che si sta scegliendo
        agent_threshold=0.99,       # identifica la soglia sotto la quale si sta avverando la crisi
        agent_aggression=5.0,      # varia tra 1 e 10 ed identifica la reattività dell'agente
        agent_alpha=0.9, # 90% importanza all'acqua, 10% alla batteria LoRa
        enable_pumps=False,       # se fissata su true da la possiblità alle cisterne di ricaricarsi tramite egente di controllo
        gateway_mode='center',      # 'center','random_offset', 'random' --> definsice dove isnerire il gateway
        #gateway_offset=0.0,       # definisce la distanza dal centro nel caso di random offset
        lora_mode='simple',      # 'simple' or 'multihop'   --> se posto su mulithop inserisce tanti sensori quanti ne servono per arrivare al gateway altrimenti semplfica a distanza di 2 km per ampie distanze
        sf_mode='fixed',    # 'sequential', 'random' or 'fixed'   --> associazione dei vari sf
        fixed_sf=12,            # only with fixed
        target_head=300         # il livello della fonte primaria
    )

results = engine.run_simulation()

print(f"\n✓ Simulation completed: {len(results.node)} nodes.")
print(f"📁 Detailed logs saved to: {engine.lora_net.log_path}")


