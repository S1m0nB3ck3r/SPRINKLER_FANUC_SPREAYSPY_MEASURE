# 🤖 SpraySpy - Contrôleur Fanuc GUI

Interface complète en Tkinter pour contrôle du robot Fanuc avec capteur optique. Support séquence 3D paramétrable (X, Y, Z) avec monitoring en temps réel.

## 📋 Vue d'ensemble

**Architecture :**
```
┌─────────────────────────────────────────────────────┐
│          GUI Tkinter (gui_robot_controller.py)      │
│  ┌──────────────┬──────────────┬──────────────────┐ │
│  │ Connexion    │  Paramètres  │  Logs en temps   │ │
│  │ Robot        │  Séquence    │  réel            │ │
│  │              │  (X/Y/Z)     │                  │ │
│  └──────────────┴──────────────┴──────────────────┘ │
└─────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────┐
│     RobotController (Communication RMI)              │
│  - Connexion socket TCP/IP                          │
│  - Protocole JSON                                   │
│  - Gestion phases manuel/auto                       │
└─────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────┐
│  Robot Fanuc (Interface RMI)                        │
│  - Port RMI: 16001 (init)                           │
│  - Port Motion: Dynamique (>30000)                  │
└─────────────────────────────────────────────────────┘
```

## 🚀 Installation

### Prérequis
- Python 3.8+
- Windows, Linux ou macOS
- Robot Fanuc avec interface RMI activée
- Réseau TCP/IP vers le robot

### Installation dépendances

```bash
# Installer depuis requirements.txt
pip install -r requirements.txt

# Ou manuellement (Tkinter est inclus avec Python)
# Aucune dépendance externe strictement requise
```

## ⚙️ Configuration

### Configuration par défaut

Au premier lancement, un fichier `robot_config.json` est créé automatiquement :

```json
{
  "robot_ip": "192.168.0.10",
  "rmi_init_port": 16001,
  "group": 1,
  "utool_number": 1,
  "wait_di_port": 1,
  "default_speed": 10,
  "z_min": 0.0,
  "z_step": -1.0,
  "z_number": 100,
  "y_min": -500.0,
  "y_step": 10.0,
  "y_number": 101,
  "x_min": -500.0,
  "x_step": 10.0,
  "x_number": 101
}
```

### Paramètres expliqués

#### Connexion
- **Robot IP** : Adresse IP du contrôleur Fanuc
- **RMI Port** : Port d'initialisation RMI (généralement 16001)

#### Paramètres Robot
- **Groupe** : Numéro du groupe/bras (1 = premier bras)
- **UTool** : Numéro de l'outil utilisé (défini sur la tablette)
- **DI Port** : Port entrée logique du capteur optique
- **Vitesse** : Vitesse robot en mm/s (1-50 pour mode collaboratif)

#### Séquence 3D
La séquence effectue une boucle imbriquée :
- **Z** : Axe vertical (profondeur)
- **Y** : Axe latéral
- **X** : Axe avant/arrière

Pour chaque paramètre d'axe :
- **Min** : Position minimale (en mm)
- **Step** : Pas entre chaque point (en mm)
- **Nombre** : Nombre de points sur cet axe

**Exemple** : 101×101 points XY avec 100 niveaux Z = 1,020,100 points de mesure

## 🎮 Utilisation

### Démarrage

```bash
python gui_robot_controller.py
```

### Interface

