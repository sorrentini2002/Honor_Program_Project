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
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


dyn_wntr_path = 'Dyn-WNTR'
for path in [dyn_wntr_path, os.path.join(dyn_wntr_path, 'mwntr')]:
    if os.path.exists(path) and path not in sys.path:
        sys.path.insert(0, path)

print(f"Working directory: {os.getcwd()}")

import importlib
import mwntr
from mwntr.network import LinkStatus
from mwntr.sim.interactive_network_simulator import MWNTRInteractiveSimulator
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
    """Simulates LoRaWAN communication using physics-based RSSI/SNR model (LoRaSimPlus).
    
    Replaces the old Markov-chain approach with realistic log-distance path loss,
    receiver sensitivity checks, SNR validation, and full collision detection
    (frequency, SF orthogonality, capture effect, timing).
    
    Public API is fully backward-compatible with the previous implementation.
    """

    # ── LoRa Physical Layer Constants (from LoRaSimPlus ParameterConfig.py) ──
    # Receiver sensitivity matrix [SF7..SF12] x [125kHz, 250kHz, 500kHz]
    _SENSI = np.array([
        [7,  -126.5,  -124.25, -120.75],
        [8,  -127.25, -126.75, -124.0],
        [9,  -131.25, -128.25, -127.5],
        [10, -132.75, -130.25, -128.75],
        [11, -134.5,  -132.75, -128.75],
        [12, -133.25, -132.25, -132.25],
    ])
    # Minimum SNR required for demodulation per SF (SF7..SF12)
    _SNR_REQ = np.array([-7.5, -10.0, -12.5, -15.0, -17.5, -20.0])
    # LoRaWAN EU868 carrier frequencies (Hz)
    _CARRIER_FREQ = np.array([867.1e6, 867.3e6, 867.5e6, 867.7e6,
                              867.9e6, 868.1e6, 868.3e6, 868.5e6])
    # Propagation model defaults (log-distance, from LoRaSimPlus)
    _PTX   = 14       # Transmit power (dBm)
    _GAMMA = 2.32     # Path-loss exponent
    _D0    = 1000.0   # Reference distance (m)
    _STD   = 7.8      # Log-normal shadowing std dev (dB)
    _LPLD0 = 128.95   # Path loss at d0 (dB)
    _GL    = 0        # Combined antenna gain (dB)
    # Collision
    _CAPTURE_THRESHOLD_DB = 6  # Capture effect power margin (dB)
    _NPREAM = 8                # Preamble symbols

    def __init__(self, log_filename="latest_simulation_log.txt", config_params=None,
                 bandwidth=125, payload_size=65, coding_rate=1, tx_power=None):
        self.gateway_pos = (0, 0)
        self.sensors = {}
        self.tx_interval_s = 1800
        self.total_transmissions = 0
        self.total_collisions = 0

        self.history = []
        self.debug_log = []

        # Configurable LoRa radio parameters
        self.bandwidth = bandwidth          # kHz (125, 250, 500)
        self.payload_size = payload_size    # bytes
        self.coding_rate = coding_rate      # 1..4 (4/5 .. 4/8)
        if tx_power is not None:
            self._PTX = tx_power

        # Persistent list of in-flight packets (survives across steps for cross-timestep collisions)
        self._active_packets = []

        # RSSI cache: sensor_id -> base RSSI (without per-step shadowing)
        self._rssi_cache = {}

        # Gestione File di Log
        log_dir = "Log_review"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        self.log_path = os.path.join(log_dir, log_filename)

        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("="*60 + "\n")
            f.write(f"  LORA CO-SIMULATION SESSION (LoRaSimPlus): {datetime.datetime.now()}\n")
            f.write("="*60 + "\n")
            if config_params:
                f.write("\n[CONFIG] Simulation Parameters:\n")
                for k, v in config_params.items():
                    f.write(f"  > {k:20}: {v}\n")
                f.write("-" * 60 + "\n\n")

    # ──────────────────────── Logging ────────────────────────
    def _log(self, message, level="INFO"):
        """Centralized logging: updates memory list and writes to physical file."""
        formatted_msg = f"[{level:5}] {message}"
        self.debug_log.append(formatted_msg)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(formatted_msg + "\n")

    # ──────────────────────── Gateway ────────────────────────
    def setup_gateway(self, pos):
        self.gateway_pos = pos
        self._log(f"Gateway set at {pos}")

    # ──────────────────── Physics Helpers ────────────────────
    @staticmethod
    def _get_sensitivity(sf, bw):
        """Return receiver sensitivity (dBm) for given SF and bandwidth."""
        bw_idx = {125: 1, 250: 2, 500: 3}.get(bw, 1)
        sf_idx = max(0, min(5, sf - 7))
        return LoRaSystem._SENSI[sf_idx, bw_idx]

    @staticmethod
    def _get_min_snr(sf):
        """Return minimum SNR (dB) required for demodulation at given SF."""
        sf_idx = max(0, min(5, sf - 7))
        return LoRaSystem._SNR_REQ[sf_idx]

    def _compute_rssi(self, distance_m):
        """Log-distance path loss model (from LoRaSimPlus Propagation.py).
        
        RSSI = Ptx + GL - (10*gamma*log10(d/d0) + N(Lpld0, std))
        Returns RSSI in dBm. Includes stochastic log-normal shadowing.
        """
        if distance_m <= 0:
            distance_m = 1.0  # Edge case: co-located sensor, use 1m
        Lpl = 10 * self._GAMMA * math.log10(distance_m / self._D0) \
              + np.random.normal(self._LPLD0, self._STD)
        return self._PTX + self._GL - Lpl

    def _compute_rssi_deterministic(self, distance_m):
        """Deterministic RSSI (no shadowing) for caching at registration."""
        if distance_m <= 0:
            distance_m = 1.0
        Lpl = 10 * self._GAMMA * math.log10(distance_m / self._D0) + self._LPLD0
        return self._PTX + self._GL - Lpl

    @staticmethod
    def _compute_snr(rssi, bw_khz=125):
        """SNR = RSSI - noise_floor, where noise_floor = -174 + 10*log10(BW_hz)."""
        noise_floor = -174.0 + 10.0 * np.log10(bw_khz * 1e3)
        return rssi - noise_floor

    def _check_receivable(self, rssi, snr, sf, bw):
        """Check if packet meets sensitivity and SNR demodulation thresholds."""
        min_sensi = self._get_sensitivity(sf, bw)
        min_snr = self._get_min_snr(sf)
        return (rssi > min_sensi) and (snr > min_snr)

    @staticmethod
    def _airtime_ms(sf, cr, payload_bytes, bw):
        """Compute LoRa packet airtime in ms (from LoRaSimPlus Packet.py)."""
        H = 0   # Explicit header
        DE = 0  # Low data rate optimization
        Npream = 8

        if bw == 125 and sf in [11, 12]:
            DE = 1
        if sf == 6:
            H = 1

        Tsym = (2.0 ** sf) / bw  # ms per symbol
        Tpream = (Npream + 4.25) * Tsym
        payloadSymbNB = 8 + max(
            math.ceil((8.0 * payload_bytes - 4.0 * sf + 28 + 16 - 20 * H)
                      / (4.0 * (sf - 2 * DE))) * (cr + 4), 0)
        Tpayload = payloadSymbNB * Tsym
        return Tpream + Tpayload

    # ──────────────────── Collision Detection ────────────────
    @staticmethod
    def _frequency_collision(p1, p2):
        """Check frequency overlap based on bandwidth (from LoRaSimPlus)."""
        if abs(p1['freq'] - p2['freq']) <= 120e3 and (p1['bw'] == 500 or p2['bw'] == 500):
            return True
        elif abs(p1['freq'] - p2['freq']) <= 60e3 and (p1['bw'] == 250 or p2['bw'] == 250):
            return True
        elif abs(p1['freq'] - p2['freq']) <= 30e3:
            return True
        return False

    @staticmethod
    def _sf_collision(p1, p2):
        """Different SFs are orthogonal — no collision."""
        return p1['sf'] == p2['sf']

    @staticmethod
    def _timing_collision(p1, p2):
        """Check if p2's transmission overlaps with p1's critical preamble.
        
        Uses absolute timestamps (start_abs_ms / end_abs_ms) so that
        collisions are correctly detected even across step boundaries.
        """
        Tpreamb = (2 ** p1['sf']) / (1.0 * p1['bw']) * (8 - 5)  # ms
        p2_end = p2['end_abs_ms']
        p1_cs = p1['start_abs_ms'] + Tpreamb
        return p1_cs < p2_end

    def _power_collision(self, p1, p2):
        """Capture effect: stronger packet survives if power gap > threshold."""
        diff = abs(p1['rssi'] - p2['rssi'])
        if diff < self._CAPTURE_THRESHOLD_DB:
            return [p1, p2]  # Both lost
        elif p2['rssi'] - p1['rssi'] > self._CAPTURE_THRESHOLD_DB:
            return [p1]  # p1 lost
        return [p2]  # p2 lost

    def _detect_collisions(self, new_packets):
        """Full collision detection: new packets vs ALL in-flight packets.
        
        Checks new_packets against each other AND against any still-active
        packets from previous steps (_active_packets), enabling cross-timestep
        collision detection.
        
        A collision requires:
        1. Frequency overlap
        2. Same SF (orthogonality)
        3. Timing overlap (absolute timestamps)
        4. Power check (capture effect)
        """
        # Build combined list: still-active old packets + new packets
        all_inflight = self._active_packets + new_packets

        for i, pkt in enumerate(new_packets):
            if pkt.get('lost', False):
                continue
            for other in all_inflight:
                if other is pkt or other.get('lost', False):
                    continue
                if self._frequency_collision(pkt, other) and self._sf_collision(pkt, other):
                    if self._timing_collision(pkt, other):
                        casualties = self._power_collision(pkt, other)
                        for c in casualties:
                            c['collided'] = True

    def _purge_expired(self, current_time_ms):
        """Remove packets whose transmission has fully completed."""
        self._active_packets = [
            p for p in self._active_packets if p['end_abs_ms'] > current_time_ms
        ]

    # ──────────────────── Sensor Registration ────────────────
    def register_iot_sensors(self, valves, wn, mode='simple', sf_mode='sequential', fixed_sf=10):
        self._log(f"NETWORK TOPOLOGY: {len(valves)} sensors registered", level="SETUP")
        self._log(f"  Physics: Ptx={self._PTX}dBm, BW={self.bandwidth}kHz, "
                  f"PL={self.payload_size}B, gamma={self._GAMMA}", level="SETUP")
        available_sfs = [7, 8, 9, 10, 11, 12]

        for i, v_name in enumerate(valves):
            # 1. Coordinate (identico alla versione precedente)
            node_name = v_name.replace("IoT_Valve_", "")
            if node_name in wn.nodes:
                node = wn.get_node(node_name)
            else:
                node = wn.get_node(wn.get_link(v_name).start_node_name)

            tx_x, tx_y = node.coordinates
            gw_x, gw_y = self.gateway_pos
            dist_m = math.sqrt((tx_x - gw_x)**2 + (tx_y - gw_y)**2)
            real_dist = dist_m / 1000.0  # km (per compatibilità)

            # 2. Assegnazione SF (identico alla versione precedente)
            if sf_mode == 'random': sf = random.choice(available_sfs)
            elif sf_mode == 'fixed': sf = fixed_sf
            elif sf_mode == 'sequential': sf = available_sfs[i % len(available_sfs)]
            else:  # distance
                if real_dist < 0.5: sf = 7
                elif real_dist < 1.0: sf = 8
                elif real_dist < 1.5: sf = 9
                elif real_dist < 2.0: sf = 10
                elif real_dist < 3.0: sf = 11
                else: sf = 12

            # 3. Pre-compute and cache deterministic RSSI + assign carrier frequency
            base_rssi = self._compute_rssi_deterministic(dist_m)
            base_snr = self._compute_snr(base_rssi, self.bandwidth)
            carrier_freq = self._CARRIER_FREQ[i % len(self._CARRIER_FREQ)]

            self._rssi_cache[v_name] = base_rssi

            # 4. Check baseline receivability (log a warning if marginal)
            sensi = self._get_sensitivity(sf, self.bandwidth)
            min_snr = self._get_min_snr(sf)
            baseline_ok = (base_rssi > sensi) and (base_snr > min_snr)

            self.sensors[v_name] = {
                'distance': real_dist,      # km — backward compat
                'distance_m': dist_m,       # meters — used for physics
                'sf': sf,
                'bw': self.bandwidth,       # kHz
                'freq': carrier_freq,       # Hz
                'cr': self.coding_rate,
                'base_rssi': base_rssi,     # dBm (deterministic, no shadowing)
                'last_tx_time': -9999.0,
                'data': {},
            }

            self._log(f"NODE {v_name:15} | SF{sf:2} | Dist: {real_dist:5.2f}km | "
                      f"RSSI: {base_rssi:6.1f}dBm | SNR: {base_snr:5.1f}dB | "
                      f"RX: {'OK' if baseline_ok else 'MARGINAL'}", level="REG")

    # ──────────────────────── Metrics ────────────────────────
    def get_packet_loss_rate(self):
        if self.total_transmissions == 0: return 0.0
        plr = (self.total_collisions / self.total_transmissions) * 100.0
        self._log(f"STATS CHECK: PLR={plr:.2f}% | Total TX={self.total_transmissions}")
        return plr

    # ──────────────────── Step Simulation ────────────────────
    def step(self, current_time, timestep_s):
        """Simulate one co-simulation timestep with cross-timestep collision support.
        
        Packets use absolute timestamps (current_time converted to ms) so that
        a packet started in step N can collide with one started in step N+1.
        The persistent _active_packets list tracks in-flight transmissions.
        
        Returns: list of dicts {'id': sensor_name, 'data': payload}
        """
        current_time_ms = current_time * 1000.0  # Convert seconds -> ms

        # Phase 0: Purge packets that finished transmitting before this step
        self._purge_expired(current_time_ms)

        # Phase 1: Build list of NEW packets transmitted this step
        new_packets = []

        for s_id, s_node in self.sensors.items():
            if current_time - s_node['last_tx_time'] >= self.tx_interval_s:
                self.total_transmissions += 1
                s_node['last_tx_time'] = current_time

                sf = s_node['sf']
                bw = s_node['bw']
                dist_m = s_node['distance_m']

                # Stochastic RSSI (with log-normal shadowing per transmission)
                rssi_val = self._compute_rssi(dist_m)
                snr_val = self._compute_snr(rssi_val, bw)

                # Check physical receivability
                is_receivable = self._check_receivable(rssi_val, snr_val, sf, bw)

                # Compute airtime for collision window
                airtime = self._airtime_ms(sf, s_node['cr'], self.payload_size, bw)

                # Absolute timestamps: TX starts exactly at current_time
                start_abs = current_time_ms
                end_abs = current_time_ms + airtime

                pkt = {
                    'sensor_id': s_id,
                    'sf': sf,
                    'bw': bw,
                    'freq': s_node['freq'],
                    'rssi': rssi_val,
                    'snr': snr_val,
                    'airtime_ms': airtime,
                    'start_abs_ms': start_abs,
                    'end_abs_ms': end_abs,
                    'lost': not is_receivable,
                    'collided': False,
                    'data': s_node.get('data', {}),
                }
                new_packets.append(pkt)

        # Phase 2: Collision detection (new packets vs ALL in-flight packets)
        if new_packets:
            self._detect_collisions(new_packets)

        # Phase 3: Add new packets to persistent in-flight list
        self._active_packets.extend(new_packets)

        # Phase 4: Determine outcomes for this step's packets
        received = []
        for pkt in new_packets:
            if pkt['lost']:
                reason = "LOST(SNR)"
                self.total_collisions += 1
            elif pkt['collided']:
                reason = "COLLIDED"
                self.total_collisions += 1
            else:
                reason = "SUCCESS"
                received.append({'id': pkt['sensor_id'], 'data': pkt['data']})

            self._log(
                f"TX @{current_time:8.1f}s | {pkt['sensor_id']:15} | SF{pkt['sf']:2} | "
                f"RSSI:{pkt['rssi']:6.1f} SNR:{pkt['snr']:5.1f} | "
                f"Air:{pkt['airtime_ms']:6.1f}ms | {reason}",
                level="COMM"
            )

        return received

