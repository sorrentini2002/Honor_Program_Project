# -*- coding: utf-8 -*-
"""
Simulazione di Gestione Crisis Idrica con Integrazione Dominio Idrico e Sensoristico

Questo progetto unisce:
- Dyn-WNTR: per la simulazione della rete idrica
- LoRaSim: per la simulazione della rete di sensori LoRaWAN

Obiettivo: Gestire una crisi idrica attraverso serbatoi intelligenti controllati da un agente centrale.
"""

import sys
import os
import random
import math
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field

# ============================================================================
# CONFIGURAZIONE DEI PERCORSI E IMPORT DELLE REPOSITORY
# ============================================================================

def setup_environment():
    """Configura l'ambiente importando correttamente le due repository."""
    
    # Percorsi delle repository (adattare in base all'ambiente di esecuzione)
    dyn_wntr_path = os.path.join(os.path.dirname(__file__), 'Dyn-WNTR')
    lorasim_path = os.path.join(os.path.dirname(__file__), 'lorasim')
    
    # Aggiungi i percorsi al sys.path se esistono
    if os.path.exists(dyn_wntr_path) and dyn_wntr_path not in sys.path:
        sys.path.insert(0, dyn_wntr_path)
        print(f"✅ Aggiunto al path: {dyn_wntr_path}")
    
    if os.path.exists(lorasim_path) and lorasim_path not in sys.path:
        sys.path.append(lorasim_path)
        print(f"✅ Aggiunto al path: {lorasim_path}")
    
    # Importa mwntr (Dyn-WNTR)
    try:
        import mwntr
        print(f"✅ Dyn-WNTR importato con successo da: {mwntr.__file__}")
    except ImportError as e:
        print(f"❌ Errore nell'import di Dyn-WNTR: {e}")
        print("Assicurati di aver clonato la repository Dyn-WNTR nella cartella del progetto.")
        raise
    
    # Importa simpy (necessario per LoRaSim)
    try:
        import simpy
        print("✅ SimPy importato con successo")
    except ImportError:
        print("⚠️ SimPy non disponibile, installazione in corso...")
        os.system('pip install simpy')
        import simpy
    
    # Importa lorasim
    try:
        import lorasim
        print("✅ LoRaSim importato con successo")
    except ImportError:
        print("⚠️ LoRaSim non disponibile come modulo, useremo le classi definite localmente")
    
    return mwntr, simpy


# ============================================================================
# CLASSI PER LA SIMULAZIONE LORAWAN
# ============================================================================

@dataclass
class Packet:
    """Rappresenta un pacchetto LoRaWAN."""
    node_id: str
    data: float
    timestamp: float
    sf: int = 7


class Gateway:
    """Gateway LoRaWAN che riceve i pacchetti dai sensori."""
    
    def __init__(self, env: simpy.Environment):
        self.env = env
        self.received_packets: List[Packet] = []
        self.active_transmissions: List[Packet] = []
        self.stat_totale_inviati: int = 0
        self.stat_totale_persi: int = 0
        self.stat_totale_ricevuti: int = 0
    
    def receive_uplink(self, packet: Packet, time_on_air: float):
        """Simula la ricezione di un uplink con possibile collisione."""
        self.stat_totale_inviati += 1
        self.active_transmissions.append(packet)
        
        yield self.env.timeout(time_on_air)
        
        # Rileva collisioni: se più trasmissioni simultanee, tutti i pacchetti vanno persi
        collision = len(self.active_transmissions) > 1
        if not collision:
            self.received_packets.append(packet)
            self.stat_totale_ricevuti += 1
        else:
            self.stat_totale_persi += len(self.active_transmissions)
        
        self.active_transmissions.remove(packet)
    
    def get_packet_loss_rate(self) -> float:
        """Calcola la percentuale di pacchetti persi."""
        if self.stat_totale_inviati == 0:
            return 0.0
        return (self.stat_totale_persi / self.stat_totale_inviati) * 100.0
    
    def get_and_clear_buffer(self) -> List[Packet]:
        """Restituisce e pulisce il buffer dei pacchetti ricevuti."""
        data = self.received_packets.copy()
        self.received_packets.clear()
        return data


