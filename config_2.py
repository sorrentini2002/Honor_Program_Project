"""
Configuration module for CoSimulationEngine
All simulation parameters can be easily modified here
"""

# ============================================================================
# NETWORK CONFIGURATION
# ============================================================================
NETWORK_FILE = "Network/NET_30_pattern_single_peak_show.inp"  # Path to the water network file 

# ============================================================================
# TEMPORAL CONFIGURATION
# ============================================================================
DURATION_HOURS = 40          # Total simulation duration in hours
STEP_MIN = 5                 # Time step in minutes
CRISIS_START_HOUR = 5      # Hour at which crisis begins (converted to steps internally)

# ============================================================================
# WATER NETWORK / HYDRAULIC CONFIGURATION
# ============================================================================
AVG_DEMAND = 0.3              # Average demand at junctions (L/s)
DIST_TYPE = 'lognormal'      # Distribution type: 'original', 'normal', 'lognormal', 'uniform'
PATTERN_MODE = 'random'      # Pattern selection: 'random', 'sequential', 'single'
TARGET_HEAD = 280             # Target reservoir head (m)
MIN_BOOST = 65                # Minimum boost pressure for IoT tanks (m)

# ============================================================================
# IOT TANKS CONFIGURATION
# ============================================================================
N_TANKS = 5                  # Number of IoT tanks to deploy
STRATEGY_NAME = 'random'     # Strategy for tank placement: 'random', 'demand', 'pressure'
ENABLE_PUMPS = True           # Enable pumps for tank control
REMOVE_TANKS = False         # Remove existing tanks before adding new ones

# ============================================================================
# CRISIS CONFIGURATION
# ============================================================================
CRISIS_MODE = 'pressure'     # Crisis mode: 'pressure', 'flow'
DECAY_TYPE = 'logarithmic'     # Crisis decay model: 'linear', 'exponential', 'instant', 'logarithmic', 'pump_test', 'ornstein_uhlenbeck'
DECAY_RATE = 4.5
CRISIS_PARAMS = {
    'decay_rate': 0.3,
    'min_ratio': 0.025,
    'recovery_hour': 30,
    'recovery_duration_hours': 10.0,
    'recovery_type': 'gradual',  # 'instant' or 'gradual'
    'recovery_rate': 0.4, # Only for gradual recovery
}                           

# ============================================================================
# LoRa / CYBER COMMUNICATION CONFIGURATION
# ============================================================================
LORA_MODE = 'simple'         # LoRa communication mode: 'simple', 'multihop'
GATEWAY_MODE = 'center'      # Gateway position: 'center', 'random_offset'
GATEWAY_OFFSET = 0.0         # Offset distance for gateway from center (meters)
SF_MODE = 'fixed'            # Spreading Factor assignment: 'sequential', 'random', 'distance', 'fixed'
FIXED_SF = 12                # Fixed SF value if SF_MODE='fixed'

# ============================================================================
# AGENT CONFIGURATION
# ============================================================================
AGENT_NAME = 'heuristic'     # Agent type: 'heuristic', 'random', 'learning'
AGENT_THRESHOLD = 0.8       # Satisfaction threshold for agent actions
AGENT_AGGRESSION = 0.3       # Aggression level (control intensity)
AGENT_ALPHA = 0.5            # Alpha parameter for agent learning/smoothing

# ============================================================================
# HELPER FUNCTION TO CREATE ENGINE WITH CONFIG
# ============================================================================
def create_engine():
    """
    Factory function to create a CoSimulationEngine with all config parameters.
    
    Usage in main.py:
        from config import create_engine
        engine = create_engine()
        engine.run_simulation()
    
    Returns:
        CoSimulationEngine: Configured engine instance
    """
    import sys
    import os
    from importlib.util import spec_from_file_location, module_from_spec
    
    # Load the main module directly by file path to avoid conflicts with Dyn-WNTR/main.py
    main_path = os.path.join(os.path.dirname(__file__), 'main.py')
    spec = spec_from_file_location("__main_module__", main_path)
    main_module = module_from_spec(spec)
    spec.loader.exec_module(main_module)
    
    CoSimulationEngine = main_module.CoSimulationEngine
    
    return CoSimulationEngine(
        network_file=NETWORK_FILE,
        duration_hours=DURATION_HOURS,
        step_min=STEP_MIN,
        remove_tanks=REMOVE_TANKS,
        crisis_mode=CRISIS_MODE,
        crisis_start_hour=CRISIS_START_HOUR,
        decay_type=DECAY_TYPE,
        decay_rate=DECAY_RATE,
        avg_demand=AVG_DEMAND,
        dist_type=DIST_TYPE,
        pattern_mode=PATTERN_MODE,
        n_tanks=N_TANKS,
        strategy_name=STRATEGY_NAME,
        agent_name=AGENT_NAME,
        agent_threshold=AGENT_THRESHOLD,
        agent_aggression=AGENT_AGGRESSION,
        enable_pumps=ENABLE_PUMPS,
        lora_mode=LORA_MODE,
        gateway_mode=GATEWAY_MODE,
        min_boost=MIN_BOOST,
        gateway_offset=GATEWAY_OFFSET,
        sf_mode=SF_MODE,
        fixed_sf=FIXED_SF,
        crisis_params=CRISIS_PARAMS,
        agent_alpha=AGENT_ALPHA,
        target_head=TARGET_HEAD,
    )
