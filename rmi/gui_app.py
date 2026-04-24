"""
GUI Tkinter pour SpraySpy — pure vue, zéro logique réseau.

L'interface communique UNIQUEMENT avec l'Orchestrator via l'EventBus.
Les mises à jour depuis les threads RMI sont récupérées par un `root.after()`
qui dépile l'EventBus toutes les 50 ms.

Layout 3 colonnes :
  1. Configuration / Connexion / Statut
  2. Paramètres séquence / Mouvement manuel / Actions
  3. Logs
"""

import sys
import csv
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import json
from dataclasses import asdict
from pathlib import Path

# Allow running this file directly (python rmi/gui_app.py) as well as
# as part of the package (python -m rmi).
if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "rmi"  # noqa: A001

from .robot_config import RobotConfig, load_config, save_config  # noqa: E402
from .orchestrator import Orchestrator, EventType, EventBus  # noqa: E402
from .state_machine import State  # noqa: E402

CONFIG_FILE = Path(__file__).resolve().parent.parent / "robot_config.json"
LAST_PARAM_FILE = Path(__file__).resolve().parent.parent / "last_param.json"

# Intervalle de polling EventBus (ms)
EVENT_POLL_MS = 50
# Intervalle de rafraîchissement boutons (ms)
BUTTON_POLL_MS = 500


