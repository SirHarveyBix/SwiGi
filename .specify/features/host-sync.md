# Spec : Fiabilité et synchronisation post-switch

**Version :** 1.1.0
**Date :** 2026-05-22
**Statut :** Implémenté (Simplifié)

---

## 1. Contexte

La commande `CHANGE_HOST` envoyée à la souris via Bluetooth peut occasionnellement échouer en raison de congestions radio (reconnexions Bluetooth massives, drain d'événements, etc.). Pour garantir un basculement extrêmement fiable et synchrone sans introduire de lenteurs ou de bogues d'utilisation, SwiGi utilise un mécanisme robuste de burst et de réinitialisation proactive de l'état.

---

## 2. Solution : Fiabilisation par Burst & Fermeture Proactive

### Le Filet unique : Burst de transport et Fermeture immédiate

Pour éviter tout blocage du thread principal du démon (ce qui dégraderait la réactivité), SwiGi n'attend pas de confirmation par ping actif (qui bloquait le thread principal pendant 300ms à 900ms). Il utilise les mesures de robustesse suivantes :

1. **Double Drain et Rafale (Burst) :** La fonction `send_change_host` vide les buffers d'entrée HID deux fois avant d'émettre, puis envoie la commande `CHANGE_HOST` **5 fois de suite sans délai** au périphérique. Cette redondance au niveau de la couche transport élimine pratiquement tout risque de perte de paquet.
2. **Fermeture Proactive du Transport :** Dès que le burst d'envoi a réussi sans exception initiale, SwiGi considère la bascule comme initiée. Il appelle immédiatement `mouse.close()` pour fermer proprement le descripteur de fichier USB/Bluetooth et réinitialise l'état local en mettant `state["mouse"] = None`.
3. **Reconnexion Proactive :** Le démon libère instantanément son thread pour surveiller la déconnexion inévitable du clavier. Lorsque l'utilisateur bascule de nouveau sur cette machine, le démon redécouvrira les deux appareils proprement.

---

## 3. Pourquoi la resynchronisation au reconnect ("Filet 2") est obsolète

L'implémentation initiale de la resynchronisation au reconnect (`_verify_and_sync`) tentait de comparer le canal Easy-Switch actif du clavier (`kb_host`) et de la souris (`mouse_host`) lors de la reconnexion automatique ou du démarrage du démon. Ce concept s'est avéré logiquement défectueux pour les raisons suivantes :

1. **Limitation physique Bluetooth :** Si le clavier est sur le Mac (Hôte A) et la souris est sur le PC (Hôte B), le démon du Mac ne peut pas s'ouvrir et dialoguer avec la souris. Ainsi, une réelle désynchronisation physique empêche l'obtention des informations de la souris et rend toute correction automatique à distance impossible sur Bluetooth direct.
2. **Régression de boucle de déconnexion infinie :** Si l'utilisateur a connecté ses deux périphériques à la même machine mais sur des canaux Easy-Switch différents (ex. Clavier sur canal 1 et Souris sur canal 2), ils sont parfaitement synchronisés physiquement. Cependant, `_verify_and_sync` détectait une « désynchronisation » car les index bruts de canaux différaient (`0 != 1`). Le démon déconnectait alors continuellement la souris pour tenter de la forcer sur le canal du clavier, créant une boucle infinie de déconnexions toutes les 5 secondes.

Par conséquent, **le Filet 2 a été complètement retiré du code**.

---

## 4. Exigences fonctionnelles

| #   | Exigence                                                                                  | Priorité |
| --- | ----------------------------------------------------------------------------------------- | -------- |
| F1  | L'envoi de CHANGE_HOST doit utiliser un double drain et un burst redondant de 5 écritures | MUST     |
| F2  | Après envoi réussi de la bascule, fermer immédiatement le transport de la souris sans lag | MUST     |
| F3  | Réinitialiser `state["mouse"] = None` pour permettre une détection propre au retour       | MUST     |
| F4  | Pas de blocage du thread principal par des pings d'attente actifs                         | MUST     |

---

## 5. Timings

| Étape                             | Durée | Impact perçu                                 |
| --------------------------------- | ----- | -------------------------------------------- |
| Burst CHANGE_HOST et Close souris | < 5ms | Instantané, aucun blocage pour l'utilisateur |

---

## 6. Conformité constitution

| Principe        | Impact     | Mesure                                                       |
| --------------- | ---------- | ------------------------------------------------------------ |
| Simplicité      | ✅ Positif | Suppression de codes complexes de ping/sleep et de resync    |
| Portabilité     | ✅ Neutre  | Standard HID++ 2.0 préservé                                  |
| Robustesse      | ✅ Positif | Élimination des plantages ou boucles de déconnexion infinies |
| Non-intrusivité | ✅ Neutre  | Aucune modification de permissions                           |
| Réactivité      | ✅ Positif | Suppression des sleeps de 300-900ms, bascule instantanée     |
