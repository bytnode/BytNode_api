"""La COUCHE CONTRAT du moteur BytNode — le code du bord, celui que le client voit.

Ces modules n'ont aucune connaissance du stockage, des sources ou des calculs :
ils decident ce qui sort, sous quel nom, dans quelle enveloppe, et ce qui est
refuse. C'est volontairement le seul etage publie de ce depot.

- `response`      — l'enveloppe unique de toute reponse publique
- `erreurs`       — le format d'erreur unique, et des codes stables
- `nommage`       — le vocabulaire public : un concept = un nom
- `query_strict`  — un parametre inconnu est une erreur, pas un silence
- `history_limits`/`history_grid` — la profondeur d'historique de l'offre, en un point
- `pagination`    — `next`, `step`, et des bornes qui ne mentent pas
- `field_selection`/`fields` — `?fields=`, valide contre les cles reelles
- `timewindow`    — les bornes `since_ms` / `until_ms`
- `horodatage`    — un seul format d'instant, partout
- `valeurs_fermees` — les valeurs acceptees, une seule source pour le contrat et les 422
- `availability`  — pourquoi une reponse est vide
- `familles_publiques` — le nom public d'une famille de donnees
"""
