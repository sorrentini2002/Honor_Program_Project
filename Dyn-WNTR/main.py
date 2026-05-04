import random
import sys
import time
import mwntr

from mwntr.sim.interactive_network_simulator import MWNTRInteractiveSimulator

def create_water_network_model():
    # 1. Create a new water network model
    wn = mwntr.network.WaterNetworkModel()

    wn.options.hydraulic.demand_model = 'PDD'

    pattern_house1 = [1.0]*8 + [5.0]*8 + [1.0]*8  
    pattern_house2 = [1.0]*8 + [1.0]*8 + [5.0]*8  
    pattern_house3 = [5.0]*8 + [1.0]*8 + [1.0]*8  
    slow_pattern_house = [1.0]*8 + [2.0]*12 + [1.0]*4    
    pump_speed_pattern = [1.0]*24

    #tank_head_pattern = [1.0]*6 + [0.5]*6 + [1.0]*6 + [0.5]*6
    
    
    wn.add_pattern('slow_pattern_house', slow_pattern_house)
    wn.add_pattern('house1_pattern', pattern_house1)
    wn.add_pattern('house2_pattern', pattern_house2)
    wn.add_pattern('house3_pattern', pattern_house3)
    wn.add_pattern('pump_speed_pattern', pump_speed_pattern)
    #wn.add_pattern('tank_head_pattern', tank_head_pattern)


    wn.add_curve('Pump1_Curve', 'HEAD' , [(0, 50), (5, 45), (10, 40)])  # Head vs. Flow
    wn.add_curve('Pump2_Curve', 'HEAD' , [(0, 90), (5, 85), (10, 80)])  # Head vs. Flow



    # 2. Add a Reservoir (Tank1) on the left
    #wn.add_reservoir('R1', base_head=2000.0, head_pattern=None, coordinates=(-50, 50))
    wn.add_tank('R1', elevation=50, init_level=100, max_level=5000, min_level=0.0, coordinates=(-50, 50))

    # 3. Build a rectangular loop (9 junctions: J0–J8)
    wn.add_junction('J0', base_demand=0.0, elevation=10.0, demand_pattern=None, coordinates=(0, 100))
    wn.add_junction('J1', base_demand=0.0, elevation=10.0, demand_pattern=None, coordinates=(50, 100))
    wn.add_junction('J2', base_demand=0.0, elevation=100.0, demand_pattern=None, coordinates=(100, 100))
    wn.add_junction('J3', base_demand=0.0, elevation=100.0, demand_pattern=None, coordinates=(100, 50))
    wn.add_junction('J4', base_demand=0.0, elevation=100.0, demand_pattern=None, coordinates=(100, 0))
    wn.add_junction('J5', base_demand=0.0, elevation=10.0, demand_pattern=None, coordinates=(50, 0))
    wn.add_junction('J6', base_demand=0.0, elevation=10.0, demand_pattern=None, coordinates=(0, 0))
    wn.add_junction('J7', base_demand=0.0, elevation=10.0, demand_pattern=None, coordinates=(0, 50))
    wn.add_junction('J8', base_demand=0.0, elevation=10.0, demand_pattern=None, coordinates=(50, 50))

    # 4. Replace the reservoir connection with a pump:
    wn.add_pipe('P_R1_J7', 'R1', 'J7', length=50, diameter=0.6, roughness=100, minor_loss=0)
    #wn.add_pump('P1', 'R1', 'J7', initial_status='OPEN', pump_type='HEAD', pump_parameter='Pump1_Curve', speed=1.0)
    #wn.add_pump('P1', 'R1', 'J7', initial_status='OPEN', pump_type='POWER', pump_parameter=0.1)

    # 5. Connect the 8 junctions in a loop (rectangle)
    wn.add_pipe('PR0', 'J0', 'J1', length=50, diameter=0.6, roughness=100 , minor_loss=0)
    #wn.add_pipe('PR1', 'J1', 'J2', length=50, diameter=0.6, roughness=100, minor_loss=0)
    wn.add_pump('P1',  'J1', 'J2', initial_status='OPEN', pump_type='POWER', speed=1)
    wn.add_pipe('PR2', 'J2', 'J3', length=50, diameter=0.6, roughness=100, minor_loss=0)
    wn.add_pipe('PR3', 'J3', 'J4', length=50, diameter=0.6, roughness=100, minor_loss=0)
    wn.add_pipe('PR4', 'J5', 'J4', length=50, diameter=0.6, roughness=100, minor_loss=0)
    wn.add_pipe('PR5', 'J6', 'J5', length=50, diameter=0.6, roughness=100, minor_loss=0)
    wn.add_pipe('PR6', 'J7', 'J6', length=50, diameter=0.6, roughness=100, minor_loss=0)
    wn.add_pipe('PR7', 'J7', 'J0', length=50, diameter=0.6, roughness=100, minor_loss=0)
    wn.add_pipe('PR8', 'J7', 'J8', length=50, diameter=0.6, roughness=100, minor_loss=0)
    wn.add_pipe('PR9', 'J8', 'J3', length=50, diameter=0.6, roughness=100, minor_loss=0)

    # 6. Add three houses (H1, H2, H3) branching from the right side
    wn.add_junction('H1', base_demand=0.5, elevation=10.0, demand_pattern=None, coordinates=(120, 100))
    wn.add_junction('H2', base_demand=0.5, elevation=10.0, demand_pattern=None, coordinates=(120, 50))
    wn.add_junction('H3', base_demand=0.5, elevation=10.0, demand_pattern=None, coordinates=(120, 0))
    #wn.add_junction('H1', base_demand=0.1, elevation=150.0, coordinates=(120, 100))
    #wn.add_junction('H2', base_demand=0.1, elevation=150.0, coordinates=(120, 50))
    #wn.add_junction('H3', base_demand=0.1, elevation=150.0, coordinates=(120, 0))

    # 7. Connect houses to the loop.
    wn.add_pipe('PH1', 'J2', 'H1', length=20, diameter=0.6, roughness=100, minor_loss=0)
    wn.add_pipe('PH3', 'J4', 'H3', length=20, diameter=0.6, roughness=100, minor_loss=0)

    # For H2, remove the existing pipe and add a valve instead:
    wn.add_pipe('PH2', 'J3', 'H2', length=20, diameter=0.6, roughness=100, minor_loss=0)
    #wn.add_valve('V1', 'J3', 'H2', diameter=0.3, valve_type="FCV", initial_status='Active')

    return wn