class SensorNode:
    """Nodo sensore che misura pressione e invia dati via LoRaWAN."""
    
    def __init__(self, env: simpy.Environment, node_id: str, gateway: Gateway, 
                 sf: int = 7, tx_interval: int = 3600):
        self.env = env
        self.node_id = node_id
        self.gateway = gateway
        self.sf = sf
        self.tx_interval = tx_interval
        self.current_pressure: float = 0.0
        self.current_tank_level: float = 0.0
        # Time on air dipende dallo Spreading Factor
        self.time_on_air = (2 ** self.sf) / 125000.0 * 20 * 1000  # in ms
        
        # Avvia il processo di trasmissione
        self.env.process(self.transmit_loop())
    
    def update_sensor_data(self, pressure: float, tank_level: float = 0.0):
        """Aggiorna i dati del sensore."""
        self.current_pressure = pressure
        self.current_tank_level = tank_level
    
    def update_tx_interval(self, new_interval: int):
        """Aggiorna l'intervallo di trasmissione."""
        self.tx_interval = new_interval
    
    def transmit_loop(self):
        """Ciclo di trasmissione periodica dei dati."""
        # Ritardo iniziale casuale per evitare sincronizzazione
        yield self.env.timeout(random.uniform(0, min(self.tx_interval, 100)))
        
        while True:
            packet = Packet(
                node_id=self.node_id,
                data=self.current_pressure,
                timestamp=self.env.now,
                sf=self.sf
            )
            
            # Invia il pacchetto attraverso il gateway
            self.env.process(self.gateway.receive_uplink(packet, self.time_on_air))
            
            # Attendi il prossimo intervallo di trasmissione
            yield self.env.timeout(self.tx_interval)


class LoRaWAN_Network:
    """Rete LoRaWAN che gestisce tutti i nodi sensore."""
    
    def __init__(self, node_ids: List[str], tx_interval: int = 3600):
        self.env = simpy.Environment()
        self.gateway = Gateway(self.env)
        self.nodes: Dict[str, SensorNode] = {}
        
        print("\n📡 INIZIALIZZAZIONE RETE LoRaWAN...")
        for node_id in node_ids:
            # Assegna uno Spreading Factor casuale (7-12)
            sf = random.choice([7, 8, 9, 10, 11, 12])
            node = SensorNode(self.env, node_id, self.gateway, sf, tx_interval)
            self.nodes[node_id] = node
            print(f"   [+] Sensore registrato: {node_id} (SF: {sf})")
    
    def run_communication_step(self, time_step: float) -> List[Packet]:
        """Esegue un passo di simulazione della comunicazione."""
        target_time = self.env.now + time_step
        self.env.run(until=target_time)
        return self.gateway.get_and_clear_buffer()
    
    def update_node_tx_interval(self, node_id: str, new_interval: int):
        """Aggiorna l'intervallo di trasmissione di un nodo specifico."""
        if node_id in self.nodes:
            self.nodes[node_id].update_tx_interval(new_interval)
    
    def get_packet_loss_rate(self) -> float:
        """Restituisce la percentuale attuale di pacchetti persi."""
        return self.gateway.get_packet_loss_rate()


# ============================================================================
# CLASSI PER LA GESTIONE DELLA RETE IDRICA
# ============================================================================

@dataclass
class TankConfig:
    """Configurazione per un serbatoio."""
    size_type: str
    tank_diameter: float
    pipe_diameter: float
    min_level: float = 0.0
    max_level: float = 12.0
    init_level: float = 10.0  # Parte quasi pieno


# Configurazioni predefinite per i tre tipi di serbatoi
TANK_CONFIGS = {
    'Small':  TankConfig('Small',  5.0,  0.15, 0.0, 8.0, 6.0),
    'Medium': TankConfig('Medium', 15.0, 0.30, 0.0, 12.0, 10.0),
    'Large':  TankConfig('Large',  40.0, 0.60, 0.0, 15.0, 12.0)
}


