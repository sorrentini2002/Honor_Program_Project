import os

def replace_pipes_with_valves_and_tag_priority(input_file, output_file):
    """
    Sostituisce le tubazioni 10147, 10193, 10203 con valvole TCV
    e aggiunge il tag HIGH_PRIORITY ai nodi prioritari mantenendo USER_1.
    """
    pipes_to_replace = {"10147", "10193", "10203"}
    priority_nodes = {"8640", "8696", "8642"}
    
    if not os.path.exists(input_file):
        print(f"Errore: File '{input_file}' non trovato.")
        return

    with open(input_file, 'r') as f:
        lines = f.readlines()

    new_lines = []
    valves_to_add = []
    in_pipes_section = False
    in_valves_section = False
    in_tags_section = False
    tags_added = False

    for line in lines:
        stripped = line.strip()
        
        # Gestione inizio sezioni
        if stripped.upper().startswith('[PIPES]'):
            in_pipes_section = True
            in_valves_section = False
            in_tags_section = False
            new_lines.append(line)
            continue
        elif stripped.upper().startswith('[VALVES]'):
            in_pipes_section = False
            in_valves_section = True
            in_tags_section = False
            new_lines.append(line)
            # Inserisci subito le valvole dopo l'intestazione
            for v_line in valves_to_add:
                new_lines.append(v_line + "\n")
            continue
        elif stripped.upper().startswith('[TAGS]'):
            in_pipes_section = False
            in_valves_section = False
            in_tags_section = True
            new_lines.append(line)
            continue
        elif stripped.startswith('['):
            # Rileva l'inizio di una nuova sezione (fine della precedente)
            if in_tags_section and not tags_added:
                for node_id in sorted(priority_nodes):
                    new_lines.append(f"NODE\t{node_id}\tHIGH_PRIORITY\n")
                tags_added = True
            
            in_pipes_section = False
            in_valves_section = False
            in_tags_section = False
            new_lines.append(line)
            continue

        # Logica per [PIPES]: salta le tubazioni da sostituire e memorizzale
        if in_pipes_section:
            parts = stripped.split()
            if parts and parts[0] in pipes_to_replace:
                pipe_id = parts[0]
                node1 = parts[1]
                node2 = parts[2]
                diameter = parts[4] # Indice 4 è il diametro
                
                # Crea la riga per la valvola (formato EPANET)
                valve_line = f"{pipe_id:<20s}{node1:<20s}{node2:<20s}{diameter:<15s}TCV{'':<15s}0.00{'':<15s}0.00"
                valves_to_add.append(valve_line)
                print(f"Sostituita tubazione {pipe_id} con valvola TCV")
                continue # Salta la riga della pipe
            else:
                new_lines.append(line)
        elif in_valves_section:
            new_lines.append(line)
        elif in_tags_section:
            new_lines.append(line) # Mantieni i tag esistenti (USER_1)
        else:
            new_lines.append(line)

    # Caso limite: se [TAGS] è l'ultima sezione del file
    if in_tags_section and not tags_added:
        for node_id in sorted(priority_nodes):
            new_lines.append(f"NODE\t{node_id}\tHIGH_PRIORITY\n")
        tags_added = True

    # Se [VALVES] non esisteva o era alla fine, aggiungi le valvole
    if valves_to_add and not any('TCV' in l for l in new_lines):
        new_lines.append("[VALVES]\n")
        for v_line in valves_to_add:
            new_lines.append(v_line + "\n")

    with open(output_file, 'w') as f:
        f.writelines(new_lines)
    
    print(f"Fatto! File salvato come {output_file}")
    print(f"Nodi prioritari etichettati con HIGH_PRIORITY (mantenendo USER_1): {sorted(priority_nodes)}")

# Esegui lo script
replace_pipes_with_valves_and_tag_priority("Network/NET_30_pattern_single_peak_show_SI.inp", "Network/NET_30_pattern_single_peak_show_Priority.inp")