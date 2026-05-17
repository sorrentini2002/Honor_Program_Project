# Water Crisis Management Co-Simulation Framework - Technical Code Description

Questo documento contiene la descrizione tecnica dettagliata dell'architettura e dell'implementazione del framework di co-simulazione cyber-fisica per reti di distribuzione idrica.

Questo repository contiene un framework avanzato di co-simulazione cyber-fisica per reti di distribuzione idrica. Permette di modellare l'insorgenza di crisi idriche (es. rotture di tubature, calo di pressione delle fonti) e di valutare strategie di mitigazione attive tramite l'impiego di agenti intelligenti, hardware IoT (serbatoi smart, valvole e pompe controllate da remoto) e protocolli di comunicazione wireless (LoRaWAN).

---

## Architettura del Progetto

Il progetto è modulare ed è suddiviso nei seguenti componenti e cartelle principali:

### 1. `main.py` (ex `main_up copy.ipynb`)
Questo file rappresenta il nucleo operativo (Orchestratore) che avvia, sincronizza e monitora la simulazione cyber-fisica. Al suo interno sono definite tre classi portanti e una funzione di supporto, ciascuna con compiti estremamente specifici:

#### A. Classe `LoRaSystem` (Powered by LoRaSimPlus physics)
Gestisce e simula il livello di comunicazione wireless LoRaWAN, utilizzando modelli fisici realistici di propagazione del segnale e rilevamento delle collisioni.
- **`__init__(self, log_filename, config_params, ...)`**: Inizializza le metriche globali, configura i parametri radio (Bandwidth, Payload Size, Coding Rate, TX Power) e prepara i file di log. Mantiene ora una lista persistente di pacchetti "in volo" per gestire collisioni che attraversano i confini temporali degli step di simulazione.
- **`_log(self, message, level)`**: Centralizza la gestione dei log su file fisico per tracciare la telemetria radio e le cause di perdita (SNR, collisioni).
- **`setup_gateway(self, pos)`**: Definisce la posizione spaziale del Gateway, fondamentale per il calcolo del Path Loss basato sulla distanza.
- **Modello Fisico (`RSSI/SNR`)**: Calcola la potenza ricevuta (RSSI) tramite un modello di decadimento log-distanza (`Log-distance Path Loss`) con ombreggiamento log-normale (`Shadowing`). Determina la ricevibilità verificando che l'RSSI sia superiore alla sensibilità del ricevitore e che il rapporto segnale-rumore (SNR) sia superiore alla soglia di demodulazione per lo Spreading Factor utilizzato.
- **Gestione Collisioni Avanzata**: Implementa un algoritmo di rilevamento collisioni completo che considera:
  - **Sovrapposizione in Frequenza**: In base alla larghezza di banda (BW) e alla frequenza centrale.
  - **Ortogonalità SF**: Sfrutta il fatto che pacchetti con SF diversi sono quasi ortogonali e non collidono tra loro.
  - **Capture Effect**: Implementa la logica per cui, in caso di collisione, se un pacchetto è significativamente più forte (> 6dB), viene comunque ricevuto correttamente.
  - **Timing/Preamble**: Verifica se la sovrapposizione temporale (basata su timestamp assoluti) danneggia il preambolo critico (8 simboli) necessario alla sincronizzazione.
- **`register_iot_sensors(self, valves, wn, mode, sf_mode, fixed_sf)`**: Registra i nodi IoT calcolandone coordinate, distanza, RSSI deterministico di base e assegnando frequenze di portante in modo round-robin tra i canali standard EU868.
- **`get_packet_loss_rate(self)`**: Restituisce la percentuale di perdita pacchetti (PLR), aggregando perdite fisiche (segnale debole) e collisioni radio.
- **`step(self, current_time, timestep_s)`**: Avanza la simulazione radio usando timestamp assoluti globali. Mantiene i pacchetti attivi oltre il singolo step se il loro `airtime` lo richiede, permettendo il rilevamento di collisioni cross-timestep. Rimuove la randomicità artificiale degli offset in favore di una sincronizzazione temporale precisa.