class WaterNetworkManager:
    """Gestisce la rete idrica, inclusi serbatoi, sensori e attuatori."""
    
    def __init__(self, network_file: str):
        """Inizializza il gestore della rete idrica."""
        import mwntr
        self.mwntr = mwntr
        
        # Carica il modello della rete
        self.wn = mwntr.network.WaterNetworkModel(network_file)
        print(f"✅ Rete caricata: {network_file}")
        print(f"   Nodi: {self.wn.num_nodes}, Archi: {self.wn.num_links}")
        
        # Traccia i serbatoi IoT aggiunti
        self.iot_tanks: Dict[str, dict] = {}
        self.iot_valves: List[str] = []
    
    def remove_existing_tanks(self):
        """Rimuove tutti i serbatoi esistenti nella rete."""
        original_tanks = list(self.wn.tank_name_list)
        print(f"\n🗑️  Rimozione di {len(original_tanks)} serbatoi esistenti...")
        
        for tank_name in original_tanks:
            # Rimuovi prima i collegamenti al serbatoio
            links_to_remove = list(self.wn.get_links_for_node(tank_name))
            for link_name in links_to_remove:
                self.wn.remove_link(link_name)
            
            # Rimuovi il serbatoio
            self.wn.remove_node(tank_name)
        
        print(f"   ✅ {len(original_tanks)} serbatoi rimossi")
    
    def add_iot_tanks(self, n_tanks: int = 8) -> List[str]:
        """
        Aggiunge serbatoi IoT in posizioni casuali nella rete.
        
        Args:
            n_tanks: Numero di serbatoi da aggiungere
        
        Returns:
            Lista dei nomi delle valvole IoT create
        """
        junctions = self.wn.junction_name_list
        
        if n_tanks > len(junctions):
            print(f"⚠️  Richiesti {n_tanks} serbatoi ma solo {len(junctions)} junction disponibili")
            n_tanks = len(junctions)
        
        # Seleziona nodi casuali per posizionare i serbatoi
        target_nodes = random.sample(junctions, n_tanks)
        tank_types = list(TANK_CONFIGS.keys())
        
        print(f"\n💧 Aggiunta di {n_tanks} serbatoi IoT...")
        
        for i, junc_name in enumerate(target_nodes):
            junc_node = self.wn.get_node(junc_name)
            
            # Scegli un tipo di serbatoio casualmente
            tank_type = random.choice(tank_types)
            config = TANK_CONFIGS[tank_type]
            
            # Calcola posizione e altezza
            offset_height = random.uniform(25, 50)  # Altezza rispetto al nodo
            tank_name = f"IoT_Tank_{tank_type}_{i+1}"
            valve_name = f"IoT_Valve_{i+1}"
            
            # Aggiungi il serbatoio
            self.wn.add_tank(
                name=tank_name,
                elevation=junc_node.elevation + offset_height,
                init_level=config.init_level,
                min_level=config.min_level,
                max_level=config.max_level,
                diameter=config.tank_diameter,
                coordinates=(junc_node.coordinates[0] + 200, 
                           junc_node.coordinates[1] + 200)
            )
            
            # Aggiungi la valvola/pipa di collegamento
            self.wn.add_pipe(
                name=valve_name,
                start_node_name=junc_name,
                end_node_name=tank_name,
                length=50.0,
                diameter=config.pipe_diameter,
                roughness=120,
                initial_status=self.mwntr.network.LinkStatus.Closed  # Inizialmente chiusa
            )
            
            # Registra il serbatoio IoT
            self.iot_tanks[tank_name] = {
                'type': tank_type,
                'junction': junc_name,
                'valve': valve_name,
                'config': config
            }
            self.iot_valves.append(valve_name)
            
            print(f"   ✅ {tank_name} ({tank_type}) collegato a {junc_name} tramite {valve_name}")
        
        return self.iot_valves
    
    def configure_source_pattern(self, pattern_name: str = 'Fonte_Pattern', 
                                  pattern_values: List[float] = None):
        """Configura un pattern per la fonte primaria."""
        if pattern_values is None:
            # Pattern di default: calo progressivo della portata
            pattern_values = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 
                            0.8, 0.6, 0.4, 0.2, 0.1, 0.1, 0.1, 0.1]
        
        self.wn.add_pattern(pattern_name, pattern_values)
        
        # Applica il pattern alla fonte primaria (reservoir)
        reservoirs = list(self.wn.reservoir_name_list)
        if reservoirs:
            primary_source = reservoirs[0]
            source_node = self.wn.get_node(primary_source)
            source_node.head_pattern_name = pattern_name
            print(f"✅ Pattern '{pattern_name}' applicato alla fonte: {primary_source}")
        else:
            print("⚠️  Nessun reservoir trovato nella rete")
    
    def set_simulation_options(self, duration_hours: int = 24, 
                               hydraulic_timestep: int = 300,
                               report_timestep: int = 300):
        """Configura le opzioni di simulazione."""
        self.wn.options.time.duration = duration_hours * 3600
        self.wn.options.time.hydraulic_timestep = hydraulic_timestep
        self.wn.options.time.report_timestep = report_timestep
        
        # Configura il modello di domanda (PDA = Pressure Driven Analysis)
        self.wn.options.hydraulic.demand_model = 'PDA'
        self.wn.options.hydraulic.minimum_pressure = 0.0
        self.wn.options.hydraulic.required_pressure = 20.0
        
        print(f"✅ Simulazione configurata: {duration_hours}h, timestep={hydraulic_timestep}s")
    
    def get_junction_pressure(self, junction_name: str) -> float:
        """Ottiene la pressione attuale in un nodo."""
        try:
            junc = self.wn.get_node(junction_name)
            if hasattr(junc, 'head') and hasattr(junc, 'elevation'):
                return junc.head - junc.elevation
        except Exception:
            pass
        return 0.0
    
    def get_tank_level(self, tank_name: str) -> float:
        """Ottiene il livello attuale di un serbatoio."""
        try:
            tank = self.wn.get_node(tank_name)
            if hasattr(tank, 'level'):
                return tank.level
        except Exception:
            pass
        return 0.0
    
    def set_valve_status(self, valve_name: str, open: bool):
        """Apre o chiude una valvola."""
        try:
            valve = self.wn.get_link(valve_name)
            if open:
                valve.status = self.mwntr.network.LinkStatus.Opened
            else:
                valve.status = self.mwntr.network.LinkStatus.Closed
        except Exception as e:
            print(f"⚠️  Errore nel settaggio della valvola {valve_name}: {e}")
    
    def calculate_demand_satisfaction(self) -> float:
        """
        Calcola il livello di soddisfazione della domanda nella rete.
        
        Returns:
            Percentuale di domanda soddisfatta (0-100)
        """
        total_demand = 0.0
        total_flow = 0.0
        
        for junc_name in self.wn.junction_name_list:
            try:
                junc = self.wn.get_node(junc_name)
                if hasattr(junc, 'demand'):
                    total_demand += abs(junc.demand)
                if hasattr(junc, 'flow'):
                    total_flow += abs(junc.flow)
            except Exception:
                continue
        
        if total_demand == 0:
            return 100.0
        
        satisfaction = (total_flow / total_demand) * 100.0
        return min(100.0, max(0.0, satisfaction))


