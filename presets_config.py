"""
Configurations pré-définies pour SpraySpy Fanuc Robot
Variantes pour différents use-cases
"""

import json
from pathlib import Path
from dataclasses import asdict, dataclass


# ==================================================
# CONFIGURATIONS PÉDÉFINIES
# ==================================================

PRESET_CONFIGS = {
    
    # ===== TEST / DÉMO =====
    "demo_small": {
        "description": "Test rapide - 18 points (2×3×3)",
        "robot_ip": "192.168.0.10",
        "rmi_init_port": 16001,
        "group": 1,
        "utool_number": 1,
        "wait_di_port": 1,
        "default_speed": 15,
        "z_min": 0.0,
        "z_step": -5.0,
        "z_number": 2,
        "y_min": -50.0,
        "y_step": 25.0,
        "y_number": 3,
        "x_min": -50.0,
        "x_step": 25.0,
        "x_number": 3,
        "estimated_points": 18,
        "estimated_time_minutes": 1,
    },
    
    "demo_medium": {
        "description": "Test moyen - 125 points (5×5×5)",
        "robot_ip": "192.168.0.10",
        "rmi_init_port": 16001,
        "group": 1,
        "utool_number": 1,
        "wait_di_port": 1,
        "default_speed": 15,
        "z_min": 0.0,
        "z_step": -4.0,
        "z_number": 5,
        "y_min": -50.0,
        "y_step": 12.5,
        "y_number": 5,
        "x_min": -50.0,
        "x_step": 12.5,
        "x_number": 5,
        "estimated_points": 125,
        "estimated_time_minutes": 5,
    },
    
    # ===== PRODUCTION =====
    "production_standard": {
        "description": "Production standard - 101×101×100 points",
        "robot_ip": "192.168.0.10",
        "rmi_init_port": 16001,
        "group": 1,
        "utool_number": 1,
        "wait_di_port": 1,
        "default_speed": 10,  # Mode collaboratif
        "z_min": 0.0,
        "z_step": -1.0,
        "z_number": 100,
        "y_min": -500.0,
        "y_step": 10.0,
        "y_number": 101,
        "x_min": -500.0,
        "x_step": 10.0,
        "x_number": 101,
        "estimated_points": 1020100,
        "estimated_time_hours": 3,
        "note": "Mode collaboratif - vitesse réduite",
    },
    
    "production_fast": {
        "description": "Production rapide - 50×50×50 points",
        "robot_ip": "192.168.0.10",
        "rmi_init_port": 16001,
        "group": 1,
        "utool_number": 1,
        "wait_di_port": 1,
        "default_speed": 25,  # Plus rapide
        "z_min": 0.0,
        "z_step": -1.0,
        "z_number": 50,
        "y_min": -250.0,
        "y_step": 10.0,
        "y_number": 50,
        "x_min": -250.0,
        "x_step": 10.0,
        "x_number": 50,
        "estimated_points": 125000,
        "estimated_time_minutes": 30,
        "note": "Mode production - vitesse augmentée",
    },
    
    "production_high_density": {
        "description": "Haute densité - 200×200×100 points",
        "robot_ip": "192.168.0.10",
        "rmi_init_port": 16001,
        "group": 1,
        "utool_number": 1,
        "wait_di_port": 1,
        "default_speed": 5,  # Très lent pour précision
        "z_min": 0.0,
        "z_step": -1.0,
        "z_number": 100,
        "y_min": -1000.0,
        "y_step": 5.0,
        "y_number": 200,
        "x_min": -1000.0,
        "x_step": 5.0,
        "x_number": 200,
        "estimated_points": 4000000,
        "estimated_time_hours": 8,
        "note": "Haute densité - vitesse très réduite pour précision",
    },
    
    # ===== CAS SPÉCIAUX =====
    "calibration": {
        "description": "Calibration - Points de référence (10 points)",
        "robot_ip": "192.168.0.10",
        "rmi_init_port": 16001,
        "group": 1,
        "utool_number": 1,
        "wait_di_port": 1,
        "default_speed": 5,
        "z_min": 0.0,
        "z_step": -5.0,
        "z_number": 2,
        "y_min": 0.0,
        "y_step": 50.0,
        "y_number": 2,
        "x_min": 0.0,
        "x_step": 50.0,
        "x_number": 2,  # Seulement 8 points au lieu de 10 (ajustable)
        "estimated_points": 8,
        "estimated_time_minutes": 1,
        "note": "Pour calibration et test du système",
    },
    
    "validation": {
        "description": "Validation - Zone réduite (1000 points)",
        "robot_ip": "192.168.0.10",
        "rmi_init_port": 16001,
        "group": 1,
        "utool_number": 1,
        "wait_di_port": 1,
        "default_speed": 10,
        "z_min": 0.0,
        "z_step": -1.0,
        "z_number": 10,
        "y_min": -50.0,
        "y_step": 10.0,
        "y_number": 10,
        "x_min": -50.0,
        "x_step": 10.0,
        "x_number": 10,
        "estimated_points": 1000,
        "estimated_time_minutes": 3,
        "note": "Pour validation avant production",
    },
    
    "custom_2d": {
        "description": "Pulvérisation 2D (XY uniquement)",
        "robot_ip": "192.168.0.10",
        "rmi_init_port": 16001,
        "group": 1,
        "utool_number": 1,
        "wait_di_port": 1,
        "default_speed": 15,
        "z_min": -50.0,  # Fixe à -50
        "z_step": 0.0,   # Pas de mouvement Z
        "z_number": 1,   # Un seul niveau Z
        "y_min": -200.0,
        "y_step": 5.0,
        "y_number": 81,
        "x_min": -200.0,
        "x_step": 5.0,
        "x_number": 81,
        "estimated_points": 6561,
        "estimated_time_minutes": 5,
        "note": "Grille 2D XY à hauteur fixe",
    },
    
    # ===== MULTI-BRAS (FUTUR) =====
    "multi_group": {
        "description": "[FUTUR] Configuration multi-groupes",
        "robot_ip": "192.168.0.10",
        "rmi_init_port": 16001,
        "groups": [1, 2],  # Deux bras
        "utool_number": 1,
        "wait_di_port": 1,
        "default_speed": 10,
        "z_min": 0.0,
        "z_step": -1.0,
        "z_number": 50,
        "y_min": -250.0,
        "y_step": 10.0,
        "y_number": 50,
        "x_min": -250.0,
        "x_step": 10.0,
        "x_number": 50,
        "note": "À implémenter : contrôle simultané",
    },
}