class WaterNetworkManager:
    def __init__(self, wn_model):
        self.wn = mwntr.network.WaterNetworkModel(wn_model) if isinstance(wn_model, str) else wn_model
        self.iot_tanks = {}
        self.iot_valves = []
        self.iot_pumps = []
        
        # Tag nodes with USER_1 based on demand patterns from original file
        self._tag_user_nodes()
    
    def _tag_user_nodes(self):
        """
        One-time initialization: Identify and tag nodes with demand patterns as 'USER_1'.
        This reads the original network file to identify nodes that have:
        - A demand pattern defined, OR
        - An ID >= 9
        
        CRITICAL: This method ONLY adds the 'USER_1' tag.
        It DOES NOT modify base_demand or demand_pattern values.
        """
        try:
            # Get the network filename
            if hasattr(self.wn, 'filename'):
                inp_file = self.wn.filename
            else:
                # Fallback: try to infer from current state
                inp_file = None
            
            if inp_file and os.path.exists(inp_file):
                # Parse the original .inp file to identify user nodes
                user_nodes = set()
                
                with open(inp_file, 'r', encoding='utf-8', errors='ignore') as f:
                    in_junctions = False
                    for line in f:
                        # Check for section header
                        if line.strip().upper().startswith('[JUNCTIONS]'):
                            in_junctions = True
                            continue
                        elif line.strip().startswith('['):
                            in_junctions = False
                            continue
                        
                        if in_junctions and line.strip() and not line.strip().startswith(';'):
                            # Parse junction line: ID Elev Demand Pattern
                            parts = line.split()
                            if len(parts) >= 2:
                                node_id = parts[0].strip()
                                
                                # Check if node has a pattern defined (non-empty 4th field)
                                has_pattern = False
                                if len(parts) >= 4:
                                    pattern_field = parts[3].strip()
                                    # Pattern field is non-empty if it's not empty
                                    has_pattern = bool(pattern_field) and pattern_field != ';'
                                
                                # Check if numeric ID >= 9
                                try:
                                    node_num = int(node_id)
                                    if node_num >= 9 or has_pattern:
                                        user_nodes.add(node_id)
                                except ValueError:
                                    # Non-numeric ID, check pattern
                                    if has_pattern:
                                        user_nodes.add(node_id)
                
                # Apply USER_1 tag to identified nodes (without modifying demand data)
                for j_name in self.wn.junction_name_list:
                    if j_name in user_nodes:
                        junction = self.wn.get_node(j_name)
                        junction.tag = 'USER_1'
        except Exception as e:
            # If tagging fails, log but continue (tagging is not critical for operation)
            pass

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

                # Check if the network has the 'USER_1' flag defined on any node
                has_user_tags = any(self.wn.get_node(n).tag == 'USER_1' for n in self.wn.junction_name_list)

                for i, j_name in enumerate(self.wn.junction_name_list):
                    junction = self.wn.get_node(j_name)

                    # Se ci sono tag definiti, usa quelli. Altrimenti, fallback: tutti sono utenti
                    is_user = False
                    if has_user_tags:
                        is_user = (junction.tag == 'USER_1')
                    else:
                        is_user = True

                    if not is_user:
                        # PASS_THROUGH: Non-user nodes get zero demand
                        junction.demand_timeseries_list.clear()
                        junction.add_demand(base=0.0, pattern_name=None)
                        log_file.write(f"{j_name:<20} | {'0.0000':<12} | {'PASS_THROUGH':<15}\n")
                        continue

                    # For USER_1 nodes: Always preserve original values from .inp file
                    if junction.demand_timeseries_list:
                        # Keep original demand and pattern
                        orig_demand = junction.demand_timeseries_list[0]
                        base_val = orig_demand.base_value
                        pattern_to_use = orig_demand.pattern_name
                    else:
                        # If no original demand exists, use default
                        base_val = 0.0
                        pattern_to_use = None
                    
                    # For USER_1 nodes, preserve the original demand (don't clear and re-add)
                    # This ensures we don't lose any information from the original .inp file
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
                    
                    num_links = len(connected_links)
                    f.write(f"  - Node {t_name}: Found {num_links} connected links.\n")
                    
                    if num_links == 1:
                        # LOGICA 1 TUBO: Rimozione totale (foglia)
                        l_name = connected_links[0]
                        f.write(f"    * Removing single connection leaf: {l_name}\n")
                        self.wn.remove_link(l_name)
                        self.wn.remove_node(t_name, with_control=True)
                        
                    elif num_links == 2:
                        # LOGICA 2 TUBI: Fusione (bypass)
                        l1_name, l2_name = connected_links[0], connected_links[1]
                        l1 = self.wn.get_link(l1_name)
                        l2 = self.wn.get_link(l2_name)
                        
                        # Troviamo i due nodi esterni
                        n1 = l1.start_node_name if l1.end_node_name == t_name else l1.end_node_name
                        n2 = l2.start_node_name if l2.end_node_name == t_name else l2.end_node_name
                        
                        f.write(f"    * Merging paths: {n1} <-> {t_name} <-> {n2}\n")
                        
                        # Proprietà del nuovo tubo (prendiamo la media o da uno dei due)
                        new_name = f"Merged_{n1}_{n2}"
                        new_diam = (l1.diameter + l2.diameter) / 2
                        new_len = l1.length + l2.length
                        new_rough = l1.roughness
                        
                        self.wn.remove_link(l1_name)
                        self.wn.remove_link(l2_name)
                        self.wn.remove_node(t_name, with_control=True)
                        
                        # Creiamo il link diretto
                        self.wn.add_pipe(new_name, n1, n2, length=new_len, diameter=new_diam, roughness=new_rough)
                        f.write(f"    * Created bypass pipe: {new_name}\n")
                        
                    else:
                        # Più di 2 tubi: Rimozione standard link per evitare loop complessi
                        for l_name in connected_links:
                            self.wn.remove_link(l_name)
                        self.wn.remove_node(t_name, with_control=True)
                        f.write(f"    * Removed node and all {num_links} links (complex junction).\n")
                
                f.write(f"  Successfully processed {len(tanks)} original tanks.\n")
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

                # --- [MODIFICA UTENTE] Strumentazione Topologica ---
                # Invece di rimuovere arbitrariamente, mappiamo tutti i collegamenti esistenti
                # tra la cisterna e la rete per trasformarli in interfacce IoT
                for l_name in connected_links:
                    link = self.wn.get_link(l_name)
                    junc_name = link.start_node_name if link.end_node_name == tank_name else link.end_node_name
                    
                    tank_node = self.wn.get_node(tank_name)
                    junc_node = self.wn.get_node(junc_name)
                    
                    # Calcolo altezza relativa per questo specifico collegamento
                    height_diff = (tank_node.elevation - junc_node.elevation) + tank_node.max_level
                    boost = max(min_boost, height_diff)
                    
                    self.wn.remove_link(l_name)
                    self._add_iot_control_to_tank(junc_name, tank_name, junc_name, link.diameter, boost, use_pumps=use_pumps)
                    f.write(f"    * Retrofitted link {l_name} connecting to Node {junc_name} (Boost: {boost:.2f}m)\n")
                # ----------------------------------------------------

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

                # [MODIFICA] Potenziamo TUTTA la rete per eliminare colli di bottiglia strutturali
                # Questo permette di vedere l'effetto della CRISI alla fonte senza interferenze interne
                #for l_name in self.wn.pipe_name_list:
                #    link = self.wn.get_link(l_name)
                #    if hasattr(link, 'diameter'):
                #        old_diam = link.diameter
                #        link.diameter = max(old_diam, 10.0) # Almeno 10 pollici per stabilità totale pre-crisi
                
                #f.write(f"  - Reservoir {res_name}: Head {old_head} -> {target_head}, Pattern {old_pattern} -> None\n")
            
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
                          initial_status='CLOSED')

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
            try:
                v_link = sim._wn.get_link('Main_Control_Valve')
                v_link.status = mwntr.network.elements.LinkStatus.Open
                v_link.setting = 0.0
            except:
                pass
            # La modifica dinamica della 'base_head' richiede il rebuild del modello idraulico
            for res_name in self.wn.reservoir_name_list:
                res = sim._wn.get_node(res_name)
                if not hasattr(res, '_original_head'):
                    res._original_head = res.head_timeseries.base_value
                
                new_head = res._original_head * ratio
                if abs(getattr(res, '_last_ratio', 1.0) - ratio) > 0.01:
                    with open(log_path, "a") as f:
                        f.write(f"PRES | {step:<4} | {ratio:<5.2f} | Head: {new_head:<10.2f} | -{(1-ratio)*100:.1f}%\n")
                    res._last_ratio = ratio
                    # Modifichiamo la head
                    res.head_timeseries.base_value = new_head
                    # Diciamo al simulatore di ricaricare il modello idraulico
                    sim.rebuild_hydraulic_model = True

        elif mode == 'flow':
            # Ritorno a una logica di crisi più semplice e diretta
            # Siccome le velocità dell'acqua sono minime, serve un coefficiente gigantesco per far calare la pressione (h_L = K * v^2 / 2g)
            loss_coeff = max(1.0, 500000.0 * (1.0 - ratio) ** 2)
            
            valve = sim._wn.get_link('Main_Control_Valve')
            
            # Interveniamo solo se c'è stata una reale variazione di ratio
            if abs(getattr(valve, '_last_ratio', 1.0) - ratio) > 0.01:
                with open(log_path, "a") as f:
                    f.write(f"FLOW | {step:<4} | {ratio:<5.2f} | Coeff: {loss_coeff:<10.2f} | -{(1-ratio)*100:.1f}%\n")
                
                # --- INIEZIONE DEL CONTROLLO WNTR PER LA CRISI ---
                control_name = f"CrisisCtrl_Valve_{current_time_s}"
                action = wntr.network.controls.ControlAction(valve, 'setting', loss_coeff)
                
                # Usiamo la condizione infallibile: scatta subito
                condition = wntr.network.controls.SimTimeCondition(sim._wn, '=', current_time_s)
                ctrl = wntr.network.controls.Control(condition, action, name=control_name)
                sim._wn.add_control(control_name, ctrl)
                # ---------------------------------------------------
                
                valve._last_ratio = ratio

        # Rimosso: sim.rebuild_hydraulic_model = True. Non serve più e rallentava tutto.
        return ratio

    def _add_iot_control_to_tank(self, junc_name, tank_name, tank_id, diameter, boost_head, use_pumps=True):
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
                # CORREZIONE: 0.005 m^3/s (5 L/s) e 0.01 m^3/s (10 L/s) per uno svuotamento graduale e realistico
                self.wn.add_curve(curve_name, 'HEAD', [(0.0, boost_head * 1.2), (0.005, boost_head * 1.1), (0.01, float(boost_head))])
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
        self.wn.options.hydraulic.minimum_pressure = -10.0
        self.wn.options.hydraulic.required_pressure = 5.0


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


