# Cyber-Physical Co-simulation Framework for Water Distribution Networks (WDNs) and LoRaWAN IoT Control

This project implements a co-simulation engine designed to model, analyze, and optimize the interaction between a **Water Distribution Network (WDN)** (the physical system) and a **LoRaWAN IoT monitoring and control system** (the cyber system). 

Under crisis scenarios (e.g., pressure drops, source failures, or physical leaks), a control agent makes real-time decisions based on sensor telemetry received over the cyber network. The agent sends commands to intelligent valves and pumps in order to distribute water optimally, prioritizing critical zones.

---

## 1. Project Overview

The simulation operates as a closed-loop cyber-physical system running step-by-step:
1. **Physical Layer**: A water network model is simulated using Pressure Driven Analysis (PDA) where actual node demand depends on node pressure. A crisis (e.g., source pressure decay) is introduced dynamically.
2. **Telemetry Transmissions (Uplink)**: Smart sensors at nodes and IoT tanks measure local pressure and tank levels. They transmit these payloads over a simulated LoRaWAN network to one or more gateways.
3. **Cyber Layer**: LoRaWAN communication factors in propagation losses, fading, signal-to-noise ratio (SNR), Spreading Factors (SF), and collisions (which cause packet loss).
4. **Control Agent**: A central controller (which can follow heuristics, priority rules, or Deep Learning models like Graph Neural Networks) processes the telemetry successfully received by the gateways. It then decides mitigation actions (e.g., regulating valves, controlling pumps, or adjusting transmission intervals).
5. **Command Transmissions (Downlink)**: The control commands are transmitted back to the actuators (valves, pumps) via LoRaWAN downlink packets.
6. **Actuation**: Actuators execute the successfully received commands, adjusting the hydraulic network state for the next step.

---

## 2. Architecture & Directory Structure

The project code is organized as follows:

```
Honor_Program_Project/
├── main.py                    # Entry point & co-simulation orchestrator
├── Configurations/            # Simulation profile configurations
│   ├── config_CSA.py          # Configuration for Cagliari Sub-Area (CSA)
│   └── config_NET30.py        # Configuration for Net-30 network
├── Agents/                    # Control agent logic and decision algorithms
│   ├── base_agent.py          # BaseAgent interface definition
│   ├── priority_agent.py      # Control algorithms (Heuristic, Priority, Random)
│   └── models/
│       └── gnn.py             # Graph Neural Network model for GNN-based agents
├── Crises/                    # Physical crisis modeling
│   ├── base_crisis.py         # Base class for hydraulic crises
│   └── deterministic_crises.py# Implementations of decay curves (linear, logarithmic, etc.)
├── Network/                   # Cyber-Physical integration layer
│   ├── lora_system.py         # LoRaWAN system simulator interface
│   ├── water_manager.py       # Hydraulic network manager facade (using mwntr)
│   └── patterns.json          # Cached demand patterns
├── Network_INP/               # EPANET network description files (.inp)
│   ├── CSA_Base_reduced.inp
│   ├── CSA_Base_reduced_Priority.inp
│   ├── NET_30_Equal.inp
│   └── NET_30_Priority.inp
├── Strategies/                # IoT tank placement strategies
│   ├── base_strategy.py       # Base placement strategy class
│   ├── random_strategy.py     # Strategy implementations (Random, Demand, Pressure)
│   └── tank_configs.json      # Tank parameters cache
├── simulation_utils/          # Utility scripts
│   ├── visualization.py       # Diagnostic plotting utility
│   └── analyze_priority_satisfaction.py # Script for priority zone metrics analysis
├── Log_review/                # Directory for generated logs and plots
├── Dashboard/                 # Interactive visual web interface
│   ├── dashboard.html         # Main dashboard HTML map interface
│   └── data.js                # Simulation timeline exported for dashboard rendering
└── (External Repositories)    # Necessary external folders (not part of core project)
    ├── LoRaSimPlus-main/      # Simulates LoRa radio propagation, collisions, & packets
    └── Dyn-WNTR/              # Fork of WNTR supporting interactive step-by-step simulations
```

### Core Components Details

