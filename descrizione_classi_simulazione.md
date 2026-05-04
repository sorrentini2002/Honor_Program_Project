# Documentazione Tecnica: Simulazione di Gestione Crisi Idrica

Questo documento descrive in dettaglio le classi e le funzioni implementate nel notebook `main.ipynb`. Il progetto mira a simulare un sistema cyber-fisico in cui una rete idrica è monitorata e controllata tramite una rete di sensori LoRaWAN, sotto la gestione di un agente intelligente.

---

## 1. Importazione Moduli e Configurazione (Cella 3)

In questa sezione vengono importate le librerie fondamentali per il progetto:
- **WNTR (mwntr)**: Per la modellazione e simulazione idraulica della rete.
- **LoRaSim**: Moduli personalizzati per la simulazione del protocollo LoRaWAN.
- **Standard Library**: `os`, `sys`, `random`, `math`, `subprocess` per la gestione del sistema e calcoli matematici.
- **Data Handling**: `numpy` e `pandas` per la manipolazione dei dati e delle statistiche.

Viene inoltre configurato il sistema di percorsi (*path*) per garantire che i moduli personalizzati (`Dyn-WNTR` e `LoRaSim`) siano accessibili all'interprete Python e viene eseguita la compilazione dei componenti C++ nativi necessari per il simulatore idraulico interattivo.

---

## 2. LoRaSystem (Integrazione LoRaSim)

La classe `LoRaSystem` gestisce lo strato di comunicazione LoRaWAN.

### Funzioni di LoRaSystem:

#### `__init__(self)`
- **Cosa**: Inizializza l'ambiente di simulazione delle comunicazioni.
- **Come**: Crea un registro dei sensori (`self.sensors`), inizializza i contatori di collisione e imposta l'intervallo di trasmissione predefinito (`3600s`).
- **Perché**: Fornisce una base per tracciare le statistiche di rete e lo stato di ogni nodo sensore durante la simulazione.

#### `_get_best_model(self, distance_km, sf)`
- **Cosa**: Seleziona il modello statistico di Markov più appropriato per un nodo.
- **Come**: Analizza i file `.ini` disponibili nella cartella `Models`, scegliendo quello che meglio approssima la distanza del sensore e lo Spreading Factor (SF) indicato.
- **Perché**: La perdita di pacchetti non è casuale ma dipende dalle condizioni fisiche; questa funzione garantisce che la simulazione sia scientificamente valida.

#### `register_sensor(self, sensor_id, distance_km, sf)`
- **Cosa**: Registra un nuovo dispositivo IoT nel sistema.
- **Come**: Configura il sensore con il suo modello di perdita specifico e inizializza lo stato della catena di Markov a "1" (buona ricezione).
- **Perché**: Permette di definire una topologia di rete sensoristica dinamica, dove ogni valvola o serbatoio può avere un sensore con caratteristiche di segnale diverse.

#### `update_sensor_data(self, sensor_id, pressure, level, is_open)`
- **Cosa**: Aggiorna i dati pronti per essere inviati dal sensore.
- **Come**: Memorizza i valori idraulici correnti nel buffer interno del sensore specifico.
- **Perché**: Separa il momento del campionamento dei dati dal momento della trasmissione effettiva, rispecchiando il comportamento reale dei dispositivi IoT.

#### `step(self, current_time, timestep_s)`
- **Cosa**: Esegue la logica di trasmissione per l'istante temporale corrente.
- **Come**: Per ogni sensore, verifica se è il momento di trasmettere. Se sì, usa le probabilità del modello di Markov per decidere se il pacchetto viene perso (stato 0) o ricevuto (stato 1).
- **Perché**: È il motore che genera il fenomeno della perdita di pacchetti, influenzando la visibilità dell'agente sullo stato della rete.

---

## 3. Gestione della Rete Idrica