def plot_tank_levels_flexible(tank_data_matrix, step_minutes, output_filename='tank_levels_trend.png'):
    """
    Genera un grafico dei livelli dei serbatoi con passo temporale variabile.
    
    Args:
        tank_data_matrix: Array numpy con shape (num_tanks, num_steps).
        step_minutes: Int, intervallo in minuti tra ogni rilevazione (es. 5, 15, 60).
        output_filename: Nome del file immagine da salvare.
    """
    tank_data = np.array(tank_data_matrix)
    num_tanks, num_steps = tank_data.shape
    
    plt.figure(figsize=(14, 7))
    
    # Calcola l'asse X in ORE (più leggibile dei minuti se la simulazione è lunga)
    # Se preferisci i minuti, basta togliere il "/ 60"
    time_axis_hours = (np.arange(num_steps) * step_minutes) / 60.0
    
    for i in range(num_tanks):
        plt.plot(time_axis_hours, tank_data[i], label=f'Tank {i+1}', linewidth=1.2)
    
    plt.xlabel('Time [hours]')
    plt.ylabel('Water Level [m]')
    plt.title(f'Water Tank Levels (Update every {step_minutes} min)')
    
    # Griglia dinamica: se simuli molti giorni, aiuta a vedere i cicli di 24h
    plt.grid(True, which='both', linestyle='--', alpha=0.5)
    
    # Se i tank sono molti, riduciamo la legenda
    if num_tanks <= 20:
        plt.legend(loc='upper right', ncol=2, fontsize='x-small')
    
    plt.tight_layout()
    plt.savefig(output_filename, dpi=300)
    print(f"Grafico salvato: {output_filename} (Passo: {step_minutes} min)")