class SpraySpyApp:
    """Application principale Tkinter — vue uniquement."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("SpraySpy — Fanuc RMI Controller")
        self.root.geometry("1280x750")

        # Orchestrateur (toute la logique)
        self.orch = Orchestrator()

        # Configuration courante
        self.current_config = self._load_config()

        # Données CSV chargées
        self._csv_steps: list = []

        # ID du timer _update_buttons (évite les doublons)
        self._btn_after_id: str | None = None

        # État servo (mis à jour par le poller de positions)
        self._servo_ready: bool = True

        # Construire la GUI
        self._apply_style()
        self._build_gui()
        self._update_buttons()

        # Boucles Tkinter
        self._poll_events()

        # Fermeture propre
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        # Raccourci clavier : Escape → Emergency Stop
        self.root.bind("<Escape>", lambda e: self._on_emergency_stop())

    # ==================================================================
    # Style
    # ==================================================================

    @staticmethod
    def _apply_style() -> None:
        style = ttk.Style()
        style.theme_use("clam")

    # ==================================================================
    # Construction GUI
    # ==================================================================

    def _build_gui(self) -> None:
        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._build_left_column(main)
        self._build_mid_column(main)
        self._build_right_column(main)

    # --- Colonne 1 : Configuration / Connexion / Statut ---------------

    def _build_left_column(self, parent: ttk.Frame) -> None:
        col = ttk.LabelFrame(parent, text="🤖 Configuration", padding=10)
        col.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=5)

        # -- Connexion --
        conn = ttk.LabelFrame(col, text="Connection", padding=8)
        conn.pack(fill=tk.X, pady=5)

        ttk.Label(conn, text="Robot IP:").grid(row=0, column=0, sticky=tk.W)
        self.ip_var = tk.StringVar(value=self.current_config.robot_ip)
        ttk.Entry(conn, textvariable=self.ip_var, width=15).grid(
            row=0, column=1, sticky=tk.W, padx=5
        )

        ttk.Label(conn, text="RMI Port:").grid(row=1, column=0, sticky=tk.W)
        self.port_var = tk.StringVar(value=str(self.current_config.rmi_init_port))
        ttk.Entry(conn, textvariable=self.port_var, width=15).grid(
            row=1, column=1, sticky=tk.W, padx=5
        )

        btn_frame = ttk.Frame(conn)
        btn_frame.grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=8)

        self.btn_connect = ttk.Button(
            btn_frame, text="🟢 Connect",
            command=self._on_connect, width=12,
        )
        self.btn_connect.pack(side=tk.LEFT, padx=2)

        self.btn_disconnect = ttk.Button(
            btn_frame, text="🔴 Disconnect",
            command=self._on_disconnect, width=12, state=tk.DISABLED,
        )
        self.btn_disconnect.pack(side=tk.LEFT, padx=2)

        # -- Paramètres --
        params = ttk.LabelFrame(col, text="Parameters", padding=8)
        params.pack(fill=tk.X, pady=5)

        param_fields = [
            ("DO Port (sensor):", "do_port_var", self.current_config.capture_do_port),
            ("Capture delay (ms):", "capture_delay_var", self.current_config.capture_delay_ms),
            ("Speed (%):", "speed_var", self.current_config.default_speed),
            ("Acq Time (s):", "acq_time_var", self.current_config.acq_time_s),
        ]
        for row, (label, attr, default) in enumerate(param_fields):
            ttk.Label(params, text=label).grid(row=row, column=0, sticky=tk.W)
            var = tk.StringVar(value=str(default))
            ttk.Entry(params, textvariable=var, width=8).grid(
                row=row, column=1, sticky=tk.W, padx=5
            )
            setattr(self, attr, var)

        # -- Statut --
        status_frame = ttk.LabelFrame(col, text="Status", padding=8)
        status_frame.pack(fill=tk.BOTH, pady=5, expand=True)

        sf = ttk.Frame(status_frame)
        sf.pack(fill=tk.BOTH, expand=True)

        self.status_text = tk.Text(
            sf, height=6, width=28,
            font=("Arial", 9, "bold"), bg="white", wrap=tk.WORD,
        )
        self.status_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        sb = ttk.Scrollbar(sf, orient=tk.VERTICAL, command=self.status_text.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.status_text.config(yscrollcommand=sb.set)

        # Tags couleurs
        for tag, color in [
            ("green", "green"), ("red", "red"),
            ("orange", "orange"), ("blue", "blue"),
            ("gray", "gray"),
        ]:
            self.status_text.tag_config(tag, foreground=color)

        self.status_text.insert(tk.END, "Disconnected", "red")
        self.status_text.config(state=tk.DISABLED)

        # -- État FSM (petit label en bas) --
        self.fsm_label = ttk.Label(col, text="State: DISCONNECTED", font=("Courier", 8))
        self.fsm_label.pack(fill=tk.X, pady=2)

    # --- Colonne 2 : Séquence / Mouvement / Actions ------------------

    def _build_mid_column(self, parent: ttk.Frame) -> None:
        col = ttk.Frame(parent)
        col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        # ── Séquence 3D (cadre englobant) ──
        seq3d = ttk.LabelFrame(col, text="📊 3D Sequence", padding=8)
        seq3d.pack(fill=tk.X, pady=3)

        self._build_axis_params(seq3d)

        self.btn_run = ttk.Button(
            seq3d, text="▶️  Run Sequence",
            command=self._on_run_sequence, state=tk.DISABLED,
        )
        self.btn_run.pack(fill=tk.X, pady=4)

        # ── Séquence CSV ──
        csv_frame = ttk.LabelFrame(col, text="📂 CSV Sequence", padding=8)
        csv_frame.pack(fill=tk.X, pady=3)

        csv_top = ttk.Frame(csv_frame)
        csv_top.pack(fill=tk.X)

        self.btn_load_csv = ttk.Button(
            csv_top, text="📁 Load CSV",
            command=self._on_load_csv, width=14,
        )
        self.btn_load_csv.pack(side=tk.LEFT, padx=2)

        self.csv_label = ttk.Label(csv_top, text="No file loaded")
        self.csv_label.pack(side=tk.LEFT, padx=5)

        self.btn_run_csv = ttk.Button(
            csv_frame, text="▶️  Run CSV Sequence",
            command=self._on_run_csv_sequence, state=tk.DISABLED,
        )
        self.btn_run_csv.pack(fill=tk.X, pady=4)

        # ── Barre de progression ──
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_bar = ttk.Progressbar(
            col, variable=self.progress_var, maximum=100, mode="determinate",
        )
        self.progress_bar.pack(fill=tk.X, pady=3)

        self.progress_label = ttk.Label(col, text="")
        self.progress_label.pack(fill=tk.X)

        # ── Abort / Arrêt d'urgence ──
        actions = ttk.Frame(col)
        actions.pack(fill=tk.X, pady=5)

        self.btn_abort_seq = ttk.Button(
            actions, text="🛑 Abort Sequence",
            command=self._on_abort_sequence, state=tk.DISABLED, width=16,
        )
        self.btn_abort_seq.pack(side=tk.LEFT, padx=2)

        self.btn_recover = ttk.Button(
            actions, text="🧹 Clear Fault",
            command=self._on_recover, state=tk.DISABLED, width=12,
        )
        self.btn_recover.pack(side=tk.LEFT, padx=2)

        self.btn_stop = ttk.Button(
            actions, text="⏹️  Emergency Stop [ESC]",
            command=self._on_emergency_stop, state=tk.DISABLED, width=24,
        )
        self.btn_stop.pack(side=tk.LEFT, padx=2)

        # ── Mouvement Manuel ──
        move = ttk.LabelFrame(col, text="🎮 Manual Movement", padding=8)
        move.pack(fill=tk.X, pady=3)

        ttk.Label(move, text="Axis:").grid(row=0, column=0, sticky=tk.W)
        self.move_axis_var = tk.StringVar(value="X")
        ttk.Combobox(
            move, textvariable=self.move_axis_var,
            values=["X", "Y", "Z", "θ1 (J4)", "θ2 (J5)"], width=10, state="readonly",
        ).grid(row=0, column=1, sticky=tk.W, padx=5)

        self.move_dist_label = ttk.Label(move, text="Distance (mm):")
        self.move_dist_label.grid(row=0, column=2, sticky=tk.W)
        self.move_dist_var = tk.StringVar(value="10.0")
        ttk.Entry(move, textvariable=self.move_dist_var, width=12).grid(
            row=0, column=3, sticky=tk.W, padx=5
        )

        self.move_speed_label = ttk.Label(move, text="Speed (%):")  
        self.move_speed_label.grid(row=1, column=0, sticky=tk.W)
        self.move_speed_var = tk.StringVar(value="10")
        ttk.Entry(move, textvariable=self.move_speed_var, width=12).grid(
            row=1, column=1, sticky=tk.W, padx=5
        )

        # Mise à jour dynamique des labels selon l'axe sélectionné
        self.move_axis_var.trace_add("write", self._on_axis_changed)

        self.btn_move = ttk.Button(
            move, text="▶️  MOVE_RELATIVE",
            command=self._on_move_relative, state=tk.DISABLED, width=20,
        )
        self.btn_move.grid(row=1, column=2, columnspan=2, sticky=tk.EW, padx=5)

        # Rotation articulaire
        jrot = ttk.LabelFrame(col, text="🔄 Joint Rotation", padding=8)
        jrot.pack(fill=tk.X, pady=3)

        ttk.Label(jrot, text="Joint:").grid(row=0, column=0, sticky=tk.W)
        self.joint_var = tk.StringVar(value="J1")
        ttk.Combobox(
            jrot, textvariable=self.joint_var,
            values=["J1", "J2", "J3", "J4", "J5", "J6"],
            width=8, state="readonly",
        ).grid(row=0, column=1, sticky=tk.W, padx=5)

        ttk.Label(jrot, text="Angle (°):").grid(row=0, column=2, sticky=tk.W)
        self.joint_angle_var = tk.StringVar(value="5.0")
        ttk.Entry(jrot, textvariable=self.joint_angle_var, width=12).grid(
            row=0, column=3, sticky=tk.W, padx=5
        )

        ttk.Label(jrot, text="Speed (%):").grid(row=1, column=0, sticky=tk.W)
        self.joint_speed_var = tk.StringVar(value="10")
        ttk.Entry(jrot, textvariable=self.joint_speed_var, width=12).grid(
            row=1, column=1, sticky=tk.W, padx=5
        )

        self.btn_joint_rotate = ttk.Button(
            jrot, text="▶️  JOINT_RELATIVE",
            command=self._on_joint_rotate, state=tk.DISABLED, width=20,
        )
        self.btn_joint_rotate.grid(row=1, column=2, columnspan=2, sticky=tk.EW, padx=5)

        # ── Position de référence + Paramètres (côte à côte) ──
        bottom_row = ttk.Frame(col)
        bottom_row.pack(fill=tk.X, pady=3)

        ref_frame = ttk.LabelFrame(bottom_row, text="📌 Reference Position", padding=8)
        ref_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        self.btn_set_ref = ttk.Button(
            ref_frame, text="📌 SET_REF_POSITION",
            command=self._on_set_ref, state=tk.DISABLED, width=18,
        )
        self.btn_set_ref.pack(side=tk.LEFT, padx=2)

        self.btn_go_ref = ttk.Button(
            ref_frame, text="🏠 GO_TO_REF",
            command=self._on_go_to_ref, state=tk.DISABLED, width=14,
        )
        self.btn_go_ref.pack(side=tk.LEFT, padx=2)

        cfg_frame = ttk.LabelFrame(bottom_row, text="⚙️ Settings", padding=8)
        cfg_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

        ttk.Button(cfg_frame, text="💾 Save", command=self._save_config).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(cfg_frame, text="📂 Load", command=self._load_config_dialog).pack(
            side=tk.LEFT, padx=2
        )

    def _build_axis_params(self, parent: ttk.Frame) -> None:
        axes = [
            ("Z", "z_min", "z_step", "z_number", "Delta Z (mm)", "Nz"),
            ("Y", "y_min", "y_step", "y_number", "Delta Y (mm)", "Ny"),
            ("X", "x_min", "x_step", "x_number", "Delta X (mm)", "Nx"),
        ]
        for axis_name, min_a, step_a, num_a, step_label, num_label in axes:
            frame = ttk.LabelFrame(parent, text=f"Axe {axis_name}", padding=8)
            frame.pack(fill=tk.X, pady=3)

            # Variable min conservée (= 0.0) mais non affichée
            min_var = tk.StringVar(value="0.0")
            setattr(self, f"{min_a}_var", min_var)

            ttk.Label(frame, text=f"{step_label}:", width=12).grid(row=0, column=0, sticky=tk.W)
            step_var = tk.StringVar(value=str(getattr(self.current_config, step_a)))
            ttk.Entry(frame, textvariable=step_var, width=12).grid(
                row=0, column=1, sticky=tk.W, padx=5
            )
            setattr(self, f"{step_a}_var", step_var)

            ttk.Label(frame, text=f"{num_label}:", width=6).grid(row=0, column=2, sticky=tk.W)
            num_var = tk.StringVar(value=str(getattr(self.current_config, num_a)))
            ttk.Entry(frame, textvariable=num_var, width=12).grid(
                row=0, column=3, sticky=tk.W, padx=5
            )
            setattr(self, f"{num_a}_var", num_var)

    # --- Colonne 3 : Logs ---------------------------------------------

    def _build_right_column(self, parent: ttk.Frame) -> None:
        col = ttk.LabelFrame(parent, text="📋 Logs", padding=10)
        col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        self.log_text = scrolledtext.ScrolledText(
            col, height=25, width=45, font=("Courier", 8),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED)

        # Tags couleur
        for tag, color in [
            ("INFO", "black"), ("SUCCESS", "green"), ("ERROR", "red"),
            ("WARNING", "orange"), ("CONN", "blue"), ("START", "purple"),
        ]:
            self.log_text.tag_config(tag, foreground=color)

        # Bouton Clear logs
        ttk.Button(col, text="🗑 Clear logs", command=self._clear_logs).pack(
            fill=tk.X, pady=3
        )

    # ==================================================================
    # EventBus polling (boucle Tkinter thread-safe)
    # ==================================================================

    def _poll_events(self) -> None:
        """Dépile tous les événements de l'orchestrateur."""
        for evt in self.orch.event_bus.get_all():
            if evt.type == EventType.LOG:
                self._append_log(evt.data, evt.level)
            elif evt.type == EventType.STATUS:
                self._set_status(evt.data["msg"], evt.data["status"])
            elif evt.type == EventType.STATE_CHANGED:
                self._on_state_changed(evt.data)
            elif evt.type == EventType.POSITION:
                srv = evt.data.get("servo_ready", 1)
                self._servo_ready = (srv == 1)
            elif evt.type == EventType.SEQUENCE_PROGRESS:
                self._on_progress(evt.data)
            elif evt.type == EventType.ERROR:
                self._append_log(str(evt.data), "ERROR")

        self.root.after(EVENT_POLL_MS, self._poll_events)

    # ==================================================================
    # Mises à jour GUI
    # ==================================================================

    def _append_log(self, msg: str, level: str = "INFO") -> None:
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n", level)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _clear_logs(self) -> None:
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _set_status(self, msg: str, status: str) -> None:
        color_map = {
            "connected": "green", "disconnected": "red",
            "connecting": "orange", "running": "blue",
            "success": "green", "error": "red",
            "waiting": "orange", "neutral": "gray",
        }
        color = color_map.get(status, "gray")
        self.status_text.config(state=tk.NORMAL)
        self.status_text.delete("1.0", tk.END)
        self.status_text.insert(tk.END, msg, color)
        self.status_text.see(tk.END)
        self.status_text.config(state=tk.DISABLED)

    def _on_state_changed(self, data: dict) -> None:
        self.fsm_label.config(text=f"State: {data['new']}")
        new = data["new"]
        if new == State.DISCONNECTED.value:
            self._servo_ready = False
        elif new == State.IDLE.value:
            # _wait_for_auto() garantit servo=ON avant IDLE
            self._servo_ready = True
        self._update_buttons()

    def _on_axis_changed(self, *_args) -> None:
        """Met à jour les labels Distance/Speed selon l'axe sélectionné."""
        axis = self.move_axis_var.get()
        if axis.startswith("θ"):
            self.move_dist_label.config(text="Angle (°):")
        else:
            self.move_dist_label.config(text="Distance (mm):")
    def _on_progress(self, data: dict) -> None:
        self.progress_var.set(data["percent"])
        self.progress_label.config(
            text=f"{data['current']}/{data['total']} ({data['percent']:.1f}%)"
        )

    def _update_buttons(self) -> None:
        """Active/désactive les boutons selon l'état FSM."""
        state = self.orch.fsm.state

        is_disconnected = state == State.DISCONNECTED
        is_idle = state == State.IDLE
        is_sequencing = state == State.SEQUENCING
        is_movable = is_idle and self._servo_ready

        # Connecté = tout état sauf DISCONNECTED et DISCONNECTING
        is_connected = state not in (State.DISCONNECTED, State.DISCONNECTING)

        # Peut se déconnecter si connecté et pas en mouvement/séquence
        can_disconnect = state in (
            State.IDLE, State.CONNECTED, State.WAIT_MANUAL,
            State.WAIT_AUTO, State.HOLD, State.ERROR,
        )

        # Emergency Stop : accessible dès qu'on est connecté
        can_stop = is_connected

        is_hold = state == State.HOLD

        self.btn_connect.config(
            state=tk.NORMAL if (is_disconnected or state == State.ERROR) else tk.DISABLED
        )
        self.btn_disconnect.config(state=tk.NORMAL if can_disconnect else tk.DISABLED)
        self.btn_move.config(state=tk.NORMAL if is_movable else tk.DISABLED)
        self.btn_joint_rotate.config(state=tk.NORMAL if is_movable else tk.DISABLED)
        self.btn_run.config(state=tk.NORMAL if is_movable else tk.DISABLED)
        self.btn_run_csv.config(
            state=tk.NORMAL if (is_movable and self._csv_steps) else tk.DISABLED
        )
        self.btn_abort_seq.config(state=tk.NORMAL if is_sequencing else tk.DISABLED)
        self.btn_recover.config(state=tk.NORMAL if is_hold else tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL if can_stop else tk.DISABLED)
        self.btn_set_ref.config(state=tk.NORMAL if is_idle else tk.DISABLED)
        self.btn_go_ref.config(
            state=tk.NORMAL if (is_idle and self.orch.has_ref_position) else tk.DISABLED
        )

        # Replanifier (annuler l'ancien timer pour éviter les doublons)
        if self._btn_after_id is not None:
            self.root.after_cancel(self._btn_after_id)
        self._btn_after_id = self.root.after(BUTTON_POLL_MS, self._update_buttons)

    # ==================================================================
    # Lecture / Écriture config
    # ==================================================================

    def _load_config(self) -> RobotConfig:
        """Charge la config depuis last_param.json (dernière session)."""
        try:
            return load_config(LAST_PARAM_FILE)
        except Exception:
            try:
                return load_config(CONFIG_FILE)
            except Exception:
                # Valeurs par défaut hardcodées (snapshot last_param.json)
                return RobotConfig(
                    robot_ip="192.168.1.100",
                    rmi_init_port=16001,
                    group_mask=1,
                    utool_number=1,
                    uframe_number=0,
                    capture_do_port=81,
                    capture_delay_ms=100,
                    acq_time_s=1.0,
                    default_speed=10,
                    speed_override=100,
                    z_min=0.0,
                    z_step=10.0,
                    z_number=10,
                    y_min=0.0,
                    y_step=10.0,
                    y_number=10,
                    x_min=0.0,
                    x_step=10.0,
                    x_number=10,
                    motion_timeout=60.0,
                    poll_interval=0.1,
                )

    def _read_config_from_gui(self) -> RobotConfig | None:
        try:
            return RobotConfig(
                robot_ip=self.ip_var.get(),
                rmi_init_port=int(self.port_var.get()),
                group_mask=1,
                utool_number=1,
                uframe_number=0,
                capture_do_port=int(self.do_port_var.get()),
                capture_delay_ms=int(self.capture_delay_var.get()),
                default_speed=int(self.speed_var.get()),
                speed_override=100,
                acq_time_s=float(self.acq_time_var.get()),
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
            messagebox.showerror("Error", f"Invalid parameter: {e}")
            return None

    def _save_config(self) -> None:
        config = self._read_config_from_gui()
        if config is None:
            return
        path = filedialog.asksaveasfilename(
            title="Save Configuration",
            defaultextension=".json",
            initialdir=str(CONFIG_FILE.parent),
            initialfile=CONFIG_FILE.name,
            filetypes=[("JSON files", "*.json"), ("All", "*.*")],
        )
        if not path:
            return
        self.current_config = config
        try:
            save_config(config, Path(path))
            self._append_log(f"[Config] Saved → {Path(path).name} ✓", "INFO")
        except Exception as e:
            self._append_log(f"[Config] Save error: {e}", "ERROR")

    def _load_config_dialog(self) -> None:
        """Charge une configuration depuis un fichier JSON via dialogue."""
        path = filedialog.askopenfilename(
            title="Load Configuration",
            initialdir=str(CONFIG_FILE.parent),
            filetypes=[("JSON files", "*.json"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            config = load_config(Path(path))
            self.current_config = config
            self._apply_config_to_gui(config)
            self._append_log(
                f"[Config] Loaded ← {Path(path).name} ✓", "INFO"
            )
        except Exception as e:
            messagebox.showerror(
                "Error", f"Unable to load configuration:\n{e}"
            )

    def _apply_config_to_gui(self, config: RobotConfig) -> None:
        """Applique un objet RobotConfig sur tous les champs GUI."""
        self.ip_var.set(config.robot_ip)
        self.port_var.set(str(config.rmi_init_port))
        self.do_port_var.set(str(config.capture_do_port))
        self.capture_delay_var.set(str(config.capture_delay_ms))
        self.speed_var.set(str(config.default_speed))
        self.acq_time_var.set(str(config.acq_time_s))
        self.z_min_var.set(str(config.z_min))
        self.z_step_var.set(str(config.z_step))
        self.z_number_var.set(str(config.z_number))
        self.y_min_var.set(str(config.y_min))
        self.y_step_var.set(str(config.y_step))
        self.y_number_var.set(str(config.y_number))
        self.x_min_var.set(str(config.x_min))
        self.x_step_var.set(str(config.x_step))
        self.x_number_var.set(str(config.x_number))

    # ==================================================================
    # Actions utilisateur → Orchestrator
    # ==================================================================

    def _on_connect(self) -> None:
        config = self._read_config_from_gui()
        if config is None:
            return
        self.current_config = config
        self.orch.connect_async(config)

    def _on_disconnect(self) -> None:
        self.orch.disconnect_async()

    def _on_move_relative(self) -> None:
        try:
            axis = self.move_axis_var.get()
            distance = float(self.move_dist_var.get())
            speed = float(self.move_speed_var.get())
        except ValueError as e:
            messagebox.showerror("Error", f"Invalid parameter: {e}")
            return
        self.orch.move_relative_async(axis, distance, speed, "FINE", 0)

    def _on_joint_rotate(self) -> None:
        try:
            joint = self.joint_var.get()
            angle = float(self.joint_angle_var.get())
            speed_pct = int(self.joint_speed_var.get())
        except ValueError as e:
            messagebox.showerror("Error", f"Invalid parameter: {e}")
            return
        self.orch.joint_rotate_async(joint, angle, speed_pct)

    def _on_load_csv(self) -> None:
        """Ouvre un fichier CSV et charge les étapes de séquence.

        Colonnes attendues (ordre) : X, Y, Z, J4, J5
        Accepte avec ou sans ligne d'en-tête.
        Délimiteur : ';' ou ',' (détection auto).
        """
        path = filedialog.askopenfilename(
            title="Load CSV Sequence",
            filetypes=[("CSV files", "*.csv"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            steps = []
            expected_headers = {"X", "Y", "Z", "J4", "J5"}
            col_order = ["X", "Y", "Z", "J4", "J5"]

            with open(path, newline="", encoding="utf-8-sig") as f:
                raw = f.read()

            # Détecter le délimiteur
            delim = ";" if ";" in raw.split("\n")[0] else ","

            lines = raw.strip().splitlines()
            if not lines:
                messagebox.showwarning("Empty CSV", "The file is empty.")
                return

            # Détecter si la première ligne est un en-tête
            first_fields = [c.strip() for c in lines[0].split(delim)]
            has_header = expected_headers.issubset(
                {f.upper() for f in first_fields}
            )

            if has_header:
                # Mapper les noms de colonnes (insensible à la casse)
                header_map = {
                    f.strip().upper(): idx
                    for idx, f in enumerate(first_fields)
                }
                data_lines = lines[1:]
            else:
                # Pas d'en-tête : colonnes dans l'ordre X, Y, Z, J4, J5
                header_map = {name: idx for idx, name in enumerate(col_order)}
                data_lines = lines

            for line_num, line in enumerate(data_lines, start=2 if has_header else 1):
                line = line.strip()
                if not line:
                    continue
                fields = [c.strip() for c in line.split(delim)]
                try:
                    step = {}
                    for name in col_order:
                        idx = header_map.get(name)
                        if idx is not None and idx < len(fields):
                            step[name] = float(fields[idx])
                        else:
                            step[name] = 0.0
                    steps.append(step)
                except (ValueError, TypeError) as e:
                    self._append_log(
                        f"[CSV] Line {line_num} skipped: {e}", "WARNING"
                    )

            if not steps:
                messagebox.showwarning(
                    "Empty CSV",
                    "No valid steps found in the file.",
                )
                return
            self._csv_steps = steps
            name = Path(path).name
            self.csv_label.config(text=f"{name} ({len(steps)} steps)")
            self._append_log(
                f"[CSV] Loaded: {name} — {len(steps)} steps", "INFO"
            )
            # Aperçu des premières lignes dans le log
            for i, s in enumerate(steps[:3]):
                self._append_log(
                    f"  [{i+1}] X={s['X']:+.2f} Y={s['Y']:+.2f} "
                    f"Z={s['Z']:+.2f} J4={s['J4']:+.2f} J5={s['J5']:+.2f}",
                    "INFO",
                )
            if len(steps) > 3:
                self._append_log(f"  ... ({len(steps) - 3} more lines)", "INFO")
            self._update_buttons()
        except Exception as e:
            messagebox.showerror("CSV Error", f"Unable to read file:\n{e}")

    def _on_run_csv_sequence(self) -> None:
        if not self._csv_steps:
            messagebox.showwarning("CSV", "No CSV sequence loaded.")
            return
        config = self._read_config_from_gui()
        if config is None:
            return
        self.progress_var.set(0)
        self.progress_label.config(text="")
        self.orch.run_csv_sequence_async(self._csv_steps, config)

    def _on_run_sequence(self) -> None:
        config = self._read_config_from_gui()
        if config is None:
            return
        self.progress_var.set(0)
        self.progress_label.config(text="")
        self.orch.run_sequence_async(config)

    def _on_abort_sequence(self) -> None:
        self.orch.abort_sequence()

    def _on_recover(self) -> None:
        self.orch.recover_async()

    def _on_emergency_stop(self) -> None:
        self.orch.emergency_stop()

    def _on_set_ref(self) -> None:
        self.orch.set_ref_position_async()

    def _on_go_to_ref(self) -> None:
        self.orch.go_to_ref_async()

    # ==================================================================
    # Fermeture
    # ==================================================================

    def _on_closing(self) -> None:
        try:
            if self.orch.client.is_connected:
                self.orch.emergency_stop()
        except Exception:
            pass
        # Sauvegarder tous les paramètres dans last_param.json
        try:
            config = self._read_config_from_gui()
            if config is not None:
                save_config(config, LAST_PARAM_FILE)
        except Exception:
            pass
        finally:
            self.root.destroy()


# ======================================================================
# Point d'entrée direct
# ======================================================================

def main() -> None:
    root = tk.Tk()
    SpraySpyApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