#### B. Classe `WaterNetworkManager`
È l'interfaccia fisica con il motore matematico idraulico WNTR. Modella e altera la rete infrastrutturale, inserendovi gli elementi cyber-fisici (sensori, pompe, serbatoi smart).
- **`__init__(self, wn_model)`**: Inizializza la topologia "nuda" base leggendola dal file `.inp` usando la libreria iterativa `mwntr`.
- **`activate_network_demands(self, ...)`**: Applica distribuzioni stocastiche (es. Normale, Lognormale, Uniforme) per alterare in modo casuale i consumi base (Base Demand) dei singoli giunti idrici. Lo fa per aggiungere realismo, rumore e incertezza alle simulazioni, invece di fare affidamento su consumi fissi o teorici, stressando la validità dell'agente.
- **`remove_existing_tanks(self, ...)`**: Elimina completamente dalla rete eventuali serbatoi preesistenti e le tubature a essi connesse. Lo fa per permettere la generazione di un ambiente sperimentale puro ("naked topolgy") su cui testare algoritmi liberi da vincoli architettonici legacy.
- **`instrument_existing_tanks(self, ...)`**: Analizza i serbatoi *già presenti* nel file `.inp` e li "retrofitta". Lo fa rimpiazzando i loro collegamenti passivi con valvole attive (TCV) e pompe (se richieste), tramutandoli di fatto in nodi attivi e controllabili a distanza dall'Intelligenza Artificiale.
- **`add_iot_tanks(self, n_tanks, strategy_name, ...)`**: Aggiunge del tutto *nuovi* serbatoi di emergenza nei punti calcolati dalle Strategie euristiche (Random, Demand, Pressure). Genera il nodo serbatoio, ne calcola l'elevazione idraulica forzando un `min_boost` necessario a contrastare la pressione della rete, e lo collega ad essa usando link IoT.
- **`fix_reservoir_head(self, target_head, ...)`**: Impone una pressione statica fissa (Head) alla sorgente primaria della città (Reservoir), ripulendo le serie storiche/pattern preesistenti. Lo fa per pulire la base di partenza e garantire che la crisi avvenga esattamente secondo i ritmi da noi dettati nel motore.
- **`get_main_link(self, ...)`**: Esplora il grafo WNTR e identifica la tubatura col diametro maggiore o principale collegata al Reservoir. Serve all'algoritmo per capire dove applicare la "strozzatura" che mima la crisi.
- **`instrument_source_with_valve(self)`**: Cancella fisicamente dal grafo la tubatura principale identificata precedentemente e la rimpiazza con una valvola a farfalla (Throttle Control Valve - TCV). Lo fa perché in WNTR il modo matematicamente più solido per simulare una caduta modulabile e dinamica della portata in modalità Pressure-Dependent (PDA) è agire sul Loss Coefficient di una valvola piuttosto che sul carico dei bacini idrici.
- **`apply_crisis_reduction(self, sim, ratio, step, mode, ...)`**: Inietta il tasso di riduzione generato dai modelli di crisi (Crises Folder) nel simulatore dinamico. Crea dinamicamente regole fisiche istantanee (Control Actions) in WNTR per chiudere proporzionalmente la TCV sorgente, simulando con precisione il progredire della rottura o carenza.
- **`_add_iot_control_to_tank(self, ...)`**: (Funzione Helper) Responsabile della costruzione fisica dei link cyber-fisici. Crea la Valvola di scarico d'emergenza e l'eventuale Pompa per la ricarica del serbatoio (costruendo le curve di potenza idraulica associate).
- **`set_simulation_options(self, timestep_s)`**: Configura profondamente le opzioni di solver di EPANET/WNTR per usare tassativamente il modello PDA (Pressure-Driven Analysis), vitale per ottenere perdite di carico realistiche.

