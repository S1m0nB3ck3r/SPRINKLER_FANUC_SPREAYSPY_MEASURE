"""
Configuration du robot Fanuc et persistance JSON.

Dataclass centralisée pour tous les paramètres : connexion, frames,
paramètres de séquence 3D et capteur.
"""

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional


@dataclass
class RobotConfig:
    """Configuration complète du robot et de la séquence de scan."""

    # --- Connexion RMI ---
    robot_ip: str = "192.168.1.100"
    rmi_init_port: int = 16001          # Port initial FRC_Connect (fixe)
    group_mask: int = 1                 # Bit-field groupes (1 = groupe 1 seul)

    # --- Frames & outils ---
    utool_number: int = 1
    uframe_number: int = 0             # 0 = World frame (mouvements relatifs)

    # --- Capteur ---
    capture_do_port: int = 0            # Port DO (0 = désactivé, sinon n° DOUT)
    capture_delay_ms: int = 100        # Délai d'acquisition capteur (ms)
    acq_time_s: float = 1.0            # SpraySpy acquisition time (s)

    # --- Mouvement ---
    default_speed: int = 10            # mm/s

    # --- Séquence 3D ---
    z_min: float = 0.0
    z_step: float = -1.0
    z_number: int = 100

    y_min: float = -500.0
    y_step: float = 10.0
    y_number: int = 101

    x_min: float = -500.0
    x_step: float = 10.0
    x_number: int = 101

    # --- Override vitesse ---
    speed_override: int = 100           # Override programme (1-100%)

    # --- Timeouts (secondes) ---
    connect_timeout: float = 5.0
    command_timeout: float = 10.0
    motion_timeout: float = 60.0
    poll_interval: float = 0.1         # Intervalle polling positions (s)

    @property
    def total_points(self) -> int:
        """Nombre total de points dans la séquence 3D."""
        return self.x_number * self.y_number * self.z_number


def load_config(path: Path) -> RobotConfig:
    """Charge la configuration depuis un fichier JSON.

    Retourne la config par défaut si le fichier n'existe pas ou est invalide.
    """
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            # Ne garder que les clés connues du dataclass
            known = {fld.name for fld in RobotConfig.__dataclass_fields__.values()}
            filtered = {k: v for k, v in data.items() if k in known}
            return RobotConfig(**filtered)
        except Exception as e:
            print(f"[Config] Erreur lecture {path}: {e} — config par défaut")
    return RobotConfig()


def save_config(config: RobotConfig, path: Path) -> None:
    """Sauvegarde la configuration dans un fichier JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(config), f, indent=2, ensure_ascii=False)