# ============================================================================
# AGENTE DI GESTIONE CRISI IDRICA
# ============================================================================

@dataclass
class AgentState:
    """Stato interno dell'agente di gestione crisi."""
    valve_states: Dict[str, str] = field(default_factory=dict)  # OPEN/CLOSED
    last_pressures: Dict[str, float] = field(default_factory=dict)
    last_tx_intervals: Dict[str, int] = field(default_factory=dict)
    last_satisfaction: float = 100.0
    action_timestamps: Dict[str, float] = field(default_factory=dict)


class WaterCrisisAgent:
    """
    Agente centrale per la gestione della crisi idrica.
    
    L'agente:
    1. Riceve dati dai sensori via LoRaWAN
    2. Valuta lo stato della rete
    3. Decide se aprire/chiudere i serbatoi
    4. Ottimizza la frequenza di invio dei sensori
    
    La funzione obiettivo considera:
    - Differenza di soddisfazione della domanda prima/dopo l'azione
    - Tempo impiegato per realizzare l'azione
    - Percentuale di pacchetti persi nella rete LoRaWAN
    """
    
    def __init__(self, valve_ids: List[str], 
                 critical_pressure: float = 15.0,
                 recovery_pressure: float = 25.0):
        """
        Inizializza l'agente.
        
        Args:
            valve_ids: Lista degli ID delle valvole controllabili
            critical_pressure: Soglia di pressione critica (sotto cui aprire i serbatoi)
            recovery_pressure: Soglia di pressione di recupero (sopra cui chiudere i serbatoi)
        """
        self.valve_ids = valve_ids
        self.critical_pressure = critical_pressure
        self.recovery_pressure = recovery_pressure
        
        # Stato iniziale
        self.state = AgentState()
        for valve_id in valve_ids:
            self.state.valve_states[valve_id] = 'CLOSED'
            self.state.last_pressures[valve_id] = 50.0
            self.state.last_tx_intervals[valve_id] = 3600  # 1 ora
            self.state.action_timestamps[valve_id] = 0.0
        
        # Statistiche
        self.actions_history: List[dict] = []
        self.objective_values: List[float] = []
    
    def process_uplink(self, packets: List[Packet]):
        """
        Elabora i pacchetti ricevuti dai sensori.
        
        Args:
            packets: Lista di pacchetti ricevuti dal gateway LoRaWAN
        """
        for packet in packets:
            valve_id = packet.node_id
            if valve_id in self.state.valve_states:
                self.state.last_pressures[valve_id] = packet.data
                self.state.action_timestamps[valve_id] = packet.timestamp
    
    def calculate_objective_function(self, satisfaction_before: float, 
                                     satisfaction_after: float,
                                     action_time: float,
                                     packet_loss_rate: float,
                                     current_time: float) -> float:
        """
        Calcola la funzione obiettivo per valutare un'azione.
        
        La funzione obiettivo è composta da:
        1. Guadagno in soddisfazione: (satisfaction_after - satisfaction_before)
        2. Penalità temporale: -alpha * action_time
        3. Penalità per pacchetti persi: -beta * packet_loss_rate
        
        Args:
            satisfaction_before: Soddisfazione prima dell'azione
            satisfaction_after: Soddisfazione dopo l'azione
            action_time: Tempo impiegato per l'azione (secondi)
            packet_loss_rate: Percentuale di pacchetti persi (0-100)
            current_time: Tempo corrente della simulazione
        
        Returns:
            Valore della funzione obiettivo (più alto = migliore)
        """
        # Pesi per i diversi componenti
        alpha = 0.1  # Peso penalità temporale
        beta = 0.5   # Peso penalità pacchetti persi
        
        # Componente 1: Miglioramento nella soddisfazione
        satisfaction_gain = satisfaction_after - satisfaction_before
        
        # Componente 2: Penalità per il tempo impiegato
        # Azioni più rapide sono preferibili
        time_penalty = alpha * action_time
        
        # Componente 3: Penalità per i pacchetti persi
        # Molti pacchetti persi indicano problemi di comunicazione
        communication_penalty = beta * (packet_loss_rate / 100.0) * 10
        
        # Funzione obiettivo complessiva
        objective_value = satisfaction_gain - time_penalty - communication_penalty
        
        return objective_value
    
    def calculate_optimal_tx_interval(self, pressure: float, 
                                      packet_loss_rate: float) -> int:
        """
        Calcola dinamicamente l'intervallo di trasmissione ottimale per un sensore.
        
        Strategia:
        - Pressione alta + pochi pacchetti persi → intervallo lungo (risparmio energetico)
        - Pressione media → intervallo medio
        - Pressione bassa (critica) → intervallo breve (monitoraggio intensivo)
        - Molti pacchetti persi → aumenta intervallo per ridurre congestione
        
        Args:
            pressure: Pressione corrente letta dal sensore
            packet_loss_rate: Percentuale di pacchetti persi nella rete
        
        Returns:
            Intervallo di trasmissione in secondi
        """
        # Intervallo base basato sulla pressione
        if pressure > self.recovery_pressure + 10:  # > 35 m
            base_interval = 3600  # 1 ora
        elif pressure > self.recovery_pressure:  # > 25 m
            base_interval = 600  # 10 minuti
        elif pressure > self.critical_pressure:  # > 15 m
            base_interval = 300  # 5 minuti
        else:  # Situazione critica
            base_interval = 60  # 1 minuto
        
        # Aggiustamento basato sui pacchetti persi
        if packet_loss_rate > 30:  # Alta perdita
            base_interval = min(base_interval * 2, 7200)  # Raddoppia, max 2 ore
        elif packet_loss_rate > 10:  # Media perdita
            base_interval = int(base_interval * 1.5)
        
        return base_interval
    
    def decide_actions(self, current_time: float, 
                      current_satisfaction: float,
                      packet_loss_rate: float) -> Dict[str, dict]:
        """
        Prende decisioni sulle azioni da intraprendere.
        
        Args:
            current_time: Tempo corrente della simulazione
            current_satisfaction: Livello attuale di soddisfazione della domanda
            packet_loss_rate: Percentuale di pacchetti persi
        
        Returns:
            Dizionario delle azioni per ogni valvola
        """
        actions = {}
        
        for valve_id in self.valve_ids:
            pressure = self.state.last_pressures.get(valve_id, 50.0)
            current_state = self.state.valve_states[valve_id]
            current_tx = self.state.last_tx_intervals[valve_id]
            
            actions[valve_id] = {
                'valve_action': None,  # OPEN, CLOSE, o None
                'new_tx_interval': None,
                'reason': '',
                'objective_value': 0.0
            }
            
            # 1. Decisione sull'intervallo di trasmissione
            optimal_tx = self.calculate_optimal_tx_interval(pressure, packet_loss_rate)
            
            if optimal_tx != current_tx:
                actions[valve_id]['new_tx_interval'] = optimal_tx
                self.state.last_tx_intervals[valve_id] = optimal_tx
                actions[valve_id]['reason'] += f"TX update: {current_tx}s → {optimal_tx}s. "
            
            # 2. Decisione sullo stato della valvola
            satisfaction_before = current_satisfaction
            
            # Stima la soddisfazione dopo l'azione (euristica)
            if pressure < self.critical_pressure and current_state == 'CLOSED':
                # Situazione critica: apri il serbatoio
                estimated_satisfaction_after = min(100.0, satisfaction_before + 15.0)
                action_time = 60.0  # Stima del tempo di apertura
                
                obj_value = self.calculate_objective_function(
                    satisfaction_before, estimated_satisfaction_after,
                    action_time, packet_loss_rate, current_time
                )
                
                actions[valve_id]['valve_action'] = 'OPEN'
                actions[valve_id]['objective_value'] = obj_value
                actions[valve_id]['reason'] += f"Critical pressure ({pressure:.1f}m). Obj={obj_value:.2f}"
                
            elif pressure > self.recovery_pressure and current_state == 'OPEN':
                # Situazione recuperata: chiudi il serbatoio per risparmiare
                estimated_satisfaction_after = max(0.0, satisfaction_before - 5.0)
                action_time = 30.0  # Stima del tempo di chiusura
                
                obj_value = self.calculate_objective_function(
                    satisfaction_before, estimated_satisfaction_after,
                    action_time, packet_loss_rate, current_time
                )
                
                # Chiudi solo se la soddisfazione rimane accettabile
                if estimated_satisfaction_after >= 80.0 or obj_value > -5.0:
                    actions[valve_id]['valve_action'] = 'CLOSE'
                    actions[valve_id]['objective_value'] = obj_value
                    actions[valve_id]['reason'] += f"Pressure recovered ({pressure:.1f}m). Obj={obj_value:.2f}"
        
        return actions
    
    def apply_actions(self, water_network: WaterNetworkManager, 
                     lora_network: LoRaWAN_Network,
                     actions: Dict[str, dict], current_time: float):
        """
        Applica le decisioni prese alle valvole e ai sensori.
        
        Args:
            water_network: Gestore della rete idrica
            lora_network: Rete LoRaWAN
            actions: Dizionario delle azioni da applicare
            current_time: Tempo corrente
        """
        for valve_id, action in actions.items():
            # Applica azione sulla valvola
            if action['valve_action'] == 'OPEN':
                water_network.set_valve_status(valve_id, open=True)
                self.state.valve_states[valve_id] = 'OPEN'
                self.state.action_timestamps[valve_id] = current_time
                print(f"🔧 [{current_time:.0f}s] {valve_id}: APERTA - {action['reason']}")
            
            elif action['valve_action'] == 'CLOSE':
                water_network.set_valve_status(valve_id, open=False)
                self.state.valve_states[valve_id] = 'CLOSED'
                self.state.action_timestamps[valve_id] = current_time
                print(f"🔧 [{current_time:.0f}s] {valve_id}: CHIUSA - {action['reason']}")
            
            # Aggiorna intervallo di trasmissione
            if action['new_tx_interval'] is not None:
                lora_network.update_node_tx_interval(valve_id, action['new_tx_interval'])
                print(f"📡 [{current_time:.0f}s] {valve_id}: TX interval → {action['new_tx_interval']}s")
            
            # Registra l'azione
            if action['valve_action'] or action['new_tx_interval']:
                self.actions_history.append({
                    'time': current_time,
                    'valve_id': valve_id,
                    'valve_action': action['valve_action'],
                    'new_tx_interval': action['new_tx_interval'],
                    'objective_value': action['objective_value'],
                    'reason': action['reason']
                })
    
    def get_statistics(self) -> dict:
        """Restituisce statistiche sulle azioni dell'agente."""
        open_count = sum(1 for a in self.actions_history if a['valve_action'] == 'OPEN')
        close_count = sum(1 for a in self.actions_history if a['valve_action'] == 'CLOSE')
        tx_updates = sum(1 for a in self.actions_history if a['new_tx_interval'] is not None)
        
        avg_objective = 0.0
        if self.actions_history:
            obj_values = [a['objective_value'] for a in self.actions_history if a['objective_value'] != 0.0]
            if obj_values:
                avg_objective = sum(obj_values) / len(obj_values)
        
        return {
            'total_actions': len(self.actions_history),
            'open_actions': open_count,
            'close_actions': close_count,
            'tx_updates': tx_updates,
            'average_objective_value': avg_objective
        }