#### C. Funzione `calculate_gateway_pos`
- **Cosa fa**: Estrapola le coordinate geografiche `(x, y)` dove posizionare il Gateway LoRaWAN.
- **Come lo fa**: Analizza tutti i nodi e, se richiesto il `mode='center'`, fa una media geometrica calcolando il centroide. Altrimenti piazza a distanza con raggio `random_offset` o su un nodo `random`.
- **Perché lo fa**: Il posizionamento topologico del Gateway altera drammaticamente il tasso di collisione e perdita di segnale radio. Modificarlo è essenziale per studiare come l'agente reagisce a perdite di pacchetti radio differenziate (Cyber-robustness).

#### D. Classe `CoSimulationEngine`
È l'orchestratore ("Direttore dei lavori") che unisce lo strato idraulico (WNTR) e quello cibernetico (LoRa + Agente).
- **`__init__(self, ...)`**: Riceve l'input configurativo massiccio. In sequenza innesca: la preparazione idrica (`WaterNetworkManager`), seleziona e instanzia la matematica della crisi (`CRISIS_MAP`), prepara il layer radio (`LoRaSystem` e `calculate_gateway_pos`), instanzia l'esecutore dinamico step-by-step (`MWNTRInteractiveSimulator`) unendolo al cervello virtuale (`AGENT_MAP`). Crea inoltre tutti gli stack in memoria in cui salvare la storicità della telemetria per il plot finale.
- **`run_simulation(self)`**: Il cuore pulsante (Ciclo While/For) dell'intera simulazione. L'algoritmo esegue in loop questi step critici:
  1. *Inizializzazione & Soft Start (Step 0)*: Per prevenire un noto errore matematico dei solver idraulici al tempo T=0 (Singular Jacobian/Matrix error) in caso di crisi, disabilita temporaneamente le "demand" fisiche dei nodi, le registra e apre del tutto le valvole. 
  2. *Ripristino e Progressione (Step > 0)*: Restituisce ai nodi le loro vere curve di domanda pre-calcolate e avanza il timer di simulazione `t` dell'intervallo prescelto `step_min`.
  3. *Iniezione Crisi*: Appena si sorpassa il `crisis_start_step`, preleva al volo il moltiplicatore dal modello di decadimento prescelto e "soffoca" la sorgente tramite `apply_crisis_reduction`.
  4. *Pulizia Cache Controlli*: Pulisce sistematicamente i vecchi controlli di rete degli step passati per prevenire overflow di memoria in WNTR e conflitti idraulici irrisolvibili.
  5. *Cyber-Physical Loop (Agente)*: Usa `sim.node_res` per scansionare istantaneamente il gap fra domanda idrica aspettata ed erogata (Satisfation `s_current`). Verifica quante collisioni LoRa sono avvenute (`pl`). Chiama l'agente tramite `agent.decide_action(...)` facendogli valutare i dati ed estrae le azioni da svolgere. Infine, tramite `agent.apply_mitigation(...)` traduce queste azioni logiche in modifiche idrauliche fisiche per il passo corrente (accensione pompe, scarico valvole serbatoi).
  6. *Salvataggio & Calcolo WNTR*: Archivia le metriche (Expected Demand vs Actual Demand vs Packet Loss) sui log CSV preposti, lancia un comando di calcolo puro della rete `sim.step_sim()` che risolve la matrice Pressione/Flusso, e re-itera il ciclo fino ad esaurimento ore. Restituisce tutti i dataframe simulati completi alla fine per graficarli.

### 2. Cartella `Crises`
Contiene la logica e i modelli che simulano il calo di prestazioni della rete, generando la crisi vera e propria. Modella come l'erogazione si riduce nel tempo partendo dall'ora di inizio crisi.
- **`base_crisis.py`** (`BaseCrisis`): Classe astratta da cui tutti i modelli ereditano. Definisce il costruttore di base col parametro `decay_rate` (tasso di decadimento).
- **`deterministic_crises.py`**:
  - `LinearCrisis`: Riduce proporzionalmente in modo lineare la portata/pressione a ogni step fino a raggiungere un `min_ratio`.
  - `ExponentialCrisis`: Causa una caduta molto ripida che si smorza via via (esponenziale), ottima per modellare perdite rapide ma non istantanee.
  - `InstantCrisis`: Riduce tutto il valore immediatamente al `min_ratio` al momento di inizio crisi (rottura catastrofica del tubo).
  - `LogarithmicCrisis`: Produce una decrescita dolce logaritmica.