```
┌─── COLONNE 1 ─────┬────── COLONNE 2 ──────┬──── COLONNE 3 ────┐
│ 🤖 Configuration  │ 📊 Paramètres Séquence│ 📋 Logs            │
│                   │                       │                    │
│ Connexion:        │ Axe Z:                │ [13:45:23] INFO   │
│  Robot IP: ...    │  Min: 0               │ ✅ Connexion...   │
│  RMI Port: ...    │  Step: -1             │ ✅ RMI initialisé │
│                   │  Nombre: 100          │ 🟢 Séquence en    │
│ [🟢 Connecter]    │                       │    cours 25%      │
│ [🔴 Déconnecter]  │ Axe Y:                │                   │
│                   │  Min: -500            │ 📈 Position: ...  │
│ Paramètres:       │  Step: 10             │                   │
│  Groupe: 1        │  Nombre: 101          │                   │
│  UTool: 1         │                       │                   │
│  DI Port: 1       │ Axe X:                │                   │
│  Vitesse: 10 mm/s │  Min: -500            │                   │
│                   │  Step: 10             │                   │
│ Statut:           │  Nombre: 101          │                   │
│ 🟢 Connecté       │                       │                   │
│                   │ [▶️  Lancer]          │                   │
│                   │ [⏹️  Arrêt urgence]  │                   │
│                   │ [💾 Sauvegarder]      │                   │
│                   │ [↻ Réinitialiser]     │                   │
└───────────────────┴───────────────────────┴────────────────────┘
```

### Workflow typique

1. **Configurer paramètres**
   - Définir IP/Port robot
   - Régler paramètres axes X/Y/Z
   - Définir vitesse

2. **Sauvegarder configuration** (optionnel)
   - `[💾 Sauvegarder]` pour future utilisation

3. **Connecter au robot**
   - Cliquer `[🟢 Connecter]`
   - Le robot doit être en mode AUTO (TP OFF)
   - L'interface confirme "✅ Connecté"

4. **Lancer séquence**
   - Cliquer `[▶️  Lancer Séquence]`
   - La progression s'affiche en temps réel
   - Les logs montrent chaque étape

5. **Arrêt d'urgence** (si besoin)
   - Cliquer `[⏹️  Arrêt d'urgence]`
   - Robot s'arrête immédiatement

## 📊 Fichiers générés

### robot_config.json
Configuration sauvegardée automatiquement.
Permet de réutiliser les mêmes paramètres au prochain lancement.

### Logs
Les logs complets s'affichent en temps réel dans l'interface.
Format : `[HH:MM:SS] LEVEL | Message`

Niveaux disponibles :
- ✅ **SUCCESS** (vert) : Opération réussie
- ❌ **ERROR** (rouge) : Erreur
- 🟡 **WAIT** (orange) : En attente
- 🔵 **CONN** (bleu) : Connexion
- ℹ️ **INFO** (noir) : Information
- 🟣 **START** (violet) : Début séquence
- ⏹️ **CANCEL** (rouge) : Annulation

## 🔒 Sécurité

### Points importants

1. **Mode AUTO obligatoire**
   - Le robot doit être en mode AUTO
   - Teach Pendant (TP) doit être OFF
   - Interface confirme "Robot prêt pour pilotage externe"

2. **Arrêt d'urgence**
   - Toujours disponible même pendant l'exécution
   - Arrête le robot immédiatement
   - Commande `FRC_Abort` envoyée

3. **Limites vitesse**
   - 1-50 mm/s recommandés
   - Plus de 50 mm/s non testé
   - Mode collaboratif : 10-25 mm/s

4. **Timeouts**
   - Tous les mouvements ont un timeout
   - Si le robot ne répond pas → erreur et déconnexion

## 🐛 Dépannage

### Erreur : "Impossible de se connecter"

**Causes possibles :**
- IP robot incorrecte
- Port RMI incorrect (généralement 16001)
- Robot éteint ou réseau inaccessible
- Firewall bloquant la connexion

**Solutions :**
```bash
# Tester la connectivité
ping 192.168.0.10

# Tester le port (Windows: Telnet activé)
telnet 192.168.0.10 16001
```

### Erreur : "Robot non prêt pour pilotage externe"

**Causes possibles :**
- Robot en mode TP (Teach Pendant)
- Mode MANUAL au lieu d'AUTO
- ServoReady = 0

**Solutions :**
1. Passer le robot en mode AUTO
2. Éteindre le Teach Pendant
3. Vérifier indicateurs lumineux du pupitre

### Séquence s'arrête avant la fin

**Causes possibles :**
- Capteur optique ne déclenche pas (timeout)
- Erreur communication RMI
- Robot en erreur

