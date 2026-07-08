import os
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg') # Backend non interattivo, ideale per script automatici
import matplotlib.pyplot as plt

def _load_sim_data_from_js(js_file_path: Path):
    """
    Legge il file data.js della dashboard e ne estrae in modo robusto
    l'oggetto JSON contenuto nella variabile window.simData.
    """
    if not js_file_path.exists():
        raise FileNotFoundError(f"Il file dati {js_file_path} non esiste. Avvia prima la simulazione.")

    with open(js_file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Separiamo la stringa esattamente dove viene dichiarato l'array dei dati
    try:
        # Prende tutto ciò che c'è dopo "window.simData = "
        parts = content.rsplit("window.simData = ", 1)
        if len(parts) < 2:
            raise ValueError("Stringa 'window.simData' non trovata nel file.")
        # Rimuove il punto e virgola finale e gli spazi vuoti
        data_str = parts[1].rsplit(";", 1)[0].strip()
        
        sim_data = json.loads(data_str)
        return sim_data
    except Exception as e:
        raise ValueError(f"Errore durante il parsing JSON da data.js: {e}")

def generate_simulation_plots(engine=None):
    """
    Genera i grafici diagnostici leggendo i log sincronizzati da data.js.
    Il parametro 'engine' è mantenuto per retrocompatibilità con main.py.
    """
    base_dir = Path.cwd()
    js_path = base_dir / "Dashboard" / "data.js"
    output_dir = base_dir / "Log_review"
    
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        sim_data = _load_sim_data_from_js(js_path)
    except Exception as e:
        print(f"[Visualization Error] {e}")
        return

    # Estrazione vettoriale dei dati dal JSON
    times = [step['time_hours'] for step in sim_data]
    
    sat_overall = [step['global_metrics'].get('satisfaction_pct', 0) for step in sim_data]
    sat_priority = [step['global_metrics'].get('satisfaction_priority_pct', 0) for step in sim_data]
    crisis_ratio = [step['global_metrics'].get('crisis_ratio', 1.0) for step in sim_data]
    packet_loss = [step['global_metrics'].get('packet_loss', 0) for step in sim_data]
    objective = [step['global_metrics'].get('objective', 0) for step in sim_data]

    # Stile globale dei grafici
    plt.style.use('seaborn-v0_8-whitegrid')

    # =========================================================================
    # GRAFICO 1: Stato della Crisi e Soddisfazione (Complessiva vs Prioritaria)
    # =========================================================================
    fig1, ax1 = plt.subplots(figsize=(12, 6))
    
    # Asse Y Sinistro: Soddisfazione
    ax1.plot(times, sat_overall, label='Soddisfazione Complessiva', color='#3b82f6', linewidth=2.5)
    
    # Disegniamo la prioritaria solo se assume valori diversi da 0 (es. Modalità Prioritaria attiva)
    if any(val > 0.1 for val in sat_priority):
        ax1.plot(times, sat_priority, label='Soddisfazione Zona Prioritaria', 
                 color='#eab308', linewidth=2.5, linestyle='--')

    ax1.set_xlabel('Tempo di Simulazione (Ore)', fontweight='bold')
    ax1.set_ylabel('Livello di Soddisfazione (%)', color='black', fontweight='bold')
    ax1.set_ylim(-5, 105)
    ax1.tick_params(axis='y', labelcolor='black')
    ax1.legend(loc='lower left', frameon=True, shadow=True)

    # Asse Y Destro: Evoluzione della Crisi
    ax2 = ax1.twinx()
    ax2.plot(times, crisis_ratio, label='Ratio Pressione Sorgente (Crisi)', 
             color='#ef4444', linewidth=2, linestyle=':')
    ax2.fill_between(times, crisis_ratio, 1.0, color='#ef4444', alpha=0.1, hatch='//') # Area di deficit
    ax2.set_ylabel('Ratio Crisi (1 = Normale, 0 = Vuoto)', color='#ef4444', fontweight='bold')
    ax2.set_ylim(-0.05, 1.05)
    ax2.tick_params(axis='y', labelcolor='#ef4444')
    ax2.legend(loc='lower right', frameon=True, shadow=True)

    plt.title('Impatto della Crisi sulla Soddisfazione Idrica', fontsize=14, fontweight='bold', pad=15)
    plt.tight_layout()
    fig1.savefig(output_dir / "01_crisis_and_satisfaction.png", dpi=300)
    plt.close(fig1)

    # =========================================================================
    # GRAFICO 2: Andamento dei Pacchetti LoRa Persi
    # =========================================================================
    fig2, ax = plt.subplots(figsize=(12, 4))
    
    ax.plot(times, packet_loss, color='#f97316', linewidth=2)
    ax.fill_between(times, packet_loss, color='#f97316', alpha=0.2)
    
    ax.set_title('Degrado Rete Cyber: Andamento Packet Loss (LoRaWAN)', fontsize=12, fontweight='bold')
    ax.set_xlabel('Tempo di Simulazione (Ore)')
    ax.set_ylabel('Packet Loss (%)')
    
    # Adattiamo l'asse Y per non schiacciare troppo i dati se la perdita è bassa
    max_pl = max(packet_loss) if packet_loss else 0
    ax.set_ylim(0, max(max_pl + 5, 10)) 
    
    plt.tight_layout()
    fig2.savefig(output_dir / "02_cyber_packet_loss.png", dpi=300)
    plt.close(fig2)

    # =========================================================================
    # GRAFICO 3: Funzione Obiettivo dell'Agente (Reward)
    # =========================================================================
    fig3, ax = plt.subplots(figsize=(12, 4))
    
    ax.plot(times, objective, color='#8b5cf6', linewidth=2.5)
    
    ax.set_title("Efficacia dell'Agente: Andamento Funzione Obiettivo", fontsize=12, fontweight='bold')
    ax.set_xlabel('Tempo di Simulazione (Ore)')
    ax.set_ylabel('Valore Reward')
    
    # Area per evidenziare i crolli dell'obiettivo
    ax.fill_between(times, objective, min(objective), color='#8b5cf6', alpha=0.1)
    
    plt.tight_layout()
    fig3.savefig(output_dir / "03_agent_objective.png", dpi=300)
    plt.close(fig3)

    print(f"\n✓ Grafici di analisi generati con successo in: {output_dir}")

if __name__ == "__main__":
    # Permette di testare o rigenerare i grafici lanciando solo questo script
    generate_simulation_plots()