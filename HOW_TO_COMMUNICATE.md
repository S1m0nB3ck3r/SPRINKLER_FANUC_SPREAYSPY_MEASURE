# 📡 HOW_TO_COMMUNICATE.md - Protocole RMI Fanuc SpraySpy

## 📑 Table des matières

1. [Vue d'ensemble](#vue-densemble)
2. [Machine à état](#machine-à-état)
3. [Phases de communication](#phases-de-communication)
4. [Commandes détaillées](#commandes-détaillées)
5. [Séquence complète 3D](#séquence-complète-3d)
6. [Codes d'erreur](#codes-derreur)
7. [Points critiques](#points-critiques)
8. [Exemples concrets](#exemples-concrets)

---

## 🎯 Vue d'ensemble

Le projet **SpraySpy** communique avec un robot **Fanuc CRX** via le protocole **RMI (Remote Motion Interface)** en JSON sur TCP/IP.

### Architecture réseau

```
┌──────────────────────┐
│  GUI Tkinter         │
│  (gui_robot_         │
│   controller.py)     │
└──────────┬───────────┘
           │ TCP/IP
           │ Port 16001 (connexion)
           │ Port 30001+ (mouvement)
           ↓
┌──────────────────────────────────────┐
│  Robot Fanuc CRX                     │
│  IP: 192.168.1.100 (par défaut)      │
│  Contrôleur RMI (KAREL program)      │
│  Mode: AUTO + TP OFF                 │
└──────────────────────────────────────┘
```

### Caractéristiques du protocole

| Aspect | Valeur |
|--------|--------|
| Format | JSON (texte) |
| Séparateur | `\r\n` (CRLF) |
| Port initial | 16001 |
| Port mouvement | 30001+ (dynamique) |
| Timeout standard | 5 secondes |
| Timeout mouvement | 30 secondes |
| Vitesse robot | 10 mm/s (collaboratif) |
| Mode robot requis | AUTO + TP OFF |
| Encoding | UTF-8 |

---

## 🔄 Machine à État

### États du robot

```
┌─────────────────────────────────────────────────────────────┐
│                 DIAGRAMME D'ÉTAT COMPLET                    │
└─────────────────────────────────────────────────────────────┘

                    ┌──────────────────────┐
                    │   DISCONNECTED       │
                    │  (État initial)      │
                    │  • Socket fermée     │
                    │  • Aucune comm.      │
                    └──────────┬───────────┘
                               │
                    [Clic bouton CONNECTER]
                    [ou connect(ip, port)]
                               │
                               ↓
                    ┌──────────────────────────┐
                    │ WAITING_AUTO_TP_OFF      │
                    │ (Préparation robot)      │
                    │ • Socket connectée ✓     │
                    │ • Boucle GetStatus 200ms│
                    │ • Attente TPMode = 0    │
                    │ • Attente ServoReady = 1│
                    └──────────┬───────────────┘
                               │
        ┌──────────────────────┴──────────────────────┐
        │                                             │
   [Timeout]                              [OK reçu]
        │                                             │
        ↓                                             ↓
    ┌───────┐                    ┌──────────────────────────┐
    │ERREUR │                    │    IDLE                  │
    └───────┘                    │  (Prêt pour commandes)   │
                                 │  • RMI initialisé ✓      │
                                 │  • UFrame/UTool = 1 ✓    │
                                 │  • Boutons actifs        │
                                 │  • Attente utilisateur   │
                                 └──────────┬───────────────┘
                                            │
                    ┌───────────────────────┼───────────────────────┐
                    │                       │                       │
        [Mouvement relatif]    [Séquence 3D]        [Déconnexion]
                    │                       │                       │
                    ↓                       ↓                       ↓
            ┌──────────────┐      ┌──────────────────┐   ┌──────────────────┐
            │   MOVING     │      │ MOVING_SEQUENCE  │   │   DISCONNECTED   │
            │ (Mouvement   │      │ (Boucles X/Y/Z) │   │  (Fermeture)     │
            │  manuel)     │      │  + Capteur       │   │  • Socket close  │
            │              │      │  + Retours pos   │   │  • Fin propre    │
            └──────┬───────┘      └──────┬───────────┘   └──────────────────┘
                   │                     │
        [Mouvement termina]  [Point complété]
                   │                     │
                   └──────────┬──────────┘
                              │
                              ↓
                   ┌──────────────────────┐
                   │      IDLE            │
                   │ (Prêt pour suite)    │
                   └──────────────────────┘
```

### Table d'état

```python
# Classe RobotState dans gui_robot_controller.py
class RobotState:
    DISCONNECTED = "disconnected"          # 1️⃣  Initial
    IDLE = "idle"                          # 2️⃣  Prêt pour commandes
    WAITING_AUTO_TP_OFF = "waiting_auto"   # 3️⃣  Attente config robot
    MOVING = "moving"                      # 4️⃣  Mouvement manuel
    MOVING_SEQUENCE = "moving_seq"         # 5️⃣  Séquence 3D en cours
```

### Transitions d'état

| Depuis | Vers | Événement | Condition |
|--------|------|-----------|-----------|
| DISCONNECTED | WAITING_AUTO_TP_OFF | `connect()` | Port 16001 réponse OK |
| WAITING_AUTO_TP_OFF | IDLE | `FRC_GetStatus` retour | TPMode=0 ET ServoReady=1 |
| WAITING_AUTO_TP_OFF | DISCONNECTED | Timeout 60s | Pas de réponse |
| IDLE | MOVING | `linear_relative()` | Mouvement lancé |
| MOVING | IDLE | Fin mouvement | `FRC_LinearRelative` réponse OK |
| IDLE | MOVING_SEQUENCE | `run_sequence()` | Séquence lancée |
| MOVING_SEQUENCE | IDLE | Fin séquence | Boucles terminées |
| Toute | DISCONNECTED | `disconnect()` | Déconnexion utilisateur |
| Toute | DISCONNECTED | Erreur critique | Exception non récupérable |

---

## 📡 Phases de communication

### Phase 1️⃣ : Connexion socket au port RMI initial

**Objectif :** Établir une connexion TCP/IP avec le contrôleur Fanuc

```python
# Code Python
socket.socket(socket.AF_INET, socket.SOCK_STREAM)
socket.connect(("192.168.1.100", 16001))
socket.settimeout(5.0)
```

**Flux :**
```
Client                                  Serveur (Robot)
  │                                         │
  ├─── SYN ─────────────────────────────────>│
  │                                         │
  │<─── SYN-ACK ─────────────────────────────┤
  │                                         │
  ├─── ACK ─────────────────────────────────>│
  │                                         │
  │     [Socket établie]
  │
  └──► État = WAITING_AUTO_TP_OFF
```

---

### Phase 2️⃣ : Commande FRC_Connect

**Objectif :** Demander accès RMI au contrôleur

#### Request

```json
    {
    "Communication": "FRC_Connect"
    }
```

#### Response (Succès)

```json
{
  "ErrorID": 0,
  "PortNumber": 30001
}
```

#### Response (Erreur)

```json
{
  "ErrorID": 3001,
  "PortNumber": 0
}
```

#### Interpretation

| Champ | Valeur | Signification |
|-------|--------|---------------|
| `ErrorID` | 0 | ✅ Succès - utiliser port 30001 |
| `ErrorID` | 3001 | ❌ Erreur: robot pas en AUTO |
| `ErrorID` | 3002 | ❌ Erreur: RMI déjà connecté |
| `PortNumber` | 30001+ | Port TCP/IP pour mouvements futurs |

**Code Python** (voir `gui_robot_controller.py`, méthode `connect`)
```python
def connect(self, ip: str, port: int) -> bool:
    self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.sock.settimeout(5.0)
    self.sock.connect((ip, port))
    
    cmd = {"Communication": "FRC_Connect"}
    reply = self._send_raw(cmd, timeout=5.0)
    
    if reply.get("ErrorID") == 0:
        self.rmi_port = reply["PortNumber"]
        self.is_connected = True
        self._set_state(RobotState.WAITING_AUTO_TP_OFF)
        return True
    return False
```

---

### Phase 3️⃣ : Boucle FRC_GetStatus (attente mode AUTO + TP OFF)

**Objectif :** Attendre que l'opérateur configure le robot correctement

**Conditions requises avant de continuer :**
- `TPMode` == 0 (Teach Pendant OFF)
- `ServoReady` == 1 (Moteurs activés)

#### Request (envoyée toutes les 200ms)

```json
{
  "Command": "FRC_GetStatus"
}
```

#### Response

```json
{
  "TPMode": 1,
  "ServoReady": 0,
  "RMIMotionStatus": 0,
  "NextSequenceID": 100,
  "GroupMask": 1,
  "NumberUTool": 0,
  "NumberUFrame": 0,
  "InMotion": 0,
  "ErrorCode": 0
}
```

#### Champs détaillés

| Champ | Type | Description |
|-------|------|-------------|
| `TPMode` | int (0/1) | 1 = TP actif, **0 = TP OFF** ✓ |
| `ServoReady` | int (0/1) | 0 = Moteurs OFF, **1 = Moteurs ON** ✓ |
| `RMIMotionStatus` | int (0/1) | 1 = RMI prêt après initialisation |
| `NextSequenceID` | int | ⚠️ **CRITIQUE** : ID pour prochaine instruction |
| `GroupMask` | int | Masque groupe (1 = groupe 1) |
| `NumberUTool` | int | Outil actuellement sélectionné |
| `NumberUFrame` | int | Repère utilisateur actuellement sélectionné |
| `InMotion` | int (0/1) | 1 = Robot en mouvement |
| `ErrorCode` | int | Code erreur robot (0 = OK) |

#### Boucle d'attente (Code Python)

```python
def wait_ready_for_remote(self):
    """Attendre que le robot soit en AUTO + TP OFF"""
    start_time = time.time()
    timeout = 60  # 60 secondes max
    
    while time.time() - start_time < timeout:
        status = self.get_status()
        
        if status["TPMode"] == 0 and status["ServoReady"] == 1:
            self._set_state(RobotState.IDLE)
            self._log("✓ Mode AUTO + TP OFF détecté", "INFO")
            return True
        
        self._log(f"Attente AUTO+TP_OFF: TPMode={status['TPMode']} "
                  f"ServoReady={status['ServoReady']}", "WAIT")
        time.sleep(0.2)  # Attendre 200ms avant prochaine tentative
    
    raise TimeoutError("Timeout: Robot pas passé en AUTO+TP_OFF")
```

---

### Phase 4️⃣ : Initialisation RMI

#### 4.1 - Commande FRC_Initialize

**Objectif :** Démarrer le programme RMI_MOVE sur le contrôleur

##### Request

```json
{
  "Command": "FRC_Initialize",
  "GroupMask": 1
}
```

##### Response

```json
{
  "ErrorID": 0
}
```

##### Interprétation

| ErrorID | Signification |
|---------|---------------|
| 0 | ✅ Initialisation réussie |
| 4000 | ❌ RMI déjà initialisé |
| 4001 | ❌ Groupe invalide |

**Attendre 300ms** avant d'envoyer les prochaines commandes (le robot démarre le programme RMI_MOVE)

#### 4.2 - Commande FRC_SetUFrameUTool

**Objectif :** Configurer l'outil et le repère utilisateur

##### Request

```json
{
  "Command": "FRC_SetUFrameUTool",
  "UFrameNumber": 1,
  "UToolNumber": 1
}
```

##### Response

```json
{
  "ErrorID": 0
}
```

##### Code Python

```python
def initialize_rmi(self, group: int, utool: int = 1, uframe: int = 1):
    """Initialiser RMI et configurer outil/repère"""
    
    # 1. FRC_Initialize
    cmd = {
        "Command": "FRC_Initialize",
        "GroupMask": group
    }
    reply = self.send_cmd(cmd, timeout=5.0)
    if reply.get("ErrorID") != 0:
        raise Exception(f"FRC_Initialize failed: {reply['ErrorID']}")
    
    # 2. Attendre 300ms
    time.sleep(0.3)
    
    # 3. FRC_SetUFrameUTool
    cmd = {
        "Command": "FRC_SetUFrameUTool",
        "UFrameNumber": uframe,
        "UToolNumber": utool
    }
    reply = self.send_cmd(cmd, timeout=5.0)
    if reply.get("ErrorID") != 0:
        raise Exception(f"FRC_SetUFrameUTool failed: {reply['ErrorID']}")
    
    self._log("✓ RMI initialisé avec UFrame=1 et UTool=1", "INFO")
```

---

## 📡 Commandes détaillées

### 🔴 Commande: FRC_LinearRelative

**Objectif :** Déplacer le robot de façon relative (X, Y, Z)

#### Request Structure

```json
{
  "Instruction": "FRC_LinearRelative",
  "SequenceID": 100,
  "Configuration": {
    "UToolNumber": 1,
    "UFrameNumber": 1,
    "Front": 1,
    "Up": 1,
    "Left": 1,
    "Flip": 0,
    "Turn4": 0,
    "Turn5": 0,
    "Turn6": 0
  },
  "Position": {
    "X": 10.0,
    "Y": 0.0,
    "Z": -5.0,
    "W": 0.0,
    "P": 0.0,
    "R": 0.0
  },
  "SpeedType": "mmSec",
  "Speed": 10,
  "TermType": "FINE"
}
```

#### Request Fields

| Champ | Type | Valeur | Explication |
|-------|------|--------|-------------|
| `Instruction` | string | "FRC_LinearRelative" | Type de commande |
| `SequenceID` | int | NextSequenceID reçu | ⚠️ **CRITIQUE** : doit correspondre exactement |
| `UToolNumber` | int | 1 | Outil utilisé |
| `UFrameNumber` | int | 1 | Repère utilisateur |
| `Front/Up/Left` | int (0/1) | Constant | **Configuration constante** pour tout la séquence |
| `X` | float | ±∞ mm | Déplacement en X (relatif) |
| `Y` | float | ±∞ mm | Déplacement en Y (relatif) |
| `Z` | float | ±∞ mm | Déplacement en Z (relatif) |
| `W/P/R` | float | 0.0 | Rotation (généralement non utilisée) |
| `Speed` | int | 1-50 | Vitesse en mm/s (10 = collaboratif) |
| `SpeedType` | string | "mmSec" | Unité de vitesse |
| `TermType` | string | "FINE" | Type de terminaison (précis) |

#### Response (Succès)

```json
{
  "ErrorID": 0,
  "NextSequenceID": 101
}
```

#### Response (Erreur)

```json
{
  "ErrorID": 5001,
  "NextSequenceID": 100
}
```

#### Codes d'erreur courants

| ErrorID | Signification |
|---------|---------------|
| 0 | ✅ Mouvement accepté |
| 5001 | ❌ SequenceID invalide |
| 5002 | ❌ Position hors limites |
| 5003 | ❌ Configuration invalide |
| 5004 | ❌ Collision détectée |

#### Points critiques

✅ **SequenceID DOIT correspondre à NextSequenceID reçu précédemment**

```
Séquence correcte:
└─ Réponse 1: NextSequenceID = 100
   └─ Requête 2: SequenceID = 100 ✓
      └─ Réponse 2: NextSequenceID = 101
         └─ Requête 3: SequenceID = 101 ✓

Séquence INCORRECTE:
└─ Réponse 1: NextSequenceID = 100
   └─ Requête 2: SequenceID = 101 ✗ ERREUR!
```

✅ **Configuration (Front/Up/Left) DOIT être constante**

```json
// CORRECT - Même configuration partout
Mouvement 1: "Front": 1, "Up": 1, "Left": 1
Mouvement 2: "Front": 1, "Up": 1, "Left": 1
Mouvement 3: "Front": 1, "Up": 1, "Left": 1

// INCORRECT - Configuration change
Mouvement 1: "Front": 1, "Up": 1, "Left": 1
Mouvement 2: "Front": 0, "Up": 1, "Left": 1  ✗ ERREUR!
```

#### Code Python

```python
def linear_relative(self, dx: float, dy: float, dz: float, 
                   speed: float, utool: int = 1, uframe: int = 1):
    """Déplacer le robot de façon relative"""
    
    status = self.get_status()
    seq_id = status["NextSequenceID"]
    
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
    
    reply = self.send_cmd(cmd, timeout=30.0)  # Timeout mouvement: 30s
    
    if reply.get("ErrorID") != 0:
        raise Exception(f"LinearRelative failed: ErrorID={reply['ErrorID']}")
    
    return reply
```

---

### 🟡 Commande: FRC_WaitDIN

**Objectif :** Attendre que le capteur sur le port DI se déclenche

#### Request

```json
{
  "Instruction": "FRC_WaitDIN",
  "SequenceID": 101,
  "PortNumber": 1,
  "PortValue": "ON"
}
```

#### Request Fields

| Champ | Type | Valeur | Explication |
|-------|------|--------|-------------|
| `Instruction` | string | "FRC_WaitDIN" | Type d'instruction |
| `SequenceID` | int | NextSequenceID | ⚠️ **CRITIQUE** : doit correspondre |
| `PortNumber` | int | 1-32 | Numéro port d'entrée logique |
| `PortValue` | string | "ON" ou "OFF" | Valeur à attendre |

#### Response (Capteur déclenché)

```json
{
  "ErrorID": 0,
  "NextSequenceID": 102
}
```

#### Response (Timeout)

```json
{
  "ErrorID": 6001,
  "NextSequenceID": 101
}
```

#### Code Python

```python
def wait_di(self, port: int):
    """Attendre capteur optique"""
    
    status = self.get_status()
    seq_id = status["NextSequenceID"]
    
    cmd = {
        "Instruction": "FRC_WaitDIN",
        "SequenceID": seq_id,
        "PortNumber": port,
        "PortValue": "ON"
    }
    
    reply = self.send_cmd(cmd, timeout=30.0)
    
    if reply.get("ErrorID") != 0:
        raise Exception(f"WaitDIN timeout on port {port}")
    
    return reply
```

---

### 🟢 Commandes de lecture (sans SequenceID)

#### FRC_ReadCartesianPosition

**Objectif :** Lire la position actuelle du robot (X, Y, Z, W, P, R)

##### Request

```json
{
  "Command": "FRC_ReadCartesianPosition",
  "Group": 1
}
```

##### Response

```json
{
  "Group": 1,
  "Position": {
    "X": 123.456,
    "Y": -45.678,
    "Z": 89.012,
    "W": 0.0,
    "P": 0.0,
    "R": 0.0
  },
  "ErrorID": 0
}
```

#### FRC_ReadJointAngles

**Objectif :** Lire les angles des 6 articulations

##### Request

```json
{
  "Command": "FRC_ReadJointAngles",
  "Group": 1
}
```

##### Response

```json
{
  "Group": 1,
  "JointAngle": {
    "J1": 45.0,
    "J2": -90.5,
    "J3": 120.3,
    "J4": 0.0,
    "J5": -30.2,
    "J6": 180.0
  },
  "ErrorID": 0
}
```

---

### 🔵 Commande: FRC_Abort

**Objectif :** Arrêter immédiatement tous les mouvements (Arrêt d'urgence)

#### Request

```json
{
  "Command": "FRC_Abort"
}
```

#### Response

```json
{
  "ErrorID": 0
}
```

#### Code Python

```python
def _emergency_stop(self):
    """Arrêt d'urgence immédiat"""
    try:
        cmd = {"Command": "FRC_Abort"}
        self.send_cmd(cmd, timeout=2.0)
        self._log("⚠️ Arrêt d'urgence activé", "WARNING")
    except:
        pass
```

---

## 🔄 Séquence complète 3D

### Workflow global

```
START
  │
  ├─→ [1] Connexion socket (port 16001)
  │
  ├─→ [2] FRC_Connect → Port 30001
  │
  ├─→ [3] Boucle FRC_GetStatus (attente AUTO + TP OFF)
  │
  ├─→ [4] FRC_Initialize (GroupMask=1)
  │
  ├─→ [5] Attendre 300ms
  │
  ├─→ [6] FRC_SetUFrameUTool (1, 1)
  │
  ├─→ [7] BOUCLE PRINCIPALE 3D
  │   │
  │   ├─→ [Z] Pour Z de Z_min à Z_min + Z_step * Z_number
  │   │   │
  │   │   ├─→ [Y] Pour Y de Y_min à Y_min + Y_step * Y_number
  │   │   │   │
  │   │   │   ├─→ [X] Pour X de X_min à X_min + X_step * X_number
  │   │   │   │   │
  │   │   │   │   ├─→ Mouvement: FRC_LinearRelative(dX, dY, dZ)
  │   │   │   │   │
  │   │   │   │   ├─→ Attendre capteur: FRC_WaitDIN(port=1)
  │   │   │   │   │
  │   │   │   │   └─→ Mettre à jour position
  │   │   │   │
  │   │   │   ├─→ Fin boucle X
  │   │   │   │
  │   │   │   ├─→ Retour en X: FRC_LinearRelative(-500, 0, 0)
  │   │   │   │
  │   │   │   └─→ Mettre à jour position
  │   │   │
  │   │   └─→ Fin boucle Y
  │   │
  │   └─→ Fin boucle Z
  │
  ├─→ [8] FRC_ReadCartesianPosition (position finale)
  │
  ├─→ [9] FRC_Abort (arrêt propre)
  │
  ├─→ [10] disconnect() (fermeture socket)
  │
  └─→ END
```

### Exemple concret: Séquence 2×2×2

**Configuration:**
```
X: min=-10, step=10, number=2  → positions: -10, 0
Y: min=-10, step=10, number=2  → positions: -10, 0
Z: min=0,   step=-5,  number=2 → positions: 0, -5
Speed: 10 mm/s
```

**Exécution step-by-step:**

```
Z=0, Y=-10:
  ├─ X=-10: Move(dx=-10, dy=-10, dz=0)   → NextSeqID=101
  │         WaitDIN(port=1)                → NextSeqID=102
  │
  ├─ X=0:   Move(dx=10, dy=0, dz=0)       → NextSeqID=103
  │         WaitDIN(port=1)                → NextSeqID=104
  │
  └─ Return: Move(dx=-10, dy=0, dz=0)     → NextSeqID=105

Z=0, Y=0:
  ├─ X=-10: Move(dx=0, dy=10, dz=0)       → NextSeqID=106
  │         WaitDIN(port=1)                → NextSeqID=107
  │
  ├─ X=0:   Move(dx=10, dy=0, dz=0)       → NextSeqID=108
  │         WaitDIN(port=1)                → NextSeqID=109
  │
  └─ Return: Move(dx=-10, dy=0, dz=0)     → NextSeqID=110

Z=-5, Y=-10:
  ├─ X=-10: Move(dx=0, dy=-10, dz=-5)     → NextSeqID=111
  │         WaitDIN(port=1)                → NextSeqID=112
  │
  ├─ X=0:   Move(dx=10, dy=0, dz=0)       → NextSeqID=113
  │         WaitDIN(port=1)                → NextSeqID=114
  │
  └─ Return: Move(dx=-10, dy=0, dz=0)     → NextSeqID=115

Z=-5, Y=0:
  ├─ X=-10: Move(dx=0, dy=10, dz=0)       → NextSeqID=116
  │         WaitDIN(port=1)                → NextSeqID=117
  │
  ├─ X=0:   Move(dx=10, dy=0, dz=0)       → NextSeqID=118
  │         WaitDIN(port=1)                → NextSeqID=119
  │
  └─ Return: Move(dx=-10, dy=0, dz=0)     → NextSeqID=120

Total: 8 points avec 8 capteurs déclenches
```

### Code Python de la séquence

```python
def run_sequence(self, config: RobotConfig, cancel_event: threading.Event):
    """Exécuter la séquence 3D complète"""
    
    try:
        self._set_state(RobotState.MOVING_SEQUENCE)
        
        total_points = (config.x_number * 
                       config.y_number * 
                       config.z_number)
        points_done = 0
        
        # Boucles imbriquées: Z → Y → X
        for z_idx in range(config.z_number):
            if cancel_event.is_set():
                break
                
            z_pos = config.z_min + z_idx * config.z_step
            
            for y_idx in range(config.y_number):
                if cancel_event.is_set():
                    break
                    
                y_pos = config.y_min + y_idx * config.y_step
                
                for x_idx in range(config.x_number):
                    if cancel_event.is_set():
                        break
                    
                    x_pos = config.x_min + x_idx * config.x_step
                    
                    # Calcul déplacement depuis position précédente
                    if x_idx == 0:
                        dx = x_pos - (config.x_min - config.x_step)
                    else:
                        dx = config.x_step
                    
                    if y_idx == 0:
                        dy = y_pos - (config.y_min - config.y_step)
                    else:
                        dy = 0
                    
                    if z_idx == 0:
                        dz = z_pos
                    else:
                        dz = config.z_step if x_idx == 0 and y_idx == 0 else 0
                    
                    # Mouvement
                    self.linear_relative(dx, dy, dz, 
                                       config.default_speed)
                    
                    # Attendre capteur
                    self.wait_di(config.wait_di_port)
                    
                    points_done += 1
                    pct = (points_done / total_points) * 100
                    self._status(f"{points_done}/{total_points} ({pct:.1f}%)",
                               "info")
                
                # Fin boucle X: retour
                if y_idx < config.y_number - 1:
                    return_x = -(config.x_number - 1) * config.x_step
                    self.linear_relative(return_x, 0, 0, 
                                       config.default_speed)
        
        self._set_state(RobotState.IDLE)
        self._status("✓ Séquence complétée", "success")
        
    except Exception as e:
        self._log(f"Erreur séquence: {e}", "ERROR")
        self._emergency_stop()
```

---

## ⚠️ Codes d'erreur

### Codes ErrorID courants

| Code | Phase | Signification |
|------|-------|---------------|
| 0 | Toutes | ✅ Succès |
| 3001 | FRC_Connect | Robot pas en mode AUTO |
| 3002 | FRC_Connect | RMI déjà connecté |
| 4000 | FRC_Initialize | RMI déjà initialisé |
| 4001 | FRC_Initialize | Groupe invalide |
| 5001 | FRC_LinearRelative | SequenceID invalide |
| 5002 | FRC_LinearRelative | Position hors limites |
| 5003 | FRC_LinearRelative | Configuration invalide |
| 5004 | FRC_LinearRelative | Collision détectée |
| 6001 | FRC_WaitDIN | Timeout (capteur non déclenché) |
| 6002 | FRC_WaitDIN | Port invalide |

### Gestion d'erreur

```python
def send_cmd(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
    """Envoyer commande et gérer erreurs"""
    
    if not self.is_connected:
        raise Exception("Robot non connecté")
    
    reply = self._send_raw(cmd)
    
    if "ErrorID" in reply:
        if reply["ErrorID"] != 0:
            error_msg = ERROR_CODES.get(
                reply["ErrorID"], 
                f"Erreur inconnue {reply['ErrorID']}"
            )
            self._log(f"❌ {error_msg}", "ERROR")
            raise RobotException(reply["ErrorID"], error_msg)
    
    return reply
```

---

## 🔐 Points critiques

### ⚠️ 1. SequenceID OBLIGATOIRE

Chaque instruction (`FRC_LinearRelative`, `FRC_WaitDIN`) **DOIT** avoir un `SequenceID` qui correspond exactement au `NextSequenceID` reçu dans la réponse précédente.

```
❌ ERREUR: Sauter un ID
└─ Réponse: NextSequenceID = 100
└─ Requête: SequenceID = 102  ← ERREUR!
   Résultat: ErrorID = 5001 (SequenceID invalide)

✅ CORRECT: Suivre les IDs
└─ Réponse 1: NextSequenceID = 100
└─ Requête 2: SequenceID = 100 ← OK
└─ Réponse 2: NextSequenceID = 101
└─ Requête 3: SequenceID = 101 ← OK
```

### ⚠️ 2. Configuration constante

Les paramètres de configuration (`Front`, `Up`, `Left`) **DOIVENT** rester identiques pour toute la séquence.

```json
// ✅ CORRECT
{
  "Configuration": {
    "Front": 1,
    "Up": 1,
    "Left": 1,
    "Flip": 0,
    "Turn4": 0,
    "Turn5": 0,
    "Turn6": 0
  }
}
// Identique pour tous les mouvements

// ❌ ERREUR: Configuration change
Mouvement 1: "Front": 1, ...
Mouvement 2: "Front": 0, ...  ← ERREUR!
```

### ⚠️ 3. Timeouts

**Respecter les timeouts** ou le robot ignore la commande:

| Commande | Timeout | Raison |
|----------|---------|--------|
| FRC_Connect | 5s | Connexion réseau |
| FRC_GetStatus | 5s | Status rapide |
| FRC_Initialize | 5s | Initialisation |
| FRC_LinearRelative | 30s | Mouvement lent |
| FRC_WaitDIN | 30s | Capteur peut être lent |

```python
# ❌ MAUVAIS: Pas de timeout
self.sock.recv(4096)  # Peut bloquer indéfiniment

# ✅ BON: Timeout défini
self.sock.settimeout(5.0)
self.sock.recv(4096)  # Max 5 secondes
```

### ⚠️ 4. Mode Robot vérifié

Avant de commencer une séquence, **vérifier obligatoirement:**

```python
status = self.get_status()

if status["TPMode"] != 0:
    raise Exception("❌ Teach Pendant est ON (TPMode=1)")
    
if status["ServoReady"] != 1:
    raise Exception("❌ Moteurs ne sont pas activés")
    
if status["InMotion"] == 1:
    raise Exception("❌ Robot déjà en mouvement")
```

### ⚠️ 5. Synchronisation SequenceID

Après chaque commande, **récupérer le NextSequenceID:**

```
// ❌ MAUVAIS: Utiliser une variable globale
next_id = 100
cmd1["SequenceID"] = next_id++

// ✅ BON: Récupérer depuis la réponse
reply1 = send(cmd1)
cmd2["SequenceID"] = reply1["NextSequenceID"]  ← Récupéré!
reply2 = send(cmd2)
cmd3["SequenceID"] = reply2["NextSequenceID"]  ← Récupéré!
```

### ⚠️ 6. JSON bien formé

Le JSON **DOIT** être strict et valide:

```json
// ❌ ERREUR: Trailing comma
{
  "SequenceID": 100,
  "Position": {
    "X": 10.0,
  }
}

// ✅ CORRECT
{
  "SequenceID": 100,
  "Position": {
    "X": 10.0
  }
}
```

### ⚠️ 7. Terminator CRLF

Chaque message JSON **DOIT** terminer par `\r\n`:

```python
# ❌ MAUVAIS
msg = json.dumps(cmd)
self.sock.send(msg.encode())

# ✅ BON
msg = json.dumps(cmd) + "\r\n"
self.sock.send(msg.encode())
```

---

## 📝 Exemples concrets

### Exemple 1: Connexion simple

```python
from gui_robot_controller import RobotController, RobotConfig

# Créer contrôleur
robot = RobotController()

# Connecter
if robot.connect("192.168.1.100", 16001):
    print("✓ Connecté")
    
    # Vérifier status
    status = robot.get_status()
    print(f"Mode: TPMode={status['TPMode']}, "
          f"ServoReady={status['ServoReady']}")
    
    # Initialiser RMI
    robot.initialize_rmi(group=1, utool=1, uframe=1)
    
    # Lire position
    pos = robot.read_position(group=1)
    print(f"Position: X={pos['Position']['X']}")
    
    # Déconnecter
    robot.disconnect()
else:
    print("❌ Connexion échouée")
```

### Exemple 2: Mouvement simple

```python
# Supposant déjà connecté et initialisé
robot.linear_relative(
    dx=10.0,    # 10 mm vers X
    dy=0.0,     # Pas de mouvement Y
    dz=-5.0,    # 5 mm vers le bas
    speed=10    # 10 mm/s
)

print("✓ Mouvement complété")
```

### Exemple 3: Attendre capteur

```python
# Mouvement + capteur
robot.linear_relative(10, 0, -5, speed=10)
print("Mouvement fait, attendre capteur...")

robot.wait_di(port=1)
print("✓ Capteur déclenché!")
```

### Exemple 4: Séquence 3D minimale

```python
config = RobotConfig(
    x_min=-10, x_step=10, x_number=2,      # X: -10, 0
    y_min=-10, y_step=10, y_number=2,      # Y: -10, 0
    z_min=0,   z_step=-5,  z_number=1,     # Z: 0
    default_speed=10
)

cancel_event = threading.Event()
robot.run_sequence(config, cancel_event)

# Pour arrêter:
# cancel_event.set()
```

---

## 🎓 Résumé pour LabVIEW

Quand vous coderez en LabVIEW, voici les points clés à implémenter:

```
1. CONNEXION
   ├─ TCP/IP Connect (192.168.1.100, 16001)
   └─ Timeout: 5s

2. FRC_CONNECT
   ├─ Send: {"Communication": "FRC_Connect"}
   ├─ Receive: {"ErrorID": 0, "PortNumber": 30001}
   └─ Vérifier: ErrorID == 0

3. BOUCLE GETSTAT (200ms)
   ├─ Tant que: TPMode != 0 ou ServoReady != 1
   ├─ Attendre: 200ms
   └─ Timeout global: 60s

4. FRC_INITIALIZE
   ├─ Send: {"Command": "FRC_Initialize", "GroupMask": 1}
   ├─ Receive: {"ErrorID": 0}
   └─ Attendre: 300ms après

5. FRC_SETUFRAMEUTOOL
   ├─ Send: {"Command": "FRC_SetUFrameUTool", ...}
   └─ Receive: {"ErrorID": 0}

6. BOUCLE MOUVEMENTS
   ├─ For Z, Y, X:
   │  ├─ FRC_LinearRelative (SequenceID = NextSeqID précédent)
   │  ├─ Récupérer: NextSequenceID de la réponse
   │  ├─ FRC_WaitDIN (SequenceID = NextSeqID)
   │  └─ Récupérer: NextSequenceID de la réponse
   └─ Timeout: 30s par mouvement

7. FRC_ABORT
   └─ Arrêt propre

8. CLOSE
   └─ Fermer socket TCP/IP
```

**Points critiques LabVIEW:**
- String terminal: `\r\n` (CR + LF)
- JSON strict (pas de trailing commas)
- Récupérer NextSequenceID après **chaque** réponse
- Variables de configuration constantes
- Timeouts configurés correctement
- Gestion d'erreur sur tous les ErrorID

---

**Document généré:** 2026-02-11  
**Version:** 1.0  
**Projet:** SpraySpy Fanuc Robot Controller