- **`Ornstein_Uhlenbeck.py`** (`OrnsteinUhlenbeck`): Implementa un modello stocastico avanzato (Mean-Reverting Process). Simula una crisi che fluttua e tende a stabilizzarsi in modo erratico verso un valore target (`mu` o `min_ratio`), introdotto da shock casuali (`volatility`). Offre le dinamiche più realistiche in presenza di piccole variazioni o instabilità della fonte primaria.
- **`test_crises.py`** (`PumpTestCrisis`): Modello di crisi specializzato per scenari di test con recupero programmato. Implementa una fase di degradazione lineare seguita da un recupero controllato. Permette di specificare:
  - `recovery_hour`: Ora di inizio del recupero della rete.
  - `recovery_duration_hours`: Durata complessiva della fase di recupero.
  - `recovery_type`: Tipo di recupero (`'instant'` per ripristino istantaneo o `'gradual'` per ripristino graduale).
  - `recovery_rate`: Tasso di recupero nel caso di ripristino graduale. Utile per simulare scenari complessi di riparazione e ripristino del servizio.

### 3. Cartella `Strategies`
Indirizza il problema di posizionamento spaziale ottimale dei nuovi serbatoi di emergenza all'interno della rete (Tank Deployment).
- **`base_strategy.py`** (`BasePlacementStrategy`): Classe astratta base. Impone il metodo `get_nodes(n_tanks)` che restituisce gli ID dei nodi dove piazzare i serbatoi.
- **`demand_strategy.py`** (`DemandStrategy`): Restituisce i nodi che presentano storicamente (o a livello base) la *domanda idrica più alta*. Cerca di mettere le scorte d'acqua vicino a chi ne consuma di più.
- **`pressure_strategy.py`** (`PressureStrategy`): Valuta i nodi più deboli strutturalmente, ossia quelli con la pressione media storica inferiore, e vi piazza serbatoi per rinforzare le zone marginali della rete.
- **`random_strategy.py`** (`RandomStrategy`): Posizionamento puramente casuale. Utilizzato per test comparativi o baseline.

### 4. Cartella `Agents`
Rappresenta l'"Intelligenza Artificiale" (o Cyber-Controller) del sistema. Questo strato legge la telemetria idraulica della rete, calcola le discrepanze (errori) rispetto ai target ottimali di erogazione e invia comandi correttivi in tempo reale verso l'hardware IoT (valvole e pompe) e verso il layer radio (frequenza dei messaggi).

#### A. `base_agent.py` (`BaseAgent`)
È la superclasse astratta e architetturale da cui ogni agente futuro dovrà ereditare.
- **`__init__(self, water_net, lora_net, threshold, aggression, alpha)`**: Inizializza le variabili essenziali di memoria dell'agente. Definisce la `threshold` (soglia di soddisfazione minima che l'agente deve difendere), l'`aggression` (il peso della reattività) e il fattore `alpha` (il peso percentuale attribuito al risparmio idrico rispetto al risparmio radio/batteria `gamma`).
- **`calculate_current_satisfaction(self, sim)`**: Scansiona la totalità dei nodi della simulazione al tempo `t`. Confronta matematicamente l'`expected_demand` (il fabbisogno teorico) con la `demand` (il fabbisogno reale erogato). 
  - *Come lo fa*: Estrae i valori tramite `sim.node_res.expected_demand` e fa una media ponderata globale calcolando un ratio `[0, 1]`. Inoltre, possiede una routine di debug integrata che, nei primi 15 step di simulazione, avverte se trova nodi cronicamente insoddisfatti prima della crisi, utilissimo per correggere le topologie base fallate.
