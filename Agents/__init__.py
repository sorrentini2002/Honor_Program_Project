from .heuristic_agent import HeuristicAgent
from .priority_agent import PriorityAgent

# Map to easily switch between agent types
AGENT_MAP = {
    'heuristic': HeuristicAgent,
    'priority': PriorityAgent
}