def create_new_water_model():

    wn = mwntr.network.WaterNetworkModel()
    
    pattern_house1 = [1.0] + [2.0] + [3.0] + [4.0] + [5.0] + [6.0] + [1.0] + [2.0] + [3.0] + [4.0] + [5.0] + [6.0] + [1.0] + [2.0] + [3.0] + [4.0] + [5.0] + [6.0] + [1.0] + [2.0] + [3.0] + [4.0] + [5.0] + [6.0]  
    pattern_house2 = [0.5]*7 + [1.5]*4 + [0.5]*6 + [1.5]*4 + [0.5]*3  
    pattern_house3 = [2.5]*8 + [0.0]*12 + [2.5]*4    

    slow_pattern_house = [1.0]*8 + [2.0]*12 + [1.0]*4    
    pump_speed_pattern = [1.0]*24

    wn.add_pattern('slow_pattern_house', slow_pattern_house)
    wn.add_pattern('house1_pattern', pattern_house1)
    wn.add_pattern('house2_pattern', pattern_house2)
    wn.add_pattern('house3_pattern', pattern_house3)
    wn.add_pattern('pump_speed_pattern', pump_speed_pattern)

    wn.options.hydraulic.demand_model = 'PDD'  # Pressure-driven demand
    wn.add_reservoir('R1', base_head=100, coordinates=(0, 50))
    wn.add_tank('T1', elevation=30, init_level=5, min_level=2, max_level=10, diameter=10, coordinates=(100, 50))

    junction_data = {
        'J1': {'demand': 0.05, 'elevation': 10, 'coordinates': (50, 40)},
        'J2': {'demand': 0.04, 'elevation': 12, 'coordinates': (75, 30)},
        'J3': {'demand': 0.03, 'elevation': 15, 'coordinates': (100, 20)},
        'J4': {'demand': 0.06, 'elevation': 8, 'coordinates': (120, 40)},
    }
    for j, data in junction_data.items():
        wn.add_junction(j, base_demand=data['demand'], elevation=data['elevation'], coordinates=data['coordinates'])

    wn.add_pipe('P1', 'R1', 'J1', length=50, diameter=0.3, roughness=100)
    wn.add_pipe('P3', 'J2', 'J3', length=50, diameter=0.3, roughness=100)
    wn.add_pipe('P5', 'J4', 'T1', length=50, diameter=0.3, roughness=100)
    wn.add_pipe('P6', 'J2', 'T1', length=70, diameter=0.3, roughness=100)

    wn.add_curve('Pump1_Curve', 'HEAD' , [(0, 50), (5, 45), (10, 40)])  # Head vs. Flow
    wn.add_curve('Pump2_Curve', 'HEAD' , [(0, 90), (5, 85), (10, 80)])  # Head vs. Flow
    wn.add_pump('Pump1', 'J1', 'J2', pump_type='HEAD', pump_parameter='Pump1_Curve', speed=1.0)

    wn.add_valve('V1', 'J3', 'J4', valve_type='PRV', diameter=0.2, initial_setting=30)

    return wn


