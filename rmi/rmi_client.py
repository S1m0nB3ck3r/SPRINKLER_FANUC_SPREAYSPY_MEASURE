"""
Client RMI haut niveau — API pour piloter un robot Fanuc via RMI.

Responsabilités :
  - Connexion en 2 phases (FRC_Connect sur port 16001 → motion port)
  - Gestion locale du SequenceID (monotone croissant, reset à FRC_Initialize)
  - Respect du buffer d'instructions (max 8 en vol, §3.2)
  - Envoi de commandes (synchrone) et d'instructions TP (async avec handshake)
  - Abonnement aux événements non-sollicités (SystemFault, Terminate)

Ce module ne contient AUCUNE logique métier (séquences, recovery).
Voir orchestrator.py pour la couche supérieure.
"""

import threading
import logging
import time
from typing import Any, Callable, Dict, Optional

from .rmi_transport import RmiTransport, RmiTransportError, ConnectionLostError
from .rmi_dispatcher import RmiDispatcher, MessageType

logger = logging.getLogger(__name__)


# ======================================================================
# Erreurs spécifiques au client RMI
# ======================================================================

class RmiError(Exception):
    """Erreur générique du client RMI."""

class RmiCommandError(RmiError):
    """Le contrôleur a retourné un ErrorID non nul."""
    def __init__(self, error_id: int, command: str, packet: Dict[str, Any]):
        self.error_id = error_id
        self.command = command
        self.packet = packet
        super().__init__(f"{command} échoué: ErrorID={error_id}")

class RmiNotConnectedError(RmiError):
    """Tentative d'opération sans connexion active."""

class RmiBufferFullError(RmiError):
    """Le buffer d'instructions (8 max) est plein et timeout atteint."""


# ======================================================================
# Lookup table des erreurs RMI (doc Annexe A)
# ======================================================================

RMI_ERRORS = {
    2556929: "RMIT-001 Internal System Error",
    2556930: "RMIT-002 Invalid UTool Number",
    2556931: "RMIT-003 Invalid UFrame Number",
    2556932: "RMIT-004 Invalid Position Register",
    2556933: "RMIT-005 Invalid Speed Override",
    2556934: "RMIT-006 Cannot Execute TP program",
    2556935: "RMIT-007 Controller Servo is Off",
    2556936: "RMIT-008 Teach Pendant is Enabled",
    2556937: "RMIT-009 RMI is Not Running",
    2556938: "RMIT-010 TP Program is Not Paused",
    2556939: "RMIT-011 Cannot Resume TP Program",
    2556940: "RMIT-012 Cannot Reset Controller",
    2556941: "RMIT-013 Invalid RMI Command",
    2556942: "RMIT-014 RMI Command Fail",
    2556943: "RMIT-015 Invalid Controller State",
    2556944: "RMIT-016 Please Cycle Power",
    2556945: "RMIT-017 Invalid Payload Schedule",
    2556946: "RMIT-018 Invalid Motion Option",
    2556947: "RMIT-019 Invalid Vision Register",
    2556948: "RMIT-020 Invalid RMI Instruction",
    2556949: "RMIT-021 Invalid Value",
    2556950: "RMIT-022 Invalid Text String",
    2556951: "RMIT-023 Invalid Position Data",
    2556952: "RMIT-024 RMI is In HOLD State",
    2556953: "RMIT-025 Remote Device Disconnected",
    2556954: "RMIT-026 Robot is Already Connected",
    2556955: "RMIT-027 Wait for Command Done",
    2556956: "RMIT-028 Wait for Instruction Done",
    2556957: "RMIT-029 Invalid sequence ID number",
    2556958: "RMIT-030 Invalid Speed Type",
    2556959: "RMIT-031 Invalid Speed Value",
    2556960: "RMIT-032 Invalid Term Type",
    2556961: "RMIT-033 Invalid Term Value",
    2556962: "RMIT-034 Invalid LCB Port Type",
    2556963: "RMIT-035 Invalid ACC Value",
    2556964: "RMIT-036 Invalid Destination Position",
    2556965: "RMIT-037 Invalid VIA Position",
    2556966: "RMIT-038 Invalid Port Number",
    2556967: "RMIT-039 Invalid Group Number",
    2556968: "RMIT-040 Invalid Group Mask",
    2556969: "RMIT-041 Joint motion with COORD",
    2556970: "RMIT-042 Incremental motn with COORD",
    2556971: "RMIT-043 Robot in Single Step Mode",
    2556972: "RMIT-044 Invalid Position Data Type",
    7004:    "MEMO-004 Specific program is in use",
    7015:    "MEMO-015 Program already exists",
}


