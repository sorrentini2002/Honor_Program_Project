"""
Modulo di Configurazione per CoSimulationEngine.
Tutti i parametri globali della co-simulazione sono centralizzati qui.
"""


from pathlib import Path

# ============================================================================
# CONFIGURAZIONE DEI PERCORSI
# ============================================================================
BASE_DIR = Path(__file__).resolve().parent.parent
NETWORK_FILE = BASE_DIR / "Network_INP" / "NET_30_Priority.inp"
LOG_DIR = BASE_DIR / "Log_review"          # Cartella centralizzata per tutti i log

# ============================================================================
# CONFIGURAZIONE TEMPORALE
# ============================================================================
DURATION_HOURS = 160         # Durata totale della simulazione in ore (~6.7 giorni)
STEP_MIN = 60                # Passo temporale in minuti (1 ora per step → 160 step totali)
CRISIS_START_HOUR = 48       # La crisi inizia all'ora 48 (fine del giorno 2)

# ============================================================================
# CONFIGURAZIONE RETE IDRICA / IDRAULICA
# ============================================================================
AVG_DEMAND = 0.003           # Domanda media ai nodi di giunzione (L/s)
DIST_TYPE = 'lognormal'      # Tipo di distribuzione: 'original', 'normal', 'lognormal', 'uniform'
PATTERN_MODE = 'random'      # Selezione dei pattern: 'random', 'sequential', 'single'
PRESERVE_DEMAND_PATTERNS = True  # True: preserva i pattern originali; False: randomizzazione stocastica
TARGET_HEAD = 50             # Carico idrico target del reservoir (m)
MIN_BOOST = 10               # Pressione di boost minima per le cisterne IoT (m)

# Parametri modello idraulico PDA
REQUIRED_PRESSURE = 35.0     # Pressione richiesta per soddisfazione piena (m) — PDA
MINIMUM_PRESSURE = 0.0       # Pressione minima al di sotto della quale erogazione = 0 (m)

# ============================================================================
# CONFIGURAZIONE CISTERNE IoT
# ============================================================================
N_TANKS = 5                  # Numero di cisterne IoT da dislocare nella rete
STRATEGY_NAME = 'random'     # Strategia di posizionamento: 'random', 'demand', 'pressure'
ENABLE_PUMPS = False         # Abilita/Disabilita le pompe per il controllo di ricarica
REMOVE_TANKS = True          # Rimuove le cisterne originali prima di aggiungere le nuove IoT

# ============================================================================
# CONFIGURAZIONE DELLA CRISI
# ============================================================================
CRISIS_MODE = 'pressure'     # Modalità della crisi: 'pressure', 'flow'
DECAY_TYPE = 'logarithmic'   # Tipo di decadimento idraulico
DECAY_RATE = 0.18

CRISIS_PARAMS = {
    'decay_rate': 0.20,
    'min_ratio': 0.05,
    'recovery_hour': 120,
    'recovery_duration_hours': 48.0,
    'recovery_type': 'gradual',
    'recovery_rate': 0.06,
}

# ============================================================================
# CONFIGURAZIONE COMUNICAZIONE LoRa / CYBER
# ============================================================================
LORA_MODE = 'simple'         # Modalità LoRa: 'simple', 'multihop'
N_GATEWAYS = 2
GATEWAY_MODE = 'kmeans'      # Posizionamento del gateway: 'center', 'random_offset'
GATEWAY_OFFSET = 0.0         # Distanza di offset dal centro per il gateway (metri)
SF_MODE = 'random'            # Assegnazione Spreading Factor: 'sequential', 'random', 'distance', 'fixed'
FIXED_SF = 12                # Valore di SF fisso se SF_MODE='fixed'

# ============================================================================
# CONFIGURAZIONE DELL'AGENTE
# ============================================================================
AGENT_NAME = 'priority'      # Algoritmo dell'agente: 'heuristic', 'random', 'priority'
AGENT_THRESHOLD = 0.8        # Soglia di tolleranza soddisfazione prima di intervenire
AGENT_AGGRESSION = 1.0       # Livello di aggressività dell'azione di controllo
AGENT_ALPHA = 0.8            # Parametro alpha per smoothing/apprendimento

# ============================================================================
# CONFIGURAZIONE VALVOLE INTELLIGENTI (Controllate dall'Agente)
# ============================================================================
# Lista degli ID dei tubi/valvole da strumentare come agent-controlled.
# Per NET30 le valvole sono già presenti nel file .inp come TCV (10147, 10193, 10203).
ISOLATION_PIPES = ['10147', '10193', '10203']

# ============================================================================
# CONFIGURAZIONE METRICHE E SOGLIE
# ============================================================================
# Soglia minima di domanda attesa per considerare valida la metrica di soddisfazione.
# Evita divisioni per zero su nodi con domanda trascurabile.
MIN_EXP_THRESHOLD = 1e-8