- **`compute_objective(self, s, tx_interval)`**: Calcola il "Reward" (Punteggio di Performance Obiettivo) dell'Agente.
  - *Come lo fa*: Applica una funzione multi-obiettivo pesata. Il punteggio sale con l'alta soddisfazione idrica (pesata da `alpha`) ma scende drasticamente se la rete di comunicazione radio viene inondata di pacchetti, comportando un alto `frequency_cost` (calcolato inversamente all'intervallo, pesato da `gamma = 1 - alpha`).
- **`decide_action(self, step, t, s)` e `apply_mitigation(self, action, sim, lora_net, t)`**: Metodi vuoti e obbligatori per il polimorfismo. Qualsiasi classe figlia deve sovrascriverli per implementare la propria logica (es. Reinforcement Learning, o un algoritmo Euristico).

#### B. `heuristic_agent.py` (`HeuristicAgent`)
È un vero e proprio **Controllore PI (Proporzionale-Integrale)** disegnato su misura per le dinamiche cyber-fisiche WNTR-LoRa. Analizza l'entità della crisi idrica e modula dinamicamente le difese fisiche (serbatoi) e quelle cibernetiche (radio).
- **`__init__(self, ...)`**: Oltre a chiamare l'`__init__` padre, precalcola matematicamente le costanti del controller. Genera il coefficiente Proporzionale (`Kp`) e quello Integrale (`Ki`) direttamente come derivati dal parametro `aggression`. Inizializza i file di log per stampare il differenziale previsto/effettivo ad ogni step.
- **`decide_action(self, step, t, s)`**: Il cuore decisionale della logica.
  - *Cosa fa*: Calcola lo scarto (errore istantaneo) tra il target ottimale (`threshold`) e la realtà (`s`). Ritorna l'azione (Apertura Valvole e Frequenza Radio).
  - *Come lo fa (Idraulica)*: Applica un integrale accumulato per prevenire il "steady-state error" (se la rete è poco in crisi da molto tempo, l'azione aumenta lo stesso). Calcola l'output PI e lo taglia fra 0 e 1. Usa uno "smoothing" esponenziale (`0.7 * current + 0.3 * target`) per evitare l'apertura brusca (prevenendo il letale "Colpo di Ariete" e il crash del solutore WNTR). Se non c'è crisi e c'è eccedenza (errore negativo), ordina l'accensione delle pompe (`pump_speed = 1.0` di notte, `0.3` di giorno) per ricaricare attivamente i serbatoi d'emergenza prelevando acqua dalla strada.
  - *Come lo fa (Cyber)*: Modula dinamicamente l'intervallo di trasmissione LoRaWAN (il "silenzio radio"). Se l'agente non sta intervenendo idraulicamente ed è tutto pacifico, fa spegnere le radio quasi totalmente (`tx_interval = 3600s`), ma durante lo stato di allerta apre la banda permettendo ai sensori di parlare ogni 5 minuti (`300s`), abbassando la latenza ma saturando l'etere di collisioni.
- **`apply_mitigation(self, action, sim, lora_net, t)`**: L'esecutore armato delle decisioni prese. Prende l'azione logica astratta [0, 1] e la materializza sull'hardware virtuale in WNTR e in LoRa.
  - *Cosa/Come lo fa*: Imposta l'intervallo deciso al modulo LoRa. Legge l'output di livello calcolato prima, lo moltiplica per i serbatoi totali `n_tanks` e decide **quanti** aprirne fisicamente. Usando le API di WNTR (`ControlAction` e `SimTimeCondition`), disattiva la configurazione base delle `TCV` dei serbatoi e forza istantaneamente su `Open` i primi `target_open` serbatoi, chiudendo gli altri. Applica un concetto identico alle pompe: se il `pump_speed` è zero spegne il macchinario in tronco, altrimenti imposta la potenza della curva idraulica al valore di setpoint calcolato.

---

## Parametri di Inizializzazione della Co-Simulazione (CoSimulationEngine)

Il passaggio centrale per modellare il comportamento desiderato è istanziare il simulatore tramite i corretti parametri del dizionario. Ecco l'analisi minuziosa dei parametri della chiamata a `CoSimulationEngine`:

| Parametro | Descrizione e Come Cambiarlo |
|---|---|
| `network_file` | Percorso e file del modello della rete idrica (es. `.inp` formato EPANET). Indica la topologia base. |
| `duration_hours` | (Int) Durata complessiva in ore della simulazione. Aumentarlo per analisi lunghe, abbassarlo per test veloci (es. 24, 72). |
| `step_min` | (Float) Risoluzione temporale di aggiornamento in minuti (es. 2.5 min = 150s). Cambialo per determinare quanto spesso Agente e WNTR si scambiano i dati (più piccolo = più precisione e carico CPU). |
| `remove_tanks` | (Bool) Se `True`, elimina tutti i serbatoi originariamente modellati nel file `.inp`. Ottimo per studiare una rete "naked" (pura). |
| `crisis_mode` | (Str) `'flow'` o `'pressure'`. Nel modo `'flow'` chiude la portata di una Throttle Control Valve fittizia al source, agendo direttamente sulla riduzione di portata. Nel modo `'pressure'` modula il carico base della sorgente (Reservoir). Usare `'pressure'` per le simulazioni più recenti che richiedono maggiore stabilità numerica. |
| `decay_type` | (Str) Decide il tipo di decadimento tra `'linear'`, `'exponential'`, `'instant'`, `'logarithmic'`, `'ornstein_uhlenbeck'`, `'pump_test'`. Cambialo per emulare crisi diverse. |
| `crisis_params` | (Dict) Parametri specifici del tipo di crisi scelto.<br>**Parametri comuni**: `decay_rate` (tasso di decadimento), `min_ratio` o `mu` (il valore percentuale minimo finale della fonte).<br>**Per `'ornstein_uhlenbeck'`**: `reversion_speed` e `volatility` (intensità di oscillazione).<br>**Per `'pump_test'`**: `recovery_hour` (ora di inizio recupero), `recovery_duration_hours` (durata recupero), `recovery_type` (`'instant'` o `'gradual'`), `recovery_rate` (tasso di recupero graduale). |
| `avg_demand` | (Float) Moltiplicatore o Base Demand media in GPM / LPS, se la distribuzione della domanda non usa i valori nativi dell' `.inp`. |
| `dist_type` | (Str) `'original'`, `'normal'`, `'lognormal'`, `'uniform'`. Modifica il modo in cui le domande iniziali base sono popolate per i nodi, generando scenari incerti. |
| `pattern_mode` | (Str) `'random'`, `'sequential'`, `'single'`. Regola i pattern di consumo estratti per i singoli nodi se non originali. |
| `min_boost` | (Float) Se retrofittati o nuovi, i serbatoi vengono elevati di questo dislivello (in metri) rispetto al nodo, per assicurare spinta e pressione minima sufficiente quando aperti. |
| `n_tanks` | (Int) Numero di nuovi IoT Tanks da piazzare nella rete. Regola il buffer capacitivo di emergenza dell'agente. |
| `strategy_name` | (Str) `'random'`, `'demand'`, `'pressure'`. La strategia usata per posizionare gli `n_tanks` nuovi serbatoi scelti al punto precedente. |
| `crisis_start_hour` | (Float) Ora di innesco della crisi (es. `1.0` = dopo 1 ora la sorgente principale inizia a cedere). |
| `agent_name` | (Str) Tipo di agente. Attualmente `'heuristic'`. |
| `agent_threshold` | (Float) Valore di soddisfazione percentuale sotto la quale l'agente inizia a reagire (es. `0.99` significa che appena la rete scende sotto il 99% di richiesta idrica servita l'agente interviene). |
| `agent_aggression` | (Float) Il moltiplicatore proporzionale della reazione dell'agente. Impostarlo alto (es. 5.0 - 10.0) fa svuotare e usare serbatoi repentinamente. Basso per svuotamento dolce. |
| `agent_alpha` | (Float) Definisce l'equilibrio nella funzione di Reward. `0.9` significa che dare acqua (90%) è ben più importante che risparmiare batteria / pacchetti radio (10%). |
| `enable_pumps` | (Bool) Se `True` viene simulata ed eretta fisicamente una Pompa che spinge l'acqua del tubo dentro al serbatoio IoT per ricaricarlo quando non in uso. |
| `gateway_mode` | (Str) Decide dove sta la torre radio LoRaWAN. `'center'`, `'random_offset'`, `'random'`. |
| `lora_mode` | (Str) Protocollo: `'simple'` (calcolo fisico diretto RSSI/SNR) o `'multihop'` (supporto per routing multi-salto, attualmente semplificato nel nuovo motore fisico). |
| `sf_mode` | (Str) Spreading Factor della rete LoRa: `'fixed'`, `'sequential'`, `'random'`, `'distance'`. Cambia la robustezza dei pacchetti alle collisioni. |
| `fixed_sf` | (Int) Valore del LoRa Spreading Factor (tra 7 e 12) per i sensori, usato solo se `sf_mode='fixed'`. `12` massimizza il raggio ma aumenta i tempi d'aria e riduce frequenza e batteria. |
| `target_head` | (Float) Forza la pressione statica / carico (Head) alla fonte Reservoir all'inizio della simulazione prima della crisi. Impatta violentemente tutta l'idraulica di rete. |

