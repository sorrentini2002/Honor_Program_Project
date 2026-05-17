# Water Crisis Management Co-Simulation Framework

Un framework avanzato per la modellazione e gestione intelligente di crisi idriche in reti di distribuzione mediante cyber-fisica, IoT e intelligenza artificiale.

## 🎯 Obiettivo

Sviluppare e testare strategie di mitigazione attive per reti idriche sotto stress, sfruttando:
- **Idraulica Realistica**: Simulazione fisico-matematica con WNTR/EPANET (Pressure-Driven Analysis)
- **Hardware IoT**: Serbatoi smart controllabili da remoto, valvole e pompe automatizzate
- **Comunicazione Wireless**: Protocollo LoRaWAN con modello di propagazione fisico-realistico (path loss, shadowing, collisioni)
- **Agenti Intelligenti**: Controllori PI e algoritmi di Reinforcement Learning per decisioni ottimali in tempo reale

## 🚀 Caratteristiche Principali

### Modellazione delle Crisi
- **Modelli Deterministici**: Linear, Exponential, Instant, Logarithmic
- **Modelli Stocastici**: Ornstein-Uhlenbeck (Mean-Reverting Process)
- **Scenari Complessi**: PumpTestCrisis con fasi di degradazione e recupero programmato

### Intelligenza Artificiale
- **Controllore PI**: Heuristic Agent con bilanciamento multi-obiettivo (acqua vs. batteria radio)
- **Architettura Estensibile**: Framework per agenti RL/MPC futuri
- **Adattività Dinamica**: Modula telemetria, frequenza di comunicazione, strategia di rilascio serbatoi

### Comunicazione Radio
- **Modello Fisico Completo**: Log-distance path loss, log-normal shadowing, SNR validation
- **Gestione Collisioni Avanzata**: Rilevamento cross-timestep, capture effect, ortogonalità SF
- **EU868 Standard**: 8 canali, SF 7-12, bandwidth 125/250/500 kHz

### Infrastruttura Idrica
- **PDA (Pressure-Driven Analysis)**: Perdite realistiche basate sulla pressione
- **Strategie di Posizionamento**: Random, Demand-based, Pressure-based tank placement
- **Controlli Dinamici**: Valvole TCV e pompe controllate dall'agente step-by-step

## 📂 Struttura del Progetto

```
Honor_Program_Project/
├── main.py                          # Orchestratore: LoRaSystem, WaterNetworkManager, CoSimulationEngine
├── Agents/
│   ├── base_agent.py               # Superclasse astratta
│   └── heuristic_agent.py          # Controllore PI implementato
├── Crises/
│   ├── base_crisis.py              # Classe base con decay_rate
│   ├── deterministic_crises.py     # Linear, Exponential, Instant, Logarithmic
│   ├── Ornstein_Uhlenbeck.py       # Modello stocastico mean-reverting
│   └── test_crises.py              # PumpTestCrisis con recovery
├── Strategies/
│   ├── base_strategy.py            # Interfaccia di posizionamento serbatoi
│   ├── demand_strategy.py          # Tank deployment near high-demand nodes
│   ├── pressure_strategy.py        # Tank deployment in weak-pressure zones
│   └── random_strategy.py          # Random placement
├── Dyn-WNTR/                       # Libreria WNTR personalizzata con PDA
│   └── mwntr/
│       ├── network/                # Modellazione idraulica
│       ├── sim/                    # Simulatore interattivo step-by-step
│       ├── metrics/                # Calcolo indici di performance
│       └── epanet/                 # Binding EPANET C
├── Network/
│   ├── NET_30_users_only.inp       # Rete di 30 utenti (scenario principale)
│   ├── NET_4.inp                   # Rete mini per test rapidi
│   └── patterns.json               # Pattern di consumo orari
├── Log_review/                     # Output simulazioni
│   ├── latest_simulation_log.txt
│   ├── crisis_status.txt
│   ├── agent_performance.txt
│   └── *.png                       # Grafici di analisi
├── documents/                      # Documentazione
│   ├── code_description.md         # Descrizione tecnica dettagliata
│   ├── simulation_report.md        # Report dello scenario testato
│   └── images/                     # Grafici e visualizzazioni
└── readme.md                       # Questo file
```

## ⚡ Quick Start

### Installazione
```bash
cd Dyn-WNTR
pip install -r requirements.txt
cd ..
```

### Esecuzione Simulazione Base
```python
from main import CoSimulationEngine

engine = CoSimulationEngine(
    network_file='Network/NET_30_users_only.inp',
    duration_hours=60,
    step_min=5,
    crisis_mode='pressure',
    decay_type='pump_test',
    n_tanks=5,
    agent_name='heuristic'
)

results = engine.run_simulation()
```

### Output
Grafici e log salvati in `Log_review/`:
- `simulation_analysis.png`: Trend di satisfaction, tank activation, packet loss
- `tank_levels_trend.png`: Livelli serbatoi nel tempo
- `agent_performance.txt`: Decisioni dell'agente
- `latest_simulation_log.txt`: Telemetria radio

## 📊 Scenario di Test

Una simulazione completa di 60 ore modella:

