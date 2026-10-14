# Cartoucheur 4×4 Transistoc'h — V3

Lecteur de jingles 16 pads pour l'antenne : grille A1 à D4, contrôleur Akai MPD218, vumètre, alerte de fin, banques exportables. Fonctionne sous Windows (exe) et macOS (app).

## Contenu du projet

| Fichier | Rôle |
|---|---|
| `cartoucheur.py` | Le programme. Seul fichier modifié d'une version à l'autre. |
| `requirements.txt` | Les bibliothèques nécessaires (interface, son, MIDI). |
| `build.bat` | Fabrique l'exe sur un PC Windows où Python est installé. |
| `build_mac.sh` | Fabrique l'app sur un Mac où Python est installé. |
| `.github/workflows/build.yml` | Fabrique l'exe Windows sur GitHub à chaque envoi de code. |
| `CHANGELOG.md` | Historique des versions. |

## Fabriquer l'exe Windows

**Avec GitHub (sans installer Python)** : à chaque envoi de `cartoucheur.py` dans le dépôt, l'onglet *Actions* refabrique l'exe. Quand la coche est verte, ouvrir le build et télécharger *Cartoucheur* dans la section *Artifacts*. L'exe est conservé 7 jours sur GitHub : gardez-en une copie.

**Sur un PC Windows** : installer Python 3.12 (python.org, cocher « Add python.exe to PATH »), puis double-cliquer sur `build.bat`. L'exe apparaît dans `dist\Cartoucheur.exe`.

Premier lancement : si Windows affiche « Windows a protégé votre ordinateur », cliquer sur « Informations complémentaires » puis « Exécuter quand même » (l'exe n'est pas signé).

## Fabriquer l'app Mac

Sur chaque Mac (Intel ou puce Apple), une seule fois : installer Python 3.12 depuis python.org. Puis dans le Terminal :

```
cd ~/Downloads/cartoucheur
bash build_mac.sh
```

L'app apparaît dans `dist/Cartoucheur.app` : la glisser dans Applications. Fabriquée sur le Mac qui l'utilise, elle est adaptée à son processeur et macOS ne la bloque pas.

Sur Mac, PySide6 est fixé en version 6.7.3, compatible dès macOS 11 (les versions récentes exigent macOS 14.4).

Si l'app ne s'ouvre pas, la lancer depuis le Terminal pour voir l'erreur :

```
./dist/Cartoucheur.app/Contents/MacOS/Cartoucheur
```

## Un cartoucheur par émission

Chaque copie renommée a sa propre configuration :

- **Windows** : copier `Cartoucheur.exe` et renommer la copie (`La Matinale.exe`). La configuration `La Matinale.json` et le dossier `sons` sont créés à côté de l'exe.
- **Mac** : dupliquer l'app et la renommer (`La Matinale.app`). La configuration et les sons sont rangés dans `Documents/Cartoucheur Transistoch/`.

Les banques `.cartouche` passent d'un Windows à un Mac et inversement.

## Utilisation

- **Charger un son** : glisser un ou plusieurs fichiers sur un pad (plusieurs fichiers remplissent les pads suivants), ou cliquer sur un pad vide. Formats : WAV, MP3, FLAC, OGG, AIFF.
- **Clic droit sur un pad** : nom, mode (stop au 2e appui / relancer du début / boucle), volume du pad, assignation MIDI, vider.
- **Corbeilles** : en bas à droite d'un pad rempli (avec confirmation) ; à côté d'une lettre de colonne pour vider ses 4 pads (bouton « Annuler » pendant 10 s). Vider un pad ne supprime pas le fichier et garde l'assignation MIDI.
- **Alerte de fin** : « FIN DANS 5 s · A1 » avec décompte, pour le son qui finit le premier. Jamais sur les boucles.
- **Vumètre** stéréo avec maintien du pic et témoin CLIP.
- **Tout arrêter** : bouton rouge ou touche Échap.
- **Clavier** (positions physiques AZERTY) : `1 2 3 4 / A Z E R / Q S D F / W X C V`.
- **Banques** : *Exporter la banque* crée un fichier `.cartouche` (pads, réglages et sons). *Importer une banque* le recharge ; les sons sont copiés dans `sons/<nom de la banque>`. N'importer que des banques venant de l'équipe.

## Contrôleur MIDI

Mapping Akai MPD218 automatique, canal 10. Le MPD218 est sélectionné tout seul quand il est branché.

| | A | B | C | D |
|---|---|---|---|---|
| **1** | N80 | N81 | N82 | N83 |
| **2** | N76 | N77 | N78 | N79 |
| **3** | N72 | N73 | N74 | N75 |
| **4** | N68 | N69 | N70 | N71 |

Le bouton *Mapping MPD218* remet ce mapping. *MIDI Learn* permet d'assigner un autre contrôleur : cliquer sur *MIDI Learn*, sur un pad (ou sur *Tout arrêter*), puis toucher le bouton du contrôleur.

## Dépôt GitHub privé

Le dépôt peut être privé (Settings > Danger Zone > Change visibility). L'offre gratuite inclut alors 2 000 minutes de fabrication par mois (une minute Windows en compte deux) et 500 Mo de stockage : d'où la conservation des exe limitée à 7 jours. Activer la double authentification sur le compte GitHub.
