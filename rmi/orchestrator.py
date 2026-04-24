"""
Orchestrateur — logique métier de haut niveau pour SpraySpy.

Coordonne le cycle de vie RMI complet :
  - Connexion → attente AUTO+TP OFF → FRC_Initialize
  - Mouvements manuels
  - Séquences 3D paramétrables
  - Recovery automatique (HOLD → Reset → Continue) selon doc §3.5
  - Arrêt d'urgence (FRC_Abort)
  - Polling positions en temps réel

Ce module fait le pont entre le RmiClient (protocole) et la GUI (affichage).
Il communique avec la GUI via un EventBus (queue.Queue thread-safe).
"""

import queue
import threading
import time
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import Any, Callable, Dict, Optional

from .rmi_client import RmiClient, RmiCommandError, RmiNotConnectedError, describe_error
from .state_machine import StateMachine, State, InvalidTransition
from .robot_config import RobotConfig

logger = logging.getLogger(__name__)


# ======================================================================
# Événements vers la GUI
# ======================================================================

class EventType(Enum):
    """Types d'événements envoyés de l'orchestrateur vers la GUI."""
    LOG = auto()
    STATUS = auto()
    POSITION = auto()
    STATE_CHANGED = auto()
    SEQUENCE_PROGRESS = auto()
    ERROR = auto()


@dataclass
class Event:
    """Événement transmis via l'EventBus."""
    type: EventType
    data: Any = None
    level: str = "INFO"
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class EventBus:
    """File d'événements thread-safe entre l'orchestrateur et la GUI."""

    def __init__(self, maxsize: int = 500) -> None:
        self._queue: queue.Queue[Event] = queue.Queue(maxsize=maxsize)

    def put(self, event: Event) -> None:
        """Publie un événement (non-bloquant, drop si plein)."""
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            pass  # Drop les événements anciens plutôt que bloquer

    def get_all(self) -> list:
        """Récupère tous les événements en attente (non-bloquant)."""
        events = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events

    @property
    def queue(self) -> queue.Queue:
        return self._queue


# ======================================================================
# Orchestrateur
# ======================================================================

