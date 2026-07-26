# Secret Club Assistant

Έτοιμο Telegram bot με πατήσιμα κουμπιά για:

- Κανόνες
- Tags και περιοχές
- Πρότυπα Couple / Single / Bi Couple / Bi Single / Lesbian / Gay
- Ασφάλεια
- Αναφορά μέλους
- Συχνές ερωτήσεις
- Επικοινωνία με Admin

## Ανέβασμα στο GitHub από κινητό

1. Άνοιξε το repository `secret-club-assistant`.
2. Πάτησε **Add file** → **Upload files**.
3. Ανέβασε όλα τα αρχεία του ZIP, όχι τον ίδιο τον φάκελο.
4. Πάτησε **Commit changes**.

Τα βασικά αρχεία που πρέπει να φαίνονται στην αρχική σελίδα του repository είναι:

- `bot.py`
- `requirements.txt`
- `Dockerfile`
- `railway.json`

Μην ανεβάσεις ποτέ το token στο GitHub.

## Σύνδεση με Railway

1. Άνοιξε Railway και επίλεξε **New Project**.
2. Επίλεξε **Deploy from GitHub Repo**.
3. Διάλεξε το repository `secret-club-assistant`.
4. Στις **Variables** πρόσθεσε:

### Υποχρεωτική μεταβλητή

`BOT_TOKEN`

Τιμή: το token που έδωσε ο BotFather.

### Προαιρετικές μεταβλητές

`ADMIN_USERNAME`

Τιμή: το username του admin χωρίς `@`.

Παράδειγμα:

`myusername`

`GROUP_URL`

Τιμή: ολόκληρο το link της ομάδας.

Παράδειγμα:

`https://t.me/secretclubexample`

5. Κάνε Deploy ή Redeploy.
6. Όταν το deployment γίνει επιτυχές, άνοιξε το bot στο Telegram και πάτησε `/start`.

## Ασφάλεια token

Το token είναι κωδικός πλήρους ελέγχου του bot.

- Μην το βάλεις στο `bot.py`.
- Μην το ανεβάσεις στο GitHub.
- Μην το στείλεις σε group ή screenshot.
- Αν διαρρεύσει, πήγαινε στον BotFather και κάνε revoke/regenerate.

## Αλλαγή κειμένων

Άνοιξε το `bot.py` στο GitHub και πάτησε το μολύβι.

- Το αρχικό κείμενο βρίσκεται στο `WELCOME_TEXT`.
- Οι σελίδες βρίσκονται στο `PAGES`.
- Τα κουμπιά βρίσκονται στη συνάρτηση `main_keyboard()`.

Μετά πάτησε **Commit changes**. Το Railway συνήθως κάνει αυτόματο redeploy.
