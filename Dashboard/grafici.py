import json
import re
import matplotlib.pyplot as plt

def parse_js_sim_data(filepath):
    """Funzione per estrarre e convertire in dizionario il blocco window.simData contenuto nel file .js"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    match = re.search(r'window\.simData\s*=\s*(\[.*\]);?', content, re.DOTALL)
    if not match:
        raise ValueError(f"Impossibile trovare window.simData nel file {filepath}")
    
    sim_data_str = match.group(1)
    return json.loads(sim_data_str)

# 1. Configurazione dei percorsi dei file
file_baseline = "baseline.js"
file_equalitario = "scenario_equalitario.js"
file_prioritario = "scenario_prioritario.js"

# 2. Caricamento dei dati di simulazione
print("Caricamento e parsing dei file di simulazione...")
data_b = parse_js_sim_data(file_baseline)
data_e = parse_js_sim_data(file_equalitario)
data_p = parse_js_sim_data(file_prioritario)

# 3. Creazione di un dizionario Baseline per mappare {tempo: soddisfazione}
# Questo garantisce che il confronto avvenga esattamente per lo stesso istante temporale
baseline_map = {step['time_hours']: step['global_metrics']['satisfaction_pct'] for step in data_b}

# 4. Calcolo delle differenze per lo scenario Equalitario
times_e = []
diff_equalitario = []
for step in data_e:
    t = step['time_hours']
    if t in baseline_map:
        times_e.append(t)
        # Differenza: Valore Scenario - Valore Baseline
        diff = step['global_metrics']['satisfaction_pct'] - baseline_map[t]
        diff_equalitario.append(diff)

# 5. Calcolo delle differenze per lo scenario Prioritario (Zona Prioritaria)
times_p_p = []
diff_prioritario_p = []
for step in data_p:
    t = step['time_hours']
    if t in baseline_map:
        times_p_p.append(t)
        diff = step['global_metrics']['satisfaction_priority_pct'] - baseline_map[t]
        diff_prioritario_p.append(diff)

# 6. Calcolo delle differenze per lo scenario Prioritario (Zona NON Prioritaria)
priority_nodes = {"Junction_1362", "Junction_1407"}
times_p_np = []
diff_prioritario_np = []

for step in data_p:
    t = step['time_hours']
    if t in baseline_map:
        sum_actual_np = 0
        sum_expected_np = 0
        for node_id, metrics in step['nodes'].items():
            if node_id not in priority_nodes:
                sum_actual_np += metrics.get('actual', 0)
                sum_expected_np += metrics.get('expected', 0)
        
        if sum_expected_np > 0:
            pct_np = (sum_actual_np / sum_expected_np) * 100
        else:
            pct_np = 100.0
            
        times_p_np.append(t)
        # Differenza rispetto alla baseline per la zona non prioritaria
        diff = pct_np - baseline_map[t]
        diff_prioritario_np.append(diff)

# 7. Creazione del grafico delle differenze
print("Generazione del grafico delle differenze rispetto alla baseline...")
plt.figure(figsize=(12, 6))

# Linea di riferimento orizzontale a 0 (rappresenta la Baseline stessa)
plt.axhline(0, color='#7f8c8d', linestyle='--', linewidth=2, label='Baseline (Riferimento = 0)')

# Plot dei delta (differenze) degli altri scenari
plt.plot(times_e, diff_equalitario, label='Δ Equalitario', color='#2980b9', linewidth=2)
plt.plot(times_p_p, diff_prioritario_p, label='Δ Prioritario - Zona Prioritaria', color='#27ae60', linewidth=2)
plt.plot(times_p_np, diff_prioritario_np, label='Δ Prioritario - Zona Non Prioritaria', color='#e67e22', linewidth=2)

# Personalizzazione del grafico
plt.title('Variazione della Soddisfazione della Domanda rispetto alla Baseline', fontsize=14, fontweight='bold', pad=15)
plt.xlabel('Tempo (ore)', fontsize=12)
plt.ylabel('Differenza di Soddisfazione (Punti Percentuali %)', fontsize=12)
plt.xlim(min(baseline_map.keys()), max(baseline_map.keys()))

# Griglia e legenda
plt.grid(True, linestyle=':', alpha=0.6)
plt.legend(fontsize=11, loc='best')

# Ottimizzazione del layout
plt.tight_layout()
plt.show()

import json
import re
import matplotlib.pyplot as plt

def parse_js_sim_data(filepath):
    """Funzione per estrarre e convertire in dizionario il blocco window.simData contenuto nel file .js"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    match = re.search(r'window\.simData\s*=\s*(\[.*\]);?', content, re.DOTALL)
    if not match:
        raise ValueError(f"Impossibile trovare window.simData nel file {filepath}")
    
    sim_data_str = match.group(1)
    return json.loads(sim_data_str)

# 1. Configurazione dei percorsi dei file
file_equalitario = "scenario_equalitario.js"
file_gateway2 = "gateway_2.js"
file_gateway3 = "gateway_3.js"

