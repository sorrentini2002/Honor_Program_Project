import os
import math
import logging
import torch
from mwntr.network.elements import LinkStatus
from .base_agent import BaseAgent
from .models.gnn import WaterNetworkGNN

logger = logging.getLogger(__name__)


class PriorityAgent(BaseAgent):
    """
    Agente idraulico Ibrido (Priority-Driven / Collective-Driven).

    - Se presenti nodi prioritari o valvole di isolamento: protegge le zone sensibili.
    - Se assenti: Modalità Collettiva — massimizza la pressione media della rete.
    """

    def __init__(self, water_net, lora_net,
                 threshold: float = 0.90,
                 aggression: float = 1.0,
                 alpha: float = 0.80,
                 crisis_start_time_s: float = 0.0):

        super().__init__(water_net, lora_net, threshold, aggression, alpha)
        self.crisis_start_time_s = crisis_start_time_s

        # ── Rilevamento dinamico topologia ──
        self.priority_nodes = (
            water_net._get_priority_nodes()
            if hasattr(water_net, '_get_priority_nodes') else []
        )
        
        junction_names = list(water_net.wn.junction_name_list)
        active_junctions = [
            j for j in junction_names
            if water_net.wn.get_node(j).demand_timeseries_list and 
               water_net.wn.get_node(j).demand_timeseries_list[0].base_value > 0
        ]
        if not active_junctions:
            active_junctions = junction_names
        
        total_active_junctions = len(active_junctions)
        
        self.collective_mode = (
            len(self.priority_nodes) == 0 or 
            (total_active_junctions > 0 and len(self.priority_nodes) >= total_active_junctions - 2)
        )

        # ── Valvole di isolamento controllabili: leggi dalla rete se disponibili ──
        self.isolation_valves = (
            getattr(water_net, 'controllable_isolation_valves', [])
        )

        # 🔴 MODIFICA: Logiche QoS Separate (Egalitario vs Prioritario)
        if self.collective_mode:
            self.isolation_valves = []
            
            # =====================================================================
            # 🔴 QoS EGALITARIO: Adaptive Data Rate spaziale (Relativo)
            # =====================================================================
            if self.lora_net is not None and hasattr(self.lora_net, 'sensors') and self.lora_net.sensors:
                # 1. Troviamo la distanza minima e massima per creare una scala relativa [0.0 - 1.0]
                distances = [data.get('distance_m', 0.0) for data in self.lora_net.sensors.values()]
                if distances:
                    min_dist = min(distances)
                    max_dist = max(distances)
                    # Evitiamo divisioni per zero se c'è un solo nodo o sono tutti alla stessa distanza
                    dist_range = (max_dist - min_dist) if (max_dist - min_dist) > 0.001 else 1.0
                    
                    # 2. Assegniamo i parametri dinamicamente
                    for s_id, s_node in self.lora_net.sensors.items():
                        dist = s_node.get('distance_m', 0.0)
                        rel_dist = (dist - min_dist) / dist_range  # Valore normalizzato [0.0, 1.0]
                        
                        # SF Proporzionale (Scala continua da 7 a 12)
                        sf_val = int(round(7 + (rel_dist * 5)))
                        sf_val = max(7, min(12, sf_val)) # Sicurezza sui limiti
                        
                        # 3. Tre fasce per Larghezza di Banda e Coding Rate
                        if rel_dist < 0.33:
                            # FASCIA 1: Vicinissimi -> "Proiettili" velocissimi, poca ridondanza
                            bw_val = 500
                            cr_val = 1
                        elif rel_dist < 0.66:
                            # FASCIA 2: Intermedi -> Bilanciamento
                            bw_val = 250
                            cr_val = 2
                        else:
                            # FASCIA 3: Lontani -> Lenti ma ultra-resistenti al rumore e collisioni
                            bw_val = 125
                            cr_val = 4
                            
                        # Sovrascriviamo le impostazioni del nodo
                        self.lora_net.sensors[s_id]['sf'] = sf_val
                        self.lora_net.sensors[s_id]['bw'] = bw_val
                        self.lora_net.sensors[s_id]['cr'] = cr_val
                        
                    logger.info(f"QoS Egalitario: Scala relativa applicata a {len(self.lora_net.sensors)} sensori.")

        else:
            # =====================================================================
            # 🔴 QoS PRIORITARIO: Profilo "URLLC" (Corazzato) per nodi vitali
            # =====================================================================
            if self.lora_net is not None and hasattr(self.lora_net, 'sensors'):
                vital_nodes = set(self.priority_nodes + self.isolation_valves)
                for s_id in self.lora_net.sensors.keys():
                    if "IoT_Valve" in s_id or "Tank" in s_id:
                        vital_nodes.add(s_id)
                
                for v_id in vital_nodes:
                    if v_id in self.lora_net.sensors:
                        self.lora_net.sensors[v_id]['sf'] = 7       
                        self.lora_net.sensors[v_id]['bw'] = 500     
                        self.lora_net.sensors[v_id]['cr'] = 4       
                        
                logger.info(f"QoS Prioritario: {len(vital_nodes)} nodi critici promossi a SF7/BW500/CR4")

        self.VALVE_MAX_OPENING = 1.0
        self.LOSS_COEFF_MIN_IOT = 50.0
        self.LOSS_COEFF_MAX_IOT = 50_000.0
        self.K_ISO_MIN = 0.0
        self.K_ISO_MAX = 50_000.0
        self.K_ISO_CLOSE_THRESHOLD = 10_000.0

        self.TX_INTERVAL_NOMINAL = 3600
        self.TX_INTERVAL_ALERT = 300

        self.current_valve_level = 0.0
        self.current_valve_levels: dict = {}
        self.opened_count = 0

        # ── GNN e Belief State ──
        self.node_names = list(water_net.wn.node_name_list)
        self.num_nodes = len(self.node_names)
        self.node_to_idx = {name: i for i, name in enumerate(self.node_names)}

        self.gnn = WaterNetworkGNN(in_features=4, hidden_features=16, out_features=2)
        self.belief_state = torch.zeros((self.num_nodes, 2))
        self.belief_state[:, 0] = 35.0   # Inizializzazione ottimistica (pressione nominale)

        self._build_adjacency_matrix(water_net.wn)

        # ── Tracciamento AoI ──
        tracked_entities = self.priority_nodes + self.water_net.iot_valves
        self.last_update_time = {entity: 0.0 for entity in tracked_entities}

        self.uncertainty_weight = 0.3
        self.max_uncertainty_time = 7200.0

        # ── Log di inizializzazione ──
        self.log_path = os.path.join("Log_review", "agent_performance.txt")
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        mode_str = "COLLECTIVE" if self.collective_mode else "PRIORITY"
        with open(self.log_path, "w") as f:
            f.write(f"--- AGENT INITIALIZED IN {mode_str} MODE ---\n")
            f.write("STEP | MAX_RISK | DEFICIT | UNCERTAINTY | IOT_OPEN | ISO_K | TX_INT\n")
            f.write("-" * 85 + "\n")

    def _build_adjacency_matrix(self, wn):
        """Costruisce la matrice di adiacenza normalizzata per la GNN."""
        adj = torch.zeros((self.num_nodes, self.num_nodes))
        for link_name in wn.link_name_list:
            link = wn.get_link(link_name)
            try:
                u = self.node_to_idx[link.start_node_name]
                v = self.node_to_idx[link.end_node_name]
                adj[u, v] = 1.0
                adj[v, u] = 1.0
            except KeyError:
                continue

        deg = torch.sum(adj, dim=1)
        deg_inv_sqrt = torch.pow(deg + 1e-6, -0.5)
        norm_adj = deg_inv_sqrt.view(-1, 1) * adj * deg_inv_sqrt.view(1, -1)

        self.adj_matrix = norm_adj + torch.eye(self.num_nodes)
        deg2 = torch.sum(self.adj_matrix, dim=1)
        deg2_inv_sqrt = torch.pow(deg2 + 1e-6, -0.5)
        self.adj_matrix = deg2_inv_sqrt.view(-1, 1) * self.adj_matrix * deg2_inv_sqrt.view(1, -1)

