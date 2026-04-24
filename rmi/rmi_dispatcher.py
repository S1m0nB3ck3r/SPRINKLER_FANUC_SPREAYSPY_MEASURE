"""
Thread de réception RMI + routage (dispatch) des messages entrants.

Architecture clé : UN SEUL thread lit le socket en continu.
Chaque paquet reçu est classifié puis routé vers :
  - Une Future (réponses aux commandes synchrones)
  - Un callback (réponses aux instructions TP asynchrones)
  - Des handlers d'événements (messages non-sollicités du contrôleur)

Messages non-sollicités (doc B-84184EN/03) :
  - FRC_SystemFault  : erreur pendant exécution TP (§2.2.4)
  - FRC_Terminate    : timeout d'inactivité 60 min (§2.2.3)
  - FRC_AsbnReady    : conversion ASCII→binaire terminée (§5.3.3)
  - FRC_Record       : position enregistrée depuis le TP (§2.5.2)
"""

import threading
import logging
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional
from concurrent.futures import Future

from .rmi_transport import RmiTransport, ConnectionLostError, RmiTransportError

logger = logging.getLogger(__name__)


# ======================================================================
# Classification des messages
# ======================================================================

class MessageType(Enum):
    """Types de messages RMI entrants."""
    COMMAND_REPLY = auto()         # Réponse à un Command (FRC_GetStatus, etc.)
    COMM_REPLY = auto()            # Réponse Communication (FRC_Connect, FRC_Disconnect)
    INSTRUCTION_REPLY = auto()     # Réponse Instruction (FRC_LinearRelative, etc.)
    SYSTEM_FAULT = auto()          # Non-sollicité : FRC_SystemFault
    TERMINATE = auto()             # Non-sollicité : FRC_Terminate
    ASBN_READY = auto()            # Non-sollicité : FRC_AsbnReady
    RECORD = auto()                # Non-sollicité : FRC_Record
    CONNECTION_LOST = auto()       # Connexion TCP perdue
    UNKNOWN = auto()               # Paquet non reconnu


def classify_packet(packet: Dict[str, Any]) -> MessageType:
    """Classifie un paquet RMI selon sa clé primaire.

    Règle RMI (doc §2.1) : la première clé est toujours
    "Command", "Communication" ou "Instruction".
    """
    if "Communication" in packet:
        comm = packet["Communication"]
        if comm == "FRC_SystemFault":
            return MessageType.SYSTEM_FAULT
        if comm == "FRC_Terminate":
            return MessageType.TERMINATE
        if comm == "FRC_AsbnReady":
            return MessageType.ASBN_READY
        return MessageType.COMM_REPLY

    if "Instruction" in packet:
        return MessageType.INSTRUCTION_REPLY

    if "Command" in packet:
        cmd = packet["Command"]
        if cmd == "FRC_Record":
            return MessageType.RECORD
        return MessageType.COMMAND_REPLY

    return MessageType.UNKNOWN


# ======================================================================
# Dispatcher
# ======================================================================

# Type pour les handlers d'événements non-sollicités
EventHandler = Callable[[Dict[str, Any]], None]