*   **`main.py`**: Coordinates the entire simulation loop. It loads the requested configuration, instantiates the `CoSimulationEngine`, executes the step-by-step simulation loop, logs diagnostic data, and triggers the generation of plots and dashboard files.
*   **`Configurations/`**: Centralizes all parameters. Each file acts as a separate simulation scenario profile (e.g., Cagliari Cagliari Sub-Area `config_CSA.py` vs. Net-30 network `config_NET30.py`).
*   **`Agents/`**: Contains the controller logic. `priority_agent.py` implements the algorithms that decide how much to open/close valves (valvole TCV) and start/stop pumps based on incoming telemetry. The `models/gnn.py` houses Graph Neural Networks for learning-based agents.
*   **`Crises/`**: Models the physical crisis. Over time, it reduces the reservoir head or junction coefficients according to decay models defined in `deterministic_crises.py`.
*   **`Network/`**: Bridges the physical and cyber models. `lora_system.py` uses the external `LoRaSimPlus-main` library to schedule uplink/downlink packets, assign Spreading Factors, compute SNR/RSSI, and count collisions. `water_manager.py` uses the external `Dyn-WNTR` framework to build/modify the EPANET model, place IoT tanks, and update valve status.
*   **`Strategies/`**: Decides which junctions in the water network will receive IoT buffer tanks, using parameters like average pressure or demand.
*   **`simulation_utils/`**: `visualization.py` extracts telemetry from `Dashboard/data.js` to create PNG charts representing satisfaction rates, packet loss, and agent objectives.
*   **`Dashboard/`**: Web page (`dashboard.html`) displaying the water network topology, gateway position, nodes, pipelines, and real-time animation of pressure, tank levels, and packet drops.

### External Dependencies / Repositories
These folders are external repositories required to run the code:
1.  **`LoRaSimPlus-main/`**: A Python implementation of a LoRa/LoRaWAN simulator. Used to evaluate packet loss, interference, capture effect, and power consumption.
2.  **`Dyn-WNTR/`**: Contains `mwntr` (Modified Water Network Tool for Resilience). WNTR originally only allowed running offline/batch simulations for a pre-configured time. `mwntr` enables an *interactive* simulator (`MWNTRInteractiveSimulator`) where a script can advance the hydraulics by small steps, inspect intermediate pressures/flows, apply control actions mid-simulation, and resume.

---

## 3. How to Run the Code

To run a simulation, execute `main.py` from the project root and specify the configuration tag name (which matches the suffix of a configuration file in `Configurations/config_<tag>.py`):

```bash
# Run simulation using Cagliari Cagliari Sub-Area config (Configurations/config_CSA.py)
python main.py CSA

# Run simulation using NET-30 network config (Configurations/config_NET30.py)
python main.py NET30
```

### Configuration Parameters (config_*.py)

All operational variables are centralized within `Configurations/config_*.py`. The primary parameters and their roles are detailed below:

#### Path Configurations
*   `NETWORK_FILE`: Absolute or relative path to the EPANET `.inp` file defining the water network.
*   `LOG_DIR`: Path where all CSV and text log files will be saved.

#### Temporal Configurations
*   `DURATION_HOURS`: Total duration of the simulation in hours.
*   `STEP_MIN`: Time interval between co-simulation steps in minutes (e.g., `5` or `60` minutes).
*   `CRISIS_START_HOUR`: The hour at which the hydraulic crisis begins.

#### Water Network / Hydraulic Configurations
*   `AVG_DEMAND`: Baseline average water demand at the junction nodes (L/s).
*   `DIST_TYPE`: Probability distribution type used if stochastically generating demands (`original`, `normal`, `lognormal`, `uniform`).
*   `PATTERN_MODE`: Method for selecting demand patterns (`random`, `sequential`, `single`).
*   `PRESERVE_DEMAND_PATTERNS`: Set to `True` to keep original EPANET demand patterns, or `False` to randomize them.
*   `TARGET_HEAD`: Target pressure head of the main reservoir (meters).
*   `MIN_BOOST`: Minimum pressure boost supplied by the IoT tanks (meters).
*   `REQUIRED_PRESSURE`: Pressure required for full demand satisfaction (m) under Pressure Driven Analysis (PDA).
*   `MINIMUM_PRESSURE`: Pressure below which demand satisfaction falls to 0 (m) under PDA.

#### IoT Tanks Configuration
*   `N_TANKS`: Number of IoT buffer tanks to deploy in the network.
*   `STRATEGY_NAME`: Strategy used to locate the IoT tanks (`random`, `demand`, `pressure`).
*   `ENABLE_PUMPS`: Set to `True` to enable pumps for tank replenishment control; otherwise `False`.
*   `REMOVE_TANKS`: Set to `True` to strip existing tanks in the EPANET file before inserting custom IoT tanks.

#### Crisis Configuration
*   `CRISIS_MODE`: Type of crisis simulated (`pressure` to decay reservoir head, `flow` to restrict flow).
*   `DECAY_TYPE`: Math profile of the decay (`linear`, `logarithmic`, `exponential`).
*   `DECAY_RATE`: Default rate coefficient of hydraulic decay.
*   `CRISIS_PARAMS`: Dictionary defining:
    *   `decay_rate`: Decay speed.
    *   `min_ratio`: Maximum severity of the crisis (e.g. `0.05` means reservoir pressure drops to 5%).
    *   `recovery_hour`: Hour when network repair begins.
    *   `recovery_duration_hours`: How long the recovery phase lasts.
    *   `recovery_type`: Profile of recovery (`gradual`, `instant`).
    *   `recovery_rate`: Speed of recovery.

