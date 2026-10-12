# Cartoucheur 4×4

Lecteur de jingles 16 pads pour la radio : sortie carte son au choix, contrôleur MIDI, glisser-déposer des sons.

## Fabriquer l'exe (une seule fois)

1. Installer **Python 3.12** depuis python.org (cocher « Add python.exe to PATH »).
2. Double-cliquer sur `build.bat`.
3. Récupérer `dist\Cartoucheur.exe`.

Sans installer Python : mettre ce dossier sur GitHub, l'onglet *Actions* construit l'exe automatiquement (fichier `.github/workflows/build.yml`) et le propose en téléchargement.

## Un cartoucheur par émission

L'exe lit la configuration qui porte **son propre nom** : copiez `Cartoucheur.exe` et renommez la copie.

```
Cartouches\
  La Matinale.exe      -> La Matinale.json (créé automatiquement)
  Le Grand Direct.exe  -> Le Grand Direct.json
  sons\
    matinale\...
    direct\...
```

Les sons rangés sous le dossier de l'exe sont enregistrés en chemin relatif : le dossier entier peut passer d'un PC à l'autre ou sur une clé USB.

## Utilisation

- **Charger** : glisser un ou plusieurs fichiers sur un pad (plusieurs = pads suivants), ou cliquer un pad vide. WAV, MP3, FLAC, OGG, AIFF.
- **Clic droit sur un pad** : nom, couleur, mode (stop au 2e appui / relancer du début / boucle), volume du pad, MIDI, vider.
- **MIDI** : choisir le contrôleur, cliquer *MIDI Learn*, cliquer un pad puis toucher le bouton du contrôleur. Fonctionne aussi pour *Tout arrêter*. Notes, CC et Program Change sont acceptés.
- **Retour LED** : choisir la sortie MIDI du contrôleur pour allumer les boutons des pads en lecture (APC mini, Launchpad, etc.).
- **Clavier** : `1 2 3 4 / A Z E R / Q S D F / W X C V` (positions physiques), `Échap` = tout arrêter.
- **Sortie audio** : chaque carte apparaît avec son pilote (MME, DirectSound, WASAPI). WASAPI donne la latence la plus faible.

Le titre de l'émission se change par double-clic dessus. Tout est enregistré automatiquement.