# 2. Caricamento dei dati di simulazione
print("Caricamento e parsing dei file in corso...")
data_e = parse_js_sim_data(file_equalitario)
data_g2 = parse_js_sim_data(file_gateway2)
data_g3 = parse_js_sim_data(file_gateway3)

# 3. Estrazione dei dati assoluti per lo Scenario Equalitario
times_e = [step['time_hours'] for step in data_e]
packet_loss_e = [step['global_metrics']['packet_loss'] for step in data_e]

# 4. Estrazione dei dati assoluti per la configurazione a 2 Gateway
times_g2 = [step['time_hours'] for step in data_g2]
packet_loss_g2 = [step['global_metrics']['packet_loss'] for step in data_g2]

# 5. Estrazione dei dati assoluti per la configurazione a 3 Gateway
times_g3 = [step['time_hours'] for step in data_g3]
packet_loss_g3 = [step['global_metrics']['packet_loss'] for step in data_g3]

# 6. Creazione del grafico con i valori assoluti
print("Generazione del grafico delle percentuali assolute...")
plt.figure(figsize=(12, 6))

# Plot delle 3 linee con i valori reali/assoluti
plt.plot(times_e, packet_loss_e, label='Scenario Equalitario', color='#2980b9', linewidth=2)
plt.plot(times_g2, packet_loss_g2, label='Configurazione 2 Gateway', color='#e67e22', linewidth=2)
plt.plot(times_g3, packet_loss_g3, label='Configurazione 3 Gateway', color='#27ae60', linewidth=2)

# Personalizzazione estetica del grafico
plt.title('Confronto Assoluto del Packet Loss nel Tempo', fontsize=14, fontweight='bold', pad=15)
plt.xlabel('Tempo (ore)', fontsize=12)
plt.ylabel('Perdita di Pacchetti (Valore Assoluto %)', fontsize=12)

# Imposta i limiti dell'asse X basandosi sulla simulazione
plt.xlim(min(times_e), max(times_e))

# Opzionale: aggiunge un piccolo margine sopra il valore massimo per una migliore leggibilità
max_val = max(max(packet_loss_e), max(packet_loss_g2), max(packet_loss_g3))
plt.ylim(-0.5, max_val + 2)

# Griglia e legenda
plt.grid(True, linestyle=':', alpha=0.6)
plt.legend(fontsize=11, loc='best')

# Ottimizzazione del layout e visualizzazione
plt.tight_layout()
plt.show()

import json
import re
import matplotlib.pyplot as plt

def parse_js_sim_data(filepath):
    """Funzione per estrarre e convertire in dizionario il blocco window.simData contenuto nel file .js"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    match = re.search(r'window\.simData\s*=\s*(\[.*\]);?', content, re.DOTALL)
    if not match:
        raise ValueError(f"Impossibile trovare window.simData nel file {filepath}")
    
    sim_data_str = match.group(1)
    return json.loads(sim_data_str)

# 1. Configurazione dei percorsi dei file
file_baseline = "baseline.js"
file_equalitario = "scenario_equalitario.js"
file_gateway3 = "gateway_3.js"

# 2. Caricamento dei dati di simulazione
print("Caricamento e parsing dei file di simulazione...")
data_b = parse_js_sim_data(file_baseline)
data_e = parse_js_sim_data(file_equalitario)
data_g3 = parse_js_sim_data(file_gateway3)

# 3. Estrazione dei dati per la Baseline
times_b = [step['time_hours'] for step in data_b]
obj_b = [step['global_metrics']['objective'] for step in data_b]

# 4. Estrazione dei dati per lo Scenario Equalitario
times_e = [step['time_hours'] for step in data_e]
obj_e = [step['global_metrics']['objective'] for step in data_e]

# 5. Estrazione dei dati per il caso con 3 Gateway
times_g3 = [step['time_hours'] for step in data_g3]
obj_g3 = [step['global_metrics']['objective'] for step in data_g3]

# 6. Generazione del grafico comparativo
print("Generazione del grafico dell'andamento della funzione obiettivo...")
plt.figure(figsize=(12, 6))

# Plot delle 3 curve richieste
plt.plot(times_b, obj_b, label='Baseline', color='#7f8c8d', linestyle='--', linewidth=2)
plt.plot(times_e, obj_e, label='Configurazione 3 Gateway', color='#2980b9', linewidth=2)
plt.plot(times_g3, obj_g3, label='Scenario Equalitario', color='#27ae60', linewidth=2)

# Personalizzazione ed estetica del grafico
plt.title('Andamento della Funzione Obiettivo nel Tempo', fontsize=14, fontweight='bold', pad=15)
plt.xlabel('Tempo (ore)', fontsize=12)
plt.ylabel('Valore Funzione Obiettivo', fontsize=12)

# Imposta i limiti dell'asse X e Y basandosi sui valori estratti
plt.xlim(min(times_b), max(times_b))
# Se la funzione obiettivo è normalizzata tra 0 e 1, puoi decommentare la linea successiva:
# plt.ylim(-0.05, 1.05)

# Griglia e legenda
plt.grid(True, linestyle=':', alpha=0.6)
plt.legend(fontsize=11, loc='best')

# Ottimizzazione del layout e visualizzazione
plt.tight_layout()
plt.show()