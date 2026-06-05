import os
import re

# ----------------------------------------------------------------------
# CONFIGURAZIONE
# ----------------------------------------------------------------------
file_input = "NET_30_pattern_single_peak_show_OG.inp"   # file originale (unità imperiali)
file_output = "NET_30_pattern_single_peak_show_SI.inp" # file convertito in SI (LPS, metri, mm)

if not os.path.exists(file_input):
    print(f"Errore: Il file '{file_input}' non esiste.")
    exit()

# Fattori di conversione
GPM_to_LPS = 0.06309          # 1 GPM = 0.06309 L/s
FT_to_M    = 0.3048           # 1 ft = 0.3048 m
IN_to_MM   = 25.4             # 1 pollice = 25.4 mm

# ----------------------------------------------------------------------
# FUNZIONI DI CONVERSIONE PER LE VARIE SEZIONI
# ----------------------------------------------------------------------
def convert_junction_line(parts):
    """parts: [ID, Elev, Demand, Pattern, ...]"""
    if len(parts) < 4:
        return parts
    # Elevazione: ft -> m
    try:
        elev_ft = float(parts[1])
        parts[1] = f"{elev_ft * FT_to_M:.6f}"
    except:
        pass
    # Demand: GPM -> LPS
    try:
        demand_gpm = float(parts[2])
        parts[2] = f"{demand_gpm * GPM_to_LPS:.6f}"
    except:
        pass
    # Pattern (colonna 4) non viene modificato
    return parts

def convert_pipe_line(parts):
    """parts: [ID, Node1, Node2, Length, Diameter, Roughness, MinorLoss, Status]"""
    if len(parts) < 5:
        return parts
    # Length: ft -> m
    try:
        length_ft = float(parts[3])
        parts[3] = f"{length_ft * FT_to_M:.4f}"
    except:
        pass
    # Diameter: in -> mm
    try:
        diam_in = float(parts[4])
        parts[4] = f"{diam_in * IN_to_MM:.4f}"
    except:
        pass
    return parts

def convert_curve_line(parts):
    """parts: [ID, X-Value, Y-Value] (per curve HEAD)"""
    if len(parts) < 3:
        return parts
    # X: GPM -> LPS
    try:
        x_gpm = float(parts[1])
        parts[1] = f"{x_gpm * GPM_to_LPS:.6f}"
    except:
        pass
    # Y: ft -> m
    try:
        y_ft = float(parts[2])
        parts[2] = f"{y_ft * FT_to_M:.6f}"
    except:
        pass
    return parts

def convert_valve_line(parts):
    """parts: [ID, Node1, Node2, Diameter, Type, Setting, MinorLoss]"""
    if len(parts) >= 4:
        try:
            diam_in = float(parts[3])
            parts[3] = f"{diam_in * IN_to_MM:.4f}"
        except:
            pass
    return parts

