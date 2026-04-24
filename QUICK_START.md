# 🚀 DÉMARRAGE RAPIDE - SpraySpy Fanuc GUI

## 5 minutes pour commencer

### 1️⃣ Installation (2 min)

```bash
# Aller dans le dossier du projet
cd c:\TRAVAIL\projets\SpraySpy_Fanuc\code

# Python 3.8+ requis - vérifier :
python --version

# Aucune dépendance externe requise pour l'interface de base
# (tkinter est inclus)
```

### 2️⃣ Lancement (30 sec)

```bash
python gui_robot_controller.py
```

L'interface s'ouvre :
```
╔════════════════════════════════════════════════════════╗
║  SpraySpy - Contrôleur Fanuc                          ║
║  ┌──────────────┬──────────────┬──────────────────┐   ║
║  │ Configuration│ Paramètres   │ Logs             │   ║
║  └──────────────┴──────────────┴──────────────────┘   ║
╚════════════════════════════════════════════════════════╝
```

### 3️⃣ Configuration (2 min)

**Remplir les champs de gauche :**

```
Robot IP:      192.168.0.10      [Votre robot]
RMI Port:      16001              [Standard Fanuc]
Groupe:        1                  [Premier bras]
UTool:         1                  [Outil sur robot]
DI Port:       1                  [Port capteur]
Vitesse:       10 mm/s            [Mode collaboratif]
```

**Axes (paramètres par défaut OK pour commencer) :**
- Axe Z : Min=0, Step=-1, Nombre=100
- Axe Y : Min=-500, Step=10, Nombre=101  
- Axe X : Min=-500, Step=10, Nombre=101

### 4️⃣ Connexion (30 sec)

Avant de cliquer, **préparer le robot :**

1. ✅ Mettre le robot en mode **AUTO** (pupitre)
2. ✅ Éteindre le Teach Pendant (TP OFF)
3. ✅ Vérifier qu'il n'y a personne près du robot

Puis cliquer :
```
[🟢 Connecter]
```

**Attendre les messages :**
```
[13:45:23] CONN    | Connexion à 192.168.0.10:16001...
[13:45:24] INFO    | En attente positionnement manuel (TP actif)
[13:45:26] WAIT    | En attente AUTO + TP OFF
[13:45:27] SUCCESS | Robot prêt pour pilotage externe
[13:45:28] SUCCESS | RMI initialisé
```

### 5️⃣ Lancer la séquence (1 min)

```
[▶️  Lancer Séquence]
```

**La séquence commence :**
```
[13:45:29] START   | Début séquence 3D
[13:45:30] INFO    | Séquence 2% (20000/1020100)
[13:45:40] INFO    | Séquence 5% (50000/1020100)
...
```

**Les logs affichent :**
- Position actuelle
- % progression
- Points complétés / total

**Pour arrêter d'urgence :**
```
[⏹️  Arrêt d'urgence]
```

## 📋 Fichiers du projet

| Fichier | Rôle | Contient |
|---------|------|----------|
| `gui_robot_controller.py` | ✅ **PRINCIPAL** | Interface complète Tkinter |
| `script_test_fanuc.py` | Version CLI | Script original sans GUI |
| `sensor_manager.py` | ✅ BONUS | Monitoring capteur optique |
| `trajectory_visualizer.py` | ✅ BONUS | Visualisation 3D trajectoires |
| `robot_config.json` | Config | Créé automatiquement |
| `README.md` | Documentation | Guide complet |
| `SUGGESTIONS_ET_AMÉLIORATIONS.md` | Roadmap | Futures améliorations |

## 🎯 Workflow simple

```
┌──────────────────────────────────────────────────┐
│ 1. Lancer GUI_ROBOT_CONTROLLER.PY                │
└──────────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────────┐
│ 2. Configurer IP/ports/axes                      │
│    (defaults OK pour tester)                     │
└──────────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────────┐
│ 3. Robot en AUTO + TP OFF                        │
│    (OBLIGATOIRE - sécurité)                      │
└──────────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────────┐
│ 4. [🟢 Connecter]                                │
│    Attendre "Robot prêt pour pilotage externe"  │
└──────────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────────┐
│ 5. [▶️  Lancer Séquence]                         │
│    Regarder progression dans les logs            │
└──────────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────────┐
│ 6. [💾 Sauvegarder] (si besoin - config future) │
└──────────────────────────────────────────────────┘
```

