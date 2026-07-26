# Secret Club Assistant V3.5

# Secret Club Assistant v3.0.0

Πλήρης αναβάθμιση της λειτουργικής v2, με ασφαλή μετάβαση και αυτόματη αναβάθμιση της υπάρχουσας βάσης.

## Τι περιλαμβάνει

- Welcome και υποχρεωτική αποδοχή κανόνων
- Προσωρινό mute μέχρι την αποδοχή
- Warnings, mute, unmute, kick, ban και purge
- Anti-links, anti-flood, anti-repeat και βασικό anti-scam
- Απαγορευμένες λέξεις
- Verification με φωτογραφία προαιρετικά και κουμπιά έγκρισης/απόρριψης
- Ticket system με αμφίδρομες απαντήσεις μέλους ↔ admins
- Αναφορές με screenshots
- Αυτόματη δημιουργία και δημοσίευση παρουσίασης
- Έλεγχο παρουσίασης πριν από τη δημοσίευση
- Dashboard, XP, levels, `/rank` και `/top`
- Join requests με κουμπιά έγκρισης/απόρριψης
- Προγραμματισμένες ανακοινώσεις και events
- Safe inactivity δύο σταδίων: προειδοποίηση και μετά προαιρετικό kick
- Βοηθό FAQ χωρίς API key
- Προαιρετικό AI μέσω OpenAI API
- Διαγραφή προσωπικών δεδομένων με `/deletemydata`

---

## 1. Αντικατάσταση αρχείων στο GitHub

Ανέβασε όλα τα παρακάτω αρχεία από το ZIP και επίλεξε **Commit changes**:

- `bot.py`
- `v3_core.py`
- `v3_flows.py`
- `v3_features.py`
- `requirements.txt`
- `Dockerfile`
- `railway.json`
- `README.md`

Το `.env.example` είναι μόνο οδηγός και δεν περιέχει token.

Το Railway θα ξεκινήσει νέο deployment αυτόματα. Αν όχι, πάτησε **Redeploy**.

---

## 2. Railway Volume

Στο Railway πρόσθεσε Volume με mount path:

```text
/data
```

Η βάση αποθηκεύεται εδώ:

```text
/data/secret_club.db
```

Έτσι δεν χάνονται activity, warnings, tickets, verification, XP και schedules στα deployments.

---

## 3. Railway Variables

Το μόνο απολύτως υποχρεωτικό είναι:

```text
BOT_TOKEN=το_token_σου
```

Κράτησε αρχικά:

```text
INACTIVE_CHECK_ENABLED=false
INACTIVE_AUTO_KICK=false
AUTO_APPROVE_JOIN_REQUESTS=false
```

Δεν χρειάζεται να βρεις μόνος σου τα IDs των ομάδων.

---

## 4. Σύνδεση κύριας ομάδας

Μέσα στην κύρια ομάδα **Secret Club**, ως admin, γράψε:

```text
/setupgroup
```

Το bot θα αποθηκεύσει αυτόματα το ID της ομάδας.

---

## 5. Ιδιωτική ομάδα admins

Δημιούργησε δεύτερη ιδιωτική ομάδα μόνο για admins, π.χ. **Secret Club Admins**.

1. Πρόσθεσε το bot.
2. Κάνε το bot admin.
3. Μέσα στην ομάδα γράψε:

```text
/setupadminchat
```

Εκεί θα φτάνουν verification, tickets, reports και join requests.

Η admin ομάδα πρέπει να είναι διαφορετική από την κύρια ομάδα.

---

## 6. Έλεγχος ρύθμισης

Γράψε στην κύρια ομάδα:

```text
/setupstatus
/botstatus
/v3help
/dashboard
```

Το `/setupstatus` πρέπει να δείξει:

```text
Κύρια ομάδα: ✅
Admin chat: ✅
```

---

## 7. Δοκιμές πριν το αφήσεις μόνο του

Κάνε αυτές τις δοκιμές με δεύτερο λογαριασμό:

1. Welcome και αποδοχή κανόνων
2. `/warn`
3. `/mute 1`
4. `/unmute`
5. Verification και έγκριση από την admin ομάδα
6. Ticket και απάντηση από admin ως reply
7. Αναφορά με screenshot
8. Δημιουργία και δημοσίευση παρουσίασης

---

## 8. Feature switches

