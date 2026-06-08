"""
analyze_priority_satisfaction.py

Confronta la soddisfazione idraulica tra:
1. Nodi prioritari (8640, 8696, 8642)
2. Nodi nella zona prioritaria (a monte delle valvole di isolamento)
3. Nodi nella zona non prioritaria (a valle delle valvole di isolamento)

Legge i dati da Dashobard/data.js generato da main.py.
"""

import json
import re
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

# ────────────────────────────────────────────────────────────────────────────
# CONFIGURAZIONE: definizione dei gruppi di nodi
# ─────────────────────────────────────────────────────────────────────────────

# Gruppo 1: Nodi prioritari (tag USER_1_P)
PRIORITY_NODES = {"8640", "8696", "8642"}

# Gruppo 2: Nodi nella zona prioritaria (a monte delle valvole di isolamento)
# Sono i nodi tra il reservoir e le valvole 10147, 10193, 10203
# Escludiamo i nodi prioritari per avere un confronto pulito
UPSTREAM_PRIORITY_ZONE = {
    # Ramo principale (dal reservoir fino a 8638, che alimenta 8640)
    "8628", "8630", "8632", "8634", "8636", "8638",
    # Nodo che alimenta 8640
    "8686",
    # Ramo che alimenta 8696 (a monte della valvola 10203 tra 8696-8698)
    "8690", "8692", "8694",
}

# Gruppo 3: Nodi nella zona non prioritaria (a valle delle valvole di isolamento)
# Questi sono i nodi che vengono isolati durante la crisi
DOWNSTREAM_NON_PRIORITY_ZONE = {
    # Ramo sinistro (valvola 10147 tra 8628 e 8644)
    "8644", "8646", "8648", "8650",
    # Ramo di 8688 (valvola 10193 tra 8642 e 8688)
    "8688", "8738",
    # Ramo di 8698 (valvola 10203 tra 8696 e 8698)
    "8698", "8700", "8702",
}

# ─────────────────────────────────────────────────────────────────────────────
# LETTURA DATI
# ─────────────────────────────────────────────────────────────────────────────

def load_simulation_data(data_js_path="Dashobard/data.js"):
    """
    Legge il file data.js generato da main.py ed estrae simData.
    Il file contiene variabili JavaScript globali, quindi dobbiamo parsarle.
    """
    path = Path(data_js_path)
    if not path.exists():
        raise FileNotFoundError(
            f"File {data_js_path} non trovato. Esegui prima main.py per generarlo."
        )
    
    content = path.read_text(encoding="utf-8")
    
    # Estrai window.simData = [...]
    # Il JSON potrebbe essere su più righe, quindi usiamo una regex flessibile
    match = re.search(r'window\.simData\s*=\s*(\[.*?\]);\s*$', content, re.DOTALL)
    if not match:
        raise ValueError("Impossibile trovare window.simData nel file data.js")
    
    sim_data_json = match.group(1)
    sim_data = json.loads(sim_data_json)
    
    print(f"✓ Caricati {len(sim_data)} step di simulazione da {data_js_path}")
    return sim_data


def compute_group_satisfaction(sim_data, node_set):
    """
    Calcola la soddisfazione media per un gruppo di nodi ad ogni step.
    Restituisce lista di (time_hours, satisfaction_pct).
    """
    times = []
    satisfactions = []
    
    for step_data in sim_data:
        t = step_data["time_hours"]
        nodes = step_data.get("nodes", {})
        
        exp_group = 0.0
        act_group = 0.0
        count = 0
        
        for node_id in node_set:
            if node_id in nodes:
                exp = nodes[node_id].get("expected", 0.0)
                act = nodes[node_id].get("actual", 0.0)
                exp_group += exp
                act_group += act
                count += 1
        
        # Soddisfazione del gruppo
        if exp_group > 1e-6:
            sat = min((act_group / exp_group) * 100.0, 100.0)
        else:
            sat = 100.0  # Nessun domanda → soddisfazione piena
        
        times.append(t)
        satisfactions.append(sat)
    
    return times, satisfactions


# ─────────────────────────────────────────────────────────────────────────────
# PLOTTING
# ─────────────────────────────────────────────────────────────────────────────