# ==================================================
# CLASSE MANAGER PRESETS
# ==================================================

class PresetManager:
    """Gestion des configurations pré-définies"""
    
    def __init__(self, preset_dir: Path = None):
        """
        Initialise le gestionnaire de presets
        
        Args:
            preset_dir: Répertoire de stockage des presets
        """
        if preset_dir is None:
            preset_dir = Path(__file__).parent / "presets"
        
        self.preset_dir = preset_dir
        self.preset_dir.mkdir(exist_ok=True)
    
    def save_presets(self):
        """Sauvegarde tous les presets en fichiers JSON"""
        for name, config in PRESET_CONFIGS.items():
            filepath = self.preset_dir / f"{name}.json"
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            print(f"✅ Preset sauvegardé: {filepath}")
    
    def load_preset(self, preset_name: str) -> dict:
        """
        Charge un preset
        
        Args:
            preset_name: Nom du preset
        
        Returns:
            Dictionnaire configuration
        """
        if preset_name in PRESET_CONFIGS:
            return PRESET_CONFIGS[preset_name]
        
        # Essayer de charger depuis fichier
        filepath = self.preset_dir / f"{preset_name}.json"
        if filepath.exists():
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        raise ValueError(f"Preset '{preset_name}' non trouvé")
    
    def list_presets(self) -> list:
        """Liste tous les presets disponibles"""
        return list(PRESET_CONFIGS.keys())
    
    def get_preset_info(self, preset_name: str) -> str:
        """Affiche les infos d'un preset"""
        config = self.load_preset(preset_name)
        
        info = []
        info.append(f"\n{'='*60}")
        info.append(f"PRESET: {preset_name}")
        info.append(f"{'='*60}")
        
        if "description" in config:
            info.append(f"Description: {config['description']}")
        
        if "note" in config:
            info.append(f"Note: {config['note']}")
        
        info.append(f"\nConfiguration:")
        info.append(f"  Robot IP: {config.get('robot_ip')}")
        info.append(f"  RMI Port: {config.get('rmi_init_port')}")
        info.append(f"  Vitesse: {config.get('default_speed')} mm/s")
        
        info.append(f"\nAxes:")
        info.append(f"  Z: min={config['z_min']}, step={config['z_step']}, "
                   f"nombre={config['z_number']}")
        info.append(f"  Y: min={config['y_min']}, step={config['y_step']}, "
                   f"nombre={config['y_number']}")
        info.append(f"  X: min={config['x_min']}, step={config['x_step']}, "
                   f"nombre={config['x_number']}")
        
        info.append(f"\nEstimations:")
        points = config.get('estimated_points', 'N/A')
        time_m = config.get('estimated_time_minutes', None)
        time_h = config.get('estimated_time_hours', None)
        
        info.append(f"  Points: {points:,}" if isinstance(points, int) else f"  Points: {points}")
        
        if time_m:
            info.append(f"  Temps estimé: ~{time_m} minutes")
        elif time_h:
            info.append(f"  Temps estimé: ~{time_h} heures")
        
        info.append(f"{'='*60}\n")
        
        return "\n".join(info)
    
    def show_all_presets(self):
        """Affiche tous les presets disponibles"""
        print("\n" + "="*70)
        print("PRÉSETS DE CONFIGURATION DISPONIBLES")
        print("="*70 + "\n")
        
        for preset_name in self.list_presets():
            print(self.get_preset_info(preset_name))
    
    def create_custom_preset(self, name: str, config: dict) -> bool:
        """
        Crée un preset personnalisé
        
        Args:
            name: Nom du preset
            config: Dictionnaire configuration
        
        Returns:
            True si succès
        """
        try:
            filepath = self.preset_dir / f"{name}.json"
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)
            print(f"✅ Preset personnalisé créé: {filepath}")
            return True
        except Exception as e:
            print(f"❌ Erreur création preset: {e}")
            return False