## ⚡ Raccourcis pratiques

| Action | Bouton |
|--------|--------|
| **Connecter au robot** | [🟢 Connecter] |
| **Lancer la séquence** | [▶️  Lancer Séquence] |
| **Arrêt d'urgence** | [⏹️  Arrêt d'urgence] |
| **Sauvegarder config** | [💾 Sauvegarder] |
| **Réinit. par défaut** | [↻ Réinitialiser] |
| **Déconnecter** | [🔴 Déconnecter] |

## 🔧 Configuration minimale requise

```
Robot Fanuc
├─ Interface RMI activée
├─ Port 16001 accessible
├─ Connecté en réseau TCP/IP
├─ Modo AUTO/TP disponible
└─ Capteur optique sur port DI

Ordinateur
├─ Python 3.8+
├─ Connexion réseau
└─ Tkinter (inclus avec Python)
```

## ❌ Erreurs courantes et solutions

| Erreur | Solution |
|--------|----------|
| "Connection refused" | Vérifier IP + port robot |
| "Phase 1: positionnement manuel" | Passer robot en AUTO |
| "Attente AUTO + TP OFF" | Éteindre Teach Pendant |
| "Timeout capteur" | Vérifier DI port + capteur |
| Interface gelée | C'est normal (threading) - attendre |

## 📈 Bonus : Visualisation trajectoire

Pour afficher la trajectoire 3D avant lancement :

```bash
# Installer matplotlib (optionnel)
pip install matplotlib numpy

# Dans l'interface, un bouton "📊 Prévisualiser" s'ajoute
# Clique dessus pour voir la trajectoire en 3D
```

## 🎓 Paramètres expliqués

### Connexion
- **Robot IP** : `192.168.0.10` = Adresse réseau du contrôleur
- **RMI Port** : `16001` = Port standard Fanuc (NE PAS CHANGER)

### Séquence
- **Min** : Point de départ sur cet axe (mm)
- **Step** : Distance entre chaque point (mm)
  - Négatif = vers les valeurs basses
  - Positif = vers les valeurs hautes
- **Nombre** : Combien de points sur cet axe

**Exemple Z :** Min=0, Step=-1, Nombre=100
→ Va de 0 à -100 par pas de -1 (100 niveaux)

### Capteur
- **DI Port** : Numéro du port entrée logique du capteur
- **Vitesse** : mm/s du robot
  - Collaboratif : 10-25 mm/s
  - Production : 25-50 mm/s

## 💾 Configuration persiste

La configuration est sauvegardée automatiquement dans :
```
robot_config.json
```

Au prochain lancement, les mêmes paramètres se chargeront.

Pour réinitialiser : bouton `[↻ Réinitialiser]`

## 📞 Besoin d'aide ?

1. **Vérifier les logs** dans l'interface (colonne de droite)
2. **Consulter README.md** pour documentation complète
3. **Tester avec script_test_fanuc.py** (version CLI plus simple)
4. **Voir SUGGESTIONS_ET_AMÉLIORATIONS.md** pour le dépannage

## ✅ Checklist avant production

- [ ] Robot en mode AUTO (OBLIGATOIRE)
- [ ] Teach Pendant éteint (OBLIGATOIRE)
- [ ] Personnes à distance (SÉCURITÉ)
- [ ] Configuration testée sur petit nombre de points
- [ ] Capteur optique fonctionne
- [ ] Logs s'affichent correctement
- [ ] Arrêt d'urgence testé

## 🚀 Prochaines étapes

Après le démarrage :

1. **Tester petit nombre de points**
   - Z_number=2, Y_number=3, X_number=3 (18 points)
   - Vérifier mouvements corrects

2. **Augmenter progressivement**
   - Z_number=5, Y_number=5, X_number=5 (125 points)
   - Vérifier capteur déclenche

3. **Production**
   - Configuration finale (101×101×100)
   - Lancer séquence complète

---

**Vous êtes prêt ! 🎉**

Lancer maintenant :
```bash
python gui_robot_controller.py
```