def _is_real_user_node(node_name, wn=None):
    """
    Determines if a node is a real user node by checking for the 'USER_1' tag.
    
    Args:
        node_name: The name of the node to check
        wn: Optional WaterNetworkModel instance for tag lookup
    
    Returns:
        True if the node has 'USER_1' tag, False otherwise
    """
    if wn is None:
        # If no network provided, cannot determine user status
        return False
    
    try:
        node = wn.get_node(node_name)
        return node.tag == 'USER_1'
    except:
        return False



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
        if n_tanks > 0:
            self.water_net.add_iot_tanks(n_tanks=n_tanks, strategy_name=strategy_name, 
                                         min_boost=self.min_boost, use_pumps=enable_pumps)

        # 1.1 Fix Reservoir and Reinforce ALL pipes (including new IoT ones)
        self.water_net.fix_reservoir_head(target_head=target_head) 

        # 2. Setup Crisi
        crisis_class = CRISIS_MAP.get(decay_type, CRISIS_MAP['linear'])
        if crisis_params is None:
            crisis_params = {'decay_rate': decay_rate}
        
        # Inseriamo automaticamente i parametri di simulazione se il modello li richiede (es: PumpTestCrisis)
        # Questo garantisce che non ci siano discrepanze tra motore e modello di crisi
        crisis_params['crisis_start_hour'] = crisis_start_hour
        crisis_params['step_min'] = step_min

        self.crisis_model = crisis_class(**crisis_params)
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
                            
        # Keep engine/main logs separate from agent's own log file
        self.perf_log = "Log_review/main_performance.txt"
        with open(self.perf_log, "w") as f:
            f.write("STEP | EXPECTED | ACTUAL | DIFF | SATISFACTION | TX_INT | OBJECTIVE\n")
            f.write("-" * 80 + "\n")

        # 5. Configurazione PDA (Pressure Driven Analysis) - CRUCIALE
        self.water_net.wn.options.hydraulic.demand_model = 'PDA'
        self.water_net.wn.options.hydraulic.minimum_pressure = -10.0
        self.water_net.wn.options.hydraulic.required_pressure = 5.0 
        
        # 6. Statistiche e Log di Crisi
        self.stats = {
            'time': [],
            'satisfaction': [],
            'packet_loss': [],
            'tanks': [],
            'tank_activation_ever': [],
            'tank_activity_steps': [],
            'reward': [],
            'tank_levels': []
        }
        self._ever_opened_valves = set()
        log_dir = "Log_review"
        if not os.path.exists(log_dir): os.makedirs(log_dir)
        # Prepare valve commands CSV for diagnostics (store as instance attribute)
        self.valve_csv = os.path.join(log_dir, "valve_commands.csv")
        with open(self.valve_csv, "w") as vf:
            vf.write("step,time_hours,valve_name,commanded_level\n")
        # Prepare valve settings CSV to record applied WNTR link settings (loss coeffs)
        self.valve_settings_csv = os.path.join(log_dir, "valve_settings.csv")
        with open(self.valve_settings_csv, "w") as vf2:
            vf2.write("step,time_hours,valve_name,initial_setting,status\n")
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
                # 1. Leggiamo il valore stocastico che era già stato generato e iniettato nel .inp
                original_val = node.demand_timeseries_list[0].base_value
                
                # 2. Lo salviamo nel dizionario di backup
                saved_stochastic_demands[j_name] = original_val
                
                # 3. NON azzeriamo nulla. Lasciamo che il valore rimanga quello reale fin dallo step 0!
                node.demand_timeseries_list[0].base_value = original_val

        try:
            main_valve = self.water_net.wn.get_link("Main_Control_Valve")
            # Inizializzala come completamente aperta
            main_valve.initial_status = mwntr.network.elements.LinkStatus.Open
            main_valve.initial_setting = 0.0
        except KeyError:
            pass # Se la valvola si chiama diversamente o non esiste, ignora

        self.sim.init_simulation()
        t = 0.0

        demand_log_path = "Log_review/demand_distribution.csv"
        with open(demand_log_path, "w") as f:
            f.write("step,time_hours,expected_demand,actual_demand,satisfaction_pct\n")



        # --- EXPORT TOPOLOGY ---
        topology = {
            "nodes": [],
            "links": []
        }
        
        # Collect all coordinates to find min/max for normalization
        all_coords = []
        node_coords_map = {}
        for n_name, node in self.water_net.wn.nodes():
            coords = node.coordinates if hasattr(node, 'coordinates') and node.coordinates else (0, 0)
            node_coords_map[n_name] = coords
            all_coords.append(coords)
        
        # Calculate min/max for normalization
        if all_coords:
            all_x = [c[0] for c in all_coords]
            all_y = [c[1] for c in all_coords]
            min_x, max_x = min(all_x), max(all_x)
            min_y, max_y = min(all_y), max(all_y)
            range_x = max_x - min_x if max_x > min_x else 1
            range_y = max_y - min_y if max_y > min_y else 1
        else:
            min_x, max_x, min_y, max_y, range_x, range_y = 0, 1, 0, 1, 1, 1
        
        # Build node list with normalized coordinates
        for n_name, node in self.water_net.wn.nodes():
            n_type = "Junction"
            if n_name in self.water_net.wn.reservoir_name_list:
                n_type = "Reservoir"
            elif n_name in self.water_net.wn.tank_name_list:
                n_type = "Tank"
            
            coords = node_coords_map[n_name]
            # Normalize to 0-1000 range
            norm_x = ((coords[0] - min_x) / range_x * 1000) if range_x > 0 else 500
            norm_y = ((coords[1] - min_y) / range_y * 1000) if range_y > 0 else 500
            
            topology["nodes"].append({
                "id": n_name,
                "type": n_type,
                "x": float(norm_x),
                "y": float(norm_y)
            })
            
        for l_name, link in self.water_net.wn.links():
            l_type = "Pipe"
            if l_name in self.water_net.wn.pump_name_list:
                l_type = "Pump"
            elif l_name in self.water_net.wn.valve_name_list:
                l_type = "Valve"
                
            topology["links"].append({
                "id": l_name,
                "type": l_type,
                "source": link.start_node_name,
                "target": link.end_node_name
            })
            
        import json
        import os
        # Assicurati che la cartella Dashobard esista
        os.makedirs("Dashobard", exist_ok=True)
        # Scrivi il file data.js nella cartella Dashobard con i dati come variabili globali JavaScript
        with open("Dashobard/data.js", "w") as js_file:
            js_file.write("// Generated dashboard data - auto-generated by main.py\n\n")
            js_file.write("window.topology = " + json.dumps(topology, indent=2) + ";\n\n")
            js_file.write("window.simData = null; // Will be set after dashboard_data is collected\n")

        # Inizializzo l'array per l'export JSON della dashboard
        dashboard_data = []

        for step in range(self.n_steps):
            t += self.timestep_s

            self.sim._currentTime = int(t)
                            # Notifichiamo al simulatore che la rete è cambiata
            if hasattr(self.sim, '_wn'):
                    self.sim._wn.options.hydraulic.demand_model = 'PDA'
                    # Impostiamo una pressione richiesta molto ampia (es. 40 ft) per spalmare la gradualità
                    self.sim._wn.options.hydraulic.required_pressure = 35 
                    self.sim._wn.options.hydraulic.minimum_pressure = 0
            # --- RIPRISTINO DOMANDA (Dallo step 1 in poi) ---
            # --- AGGIORNATO: RIPRISTINO DOMANDA CON NOTIFICA AL SIMULATORE INTERATTIVO ---
            # Iniettiamo i valori stocastici stabili nella copia attiva del simulatore
                    if step == 0:
                        for j_name, val in saved_stochastic_demands.items():
                            sim_node = self.sim._wn.get_node(j_name)
                            if sim_node.demand_timeseries_list:
                                sim_node.demand_timeseries_list[0].base_value = val
                        self.sim.rebuild_hydraulic_model = True
                
                        # Forziamo il simulatore a ricostruire il modello matematico per applicare le nuove domande
                        if hasattr(self.sim, 'rebuild_hydraulic_model'):
                            self.sim.rebuild_hydraulic_model = True


            crisis_start_time_s = self.crisis_start_step * self.timestep_s
            
            current_ratio = 1.0
            if t >= crisis_start_time_s:
                time_elapsed_hours = (t - crisis_start_time_s) / 3600.0
                current_ratio = self.crisis_model.get_ratio(time_elapsed_hours)
                self.water_net.apply_crisis_reduction(self.sim, current_ratio, step, mode=self.crisis_mode_name)

            if hasattr(self.sim, '_wn'):
                old_controls = [c_name for c_name in self.sim._wn.control_name_list 
                                if c_name.startswith("AgentCtrl_") or c_name.startswith("CrisisCtrl_")]
                for c_name in old_controls:
                    self.sim._wn.remove_control(c_name)

            # Il cuore della co-simulazione
            s_current = self.stats['satisfaction'][-1] / 100.0 if self.stats['satisfaction'] else 1.0
            pl = self.lora_net.get_packet_loss_rate()
            act = self.agent.decide_action(step, t, s_current)
            self.agent.apply_mitigation(act, self.sim, self.lora_net, t)
            
            self.water_net.sim = self.sim

            self.sim.step_sim()
            
            # --- PRELIEVO INDICE DI CRISI ---
            source_head = 0.0
            for res_name in self.water_net.wn.reservoir_name_list:
                res_node = self.sim._wn.get_node(res_name) # Cambiato 'res' in 'res_node' per evitare conflitti
                source_head = res_node.head_timeseries.base_value
                

            # 2. Verifica i Serbatoi
            tank_links = [l for l in self.water_net.wn.link_name_list if 'tank' in l.lower() or 'cistern' in l.lower()]
            for t_id in tank_links:
                t_link = self.water_net.wn.get_link(t_id)
                if 'flow' in self.sim.node_res and t_id in self.sim.node_res['flow']:
                    f_data = self.sim.node_res['flow'][t_id]
                    t_flow = f_data[-1] if len(f_data) > 0 else 0
                else:
                    t_flow = 0.0
                
            
            # 3. Verifica Pressione Media
            pressures = []
            if 'pressure' in self.sim.node_res:
                for n in self.sim.node_res['pressure']:
                    p_list = self.sim.node_res['pressure'][n]
                    if len(p_list) > 0:
                        pressures.append(p_list[-1])
            if pressures:
                avg_p = sum(pressures) / len(pressures)

            self.lora_net.step(t, self.timestep_s)

            # --- RACCOLTA DATI AGGIORNATA E SICURA ---
            node_demands_dict = {}
            sim_nodes_source = self.sim.node_res
            
            # Calcoliamo l'ora corrente
            current_hour_int = int(t / 3600) % 24
            
            real_user_nodes = [j_name for j_name in self.water_net.wn.junction_name_list
                               if _is_real_user_node(j_name, self.water_net.wn)]

            for j_name in real_user_nodes:
                exp_val = 0.0
                act_val = 0.0
                
                try:
                    # Leggiamo il nodo direttamente dall'istanza attiva del simulatore
                    node_obj = self.sim._wn.get_node(j_name) if hasattr(self.sim, '_wn') else self.water_net.wn.get_node(j_name)
                    
                    if node_obj.demand_timeseries_list:
                        base_dem = node_obj.demand_timeseries_list[0].base_value
                        pattern_obj = node_obj.demand_timeseries_list[0].pattern
                        
                        if pattern_obj and hasattr(pattern_obj, 'multipliers'):
                            multipliers = pattern_obj.multipliers
                            if len(multipliers) > 0:
                                current_mult = multipliers[current_hour_int % len(multipliers)]
                                exp_val = base_dem * current_mult
                            else:
                                exp_val = base_dem
                        else:
                            exp_val = base_dem
                except:
                    exp_val = 0.0
                
                # Lettura dell'Actual demand calcolata dal solutore PDA
                if 'demand' in sim_nodes_source and j_name in sim_nodes_source['demand']:
                    vec = sim_nodes_source['demand'][j_name]
                    if len(vec) > 0:
                        calculated_act = vec[-1]
                        act_val = min(calculated_act, exp_val) if calculated_act > 0 else 0.0
                else:
                    act_val=0.0

                node_demands_dict[j_name] = {'expected': float(exp_val), 'actual': float(act_val)}

            # Calcolo globale esclusivamente sui nodi utente reali 1..30.
            exp_t = sum(item['expected'] for name, item in node_demands_dict.items() if _is_real_user_node(name, self.water_net.wn))
            act_t = sum(item['actual'] for name, item in node_demands_dict.items() if _is_real_user_node(name, self.water_net.wn))
            sat_p = (act_t / exp_t * 100) if exp_t > 0 else 100.0
            
            # Scrittura su file standard
            with open("Log_review/demand_distribution.csv", "a") as f:
                f.write(f"{step},{t/3600:.2f},{exp_t:.2f},{act_t:.2f},{sat_p:.2f}\n")

            diff = exp_t - act_t
            s_real = sat_p / 100.0
            fa = self.agent.compute_objective(s_real, self.lora_net.tx_interval_s)

            with open("Log_review/network_metrics.txt", "a") as f:
                if step == 0: f.write("STEP | EXP | ACT | DIFF | SAT\n")
                f.write(f"{step:<4} | {exp_t:<5.2f} | {act_t:<5.2f} | {diff:<4.2f} | {s_real*100:.1f}%\n")

            self.stats['time'].append(t)
            self.stats['satisfaction'].append(s_real * 100)
            self.stats['packet_loss'].append(pl)
            self.stats['reward'].append(fa)
            current_open_valves = {
                v_name for v_name, level in getattr(self.agent, 'current_valve_levels', {}).items()
                if float(level) > 0.0
            }
            self._ever_opened_valves.update(current_open_valves)
            self.stats['tanks'].append(self.agent.opened_count)
            self.stats['tank_activation_ever'].append(len(self._ever_opened_valves))
            self.stats['tank_activity_steps'].append({
                v_name: float(level)
                for v_name, level in getattr(self.agent, 'current_valve_levels', {}).items()
            })
            
