<img src="web/icone.png" width="96" align="right" alt="Icône d'Efface-moi : une gomme">

# Efface-moi

Faites supprimer vos données personnelles chez les courtiers en données et les entreprises, sans abonnement.

Efface-moi est une alternative libre et gratuite aux services comme Incogni. L'application écrit aux entreprises
en votre nom (article 17 du RGPD), suit le délai légal d'un mois, relance, et prépare la plainte à la CNIL.
Elle tourne sur votre ordinateur, envoie depuis votre propre boîte mail, et ne transmet rien à personne d'autre.

## Ce qu'elle fait

| Étape | Automatique ? |
|---|---|
| Rédiger la demande d'effacement, en français ou en anglais selon l'entreprise | Oui |
| Envoyer par e-mail, avec une limite quotidienne et une pause entre deux envois | Oui |
| Repérer les réponses, les accusés de réception et les adresses invalides | Oui (expéditeur et objet seulement) |
| Relancer après un mois sans réponse | Oui |
| Préparer le texte de la plainte CNIL 15 jours après la relance | Oui |
| Redemander l'effacement aux courtiers tous les 6 mois | Oui |
| Trouver qui a vos données, d'après les expéditeurs de votre boîte mail | Oui |
| Chercher l'adresse du délégué à la protection des données sur le site d'une entreprise | Oui, quand le site se laisse lire |
| Recevoir les adresses trouvées par les autres utilisateurs, et partager les vôtres | Oui, après relecture |
| Chercher les pages du web qui affichent votre nom, e-mail, téléphone ou pseudo | Oui, avec un moteur (voir « Empreinte publique ») |
| Demander au site la suppression d'une page précise, et suivre si elle disparaît | Oui |
| Demander le déréférencement à Google et à Bing | Non, le texte du formulaire est prêt à copier |
| Lire une réponse et dire si la suppression est confirmée | Non, c'est vous |
| Remplir un formulaire en ligne ou poster un courrier (entreprises sans e-mail) | Non, le texte est prêt à copier |
| Déposer la plainte sur cnil.fr | Non, le texte est prêt à copier |

## Installer

**Le plus simple (Windows) :** téléchargez `EffaceMoi.exe` depuis la page
[Releases](../../releases/latest) et double-cliquez dessus. Rien d'autre à installer.

Le programme n'est pas signé : au premier lancement, Windows peut afficher « Windows a protégé votre
ordinateur ». Cliquez sur « Informations complémentaires », puis « Exécuter quand même ». Certains antivirus
se méfient aussi des programmes fabriqués avec PyInstaller ; le code source est ici pour vérification.

**Avec Python (Windows, macOS, Linux) :** il faut Python 3.10 ou plus récent, et aucune autre dépendance.

    git clone https://github.com/egstar38-afk/efface-moi.git
    cd efface-moi
    python efface.py

Sous Windows, un double-clic sur `EFFACEMOI.bat` fait la même chose.

## Premiers pas

L'application s'ouvre dans votre navigateur. L'onglet **Guide** détaille chaque étape ; en résumé :

1. **Réglages** : votre nom, votre e-mail, puis un *mot de passe d'application* pour votre messagerie.
   Pour Gmail, activez la validation en deux étapes, puis créez-le sur
   <https://myaccount.google.com/apppasswords>.
2. **Entreprises** : téléchargez la base, cochez les entreprises, « Préparer les demandes ».
3. **Demandes** : relisez les courriers, puis « Envoyer ».
4. **Réglages → Automatisation** : activez le suivi quotidien.

L'application démarre en **mode test** : « Envoyer » écrit les courriers dans un dossier d'essais sans rien
envoyer. Décochez « Mode test » dans Réglages quand le texte vous convient.

Les comptes Hotmail, Outlook.com et Live personnels n'acceptent plus les mots de passe d'application. Connectez
une autre adresse (Gmail par exemple) et indiquez l'adresse Microsoft dans « Autres e-mails à faire effacer ».

## Laisser tourner tout seul

Le suivi (lecture des réponses, relances, plaintes à préparer, renouvellements) peut se lancer de trois façons :