class Orchestrator:
    """Coordonne le robot Fanuc via RMI avec gestion d'état formelle.

    Usage typique (depuis la GUI) :
        orch = Orchestrator()
        orch.connect_async(config)      # Lance en thread
        orch.move_relative_async(...)   # Lance en thread
        orch.run_sequence_async(...)    # Lance en thread
        orch.emergency_stop()           # Immédiat
        orch.disconnect_async()         # Lance en thread
    """

    def __init__(self) -> None:
        self.client = RmiClient()
        self.fsm = StateMachine()
        self.event_bus = EventBus()
        # Chaque changement d'état (transition ou force_state) émet automatiquement
        # un événement STATE_CHANGED vers la GUI via l'EventBus.
        self.fsm.on_transition(self._emit_state)

        # Configuration courante
        self.config: Optional[RobotConfig] = None

        # Contrôle des threads
        self._cancel_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._poller_thread: Optional[threading.Thread] = None
        self._poller_stop = threading.Event()
        # Verrou pour éviter le traitement multiple de FRC_SystemFault
        self._fault_in_progress = threading.Event()

        # Position de référence (SET_REF / GO_TO_REF)
        self._ref_cartesian: Optional[Dict[str, Any]] = None
        self._ref_joints: Optional[Dict[str, Any]] = None

        # Abonner le client aux événements RMI
        self.client.set_on_system_fault(self._on_system_fault)
        self.client.set_on_connection_lost(self._on_connection_lost)
        self.client.set_on_log(self._on_client_log)

    # ------------------------------------------------------------------
    # Événements vers la GUI
    # ------------------------------------------------------------------

    def _emit_log(self, msg: str, level: str = "INFO") -> None:
        """Émet un événement de log."""
        ts = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{ts}] {level:8s} | {msg}"
        logger.log(getattr(logging, level, logging.INFO), msg)
        self.event_bus.put(Event(EventType.LOG, formatted, level))

    def _emit_status(self, msg: str, status: str = "neutral") -> None:
        """Émet un événement de statut."""
        self.event_bus.put(Event(EventType.STATUS, {"msg": msg, "status": status}))

    def _emit_state(self, old: State, new: State) -> None:
        """Émet un événement de changement d'état."""
        self.event_bus.put(Event(
            EventType.STATE_CHANGED,
            {"old": old.value, "new": new.value}
        ))

    def _emit_position(self, data: Dict[str, Any]) -> None:
        """Émet un événement de position."""
        self.event_bus.put(Event(EventType.POSITION, data))

    def _emit_progress(self, current: int, total: int) -> None:
        """Émet un événement de progression séquence."""
        pct = (current / total * 100) if total > 0 else 0
        self.event_bus.put(Event(
            EventType.SEQUENCE_PROGRESS,
            {"current": current, "total": total, "percent": pct}
        ))

    # ------------------------------------------------------------------
    # Callbacks du client RMI
    # ------------------------------------------------------------------

    def _on_system_fault(self, packet: Dict[str, Any]) -> None:
        """Réaction à FRC_SystemFault — transition vers HOLD."""
        # Déduplique : plusieurs instructions en vol génèrent plusieurs notifications
        if self.fsm.state == State.HOLD:
            return
        seq_id = packet.get("SequenceID", "?")
        self._emit_log(
            f"⚠️ SYSTEM FAULT (SequenceID={seq_id}) — entering HOLD",
            "WARNING"
        )
        # Signaler l'annulation pour débloquer les boucles de séquence
        self._cancel_event.set()
        try:
            self.fsm.transition(State.HOLD)
        except InvalidTransition:
            self.fsm.force_state(State.HOLD)
        # Le callback on_transition notifie la GUI automatiquement
        self._emit_status(
            "⚠️ System Fault — Robot in HOLD\n"
            "Disconnect and reconnect to recover.",
            "error"
        )
        # Lire le détail de l'erreur depuis le contrôleur (thread séparé
        # pour éviter un deadlock avec le Dispatcher)
        threading.Thread(
            target=self._log_controller_error, daemon=True,
            name="Orch-ReadErr"
        ).start()

    def _on_connection_lost(self, packet: Dict[str, Any]) -> None:
        """Réaction à la perte de connexion."""
        self._emit_log("❌ Connection lost with controller", "ERROR")
        self._cancel_event.set()
        self._poller_stop.set()
        self.fsm.force_state(State.DISCONNECTED)
        self._emit_status("Connection lost", "error")

    def _on_client_log(self, msg: str, level: str) -> None:
        """Forward les logs du client."""
        self._emit_log(msg, level)

    def _log_controller_error(self) -> None:
        """Lit la dernière erreur du contrôleur après un SystemFault et la logue."""
        try:
            time.sleep(0.3)  # Laisser le contrôleur se stabiliser
            reply = self.client.read_error()
            err_id = reply.get("ErrorID", 0)
            if err_id and err_id != 0:
                self._emit_log(
                    f"   Cause: {describe_error(err_id)} (ErrorID={err_id})",
                    "WARNING"
                )
            msg = reply.get("ErrorMessage") or reply.get("Message") or reply.get("Text")
            if msg:
                self._emit_log(f"   Controller: {msg}", "WARNING")
        except Exception:
            pass  # Ne pas crasher si read_error échoue en état de fault

    # ------------------------------------------------------------------
    # Helpers FSM
    # ------------------------------------------------------------------

    def _safe_transition(self, new_state: State) -> bool:
        """Tente une transition FSM, retourne False si invalide."""
        try:
            self.fsm.transition(new_state)  # callback on_transition émet l'état
            return True
        except InvalidTransition as e:
            self._emit_log(f"Invalid transition: {e}", "WARNING")
            return False

    # ------------------------------------------------------------------
    # Connexion (séquence complète)
    # ------------------------------------------------------------------

    def connect_async(self, config: RobotConfig) -> None:
        """Lance la connexion + préparation en thread."""
        # Attendre que l'ancien thread se termine (max 2s)
        if self._worker_thread and self._worker_thread.is_alive():
            self._cancel_event.set()
            self._worker_thread.join(timeout=2.0)
            if self._worker_thread.is_alive():
                self._emit_log("Previous thread still active — forced", "WARNING")
        # Reset pour la nouvelle connexion
        self._cancel_event.clear()
        self.config = config
        self._worker_thread = threading.Thread(
            target=self._connect_sequence, args=(config,),
            name="Orch-Connect", daemon=True
        )
        self._worker_thread.start()

    def _connect_sequence(self, config: RobotConfig) -> None:
        """Séquence complète : connect → wait → initialize."""
        try:
            # 1. Connexion TCP + FRC_Connect
            self._safe_transition(State.CONNECTING)
            self._emit_status("Connecting...", "connecting")

            reply = self.client.connect(
                config.robot_ip,
                config.rmi_init_port,
                connect_timeout=config.connect_timeout,
                command_timeout=config.command_timeout,
            )
            self._safe_transition(State.CONNECTED)
            self._emit_log(
                f"✅ Connected (MajorVersion={reply.get('MajorVersion', '?')})",
                "INFO"
            )

            # 2. Démarrer le polling positions
            self._start_position_polling(config)

            # 3. Attendre AUTO + TP OFF
            self._safe_transition(State.WAIT_AUTO)
            self._emit_status("Waiting for AUTO + TP OFF mode...", "waiting")
            self._wait_for_auto(config)

            # 4. FRC_Initialize
            self._safe_transition(State.INITIALIZING)
            self._emit_status("RMI initialization...", "connecting")
            self.client.initialize(group_mask=config.group_mask)
            time.sleep(0.3)  # Attendre démarrage RMI_MOVE

            # 5. FRC_SetUFrameUTool
            self.client.set_uframe_utool(
                uframe=config.uframe_number,
                utool=config.utool_number
            )

            # 6. Speed override — always 100%
            self.client.set_override(100)
            self._emit_log("Speed override: 100%", "INFO")

            # 7. Prêt
            self._safe_transition(State.IDLE)
            self._emit_status("Ready", "connected")
            self._emit_log("✅ Robot ready for sequence", "INFO")

        except RmiCommandError as e:
            self._emit_log(f"❌ RMI error: {e} ({describe_error(e.error_id)})", "ERROR")
            self._emit_status(f"Error: {e}", "error")
            self._handle_fatal_error()

        except Exception as e:
            self._emit_log(f"❌ Connection error: {e}", "ERROR")
            self._emit_status(f"Error: {e}", "error")
            self._handle_fatal_error()

    def _wait_for_auto(self, config: RobotConfig) -> None:
        """Boucle d'attente AUTO + TP OFF + ServoReady."""
        while not self._cancel_event.is_set():
            try:
                status = self.client.get_status(timeout=3.0)
                tp_mode = status.get("TPMode", 1)
                servo = status.get("ServoReady", 0)

                if tp_mode == 0 and servo == 1:
                    self._emit_log("AUTO mode + TP OFF + Servo OK", "INFO")
                    return

                mode_str = "TP ON" if tp_mode == 1 else "TP OFF"
                servo_str = "Servo ON" if servo == 1 else "Servo OFF"
                self._emit_status(
                    f"Waiting... ({mode_str}, {servo_str})", "waiting"
                )
            except Exception:
                pass
            time.sleep(0.5)

        raise RuntimeError("Auto wait cancelled")

    # ------------------------------------------------------------------
    # Déconnexion
    # ------------------------------------------------------------------

    def disconnect_async(self) -> None:
        """Lance la déconnexion en thread."""
        if self._worker_thread and self._worker_thread.is_alive():
            self._cancel_event.set()
            self._worker_thread.join(timeout=2.0)
        self._worker_thread = threading.Thread(
            target=self._disconnect_sequence,
            name="Orch-Disconnect", daemon=True
        )
        self._worker_thread.start()

    def _disconnect_sequence(self) -> None:
        """Déconnexion propre."""
        try:
            self._cancel_event.set()
            self._stop_position_polling()
            self._safe_transition(State.DISCONNECTING)
            self._emit_status("Disconnecting...", "connecting")

            self.client.disconnect()

            self._safe_transition(State.DISCONNECTED)
            self._emit_status("Disconnected", "disconnected")
            self._emit_log("✅ Disconnected successfully", "INFO")

        except Exception as e:
            self._emit_log(f"Disconnect error: {e}", "ERROR")
            self.fsm.force_state(State.DISCONNECTED)

    # ------------------------------------------------------------------
    # Mouvement manuel
    # ------------------------------------------------------------------

    def move_relative_async(self, axis: str, distance: float,
                            speed: float, term_type: str = "FINE",
                            term_value: int = 0) -> None:
        """Lance un mouvement relatif en thread."""
        if self._worker_thread and self._worker_thread.is_alive():
            self._cancel_event.set()
            self._worker_thread.join(timeout=2.0)
            if self._worker_thread.is_alive():
                self._emit_log("Previous operation not finished", "WARNING")
                return
        self._cancel_event.clear()
        self._worker_thread = threading.Thread(
            target=self._move_relative,
            args=(axis, distance, speed, term_type, term_value),
            name="Orch-Move", daemon=True
        )
        self._worker_thread.start()

    def _move_relative(self, axis: str, distance: float, speed: float,
                       term_type: str = "FINE", term_value: int = 0) -> None:
        """Exécute un mouvement relatif unique (cartésien ou articulaire)."""
        if not self.config:
            return
        try:
            self._safe_transition(State.MOVING)

            # Axes angulaires (bout de bras) → FRC_JointRelativeJRep
            joint_map = {"θ1 (J4)": "J4", "θ2 (J5)": "J5"}
            if axis in joint_map:
                joint = joint_map[axis]
                self._emit_log(
                    f"Rotating {axis}: {distance:+.2f}° at {int(speed)}%", "INFO"
                )
                self._emit_status(f"Rotating {axis}...", "running")
                reply = self.client.joint_relative_jrep(
                    {joint: distance},
                    speed_pct=int(speed),
                    timeout=self.config.motion_timeout,
                )
            else:
                # Axes cartésiens X / Y / Z → FRC_JointRelative
                # Interpolation articulaire, vitesse en %, évite MOTN-018
                self._emit_log(
                    f"Moving {axis}: {distance}mm at {speed}% [{term_type}"
                    + (f" {term_value}" if term_type == "CNT" else "") + "]",
                    "INFO"
                )
                self._emit_status(f"Moving {axis}...", "running")
                dx, dy, dz = {"X": (distance, 0, 0),
                              "Y": (0, distance, 0),
                              "Z": (0, 0, distance)}.get(axis, (0, 0, 0))
                reply = self.client.joint_relative(
                    dx, dy, dz, speed,
                    utool=self.config.utool_number,
                    uframe=self.config.uframe_number,
                    term_type=term_type,
                    term_value=term_value,
                    timeout=self.config.motion_timeout,
                )

            error_id = reply.get("ErrorID", 0)
            if error_id != 0:
                desc = describe_error(error_id) if error_id > 0 else "SystemFault"
                self._emit_log(f"❌ Motion error: {desc}", "ERROR")
                self._handle_motion_error()
                return

            self._safe_transition(State.IDLE)
            self._emit_log(f"✅ {axis} motion complete", "INFO")
            self._emit_status("Ready", "connected")

        except RmiCommandError as e:
            self._emit_log(f"❌ Motion error: {e}", "ERROR")
            self._handle_motion_error()
        except Exception as e:
            self._emit_log(f"❌ Motion error: {e}", "ERROR")
            self._handle_motion_error()

    # ------------------------------------------------------------------
    # Rotation articulaire manuelle
    # ------------------------------------------------------------------

    def joint_rotate_async(self, joint: str, angle: float,
                           speed_pct: int) -> None:
        """Lance une rotation articulaire relative en thread."""
        if self._worker_thread and self._worker_thread.is_alive():
            self._cancel_event.set()
            self._worker_thread.join(timeout=2.0)
            if self._worker_thread.is_alive():
                self._emit_log("Previous operation not finished", "WARNING")
                return
        self._cancel_event.clear()
        self._worker_thread = threading.Thread(
            target=self._joint_rotate, args=(joint, angle, speed_pct),
            name="Orch-JointRotate", daemon=True
        )
        self._worker_thread.start()

    def _joint_rotate(self, joint: str, angle: float,
                      speed_pct: int) -> None:
        """Exécute une rotation articulaire relative unique."""
        if not self.config:
            return
        try:
            self._safe_transition(State.MOVING)
            self._emit_log(
                f"JOINT_RELATIVE {joint}: {angle:+.2f}° à {speed_pct}%", "INFO"
            )
            self._emit_status(f"Rotating {joint}...", "running")

            joints = {joint: angle}
            reply = self.client.joint_relative_jrep(
                joints, speed_pct=speed_pct,
                timeout=self.config.motion_timeout,
            )

            error_id = reply.get("ErrorID", 0)
            if error_id != 0:
                desc = describe_error(error_id) if error_id > 0 else "SystemFault"
                self._emit_log(f"❌ Rotation error: {desc}", "ERROR")
                self._handle_motion_error()
                return

            self._safe_transition(State.IDLE)
            self._emit_log(f"✅ {joint} rotation complete", "INFO")
            self._emit_status("Ready", "connected")

        except RmiCommandError as e:
            self._emit_log(f"❌ Rotation error: {e}", "ERROR")
            self._handle_motion_error()
        except Exception as e:
            self._emit_log(f"❌ Rotation error: {e}", "ERROR")
            self._handle_motion_error()

    # ------------------------------------------------------------------
    # Position de référence (SET_REF / GO_TO_REF)
    # ------------------------------------------------------------------

    @property
    def has_ref_position(self) -> bool:
        """Indique si une position de référence a été enregistrée."""
        return self._ref_cartesian is not None

    def set_ref_position(self) -> None:
        """Enregistre la position cartésienne + articulaire actuelle comme référence."""
        try:
            cart_reply = self.client.read_position()
            joint_reply = self.client.read_joint_angles()
            self._ref_cartesian = cart_reply
            self._ref_joints = joint_reply
            pos = cart_reply.get("Position", {})
            self._emit_log(
                f"📌 REF saved: X={pos.get('X', 0):.2f} "
                f"Y={pos.get('Y', 0):.2f} Z={pos.get('Z', 0):.2f}",
                "INFO",
            )
            self._emit_status("Reference position saved", "success")
        except Exception as e:
            self._emit_log(f"❌ SET_REF error: {e}", "ERROR")

    def set_ref_position_async(self) -> None:
        """Enregistre la position de référence en thread (non-bloquant)."""
        threading.Thread(
            target=self.set_ref_position, name="Orch-SetRef", daemon=True
        ).start()

    def go_to_ref_async(self) -> None:
        """Lance le retour à la position de référence en thread."""
        if self._ref_cartesian is None:
            self._emit_log("⚠️ No reference position saved", "WARNING")
            return
        if self._worker_thread and self._worker_thread.is_alive():
            self._cancel_event.set()
            self._worker_thread.join(timeout=2.0)
            if self._worker_thread.is_alive():
                self._emit_log("Previous operation not finished", "WARNING")
                return
        self._cancel_event.clear()
        self._worker_thread = threading.Thread(
            target=self._go_to_ref, name="Orch-GoToRef", daemon=True
        )
        self._worker_thread.start()

    def _go_to_ref(self) -> None:
        """Exécute le retour à la position de référence (cartésien absolu)."""
        if not self.config or not self._ref_cartesian:
            return
        try:
            self._safe_transition(State.MOVING)
            pos = self._ref_cartesian.get("Position", {})
            cfg = self._ref_cartesian.get("Configuration", {})
            self._emit_log(
                f"↩ GO_TO_REF: X={pos.get('X', 0):.2f} "
                f"Y={pos.get('Y', 0):.2f} Z={pos.get('Z', 0):.2f}",
                "INFO",
            )
            self._emit_status("Returning to reference position...", "running")

            reply = self.client.linear_motion(
                x=pos.get("X", 0), y=pos.get("Y", 0), z=pos.get("Z", 0),
                w=pos.get("W", 0), p=pos.get("P", 0), r=pos.get("R", 0),
                speed=max(1, int(self.config.default_speed * 250 / 100)),
                speed_type="mmSec",
                utool=self.config.utool_number,
                uframe=self.config.uframe_number,
                config=cfg if cfg else None,
                timeout=self.config.motion_timeout,
            )

            error_id = reply.get("ErrorID", 0)
            if error_id != 0:
                desc = describe_error(error_id) if error_id > 0 else "SystemFault"
                self._emit_log(f"❌ GO_TO_REF error: {desc}", "ERROR")
                self._handle_motion_error()
                return

            self._safe_transition(State.IDLE)
            self._emit_log("✅ Robot at reference position", "INFO")
            self._emit_status("Reference position reached", "success")

        except RmiCommandError as e:
            self._emit_log(f"❌ GO_TO_REF error: {e}", "ERROR")
            self._handle_motion_error()
        except Exception as e:
            self._emit_log(f"❌ GO_TO_REF error: {e}", "ERROR")
            self._handle_motion_error()

    # ------------------------------------------------------------------
    # Séquence 3D
    # ------------------------------------------------------------------

    def run_sequence_async(self, config: RobotConfig) -> None:
        """Lance la séquence 3D en thread."""
        if self._worker_thread and self._worker_thread.is_alive():
            self._cancel_event.set()
            self._worker_thread.join(timeout=2.0)
            if self._worker_thread.is_alive():
                self._emit_log("Previous operation not finished", "WARNING")
                return
        self.config = config
        self._cancel_event.clear()
        self._worker_thread = threading.Thread(
            target=self._run_sequence, args=(config,),
            name="Orch-Sequence", daemon=True
        )
        self._worker_thread.start()

    def _run_sequence(self, config: RobotConfig) -> None:
        """Exécute la séquence 3D dans un thread worker."""
        try:
            self.fsm.transition(State.SEQUENCING)
            self._emit_log("🚀 Starting 3D sequence", "START")

            speed = config.default_speed
            dz = config.z_step
            dy = config.y_step
            dx = config.x_step

            total = config.z_number * config.y_number * config.x_number
            current = 0

            def _move(mx: float, my: float, mz: float, label: str) -> None:
                """Mouvement articulaire relatif avec vérification ErrorID."""
                reply = self.client.joint_relative(
                    mx, my, mz, speed,
                    utool=config.utool_number, uframe=config.uframe_number,
                    timeout=config.motion_timeout,
                )
                if reply.get("ErrorID", 0) != 0:
                    raise RuntimeError(
                        f"FRC_JointRelative échoué [{label}]: "
                        f"ErrorID={reply['ErrorID']}"
                    )

            z_steps_done = 0
            for iz in range(config.z_number):
                if self._cancel_event.is_set():
                    break

                if iz > 0:
                    self._emit_log(f"  MOVE Z {dz:+.3f}mm (plane {iz+1}/{config.z_number})", "INFO")
                    _move(0, 0, dz, f"Z plane {iz+1}")
                    self._emit_log("  ✅ Z motion complete", "SUCCESS")
                    z_steps_done = iz

                y_steps_done = 0
                for iy in range(config.y_number):
                    if self._cancel_event.is_set():
                        break

                    if iy > 0:
                        self._emit_log(f"  MOVE Y {dy:+.3f}mm (row {iy+1}/{config.y_number})", "INFO")
                        _move(0, dy, 0, f"Y row {iy+1}")
                        self._emit_log("  ✅ Y motion complete", "SUCCESS")
                        y_steps_done = iy

                    x_steps_done = 0
                    for ix in range(config.x_number):
                        if self._cancel_event.is_set():
                            break

                        if ix > 0:
                            self._emit_log(f"    MOVE X {dx:+.3f}mm (pt {ix+1}/{config.x_number})", "INFO")
                            _move(dx, 0, 0, f"X pt {ix+1}")
                            self._emit_log("    ✅ X motion complete", "SUCCESS")
                            x_steps_done = ix

                        current += 1
                        self._emit_progress(current, total)
                        self._trigger_capture(config)

                    # Retour X — distance réelle parcourue, annulé si cancel
                    if x_steps_done > 0 and not self._cancel_event.is_set():
                        retour_x = -dx * x_steps_done
                        self._emit_log(f"  ↩ Return X {retour_x:+.3f}mm", "INFO")
                        _move(retour_x, 0, 0, "Return X")

                # Retour Y — distance réelle parcourue, annulé si cancel
                if y_steps_done > 0 and not self._cancel_event.is_set():
                    retour_y = -dy * y_steps_done
                    self._emit_log(f"  ↩ Return Y {retour_y:+.3f}mm", "INFO")
                    _move(0, retour_y, 0, "Return Y")

            # Retour Z — distance réelle parcourue, annulé si cancel
            if z_steps_done > 0 and not self._cancel_event.is_set():
                retour_z = -dz * z_steps_done
                self._emit_log(f"  ↩ Return Z {retour_z:+.3f}mm", "INFO")
                _move(0, 0, retour_z, "Return Z")

            if self._cancel_event.is_set():
                self._emit_log("⚠️ Sequence cancelled by user", "WARNING")
                self._safe_transition(State.IDLE)
            else:
                self._emit_log("✅ 3D sequence completed successfully", "SUCCESS")
                self._safe_transition(State.IDLE)

        except Exception as e:
            self._emit_log(f"❌ Sequence error: {e}", "ERROR")
            self._handle_motion_error(e)

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def _trigger_capture(self, config: RobotConfig) -> None:
        """Trigger a SpraySpy capture: settle delay → DO ON → wait acquisition → DO OFF.

        If capture_do_port == 0, DO is skipped and only the wait is performed.
        """
        # Settle delay between end of motion and DO trigger
        if config.capture_delay_ms > 0:
            delay_s = config.capture_delay_ms / 1000.0
            self._emit_log(f"    WAIT SETTLE ({delay_s:.3f}s)", "INFO")
            time.sleep(delay_s)

        port = config.capture_do_port
        if port > 0:
            self._emit_log(f"    DO[{port}] → ON", "INFO")
            self.client.write_dout(port, "ON")

        self._emit_log(f"    WAIT ACQUISITION ({config.acq_time_s:.2f}s)", "INFO")
        time.sleep(config.acq_time_s)

        if port > 0:
            self._emit_log(f"    DO[{port}] → OFF", "INFO")
            self.client.write_dout(port, "OFF")

    # ------------------------------------------------------------------
    # Arrêt d'urgence
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Séquence CSV
    # ------------------------------------------------------------------

    def run_csv_sequence_async(self, steps: list,
                               config: RobotConfig) -> None:
        """Lance une séquence CSV en thread.

        Args:
            steps: Liste de dicts [{"X": .., "Y": .., "Z": .., "J4": .., "J5": ..}, ...]
            config: Configuration robot courante.
        """
        if self._worker_thread and self._worker_thread.is_alive():
            self._cancel_event.set()
            self._worker_thread.join(timeout=2.0)
            if self._worker_thread.is_alive():
                self._emit_log("Previous operation not finished", "WARNING")
                return
        self._cancel_event.clear()
        self._worker_thread = threading.Thread(
            target=self._run_csv_sequence, args=(steps, config),
            name="Orch-CSVSeq", daemon=True
        )
        self._worker_thread.start()

    def _run_csv_sequence(self, steps: list, config: RobotConfig) -> None:
        """Exécute la séquence CSV dans un thread worker."""
        try:
            self.fsm.transition(State.SEQUENCING)
            total = len(steps)
            self._emit_log(f"🚀 Starting CSV sequence: {total} steps", "START")

            speed = config.default_speed

            for i, step in enumerate(steps):
                if self._cancel_event.is_set():
                    break

                self._emit_log(f"--- Step {i+1}/{total} ---", "INFO")

                # Mouvement linéaire relatif (X, Y, Z)
                dx = step.get("X", 0.0)
                dy = step.get("Y", 0.0)
                dz = step.get("Z", 0.0)

                if dx != 0.0 or dy != 0.0 or dz != 0.0:
                    increments = {}
                    parts = []
                    if dx != 0.0:
                        increments["X"] = dx
                        parts.append(f"X={dx:+.3f}")
                    if dy != 0.0:
                        increments["Y"] = dy
                        parts.append(f"Y={dy:+.3f}")
                    if dz != 0.0:
                        increments["Z"] = dz
                        parts.append(f"Z={dz:+.3f}")
                    self._emit_log(f"    MOVE {' '.join(parts)}mm", "INFO")
                    result = self.client.joint_relative(
                        increments.get("X", 0.0), increments.get("Y", 0.0),
                        increments.get("Z", 0.0), speed,
                        utool=config.utool_number, uframe=config.uframe_number,
                        timeout=config.motion_timeout,
                    )
                    if result.get("ErrorID", 0) != 0:
                        raise RuntimeError(
                            f"FRC_JointRelative échoué: ErrorID={result['ErrorID']}"
                        )
                    self._emit_log("    ✅ Motion complete", "SUCCESS")

                if self._cancel_event.is_set():
                    break

                # Rotation articulaire (J4, J5)
                dj4 = step.get("J4", 0.0)
                dj5 = step.get("J5", 0.0)

                if dj4 != 0.0 or dj5 != 0.0:
                    joint_inc = {}
                    parts = []
                    if dj4 != 0.0:
                        joint_inc["J4"] = dj4
                        parts.append(f"J4={dj4:+.2f}°")
                    if dj5 != 0.0:
                        joint_inc["J5"] = dj5
                        parts.append(f"J5={dj5:+.2f}°")
                    self._emit_log(f"    JOINT {' '.join(parts)}", "INFO")
                    result = self.client.joint_relative_jrep(
                        joint_inc, speed_pct=10, timeout=config.motion_timeout
                    )
                    if result.get("ErrorID", 0) != 0:
                        raise RuntimeError(
                            f"FRC_JointRelativeJRep échoué: ErrorID={result['ErrorID']}"
                        )
                    self._emit_log("    ✅ Rotation complete", "SUCCESS")

                if self._cancel_event.is_set():
                    break

                # Capture
                self._trigger_capture(config)

                self._emit_progress(i + 1, total)

            if self._cancel_event.is_set():
                self._emit_log("⚠️ CSV sequence cancelled by user", "WARNING")
                self._safe_transition(State.IDLE)
            else:
                self._emit_log("✅ CSV sequence completed successfully", "SUCCESS")
                self._safe_transition(State.IDLE)

        except Exception as e:
            self._emit_log(f"❌ Sequence error: {e}", "ERROR")
            self._handle_motion_error(e)

    def abort_sequence(self) -> None:
        """Annule la séquence en cours (arrêt doux, sans déconnexion)."""
        if self.fsm.state == State.SEQUENCING:
            self._cancel_event.set()
            self._emit_log("🛑 Abort sequence requested", "WARNING")
        else:
            self._emit_log("⚠️ No sequence running to abort", "WARNING")

    def emergency_stop(self) -> None:
        """Arrêt d'urgence immédiat (FRC_Abort + déconnexion complète).

        Appelable depuis n'importe quel thread (y compris le MainThread GUI).
        """
        self._cancel_event.set()
        self._emit_log("⏹️ EMERGENCY STOP", "ERROR")
        try:
            self.client.abort()
            self._emit_log("FRC_Abort sent", "INFO")
        except Exception as e:
            self._emit_log(f"FRC_Abort error: {e}", "ERROR")
        self._abort_and_disconnect()

    def _abort_and_disconnect(self) -> None:
        """FRC_Abort (si possible) + déconnexion complète → DISCONNECTED."""
        self._cancel_event.set()
        self._stop_position_polling()
        try:
            self.client.disconnect()
        except Exception:
            pass
        self.fsm.force_state(State.DISCONNECTED)
        self._emit_status("Disconnected (after stop)", "disconnected")
        self._emit_log("🔌 RMI disconnected", "INFO")

    # ------------------------------------------------------------------
    # Recovery (doc §3.3 et §3.5)
    # ------------------------------------------------------------------

    def _handle_motion_error(self, error: Exception | None = None) -> None:
        """Gestion d'erreur mouvement — tente un retour IDLE, sinon déconnecte."""
        # Si _cancel_event est levé, un gestionnaire spécialisé (_on_system_fault
        # ou emergency_stop) gère déjà la transition FSM — ne pas interférer.
        if self._cancel_event.is_set():
            return
        try:
            # Tenter de rester connecté (erreur récupérable)
            if self._safe_transition(State.IDLE):
                self._emit_log("⚠️ Motion error — returned to IDLE", "WARNING")
                self._emit_status("Error recovered — Ready", "connected")
            else:
                # Transition refusée → forcer IDLE si la connexion est active
                if self.client.is_connected:
                    self.fsm.force_state(State.IDLE)
                    self._emit_log("⚠️ Motion error — forced IDLE", "WARNING")
                    self._emit_status("Error recovered — Ready", "connected")
                else:
                    raise ConnectionError("Connection lost")
        except Exception:
            # Connexion perdue → déconnexion complète
            self._emit_log("Motion error — RMI disconnect", "ERROR")
            self._abort_and_disconnect()

    def _handle_fatal_error(self) -> None:
        """Erreur fatale — déconnexion complète."""
        self._abort_and_disconnect()

    def recover_async(self) -> None:
        """Lance la procédure de récupération depuis HOLD en thread.

        Séquence : FRC_Reset → FRC_Continue → IDLE
        """
        if self.fsm.state != State.HOLD:
            self._emit_log("⚠️ recover_async appelé hors HOLD — ignoré", "WARNING")
            return
        self._cancel_event.clear()
        # Ne pas vider _fault_in_progress ici : des notifications en attente
        # dans le Dispatcher pourraient retourner en HOLD pendant la recovery.
        # On le vide uniquement après la transition IDLE réussie dans _recover().
        self._worker_thread = threading.Thread(
            target=self._recover, name="Orch-Recover", daemon=True
        )
        self._worker_thread.start()

    def _recover(self) -> None:
        """Exécute FRC_Reset + FRC_Continue pour sortir de HOLD."""
        try:
            self._emit_log("🔄 Recovering from HOLD...", "INFO")
            self._emit_status("Recovering...", "connecting")

            # FRC_Reset — efface l'alarme sur le contrôleur
            self.client.reset(timeout=10.0)
            self._emit_log("   FRC_Reset OK", "INFO")

            # FRC_Continue — reprend RMI_MOVE
            self.client.continue_program(timeout=5.0)
            self._emit_log("   FRC_Continue OK", "INFO")

            self._safe_transition(State.IDLE)
            # Maintenant que nous sommes IDLE, autoriser à nouveau
            # la détection de SystemFault
            self._fault_in_progress.clear()
            self._emit_status("Ready", "connected")
            self._emit_log("✅ Recovery successful — ready", "INFO")

        except Exception as e:
            self._emit_log(f"❌ Recovery failed: {e}", "ERROR")
            self._emit_status(f"Recovery failed: {e}\nTry Disconnect.", "error")
            # Si le reset échoue, passer en ERROR pour bloquer tout mouvement
            try:
                self.fsm.transition(State.ERROR)
            except InvalidTransition:
                pass

    # ------------------------------------------------------------------
    # Polling positions en temps réel
    # ------------------------------------------------------------------

    def _start_position_polling(self, config: RobotConfig) -> None:
        """Démarre le polling des positions (thread dédié)."""
        self._poller_stop.clear()
        self._poller_thread = threading.Thread(
            target=self._position_poll_loop,
            args=(config,),
            name="Orch-Poller", daemon=True
        )
        self._poller_thread.start()

    def _stop_position_polling(self) -> None:
        """Arrête le polling positions."""
        self._poller_stop.set()
        if self._poller_thread:
            self._poller_thread.join(timeout=2.0)
            self._poller_thread = None

    def _position_poll_loop(self, config: RobotConfig) -> None:
        """Boucle de lecture positions + statut (10x/s)."""
        group = config.group_mask  # Pour groupe simple, group_mask = 1
        interval = config.poll_interval

        while not self._poller_stop.is_set() and self.client.is_connected:
            try:
                # Lecture statut
                status = self.client.get_status(timeout=2.0)

                # Lecture position cartésienne
                pos_reply = self.client.read_position(group, timeout=2.0)

                # Lecture angles articulaires
                joints_reply = self.client.read_joint_angles(group, timeout=2.0)

                # Extraire X, Y, Z
                pos_data = self._extract_position(pos_reply)
                joint_data = self._extract_joints(joints_reply)

                # Mode et servo
                tp_mode = status.get("TPMode", 1)
                servo_ready = status.get("ServoReady", 0)
                rmi_status = status.get("RMIMotionStatus", 0)
                override = status.get("Override", "?")

                self._emit_position({
                    "position": pos_data,
                    "joints": joint_data,
                    "tp_mode": tp_mode,
                    "servo_ready": servo_ready,
                    "rmi_status": rmi_status,
                    "override": override,
                })

                # Mettre à jour le statut visuel si en IDLE
                if self.fsm.is_in(State.IDLE, State.CONNECTED,
                                   State.WAIT_AUTO, State.WAIT_MANUAL):
                    mode = "AUTO" if tp_mode == 0 else "TP"
                    servo = "Servo ON" if servo_ready == 1 else "Servo OFF"
                    x = pos_data.get("X", "?")
                    y = pos_data.get("Y", "?")
                    z = pos_data.get("Z", "?")
                    pos_str = (
                        f"X={x:.3f} Y={y:.3f} Z={z:.3f}"
                        if all(isinstance(v, (int, float)) for v in [x, y, z])
                        else f"X={x} Y={y} Z={z}"
                    )
                    status_level = "connected" if tp_mode == 0 else "waiting"
                    self._emit_status(
                        f"POS: {pos_str}\nMode: {mode} | {servo} | OVR:{override}%",
                        status_level
                    )

            except Exception:
                pass  # Le polling ne doit jamais crasher

            time.sleep(interval)

    @staticmethod
    def _extract_position(reply: Dict[str, Any]) -> Dict[str, Any]:
        """Extrait X, Y, Z depuis la réponse FRC_ReadCartesianPosition."""
        if not isinstance(reply, dict):
            return {}
        # Format doc §2.3.14 : reply["Position"]["X"], etc.
        pos = reply.get("Position")
        if isinstance(pos, dict):
            return {k: pos.get(k, "?") for k in ("X", "Y", "Z", "W", "P", "R")}
        # Fallback : position dans Configuration
        conf = reply.get("Configuration", {})
        pos = conf.get("Position") if isinstance(conf, dict) else None
        if isinstance(pos, dict):
            return {k: pos.get(k, "?") for k in ("X", "Y", "Z", "W", "P", "R")}
        return {}

    @staticmethod
    def _extract_joints(reply: Dict[str, Any]) -> Dict[str, Any]:
        """Extrait J1..J6 depuis la réponse FRC_ReadJointAngles."""
        if not isinstance(reply, dict):
            return {}
        # Format doc §2.3.15 : reply["JointAngle"]["J1"], etc.
        ja = reply.get("JointAngle") or reply.get("JointAngles") or {}
        if isinstance(ja, dict):
            return {f"J{i}": ja.get(f"J{i}", "?") for i in range(1, 7)}
        return {}