1. **Pre-Crisis (0-1.5h)**: Rete operativa al 100%
2. **Crisis Onset (1.5-5h)**: Degradazione lineare della sorgente (-2% all'ora)
3. **Peak Crisis (5-17h)**: Rete al 2.5% di capacità, agente attiva serbatoi per mantenere >80% satisfaction
4. **Recovery (17-22h)**: Recupero istantaneo della capacità
5. **Post-Crisis (22-60h)**: Normalità, ricarica progressiva serbatoi

**Risultati**:
- ✅ Demand satisfaction mantenuta >82% anche in crisi severa
- ✅ Emergency tanks attivati in cascata (max 2 contemporaneamente)
- ✅ Packet loss controllato: 0.1% normale, 25-30% in crisi
- ✅ Transizione smooth al recovery senza discontinuità

## 🔧 Configurazione Avanzata

Tutti i parametri sono personalizzabili per esplorare diversi scenari:

### Crisi
- `decay_type`: Tipo di degradazione (linear, exponential, instant, logarithmic, ornstein_uhlenbeck, pump_test)
- `crisis_params`: Parametri specifici (min_ratio, volatility, recovery_hour, etc.)
- `crisis_start_hour`: Quando inizia la crisi

### Infrastruttura
- `n_tanks`: Numero serbatoi di emergenza
- `strategy_name`: Strategia posizionamento (random, demand, pressure)
- `enable_pumps`: Attiva pompe per ricircolo
- `min_boost`: Elevazione minima serbatoi

### Agente
- `agent_threshold`: Soglia di intervento (default 95%)
- `agent_aggression`: Reattività (1-10, default 3)
- `agent_alpha`: Bilancia acqua vs. batteria radio (0-1, default 0.5)

### Radio
- `sf_mode`: Spreading Factor (fixed, sequential, random, distance)
- `fixed_sf`: Valore SF se fisso (7-12)
- `gateway_mode`: Posizionamento gateway (center, random_offset, random)

## 📈 Metriche di Performance

| Metrica | Descrizione | Valori Tipici |
|---------|-------------|----------------|
| **Demand Satisfaction** | % di domanda idrica erogata | 82-100% |
| **Packet Loss Rate** | Perdita pacchetti LoRa | 0.1-30% |
| **Tank Activation** | Serbatoi contemporaneamente aperti | 0-3 |
| **Objective Reward** | Score multi-obiettivo (acqua + radio) | 0.40-0.48 |
| **Crisis Duration** | Ore di crisi effettiva | 5-17h (scenari testati) |

## 🧠 Architettura Agenti

### BaseAgent
Superclasse astratta con metodi polimorfici:
- `calculate_current_satisfaction(sim)`: Stima domanda servita vs. attesa
- `compute_objective(s, tx_interval)`: Calcola reward multi-obiettivo
- `decide_action(step, t, s)`: **Implementare** logica decisionale
- `apply_mitigation(action, sim, lora_net, t)`: **Implementare** esecuzione

### HeuristicAgent (Fornito)
Controllore PI con:
- Feedback proporzionale-integrale sulla satisfaction
- Modulazione dinamica dell'intervallo TX LoRa
- Smoothing esponenziale per evitare colpi di ariete
- Log dettagliato per debug

### Espansioni Future
- **RL Agent**: Deep Q-Learning o Policy Gradient per politiche ottimali
- **MPC Agent**: Model Predictive Control per ottimizzazione con orizzonte
- **Multi-Agent**: Coordinamento distribuito tra agenti locali

## 📚 Documentazione

- **[documents/code_description.md](documents/code_description.md)**: Descrizione tecnica dettagliata di tutte le classi e funzioni
- **[documents/simulation_report.md](documents/simulation_report.md)**: Report completo dello scenario di test con grafici e analisi

## 🔬 Esperimenti Suggeriti

1. **Confronto Strategie**: Demand vs. Pressure vs. Random placement di serbatoi
2. **Aggressione dell'Agente**: Variare aggression da 1 a 10, misurare satisfaction e tank discharge rate
3. **Numero Serbatoi**: Test con n_tanks da 1 a 10, trovare il trade-off costo-beneficio
4. **Crisi Alternative**: Ornstein-Uhlenbeck vs. Linear, test robustness ad incertezza
5. **Radio Robustness**: Variare SF, gateway placement, misurare PLR impact sulla decision quality

## ⚙️ Requisiti Tecnici

- **Python 3.8+**
- **NumPy, Pandas**: Calcoli numerici e data wrangling
- **Matplotlib**: Plotting
- **WNTR**: Simulazione idraulica (incluso in Dyn-WNTR/)
- **EPANET C Library**: Backend di calcolo (Windows/Linux/macOS)

## 📝 Licenza

Questo progetto è parte del **Honor Program** presso l'Università di Roma (Sapienza).

## 👤 Autore

Matteo - Ingegneria Informatica, Corso di Laurea Magistrale  
Università di Roma Sapienza  
A.A. 2025-2026

---

## 📞 Contatti e Supporto

Per domande su:
- **Framework e Architettura**: Vedi [documents/code_description.md](documents/code_description.md)
- **Risultati Simulazioni**: Vedi [documents/simulation_report.md](documents/simulation_report.md)
- **Setup Locale**: Controlla Dyn-WNTR/README.md

---

**Ultima Aggiornamento**: Maggio 2026  
**Versione**: 1.0 (Release Stabile)