- **Chaque jour à 9 h**, par une tâche planifiée Windows : bouton « Activer le suivi quotidien ».
- **À chaque démarrage de l'ordinateur**, pour un PC qui n'est pas toujours allumé à 9 h : case à cocher dans
  Réglages → Automatisation. Le suivi tourne en arrière-plan, sans fenêtre.
- **Sur un serveur** allumé en permanence, pour que les relances partent même PC éteint : voir ci-dessous.

### Sur un serveur Linux

Sur le serveur (mini-PC, NAS, Raspberry Pi, avec Python 3.10+ et cron) :

    git clone https://github.com/egstar38-afk/efface-moi.git
    cd efface-moi
    sh serveur/efface-serveur.sh install

Cette commande lance l'interface en arrière-plan et ajoute deux lignes à votre crontab (démarrage du serveur et
suivi quotidien à 8 h, heure du serveur) sans toucher aux autres. `serveur/efface-serveur.sh uninstall` les retire.

L'interface n'écoute que sur le serveur lui-même. Pour l'ouvrir depuis votre PC, double-cliquez sur
`EFFACEMOI-SERVEUR.bat` : il ouvre un tunnel SSH vers le serveur, puis la page dans votre navigateur. Il faut
pouvoir se connecter au serveur en SSH avec une clé. À la main :

    ssh -N -L 8766:127.0.0.1:8765 monserveur

puis <http://127.0.0.1:8766/>. Sur un serveur Linux, le mot de passe d'application est enregistré dans le
dossier `data/`, réservé à votre compte mais non chiffré : n'installez Efface-moi que sur un serveur à vous.

## Empreinte publique

L'onglet **Empreinte** s'occupe de ce qui est visible sur le web à votre sujet.

- **Trouver.** Sans rien régler, l'application prépare les recherches utiles (nom, e-mails, téléphone, pseudos,
  réseaux sociaux, annuaires) à ouvrir dans votre navigateur ; vous collez ensuite l'adresse des pages qui vous
  concernent. Avec un moteur de recherche, elle cherche toute seule, lit chaque page trouvée et ne garde que
  celles où vos données figurent vraiment, puis recommence chaque semaine.
