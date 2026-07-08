import sys
import math
import random
import datetime
import json
import numpy as np
from pathlib import Path

# ── Path Setup for LoRaSimPlus Integration ──
_current_dir = Path(__file__).resolve().parent
_lorasim_path = _current_dir.parent / "LoRaSimPlus-main"
if str(_lorasim_path) not in sys.path:
    sys.path.append(str(_lorasim_path))

# ── LoRaSimPlus Imports ──
import ParameterConfig
from ParameterConfig import sensi, SNR_Req, Carrier_Frequency, Ptx, gamma, d0, std, Lpld0, GL
from Propagation import rssi as lorasim_rssi, snr as lorasim_snr
from Packet import myPacket
from Allocation import random_allocation, closest_allocation, polling_allocation


def calculate_gateway_pos(wn, mode: str = 'center', offset_dist: float = 0.0,
                           sensors_list=None, n_gateways: int = 1):
    """
    Calcola la posizione ottimale del gateway sulla topologia della rete idrica.

    Args:
        wn:             WaterNetworkModel instance (mwntr).
        mode:           'center'        — centroide dei sensori/nodi.
                        'random_offset' — centroide + offset casuale di `offset_dist` metri.
                        'kmeans'        — fallback su centroide (sklearn non richiesto).
        offset_dist:    Distanza di offset dal centroide in metri (usata da 'random_offset').
        sensors_list:   Lista opzionale di nomi sensore/valvola per calcolare il centroide
                        solo sui nodi strumentati; se None usa tutti i nodi della rete.

    Returns:
        Singola tupla (x, y) con le coordinate del gateway.
        Può essere esteso a lista di tuple per deployment multi-gateway.
    """
    coords = []

    if sensors_list:
        for name in sensors_list:
            try:
                # Prima prova a recuperare il nodo direttamente
                node = wn.get_node(name)
                if node.coordinates:
                    coords.append(node.coordinates)
            except KeyError:
                # Fallback: è il nome di un link (valvola IoT) — usa il nodo terminale
                try:
                    link = wn.get_link(name)
                    node = wn.get_node(link.end_node_name)
                    if node.coordinates:
                        coords.append(node.coordinates)
                except KeyError:
                    pass
    else:
        coords = [
            node.coordinates
            for _, node in wn.nodes()
            if hasattr(node, 'coordinates') and node.coordinates
        ]

    if not coords:
        return [(0.0, 0.0)] if n_gateways > 1 else (0.0, 0.0)

    if mode == 'kmeans' and n_gateways > 1:
        try:
            import os
            # Fix per prevenire deadlock di thread e hanging di KMeans su Windows
            os.environ["OMP_NUM_THREADS"] = "1"
            os.environ["OPENBLAS_NUM_THREADS"] = "1"
            os.environ["MKL_NUM_THREADS"] = "1"
            os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
            os.environ["NUMEXPR_NUM_THREADS"] = "1"
            
            from sklearn.cluster import KMeans
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                import numpy as np
                X = np.array(coords)
                # Raggruppa i sensori e trova i centroidi ottimali
                kmeans = KMeans(n_clusters=min(n_gateways, len(X)), random_state=42, n_init=10).fit(X)
                return [(float(c[0]), float(c[1])) for c in kmeans.cluster_centers_]
        except ImportError:
            print("Sklearn non trovato. Fallback su campionamento casuale.")
            import random
            return random.sample(coords, min(n_gateways, len(coords)))

    # Modalità singolo gateway
    cx = sum(c[0] for c in coords) / len(coords)
    cy = sum(c[1] for c in coords) / len(coords)

    if mode == 'center' or mode == 'kmeans':
        return (cx, cy)
    
    
    elif mode == 'random_offset':
        if offset_dist <= 0.0:
            return (cx, cy)
        angle = random.uniform(0.0, 2.0 * math.pi)
        return (
            cx + offset_dist * math.cos(angle),
            cy + offset_dist * math.sin(angle),
        )

    return (cx, cy)


