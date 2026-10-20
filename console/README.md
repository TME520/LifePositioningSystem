# LPS Console

*Français ci-dessous / English below.*

---

## Français

**LPS Console** reprend les fonctions de **LPS Companion** (PicoCalc) dans un
terminal Linux, façon VT100. Il lit et écrit les mêmes fichiers ICS et CSV que
le Companion.

- Python 3 et bibliothèque standard uniquement (`curses`), aucune dépendance.
- Fonctionne dans un terminal 80×24 ordinaire (minimum 40×20). Supporte le
  redimensionnement.
- **100 % clavier, la souris n'est pas utilisée.**
- Menus, textes, règles d'XP et limites copiés de `lps_companion.cpp` (v1.11).

### Lancement

```sh
cd console
./lps-console                       # ou : python3 -m lpsconsole (depuis console/)
./lps-console --import /media/$USER/PICOCALC   # importe la carte SD puis ouvre l'interface
./lps-console --import /media/$USER/PICOCALC/LPS --import-save --no-ui  # import seul
./lps-console --export-dir /media/$USER/PICOCALC/LPS   # écrit les exports directement sur la SD
./lps-console --lang fr
python3 -m unittest discover -s tests -t .  # tests (depuis console/)
```

### Menu (comme le Companion)

| Touche | Écran |
|---|---|
| `0` | Date (`AAAA-MM-JJ`, 2000-2099) |
| `1` | Activités : 16 cases sur 2 pages (10 + 6) |
| `2` | Bloc notes : 96 caractères ASCII |
| `3` | Suivi du poids : attendu et effectif (kg, point ou virgule, 3 décimales) |
| `4` | Kanban : 24 tâches de 32 caractères, colonnes À FAIRE / EN COURS / FINI |
| `5` | Rosaire chrétien : 4 modes, mystères du jour, mêmes textes que le Companion |
| `6` | Revue journalière |
| `7` | Config : 1 Langue, 2 Couleurs (PAL1-5), 3 Enregistrer, **4 Import SD Companion** |
| `8` | Rappels : 3 maximum, affichés au démarrage, supprimés à expiration |
| `9` | Minuteur : 5, 15 ou 30 min, en rouge sous 01:00 |
| `10` | Terminer le jour : avec Haut depuis l'entrée 0, ou flèches puis Entrée |

### Touches

- **Haut/Bas** : sélection. **Entrée** : valider.
- **Échap** ou **q** : retour. **q** sur l'accueil : quitter. Dans les champs
  texte, `q` est une lettre normale.
- **Accueil** : **Gauche** pour le jour précédent (31 derniers jours, lecture
  seule), **Droite** pour revenir au jour actuel.
- **Activités** : Entrée ou Espace pour cocher, `1`-`9` en raccourci,
  Gauche/Droite pour changer de page.
- **Kanban** :
  - `N` : nouvelle tâche ; Entrée : renommer ; Suppr ou `x` : effacer ;
  - F1/F2/F3 ou `1`/`2`/`3` : déplacer vers une colonne ;
  - F4 ou `-` : monter ; F5 ou `+` : descendre ;
  - Gauche/Droite : changer de colonne.
- **Rappels** : `N` nouveau, Entrée modifier, Suppr effacer. Dans l'éditeur,
  Haut/Bas (ou Tab) change de champ.
- **Minuteur** : Entrée met en pause ou reprend, Échap annule.
- **Rosaire** : Entrée, Droite ou Espace pour avancer, Gauche pour revenir,
  Échap pour quitter la séance.
- **Import** : saisir le chemin, Tab pour lire aussi `SAVE_A/B.BIN`, Entrée.

### Fichiers

| Quoi | Où |
|---|---|
| État local (JSON, écriture atomique, une sauvegarde `.bak`) | `$XDG_DATA_HOME/lps-console/state.json` (par défaut `~/.local/share/lps-console/`) |
| Exports ICS (un fichier par jour terminé) | `<data>/LPS/EXPORT/AAAA-MM-JJ_LPS-Companion.ics` |
| Journal des poids | `<data>/LPS/WEIGHTS/AAAA.csv` (`date,expected_kg,measured_kg`) |

Les exports sont **identiques à l'octet près** à ceux du Companion (vérifié
par les tests sur des fichiers produits par le vrai code du Companion). Les
fichiers sont donc interchangeables.

### Import depuis la carte SD

Le chemin peut être la racine de la carte (qui contient `LPS/`) ou le dossier
`LPS` lui-même.

- `EXPORT/*.ics` : chaque jour absent localement devient un jour terminé. Son
  XP, recalculé avec les règles du Companion, est ajouté au total.
- `WEIGHTS/*.csv` : alimente le journal des poids et complète les poids
  manquants des jours terminés.
- Option `SAVE_A.BIN`/`SAVE_B.BIN` : lecture seule du format v1-v6. L'outil
  prend la sauvegarde valide de plus haute génération, importe l'historique
  daté, et reprend le Kanban et les rappels seulement s'ils sont vides
  localement.