def main():
    one_day_in_seconds = 86400
    global_timestep = 5

    wn = mwntr.network.WaterNetworkModel('NET_4.inp')
    wn.add_pattern('house1_pattern', MWNTRInteractiveSimulator.expand_pattern_to_simulation_duration([1,5,1], global_timestep, simulation_duration=one_day_in_seconds))
    wn.add_pattern('ptn_1', MWNTRInteractiveSimulator.expand_pattern_to_simulation_duration([1,3,5,3,1], global_timestep, simulation_duration=one_day_in_seconds))
        
    sim = MWNTRInteractiveSimulator(wn)
    sim.init_simulation(duration=one_day_in_seconds, global_timestep=global_timestep)

    
    start = time.time()

    #sim.plot_network()
    sims = [sim]

    has_active_leak = []
    has_active_demand = []
    closed_pipe = []


    node_list = wn.junction_name_list
    link_list = wn.link_name_list

    try:
        while not sim.is_terminated():
            #print(f"Current time: {current_time} {current_time / sim.hydraulic_timestep()}")
            #current_time = sim.get_sim_time()

            r = random.random()
            if r < 0.05:
                r2 = random.random()
                if r2 < 0.3:
                    if len(has_active_leak) == 0 or random.random() < 0.5:    
                        node = random.choice(node_list)
                        sim.start_leak(node, 0.1)
                        has_active_leak.append(node)
                        print(f"Leak started on {node}")
                    else:
                        node = random.choice(has_active_leak)
                        sim.stop_leak(node)
                        has_active_leak.remove(node)
                        print(f"Leak stopped on {node}")
                elif r2 < 0.6:
                    if len(has_active_demand) == 0 or random.random() < 0.5:    
                        node = random.choice(node_list)
                        sim.change_demand(node, 1, name='ptn_1')
                        has_active_demand.append(node)
                        print(f"Demand added on {node}")
                    else:
                        node = random.choice(has_active_demand)
                        sim.change_demand(node)
                        has_active_demand.remove(node)
                        print(f"Demand removed on {node}")
                else:
                    if len(closed_pipe) == 0 or random.random() < 0.5:    
                        link = random.choice(link_list)
                        sim.close_pipe(link)
                        closed_pipe.append(link)
                        print(f"Pipe closed {link}")
                    else:
                        link = random.choice(closed_pipe)
                        sim.open_pipe(link)
                        closed_pipe.remove(link)
                        print(f"Pipe opened {link}")

            sim.step_sim()

        end = time.time()
                
        if sim.get_sim_time() >= one_day_in_seconds - global_timestep:
            sim.dump_results_to_csv()
            i += 1
            print(f"Simulation {i} completed in {end - start} seconds.")
        else:
            print("Simulation terminated before reaching the end time.")

    except Exception as e:
        if isinstance(e, KeyboardInterrupt):
            print("Simulation interrupted by user.")
            sys.exit()
        else:
            print("An error occurred during the simulation.")

if __name__ == "__main__":
    main()