class LoRaSystem:
    """Simulates LoRaWAN communication using physics-based RSSI/SNR model (LoRaSimPlus).

    Replaces the old Markov-chain approach with realistic log-distance path loss,
    receiver sensitivity checks, SNR validation, and full collision detection
    (frequency, SF orthogonality, capture effect, timing).

    Public API is fully backward-compatible with the previous implementation.
    """

    _CAPTURE_THRESHOLD_DB = 6  # Capture effect power margin (dB)

    def __init__(self, log_filename: str = "latest_simulation_log.txt",
                 config_params=None, bandwidth: int = 125,
                 payload_size: int = 65, coding_rate: int = 1,
                 tx_power=None):

        self.gateway_pos = (0, 0)
        self.sensors = {}
        self.tx_interval_s = 1800
        self.total_transmissions = 0
        self.total_collisions = 0
        
        self.gateways = []
        
        self.history = []
        self.debug_log = []

        self.bandwidth = bandwidth
        self.payload_size = payload_size
        self.coding_rate = coding_rate

        self._PTX = tx_power if tx_power is not None else Ptx
        self._GAMMA = gamma
        self._D0 = d0
        self._STD = std
        self._LPLD0 = Lpld0
        self._GL = GL
        self._CARRIER_FREQ = Carrier_Frequency
        self._SENSI = sensi
        self._SNR_REQ = SNR_Req

        # Sync instance config back to LoRaSimPlus globals
        ParameterConfig.Ptx = self._PTX
        ParameterConfig.gamma = self._GAMMA
        ParameterConfig.d0 = self._D0
        ParameterConfig.std = self._STD
        ParameterConfig.Lpld0 = self._LPLD0
        ParameterConfig.GL = self._GL
        ParameterConfig.Carrier_Frequency = self._CARRIER_FREQ
        ParameterConfig.PayloadSize = self.payload_size

        self._active_packets = []
        self._rssi_cache = {}

        log_dir = Path("Log_review")
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / log_filename

        with self.log_path.open("w", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write(f"  LORA CO-SIMULATION SESSION (LoRaSimPlus): {datetime.datetime.now()}\n")
            f.write("=" * 60 + "\n")
            if config_params:
                f.write("\n[CONFIG] Simulation Parameters:\n")
                for k, v in config_params.items():
                    f.write(f"   > {k:20}: {v}\n")
                f.write("-" * 60 + "\n\n")

    # ──────────────────────── Logging ────────────────────────
    def _log(self, message: str, level: str = "INFO"):
        formatted_msg = f"[{level:5}] {message}"
        self.debug_log.append(formatted_msg)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(formatted_msg + "\n")

    # ──────────────────────── Gateway ────────────────────────
    def setup_gateways(self, gateway_positions):
        """gateway_positions: lista di tuple (x, y)"""
        self.gateways = []
        for i, pos in enumerate(gateway_positions):
            self.gateways.append({'id': f"GW_{i}", 'pos': pos, 'x': pos[0], 'y': pos[1]})
        self._log(
            f"NETWORK TOPOLOGY: {len(self.gateways)} gateways deployed at optimized locations",
            level="SETUP"
        )

    # ──────────────────── Physics Helpers ────────────────────
    @staticmethod
    def _get_sensitivity(sf, bw):
        return myPacket.GetReceiveSensitivity(sf, bw)

    @staticmethod
    def _get_min_snr(sf):
        return myPacket.GetMiniSNR(sf)

    def _compute_rssi(self, distance_m: float) -> float:
        if distance_m <= 0:
            distance_m = 1.0
        Lpl = (10 * self._GAMMA * math.log10(distance_m / self._D0)
               + np.random.normal(self._LPLD0, self._STD))
        return self._PTX + self._GL - Lpl

    def _compute_rssi_deterministic(self, distance_m: float) -> float:
        if distance_m <= 0:
            distance_m = 1.0
        Lpl = 10 * self._GAMMA * math.log10(distance_m / self._D0) + self._LPLD0
        return self._PTX + self._GL - Lpl

    @staticmethod
    def _compute_snr(rssi: float, bw_khz: int = 125) -> float:
        noise_floor = -174.0 + 10.0 * np.log10(bw_khz * 1e3)
        return rssi - noise_floor

    def _check_receivable(self, rssi: float, snr: float, sf: int, bw: int) -> bool:
        min_sensi = self._get_sensitivity(sf, bw)
        min_snr = self._get_min_snr(sf)
        return (rssi > min_sensi) and (snr > min_snr)

    @staticmethod
    def _airtime_ms(sf, cr, payload_bytes, bw) -> float:
        return myPacket.airtime(sf, cr, payload_bytes, bw)

    # ──────────────────── Collision Detection ────────────────
    @staticmethod
    def _frequency_collision(p1, p2) -> bool:
        if abs(p1['freq'] - p2['freq']) <= 120e3 and (p1['bw'] == 500 or p2['bw'] == 500):
            return True
        elif abs(p1['freq'] - p2['freq']) <= 60e3 and (p1['bw'] == 250 or p2['bw'] == 250):
            return True
        elif abs(p1['freq'] - p2['freq']) <= 30e3:
            return True
        return False

    @staticmethod
    def _sf_collision(p1, p2) -> bool:
        return p1['sf'] == p2['sf']

    @staticmethod
    def _timing_collision(p1, p2) -> bool:
        Tpreamb = (2 ** p1['sf']) / (1.0 * p1['bw']) * (8 - 5)
        p2_end = p2['end_abs_ms']
        p1_cs = p1['start_abs_ms'] + Tpreamb
        return p1_cs < p2_end

    def _power_collision(self, p1, p2):
        diff = abs(p1['rssi'] - p2['rssi'])
        if diff < self._CAPTURE_THRESHOLD_DB:
            return [p1, p2]
        elif p2['rssi'] - p1['rssi'] > self._CAPTURE_THRESHOLD_DB:
            return [p1]
        return [p2]

    def _detect_collisions(self, new_packets):
        all_inflight = self._active_packets + new_packets
        for pkt in new_packets:
            if pkt.get('lost', False):
                continue
            for other in all_inflight:
                if other is pkt or other.get('lost', False):
                    continue
                if pkt.get('gw_id') == other.get('gw_id'):
                    if self._frequency_collision(pkt, other) and self._sf_collision(pkt, other):
                        if self._timing_collision(pkt, other):
                            for c in self._power_collision(pkt, other):
                                c['collided'] = True

    def _purge_expired(self, current_time_ms: float):
        self._active_packets = [
            p for p in self._active_packets if p['end_abs_ms'] > current_time_ms
        ]

    # ──────────────────── Sensor Registration ────────────────
    def register_iot_sensors(self, valves, wn, mode: str = 'simple',
                             sf_mode: str = 'sequential', fixed_sf: int = 10):
        self._log(f"NETWORK TOPOLOGY: {len(valves)} sensors registered", level="SETUP")
        self._log(
            f"  Physics: Ptx={self._PTX}dBm, BW={self.bandwidth}kHz, "
            f"PL={self.payload_size}B, gamma={self._GAMMA}", level="SETUP"
        )

        for i, v_name in enumerate(valves):
            node_name = v_name.replace("IoT_Valve_", "")
            if node_name in wn.nodes:
                node = wn.get_node(node_name)
            else:
                node = wn.get_node(wn.get_link(v_name).start_node_name)

            tx_x, tx_y = node.coordinates

            best_gw = None
            min_dist = float('inf')
            for gw in self.gateways:
                gw_x, gw_y = gw['pos']
                dist = math.hypot(tx_x - gw_x, tx_y - gw_y)
                if dist < min_dist:
                    min_dist = dist
                    best_gw = gw

            dist_m = min_dist
            real_dist = dist_m / 1000.0

            if sf_mode == 'random':
                sf, bw, freq = random_allocation()
            elif sf_mode in ('closest', 'distance'):
                sf, bw, freq = closest_allocation(dist_m)
            elif sf_mode in ('polling', 'sequential'):
                sf, bw, freq = polling_allocation(i)
            else:  # 'fixed' or fallback
                sf = fixed_sf
                bw = self.bandwidth
                freq = self._CARRIER_FREQ[i % len(self._CARRIER_FREQ)]

            bw = int(bw)
            freq = float(freq)

            base_rssi = self._compute_rssi_deterministic(dist_m)
            base_snr = self._compute_snr(base_rssi, bw)
            self._rssi_cache[v_name] = base_rssi

            sensi_val = self._get_sensitivity(sf, bw)
            min_snr_val = self._get_min_snr(sf)
            baseline_ok = (base_rssi > sensi_val) and (base_snr > min_snr_val)

            self.sensors[v_name] = {
                'x': tx_x,
                'y': tx_y,
                'tx_count': 0,
                'rx_count': 0,
                'distance': real_dist,
                'distance_m': dist_m,
                'assigned_gw_id': best_gw['id'] if best_gw else 'UNKNOWN',
                'assigned_gw_pos': best_gw['pos'] if best_gw else (0, 0),
                'sf': sf,
                'bw': bw,
                'freq': freq,
                'cr': self.coding_rate,
                'base_rssi': base_rssi,
                'last_tx_time': -9999.0,
                'data': {},
            }

            self._log(
                f"NODE {v_name:15} | SF{sf:2} BW{bw:3} | Dist: {real_dist:5.2f}km |  "
                f"RSSI: {base_rssi:6.1f}dBm | SNR: {base_snr:5.1f}dB |  "
                f"RX: {'OK' if baseline_ok else 'MARGINAL'}", level="REG"
            )

    # ──────────────────────── Metrics ────────────────────────
    def get_packet_loss_rate(self) -> float:
        if self.total_transmissions == 0:
            return 0.0
        plr = (self.total_collisions / self.total_transmissions) * 100.0
        self._log(f"STATS CHECK: PLR={plr:.2f}% | Total TX={self.total_transmissions}")
        return plr

    # ──────────────────── Step Simulation ────────────────────
    def step(self, current_time: float, timestep_s: float) -> list:
        """Simulate one co-simulation timestep with LoRaWAN Macro-Diversity."""
        if not hasattr(self, 'gateway_busy_time_ms') or not isinstance(self.gateway_busy_time_ms, dict):
            self.gateway_busy_time_ms = {gw['id']: 0.0 for gw in self.gateways}

        current_time_ms = current_time * 1000.0
        self._purge_expired(current_time_ms)

        new_packets = []
        
        for s_id, s_node in self.sensors.items():
            num_packets = max(1, int(timestep_s / max(1, self.tx_interval_s)))
            s_node['tx_count'] += num_packets

            for pkt_idx in range(num_packets):
                self.total_transmissions += 1
                s_node['last_tx_time'] = current_time
                
                # Jitter comune per l'evento di trasmissione fisica (il sensore trasmette una volta sola)
                jitter_ms = random.uniform(0, timestep_s * 1000.0)
                pkt_start_ms = current_time_ms + jitter_ms
                
                # Crea un ID univoco per questo specifico evento di trasmissione per deduplicarlo dopo
                tx_event_id = f"{s_id}_{current_time_ms}_{pkt_idx}"

                sf = s_node['sf']
                bw = s_node['bw']

                actual_freq = float(random.choice(self._CARRIER_FREQ))

                data_dict = s_node.get('data', {})
                payload_str = json.dumps(data_dict, separators=(',', ':'))
                actual_payload_bytes = max(self.payload_size, len(payload_str.encode('utf-8')))
                airtime = self._airtime_ms(sf, s_node['cr'], actual_payload_bytes, bw)

                # 🔴 LA VERA FISICA LORAWAN: Il pacchetto viaggia verso TUTTI i gateway
                for gw in self.gateways:
                    gw_id = gw['id']
                    
                    # Ricalcola la distanza e il segnale per QUESTO specifico gateway
                    dist_m = math.dist((s_node['x'], s_node['y']), (gw['x'], gw['y']))
                    dist_m = max(dist_m, 1.0)
                    
                    rssi_val = self._compute_rssi(dist_m)
                    snr_val = self._compute_snr(rssi_val, bw)
                    is_receivable = self._check_receivable(rssi_val, snr_val, sf, bw)

                    gw_busy_time = self.gateway_busy_time_ms.get(gw_id, 0.0)
                    gw_is_transmitting = pkt_start_ms < (current_time_ms + gw_busy_time)

                    if gw_is_transmitting:
                        is_receivable = False

                    # Creiamo un'istanza (clone) del pacchetto "in volo" verso questo gateway
                    pkt_clone = {
                        'tx_event_id': tx_event_id, # ID per la deduplicazione finale
                        'sensor_id': s_id,
                        'gw_id': gw_id,             # Fondamentale per far collidere solo ai GW corretti
                        'sf': sf,
                        'bw': bw,
                        'freq': actual_freq,
                        'rssi': rssi_val,
                        'snr': snr_val,
                        'airtime_ms': airtime,
                        'start_abs_ms': pkt_start_ms,
                        'end_abs_ms': pkt_start_ms + airtime,
                        'lost': not is_receivable,
                        'collided': False,
                        'gw_busy': gw_is_transmitting,
                        'data': data_dict,
                    }
                    new_packets.append(pkt_clone)

        if new_packets:
            # Rileva collisioni (che grazie alla nostra passata modifica controllerà gw_id == gw_id)
            self._detect_collisions(new_packets)

        self._active_packets.extend(new_packets)

        # 🔴 NETWORK SERVER: Deduplicazione dei pacchetti ricevuti
        received = []
        delivered_tx_ids = set() # Traccia le trasmissioni già raccolte da almeno un gateway
        
        for pkt in new_packets:
            if not pkt['lost'] and not pkt['collided']:
                if pkt['tx_event_id'] not in delivered_tx_ids:
                    delivered_tx_ids.add(pkt['tx_event_id'])
                    received.append({'id': pkt['sensor_id'], 'data': pkt['data']})
                    self.sensors[pkt['sensor_id']]['rx_count'] += 1
                    if random.random() < 0.1:
                        self._log(f"TX @{(pkt['start_abs_ms']/1000.0):8.1f}s | {pkt['sensor_id']:15} | SF{pkt['sf']:2} | SUCCESS via {pkt['gw_id']}", level="COMM")
                    
        # 2. Calcolo corretto delle perdite per il Macro-Diversity
        # Troviamo tutti gli ID univoci generati in questo step
        all_tx_ids_this_step = set(p['tx_event_id'] for p in new_packets)
        
        # Una trasmissione è persa a livello di rete SOLO se il suo ID non è tra quelli consegnati
        failed_tx_ids = all_tx_ids_this_step - delivered_tx_ids
        
        # Aggiorniamo il contatore globale.
        self.total_collisions += len(failed_tx_ids)
                    
        return received

    def step_downlink(self, commands_dict: dict, current_time: float,
                      timestep_s: float) -> dict:
        GW_TX_POWER = 27.0  # dBm
        received_commands = {}

        for s_id, payload in commands_dict.items():
            if s_id not in self.sensors:
                continue

            s_node = self.sensors[s_id]
            dist_m = max(s_node['distance_m'], 1.0)
            sf = s_node['sf']
            bw = s_node['bw']
            gw_id = s_node['assigned_gw_id'] # A quale gateway stiamo parlando?

            Lpl = (10 * self._GAMMA * math.log10(dist_m / self._D0)
                   + np.random.normal(self._LPLD0, self._STD))
            rssi_down = GW_TX_POWER + self._GL - Lpl
            snr_down = self._compute_snr(rssi_down, bw)

            is_receivable = self._check_receivable(rssi_down, snr_down, sf, bw)

            if is_receivable:
                received_commands[s_id] = payload
                
                # Aggiungi il peso del downlink al gateway che lo sta trasmettendo
                cmd_bytes = len(json.dumps(payload).encode('utf-8'))
                airtime = self._airtime_ms(sf, s_node['cr'], max(self.payload_size, cmd_bytes), bw)
                self.gateway_busy_time_ms[gw_id] += airtime
                
                self._log(f"DL @{current_time:8.1f}s | To: {s_id:15} | SUCCESS via {gw_id}", level="COMM")
            else:
                self._log(f"DL @{current_time:8.1f}s | To: {s_id:15} | LOST", level="COMM")

        return received_commands