# =================================================================
            # EXTRAZIONE DATI CO-SIMULAZIONE (Sincronizzata, Ordinata e Corretta)
            # =================================================================
            
            # 1. Identificazione immediata della rete attiva e dell'indice temporale corretto
            active_wn = self.sim._wn if hasattr(self.sim, '_wn') else self.water_net.wn
            current_sim_step_idx = len(self.sim.node_res.get('pressure', {}).get(list(active_wn.node_name_list)[0], [])) - 1
            current_sim_step_idx = max(0, current_sim_step_idx)

# --- RACCOLTA LIVELLI SERBATOI (TANKS) ---
            current_levels = []
            current_levels_dict = {}  # Inizializzazione pulita ad ogni step
            
            for t_name in active_wn.tank_name_list:
                # Per i serbatoi, il livello effettivo è memorizzato in node_res['pressure']
                # (pressure = head - elevation = level per i serbatoi)
                if t_name in self.sim.node_res.get('pressure', {}):
                    p_data = self.sim.node_res['pressure'][t_name]
                    if len(p_data) > current_sim_step_idx:
                        level = p_data[current_sim_step_idx]
                    else:
                        level = p_data[-1] if len(p_data) > 0 else 0.0
                else:
                    try:
                        # Fallback: leggi direttamente dal tank object durante la simulazione
                        tank_obj = active_wn.get_node(t_name)
                        level = tank_obj.level if hasattr(tank_obj, 'level') else 0.0
                    except:
                        level = 0.0
                
                tank_obj = active_wn.get_node(t_name)
                max_l = getattr(tank_obj, 'max_level', 10.0)
                clipped_level = max(0.0, min(level, max_l))
                
                # Riempire ENTRAMBE le strutture per non perdere i dati
                current_levels.append(clipped_level)
                current_levels_dict[t_name] = float(clipped_level)

            self.stats['tank_levels'].append(current_levels)

            # --- RACCOLTA FLUSSO TUBI E STATO VALVOLE (PIPES & VALVES) ---
            pipe_flows_dict = {}
            valves_status_dict = {}
            
            for link_name in active_wn.link_name_list:
                flow_val = 0.0
                
                if 'flow' in self.sim.link_res and link_name in self.sim.link_res['flow']:
                    f_data = self.sim.link_res['flow'][link_name]
                    if len(f_data) > current_sim_step_idx:
                        flow_val = f_data[current_sim_step_idx]
                    elif len(f_data) > 0:
                        flow_val = f_data[-1]
                else:
                    try:
                        link_obj = active_wn.get_link(link_name)
                        if hasattr(link_obj, 'flow'):
                            flow_val = link_obj.flow
                    except:
                        flow_val = 0.0
                
                pipe_flows_dict[link_name] = float(flow_val)
                
                if link_name in active_wn.valve_name_list or link_name in active_wn.pump_name_list:
                    try:
                        v_link = active_wn.get_link(link_name)
                        status_str = "OPEN"
                        if hasattr(v_link, 'status'):
                            status_str = str(v_link.status.name).upper()
                        valves_status_dict[link_name] = status_str
                    except:
                        valves_status_dict[link_name] = "OPEN"
                else:
                    valves_status_dict[link_name] = "OPEN"

            # --- CREAZIONE PAYLOAD DEL TIME-STEP PER LA DASHBOARD ---
            step_data = {
                "step": step,
                "time_hours": float(t / 3600),
                "global_metrics": {
                    "satisfaction_pct": float(s_real * 100),
                    "crisis_ratio": float(current_ratio),
                    "source_head": float(source_head),
                    "active_tanks": int(self.agent.opened_count)
                },
                "nodes": node_demands_dict,
                "pipes": pipe_flows_dict,
                "tanks": current_levels_dict,
                "valves": valves_status_dict,
                # Include agent-commanded valve levels (if agent exposes them)
                "valve_commands": (getattr(self.agent, 'current_valve_levels', {})),
                # Expose the low-level valve 'initial_setting' (loss coeff) for diagnostics
                "valve_settings": {
                    ln: float(getattr(active_wn.get_link(ln), 'initial_setting', float('nan')))
                    for ln in active_wn.valve_name_list if ln in active_wn.link_name_list
                }
            }
            dashboard_data.append(step_data)

            # Append per-valve commands to CSV for offline analysis
            try:
                with open(self.valve_csv, "a") as vf:
                    vlevels = getattr(self.agent, 'current_valve_levels', {})
                    for vname, lvl in vlevels.items():
                        vf.write(f"{step},{t/3600:.3f},{vname},{float(lvl):.6f}\n")
            except Exception:
                pass

            # Append valve settings (applied loss coeff) for comparison
            try:
                with open(self.valve_settings_csv, "a") as vf2:
                    for vname in active_wn.valve_name_list:
                        try:
                            vobj = active_wn.get_link(vname)
                            init_set = float(getattr(vobj, 'initial_setting', float('nan')))
                            status = getattr(vobj, 'initial_status', getattr(vobj, 'status', 'UNKNOWN'))
                            status_str = str(status.name).upper() if hasattr(status, 'name') else str(status)
                            vf2.write(f"{step},{t/3600:.3f},{vname},{init_set:.6f},{status_str}\n")
                        except Exception:
                            pass
            except Exception:
                pass


        # Esporta i dati di simulazione nel file data.js (nella cartella Dashobard)
        import json
        with open("Dashobard/data.js", "a") as js_file:
            js_file.write("window.simData = " + json.dumps(dashboard_data, indent=2) + ";\n")

        print("\n✓ Generated Dashobard/data.js - Ready to use without server!")

        return self.sim.get_results()


    
    # Inizializzazione del motore di co-simulazione con parametri dal file di configurazione
    # NOTA: Modifica i parametri in config.py, non qui!