### 3.1 TankConfig
- **Cosa**: Struttura dati per le specifiche tecniche dei serbatoi.
- **Come**: Memorizza parametri fisici (diametri, livelli critici) in un oggetto compatto.
- **Perché**: Evita di dover passare numerosi parametri ogni volta che si aggiunge un serbatoio, garantendo coerenza tra i diversi profili (`Small`, `Medium`, `Large`).

### 3.2 WaterNetworkManager
Questa classe manipola la topologia e lo stato della rete idraulica.

#### `__init__(self, wn_model)`
- **Cosa**: Carica il modello della rete idrica.
- **Come**: Accetta un file `.inp` o un oggetto `WaterNetworkModel` esistente.
- **Perché**: Centralizza l'accesso al grafo della rete per tutte le operazioni successive.

#### `remove_existing_tanks(self)`
- **Cosa**: Rimuove i serbatoi pre-esistenti nel file di input.
- **Come**: Itera su tutti i nodi di tipo `Tank` e li elimina, rimuovendo anche i controlli associati.
- **Perché**: Permette di testare l'efficacia dei soli serbatoi IoT aggiunti dinamicamente, senza interferenze da infrastrutture pregresse.

#### `add_iot_tanks(self, n_tanks)`
- **Cosa**: Installa i serbatoi di emergenza nella rete.
- **Come**: Seleziona giunzioni casuali, aggiunge un serbatoio in quota e lo collega tramite una tubazione che funge da valvola (`IoT_Valve`).
- **Perché**: Crea i "punti di intervento" che l'agente può attivare per risolvere la crisi idrica.

#### `trigger_blackout(self, head_multiplier)`
- **Cosa**: Simula l'inizio di una crisi idrica.
- **Come**: Riduce la pressione (head) dei reservoir principali della rete applicando il moltiplicatore indicato.
- **Perché**: Rappresenta lo stress-test del sistema, simulando ad esempio un guasto elettrico massivo alle stazioni di pompaggio.

#### `set_simulation_options(self, timestep_s)`
- **Cosa**: Configura i parametri tecnici del solutore idraulico.
- **Come**: Imposta la durata, i passi temporali e attiva il modello PDA (*Pressure Driven Analysis*).
- **Perché**: Il modello PDA è indispensabile durante una crisi (pressioni basse) perché calcola la portata effettivamente erogata in base alla pressione disponibile, a differenza del modello DDA standard.

---

## 4. CrisisManagementAgent (Agente Intelligente)

L'agente ottimizza la risposta alla crisi unendo i domini idrici e sensoristici tramite la funzione obiettivo:
$$ F(a) = (\alpha \cdot \Delta S) - (\beta \cdot T_{resp}) - (\gamma \cdot PL_f) $$

- **Focus**: La funzione obiettivo bilancia il miglioramento della pressione idraulica ($\Delta S$) con la rapidità di intervento ($T_{resp}$) e la qualità della comunicazione ($PL_f$).

---

## 5. CoSimulationEngine (Motore di Co-Simulazione)

L'orchestratore che sincronizza l'intero esperimento.

#### `__init__(self, network_file, duration_hours, step_min)`
- **Cosa**: Configura l'intero scenario di prova.
- **Come**: Istanzia il `WaterNetworkManager`, il `LoRaSystem`, l'agente e il simulatore interattivo.
- **Perché**: Prepara tutti i componenti affinché siano pronti a scambiarsi dati in modo coerente.

#### `run_simulation(self)`
- **Cosa**: Esegue il ciclo di vita della simulazione.
- **Come**:
    1.  Cicla su ogni step temporale.
    2.  Raccoglie i dati dai sensori (simulando la latenza/perdita LoRa).
    3.  Chiede all'agente di agire se la pressione è bassa.
    4.  Applica le manovre alle valvole.
    5.  Aumenta la frequenza di trasmissione dei sensori se viene aperta una valvola (frequenza di emergenza).
    6.  Avanza entrambi i simulatori.
- **Perché**: Permette di osservare come le decisioni cyber (agente/sensori) influenzano direttamente la realtà fisica (acqua) e viceversa.