- Fusion **par date, sans doublon**. Les données locales gardent toujours la
  priorité. Rien n'est jamais écrit sur la carte, sauf si vous choisissez
  `--export-dir`.

### Réutilisation

`lpsconsole/lpsdata.py` est un module pur Python, sans curses : lecture et
écriture des ICS et CSV, décodage des `SAVE`, règles d'XP, état. Il est prévu
pour être importé par `c18/lps-rpi4.py` :

```python
import sys; sys.path.insert(0, "/chemin/LifePositioningSystem/console")
from lpsconsole import lpsdata
days, errors = lpsdata.read_ics_dir("/media/sd/LPS/EXPORT")
```

### Différences avec le Companion

- Une nouvelle installation démarre à la date du jour du PC, et non au
  2026-09-17. La date reste modifiable avec `0`.
- Il n'y a ni alerte batterie ni alerte de carte SD absente. Les palettes
  utilisent les couleurs du terminal.
- Ajouts propres à la console :
  - Config 4 (import) et le message « exports » ;
  - Espace pour cocher ;
  - `x`, `1`/`2`/`3` et `-`/`+` en plus de Suppr et F1-F5 ;
  - `q` ;
  - l'historique navigable couvre les 31 derniers jours **par date**, imports
    compris.
- Le Rosaire est paginé selon la largeur du terminal, et non par blocs de
  40×9.

---

## English

**LPS Console** brings the **LPS Companion** (PicoCalc) features to a Linux
terminal in VT100 style, and reads and writes the same ICS and CSV files as
the Companion.

- Python 3 standard library only (`curses`), no dependencies.
- Works in a plain 80×24 terminal (minimum 40×20) and handles resizing.
- **Keyboard only; the mouse is not used.**
- Menus, texts, XP rules and limits are copied from `lps_companion.cpp` (v1.11).

### Running

```sh
cd console
./lps-console                       # or: python3 -m lpsconsole (from console/)
./lps-console --import /media/$USER/PICOCALC          # import the SD card, then open the UI
./lps-console --import /media/$USER/PICOCALC/LPS --import-save --no-ui
./lps-console --export-dir /media/$USER/PICOCALC/LPS  # write exports straight to the SD card
python3 -m unittest discover -s tests -t .            # tests (from console/)
```

### Keys

Same menu as the Companion:

| Key | Screen |
|---|---|
| `0` | Date |
| `1` | Activities |
| `2` | Notepad |
| `3` | Weight |
| `4` | Kanban |
| `5` | Rosary |
| `6` | Daily review |
| `7` | Config (4 = import from SD) |
| `8` | Reminders |
| `9` | Timer |
| `10` | Close the day (Up from item 0, then Enter) |

- **Up/Down** select, **Enter** confirm.
- **Esc** or **q** go back; **q** on Home quits. In text fields `q` is an
  ordinary letter.
- **Home**: Left/Right browse the last 31 closed days, read-only.
- **Activities**: Enter or Space toggles, `1`-`9` are shortcuts, Left/Right
  change page.
- **Kanban**:
  - `N` new, Enter rename, Del or `x` delete;
  - F1-F3 or `1`-`3` move to a column;
  - F4 or `-` move up, F5 or `+` move down;
  - Left/Right change column.
- **Reminders**: `N`, Enter, Del.
- **Timer**: Enter pauses or resumes, Esc cancels.
- **Rosary**: Enter or Right advance, Left goes back, Esc leaves the session.
- **Import**: type the path, Tab toggles reading `SAVE_A/B.BIN`, Enter imports.

### Files

| What | Where |
|---|---|
| State | `$XDG_DATA_HOME/lps-console/state.json`, plus `.bak` (atomic writes) |
| ICS exports | `<data>/LPS/EXPORT/YYYY-MM-DD_LPS-Companion.ics` |
| Weights | `<data>/LPS/WEIGHTS/YYYY.csv` |

Exports are **byte-identical** to the Companion's; the unit tests compare
them with files produced by the real Companion adapter.

Import (from the SD card root or the `LPS` folder) merges ICS days and weights
CSV rows by date, with no duplicates and local data taking priority. It can
optionally decode `SAVE_A/B.BIN` (formats 1-6, read-only).

`lpsconsole/lpsdata.py` is the reusable, curses-free module intended for
`c18/lps-rpi4.py`.

### Differences from the Companion

- A new profile starts on the PC's current date.
- There are no battery or SD-card alerts, and palettes use terminal colours.
- Console-only additions: the import screen, Space to toggle, `x`/`1-3`/`-`/`+`
  in addition to Del and F1-F5, `q`, and history browsed by date (imports
  included).
- Rosary pages follow the terminal width.

### Layout

```
console/
  lps-console            launcher
  lpsconsole/lpsdata.py  reusable data module (ICS, CSV, SAVE, state, import)
  lpsconsole/app.py      screen state machine (no curses, unit-tested)
  lpsconsole/tui.py      curses front-end
  lpsconsole/rosary_texts.py  generated by tools/extract_rosary.py
  tests/                 unittest + real Companion fixtures
```
