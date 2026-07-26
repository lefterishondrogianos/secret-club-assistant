# Secret Club Assistant v2

Πλήρες Telegram bot διαχείρισης με:

- ιδιωτικό μενού με κουμπιά
- welcome νέων μελών
- υποχρεωτική αποδοχή κανόνων
- προσωρινό mute μέχρι την αποδοχή
- καταγραφή δραστηριότητας
- anti-links
- anti-flood
- anti-repeat
- απαγορευμένες λέξεις
- warnings
- mute / unmute
- kick / ban
- purge μηνυμάτων
- join requests
- inactive member report και προαιρετικό auto-kick
- admin logs
- SQLite βάση δεδομένων

## Σημαντικός περιορισμός για inactive users

Το Telegram Bot API δεν δίνει στο bot πλήρη λίστα όλων των μελών ούτε το ιστορικό της
τελευταίας δραστηριότητάς τους. Το bot μπορεί να ελέγχει αδράνεια μόνο για μέλη που έχει
δει να μπαίνουν ή να στέλνουν μήνυμα από τη στιγμή που εγκαταστάθηκε.

Το auto-kick είναι σκόπιμα απενεργοποιημένο αρχικά.

## Αντικατάσταση αρχείων στο GitHub

Στο υπάρχον repository αντικατάστησε:

- `bot.py`
- `requirements.txt`
- `Dockerfile`
- `railway.json`
- `README.md`

Μπορείς επίσης να ανεβάσεις το `.env.example`, αλλά δεν είναι υποχρεωτικό.

## Railway Variables

Κράτησε οπωσδήποτε:

`BOT_TOKEN`

Πρόσθεσε προαιρετικά:

`ADMIN_USERNAME` — χωρίς @

`GROUP_URL` — invite link ιδιωτικού group, μόνο αν θέλεις κουμπί

`LOG_CHAT_ID` — αριθμητικό chat ID για admin logs

### Welcome και αποδοχή κανόνων

`WELCOME_ENABLED=true`

`RULES_GATE_ENABLED=true`

Το bot κάνει προσωρινό mute στο νέο μέλος μέχρι να πατήσει «Αποδέχομαι».

### Anti-spam

`ANTI_LINKS=true`

`ANTI_SPAM=true`

`SPAM_MAX_MESSAGES=6`

`SPAM_WINDOW_SECONDS=10`

`REPEAT_MAX=3`

### Inactive users

Αρχικά βάλε:

`INACTIVE_CHECK_ENABLED=false`

`INACTIVE_AUTO_KICK=false`

Δες πρώτα τη λίστα με:

`/inactive`

Και τρέξε χειροκίνητα έλεγχο με:

`/inactive_run`

Όταν καταλάβεις πώς λειτουργεί, μπορείς να ενεργοποιήσεις:

`INACTIVE_CHECK_ENABLED=true`

`INACTIVE_DAYS=90`

`INACTIVE_WARNING_DAYS=7`

Για προειδοποιήσεις χωρίς διαγραφή:

`INACTIVE_AUTO_KICK=false`

Για αυτόματη αφαίρεση:

`INACTIVE_AUTO_KICK=true`

## Δικαιώματα admin του bot

Στο Telegram group, κάνε το bot administrator και δώσε του:

- Delete messages
- Ban users
- Restrict members
- Invite users / Manage join requests
- Pin messages, μόνο αν το χρειαστείς

## Privacy Mode

Για να βλέπει όλα τα μηνύματα και να καταγράφει δραστηριότητα:

1. Άνοιξε `@BotFather`
2. `/mybots`
3. επίλεξε το bot
4. **Bot Settings**
5. **Group Privacy**
6. **Turn off**

Εναλλακτικά, ως admin το bot λαμβάνει τα μηνύματα της ομάδας, αλλά προτείνεται να
επιβεβαιώσεις ότι το Privacy Mode είναι κλειστό για συνεπή activity tracking.

## Admin commands

Οι περισσότερες moderation εντολές γίνονται ως reply στο μήνυμα του μέλους:

- `/warn [λόγος]`
- `/clearwarns`
- `/mute [λεπτά]`
- `/unmute`
- `/kick [λόγος]`
- `/ban [λόγος]`
- `/exempt`
- `/unexempt`
- `/purge`
- `/blockword λέξη`
- `/unblockword λέξη`
- `/words`
- `/inactive`
- `/inactive_run`
- `/botstatus`
- `/adminhelp`

## Ασφαλής σειρά ενεργοποίησης

1. Ανέβασε το v2.
2. Βεβαιώσου ότι γίνεται successful deploy.
3. Κάνε το bot admin.
4. Κλείσε το Privacy Mode.
5. Δοκίμασε το welcome με ένα δοκιμαστικό μέλος.
6. Δοκίμασε `/warn`, `/mute`, `/unmute`.
7. Άφησε το inactive auto-kick κλειστό για τουλάχιστον μερικές εβδομάδες.
8. Μόνο όταν είσαι βέβαιος ότι όλα λειτουργούν, αφαίρεσε τη Rose.