# ----------------------------------------------------------------------
# LETTURA E PROCESSAMENTO LINEE
# ----------------------------------------------------------------------
with open(file_input, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
in_junctions = False
in_pipes = False
in_curves = False
in_valves = False
in_options = False

# Per il tagging successivo
nodi_da_taggare = []

for line in lines:
    stripped = line.strip()
    lower = stripped.lower()

    # Rilevazione cambi di sezione
    if stripped.startswith('[JUNCTIONS]'):
        in_junctions = True
        in_pipes = in_curves = in_valves = in_options = False
    elif stripped.startswith('[PIPES]'):
        in_pipes = True
        in_junctions = in_curves = in_valves = in_options = False
    elif stripped.startswith('[CURVES]'):
        in_curves = True
        in_junctions = in_pipes = in_valves = in_options = False
    elif stripped.startswith('[VALVES]'):
        in_valves = True
        in_junctions = in_pipes = in_curves = in_options = False
    elif stripped.startswith('[OPTIONS]'):
        in_options = True
        in_junctions = in_pipes = in_curves = in_valves = False
    elif stripped.startswith('[') and stripped.endswith(']'):
        # Altre sezioni: disattiva tutte
        in_junctions = in_pipes = in_curves = in_valves = in_options = False

    # --- Conversione [JUNCTIONS] ---
    if in_junctions and stripped and not stripped.startswith(';'):
        parts = line.split()
        if len(parts) >= 4:
            # Salva ID per il tagging se pattern = '9'
            pattern = parts[3].split(';')[0].strip()
            if pattern == '9':
                nodi_da_taggare.append(parts[0])
            # Converti i valori numerici
            parts = convert_junction_line(parts)
            line = '  '.join(parts) + '\n'   # mantieni spaziatura simile

    # --- Conversione [PIPES] ---
    if in_pipes and stripped and not stripped.startswith(';'):
        parts = line.split()
        if len(parts) >= 5:
            parts = convert_pipe_line(parts)
            line = '  '.join(parts) + '\n'

    # --- Conversione [CURVES] ---
    if in_curves and stripped and not stripped.startswith(';'):
        parts = line.split()
        if len(parts) >= 3:
            parts = convert_curve_line(parts)
            line = '  '.join(parts) + '\n'

    # --- Conversione [VALVES] (se presenti diametri) ---
    if in_valves and stripped and not stripped.startswith(';'):
        parts = line.split()
        if len(parts) >= 4:
            parts = convert_valve_line(parts)
            line = '  '.join(parts) + '\n'

    # --- Conversione [OPTIONS] : modifica UNITS ---
    if in_options and stripped.upper().startswith('UNITS'):
        # Sostituisci GPM con LPS (o qualsiasi altra unità imperiale)
        new_line = re.sub(r'\bGPM\b', 'LPS', line, flags=re.IGNORECASE)
        line = new_line

    new_lines.append(line)

# ----------------------------------------------------------------------
# AGGIUNTA TAG [TAGS] (come nello script originale)
# ----------------------------------------------------------------------
# Cerchiamo la sezione [TAGS] e inseriamo i tag
tags_inserted = False
final_lines = []
for line in new_lines:
    final_lines.append(line)
    if line.strip().startswith('[TAGS]') and not tags_inserted:
        final_lines.append("; Tag inseriti automaticamente per abilitare la co-simulazione cyber-fisica\n")
        for node_id in nodi_da_taggare:
            final_lines.append(f"NODE            {node_id:<15} USER_1\n")
        tags_inserted = True

# Se la sezione [TAGS] non esiste, la aggiungiamo in fondo
if not tags_inserted and nodi_da_taggare:
    final_lines.append("\n[TAGS]\n")
    final_lines.append("; Tag inseriti automaticamente per abilitare la co-simulazione cyber-fisica\n")
    for node_id in nodi_da_taggare:
        final_lines.append(f"NODE            {node_id:<15} USER_1\n")
    final_lines.append("\n")

# ----------------------------------------------------------------------
# SCRITTURA DEL NUOVO FILE CONVERTITO
# ----------------------------------------------------------------------
with open(file_output, 'w', encoding='utf-8') as f:
    f.writelines(final_lines)

print(f"✅ Conversione completata.")
print(f"   - File originale: {file_input}")
print(f"   - File convertito: {file_output}")
print(f"   - Nodi utente (pattern=9) trovati: {len(nodi_da_taggare)}")
print("\nConversioni eseguite:")
print("   • Junctions: Elevazione (ft → m), Domanda (GPM → LPS)")
print("   • Pipes: Lunghezza (ft → m), Diametro (in → mm)")
print("   • Curves: X (GPM → LPS), Y (ft → m)")
print("   • Valves: Diametro (in → mm) [se presenti]")
print("   • OPTIONS: UNITS = LPS")
print("\n⚠️ Nota: Il 'Head' dei reservoir NON è stato convertito (lasciato in ft).")
print("   Modificalo manualmente o tramite script successivo.")