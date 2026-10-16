# Alerte stock Pokémon 30 ans

Surveille les recherches « Pokémon 30e anniversaire » sur Amazon, Fnac, Cultura, Carrefour, Leclerc, Smyths, King Jouet, JouéClub, Micromania et Auchan, et t'envoie une notification :

- ✅ **EN STOCK** : un produit 30 ans passe en stock ou en précommande (notification prioritaire) ;
- 🆕 **Nouveau produit** : un nouveau produit 30 ans apparaît dans les résultats d'une enseigne.

Tourne gratuitement sur GitHub toutes les 10 minutes, sans PC allumé.

---

## Installation (≈ 15 min, plus simple depuis un ordinateur)

### 1. Notifications sur le téléphone (ntfy)
1. Installe l'appli **ntfy** (App Store / Play Store).
2. Appuie sur **+** et abonne-toi à un nom de canal **long et unique**, par ex. `pokemon-30ans-emmanuel-8f3k2`.
   (Toute personne qui connaît ce nom peut lire tes alertes, d'où le côté aléatoire.)

### 2. Mettre le projet sur GitHub
1. Crée un compte sur github.com si besoin.
2. **New repository** → nom `alerte-pokemon` → **Public** (minutes illimitées et gratuites) → Create.
3. **Add file → Upload files** → glisse **tout le contenu** du dossier décompressé, y compris le dossier `.github`
   (sur Mac, `Cmd + Maj + .` pour afficher les dossiers cachés) → **Commit**.

### 3. Les secrets
Dans le repo : **Settings → Secrets and variables → Actions → New repository secret** :

| Nom | Valeur |
|---|---|
| `NTFY_TOPIC` | le nom de canal choisi à l'étape 1 |
| `EMAIL_USER` *(optionnel)* | ton adresse Gmail |
| `EMAIL_PASSWORD` *(optionnel)* | un **mot de passe d'application** Gmail (myaccount.google.com → Sécurité → Mots de passe des applications), pas ton vrai mot de passe |
| `EMAIL_TO` *(optionnel)* | adresse qui reçoit les alertes (par défaut : `EMAIL_USER`) |

### 4. Lancer
**Actions** → active les workflows si GitHub le demande → **Surveillance stock Pokémon** → **Run workflow**.
Ensuite ça tourne tout seul toutes les 10 minutes.

Le premier passage enregistre les produits déjà présents sans t'envoyer de notification « nouveau produit »
(seulement « en stock » pour ceux déjà dispo).

---

## Lire le journal
Actions → clique sur un passage → **Vérification des stocks**. Pour chaque enseigne tu vois :
- `dispo` / `rupture` / `inconnu` pour chaque produit trouvé ;
- `bloqué` : le site a refusé l'accès (anti-robot) ;
- `0 produit trouvé` : soit rien ne correspond, soit le site affiche ses produits en JavaScript → mets `js: true` pour ce site dans `config.yaml`.

## Personnaliser (`config.yaml`, modifiable sur GitHub avec l'icône crayon)
- **filtre** : mots qui doivent / ne doivent pas apparaître dans le titre.
- **recherches** : une URL de page de résultats par enseigne. Si une URL ne marche pas, fais la recherche sur le site et colle l'adresse obtenue.
- **produits** : fiches précises à surveiller en plus.

## Limites à connaître
- **Anti-robots** : Amazon, Fnac, Leclerc et Carrefour bloquent souvent les serveurs de GitHub. L'outil te prévient une fois si un site le bloque. Dans ce cas, le lancer depuis ton PC (adresse internet « normale ») marche beaucoup mieux :
  ```
  pip install -r requirements.txt
  python -m playwright install chromium
  NTFY_TOPIC=ton-canal python stock_watcher.py --boucle 3
  ```
  (sous Windows : `set NTFY_TOPIC=ton-canal` puis `python stock_watcher.py --boucle 3`)
- **Délai** : GitHub lance souvent les passages avec quelques minutes de retard. Pour les restocks éclair, la version PC est plus réactive.
- **GitHub met en pause** les tâches planifiées d'un repo sans activité depuis 60 jours : il suffit alors de cliquer sur « Enable workflow ».
- L'outil **notifie seulement**, il n'achète rien à ta place.

## Test
`NTFY_TOPIC=ton-canal python stock_watcher.py --test` envoie une notification de test.