if __name__ == "__main__":
    from config_2 import create_engine
    engine = create_engine()

    results = engine.run_simulation()

    print(f"\nSimulation completed: {len(results.node['pressure'].columns)} nodes.")
    print(f"Detailed logs saved to: {engine.lora_net.log_path}")

    # --- GENERAZIONE GRAFICI DI ANALISI ---
    if 'engine' in locals() and engine.stats['time']:
        print("\nGenerating simulation analysis plots...")
        fig, axes = plt.subplots(3, 1, figsize=(14, 12))
        # Create x-axis proportional to configured simulation duration (in hours).
        try:
            total_hours = float(getattr(engine, 'duration_hours', None))
            if total_hours is None or total_hours <= 0:
                # fallback to last recorded time in stats
                total_hours = engine.stats['time'][-1] / 3600.0 if engine.stats['time'] else 0.0
        except Exception:
            total_hours = engine.stats['time'][-1] / 3600.0 if engine.stats['time'] else 0.0

        # Distribute the x-axis linearly from 0 to total_hours across samples
        import numpy as _np
        time_hours = _np.linspace(0.0, total_hours, num=len(engine.stats['time']))
    
        # Plot 1: Demand Satisfaction
        axes[0].plot(time_hours, engine.stats['satisfaction'], 'b-', linewidth=2, label='Satisfied Demand (%)')
        if hasattr(engine, 'agent') and hasattr(engine.agent, 'threshold'):
            axes[0].axhline(y=engine.agent.threshold * 100.0, color='r', linestyle='--', label=f'Agent Threshold ({engine.agent.threshold * 100.0}%)')
    
        axes[0].set_ylabel('Satisfaction (%)')
        axes[0].set_title('Hydraulic Performance: Demand Satisfaction')
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)

        # Plot 2: IoT Tanks Status (hourly counts based on tank level drawdown)
        # Preferred metric: a tank is ACTIVE in an hour if its level decreased from
        # the first step to the last step of that hour (indicating discharge).
        tank_names = list(engine.water_net.wn.tank_name_list)
        tank_levels = engine.stats.get('tank_levels', [])  # list per step: [level1, level2, ...]
        step_minutes = max(1, int(engine.timestep_s // 60))
        steps_per_hour = max(1, int(60 // step_minutes))

        if tank_names and tank_levels:
            levels_arr = np.array(tank_levels)  # shape: (num_steps, num_tanks)
            num_steps, num_tanks = levels_arr.shape
            num_hours = (num_steps + steps_per_hour - 1) // steps_per_hour

            hourly_active_counts = []
            for h in range(num_hours):
                start = h * steps_per_hour
                end = min(start + steps_per_hour - 1, num_steps - 1)
                if start > end:
                    hourly_active_counts.append(0); continue
                start_vals = levels_arr[start, :]
                end_vals = levels_arr[end, :]
                # count tanks whose level decreased (start > end)
                active = np.sum((start_vals - end_vals) > 1e-6)
                hourly_active_counts.append(int(active))

            hours = np.arange(len(hourly_active_counts), dtype=float)
            axes[1].clear()
            axes[1].bar(hours, hourly_active_counts, color='green', alpha=0.7, label='Tanks discharged per hour')
            axes[1].step(hours, hourly_active_counts, where='mid', color='black', linewidth=1)
            axes[1].set_xlabel('Hour')
            axes[1].set_ylabel('Number of Tanks')
            axes[1].set_ylim(-0.5, num_tanks + 0.5)
            axes[1].set_title('Cyber-Physical Response: Tanks Discharged (hourly)')
            axes[1].legend()
        else:
            # fallback to valve-command based approach if tank levels are unavailable
            step_activity = engine.stats.get('tank_activity_steps', [])
            if step_activity:
                steps_per_hour = max(1, int(60 // step_minutes))
                num_hours = (len(step_activity) + steps_per_hour - 1) // steps_per_hour
                def valve_to_tank(vname):
                    candidates = [
                        vname.replace('IoT_Valve_', 'IoT_Tank_'),
                        vname.replace('IoT_Valve_New_', 'IoT_Tank_'),
                        vname.replace('IoT_ValveNew_', 'IoT_Tank_'),
                        vname.replace('IoTValve_', 'IoT_Tank_')
                    ]
                    for c in candidates:
                        if c in tank_names:
                            return c
                    import re
                    m = re.search(r"(\d+)", vname)
                    if m:
                        idx = m.group(1)
                        for t in tank_names:
                            if idx in t:
                                return t
                    return None

                hourly_active_counts = []
                for h in range(num_hours):
                    start = h * steps_per_hour
                    end = start + steps_per_hour
                    hour_slice = step_activity[start:end]
                    active_tanks = set()
                    for step_snapshot in hour_slice:
                        for valve_name, commanded_level in step_snapshot.items():
                            try:
                                lvl = float(commanded_level)
                            except Exception:
                                continue
                            # fallback rule: treat value > 0 as active
                            if lvl > 0.0:
                                tname = valve_to_tank(valve_name)
                                if tname:
                                    active_tanks.add(tname)
                    hourly_active_counts.append(len(active_tanks))

                hours = np.arange(len(hourly_active_counts), dtype=float)
                axes[1].clear()
                axes[1].bar(hours, hourly_active_counts, color='green', alpha=0.7, label='Active tanks per hour (fallback)')
                axes[1].step(hours, hourly_active_counts, where='mid', color='black', linewidth=1)
                axes[1].set_xlabel('Hour')
                axes[1].set_ylabel('Number of Tanks')
                axes[1].set_ylim(-0.5, len(tank_names) + 0.5)
                axes[1].set_title('Cyber-Physical Response: Emergency Tank Activation (fallback)')
                axes[1].legend()
            else:
                axes[1].text(0.5, 0.5, 'No tank or valve data available', ha='center', va='center')
        axes[1].grid(True, alpha=0.3)

        # Plot 3: Packet Loss and Objective Function
        ax2_twin = axes[2].twinx()
        axes[2].plot(time_hours, engine.stats['packet_loss'], 'orange', linewidth=2, label='Packet Loss (%)')
        ax2_twin.plot(time_hours, engine.stats['reward'], 'purple', linestyle=':', label='Objective F(a)')
    
        axes[2].set_xlabel('Time (hours)')
        axes[2].set_ylabel('Packet Loss (%)', color='orange')
        ax2_twin.set_ylabel('Objective Reward F(a)', color='purple')
        axes[2].set_title('Communication Quality and Agent Reward')
    
        lines1, labels1 = axes[2].get_legend_handles_labels()
        lines2, labels2 = ax2_twin.get_legend_handles_labels()
        axes[2].legend(lines1 + lines2, labels1 + labels2, loc='upper right')
        axes[2].grid(True, alpha=0.3)

        plt.tight_layout()
        plot_path = "Log_review/simulation_analysis.png"
        plt.savefig(plot_path)
        print(f"Plot saved to: {plot_path}")

        # --- GRAFICO LIVELLI SERBATOI ---
        tank_names = engine.water_net.wn.tank_name_list
        if tank_names:
            print("Generating tank levels trend plot...")
            # Usiamo i dati raccolti durante tutta la simulazione (engine.stats['tank_levels'])
            # La lista di liste va convertita in matrice e trasposta per avere (num_tanks, num_steps)
            tank_levels_matrix = np.array(engine.stats['tank_levels']).T
        
            plot_tank_levels_flexible(
                tank_data_matrix=tank_levels_matrix, 
                step_minutes=engine.timestep_s // 60, 
                output_filename='Log_review/tank_levels_trend.png'
            )