**Solutions :**
1. Vérifier DI port correct
2. Tester capteur manuellement
3. Consulter l'écran du robot pour les messages d'erreur

### Interface figée

**Cause :** Une opération longue bloque l'UI

**Solution :** La GUI utilise le threading pour éviter ce problème.
Si ça arrive quand même :
1. Attendre quelques secondes
2. Utiliser Ctrl+C dans le terminal
3. Relancer l'interface

## 📈 Monitoring capteur optique

### Utilisation SensorManager

```python
from sensor_manager import SensorManager

# Initialisation
sensor_mgr = SensorManager(robot_controller)

# Enregistrer les ports capteur
sensor_mgr.register_port(1, timeout=5.0)
sensor_mgr.register_port(2, timeout=5.0)

# Démarrer monitoring
sensor_mgr.start_monitoring(interval=0.5)

# Récupérer statistiques
stats = sensor_mgr.get_statistics(port=1)
# {'port': 1, 'total_readings': 1000, 'triggers': 950, 'timeouts': 2, ...}

# Exporter logs
sensor_mgr.export_logs(Path("logs.json"))

# Générer rapport
report = sensor_mgr.generate_report()
```

## 📚 Structure du projet

```
SpraySpy_Fanuc/
├── code/
│   ├── script_test_fanuc.py          # Script original sans GUI
│   ├── gui_robot_controller.py       # ✅ GUI PRINCIPALE
│   ├── sensor_manager.py             # ✅ Gestion capteur
│   ├── robot_config.json             # Config sauvegardée
│   ├── SUGGESTIONS_ET_AMÉLIORATIONS.md  # ✅ Améliorations futures
│   └── README.md                     # ✅ Ce fichier
```

## 🚀 Prochaines étapes

### Tier 1 - Recommandé
- [ ] Reconnexion automatique en cas de déconnexion
- [ ] Visualisation 3D trajectoire (matplotlib/vispy)
- [ ] Gestion capteur avancée avec statistiques
- [ ] Enregistrement/replay mouvements

### Tier 2 - Intéressant
- [ ] Support multi-bras simultanés
- [ ] API REST pour accès distant
- [ ] Base de données logging persistant
- [ ] Génération rapports PDF

### Tier 3 - Bonus
- [ ] Exécutable standalone (.exe)
- [ ] Machine learning pour optimisation
- [ ] Support matériel externe (GPIO, Arduino)
- [ ] Version Web (Flask/React)

Voir [SUGGESTIONS_ET_AMÉLIORATIONS.md](SUGGESTIONS_ET_AMÉLIORATIONS.md) pour détails complets.

## 📞 Support technique

### Logs détaillés
Tous les logs s'affichent en temps réel dans l'interface.
Format standard : `[HH:MM:SS] LEVEL | Message`

### Messages d'erreur courants

| Message | Cause | Solution |
|---------|-------|----------|
| "Connection refused" | Port incorrect | Vérifier IP/Port |
| "Timeout waiting for response" | Robot ne répond pas | Vérifier robot en AUTO |
| "Phase 1 : positionnement manuel" | Attend TP actif | Activer TP sur robot |
| "Attente AUTO + TP OFF" | Mode incorrect | Passer en AUTO |
| "ANOMALIE: Pas de réponse capteur" | Capteur ne déclenche pas | Vérifier capteur + port |

### Contact

Pour les bugs ou questions sur le contrôleur Fanuc RMI :
1. Vérifier les logs dans l'interface GUI
2. Tester avec `script_test_fanuc.py` (version CLI)
3. Consulter la documentation Fanuc RMI officielle

## 📄 Licence

Projet SpraySpy - Contrôle robot Fanuc
Utilisé pour séquence de pulvérisation avec capteur optique

## 🙏 Crédits

GUI développée avec :
- **Tkinter** : Interface graphique native Python
- **Socket** : Communication TCP/IP
- **Threading** : Exécution non-bloquante
- **JSON** : Format configuration
- **Python 3.8+**

---

**Version actuelle : 1.0**  
**Dernière mise à jour : 2026-01-28**