---

## Esempio di Configurazione Pratica

Di seguito un esempio concreto di istanziazione del motore di co-simulazione con parametri realistici:

```python
engine = CoSimulationEngine(
    network_file='Network/NET_30_users_only.inp',
    duration_hours=60,
    step_min=5,
    remove_tanks=False,
    crisis_mode='pressure',
    decay_type='pump_test',
    crisis_params={
        'decay_rate': 0.1,
        'min_ratio': 0.025,
        'recovery_hour': 17.0,
        'recovery_duration_hours': 5.0,
        'recovery_type': 'instant',
        'recovery_rate': 0.1,
    },
    avg_demand=15,
    dist_type='lognormal',
    pattern_mode='random',
    min_boost=1,
    n_tanks=5,
    strategy_name='demand',
    crisis_start_hour=1.5,
    agent_name='heuristic',
    agent_threshold=0.95,
    agent_aggression=3.0,
    agent_alpha=0.5,
    enable_pumps=True,
    gateway_mode='center',
    lora_mode='simple',
    sf_mode='fixed',
    fixed_sf=12,
    target_head=20
)

results = engine.run_simulation()
```

**Descrizione della Configurazione:**
- **Network & Duration**: Simula una rete di 30 utenti per 60 ore con step di 5 minuti.
- **Crisis Scenario**: Crisis di tipo `pump_test` che degrada linearmente a partire dall'ora 1.5, raggiungendo il 2.5% di capacità, con recupero istantaneo all'ora 17.
- **Demand Model**: Distribuisce la domanda idrica secondo una distribuzione lognormale (realistica per scenari urbani reali) con media di 15 GPM.
- **IoT Infrastructure**: Piazza 5 serbatoi di emergenza utilizzando la strategia `demand` (vicino ai nodi ad alto consumo), abilitando pompe per il ricircolo.
- **Agent Strategy**: L'agente `heuristic` (Controllore PI) interviene quando la soddisfazione scende sotto il 95%, con aggressività moderata (3.0) e focus sulla qualità idrica (alpha=0.5).
- **Radio Configuration**: Gateway centrale con LoRa SF=12 fisso per massima robustezza ai segnali deboli, mode `simple` per calcoli RSSI/SNR deterministici.
- **Pressure Control**: Pressione statica iniziale (Head) fissata a 20 metri per equilibrare sensibilità alla crisi e stabilità numerica del solutore.