# ==================================================
# UTILISATION EXEMPLE
# ==================================================

USAGE_EXAMPLE = """
# Exemple d'utilisation des presets

from presets_config import PresetManager, PRESET_CONFIGS

# 1. Initialiser le manager
pm = PresetManager()

# 2. Lister tous les presets
print(pm.list_presets())
# ['demo_small', 'demo_medium', 'production_standard', ...]

# 3. Afficher infos d'un preset
print(pm.get_preset_info('demo_small'))

# 4. Charger un preset
config = pm.load_preset('production_standard')
print(config['default_speed'])  # 10

# 5. Sauvegarder les presets
pm.save_presets()

# 6. Créer preset personnalisé
my_config = {
    'description': 'Ma configuration',
    'robot_ip': '192.168.1.100',
    'default_speed': 20,
    # ... etc
}
pm.create_custom_preset('ma_config', my_config)

# 7. Utiliser dans GUI
from gui_robot_controller import RobotControllerGUI
gui = RobotControllerGUI(root)

# Charger un preset
preset = pm.load_preset('demo_medium')
gui.ip_var.set(preset['robot_ip'])
gui.speed_var.set(preset['default_speed'])
gui.z_number_var.set(preset['z_number'])
# ... etc
"""

# ==================================================
# INTÉGRATION DANS GUI
# ==================================================

GUI_INTEGRATION = """
# Dans gui_robot_controller.py

from presets_config import PresetManager

class RobotControllerGUI:
    def __init__(self, root):
        # ... code existant ...
        
        self.preset_manager = PresetManager()
        
        # Ajouter menu presets
        self._create_preset_menu()
    
    def _create_preset_menu(self):
        \"\"\"Crée le menu de sélection de presets\"\"\"
        preset_frame = ttk.Frame(left_frame)
        preset_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(preset_frame, text="Preset:").pack(side=tk.LEFT)
        
        preset_var = tk.StringVar()
        preset_combo = ttk.Combobox(
            preset_frame,
            textvariable=preset_var,
            values=self.preset_manager.list_presets(),
            width=20
        )
        preset_combo.pack(side=tk.LEFT, padx=5)
        preset_combo.bind('<<ComboboxSelected>>', self._on_preset_selected)
    
    def _on_preset_selected(self, event):
        \"\"\"Callback sélection preset\"\"\"
        preset_name = event.widget.get()
        preset = self.preset_manager.load_preset(preset_name)
        
        # Charger les valeurs
        self.ip_var.set(preset['robot_ip'])
        self.port_var.set(str(preset['rmi_init_port']))
        self.speed_var.set(str(preset['default_speed']))
        self.z_min_var.set(str(preset['z_min']))
        # ... etc
        
        self._add_log(f"Preset '{preset_name}' chargé", "SUCCESS")
"""


if __name__ == "__main__":
    # Test
    pm = PresetManager()
    pm.show_all_presets()
    
    # Sauvegarder
    print("\nSauvegarde des presets en fichiers JSON...")
    pm.save_presets()
    
    # Charger un preset
    print("\nChargement preset 'demo_small':")
    config = pm.load_preset('demo_small')
    print(f"  Vitesse: {config['default_speed']} mm/s")
    print(f"  Points estimés: {config['estimated_points']:,}")
