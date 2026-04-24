"""
Machine à états (FSM) pour le robot Fanuc RMI.

Chaque transition est validée : on ne peut pas envoyer un mouvement
si le robot est en HOLD ou ERROR. Les transitions invalides lèvent
une exception, garantissant la cohérence du système.

Les callbacks on_enter / on_exit / on_transition permettent à
l'orchestrateur et à la GUI de réagir aux changements d'état.
"""

import threading
import logging
from enum import Enum, auto
from typing import Callable, Dict, FrozenSet, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ======================================================================
# États
# ======================================================================

class State(Enum):
    """États possibles du système robot + RMI."""

    DISCONNECTED = "disconnected"
    """Pas de connexion TCP."""

    CONNECTING = "connecting"
    """Connexion TCP en cours (FRC_Connect)."""

    CONNECTED = "connected"
    """TCP connecté, RMI_MOVE pas encore démarré."""

    WAIT_MANUAL = "wait_manual"
    """TP actif — phase de positionnement manuel."""

    WAIT_AUTO = "wait_auto"
    """Attend que le contrôleur soit en mode AUTO + TP OFF."""

    INITIALIZING = "initializing"
    """FRC_Initialize en cours (création de RMI_MOVE)."""

    IDLE = "idle"
    """Prêt — RMI_MOVE tourne, en attente d'instructions."""

    MOVING = "moving"
    """Mouvement manuel unique en cours."""

    SEQUENCING = "sequencing"
    """Séquence 3D en cours."""

    HOLD = "hold"
    """RMI en HOLD (erreur récupérable, doc §3.3)."""

    ERROR = "error"
    """Erreur non-récupérable — intervention requise."""

    ABORTING = "aborting"
    """FRC_Abort en cours."""

    DISCONNECTING = "disconnecting"
    """Déconnexion en cours."""


# ======================================================================
# Transitions autorisées
# ======================================================================

# Dict[état_source] → {ensemble d'états destination autorisés}
TRANSITIONS: Dict[State, FrozenSet[State]] = {
    State.DISCONNECTED: frozenset({
        State.CONNECTING,
    }),
    State.CONNECTING: frozenset({
        State.CONNECTED,
        State.DISCONNECTED,      # Échec connexion
    }),
    State.CONNECTED: frozenset({
        State.WAIT_MANUAL,
        State.WAIT_AUTO,
        State.INITIALIZING,      # Si déjà AUTO + TP OFF
        State.DISCONNECTING,
        State.DISCONNECTED,      # Perte connexion
    }),
    State.WAIT_MANUAL: frozenset({
        State.WAIT_AUTO,
        State.DISCONNECTING,
        State.DISCONNECTED,
    }),
    State.WAIT_AUTO: frozenset({
        State.INITIALIZING,
        State.WAIT_MANUAL,       # L'opérateur rallume le TP
        State.DISCONNECTING,
        State.DISCONNECTED,
    }),
    State.INITIALIZING: frozenset({
        State.IDLE,
        State.ERROR,
        State.DISCONNECTING,
        State.DISCONNECTED,
    }),
    State.IDLE: frozenset({
        State.MOVING,
        State.SEQUENCING,
        State.ABORTING,
        State.HOLD,              # FRC_SystemFault reçu
        State.ERROR,
        State.DISCONNECTING,
        State.DISCONNECTED,
    }),
    State.MOVING: frozenset({
        State.IDLE,              # Mouvement terminé
        State.HOLD,              # Erreur récupérable
        State.ERROR,             # Erreur non-récupérable
        State.ABORTING,          # Arrêt d'urgence
        State.DISCONNECTED,
    }),
    State.SEQUENCING: frozenset({
        State.IDLE,              # Séquence terminée
        State.HOLD,              # Erreur récupérable
        State.ERROR,             # Erreur non-récupérable
        State.ABORTING,          # Arrêt d'urgence
        State.DISCONNECTED,
    }),
    State.HOLD: frozenset({
        State.IDLE,              # Après FRC_Reset + FRC_Continue
        State.ABORTING,          # L'utilisateur décide d'abandonner
        State.ERROR,             # Le reset échoue
        State.DISCONNECTED,
    }),
    State.ERROR: frozenset({
        State.ABORTING,
        State.DISCONNECTING,
        State.DISCONNECTED,
    }),
    State.ABORTING: frozenset({
        State.CONNECTED,         # Après FRC_Abort réussi
        State.IDLE,              # Ré-initialisation rapide
        State.ERROR,
        State.DISCONNECTING,
        State.DISCONNECTED,
    }),
    State.DISCONNECTING: frozenset({
        State.DISCONNECTED,
    }),
}


# ======================================================================
# Exceptions
# ======================================================================

class InvalidTransition(Exception):
    """Tentative de transition non autorisée dans la FSM."""
    def __init__(self, from_state: State, to_state: State):
        self.from_state = from_state
        self.to_state = to_state
        allowed = TRANSITIONS.get(from_state, frozenset())
        super().__init__(
            f"Transition invalide: {from_state.value} → {to_state.value}. "
            f"Autorisés: {[s.value for s in allowed]}"
        )