def plot_comparison(sim_data, output_path="Log_review/priority_satisfaction_comparison.png"):
    """
    Genera il grafico di confronto tra i tre gruppi.
    """
    # Calcola soddisfazioni per ogni gruppo
    t1, sat1 = compute_group_satisfaction(sim_data, PRIORITY_NODES)
    t2, sat2 = compute_group_satisfaction(sim_data, UPSTREAM_PRIORITY_ZONE)
    t3, sat3 = compute_group_satisfaction(sim_data, DOWNSTREAM_NON_PRIORITY_ZONE)
    
    # Calcola anche la soddisfazione globale (tutti i nodi USER_1/USER_1_P)
    all_nodes = PRIORITY_NODES | UPSTREAM_PRIORITY_ZONE | DOWNSTREAM_NON_PRIORITY_ZONE
    t_all, sat_all = compute_group_satisfaction(sim_data, all_nodes)
    
    # Plot
    fig, ax = plt.subplots(figsize=(14, 7))
    
    ax.plot(t_all, sat_all, 'k-', linewidth=1.5, alpha=0.4, label='Globale (tutti i nodi)')
    ax.plot(t2, sat2, 'b--', linewidth=2, label=f'Zona Prioritaria a monte ({len(UPSTREAM_PRIORITY_ZONE)} nodi)')
    ax.plot(t3, sat3, 'r:', linewidth=2, label=f'Zona Non Prioritaria a valle ({len(DOWNSTREAM_NON_PRIORITY_ZONE)} nodi)')
    
    # Linea di threshold dell'agente (default 90%)
    ax.axhline(y=90, color='orange', linestyle='-.', alpha=0.6, label='Threshold Agente (90%)')
    
    ax.set_xlabel('Tempo (ore)', fontsize=12)
    ax.set_ylabel('Soddisfazione (%)', fontsize=12)
    ax.set_title('Confronto Soddisfazione: Nodi Prioritari vs Zona Prioritaria vs Zona Non Prioritaria', fontsize=13)
    ax.set_ylim(-5, 105)
    ax.legend(loc='lower left', fontsize=10)
    ax.grid(True, alpha=0.3)
    
    # Annotazioni statistiche
    stats_text = (
        f"Media Prioritari: {np.mean(sat1):.1f}%\n"
        f"Media Zona Prioritaria: {np.mean(sat2):.1f}%\n"
        f"Media Zona Non Prioritaria: {np.mean(sat3):.1f}%"
    )
    ax.text(0.98, 0.02, stats_text, transform=ax.transAxes, fontsize=10,
            verticalalignment='bottom', horizontalalignment='right',
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    plt.tight_layout()
    
    # Salva
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150)
    print(f"✓ Grafico salvato in: {output_path}")
    
    # Mostra anche statistiche testuali
    print("\n" + "="*70)
    print("STATISTICHE DI SODDISFAZIONE PER GRUPPO")
    print("="*70)
    print(f"{'Gruppo':<35} {'Media':<10} {'Min':<10} {'Max':<10}")
    print("-"*70)
    print(f"{'Nodi Prioritari':<35} {np.mean(sat1):<10.2f} {min(sat1):<10.2f} {max(sat1):<10.2f}")
    print(f"{'Zona Prioritaria (a monte)':<35} {np.mean(sat2):<10.2f} {min(sat2):<10.2f} {max(sat2):<10.2f}")
    print(f"{'Zona Non Prioritaria (a valle)':<35} {np.mean(sat3):<10.2f} {min(sat3):<10.2f} {max(sat3):<10.2f}")
    print(f"{'Globale':<35} {np.mean(sat_all):<10.2f} {min(sat_all):<10.2f} {max(sat_all):<10.2f}")
    print("="*70)
    
    plt.show()
    return sat1, sat2, sat3


# ─────────────────────────────────────────────────────────────────────────────
# ESECUZIONE
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Analisi soddisfazione per gruppi di nodi")
    parser.add_argument("--data", default="Dashobard/data.js", help="Path al file data.js")
    parser.add_argument("--output", default="Log_review/priority_satisfaction_comparison.png", help="Path output grafico")
    args = parser.parse_args()
    
    sim_data = load_simulation_data(args.data)
    plot_comparison(sim_data, args.output)