# ============================================================================
# SIMULATORE PRINCIPALE
# ============================================================================

class WaterCrisisSimulator:
    """
    Simulatore principale che coordina la co-simulazione
    del dominio idrico e sensoristico.
    """
    
    def __init__(self, network_file: str, n_tanks: int = 8):
        """
        Inizializza il simulatore.
        
        Args:
            network_file: Percorso del file .inp della rete idrica
            n_tanks: Numero di serbatoi IoT da aggiungere
        """
        print("=" * 70)
        print("SIMULATORE DI GESTIONE CRISI IDRICA")
        print("Integrazione Dominio Idrico + Dominio Sensoristico")
        print("=" * 70)
        
        # Setup dell'ambiente
        self.mwntr, self.simpy = setup_environment()
        
        # Inizializza il gestore della rete idrica
        self.water_network = WaterNetworkManager(network_file)
        
        # Rimuovi serbatoi esistenti e aggiungi quelli IoT
        self.water_network.remove_existing_tanks()
        self.valve_ids = self.water_network.add_iot_tanks(n_tanks)
        
        # Configura la fonte primaria
        self.water_network.configure_source_pattern()
        
        # Configura la simulazione
        self.water_network.set_simulation_options(
            duration_hours=24,
            hydraulic_timestep=300,
            report_timestep=300
        )
        
        # Inizializza la rete LoRaWAN
        self.lora_network = LoRaWAN_Network(self.valve_ids, tx_interval=3600)
        
        # Inizializza l'agente di gestione crisi
        self.agent = WaterCrisisAgent(
            self.valve_ids,
            critical_pressure=15.0,
            recovery_pressure=25.0
        )
        
        # Crea il simulatore idraulico
        self.simulator = self.mwntr.sim.WNTRSimulator(self.water_network.wn)
        
        # Statistiche della simulazione
        self.simulation_stats = {
            'timesteps': [],
            'satisfaction_levels': [],
            'packet_loss_rates': [],
            'valve_states_history': []
        }
    
    def run(self):
        """Esegue la co-simulazione completa."""
        print("\n" + "=" * 70)
        print("AVVIO CO-SIMULAZIONE CYBER-PHYSICAL SYSTEM")
        print("=" * 70)
        
        time_step = self.water_network.wn.options.time.hydraulic_timestep
        
        # Loop principale di simulazione
        step_count = 0
        for current_time in self.simulator:
            step_count += 1
            
            # 1. Lettura dati dai sensori (dominio fisico → cyber)
            packets_data = []
            for valve_id in self.valve_ids:
                try:
                    # Trova il serbatoio associato a questa valvola
                    tank_info = None
                    for tank_name, info in self.water_network.iot_tanks.items():
                        if info['valve'] == valve_id:
                            tank_info = info
                            break
                    
                    if tank_info:
                        junction_name = tank_info['junction']
                        tank_name = tank_info['tank'] if 'tank' in tank_info else tank_name
                        
                        # Leggi pressione dal nodo di giunzione
                        pressure = self.water_network.get_junction_pressure(junction_name)
                        
                        # Leggi livello del serbatoio
                        tank_level = self.water_network.get_tank_level(tank_name)
                        
                        # Aggiorna il nodo sensore corrispondente
                        if valve_id in self.lora_network.nodes:
                            self.lora_network.nodes[valve_id].update_sensor_data(
                                pressure, tank_level
                            )
                            
                            packets_data.append({
                                'valve_id': valve_id,
                                'pressure': pressure,
                                'tank_level': tank_level
                            })
                except Exception as e:
                    print(f"⚠️  Errore nella lettura del sensore {valve_id}: {e}")
            
            # 2. Simulazione comunicazioni LoRaWAN
            received_packets = self.lora_network.run_communication_step(time_step)
            packet_loss_rate = self.lora_network.get_packet_loss_rate()
            
            # 3. Elaborazione dati da parte dell'agente
            self.agent.process_uplink(received_packets)
            
            # Calcola soddisfazione corrente della domanda
            current_satisfaction = self.water_network.calculate_demand_satisfaction()
            
            # 4. Decisioni dell'agente
            actions = self.agent.decide_actions(
                current_time, 
                current_satisfaction,
                packet_loss_rate
            )
            
            # 5. Applicazione delle azioni (dominio cyber → fisico)
            self.agent.apply_actions(
                self.water_network,
                self.lora_network,
                actions,
                current_time
            )
            
            # 6. Registra statistiche
            self.simulation_stats['timesteps'].append(current_time)
            self.simulation_stats['satisfaction_levels'].append(current_satisfaction)
            self.simulation_stats['packet_loss_rates'].append(packet_loss_rate)
            self.simulation_stats['valve_states_history'].append(
                dict(self.agent.state.valve_states)
            )
            
            # Output periodico dello stato
            if step_count % 10 == 0 or step_count == 1:
                open_valves = sum(1 for s in self.agent.state.valve_states.values() if s == 'OPEN')
                print(f"\n📊 [{current_time:.0f}s] Step {step_count}: "
                      f"Soddisfazione={current_satisfaction:.1f}%, "
                      f"Serbatoi aperti={open_valves}/{len(self.valve_ids)}, "
                      f"Pacchetti persi={packet_loss_rate:.1f}%")
        
        # Fine simulazione
        print("\n" + "=" * 70)
        print("CO-SIMULAZIONE COMPLETATA")
        print("=" * 70)
        
        # Stampa statistiche finali
        self._print_final_statistics()
    
    def _print_final_statistics(self):
        """Stampa le statistiche finali della simulazione."""
        stats = self.agent.get_statistics()
        
        print("\n📈 STATISTICHE DELL'AGENTE:")
        print(f"   Totale azioni: {stats['total_actions']}")
        print(f"   Aperture valvole: {stats['open_actions']}")
        print(f"   Chiusure valvole: {stats['close_actions']}")
        print(f"   Aggiornamenti TX: {stats['tx_updates']}")
        print(f"   Valore medio funzione obiettivo: {stats['average_objective_value']:.2f}")
        
        print("\n📊 STATISTICHE DI SIMULAZIONE:")
        if self.simulation_stats['satisfaction_levels']:
            avg_satisfaction = sum(self.simulation_stats['satisfaction_levels']) / len(self.simulation_stats['satisfaction_levels'])
            min_satisfaction = min(self.simulation_stats['satisfaction_levels'])
            max_satisfaction = max(self.simulation_stats['satisfaction_levels'])
            print(f"   Soddisfazione media: {avg_satisfaction:.1f}%")
            print(f"   Soddisfazione minima: {min_satisfaction:.1f}%")
            print(f"   Soddisfazione massima: {max_satisfaction:.1f}%")
        
        if self.simulation_stats['packet_loss_rates']:
            avg_packet_loss = sum(self.simulation_stats['packet_loss_rates']) / len(self.simulation_stats['packet_loss_rates'])
            print(f"   Perdita media pacchetti: {avg_packet_loss:.1f}%")
        
        print("\n✅ Simulazione terminata con successo!")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    # Percorso del file della rete Net4
    # Nota: assicurati che il file NET_4.inp sia presente nella directory del progetto
    # o nella sottocartella Dyn-WNTR
    
    network_file = 'NET_4.inp'
    
    # Cerca il file in percorsi comuni
    possible_paths = [
        network_file,
        os.path.join('Dyn-WNTR', network_file),
        os.path.join(os.path.dirname(__file__), 'Dyn-WNTR', network_file),
        os.path.join(os.path.dirname(__file__), network_file)
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            network_file = path
            break
    
    print(f"🔍 File rete idrica: {network_file}")
    
    if not os.path.exists(network_file):
        print(f"❌ Errore: File {network_file} non trovato!")
        print("Assicurati di aver clonato la repository Dyn-WNTR e che NET_4.inp sia presente.")
        print("\nPer clonare le repository necessarie:")
        print("  git clone https://github.com/rastafaninplakeibol/Dyn-WNTR.git")
        print("  git clone https://github.com/mcbor/lorasim.git")
        sys.exit(1)
    
    # Crea e esegui il simulatore
    simulator = WaterCrisisSimulator(
        network_file=network_file,
        n_tanks=8  # Numero di serbatoi IoT da aggiungere
    )
    
    simulator.run()
print(f"📊 Packet Loss Rate Finale: {lora_net.gateway.get_packet_loss_rate():.2f}%")