# ======================================================================
# Type callbacks
# ======================================================================

# callback(old_state, new_state)
TransitionCallback = Callable[[State, State], None]
# callback(state)
StateCallback = Callable[[State], None]


# ======================================================================
# Machine à états
# ======================================================================

class StateMachine:
    """Machine à états formelle avec transitions validées.

    Thread-safe : toutes les transitions sont protégées par un Lock.
    Les callbacks sont appelés **en dehors** du lock pour éviter les deadlocks.
    """

    def __init__(self, initial: State = State.DISCONNECTED) -> None:
        self._state = initial
        self._lock = threading.Lock()

        # Callbacks
        self._on_transition: List[TransitionCallback] = []
        self._on_enter: Dict[State, List[StateCallback]] = {}
        self._on_exit: Dict[State, List[StateCallback]] = {}

    # ------------------------------------------------------------------
    # Propriété état courant
    # ------------------------------------------------------------------

    @property
    def state(self) -> State:
        """État courant (lecture thread-safe sans lock — atomique en CPython)."""
        return self._state

    @property
    def is_operational(self) -> bool:
        """Le robot est dans un état opérationnel (connecté + RMI actif)."""
        return self._state in (State.IDLE, State.MOVING, State.SEQUENCING)

    @property
    def can_move(self) -> bool:
        """On peut envoyer un mouvement."""
        return self._state == State.IDLE

    @property
    def is_busy(self) -> bool:
        """Un mouvement ou une séquence est en cours."""
        return self._state in (State.MOVING, State.SEQUENCING)

    # ------------------------------------------------------------------
    # Transition
    # ------------------------------------------------------------------

    def transition(self, new_state: State) -> Tuple[State, State]:
        """Effectue une transition d'état.

        Args:
            new_state: Nouvel état cible.

        Returns:
            Tuple (ancien_état, nouvel_état).

        Raises:
            InvalidTransition: Si la transition n'est pas autorisée.
        """
        callbacks_to_call: List[Callable] = []

        with self._lock:
            old_state = self._state

            # Même état → no-op
            if old_state == new_state:
                return (old_state, new_state)

            # Valider la transition
            allowed = TRANSITIONS.get(old_state, frozenset())
            if new_state not in allowed:
                raise InvalidTransition(old_state, new_state)

            # Effectuer la transition
            self._state = new_state

            # Préparer les callbacks (on les appellera hors du lock)
            for cb in self._on_exit.get(old_state, []):
                callbacks_to_call.append(lambda c=cb: c(old_state))

            for cb in self._on_transition:
                callbacks_to_call.append(lambda c=cb, o=old_state, n=new_state: c(o, n))

            for cb in self._on_enter.get(new_state, []):
                callbacks_to_call.append(lambda c=cb: c(new_state))

        # Appeler les callbacks HORS du lock
        logger.info("FSM: %s → %s", old_state.value, new_state.value)
        for cb in callbacks_to_call:
            try:
                cb()
            except Exception:
                logger.exception("Erreur callback FSM")

        return (old_state, new_state)

    def force_state(self, new_state: State) -> None:
        """Force un état sans validation (usage exceptionnel : perte connexion).

        Les callbacks on_transition sont appelés comme pour transition(),
        garantissant que la GUI est toujours notifiée.
        """
        callbacks_to_call: List[Callable] = []
        with self._lock:
            old = self._state
            if old == new_state:
                return
            self._state = new_state
            for cb in self._on_transition:
                callbacks_to_call.append(lambda c=cb, o=old, n=new_state: c(o, n))
        logger.warning("FSM forcé: %s → %s", old.value, new_state.value)
        for cb in callbacks_to_call:
            try:
                cb()
            except Exception:
                logger.exception("Erreur callback FSM force_state")

    # ------------------------------------------------------------------
    # Enregistrement callbacks
    # ------------------------------------------------------------------

    def on_transition(self, callback: TransitionCallback) -> None:
        """Enregistre un callback appelé à chaque transition.

        callback(old_state, new_state)
        """
        self._on_transition.append(callback)

    def on_enter(self, state: State, callback: StateCallback) -> None:
        """Enregistre un callback appelé à l'entrée dans un état."""
        self._on_enter.setdefault(state, []).append(callback)

    def on_exit(self, state: State, callback: StateCallback) -> None:
        """Enregistre un callback appelé à la sortie d'un état."""
        self._on_exit.setdefault(state, []).append(callback)

    # ------------------------------------------------------------------
    # Helpers d'interrogation
    # ------------------------------------------------------------------

    def is_in(self, *states: State) -> bool:
        """Vérifie si l'état courant fait partie des états donnés."""
        return self._state in states

    def __repr__(self) -> str:
        return f"StateMachine(state={self._state.value})"