#### Cyber / LoRaWAN Configuration
*   `LORA_MODE`: Multi-gateway routing scheme (`simple`, `multihop`).
*   `N_GATEWAYS`: Number of gateways to deploy.
*   `GATEWAY_MODE`: Placement strategy for gateways (`center` of the network, `kmeans`, `random_offset`).
*   `GATEWAY_OFFSET`: Spatial offset distance (meters) for gateway deployment.
*   `SF_MODE`: Spreading Factor assignment strategy (`sequential`, `random`, `distance`-based, or `fixed`).
*   `FIXED_SF`: Hardcoded SF value (6 to 12) if `SF_MODE` is `'fixed'`.

#### Agent Configuration
*   `AGENT_NAME`: The control algorithm used (`heuristic`, `random`, `priority`).
*   `AGENT_THRESHOLD`: Minimum satisfaction threshold below which the agent intervenes.
*   `AGENT_AGGRESSION`: Scaling factor for control updates.
*   `AGENT_ALPHA`: Alpha parameter used for smoothing or feedback adjustments.

#### Actuator Valves Configuration
*   `ISOLATION_PIPES`: List of pipe IDs in the network to be converted into controllable TCV (Throttle Control Valves) governed by the agent.

---

## 4. Simulation Outputs & Logs

Upon completion, the engine exports logs, CSVs, and visualization files.

### 4.1 Logs Directory (`Log_review/`)

The following files are stored inside the configured log directory (usually `Log_review/`):

1.  **`simulation_YYYYMMDD_HHMMSS.log`**:
    Detailed text log of execution. It records system initialization, gateway placement, sensor counts, and highlights transmission successes, collisions, and errors at each step.
2.  **`main_performance.txt`**:
    A tabular log summarizing each step's main performance indicators:
    *   `STEP`: Simulation step index.
    *   `EXPECTED`: Total expected demand of the network (L/s).
    *   `ACTUAL`: Total actual demand met (L/s).
    *   `DIFF`: Water deficit (L/s).
    *   `SATISFACTION`: Global network demand satisfaction level (%).
    *   `TX_INT`: Current transmission interval configured for sensors (seconds).
    *   `OBJECTIVE`: Control agent objective score (reward).
3.  **`demand_distribution.csv`**:
    Tracks step-by-step expected demand, actual demand, and overall satisfaction percentage.
4.  **`valve_commands.csv`**:
    Logs commands sent by the agent to the control valves. Columns: `step`, `time_hours`, `valve_name`, and `commanded_level` (fraction from `0` to `1` indicating the valve opening ratio).
5.  **`valve_settings.csv`**:
    Logs physical settings of the valves applied in the hydraulic solver: `step`, `time_hours`, `valve_name`, `initial_setting`, and `status`.
6.  **`crisis_status.txt`**:
    Logs the progression of the crisis: the step, the reduction ratio (from `1.0` down to `min_ratio`), and the resulting reservoir head/flow coefficients.
7.  **`water_network_setup.txt`**:
    A structural log mapping out junction coordinates, priority nodes, placed IoT tanks, and valve properties.

### 4.2 Diagnostic Charts (`Log_review/`)

At the end of the simulation, three diagnostic plots are generated:

*   **`01_crisis_and_satisfaction.png`**:
    Compares the physical crisis intensity (reduction ratio of source pressure, right Y-axis) against the overall network satisfaction rate and the priority zone satisfaction rate (left Y-axis) over time. This illustrates the exact correlation between crisis progression, agent response, and mitigation efficiency.
*   **`02_cyber_packet_loss.png`**:
    Plots the packet loss percentage over time in the LoRaWAN network. High levels of packet loss indicate congestion or transmission range issues, which delay or block agent telemetry and control commands.
*   **`03_agent_objective.png`**:
    Illustrates the agent's objective reward score at each hour. Higher and smoother scores represent successful optimization and mitigation, while drops mark periods of unmitigated pressure stress.

### 4.3 Web Dashboard (`Dashboard/`)

The simulation updates the interactive dashboard:
*   **`Dashboard/data.js`**: Appends the detailed simulation run data (pressures, flow rates, tank levels, packet statuses, gateway coordinates) into `window.simData`.
*   **`Dashboard/dashboard.html`**: Open this file in any web browser to view the interactive map. You can play, pause, or step through the timeline to see node status (colored by pressure/satisfaction), gateway ranges, packet animations, and live charts.
