"""
Package rmi — Architecture modulaire SpraySpy pour Fanuc RMI.

Couches (de bas en haut) :
  1. robot_config    — Configuration centralisée (dataclass + JSON)
  2. rmi_transport   — TCP + framing \\r\\n
  3. rmi_dispatcher  — RecvThread + routage messages
  4. rmi_client      — API RMI haut niveau, SequenceID, buffer 8
  5. state_machine   — FSM formelle avec transitions validées
  6. orchestrator    — Logique métier, recovery, séquences
  7. gui_app         — Pure vue Tkinter, EventBus

Usage :
    python -m rmi
"""

__version__ = "2.0.0"

from .robot_config import RobotConfig, load_config, save_config
from .rmi_transport import RmiTransport, RmiTransportError, ConnectionLostError
from .rmi_dispatcher import RmiDispatcher, MessageType
from .rmi_client import RmiClient, RmiCommandError, RmiNotConnectedError
from .state_machine import StateMachine, State, InvalidTransition
from .orchestrator import Orchestrator, EventBus, EventType, Event

__all__ = [
    # Config
    "RobotConfig", "load_config", "save_config",
    # Transport
    "RmiTransport", "RmiTransportError", "ConnectionLostError",
    # Dispatcher
    "RmiDispatcher", "MessageType",
    # Client
    "RmiClient", "RmiCommandError", "RmiNotConnectedError",
    # State Machine
    "StateMachine", "State", "InvalidTransition",
    # Orchestrator
    "Orchestrator", "EventBus", "EventType", "Event",
]