def describe_error(error_id: int) -> str:
    """Retourne la description lisible d'un ErrorID RMI."""
    return RMI_ERRORS.get(error_id, f"ErrorID inconnu ({error_id})")


# ======================================================================
# Client RMI
# ======================================================================

class RmiClient:
    """Client RMI pour piloter un robot Fanuc R-30iB Plus.

    Cycle de vie typique :
        client = RmiClient()
        client.connect("192.168.1.100")
        client.initialize(group_mask=1)
        client.set_uframe_utool(uframe=0, utool=1)
        reply = client.linear_relative(dx=10, dy=0, dz=0, speed=50)
        client.abort()
        client.disconnect()
    """

    # Taille max du buffer d'instructions RMI (doc §1.4.4 et §3.2)
    MAX_INFLIGHT = 8

    def __init__(self) -> None:
        self._transport = RmiTransport()
        self._dispatcher = RmiDispatcher(self._transport)

        # SequenceID local (reset à FRC_Initialize, incrémenté par instruction)
        self._seq_id: int = 0
        self._seq_lock = threading.Lock()

        # Compteur d'instructions en vol (max 8)
        self._inflight: int = 0
        self._inflight_cond = threading.Condition()

        # Callbacks utilisateur pour événements
        self._on_system_fault: Optional[Callable] = None
        self._on_connection_lost: Optional[Callable] = None
        self._on_log: Optional[Callable] = None

        # IP mémorisée pour reconnexion
        self._ip: str = ""
        self._connected: bool = False

    # ------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected and self._transport.is_connected

    @property
    def transport(self) -> RmiTransport:
        return self._transport

    @property
    def dispatcher(self) -> RmiDispatcher:
        return self._dispatcher

    # ------------------------------------------------------------------
    # Callbacks utilisateur
    # ------------------------------------------------------------------

    def set_on_system_fault(self, handler: Optional[Callable]) -> None:
        """Enregistre un handler pour FRC_SystemFault."""
        self._on_system_fault = handler

    def set_on_connection_lost(self, handler: Optional[Callable]) -> None:
        """Enregistre un handler pour perte de connexion."""
        self._on_connection_lost = handler

    def set_on_log(self, handler: Optional[Callable]) -> None:
        """Enregistre un callback de log : handler(message, level)."""
        self._on_log = handler

    def _log(self, msg: str, level: str = "INFO") -> None:
        logger.log(getattr(logging, level, logging.INFO), msg)
        if self._on_log:
            self._on_log(msg, level)

    # ------------------------------------------------------------------
    # Connexion / déconnexion (doc §2.2.1)
    # ------------------------------------------------------------------

    def connect(self, ip: str, port: int = 16001,
                connect_timeout: float = 5.0,
                command_timeout: float = 10.0) -> Dict[str, Any]:
        """Connexion RMI en 2 phases.

        Phase 1 : TCP vers port 16001, envoi FRC_Connect, récupère PortNumber.
        Phase 2 : TCP vers PortNumber, démarre le Dispatcher.

        Args:
            ip: Adresse IP du contrôleur.
            port: Port initial (16001 par défaut).
            connect_timeout: Timeout connexion TCP.
            command_timeout: Timeout par défaut pour les commandes.

        Returns:
            Dict de la réponse FRC_Connect (contient MajorVersion, etc.)
        """
        self._ip = ip

        # Phase 1 — port initial
        self._log(f"Connexion à {ip}:{port}...", "INFO")
        self._transport.connect(ip, port, timeout=connect_timeout)
        reply = self._transport.send_recv(
            {"Communication": "FRC_Connect"},
            timeout=connect_timeout
        )

        error_id = reply.get("ErrorID", -1)
        if error_id != 0:
            self._transport.close()
            raise RmiCommandError(error_id, "FRC_Connect", reply)

        motion_port = reply.get("PortNumber")
        major_ver = reply.get("MajorVersion", "?")
        if not motion_port:
            self._transport.close()
            raise RmiError(f"PortNumber absent dans la réponse: {reply}")

        self._log(
            f"FRC_Connect OK — PortNumber={motion_port}, "
            f"MajorVersion={major_ver}",
            "INFO"
        )

        # Phase 2 — port de mouvement
        self._transport.close()  # Le contrôleur ferme aussi le port 16001
        self._transport.connect(ip, motion_port, timeout=connect_timeout)

        # Configurer le timeout par défaut pour les commandes
        if self._transport._sock:
            self._transport._sock.settimeout(command_timeout)

        # Démarrer le Dispatcher (RecvThread)
        self._dispatcher.on(MessageType.SYSTEM_FAULT, self._handle_system_fault)
        self._dispatcher.on(MessageType.TERMINATE, self._handle_terminate)
        self._dispatcher.on(MessageType.CONNECTION_LOST, self._handle_conn_lost)
        self._dispatcher.start()

        self._connected = True
        self._log("Connecté au port de mouvement — Dispatcher actif", "INFO")
        return reply

    def disconnect(self) -> None:
        """Déconnexion propre (FRC_Abort optionnel + fermeture)."""
        self._log("Déconnexion...", "INFO")
        try:
            if self._connected:
                try:
                    self.abort()
                except Exception:
                    pass
        finally:
            self._dispatcher.stop()
            self._transport.close()
            self._connected = False
            self._seq_id = 0
            with self._inflight_cond:
                self._inflight = 0
                self._inflight_cond.notify_all()
            self._log("Déconnecté", "INFO")

    # ------------------------------------------------------------------
    # Commandes (synchrones — doc §2.3)
    # ------------------------------------------------------------------

    def _send_command(self, packet: Dict[str, Any],
                      timeout: float = 10.0) -> Dict[str, Any]:
        """Envoie une commande et attend la réponse via le Dispatcher.

        Args:
            packet: Paquet JSON (doit avoir "Command" ou "Communication").
            timeout: Timeout d'attente de la réponse.

        Returns:
            Dict de la réponse.

        Raises:
            RmiNotConnectedError: Si non connecté.
            RmiCommandError: Si ErrorID != 0.
            TimeoutError: Si pas de réponse dans le délai.
        """
        if not self.is_connected:
            raise RmiNotConnectedError("Non connecté")

        # Déterminer la clé pour le routage
        key = packet.get("Command") or packet.get("Communication")
        if not key:
            raise RmiError(f"Paquet sans Command/Communication: {packet}")

        future = self._dispatcher.expect_command(key)
        self._transport.send(packet)

        try:
            reply = future.result(timeout=timeout)
        except Exception as exc:
            if isinstance(exc, (TimeoutError,)):
                raise TimeoutError(f"Timeout commande {key}") from exc
            raise

        return reply

    def _check_error(self, reply: Dict[str, Any], command: str) -> None:
        """Vérifie ErrorID dans la réponse et lève une exception si != 0."""
        error_id = reply.get("ErrorID", 0)
        if error_id != 0:
            desc = describe_error(error_id)
            self._log(f"Erreur {command}: {desc}", "ERROR")
            raise RmiCommandError(error_id, command, reply)

    # --- Commandes spécifiques ---

    def get_status(self, timeout: float = 5.0) -> Dict[str, Any]:
        """FRC_GetStatus — Récupère le statut complet du contrôleur.

        Retourne les champs : ServoReady, TPMode, RMIMotionStatus,
        ProgramStatus, NextSequenceID, Override, etc.
        """
        reply = self._send_command({"Command": "FRC_GetStatus"}, timeout)
        # FRC_GetStatus peut retourner ErrorID != 0 mais on veut quand même
        # les données pour diagnostic, donc on ne lève pas d'exception ici.
        return reply

    def initialize(self, group_mask: int = 1,
                   timeout: float = 10.0) -> Dict[str, Any]:
        """FRC_Initialize — Démarre le programme RMI_MOVE (doc §2.3.1).

        Reset le SequenceID local à 0.
        Attend la réponse avant de continuer (le contrôleur a besoin de temps).

        Args:
            group_mask: Bit-field des groupes (1 = groupe 1 seul).
        """
        with self._seq_lock:
            self._seq_id = 0
        with self._inflight_cond:
            self._inflight = 0

        reply = self._send_command({
            "Command": "FRC_Initialize",
            "GroupMask": group_mask,
        }, timeout)
        self._check_error(reply, "FRC_Initialize")
        self._log(f"FRC_Initialize OK (GroupMask={group_mask})", "INFO")
        return reply

    def abort(self, timeout: float = 5.0) -> Dict[str, Any]:
        """FRC_Abort — Termine le programme RMI_MOVE (doc §2.3.2)."""
        reply = self._send_command({"Command": "FRC_Abort"}, timeout)
        # Reset inflight car toutes les instructions en attente sont annulées
        with self._inflight_cond:
            self._inflight = 0
            self._inflight_cond.notify_all()
        return reply

    def pause(self, timeout: float = 5.0) -> Dict[str, Any]:
        """FRC_Pause — Met en pause le programme RMI_MOVE."""
        reply = self._send_command({"Command": "FRC_Pause"}, timeout)
        self._check_error(reply, "FRC_Pause")
        return reply

    def continue_program(self, timeout: float = 5.0) -> Dict[str, Any]:
        """FRC_Continue — Reprend le programme RMI_MOVE."""
        reply = self._send_command({"Command": "FRC_Continue"}, timeout)
        self._check_error(reply, "FRC_Continue")
        return reply

    def reset(self, timeout: float = 10.0) -> Dict[str, Any]:
        """FRC_Reset — Réinitialise les erreurs du contrôleur (doc §2.3.20).

        ⚠️ Attendre la réponse avant d'envoyer une autre commande.
        """
        reply = self._send_command({"Command": "FRC_Reset"}, timeout)
        self._check_error(reply, "FRC_Reset")
        return reply

    def set_uframe_utool(self, uframe: int, utool: int,
                         group: Optional[int] = None,
                         timeout: float = 5.0) -> Dict[str, Any]:
        """FRC_SetUFrameUTool — Définit UFrame et UTool courants (doc §2.3.6).

        ⚠️ Ne pas envoyer pendant un mouvement.
        """
        cmd: Dict[str, Any] = {
            "Command": "FRC_SetUFrameUTool",
            "UFrameNumber": uframe,
            "UToolNumber": utool,
        }
        if group is not None:
            cmd["Group"] = group
        reply = self._send_command(cmd, timeout)
        self._check_error(reply, "FRC_SetUFrameUTool")
        self._log(f"UFrame={uframe}, UTool={utool} configurés", "INFO")
        return reply

    def set_override(self, value: int, timeout: float = 5.0) -> Dict[str, Any]:
        """FRC_SetOverRide — Change l'override programme (1-100%)."""
        reply = self._send_command({
            "Command": "FRC_SetOverRide",
            "Value": max(1, min(100, value)),
        }, timeout)
        self._check_error(reply, "FRC_SetOverRide")
        return reply

    def read_position(self, group: int = 1,
                      timeout: float = 5.0) -> Dict[str, Any]:
        """FRC_ReadCartesianPosition — Lit la position cartésienne actuelle."""
        return self._send_command({
            "Command": "FRC_ReadCartesianPosition",
            "Group": group,
        }, timeout)

    def read_joint_angles(self, group: int = 1,
                          timeout: float = 5.0) -> Dict[str, Any]:
        """FRC_ReadJointAngles — Lit les angles articulaires actuels."""
        return self._send_command({
            "Command": "FRC_ReadJointAngles",
            "Group": group,
        }, timeout)

    def read_error(self, count: int = 1,
                   timeout: float = 5.0) -> Dict[str, Any]:
        """FRC_ReadError — Lit les dernières erreurs du contrôleur."""
        cmd: Dict[str, Any] = {"Command": "FRC_ReadError"}
        if count > 1:
            cmd["Count"] = min(count, 5)
        return self._send_command(cmd, timeout)

    def read_din(self, port: int, timeout: float = 5.0) -> Dict[str, Any]:
        """FRC_ReadDIN — Lit un port d'entrée numérique."""
        return self._send_command({
            "Command": "FRC_ReadDIN",
            "PortNumber": port,
        }, timeout)

    def write_dout(self, port: int, value: str,
                   timeout: float = 5.0) -> Dict[str, Any]:
        """FRC_WriteDOUT — Écrit sur un port de sortie numérique.

        Args:
            port: Numéro du port DOUT.
            value: "ON" ou "OFF".
        """
        reply = self._send_command({
            "Command": "FRC_WriteDOUT",
            "PortNumber": port,
            "PortValue": value,
        }, timeout)
        self._check_error(reply, "FRC_WriteDOUT")
        return reply

    def read_tcp_speed(self, timeout: float = 5.0) -> Dict[str, Any]:
        """FRC_ReadTCPSpeed — Lit la vitesse TCP actuelle (mm/s)."""
        return self._send_command({"Command": "FRC_ReadTCPSpeed"}, timeout)

    def get_uframe_utool(self, group: Optional[int] = None,
                         timeout: float = 5.0) -> Dict[str, Any]:
        """FRC_GetUFrameUTool — Lit UFrame/UTool actuels."""
        cmd: Dict[str, Any] = {"Command": "FRC_GetUFrameUTool"}
        if group is not None:
            cmd["Group"] = group
        return self._send_command(cmd, timeout)

    # ------------------------------------------------------------------
    # Instructions TP (asynchrones — doc §2.4)
    # ------------------------------------------------------------------

    def _next_seq_id(self) -> int:
        """Incrémente et retourne le prochain SequenceID."""
        with self._seq_lock:
            self._seq_id += 1
            return self._seq_id

    def _acquire_slot(self, timeout: float = 30.0) -> None:
        """Attend qu'un slot soit disponible dans le buffer de 8 instructions.

        Bloque si le buffer est plein, jusqu'à ce qu'une instruction
        se termine (libérant un slot).
        """
        deadline = time.monotonic() + timeout
        with self._inflight_cond:
            while self._inflight >= self.MAX_INFLIGHT:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RmiBufferFullError(
                        f"Buffer plein ({self.MAX_INFLIGHT} instructions en vol), "
                        f"timeout après {timeout}s"
                    )
                self._inflight_cond.wait(timeout=min(remaining, 1.0))
            self._inflight += 1

    def _release_slot(self) -> None:
        """Libère un slot dans le buffer d'instructions."""
        with self._inflight_cond:
            self._inflight = max(0, self._inflight - 1)
            self._inflight_cond.notify()

    def send_instruction(self, instruction: Dict[str, Any],
                         timeout: float = 60.0) -> Dict[str, Any]:
        """Envoie une instruction TP et attend sa complétion.

        Gère automatiquement :
          - Le SequenceID (incrémenté localement)
          - Le buffer de 8 instructions (bloque si plein)
          - Le handshake (attend la réponse du contrôleur)

        Args:
            instruction: Dict de l'instruction (sans SequenceID).
            timeout: Timeout total pour l'exécution.

        Returns:
            Dict de la réponse du contrôleur.

        Raises:
            RmiBufferFullError: Si timeout atteint avec buffer plein.
            RmiCommandError: Si ErrorID != 0.
            TimeoutError: Si pas de réponse.
        """
        if not self.is_connected:
            raise RmiNotConnectedError("Non connecté")

        # Synchroniser le SequenceID AVANT d'acquérir un slot.
        # FRC_GetStatus passe par _send_command() (canal commandes, indépendant
        # du canal instructions). Doit être fait avant _acquire_slot() pour
        # éviter toute interférence avec le buffer d'instructions en vol.
        try:
            self.sync_seq_id()
        except Exception:
            pass

        # Attendre un slot libre dans le buffer
        self._acquire_slot(timeout=timeout)

        seq_id = self._next_seq_id()
        instruction["SequenceID"] = seq_id

        # Préparer la synchronisation
        result_event = threading.Event()
        result_holder: Dict[str, Any] = {}

        def on_reply(packet: Dict[str, Any]) -> None:
            result_holder["reply"] = packet
            self._release_slot()
            result_event.set()

        self._dispatcher.expect_instruction(seq_id, on_reply)

        try:
            self._transport.send(instruction)
        except Exception:
            self._release_slot()
            raise

        # Attendre la réponse
        if not result_event.wait(timeout=timeout):
            self._release_slot()
            raise TimeoutError(
                f"Timeout instruction SequenceID={seq_id} après {timeout}s"
            )

        reply = result_holder["reply"]
        return reply

    def send_instruction_nowait(self, instruction: Dict[str, Any],
                                callback: Optional[Callable] = None,
                                timeout: float = 30.0) -> int:
        """Envoie une instruction sans attendre la complétion (fire & forget).

        Utile pour remplir le buffer d'instructions en avance (pipeline).

        Args:
            instruction: Dict de l'instruction.
            callback: Optionnel, appelé à la complétion : callback(reply).
            timeout: Timeout pour acquérir un slot dans le buffer.

        Returns:
            Le SequenceID assigné.
        """
        if not self.is_connected:
            raise RmiNotConnectedError("Non connecté")

        self._acquire_slot(timeout=timeout)
        seq_id = self._next_seq_id()
        instruction["SequenceID"] = seq_id

        def on_reply(packet: Dict[str, Any]) -> None:
            self._release_slot()
            if callback:
                callback(packet)

        self._dispatcher.expect_instruction(seq_id, on_reply)
        try:
            self._transport.send(instruction)
        except Exception:
            self._release_slot()
            raise

        return seq_id

    # ------------------------------------------------------------------
    # Helpers d'instructions courantes
    # ------------------------------------------------------------------

    def joint_relative(self, dx: float, dy: float, dz: float,
                       speed: float, utool: int = 1, uframe: int = 0,
                       term_type: str = "FINE", term_value: int = 0,
                       timeout: float = 60.0) -> Dict[str, Any]:
        """FRC_JointRelative — Mouvement relatif avec interpolation articulaire.

        Contrairement à FRC_LinearRelative (chemin droit cartésien),
        FRC_JointRelative utilise l'interpolation articulaire :
        chaque axe tourne à vitesse constante proportionnelle,
        ce qui évite les singularités et les erreurs MOTN-018.

        Args:
            dx, dy, dz: Déplacements relatifs en mm.
            speed: Vitesse en mm/s.
            utool: Numéro UTool.
            uframe: Numéro UFrame.
            term_type: "FINE" ou "CNT".
            term_value: Valeur CNT (0-100), ignoré si FINE.
            timeout: Timeout d'exécution.
        """
        instr: Dict[str, Any] = {
            "Instruction": "FRC_JointRelative",
            "Configuration": {
                "UToolNumber": utool,
                "UFrameNumber": uframe,
                "Front": 0, "Up": 0, "Left": 0, "Flip": 0,
                "Turn4": 0, "Turn5": 0, "Turn6": 0,
            },
            "Position": {
                "X": float(dx), "Y": float(dy), "Z": float(dz),
                "W": 0.0, "P": 0.0, "R": 0.0,
            },
            "SpeedType": "Percent",
            "Speed": max(1, min(100, int(speed))),
            "TermType": term_type,
        }
        if term_type == "CNT":
            instr["TermValue"] = max(0, min(100, int(term_value)))
        return self.send_instruction(instr, timeout=timeout)

    def linear_relative(self, dx: float, dy: float, dz: float,
                        speed: float, utool: int = 1, uframe: int = 0,
                        term_type: str = "FINE", term_value: int = 0,
                        timeout: float = 60.0) -> Dict[str, Any]:
        """FRC_LinearRelative — Mouvement linéaire incrémental (doc §2.4.8).

        Args:
            dx, dy, dz: Déplacements relatifs en mm.
            speed: Vitesse en mm/s.
            utool: Numéro UTool.
            uframe: Numéro UFrame.
            term_type: "FINE" (arrêt précis) ou "CNT" (passage continu).
            term_value: Pour CNT : 0-100 (100 = déviation max, chemin le plus
                        fluide). Ignoré si term_type="FINE".
            timeout: Timeout d'exécution.
        """
        instr: Dict[str, Any] = {
            "Instruction": "FRC_LinearRelative",
            "Position": {
                "X": dx, "Y": dy, "Z": dz,
                "W": 0.0, "P": 0.0, "R": 0.0,
            },
            "SpeedType": "mmSec",
            "Speed": int(speed),
            "TermType": term_type,
        }
        if term_type == "CNT":
            instr["TermValue"] = max(0, min(100, int(term_value)))
        return self.send_instruction(instr, timeout=timeout)

    def linear_motion(self, x: float, y: float, z: float,
                      w: float, p: float, r: float,
                      speed: float, utool: int = 1, uframe: int = 0,
                      config: Optional[Dict] = None,
                      speed_type: str = "mmSec",
                      term_type: str = "FINE", term_value: int = 0,
                      timeout: float = 60.0) -> Dict[str, Any]:
        """FRC_LinearMotion — Mouvement linéaire absolu (doc §2.4.7)."""
        cfg = config or {
            "UToolNumber": utool, "UFrameNumber": uframe,
            "Front": 1, "Up": 1, "Left": 1, "Flip": 0,
            "Turn4": 0, "Turn5": 0, "Turn6": 0,
        }
        instr: Dict[str, Any] = {
            "Instruction": "FRC_LinearMotion",
            "Configuration": cfg,
            "Position": {
                "X": x, "Y": y, "Z": z,
                "W": w, "P": p, "R": r,
            },
            "SpeedType": speed_type,
            "Speed": max(1, min(100, int(speed))) if speed_type == "Percent" else max(1, int(speed)),
            "TermType": term_type,
        }
        if term_type == "CNT":
            instr["TermValue"] = max(0, min(100, int(term_value)))
        return self.send_instruction(instr, timeout=timeout)

    def joint_motion_jrep(self, joints: Dict[str, float],
                          speed_pct: int = 10,
                          term_type: str = "FINE",
                          timeout: float = 60.0) -> Dict[str, Any]:
        """FRC_JointMotionJRep — Mouvement articulaire (doc §2.4.13)."""
        instr: Dict[str, Any] = {
            "Instruction": "FRC_JointMotionJRep",
            "JointAngle": joints,
            "SpeedType": "Percent",
            "Speed": speed_pct,
            "TermType": term_type,
        }
        return self.send_instruction(instr, timeout=timeout)

    def joint_relative_jrep(self, joints: Dict[str, float],
                            speed_pct: int = 10,
                            term_type: str = "FINE",
                            timeout: float = 60.0) -> Dict[str, Any]:
        """FRC_JointRelativeJRep — Mouvement articulaire incrémental (doc §2.4.14).

        Args:
            joints: Dict des incréments articulaires, ex. {"J1": 5.0, "J2": 0, ...}.
                    Les clés non fournies sont mises à 0.
            speed_pct: Vitesse en pourcentage (1-100).
            term_type: "FINE" ou "CNT".
            timeout: Timeout d'exécution.
        """
        # S'assurer que J1..J6 sont toujours présents (0 par défaut)
        full_joints = {f"J{i}": 0.0 for i in range(1, 7)}
        full_joints.update(joints)
        instr: Dict[str, Any] = {
            "Instruction": "FRC_JointRelativeJRep",
            "JointAngle": full_joints,
            "SpeedType": "Percent",
            "Speed": int(speed_pct),
            "TermType": term_type,
        }
        return self.send_instruction(instr, timeout=timeout)

    def wait_din(self, port: int, value: str = "ON",
                 timeout: float = 120.0) -> Dict[str, Any]:
        """FRC_WaitDIN — Attend un signal DIN (doc §2.4.1)."""
        return self.send_instruction({
            "Instruction": "FRC_WaitDIN",
            "PortNumber": port,
            "PortValue": value,
        }, timeout=timeout)

    def wait_time(self, seconds: float,
                  timeout: float = 120.0) -> Dict[str, Any]:
        """FRC_WaitTime — Pause TP (doc §2.4.4)."""
        return self.send_instruction({
            "Instruction": "FRC_WaitTime",
            "Time": seconds,
        }, timeout=timeout)

    def set_utool_instr(self, tool_num: int,
                        timeout: float = 10.0) -> Dict[str, Any]:
        """FRC_SetUTool — Instruction TP pour changer l'outil (doc §2.4.3)."""
        return self.send_instruction({
            "Instruction": "FRC_SetUTool",
            "ToolNumber": tool_num,
        }, timeout=timeout)

    def set_uframe_instr(self, frame_num: int,
                         timeout: float = 10.0) -> Dict[str, Any]:
        """FRC_SetUFrame — Instruction TP pour changer le frame (doc §2.4.2)."""
        return self.send_instruction({
            "Instruction": "FRC_SetUFrame",
            "FrameNumber": frame_num,
        }, timeout=timeout)

    def call_program(self, program_name: str,
                     timeout: float = 120.0) -> Dict[str, Any]:
        """FRC_Call — Appelle un sous-programme TP (doc §2.4.6)."""
        return self.send_instruction({
            "Instruction": "FRC_Call",
            "ProgramName": program_name,
        }, timeout=timeout)

    # ------------------------------------------------------------------
    # Sync SequenceID avec le contrôleur
    # ------------------------------------------------------------------

    def sync_seq_id(self) -> int:
        """Synchronise le SequenceID local avec NextSequenceID du contrôleur.

        Utile après un FRC_Reset pour reprendre la numérotation correcte.
        """
        status = self.get_status()
        next_id = status.get("NextSequenceID", self._seq_id + 1)
        with self._seq_lock:
            self._seq_id = next_id - 1  # _next_seq_id() incrémentera
        self._log(f"SequenceID synchronisé: prochain={next_id}", "INFO")
        return next_id

    # ------------------------------------------------------------------
    # Handlers événements internes
    # ------------------------------------------------------------------

    def _handle_system_fault(self, packet: Dict[str, Any]) -> None:
        """Gestion interne de FRC_SystemFault."""
        seq_id = packet.get("SequenceID", "?")
        self._log(
            f"⚠️ FRC_SystemFault reçu (SequenceID={seq_id})", "WARNING"
        )
        if self._on_system_fault:
            self._on_system_fault(packet)

    def _handle_terminate(self, packet: Dict[str, Any]) -> None:
        """Gestion interne de FRC_Terminate (timeout inactivité)."""
        self._log("⚠️ FRC_Terminate reçu — connexion terminée par le contrôleur", "WARNING")
        self._connected = False
        if self._on_connection_lost:
            self._on_connection_lost(packet)

    def _handle_conn_lost(self, packet: Dict[str, Any]) -> None:
        """Gestion interne de perte de connexion TCP."""
        self._log("❌ Connexion TCP perdue", "ERROR")
        self._connected = False
        # Libérer tous les slots pour débloquer les threads en attente
        with self._inflight_cond:
            self._inflight = 0
            self._inflight_cond.notify_all()
        if self._on_connection_lost:
            self._on_connection_lost(packet)