# ──────────────────────────────────────────────────────────────────────────
    # Ciclo decisionale
    # ──────────────────────────────────────────────────────────────────────────

    def decide_action(self, step: int, t: float, received_telemetry: list,
                       sim=None) -> dict:
        import math
        
        # 1. Costruzione osservazione corrente
        current_obs = self.belief_state[:, 0:1].clone()
        current_obs = torch.cat([
            current_obs,
            torch.zeros((self.num_nodes, 1)),
            torch.zeros((self.num_nodes, 1)),
            torch.zeros((self.num_nodes, 1)),
        ], dim=1)
        valid_mask = torch.zeros(self.num_nodes, dtype=torch.bool)

        # 2. Assimilazione telemetria uplink
        for pkt in received_telemetry:
            s_id = pkt.get('id')
            data = pkt.get('data', {})
            target_node_name = None

            if data.get('type') == 'PRIORITY_NODE':
                target_node_name = s_id
                if s_id in self.last_update_time:
                    self.last_update_time[s_id] = t

            elif data.get('type') == 'IOT_TANK':
                if s_id in self.last_update_time:
                    self.last_update_time[s_id] = t
                try:
                    if sim and hasattr(sim, '_wn'):
                        link = sim._wn.get_link(s_id)
                        target_node_name = link.end_node_name
                        served = data.get('served_priority_node')
                        if served and served in self.last_update_time:
                            self.last_update_time[served] = t
                except Exception:
                    continue

            if target_node_name and target_node_name in self.node_to_idx:
                idx = self.node_to_idx[target_node_name]
                valid_mask[idx] = True
                current_obs[idx, 0] = data.get('node_p', 0.0)
                current_obs[idx, 1] = data.get('tank_lvl', 0.0)
                current_obs[idx, 2] = data.get('v_setting', 0.0)
                if data.get('is_priority_node', False) or target_node_name in self.priority_nodes:
                    current_obs[idx, 3] = 1.0

        # 3. Aggiornamento Belief State tramite GNN
        with torch.no_grad():
            new_belief = self.gnn(current_obs, self.adj_matrix)
            alpha_mem = 0.8
            self.belief_state = torch.where(
                valid_mask.unsqueeze(1),
                new_belief,
                alpha_mem * self.belief_state + (1 - alpha_mem) * new_belief
            )

        # 4. Calcolo rischio bi-obiettivo
        max_risk = 0.0
        max_deficit = 0.0
        max_uncertainty = 0.0
        req_pressure = 35.0

        if self.collective_mode:
            est_pressures = self.belief_state[:, 0]
            deficits = torch.clamp((req_pressure - est_pressures) / req_pressure, min=0.0)
            max_deficit = torch.mean(deficits).item()
            if self.water_net.iot_valves:
                times_since = [t - self.last_update_time.get(v, 0.0) for v in self.water_net.iot_valves]
                max_uncertainty = min(1.0, sum(times_since) / len(times_since) / self.max_uncertainty_time)
            max_risk = ((1.0 - self.uncertainty_weight) * max_deficit + self.uncertainty_weight * max_uncertainty)
        else:
            for p_node in self.priority_nodes:
                if p_node not in self.node_to_idx: continue
                idx = self.node_to_idx[p_node]
                est_pressure = self.belief_state[idx, 0].item()
                deficit = max(0.0, (req_pressure - est_pressure) / req_pressure)
                max_deficit = max(max_deficit, deficit)
                time_since = t - self.last_update_time.get(p_node, 0.0)
                uncertainty = min(1.0, time_since / self.max_uncertainty_time)
                max_uncertainty = max(max_uncertainty, uncertainty)
                risk = ((1.0 - self.uncertainty_weight) * deficit + self.uncertainty_weight * uncertainty)
                max_risk = max(max_risk, risk)

        # 5. Controllo di emergenza: cisterne in esaurimento (INDIPENDENTE)
        critical_valves = []
        if sim and hasattr(sim, '_wn'):
            for v_name in self.water_net.iot_valves:
                try:
                    link = sim._wn.get_link(v_name)
                    tank_name = link.start_node_name
                    if hasattr(sim, 'node_res') and 'pressure' in sim.node_res and tank_name in sim.node_res['pressure']:
                        p_data = sim.node_res['pressure'][tank_name]
                        current_level = float(p_data[-1]) if len(p_data) > 0 else sim._wn.get_node(tank_name).level
                    else:
                        current_level = sim._wn.get_node(tank_name).level
                    if current_level < 1.0:
                        critical_valves.append(v_name)
                except Exception:
                    pass

        # ── 6. LOGICA DECISIONALE (RIPRISTINATA ONNISCIENZA + TEMPESTA CYBER) ──

        # L'agente sa quando inizia la crisi (Onnisciente)
        if t < self.crisis_start_time_s:
            self._valves_closed_since = None
            return {
                "open_valve": False,
                "loss_coeff": self.LOSS_COEFF_MAX_IOT,
                "tx_interval": self.TX_INTERVAL_NOMINAL,
                "k_iso_dict": {v: self.K_ISO_MIN for v in self.isolation_valves},
                "step": step,
                "critical_valves": critical_valves,
            }   

        if not hasattr(self, '_valves_closed_since'):
            self._valves_closed_since = None

        effective_threshold = (
            self.threshold * 1.05
            if self._valves_closed_since is not None
            else self.threshold
        )
        risk_exceeds = max_risk > (1.0 - effective_threshold)

        # 🔴 MODIFICA: Ottimizzazione a Ciclo Chiuso Consapevole della Congestione Radio
        # Sfrutta il riferimento lora_net inserito dal motore di co-simulazione per leggere il reale degrado attuale
        current_plr = 0.0
        if hasattr(self, 'lora_net') and self.lora_net:
            # get_packet_loss_rate() restituisce una percentuale (es. 75.0), la convertiamo in frazione [0, 1]
            current_plr = self.lora_net.get_packet_loss_rate() / 100.0 

        candidate_txs = [60, 120, 300, 600, 1800, 3600]
        best_tx = self.TX_INTERVAL_NOMINAL
        best_score = -1e9
        est_sat = max(0.0, 1.0 - max_deficit)

        for ctx in candidate_txs:
            base_obj = self.compute_objective(est_sat, ctx)
            
            # 1. Modello Euristico del Packet Loss atteso in base alla scelta di 'ctx':
            # Se la rete è già congestionata e decidiamo di rimanere in allarme (< 300s), la perdita rimarrà altissima.
            # Se decidiamo di fare "back-off" allentando la pressione (> 600s), stimiamo che la rete respirerà dimezzando la perdita.
            if ctx <= 120:
                expected_plr = max(current_plr, 0.4 * current_plr + 0.4) if current_plr > 0.05 else 0.0
            elif ctx <= 600:
                expected_plr = current_plr * 0.7 if current_plr > 0.05 else 0.0
            else:
                expected_plr = current_plr * 0.2 if current_plr > 0.05 else 0.0
            
            # 2. Correzione dell'Incertezza reale: se i pacchetti collidono, l'intervallo temporale effettivo si dilata
            effective_interval = ctx / (1.0 - min(0.95, expected_plr))
            est_unc = min(1.0, effective_interval / self.max_uncertainty_time)
            
            est_risk = ((1.0 - self.uncertainty_weight) * max_deficit + self.uncertainty_weight * est_unc)
            
            panic_multiplier = 5.0 if max_deficit > 0.02 else 0.5
            
            # 3. Penalità di Rete: sanziona le scelte che distruggono l'infrastruttura cyber
            network_penalty = expected_plr * self.aggression * 2.0
            
            # Il punteggio finale ora bilancia: Obiettivo Idraulico - Rischio Corretto - Penalità Congestione
            score = base_obj - (est_risk * self.aggression * panic_multiplier) - network_penalty
            
            if score > best_score:
                best_score = score
                best_tx = ctx

        tx_interval = best_tx

        if risk_exceeds or len(critical_valves) > 0:
            risk_norm = (min(1.0, max_risk / effective_threshold) if effective_threshold > 0 else 1.0)
            
            # ─ Gestione Razionata delle Cisterne IoT ─
            trustworthy_deficit = (max_deficit > 0.01) or (max_uncertainty > 0.3)
            if trustworthy_deficit:
                def_range = (1.0 - effective_threshold)
                deficit_norm = min(1.0, max_deficit / def_range) if def_range > 0 else max_deficit
                
                # 1. Calcolo delle ore trascorse dall'inizio della crisi
                crisis_hours = max(0.0, (t - self.crisis_start_time_s) / 3600.0)

                # 2. Profilo di razionamento basato sul tempo (Time-based Throttling)
                if crisis_hours < 2.0:
                    # Prime ore: evitiamo lo svuotamento immediato causato dal panico iniziale
                    time_cap = 0.15
                elif crisis_hours < 12.0:
                    # Fase centrale: concediamo un maggiore supporto alla rete, ma comunque limitato
                    time_cap = 0.30
                elif crisis_hours < 24.0:
                    # Razionamento per estendere la durata residua dell'acqua
                    time_cap = 0.15
                else:
                    # Sopravvivenza a lungo termine: filo d'acqua
                    time_cap = 0.05
                
                # 3. Applichiamo i limiti calcolati all'apertura effettiva
                if max_deficit <= 0.01 and max_uncertainty > 0.3:
                    # Pura incertezza: apertura esplorativa minima
                    self.current_valve_level = min(0.10, time_cap) * self.VALVE_MAX_OPENING
                else:
                    # L'apertura è proporzionale al deficit e all'aggressività, 
                    # MA non può MAI superare il limite dettato dal tempo (time_cap)
                    base_requested_opening = deficit_norm * self.aggression * 0.5
                    self.current_valve_level = min(base_requested_opening, time_cap) * self.VALVE_MAX_OPENING
                    
                open_valve = True
            else:
                self.current_valve_level = 0.0
                open_valve = False

            # 🔴 CHIUSURA VALVOLE DI LINEA (MODIFICATA)
            if self.collective_mode:
                # Se tutti (o nessuno) sono prioritari, NON tocchiamo le valvole di linea.
                # La crisi è gestita ESCLUSIVAMENTE tramite l'immissione di acqua dalle cisterne IoT.
                k_iso = self.K_ISO_MIN
            else:
                # 1. Calcoliamo il fattore tempo (da 0.0 a 1.0) nelle prime 3 ore della crisi
                crisis_hours = max(0.0, (t - self.crisis_start_time_s) / 3600.0)
                time_factor = min(1.0, crisis_hours / 3.0)  # Sbarramento totale dopo 3 ore
                
                # 2. Sbarramento progressivo: cresce proporzionalmente al rischio E al tempo
                k_iso = self.K_ISO_MIN + (risk_norm * time_factor * (self.K_ISO_MAX - self.K_ISO_MIN))
                
                if self._valves_closed_since is None and k_iso > self.K_ISO_MIN:
                    self._valves_closed_since = t

        else:
            # ─ Logica di Disattivazione con Memoria (Ripristinata) ─
            MIN_CLOSED_DURATION_HOURS = 2.0
            
            if self._valves_closed_since is not None:
                hours_since = (t - self._valves_closed_since) / 3600.0
                if hours_since < MIN_CLOSED_DURATION_HOURS:
                    self.current_valve_level = 0.0
                    open_valve = False
                    tx_interval = min(tx_interval, 600)  # Evita che la tempesta duri all'infinito
                    k_iso = self.K_ISO_MAX * 0.7  
                else:
                    self.current_valve_level = 0.0
                    tx_interval = self.TX_INTERVAL_NOMINAL
                    open_valve = False
                    k_iso = self.K_ISO_MIN
                    self._valves_closed_since = None
            else:
                self.current_valve_level = 0.0
                tx_interval = self.TX_INTERVAL_NOMINAL
                open_valve = False
                k_iso = self.K_ISO_MIN

        # Conversione logaritmica dell'apertura → coefficiente di perdita TCV
        if self.current_valve_level > 0.001:
            ratio = max(0.001, min(1.0, self.current_valve_level / self.VALVE_MAX_OPENING))
            log_min = math.log(self.LOSS_COEFF_MIN_IOT)
            log_max = math.log(self.LOSS_COEFF_MAX_IOT)
            loss_coeff = math.exp(log_max - ratio * (log_max - log_min))
        else:
            loss_coeff = self.LOSS_COEFF_MAX_IOT

        loss_coeff = max(self.LOSS_COEFF_MIN_IOT, min(self.LOSS_COEFF_MAX_IOT, loss_coeff))

        self.critical_valves = critical_valves
        
        # Crea un dizionario che associa ad ogni valvola di isolamento il suo valore K_ISO
        k_iso_dict = {v_name: k_iso for v_name in self.isolation_valves}

        with open(self.log_path, "a") as f:
            f.write(
                f"{step:4d} | {max_risk:.4f} | {max_deficit:.4f} | {max_uncertainty:.4f} | "
                f"{self.current_valve_level:.4f} | {k_iso:.2f} | {tx_interval} | "
                f"{loss_coeff:.2f}\n"
            )

        return {
            "open_valve": open_valve,
            "loss_coeff": loss_coeff,
            "tx_interval": tx_interval,
            "k_iso": k_iso,
            "step": step,
            "critical_valves": critical_valves,
        }
    # ──────────────────────────────────────────────────────────────────────────
    # Formattazione comandi downlink
    # ──────────────────────────────────────────────────────────────────────────

    def format_downlink_commands(self, action: dict) -> dict:
        commands = {}
        for v_name in self.water_net.iot_valves:
            commands[v_name] = {
                'cmd_type': 'SET_VALVE',
                'open': action['open_valve'],
                'k_setting': action['loss_coeff'],
            }
        # FIX: include k_setting nel comando di isolamento
        # k_iso è il coefficiente di perdita della valvola (0 = aperta, K_ISO_MAX = chiusa)
        k_iso = action.get('k_iso', self.K_ISO_MIN)
        for v_name in self.isolation_valves:
            commands[v_name] = {
                'cmd_type': 'SET_ISO_VALVE',
                'k_setting': k_iso,
            }
        return commands

  # ──────────────────────────────────────────────────────────────────────────
    # Applicazione azioni idrauliche
    # ──────────────────────────────────────────────────────────────────────────

    def apply_mitigation(self, received_downlink: dict, sim, lora_net, t=None):
        import math
        self.opened_count = 0
        g = 9.81
        
        critical_valves = getattr(self, 'critical_valves', [])

        for s_id, cmd in received_downlink.items():
            try:
                link = sim._wn.get_link(s_id)
                
                if cmd['cmd_type'] == 'SET_VALVE':
                    # Apre solo se c'è emergenza (open_valve) E la cisterna NON è vuota
                    if cmd['open'] and cmd['k_setting'] < self.LOSS_COEFF_MAX_IOT and s_id not in critical_valves:
                        link._user_status = LinkStatus(1)
                        link._internal_status = LinkStatus(1)
                        link._setting = cmd['k_setting']
                        if hasattr(self, 'current_valve_levels'):
                            self.current_valve_levels[s_id] = getattr(self, 'current_valve_level', 0.0)
                        self.opened_count += 1
                    else:
                        link._user_status = LinkStatus(0)
                        link._internal_status = LinkStatus(0)
                        link._setting = self.LOSS_COEFF_MAX_IOT
                        if hasattr(self, 'current_valve_levels'):
                            self.current_valve_levels[s_id] = 0.0

                elif cmd['cmd_type'] == 'SET_ISO_VALVE':
                    k_val = cmd['k_setting']
                    if k_val < self.K_ISO_CLOSE_THRESHOLD:
                        link._user_status = LinkStatus(1)
                        link._internal_status = LinkStatus(1)
                        link._setting = k_val
                    else:
                        link._user_status = LinkStatus(0)
                        link._internal_status = LinkStatus(0)
                        link._setting = self.K_ISO_MAX

                try:
                    sim._model_updater.update(sim._model, sim._wn, link, 'status')
                    sim._model_updater.update(sim._model, sim._wn, link, 'setting')
                except Exception:
                    pass
                
                try:
                    if hasattr(sim._model, 'valve_setting') and s_id in sim._model.valve_setting:
                        sim._model.valve_setting[s_id].value = link._setting
                    
                    if hasattr(sim._model, 'tcv_resistance') and s_id in sim._model.tcv_resistance:
                        D = link.diameter
                        if D > 0:
                            effective_K = 1e8 if link._user_status == LinkStatus(0) else link._setting
                            r = 8.0 * effective_K / (g * math.pi**2 * D**4)
                            sim._model.tcv_resistance[s_id].value = r
                except Exception:
                    pass

            except Exception as exc:
                import logging
                logger = logging.getLogger(__name__)
                logger.debug("apply_mitigation: link %s — %s", s_id, exc)

    # ──────────────────────────────────────────────────────────────────────────
    # Funzione obiettivo
    # ──────────────────────────────────────────────────────────────────────────

    def compute_objective(self, satisfaction: float, tx_interval: float) -> float:
        """
        Calcola la reward bi-obiettivo: soddisfazione idraulica + efficienza LoRa.

        Args:
            satisfaction: Soddisfazione globale in [0, 1].
            tx_interval:  Intervallo TX corrente in secondi.

        Returns:
            Scalare in [0, 1] — alto = buono.
        """
        # Efficienza comunicativa: 1 = intervallo nominale (risparmio energia),
        # 0 = full-alert (consumo massimo)
        comm_efficiency = min(1.0, tx_interval / self.TX_INTERVAL_NOMINAL)
        return ((1.0 - self.uncertainty_weight) * satisfaction
                + self.uncertainty_weight * comm_efficiency)

    # ──────────────────────────────────────────────────────────────────────────
    # Utility
    # ──────────────────────────────────────────────────────────────────────────

    def compute_action(self, state: dict, t: float = 0.0) -> dict:
        step = state.get("step", 0)
        return self.decide_action(step, t, [])

    def _cleanup_agent_controls(self, sim):
        for mgr in [sim._presolve_controls, sim._postsolve_controls,
                    sim._rules, sim._feasibility_controls]:
            to_remove = [
                c for c in mgr._controls
                if hasattr(c, '_name') and c._name and c._name.startswith("AgentCtrl_")
            ]
            for ctrl in to_remove:
                mgr.deregister(ctrl)
                try:
                    sim._change_tracker.deregister(ctrl)
                except Exception:
                    pass
        for name in [n for n in sim._wn.control_name_list if n.startswith("AgentCtrl_")]:
            sim._wn.remove_control(name)