class RmiDispatcher:
    """Thread de réception unique + routage des messages RMI.

    Usage :
        dispatcher = RmiDispatcher(transport)
        dispatcher.on(MessageType.SYSTEM_FAULT, my_handler)
        dispatcher.start()

        # Envoyer une commande et attendre la réponse
        future = dispatcher.expect_command("FRC_GetStatus")
        transport.send({"Command": "FRC_GetStatus"})
        reply = future.result(timeout=10)

        # Envoyer une instruction et enregistrer un callback
        dispatcher.expect_instruction(seq_id=5, callback=on_motion_done)
        transport.send(instruction_packet)
    """

    def __init__(self, transport: RmiTransport) -> None:
        self._transport = transport

        # Réponses attendues : Command/Communication → Future
        self._pending_commands: Dict[str, Future] = {}
        self._pending_lock = threading.Lock()

        # Réponses instructions : SequenceID → callback
        self._instruction_callbacks: Dict[int, Callable] = {}
        self._instr_lock = threading.Lock()

        # Handlers pour événements non-sollicités
        self._event_handlers: Dict[MessageType, List[EventHandler]] = {}
        self._handler_lock = threading.Lock()

        # Thread de réception
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Démarre le thread de réception."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._recv_loop, name="RMI-RecvThread", daemon=True
        )
        self._thread.start()
        logger.info("Dispatcher démarré")

    def stop(self) -> None:
        """Arrête le thread de réception."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

        # Annuler toutes les futures en attente
        with self._pending_lock:
            for key, future in self._pending_commands.items():
                if not future.done():
                    future.set_exception(
                        RmiTransportError("Dispatcher arrêté")
                    )
            self._pending_commands.clear()

        with self._instr_lock:
            for seq_id, cb in list(self._instruction_callbacks.items()):
                try:
                    cb({
                        "Instruction": "FRC_Abort",
                        "SequenceID": seq_id,
                        "ErrorID": -2,
                    })
                except Exception:
                    pass
            self._instruction_callbacks.clear()

        logger.info("Dispatcher arrêté")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Enregistrement des attentes
    # ------------------------------------------------------------------

    def expect_command(self, command_name: str) -> Future:
        """Enregistre une Future pour la prochaine réponse à cette commande.

        Args:
            command_name: Nom de la commande (ex: "FRC_GetStatus", "FRC_Connect").

        Returns:
            Future qui sera résolue avec le dict de réponse.
        """
        future: Future = Future()
        with self._pending_lock:
            # Si une future existait déjà pour cette commande, l'annuler
            old = self._pending_commands.pop(command_name, None)
            if old and not old.done():
                old.set_exception(
                    RmiTransportError(f"Commande {command_name} remplacée")
                )
            self._pending_commands[command_name] = future
        return future

    def expect_instruction(self, seq_id: int, callback: Callable) -> None:
        """Enregistre un callback pour la réponse à une instruction TP.

        Le callback reçoit le dict de réponse : callback(reply_packet).

        Args:
            seq_id: SequenceID de l'instruction.
            callback: Fonction appelée à réception de la réponse.
        """
        with self._instr_lock:
            self._instruction_callbacks[seq_id] = callback

    def on(self, msg_type: MessageType, handler: EventHandler) -> None:
        """Enregistre un handler pour un type de message non-sollicité.

        Args:
            msg_type: Type de message (SYSTEM_FAULT, TERMINATE, etc.)
            handler: Fonction callback(packet).
        """
        with self._handler_lock:
            self._event_handlers.setdefault(msg_type, []).append(handler)

    def off(self, msg_type: MessageType, handler: EventHandler) -> None:
        """Désenregistre un handler."""
        with self._handler_lock:
            handlers = self._event_handlers.get(msg_type, [])
            if handler in handlers:
                handlers.remove(handler)

    # ------------------------------------------------------------------
    # Boucle de réception (thread dédié)
    # ------------------------------------------------------------------

    def _recv_loop(self) -> None:
        """Boucle principale du thread de réception.

        Lit des paquets en continu et les dispatche.
        S'arrête proprement sur stop_event ou erreur de connexion.
        """
        logger.info("RecvThread démarré")
        while not self._stop_event.is_set():
            try:
                packet = self._transport.recv_packet(timeout=0.5)
            except ConnectionLostError:
                logger.warning("Connexion perdue détectée par RecvThread")
                self._fire_event(MessageType.CONNECTION_LOST, {})
                break
            except RmiTransportError:
                if not self._stop_event.is_set():
                    logger.warning("Erreur transport dans RecvThread")
                    self._fire_event(MessageType.CONNECTION_LOST, {})
                break
            except TimeoutError:
                continue
            except OSError:
                # Socket fermée pendant l'attente → arrêt normal
                if not self._stop_event.is_set():
                    self._fire_event(MessageType.CONNECTION_LOST, {})
                break
            except Exception as exc:
                # socket.timeout sur Python < 3.11 n'hérite pas de TimeoutError
                import socket as _sock
                if isinstance(exc, _sock.timeout):
                    continue
                logger.exception("Erreur inattendue dans RecvThread: %s", exc)
                continue

            # Classifier et dispatcher
            try:
                self._dispatch(packet)
            except Exception:
                logger.exception("Erreur dispatch du paquet: %s", packet)

        logger.info("RecvThread terminé")

    def _dispatch(self, packet: Dict[str, Any]) -> None:
        """Route un paquet vers son destinataire."""
        msg_type = classify_packet(packet)
        logger.debug("Paquet reçu [%s]: %s", msg_type.name, packet)

        if msg_type == MessageType.COMMAND_REPLY:
            key = packet.get("Command", "")
            self._resolve_pending(key, packet)

        elif msg_type == MessageType.COMM_REPLY:
            key = packet.get("Communication", "")
            self._resolve_pending(key, packet)

        elif msg_type == MessageType.INSTRUCTION_REPLY:
            seq_id = packet.get("SequenceID")
            with self._instr_lock:
                cb = self._instruction_callbacks.pop(seq_id, None)
            if cb:
                try:
                    cb(packet)
                except Exception:
                    logger.exception(
                        "Erreur callback instruction SeqID=%s", seq_id
                    )
            else:
                logger.warning(
                    "Réponse instruction inattendue SeqID=%s", seq_id
                )

        elif msg_type in (
            MessageType.SYSTEM_FAULT,
            MessageType.TERMINATE,
            MessageType.ASBN_READY,
            MessageType.RECORD,
            MessageType.UNKNOWN,
        ):
            # SystemFault contient le SequenceID de l'instruction fautive.
            # Résoudre le callback pour débloquer send_instruction
            # AVANT de notifier les handlers (qui peuvent changer l'état).
            if msg_type == MessageType.SYSTEM_FAULT:
                seq_id = packet.get("SequenceID")
                if seq_id is not None:
                    with self._instr_lock:
                        cb = self._instruction_callbacks.pop(seq_id, None)
                    if cb:
                        fault_reply = {
                            "Instruction": "FRC_SystemFault",
                            "SequenceID": seq_id,
                            "ErrorID": -1,
                        }
                        try:
                            cb(fault_reply)
                        except Exception:
                            logger.exception(
                                "Erreur callback SystemFault SeqID=%s",
                                seq_id,
                            )
            self._fire_event(msg_type, packet)

    def _resolve_pending(self, key: str, packet: Dict[str, Any]) -> None:
        """Résout la Future en attente pour une commande/communication."""
        with self._pending_lock:
            future = self._pending_commands.pop(key, None)
        if future and not future.done():
            future.set_result(packet)
        elif not future:
            logger.warning("Réponse commande inattendue: %s", key)

    def _fire_event(self, msg_type: MessageType, packet: Dict[str, Any]) -> None:
        """Appelle tous les handlers enregistrés pour un type d'événement."""
        with self._handler_lock:
            handlers = list(self._event_handlers.get(msg_type, []))
        for handler in handlers:
            try:
                handler(packet)
            except Exception:
                logger.exception(
                    "Erreur handler événement %s", msg_type.name
                )
