# Dashboard Documentation

**[UPDATE 27/05]** - Documentazione della nuova Dashboard Web interattiva per il monitoraggio real-time delle simulazioni cyber-fisiche.

## Panoramica

La dashboard è un'interfaccia web interattiva che visualizza in tempo reale i dati della co-simulazione idro-comunicativa. È generata automaticamente dal motore di simulazione e visualizzata tramite una pagina HTML standalone + dati JavaScript.

---

## Architettura

### 1. File Principale: `Dashobard/dashboard.html`

Pagina HTML responsive che:
- **Carica i dati** dal file JavaScript `data.js` generato dinamicamente
- **Renderizza grafici interattivi** usando librerie JavaScript (Chart.js, Plotly.js, o D3.js)
- **Aggiorna in tempo reale** durante la simulazione (polling del file `data.js` o WebSocket)
- **Fornisce controlli** per zoom, filtri temporali, esportazione dati

### 2. File Dati: `Dashobard/data.js`

File JavaScript generato dall'orchestratore `CoSimulationEngine` al termine di ogni simulazione. Contiene:

```javascript
// Struttura tipica del data.js
const simulationData = {
  metadata: {
    timestamp: "2026-05-26T12:41:57",
    duration_hours: 40,
    timestep_minutes: 5,
    network_file: "NET_30_Small.inp",
    crisis_type: "pump_test",
    agent_type: "heuristic"
  },
  timeseries: {
    timestamps: [0, 5, 10, 15, ...], // minuti dal inizio
    satisfaction: [100.0, 100.0, ..., 86.4],
    packet_loss_rate: [0.0, 0.0, ..., 15.3],
    active_tanks: [0, 0, ..., 2],
    tank_levels: {
      tank_1: [8.0, 8.0, ..., 2.1],
      tank_2: [5.0, 5.0, ..., 0.0],
      // ...
    },
    valve_settings: {
      valve_1: [0.0, 0.0, ..., 0.7],
      valve_2: [0.0, 0.0, ..., 0.0],
      // ...
    },
    objective_reward: [0.4835, 0.4835, ..., 0.4520]
  },
  summary: {
    min_satisfaction: 86.4,
    max_satisfaction: 100.0,
    avg_satisfaction: 98.2,
    peak_packet_loss: 35.2,
    total_tank_discharge_liters: 145230,
    max_simultaneous_active_tanks: 3,
    crisis_duration_hours: 5.5,
    agent_interventions: 47
  }
};
```

---

## Sezioni Visualizzate

### 1. **Hydraulic Performance Panel**
- **Demand Satisfaction (%)**: Andamento temporale della percentuale di domanda idrica erogata
  - Linea blu: Satisfaction in tempo reale
  - Soglia rossa tratteggiata: Threshold dell'agente (95%)
  - Zona grigia: Periodo di crisi
- **Expected vs Actual Demand (L/s)**: Confronto tra domanda teorica e domanda reale erogata

### 2. **Cyber-Physical Response Panel**
- **Emergency Tank Activation**: Numero di serbatoi IoT attivi contemporaneamente
  - Istogramma verde: Conteggio step-by-step
  - Picchi corrispondono ai periodi di massima crisi
- **Tank Discharge Rate (L/min)**: Velocità di svuotamento dei serbatoi
  - Linee colorate per ogni serbatoio
  - Area rossa: Svuotamento eccessivo (rischio prosciugamento)

### 3. **Communication Quality Panel**
- **Packet Loss Rate (%)**: Percentuale di perdita pacchetti LoRa
  - Linea arancione: PLR istantaneo
  - Bande di confidenza: Intervallo min-max
- **SNR Distribution**: Istogramma della distribuzione del rapporto segnale-rumore sui 5 nodi sensori
  - Rosso: SNR < soglia demodulazione (pacchetti persi)
  - Giallo: SNR borderline (instabile)
  - Verde: SNR robusto

### 4. **Tank Levels Visualization**
- **Multi-line Chart**: Evoluzione del livello idrico per ogni serbatoio
  - Asse Y: Altezza in metri
  - Colori diversi per ogni tank
  - Discontinuità = apertura/chiusura valvole

### 5. **Agent Decision Log**
- **PI Controller Actions**: Timeline delle decisioni dell'agente
  - Proporzionale (P): Reazione istantanea all'errore
  - Integrale (I): Accumulo dell'errore storico
  - Output finale: Combinazione P+I, normalizzata [0,1]
- **Objective Reward Trend**: Score multi-obiettivo (acqua vs. batteria radio)
  - Area sotto la curva: Punteggio cumulativo

### 6. **Radio Network Metrics**
- **Gateway Position Map**: Posizione 2D del gateway e dei 5 nodi sensori
  - Cerchi: Raggio di copertura (RSSI > sensibilità ricevitore)
  - Linee: Collegamento attivo/inattivo
- **Collision Matrix**: Heatmap delle collisioni per fascia oraria
  - Asse X: Ora della simulazione
  - Asse Y: Nodi sensori
  - Intensità colore: Numero collisioni rilevate

---

## Export e Interattività

### Controlli Disponibili

1. **Time Range Selector**: Slider per selezionare intervallo temporale specifico
2. **Zoom & Pan**: Mouse per zoom in/out e spostamento grafici
3. **Toggle Series**: Click su legenda per mostrare/nascondere singole serie dati
4. **Download Options**:
   - CSV: Esporta timeseries in formato tabulare
   - PNG: Screenshot del dashboard
   - JSON: Export completo dati raw per analisi esterna

### Real-time Updates

Se la dashboard è aperta durante la simulazione:
- Polling ogni 10 secondi del file `data.js`
- Se timestamp è aggiornato, ricarica i grafici
- Smooth animation delle transizioni dati

---

## Integrazione con CoSimulationEngine

Al termine di ogni `run_simulation()`, il motore scrive automaticamente:

```python
# Esempio nel main.py
engine = CoSimulationEngine(...)
results_df = engine.run_simulation()

# [AUTO] Salva data.js
engine.export_dashboard_data("Dashobard/data.js")
```

La funzione `export_dashboard_data()` aggrega tutti i dataframe salvati in CSV e li trasforma in struttura JavaScript ottimizzata.

---

## Browser Compatibility

- ✅ Chrome/Edge 90+
- ✅ Firefox 88+
- ✅ Safari 14+
- ⚠️ Internet Explorer: Non supportato

---

## Troubleshooting

| Problema | Causa | Soluzione |
|----------|-------|-----------|
| Grafici vuoti | `data.js` non trovato/corrotto | Rieseguire simulazione, verificare percorso file |
| Valori incoerenti | Cache browser | Ctrl+Shift+Del, svuota cache, F5 |
| Performance lenta | Troppi punti dati | Ridurre durata o aumentare timestep in config |

---

## [UPDATE 27/05] - Novità e Miglioramenti

- ✅ Aggiunto **Radio Network Metrics** con collision matrix
- ✅ Integrazione **agent decision log** in tempo reale  
- ✅ Export CSV/JSON per analisi esterna
- ✅ Responsive design per mobile/tablet
- 📋 *In progress*: WebSocket per aggiornamenti live durante simulazione
- 📋 *Future*: Overlay con annotazioni crisis phases (Pre-crisis, Peak, Recovery)

