# HR Document Generator - Web Version

Cette application web est une version identique de l'application desktop PyQt6, déployée sur le web avec Flask. Elle conserve toutes les fonctionnalités de l'application desktop tout en permettant l'accès via navigateur.

## 🚀 Démarrage Rapide

### Option 1: Script Automatique (Recommandé)
Double-cliquez sur `start_web_app.bat` - il installera automatiquement toutes les dépendances et démarrera le serveur.

### Option 2: Lancement Manuel

1. **Installer les dépendances :**
   ```bash
   pip install -r requirements.txt
   ```

2. **Démarrer le serveur :**
   ```bash
   python app.py
   ```

3. **Accéder à l'application :**
   Ouvrez votre navigateur : `http://localhost:5000`

## 🔧 Dépannage

### Erreur "ModuleNotFoundError: No module named 'flask'"

**Solution automatique :**
- Double-cliquez sur `troubleshoot.bat` pour diagnostiquer et corriger les problèmes

**Solution manuelle :**
```bash
pip install Flask
pip install -r requirements.txt
```

### Erreur "Python n'est pas reconnu"

- Installez Python depuis https://python.org
- Assurez-vous que Python est dans le PATH système
- Redémarrez votre terminal après l'installation

### Le serveur ne démarre pas

1. Vérifiez qu'aucun autre programme n'utilise le port 5000
2. Fermez tous les terminaux Python
3. Redémarrez votre ordinateur si nécessaire

### Erreur d'import des modules locaux

Assurez-vous que la structure des dossiers est correcte :
```
web_app/
├── app.py
├── src/          ← Ce dossier doit exister
├── config/       ← Ce dossier doit exister
└── templates/
```

### Test de fonctionnement

Exécutez `python test_setup.py` pour vérifier que tout fonctionne correctement.

## 📋 Fonctionnalités

### ✅ Fonctionnalités Identiques à l'App Desktop

- **Attestation de Travail** : Génération avec toutes les informations employé
- **Ordre de Mission** : Création avec détails de déplacement
- **Import Excel** : Support des fichiers locaux et URLs cloud
- **URLs Cloud Supportées** :
  - Google Drive (fichiers et Google Sheets)
  - Dropbox
  - OneDrive/SharePoint
  - Google Sheets (conversion automatique en Excel)
- **Gestion des Références** : Compteurs automatiques avec historique
- **Configuration Société** : Utilise la même config que l'app desktop

### 🌐 Fonctionnalités Web Supplémentaires

- **Interface Web Responsive** : Fonctionne sur desktop, tablette, mobile
- **Téléchargement Direct** : Documents générés téléchargés automatiquement
- **Messages de Feedback** : Notifications de succès/erreur
- **Navigation Intuitive** : Menu de navigation avec icônes

## 🏗️ Architecture

```
web_app/
├── app.py                 # Application Flask principale
├── requirements.txt       # Dépendances Python
├── run_web_app.bat       # Script de lancement Windows
├── templates/            # Templates HTML
│   ├── base.html         # Template de base
│   ├── index.html        # Page d'accueil
│   ├── attestation.html  # Formulaire attestation
│   ├── ordre_mission.html # Formulaire ordre de mission
│   └── references.html   # Gestion des références
├── static/               # Fichiers statiques (CSS, JS, images)
├── uploads/              # Fichiers Excel uploadés (auto-créé)
└── output/               # Documents générés (auto-créé)
```

## 🔧 Configuration

### Configuration Société
L'application utilise le même fichier de configuration que l'app desktop :
- **Fichier** : `../config/company.json`
- **Contenu** : Informations société (nom, adresse, téléphone, etc.)

### Références
- **Fichier** : `../config/references.json`
- **Partagé** : Même système de références que l'app desktop

### Templates
- **Dossier** : `../templates/`
- **Partagé** : Utilise les mêmes templates Word que l'app desktop

## 🌐 Déploiement

### Développement Local
```bash
cd web_app
python app.py
```
Accès : `http://localhost:5000`

### Production (avec Gunicorn)
```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

### Docker (Optionnel)
```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["python", "app.py"]
```

## 🔒 Sécurité

- **Uploads Sécurisés** : Validation des types de fichiers Excel
- **Limite de Taille** : Maximum 16MB par fichier
- **Nettoyage Automatique** : Fichiers temporaires supprimés après traitement
- **Validation des Données** : Champs requis validés côté serveur

## 🐛 Dépannage

### Erreurs Courantes

**Erreur d'import de modules :**
```bash
# Assurez-vous que le dossier parent est accessible
cd web_app
python app.py
```

**Port déjà utilisé :**
```bash
# Changez le port dans app.py
app.run(debug=True, host='0.0.0.0', port=5001)
```

**Erreur de configuration société :**
- Vérifiez que `../config/company.json` existe
- Format JSON valide requis

### Logs
Les erreurs sont affichées dans la console. Pour le debug :
```python
app.run(debug=True, host='0.0.0.0', port=5000)
```

## 📱 Utilisation Mobile

L'application est entièrement responsive et fonctionne sur :
- ✅ Ordinateurs de bureau
- ✅ Tablettes
- ✅ Téléphones mobiles
- ✅ Tous les navigateurs modernes

## 🔄 Synchronisation avec l'App Desktop

- **Configuration Partagée** : Même fichiers config utilisés
- **Références Partagées** : Compteurs synchronisés
- **Templates Partagés** : Même modèles de documents
- **Données Indépendantes** : Fonctionnement séparé possible

## 🚀 Fonctionnalités Futures

- [ ] Authentification utilisateur
- [ ] Base de données pour historique
- [ ] API REST pour intégrations
- [ ] Export PDF direct
- [ ] Interface d'administration
- [ ] Notifications par email

## 📞 Support

Pour les problèmes spécifiques à la version web :
1. Vérifiez les logs de la console
2. Testez avec l'app desktop pour comparer
3. Vérifiez la configuration des chemins relatifs

---

**Version Web :** 1.0.0
**Compatible avec Desktop :** Toutes versions
**Dernière mise à jour :** Mars 2026