- **Traiter.** Pour chaque page : demander la suppression au site (le courrier cite la page, l'application cherche
  l'adresse de contact), demander le déréférencement à Google et à Bing (texte du formulaire prêt, délai d'un
  mois suivi, plainte CNIL prête ensuite), supprimer votre compte (lien direct quand il est connu, grâce à la
  base libre [JustDeleteMe](https://justdeleteme.xyz)), ou écarter la page.
- **Suivre.** L'application vérifie si les pages en attente ont disparu.
- **Liste guidée.** Annuaires, registres d'entreprises et outils officiels, avec la démarche pour chacun.

La recherche automatique ne porte que sur vous : elle refuse de tourner si l'e-mail de votre identité n'est pas
celui de la messagerie connectée. Elle ne fouille pas les réseaux sociaux par nom (ils l'interdisent et le
bloquent) et ne fait pas de recherche par photo.

### Choisir un moteur

- **SearXNG**, un moteur libre à installer sur un serveur. Le dossier [`serveur/searxng`](serveur/searxng)
  contient de quoi le lancer avec Docker en deux commandes ; il n'écoute que sur la machine elle-même. Dans
  l'application : Empreinte → Recherche automatique → SearXNG → `http://127.0.0.1:8892`.
- **Brave Search**, sans rien installer : créez une clé sur <https://api.search.brave.com> et collez-la dans
  l'application. Vérifiez sur leur site ce que l'offre gratuite permet.

## Partager une adresse qui a marché

La base est pauvre en entreprises françaises : chaque adresse trouvée par un utilisateur peut servir aux autres.

Quand une entreprise que vous avez ajoutée vous-même vous a répondu, l'accueil de l'application propose
« Partager cette adresse ». Le bouton ouvre ici une proposition pré-remplie (il faut un compte GitHub). Elle ne
contient que le nom de l'entreprise, son site et l'adresse : rien sur vous. Vous pouvez aussi proposer une adresse
directement depuis l'onglet [Issues](../../issues/new/choose).

Aucune adresse n'est diffusée sans relecture, car une fausse adresse recevrait les demandes, donc le nom et
l'e-mail, de tous les utilisateurs. Chaque proposition est d'abord vérifiée automatiquement (l'adresse figure-t-elle
sur le site de l'entreprise ? est-elle sur le même domaine ?), puis acceptée ou refusée par un mainteneur.
Les adresses acceptées sont dans [`communaute/entreprises.json`](communaute/entreprises.json) ; l'application les
télécharge avec la base (onglet Entreprises, « Mettre à jour ») et les range sous le filtre « Adresses partagées
par les utilisateurs ».

**Pour le mainteneur :** lisez le rapport posté sous la proposition, ouvrez le site de l'entreprise en cas de
doute, puis ajoutez l'étiquette `acceptee` pour accepter (l'adresse est enregistrée et la proposition se ferme
toute seule), ou fermez la proposition pour refuser.

## Limites

- La base d'entreprises vient de [datarequests.org](https://www.datarequests.org). Elle est surtout allemande
  et internationale. Pour une entreprise française absente, utilisez « Ajouter une entreprise ».
- La recherche d'adresse sur le site d'une entreprise échoue sur les grands sites qui bloquent les robots, et
  peut proposer une mauvaise adresse : vérifiez toujours la page d'origine.
- La recherche de pseudo ne peut pas vérifier tous les sites.
- La recherche automatique dépend de ce que les moteurs veulent bien montrer : une page peut exister sans être
  trouvée, et un homonyme peut être signalé à tort. C'est vous qui triez.
- Efface-moi ne lit pas le contenu des réponses : c'est vous qui dites si la suppression est confirmée.

## Vos données

- Profil, demandes et historique sont dans un dossier local : `data/` à côté du code, ou
  `%LOCALAPPDATA%\EffaceMoi` pour la version .exe. L'onglet Réglages affiche son emplacement.
- Sous Windows, le mot de passe d'application est chiffré par Windows (DPAPI) pour votre seul compte.
- L'application ne contacte que : votre messagerie, GitHub (base des entreprises, adresses partagées, liste
  des sites pour la recherche de pseudo, liens de suppression de compte), le moteur de recherche que vous avez
  choisi, les sites que vous lui demandez de vérifier, et les entreprises à qui vous écrivez.
- L'interface n'est accessible que depuis la machine où elle tourne.

## Développer

    python tests/test_flow.py       # cycle complet avec un faux serveur de messagerie
    python tests/test_contact.py    # recherche d'adresse sur un faux site
    python tests/test_commu.py      # partage d'adresses entre utilisateurs
    python tests/test_empreinte.py  # empreinte publique, avec un faux moteur de recherche

Les tests n'envoient aucun e-mail et n'accèdent pas à Internet. `construire-exe.bat` fabrique
`dist\EffaceMoi.exe` avec PyInstaller. `outils/icone.py` redessine l'icône (il lui faut Pillow). Pousser une étiquette `v1.2.3` fait fabriquer et publier l'exe par
GitHub Actions.

    core/companies.py   base des entreprises, recherche
    core/letters.py     textes des courriers (effacement, relance, plainte)
    core/mailer.py      envoi SMTP, lecture des en-têtes IMAP
    core/engine.py      cycle de vie des demandes
    core/discover.py    analyse de la boîte mail, recherche de pseudo
    core/contact.py     recherche d'adresse sur le site d'une entreprise
    core/footprint.py   empreinte publique : recherche, pages trouvées, suppression, déréférencement
    core/task.py        tâche planifiée et lancement au démarrage (Windows)
    outils/adresse.py   vérification et acceptation des adresses proposées (GitHub Actions)
    communaute/         adresses partagées par les utilisateurs
    web/index.html      interface
    web/icone.ico       icône (dessinée par outils/icone.py)

## Crédits et licence

Code sous licence MIT (voir `LICENSE`). Base d'entreprises et modèles de courriers adaptés du projet
[Datenanfragen.de](https://www.datenanfragen.de) (CC0). Liste de sites du projet
[Sherlock](https://github.com/sherlock-project/sherlock) (MIT), téléchargée à l'usage.

Efface-moi est un outil d'aide, pas un conseil juridique.
