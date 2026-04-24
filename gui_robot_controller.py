"""
GUI Tkinter pour contrôle du robot Fanuc avec capteur optique
Support pour séquence 3D paramétrable (X, Y, Z)
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import queue
import json
import socket
import time
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

# ==================================================
# CONFIGURATION PAR DÉFAUT
# ==================================================

@dataclass
class RobotConfig:
    robot_ip: str = "192.168.1.100"
    rmi_init_port: int = 16001
    group: int = 1
    utool_number: int = 1
    uframe_number: int = 0  # Frame par défaut (mouvements relatifs à partir de position de référence)
    wait_di_port: int = 1  # Port DO (sortie robot vers capteur)
    capture_delay_ms: int = 100  # Délai d'acquisition capteur (ms)
    default_speed: int = 10
    
    # Paramètres de séquence
    z_min: float = 0.0
    z_step: float = -1.0
    z_number: int = 100
    y_min: float = -500.0
    y_step: float = 10.0
    y_number: int = 101
    x_min: float = -500.0
    x_step: float = 10.0
    x_number: int = 101

CONFIG_FILE = Path(__file__).parent / "robot_config.json"

# ==================================================
# ÉTATS DU ROBOT
# ==================================================

class RobotState:
    """États possibles du robot"""
    DISCONNECTED = "disconnected"          # Non connecté
    IDLE = "idle"                          # Connecté et prêt
    WAITING_AUTO_TP_OFF = "waiting_auto"   # En attente AUTO + TP OFF
    MOVING = "moving"                      # En cours d'un mouvement manuel
    MOVING_SEQUENCE = "moving_seq"         # En cours d'une séquence

class RobotController:
    """Contrôleur Fanuc RMI avec gestion de thread"""
    
    def __init__(self):
        self.sock: Optional[socket.socket] = None
        self.is_connected = False
        self.state = RobotState.DISCONNECTED
        self.status_callback = None
        self.log_callback = None
        self.sock_lock = threading.Lock()
        self.cmd_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.cmd_thread: Optional[threading.Thread] = None
        self.cmd_stop = threading.Event()
        
    def set_callbacks(self, status_cb=None, log_cb=None):
        self.status_callback = status_cb
        self.log_callback = log_cb
    
    def _set_state(self, new_state: str):
        """Change l'état du robot"""
        if self.state != new_state:
            self._log(f"État: {self.state} → {new_state}", "INFO")
            self.state = new_state
        
    def _log(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{ts}] {level:8s} | {msg}"
        print(formatted)
        if self.log_callback:
            self.log_callback(formatted)
            
    def _status(self, msg: str, status: str = "neutral"):
        if self.status_callback:
            self.status_callback(msg, status)
    
    def _send_raw(self, cmd: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        """Envoie une commande JSON directement sur la socket (thread I/O)"""
        if not self.is_connected:
            raise RuntimeError("Robot non connecté")

        msg = json.dumps(cmd) + "\r\n"
        with self.sock_lock:
            prev_timeout = self.sock.gettimeout()
            try:
                if timeout is not None:
                    self.sock.settimeout(timeout)
                self.sock.sendall(msg.encode("ascii"))
                reply = self.sock.recv(4096).decode("ascii")
            finally:
                if timeout is not None:
                    self.sock.settimeout(prev_timeout)
        return json.loads(reply)


    def _start_cmd_thread(self):
        if self.cmd_thread and self.cmd_thread.is_alive():
            return
        self.cmd_stop.clear()
        self.cmd_thread = threading.Thread(target=self._cmd_loop, daemon=True)
        self.cmd_thread.start()

    def _stop_cmd_thread(self):
        self.cmd_stop.set()
        try:
            self.cmd_queue.put_nowait({"_stop": True})
        except Exception:
            pass

    def _cmd_loop(self):
        while not self.cmd_stop.is_set():
            try:
                item = self.cmd_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item.get("_stop"):
                break

            cmd = item["cmd"]
            timeout = item.get("timeout")
            event = item["event"]
            try:
                item["response"] = self._send_raw(cmd, timeout=timeout)
            except Exception as e:
                item["error"] = e
            finally:
                event.set()

    def send_cmd(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Envoie une commande JSON via le thread I/O et reçoit la réponse"""
        if not self.is_connected:
            raise RuntimeError("Robot non connecté")

        if self.cmd_thread and self.cmd_thread.is_alive() and threading.current_thread() != self.cmd_thread:
            event = threading.Event()
            item: Dict[str, Any] = {"cmd": cmd, "event": event}
            self.cmd_queue.put(item)
            event.wait(timeout=15.0)
            if "error" in item:
                raise item["error"]
            if "response" not in item:
                raise TimeoutError("Réponse RMI non reçue")
            return item["response"]

        return self._send_raw(cmd)

    def send_cmd_async(self, cmd: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        """Envoie une commande JSON sans bloquer (retourne l'item de suivi)."""
        if not self.is_connected:
            raise RuntimeError("Robot non connecté")

        event = threading.Event()
        item: Dict[str, Any] = {"cmd": cmd, "event": event}
        if timeout is not None:
            item["timeout"] = timeout
        self.cmd_queue.put(item)
        return item

    def send_cmd_with_timeout(self, cmd: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        """Envoie une commande JSON via le thread I/O avec timeout spécifique"""
        if not self.is_connected:
            raise RuntimeError("Robot non connecté")

        if self.cmd_thread and self.cmd_thread.is_alive() and threading.current_thread() != self.cmd_thread:
            event = threading.Event()
            item: Dict[str, Any] = {"cmd": cmd, "event": event, "timeout": timeout}
            self.cmd_queue.put(item)
            event.wait(timeout=timeout + 2.0)
            if "error" in item:
                raise item["error"]
            if "response" not in item:
                raise TimeoutError("Réponse RMI non reçue")
            return item["response"]

        return self._send_raw(cmd, timeout=timeout)
    
    def connect(self, ip: str, port: int) -> bool:
        """Connexion RMI"""
        try:
            self._log(f"Connexion à {ip}:{port}...", "CONN")
            self._status("Connexion...", "connecting")
            
            # Connexion initiale avec timeout
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)  # Timeout de 5 secondes pour la connexion
            sock.connect((ip, port))
            
            # FRC_Connect pour obtenir le port de mouvement
            self.sock = sock
            self.is_connected = True
            reply = self.send_cmd({"Communication": "FRC_Connect"})
            
            # Vérifier ErrorID (0 = succès)
            if reply.get("ErrorID") != 0:
                raise ValueError(f"FRC_Connect échouée. ErrorID: {reply.get('ErrorID')}")
            
            motion_port = reply.get("PortNumber")
            
            if not motion_port:
                raise ValueError(f"PortNumber non reçu. Réponse: {reply}")
            
            # Connexion au port de mouvement avec timeout
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)  # Timeout de 10 secondes pour les commandes
            sock.connect((ip, motion_port))
            
            self.sock = sock
            self.is_connected = True

            self._start_cmd_thread()
            self._set_state(RobotState.WAITING_AUTO_TP_OFF)
            self._log("Connexion RMI établie", "SUCCESS")
            self._status("Connecté - Attente AUTO+TP OFF", "connecting")
            return True
            
        except Exception as e:
            self._log(f"Erreur connexion: {e}", "ERROR")
            self._status(f"Erreur: {e}", "error")
            return False
    
    def disconnect(self):
        """Déconnexion propre"""
        try:
            self._log("Déconnexion en cours...", "CONN")
            
            # Essayer d'envoyer FRC_Abort si connecté
            if self.is_connected and self.sock:
                try:
                    self.send_cmd({"Command": "FRC_Abort"})
                    time.sleep(0.2)
                except Exception as e:
                    self._log(f"Erreur envoi FRC_Abort: {e}", "WAIT")
            
            # Fermer la socket
            if self.sock:
                try:
                    self.sock.close()
                except:
                    pass

            self._stop_cmd_thread()
            
            self.is_connected = False
            self._set_state(RobotState.DISCONNECTED)
            self._log("✅ Déconnexion réussie", "SUCCESS")
            self._status("Déconnecté", "disconnected")
            
        except Exception as e:
            self._log(f"❌ Erreur déconnexion: {e}", "ERROR")
    
    def get_status(self) -> Dict[str, Any]:
        """Récupère le statut du robot et l'affiche dans la zone Status pour debug"""
        try:
            return self.send_cmd({"Command": "FRC_GetStatus"})
        except Exception as e:
            self._log(f"Erreur lecture statut: {e}", "ERROR")
            return {}
    
    def wait_manual_phase(self):
        """Attend la phase de positionnement manuel"""
        self._log("En attente positionnement manuel (TP actif)", "WAIT")
        self._status("En attente phase manuelle...", "waiting")
        while True:
            st = self.get_status()
            if st.get("TPMode") == 1:
                time.sleep(0.2)
            else:
                break
    
    def wait_ready_for_remote(self):
        """Attend que le robot soit prêt (AUTO + TP OFF)"""
        self._log("En attente AUTO + TP OFF", "WAIT")
        self._status("En attente mode AUTO + TP OFF...", "waiting")
        while True:
            st = self.get_status()
            if st.get("TPMode") == 0 and st.get("ServoReady") == 1:
                self._set_state(RobotState.IDLE)
                self._log("Robot prêt pour pilotage externe", "SUCCESS")
                self._status("Prêt", "connected")
                self._status("Prêt", "connected")
                return
            time.sleep(0.2)
    
    def initialize_rmi(self, group: int, utool: int = 1, uframe: int = 1):
        """Initialisation RMI + SetUFrameUTool (UTool=1, UFrame=1)"""
        try:
            self.send_cmd({
                "Command": "FRC_Initialize",
                "GroupMask": group
            })
            self._log(f"RMI initialisé (groupe {group})", "SUCCESS")
            time.sleep(0.3)  # Attendre que RMI_MOVE démarre automatiquement
            
            # Envoyer FRC_SetUFrameUTool (obligatoire)
            reply = self.send_cmd({
                "Command": "FRC_SetUFrameUTool",
                "UFrameNumber": uframe,
                "UToolNumber": utool
            })
            self._log(f"FRC_SetUFrameUTool envoyé: UFrame={uframe}, UTool={utool}", "INFO")
            
            # Vérifier prise en compte dans le statut
            status = self.get_status()
            n_utool = status.get("NumberUTool")
            n_uframe = status.get("NumberUFrame")
            if n_utool != utool or n_uframe != uframe:
                self._log(f"UTool/UFrame non pris en compte: UTool={n_utool}, UFrame={n_uframe}", "ERROR")
                if self.status_callback:
                    self.status_callback(f"UTool/UFrame non pris en compte: UTool={n_utool}, UFrame={n_uframe}", "error")
            else:
                self._log(f"UTool/UFrame OK: UTool={n_utool}, UFrame={n_uframe}", "SUCCESS")
        except Exception as e:
            self._log(f"Erreur initialisation: {e}", "ERROR")
            raise
    
    def select_utool(self, tool_num: int):
        """Sélection outil avec SequenceID conforme"""
        try:
            status = self.get_status()
            seq_id = status.get("NextSequenceID", 1)
            reply = self.send_cmd({
                "Instruction": "FRC_SetUTool",
                "SequenceID": seq_id,
                "ToolNumber": tool_num
            })
            self._log(f"UTool {tool_num} sélectionné (SequenceID={seq_id})", "SUCCESS")
        except Exception as e:
            self._log(f"Erreur sélection outil: {e}", "ERROR")
            raise
    
    def read_position(self, group: int) -> Dict[str, Any]:
        """Lecture position cartésienne"""
        try:
            pos = self.send_cmd({
                "Command": "FRC_ReadCartesianPosition",
                "Group": group
            })
            return pos
        except Exception as e:
            self._log(f"Erreur lecture position: {e}", "ERROR")
            return {}

    def read_joint_angles(self, group: int) -> Dict[str, Any]:
        """Lecture angles articulaires"""
        try:
            joints = self.send_cmd({
                "Command": "FRC_ReadJointAngles",
                "Group": group
            })
            return joints
        except Exception as e:
            self._log(f"Erreur lecture joints: {e}", "ERROR")
            return {}
    
    def linear_relative(self, dx: float, dy: float, dz: float, speed: float, utool: int = 1, uframe: int = 1):
        """Mouvement linéaire relatif avec SequenceID conforme à NextSequenceID"""
        try:
            # 1. Récupérer le statut actuel
            status = self.get_status()
            
            # 2. Vérifications essentielles (selon doc FANUC)
            if status.get("TPMode") != 0:
                self._log("Robot non en mode AUTO/TP OFF, mouvement interdit.", "ERROR")
                if self.status_callback:
                    self.status_callback("Robot non en mode AUTO/TP OFF, mouvement interdit.", "error")
                raise RuntimeError("Robot non en mode AUTO/TP OFF")
            
            if status.get("RMIMotionStatus") != 1:
                self._log("RMI non initialisé ou en erreur, tentative de réinitialisation...", "ERROR")
                try:
                    self.send_cmd({"Command": "FRC_Abort"})
                    time.sleep(0.2)
                    self.initialize_rmi(status.get("GroupMask", 1), utool=utool, uframe=uframe)
                    status = self.get_status()
                except Exception as e:
                    self._log(f"Erreur lors de la réinitialisation RMI: {e}", "ERROR")
                    if self.status_callback:
                        self.status_callback(f"Erreur RMI: {e}", "error")
                    raise RuntimeError(f"RMI non initialisable: {e}")
                if status.get("RMIMotionStatus") != 1:
                    self._log("Impossible d'initialiser RMI, mouvement annulé.", "ERROR")
                    if self.status_callback:
                        self.status_callback("Impossible d'initialiser RMI, mouvement annulé.", "error")
                    raise RuntimeError("RMI non initialisable")
            
            # 3. Récupérer NextSequenceID (CRITIQUE selon doc FANUC)
            seq_id = status.get("NextSequenceID", 1)
            self._log(f"Utilisation SequenceID={seq_id} (NextSequenceID du robot)", "INFO")
            cmd = {
                "Instruction": "FRC_LinearRelative",
                "SequenceID": seq_id,
                "Configuration": {
                    "UToolNumber": utool,
                    "UFrameNumber": uframe,
                    "Front": 1,
                    "Up": 1,
                    "Left": 1,
                    "Flip": 0,
                    "Turn4": 0,
                    "Turn5": 0,
                    "Turn6": 0
                },
                "Position": {
                    "X": dx,
                    "Y": dy,
                    "Z": dz,
                    "W": 0.0,
                    "P": 0.0,
                    "R": 0.0
                },
                "SpeedType": "mmSec",
                "Speed": speed,
                "TermType": "FINE"
            }
            self._log(f"FRC_LinearRelative envoyé: {json.dumps(cmd)}", "INFO")
            reply = self.send_cmd_with_timeout(cmd, 30.0)
            if isinstance(reply, dict) and reply.get("ErrorID", 0) != 0:
                self._log(f"Erreur FRC_LinearRelative: ErrorID={reply.get('ErrorID')} (voir doc Fanuc)", "ERROR")
                if self.status_callback:
                    self.status_callback(f"Erreur FRC_LinearRelative: ErrorID={reply.get('ErrorID')}", "error")
            else:
                self._log(f"Réponse FRC_LinearRelative: {reply}", "INFO")
        except Exception as e:
            self._log(f"Erreur mouvement: {e}", "ERROR")
            if self.status_callback:
                self.status_callback(f"Erreur mouvement: {e}", "error")
            raise

    def linear_relative_async(self, dx: float, dy: float, dz: float, speed: float, utool: int = 1, uframe: int = 1) -> Dict[str, Any]:
        """Mouvement linéaire relatif non bloquant (retourne un item de suivi)."""
        # Vérifications essentielles
        status = self.get_status()
        if status.get("TPMode") != 0:
            raise RuntimeError("Robot non en mode AUTO/TP OFF")
        if status.get("RMIMotionStatus") != 1:
            raise RuntimeError("RMI non initialisé")

        seq_id = status.get("NextSequenceID", 1)
        self._log(f"Utilisation SequenceID={seq_id} (NextSequenceID du robot)", "INFO")
        cmd = {
            "Instruction": "FRC_LinearRelative",
            "SequenceID": seq_id,
            "Configuration": {
                "UToolNumber": utool,
                "UFrameNumber": uframe,
                "Front": 1,
                "Up": 1,
                "Left": 1,
                "Flip": 0,
                "Turn4": 0,
                "Turn5": 0,
                "Turn6": 0
            },
            "Position": {
                "X": dx,
                "Y": dy,
                "Z": dz,
                "W": 0.0,
                "P": 0.0,
                "R": 0.0
            },
            "SpeedType": "mmSec",
            "Speed": speed,
            "TermType": "FINE"
        }
        self._log(f"FRC_LinearRelative envoyé (async): {json.dumps(cmd)}", "INFO")
        return self.send_cmd_async(cmd, timeout=30.0)
    
    def trigger_capture(self, port: int, delay_ms: int):
        """Déclenche la capture du capteur (DO) et attend le délai d'acquisition"""
        try:
            # Note: En pratique, le robot envoie un signal DO au capteur
            # via sa sortie numérique. Ici on simule l'attente d'acquisition.
            self._log(f"Capteur déclenché (Port DO={port}), attente {delay_ms}ms pour acquisition", "INFO")
            
            # Attendre que le capteur acquière les données
            time.sleep(delay_ms / 1000.0)
            
            self._log(f"Acquisition capteur complétée", "INFO")
        except Exception as e:
            self._log(f"Erreur trigger_capture: {e}", "ERROR")
            raise
    
    def start_reading_position(self, group: int, cancel_event: threading.Event):
        """Lit les positions et statut en continu (10x/s)"""
        while not cancel_event.is_set() and self.is_connected:
            try:
                # Appel explicite à get_status pour affichage debug
                status = self.get_status()  # Affiche le message brut dans la zone Status
                # ... on peut garder le reste pour l'affichage normal si besoin ...
                pos = self.read_position(group)
                joints = self.read_joint_angles(group)

                # Extraire X, Y, Z si possible
                x = y = z = '?'
                if isinstance(pos, dict):
                    # Support du format pos['Configuration']['Position']
                    position = None
                    if 'Position' in pos and isinstance(pos['Position'], dict):
                        position = pos['Position']
                    elif 'Configuration' in pos and isinstance(pos['Configuration'], dict):
                        conf = pos['Configuration']
                        if 'Position' in conf and isinstance(conf['Position'], dict):
                            position = conf['Position']
                    if position:
                        x = position.get('X', '?')
                        y = position.get('Y', '?')
                        z = position.get('Z', '?')

                pos_str = f"X={x:.3f} Y={y:.3f} Z={z:.3f}" if all(isinstance(v, (int, float)) for v in [x, y, z]) else f"X={x} Y={y} Z={z}"

                # Extraire J1..J6 si possible
                j_vals = ['?', '?', '?', '?', '?', '?']
                if isinstance(joints, dict):
                    joint_data = joints.get("JointAngle") or joints.get("JointAngles") or {}
                    if isinstance(joint_data, dict):
                        j_vals = [
                            joint_data.get("J1", '?'),
                            joint_data.get("J2", '?'),
                            joint_data.get("J3", '?'),
                            joint_data.get("J4", '?'),
                            joint_data.get("J5", '?'),
                            joint_data.get("J6", '?')
                        ]

                joints_str = (
                    "J1={:.2f} J2={:.2f} J3={:.2f} J4={:.2f} J5={:.2f} J6={:.2f}".format(*j_vals)
                    if all(isinstance(v, (int, float)) for v in j_vals)
                    else f"J1={j_vals[0]} J2={j_vals[1]} J3={j_vals[2]} J4={j_vals[3]} J5={j_vals[4]} J6={j_vals[5]}"
                )

                mode = "AUTO" if status.get("TPMode") == 0 else "TP"
                tp_on = "TP ON" if status.get("TPMode") == 1 else "TP OFF"
                servo = "Servo ON" if status.get("ServoReady") == 1 else "Servo OFF"

                # Affichage temps réel (même en mode TP / guided teaching)
                status_level = "connected" if status.get("TPMode") == 0 else "waiting"
                self._status(f"POS: {pos_str}\n{joints_str}\nMode: {mode} | {tp_on} | {servo}", status_level)

                time.sleep(0.1)  # 10x/s = 100ms
            except socket.timeout:
                time.sleep(0.1)
            except Exception:
                time.sleep(0.1)
    
    def run_sequence(self, config: RobotConfig, cancel_event: threading.Event):
        """Exécute la séquence 3D paramétrable"""
        try:
            self._log("Début séquence 3D", "START")
            self._status("Séquence en cours...", "running")
            
            total_points = config.x_number * config.y_number * config.z_number
            current = 0
            
            current_z = 0.0
            for zi in range(config.z_number):
                if cancel_event.is_set():
                    self._log("Séquence annulée", "CANCEL")
                    break
                    
                target_z = config.z_min + zi * config.z_step
                dz = target_z - current_z
                
                self.linear_relative(0, 0, dz, config.default_speed)
                self.trigger_capture(config.wait_di_port, config.capture_delay_ms)
                current_z = target_z
                
                current_y = 0.0
                for yi in range(config.y_number):
                    if cancel_event.is_set():
                        break
                        
                    target_y = config.y_min + yi * config.y_step
                    dy = target_y - current_y
                    
                    current_x = 0.0
                    for xi in range(config.x_number):
                        if cancel_event.is_set():
                            break
                        
                        target_x = config.x_min + xi * config.x_step
                        dx = target_x - current_x
                        
                        self.linear_relative(dx, 0, 0, config.default_speed)
                        self.trigger_capture(config.wait_di_port, config.capture_delay_ms)
                        
                        current_x = target_x
                        current += 1
                        progress = (current / total_points) * 100
                        self._status(f"Séquence {progress:.1f}% ({current}/{total_points})", "running")
                    
                    # Retour X + incrément Y
                    self.linear_relative(-current_x, dy, 0, config.default_speed)
                    self.trigger_capture(config.wait_di_port, config.capture_delay_ms)
                    current_y = target_y
            
            if not cancel_event.is_set():
                self._log("Séquence terminée avec succès", "SUCCESS")
                self._status("Séquence terminée", "success")
                
        except Exception as e:
            self._log(f"Erreur séquence: {e}", "ERROR")
            self._status(f"Erreur: {e}", "error")
            raise


# ==================================================
# INTERFACE GUI
# ==================================================

class RobotControllerGUI:
    """Interface Tkinter pour contrôle du robot"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("SpraySpy - Contrôleur Fanuc")
        self.root.geometry("1200x700")
        
        self.controller = RobotController()
        self.controller.set_callbacks(self._update_status, self._add_log)
        self.cancel_event = threading.Event()
        self.position_read_event = threading.Event()
        
        self.current_config = self._load_config()
        self.thread = None
        
        self._create_gui()
        self._update_status_display()
        
        # Sauvegarde automatique à la fermeture
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        
    def _load_config(self) -> RobotConfig:
        """Charge la configuration depuis fichier ou par défaut"""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    data = json.load(f)
                    return RobotConfig(**data)
            except Exception as e:
                print(f"Erreur lecture config: {e}")
        return RobotConfig()
    
    def _save_config(self):
        """Sauvegarde la configuration actuelle"""
        try:
            config = self._read_config_from_gui()
            if config is None:
                return
            self.current_config = config
            with open(CONFIG_FILE, 'w') as f:
                json.dump(asdict(self.current_config), f, indent=2)
            self._add_log("Configuration sauvegardée ✓")
        except Exception as e:
            self._add_log(f"Erreur sauvegarde: {e}", "error")
    
    def _on_closing(self):
        """Sauvegarde config et ferme l'application"""
        try:
            # Déconnecter le robot avant de fermer
            if self.controller.is_connected:
                self.controller.disconnect()
            # Sauvegarder la configuration
            self._save_config()
        except Exception as e:
            print(f"Erreur fermeture: {e}")
        finally:
            self.root.destroy()
    
    def _create_gui(self):
        """Crée l'interface graphique"""
        
        # Style
        style = ttk.Style()
        style.theme_use('clam')
        
        # Frame principal avec 3 colonnes
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # COLONNE 1 : Connexion et Configuration
        left_frame = ttk.LabelFrame(main_frame, text="🤖 Configuration", padding=10)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=5)
        
        # Connexion
        conn_frame = ttk.LabelFrame(left_frame, text="Connexion", padding=8)
        conn_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(conn_frame, text="Robot IP:").grid(row=0, column=0, sticky=tk.W)
        self.ip_var = tk.StringVar(value=self.current_config.robot_ip)
        ttk.Entry(conn_frame, textvariable=self.ip_var, width=15).grid(row=0, column=1, sticky=tk.W, padx=5)
        
        ttk.Label(conn_frame, text="RMI Port:").grid(row=1, column=0, sticky=tk.W)
        self.port_var = tk.StringVar(value=str(self.current_config.rmi_init_port))
        ttk.Entry(conn_frame, textvariable=self.port_var, width=15).grid(row=1, column=1, sticky=tk.W, padx=5)
        
        # Boutons connexion
        btn_frame = ttk.Frame(conn_frame)
        btn_frame.grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=8)
        
        self.btn_connect = ttk.Button(btn_frame, text="🟢 Connecter", command=self._thread_connect, width=12)
        self.btn_connect.pack(side=tk.LEFT, padx=2)
        
        self.btn_disconnect = ttk.Button(btn_frame, text="🔴 Déconnecter", command=self._thread_disconnect, 
                                         width=12, state=tk.DISABLED)
        self.btn_disconnect.pack(side=tk.LEFT, padx=2)
        
        # Paramètres
        params_frame = ttk.LabelFrame(left_frame, text="Paramètres", padding=8)
        params_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(params_frame, text="Groupe:").grid(row=0, column=0, sticky=tk.W)
        self.group_var = tk.StringVar(value=str(self.current_config.group))
        ttk.Entry(params_frame, textvariable=self.group_var, width=8).grid(row=0, column=1, sticky=tk.W, padx=5)
        
        ttk.Label(params_frame, text="UTool:").grid(row=1, column=0, sticky=tk.W)
        self.utool_var = tk.StringVar(value=str(self.current_config.utool_number))
        ttk.Entry(params_frame, textvariable=self.utool_var, width=8).grid(row=1, column=1, sticky=tk.W, padx=5)
        
        ttk.Label(params_frame, text="DO Port (sortie vers capteur):").grid(row=2, column=0, sticky=tk.W)
        self.di_port_var = tk.StringVar(value=str(self.current_config.wait_di_port))
        ttk.Entry(params_frame, textvariable=self.di_port_var, width=8).grid(row=2, column=1, sticky=tk.W, padx=5)
        
        ttk.Label(params_frame, text="Délai capture (ms):").grid(row=3, column=0, sticky=tk.W)
        self.capture_delay_var = tk.StringVar(value=str(self.current_config.capture_delay_ms))
        ttk.Entry(params_frame, textvariable=self.capture_delay_var, width=8).grid(row=3, column=1, sticky=tk.W, padx=5)
        
        ttk.Label(params_frame, text="Vitesse (mm/s):").grid(row=4, column=0, sticky=tk.W)
        self.speed_var = tk.StringVar(value=str(self.current_config.default_speed))
        ttk.Entry(params_frame, textvariable=self.speed_var, width=8).grid(row=4, column=1, sticky=tk.W, padx=5)
        
        # Statut
        status_frame = ttk.LabelFrame(left_frame, text="Statut", padding=8)
        status_frame.pack(fill=tk.BOTH, pady=5, expand=True)
        
        # Text widget avec scrollbar pour afficher le statut
        status_text_frame = ttk.Frame(status_frame)
        status_text_frame.pack(fill=tk.BOTH, expand=True)
        
        self.status_label = tk.Text(status_text_frame, height=4, width=20, 
                                     font=("Arial", 9, "bold"), 
                                     bg="white", wrap=tk.WORD)
        self.status_label.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Scrollbar pour le statut
        status_scrollbar = ttk.Scrollbar(status_text_frame, orient=tk.VERTICAL, 
                                         command=self.status_label.yview)
        status_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.status_label.config(yscrollcommand=status_scrollbar.set)
        
        # Texte initial
        self.status_label.insert(tk.END, "Déconnecté")
        self.status_label.tag_add("red", "1.0", tk.END)
        self.status_label.tag_config("red", foreground="red")
        self.status_label.tag_config("green", foreground="green")
        self.status_label.tag_config("orange", foreground="orange")
        self.status_label.tag_config("blue", foreground="blue")
        self.status_label.config(state=tk.DISABLED)  # Read-only
        
        # COLONNE 2 : Paramètres de séquence
        mid_frame = ttk.LabelFrame(main_frame, text="📊 Paramètres Séquence", padding=10)
        mid_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        # Axes
        self._create_axis_params(mid_frame)
        
        # Mouvement manuel
        move_frame = ttk.LabelFrame(mid_frame, text="🎮 Mouvement Manuel", padding=8)
        move_frame.pack(fill=tk.X, pady=5)
        
        # Sélection axe
        ttk.Label(move_frame, text="Axe:").grid(row=0, column=0, sticky=tk.W)
        self.move_axis_var = tk.StringVar(value="X")
        axis_combo = ttk.Combobox(move_frame, textvariable=self.move_axis_var, 
                                   values=["X", "Y", "Z"], width=8, state="readonly")
        axis_combo.grid(row=0, column=1, sticky=tk.W, padx=5)
        
        # Distance
        ttk.Label(move_frame, text="Distance (mm):").grid(row=0, column=2, sticky=tk.W)
        self.move_distance_var = tk.StringVar(value="10.0")
        ttk.Entry(move_frame, textvariable=self.move_distance_var, width=12).grid(row=0, column=3, sticky=tk.W, padx=5)
        
        # Vitesse
        ttk.Label(move_frame, text="Vitesse (mm/s):").grid(row=1, column=0, sticky=tk.W)
        self.move_speed_var = tk.StringVar(value="10")
        ttk.Entry(move_frame, textvariable=self.move_speed_var, width=12).grid(row=1, column=1, sticky=tk.W, padx=5)
        
        # Bouton MOVE_RELATIVE
        self.btn_move = ttk.Button(move_frame, text="▶️  MOVE_RELATIVE", 
                                    command=self._thread_move_relative, state=tk.DISABLED, width=20)
        self.btn_move.grid(row=1, column=2, columnspan=2, sticky=tk.EW, padx=5)
        
        # Boutons action
        action_frame = ttk.Frame(mid_frame)
        action_frame.pack(fill=tk.X, pady=10)
        

        self.btn_run = ttk.Button(action_frame, text="▶️  Lancer Séquence", 
                      command=self._thread_run_sequence, state=tk.DISABLED, width=20)
        self.btn_run.pack(side=tk.LEFT, padx=2)

        self.btn_stop = ttk.Button(action_frame, text="⏹️  Arrêt d'urgence", 
                       command=self._emergency_stop, state=tk.DISABLED, width=20)
        self.btn_stop.pack(side=tk.LEFT, padx=2)

        
        # Boutons config
        config_frame = ttk.Frame(mid_frame)
        config_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(config_frame, text="💾 Sauvegarder", command=self._save_config).pack(side=tk.LEFT, padx=2)
        ttk.Button(config_frame, text="↻ Réinitialiser", command=self._reset_config).pack(side=tk.LEFT, padx=2)
        
        # COLONNE 3 : Logs
        right_frame = ttk.LabelFrame(main_frame, text="📋 Logs", padding=10)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        self.log_text = scrolledtext.ScrolledText(right_frame, height=25, width=40, 
                                                   font=("Courier", 8))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED)
        
        # Configurer tags pour couleurs
        self.log_text.tag_config("SUCCESS", foreground="green")
        self.log_text.tag_config("ERROR", foreground="red")
        self.log_text.tag_config("WAIT", foreground="orange")
        self.log_text.tag_config("CONN", foreground="blue")
        self.log_text.tag_config("INFO", foreground="black")
        self.log_text.tag_config("START", foreground="purple")
        self.log_text.tag_config("CANCEL", foreground="red")
    
    def _create_axis_params(self, parent):
        """Crée les paramètres d'axes"""
        
        axes_data = [
            ("Z", "z_min", "z_step", "z_number"),
            ("Y", "y_min", "y_step", "y_number"),
            ("X", "x_min", "x_step", "x_number"),
        ]
        
        for axis, min_attr, step_attr, num_attr in axes_data:
            frame = ttk.LabelFrame(parent, text=f"Axe {axis}", padding=8)
            frame.pack(fill=tk.X, pady=5)
            
            # Min
            ttk.Label(frame, text=f"Min:", width=8).grid(row=0, column=0, sticky=tk.W)
            min_var = tk.StringVar(value=str(getattr(self.current_config, min_attr)))
            ttk.Entry(frame, textvariable=min_var, width=12).grid(row=0, column=1, sticky=tk.W, padx=5)
            setattr(self, f"{min_attr}_var", min_var)
            
            # Step
            ttk.Label(frame, text=f"Step:", width=8).grid(row=0, column=2, sticky=tk.W)
            step_var = tk.StringVar(value=str(getattr(self.current_config, step_attr)))
            ttk.Entry(frame, textvariable=step_var, width=12).grid(row=0, column=3, sticky=tk.W, padx=5)
            setattr(self, f"{step_attr}_var", step_var)
            
            # Nombre
            ttk.Label(frame, text=f"Nombre:", width=8).grid(row=0, column=4, sticky=tk.W)
            num_var = tk.StringVar(value=str(getattr(self.current_config, num_attr)))
            ttk.Entry(frame, textvariable=num_var, width=12).grid(row=0, column=5, sticky=tk.W, padx=5)
            setattr(self, f"{num_attr}_var", num_var)
    
    def _add_log(self, msg: str, level: str = "INFO"):
        """Ajoute un message aux logs"""
        def add_log_thread_safe():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, msg + "\n", level)
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        
        self.root.after(0, add_log_thread_safe)
    
    def _update_status(self, msg: str, status: str):
        """Met à jour le statut"""
        def update_thread_safe():
            colors = {
                "connected": "green",
                "disconnected": "red",
                "connecting": "orange",
                "running": "blue",
                "success": "green",
                "error": "red",
                "waiting": "orange",
                "neutral": "gray"
            }
            color = colors.get(status, "gray")
            
            # Supprimer ancien texte et insérer nouveau
            self.status_label.config(state=tk.NORMAL)
            self.status_label.delete("1.0", tk.END)
            self.status_label.insert(tk.END, msg, color)
            self.status_label.see(tk.END)  # Scroll vers le bas
            self.status_label.config(state=tk.DISABLED)
        
        self.root.after(0, update_thread_safe)
    
    def _update_status_display(self):
        """Gère l'activation/désactivation des boutons selon l'état du robot"""
        state = self.controller.state
        
        # Connexion - toujours actif sauf si déjà connecté
        self.btn_connect.config(state=tk.DISABLED if state != RobotState.DISCONNECTED else tk.NORMAL)
        
        # Disconnect - actif seulement en IDLE
        self.btn_disconnect.config(state=tk.NORMAL if state == RobotState.IDLE else tk.DISABLED)
        
        # Mouvement - actif seulement en IDLE
        self.btn_move.config(state=tk.NORMAL if state == RobotState.IDLE else tk.DISABLED)
        
        # Séquence - actif seulement en IDLE
        self.btn_run.config(state=tk.NORMAL if state == RobotState.IDLE else tk.DISABLED)
        
        # Stop (arrêt urgence) - actif si en mouvement ou séquence
        self.btn_stop.config(state=tk.NORMAL if state in [RobotState.MOVING, RobotState.MOVING_SEQUENCE] else tk.DISABLED)
        
        self.root.after(500, self._update_status_display)
    
    def _read_config_from_gui(self) -> RobotConfig:
        """Lit la configuration depuis l'interface"""
        try:
            return RobotConfig(
                robot_ip=self.ip_var.get(),
                rmi_init_port=int(self.port_var.get()),
                group=int(self.group_var.get()),
                utool_number=int(self.utool_var.get()),
                wait_di_port=int(self.di_port_var.get()),
                capture_delay_ms=int(self.capture_delay_var.get()),
                default_speed=int(self.speed_var.get()),
                z_min=float(self.z_min_var.get()),
                z_step=float(self.z_step_var.get()),
                z_number=int(self.z_number_var.get()),
                y_min=float(self.y_min_var.get()),
                y_step=float(self.y_step_var.get()),
                y_number=int(self.y_number_var.get()),
                x_min=float(self.x_min_var.get()),
                x_step=float(self.x_step_var.get()),
                x_number=int(self.x_number_var.get()),
            )
        except ValueError as e:
            messagebox.showerror("Erreur", f"Paramètre invalide: {e}")
            return None
    
    def _reset_config(self):
        """Réinitialise à la configuration par défaut"""
        default = RobotConfig()
        self.current_config = default
        self.ip_var.set(default.robot_ip)
        self.port_var.set(str(default.rmi_init_port))
        self.group_var.set(str(default.group))
        self.utool_var.set(str(default.utool_number))
        self.di_port_var.set(str(default.wait_di_port))
        self.capture_delay_var.set(str(default.capture_delay_ms))
        self.speed_var.set(str(default.default_speed))
        self.z_min_var.set(str(default.z_min))
        self.z_step_var.set(str(default.z_step))
        self.z_number_var.set(str(default.z_number))
        self.y_min_var.set(str(default.y_min))
        self.y_step_var.set(str(default.y_step))
        self.y_number_var.set(str(default.y_number))
        self.x_min_var.set(str(default.x_min))
        self.x_step_var.set(str(default.x_step))
        self.x_number_var.set(str(default.x_number))
        self._add_log("Configuration réinitialisée ✓")
    
    def _thread_connect(self):
        """Connexion en thread"""
        if self.thread and self.thread.is_alive():
            return
        
        self.thread = threading.Thread(target=self._connect, daemon=True)
        self.thread.start()
    
    def _connect(self):
        """Effectue la connexion"""
        config = self._read_config_from_gui()
        if config is None:
            return
        
        self.current_config = config
        # Sauvegarder la configuration avant la connexion
        self._save_config()
        
        if self.controller.connect(config.robot_ip, config.rmi_init_port):
            time.sleep(0.5)
            try:
                # Lancer la lecture en temps réel des positions dès la connexion
                self.position_read_event = threading.Event()
                position_thread = threading.Thread(
                    target=self.controller.start_reading_position,
                    args=(config.group, self.position_read_event),
                    daemon=True
                )
                position_thread.start()

                # Phase manuelle
                self.controller.wait_manual_phase()
                self.controller.wait_ready_for_remote()
                
                # Référence
                self.controller.read_position(config.group)
                
                # Initialisation
                # Initialisation RMI avec UTool et UFrame configurés
                # FRC_Initialize démarre automatiquement le programme RMI_MOVE
                self.controller.initialize_rmi(config.group, utool=config.utool_number, uframe=config.uframe_number)
                
                self._add_log("✅ Robot prêt pour séquence", "SUCCESS")
                
            except Exception as e:
                self._add_log(f"❌ Erreur préparation: {e}", "ERROR")
                self.controller.disconnect()
    
    def _thread_disconnect(self):
        """Déconnexion en thread"""
        if self.thread and self.thread.is_alive():
            return
        
        # Arrêter la lecture en temps réel des positions
        self.position_read_event.set()
        
        self.thread = threading.Thread(target=self.controller.disconnect, daemon=True)
        self.thread.start()
    
    def _thread_move_relative(self):
        """Lance un mouvement relatif en thread"""
        if self.thread and self.thread.is_alive():
            messagebox.showwarning("Attention", "Une opération est déjà en cours")
            return
        
        self.thread = threading.Thread(target=self._move_relative, daemon=True)
        self.thread.start()
    
    def _move_relative(self):
        """Effectue un mouvement relatif"""
        try:
            self.controller._set_state(RobotState.MOVING)

            axis = self.move_axis_var.get()
            distance = float(self.move_distance_var.get())
            speed = float(self.move_speed_var.get())

            # Récupérer UTool et UFrame depuis la config courante
            utool = int(self.utool_var.get()) if hasattr(self, 'utool_var') else 1
            uframe = 0  # UFrame = 0 (frame par défaut, mouvements relatifs)

            self._add_log(f"Mouvement {axis}: {distance}mm à {speed}mm/s", "START")

            # Préparer les valeurs de déplacement (dx, dy, dz)
            dx, dy, dz = 0.0, 0.0, 0.0
            if axis == "X":
                dx = distance
            elif axis == "Y":
                dy = distance
            elif axis == "Z":
                dz = distance

            # Exécuter le mouvement sans bloquer
            move_item = self.controller.linear_relative_async(dx, dy, dz, speed, utool=utool, uframe=uframe)
            
            # Attendre que le mouvement soit terminé (vérifier que le robot n'est plus en mouvement)
            time.sleep(0.2)
            while True:
                # Si une réponse est arrivée et contient une erreur, arrêter le mouvement
                if move_item.get("event") and move_item["event"].is_set() and "error" in move_item:
                    raise move_item["error"]

                status = self.controller.get_status()
                if status.get("InMotion", 0) == 0:
                    break
                time.sleep(0.1)
            
            self._add_log(f"✅ Mouvement {axis} terminé", "SUCCESS")
            self.controller._set_state(RobotState.IDLE)

        except ValueError as e:
            messagebox.showerror("Erreur", f"Paramètre invalide: {e}")
            self._add_log(f"Erreur mouvement: {e}", "ERROR")
            self.controller._set_state(RobotState.IDLE)
        except Exception as e:
            self._add_log(f"❌ Erreur mouvement: {e}", "ERROR")
            self.controller._set_state(RobotState.IDLE)
    
    def _thread_run_sequence(self):
        """Lance la séquence en thread"""
        if self.thread and self.thread.is_alive():
            messagebox.showwarning("Attention", "Une séquence est déjà en cours")
            return
        
        config = self._read_config_from_gui()
        if config is None:
            return
        
        self.cancel_event.clear()
        self.thread = threading.Thread(target=self._run_sequence, args=(config,), daemon=True)
        self.thread.start()
    
    def _run_sequence(self, config: RobotConfig):
        """Exécute la séquence"""
        try:
            self.controller._set_state(RobotState.MOVING_SEQUENCE)
            self.controller.run_sequence(config, self.cancel_event)
            self.controller._set_state(RobotState.IDLE)
        except Exception as e:
            self._add_log(f"❌ Erreur: {e}", "ERROR")
            self.controller._set_state(RobotState.IDLE)
    
    def _emergency_stop(self):
        """Arrêt d'urgence"""
        self.cancel_event.set()
        try:
            self.controller.send_cmd({"Command": "FRC_Abort"})
            self._add_log("⏹️  ARRÊT D'URGENCE ACTIVÉ", "CANCEL")
        except Exception as e:
            self._add_log(f"Erreur arrêt: {e}", "ERROR")


# ==================================================
# PROGRAMME PRINCIPAL
# ==================================================

if __name__ == "__main__":
    root = tk.Tk()
    gui = RobotControllerGUI(root)
    root.mainloop()