Δες τις νέες λειτουργίες:

```text
/feature
```

Παραδείγματα:

```text
/feature levels on
/feature level_notices on
/feature presentation_verified on
/feature inactivity on
/feature inactive_kick off
/feature ai on
```

### Διαθέσιμα v3 features

- `levels`
- `level_notices`
- `auto_approve`
- `inactivity`
- `inactive_kick`
- `presentation_verified`
- `ai`

---

## 9. Inactive users

Το Telegram δεν δίνει στο bot παλιό ιστορικό «τελευταίας εμφάνισης» όλων των μελών. Η καταγραφή αρχίζει όταν το bot δει ένα μέλος να μπαίνει ή να γράφει.

Λίστα υποψήφιων inactive:

```text
/inactive
```

Χειροκίνητος έλεγχος:

```text
/inactive_run
```

Ενεργοποίηση καθημερινού ελέγχου:

```text
/feature inactivity on
```

Η v3 λειτουργεί σε δύο στάδια:

1. Προειδοποίηση μετά από `INACTIVE_DAYS`.
2. Περίοδος χάριτος `INACTIVE_WARNING_DAYS`.
3. Αφαίρεση μόνο όταν ενεργοποιήσεις:

```text
/feature inactive_kick on
```

Άφησε το `inactive_kick` κλειστό για αρκετές εβδομάδες, μέχρι να έχει συγκεντρωθεί πραγματικό activity history.

Για εξαίρεση μέλους, απάντησε σε μήνυμά του:

```text
/exempt
```

---

## 10. Προγραμματισμένες ανακοινώσεις και events

Καθημερινή ανακοίνωση:

```text
/schedule daily 21:00 | Το κείμενο της ανακοίνωσης
```

Κάθε Παρασκευή:

```text
/schedule weekly FRI 21:00 | Το κείμενο της ανακοίνωσης
```

Μία φορά:

```text
/schedule once 2026-08-01 21:00 | Το κείμενο του event
```

Λίστα:

```text
/schedules
```

Διαγραφή:

```text
/delschedule 3
```

Η ώρα ακολουθεί το `TIMEZONE`, με προεπιλογή `Europe/Athens`.

---

## 11. Verification

Το μέλος ανοίγει προσωπική συνομιλία με το bot και πατά:

```text
/start → Verification
```

Η φωτογραφία είναι προαιρετική. Το bot ζητά ρητά να μη σταλεί ταυτότητα, έγγραφο, τραπεζικό στοιχείο ή κωδικός.

Η αίτηση φτάνει στην admin ομάδα με κουμπιά:

- ✅ Έγκριση
- ❌ Απόρριψη

---

## 12. Tickets και reports

Το μέλος πατά:

```text
/start → Ticket
```

Ο admin απαντά στην admin ομάδα κάνοντας **reply** πάνω στο μήνυμα του ticket. Το bot προωθεί την απάντηση στο μέλος.

Για αναφορά:

```text
/start → Αναφορά μέλους
```

---

## 13. Παρουσιάσεις

Το μέλος πατά:

```text
/start → Δημιουργία παρουσίασης
```

Το bot ζητά κατηγορία, περιοχή, ηλικίες, τι αναζητά και σύντομο κείμενο. Μετά εμφανίζει preview και κουμπί δημοσίευσης στην κύρια ομάδα.

Για να επιτρέπεται δημοσίευση μόνο σε verified μέλη:

```text
/feature presentation_verified on
```

---

## 14. AI / FAQ βοηθός

Χωρίς κανένα API key, ο βοηθός λειτουργεί με τοπικές FAQ απαντήσεις.

Για πλήρες AI πρόσθεσε στο Railway:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5-mini
```

Αν η κλήση AI αποτύχει, το bot επιστρέφει αυτόματα στις τοπικές FAQ.

---

## 15. Δικαιώματα bot

Στην κύρια ομάδα δώσε:

- Delete messages
- Ban users
- Restrict members
- Invite users / Manage join requests

Δεν χρειάζεται:

- Add new admins
- Anonymous admin

Στο BotFather κράτησε το **Group Privacy → Turn off**.

---

## 16. Migration από v2

Η v3 χρησιμοποιεί την ίδια βάση και προσθέτει αυτόματα τα νέα πεδία. Μην διαγράψεις το Volume ή το `/data/secret_club.db`.
