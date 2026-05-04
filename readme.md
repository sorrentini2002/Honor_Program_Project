# Technical Documentation: Water Crisis Management Simulation

This document describes in detail the classes and functions implemented in the `main.ipynb` notebook. The project aims to simulate a cyber-physical system where a water network is monitored and controlled through a LoRaWAN sensor network, under the management of an intelligent agent.

---

## 1. Module Import and Configuration (Cell 3)

In this section, the fundamental libraries for the project are imported:
- **WNTR (mwntr)**: For hydraulic modeling and simulation of the network.
- **LoRaSim**: Custom modules for simulating the LoRaWAN protocol.
- **Standard Library**: `os`, `sys`, `random`, `math`, `subprocess` for system management and mathematical calculations.
- **Data Handling**: `numpy` and `pandas` for data and statistics manipulation.

The path system is also configured to ensure that custom modules (`Dyn-WNTR` and `LoRaSim`) are accessible to the Python interpreter, and the native C++ components required for the interactive hydraulic simulator are compiled.

---

## 2. LoRaSystem (LoRaSim Integration)

The `LoRaSystem` class manages the LoRaWAN communication layer.

### LoRaSystem Functions:

#### `__init__(self)`
- **What**: Initializes the communication simulation environment.
- **How**: Creates a sensor registry (`self.sensors`), initializes collision counters, and sets the default transmission interval (`3600s`).
- **Why**: Provides a basis for tracking network statistics and the state of each sensor node during the simulation.

#### `_get_best_model(self, distance_km, sf)`
- **What**: Selects the most appropriate Markov statistical model for a node.
- **How**: Analyzes the `.ini` files available in the `Models` folder, choosing the one that best approximates the sensor distance and the specified Spreading Factor (SF).
- **Why**: Packet loss is not random but depends on physical conditions; this function ensures the simulation is scientifically valid.

#### `register_sensor(self, sensor_id, distance_km, sf)`
- **What**: Registers a new IoT device in the system.
- **How**: Configures the sensor with its specific loss model and initializes the Markov chain state to "1" (good reception).
- **Why**: Allows for defining a dynamic sensor network topology, where each valve or tank can have a sensor with different signal characteristics.

#### `update_sensor_data(self, sensor_id, pressure, level, is_open)`
- **What**: Updates the data ready to be sent by the sensor.
- **How**: Stores the current hydraulic values in the internal buffer of the specific sensor.
- **Why**: Decouples the data sampling moment from the actual transmission moment, reflecting the real behavior of IoT devices.

#### `step(self, current_time, timestep_s)`
- **What**: Executes the transmission logic for the current time step.
- **How**: For each sensor, it checks if it is time to transmit. If so, it uses the Markov model probabilities to decide if the packet is lost (state 0) or received (state 1).
- **Why**: It is the engine that generates the packet loss phenomenon, influencing the agent's visibility of the network state.

---

## 3. Water Network Management

### 3.1 TankConfig
- **What**: Data structure for tank technical specifications.
- **How**: Stores physical parameters (diameters, critical levels) in a compact object.
- **Why**: Avoids having to pass numerous parameters every time a tank is added, ensuring consistency across different profiles (`Small`, `Medium`, `Large`).

### 3.2 WaterNetworkManager
This class manipulates the topology and state of the hydraulic network.

#### `__init__(self, wn_model)`
- **What**: Loads the water network model.
- **How**: Accepts an `.inp` file or an existing `WaterNetworkModel` object.
- **Why**: Centralizes access to the network graph for all subsequent operations.

#### `remove_existing_tanks(self)`
- **What**: Removes pre-existing tanks in the input file.
- **How**: Iterates over all nodes of type `Tank` and deletes them, also removing associated controls.
- **Why**: Allows for testing the effectiveness of only the dynamically added IoT tanks, without interference from previous infrastructure.

#### `add_iot_tanks(self, n_tanks)`
- **What**: Installs emergency tanks in the network.
- **How**: Selects random junctions, adds an elevated tank, and connects it via a pipe that acts as a valve (`IoT_Valve`).
- **Why**: Creates the "intervention points" that the agent can activate to resolve the water crisis.

#### `trigger_blackout(self, head_multiplier)`
- **What**: Simulates the start of a water crisis.
- **How**: Reduces the pressure (head) of the main network reservoirs by applying the specified multiplier.
- **Why**: Represents the system stress-test, simulating, for example, a massive electrical failure at the pumping stations.

#### `set_simulation_options(self, timestep_s)`
- **What**: Configures the technical parameters of the hydraulic solver.
- **How**: Sets the duration, time steps, and activates the PDA (*Pressure Driven Analysis*) model.
- **Why**: The PDA model is indispensable during a crisis (low pressures) because it calculates the actual flow delivered based on the available pressure, unlike the standard DDA model.

---

## 4. CrisisManagementAgent (Intelligent Agent)

The agent optimizes the response to the crisis by merging the water and sensor domains through the objective function:
$$ F(a) = (\alpha \cdot \Delta S) - (\beta \cdot T_{resp}) - (\gamma \cdot PL_f) $$

- **Focus**: The objective function balances the improvement in hydraulic pressure ($\Delta S$) with the speed of intervention ($T_{resp}$) and communication quality ($PL_f$).

---

## 5. CoSimulationEngine (Co-Simulation Engine)

The orchestrator that synchronizes the entire experiment.

#### `__init__(self, network_file, duration_hours, step_min)`
- **What**: Configures the entire test scenario.
- **How**: Instantiates the `WaterNetworkManager`, the `LoRaSystem`, the agent, and the interactive simulator.
- **Why**: Prepares all components so they are ready to exchange data consistently.

#### `run_simulation(self)`
- **What**: Executes the simulation lifecycle.
- **How**:
    1.  Loops over each time step.
    2.  Collects data from sensors (simulating LoRa latency/loss).
    3.  Asks the agent to act if the pressure is low.
    4.  Applies maneuvers to the valves.
    5.  Increases sensor transmission frequency if a valve is opened (emergency frequency).
    6.  Advances both simulators.
- **Why**: Allows observing how cyber decisions (agent/sensors) directly influence the physical reality (water) and vice versa.
