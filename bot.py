import logging
import os
from typing import Final

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN: Final[str] = os.getenv("BOT_TOKEN", "").strip()
ADMIN_USERNAME: Final[str] = os.getenv("ADMIN_USERNAME", "").strip().lstrip("@")
GROUP_URL: Final[str] = os.getenv("GROUP_URL", "").strip()

WELCOME_TEXT = """
🖤 <b>SECRET CLUB ASSISTANT</b>

Καλώς ήρθες!

Εδώ θα βρεις εύκολα:
• πρότυπα παρουσίασης
• κατηγορίες και περιοχές
• κανόνες και συμβουλές ασφάλειας
• τρόπο αναφοράς προβλήματος

👇 Διάλεξε αυτό που χρειάζεσαι:
""".strip()

PAGES = {
    "rules": """
📜 <b>ΚΑΝΟΝΕΣ SECRET CLUB</b>

✅ Σεβόμαστε όλα τα μέλη.
✅ Η διακριτικότητα είναι απαραίτητη.
✅ Απαγορεύονται spam και διαφημίσεις.
✅ Απαγορεύεται η προώθηση επαγγελματικών σεξουαλικών υπηρεσιών.
✅ Δεν κοινοποιούμε φωτογραφίες, μηνύματα ή προσωπικά στοιχεία άλλων χωρίς άδεια.
✅ Οι προσωπικές γνωριμίες και συναντήσεις γίνονται με ευθύνη των ίδιων των μελών.
✅ Οι αποφάσεις των διαχειριστών είναι οριστικές.

🔞 Η κοινότητα απευθύνεται αποκλειστικά σε ενήλικες.
""".strip(),

    "tags": """
🏷️ <b>TAGS ΚΑΤΗΓΟΡΙΑΣ & ΠΕΡΙΟΧΗΣ</b>

Στην παρουσίασή σου βάλε:
1️⃣ ένα tag κατηγορίας
2️⃣ ένα tag περιοχής

<b>Κατηγορίες</b>
#Couple
#BiCouple
#SingleM
#SingleF
#BiSingle
#Lesbian
#Gay

<b>Περιοχές</b>
#Αττική  #Αθήνα  #Πειραιάς
#Θεσσαλονίκη  #Πάτρα  #Λάρισα
#Βόλος  #Τρίκαλα  #Καρδίτσα
#Λαμία  #Χαλκίδα  #Κατερίνη
#Σέρρες  #Καβάλα  #Δράμα
#Ξάνθη  #Κομοτηνή  #Αλεξανδρούπολη
#Ιωάννινα  #Άρτα  #Πρέβεζα
#Ηγουμενίτσα  #Αγρίνιο  #Μεσολόγγι
#Κόρινθος  #Ναύπλιο  #Τρίπολη
#Καλαμάτα  #Σπάρτη  #Πύργος
#Ηράκλειο  #Χανιά  #Ρέθυμνο
#Λασίθι  #ΆγιοςΝικόλαος
#Κέρκυρα  #Ζάκυνθος  #Κεφαλονιά
#Λευκάδα  #Λέσβος  #Χίος
#Σάμος  #Ικαρία  #Λήμνος
#Ρόδος  #Κως  #Κάλυμνος
#Κάρπαθος  #Λέρος  #Πάτμος
#Σύμη  #Νίσυρος  #Αστυπάλαια
#Καστελλόριζο  #Δωδεκάνησα
#Σύρος  #Μύκονος  #Πάρος
#Νάξος  #Σαντορίνη  #Μήλος
#Τήνος  #Άνδρος  #Ίος
#Αμοργός  #Κυκλάδες  #ΒόρειοΑιγαίο

<b>Παράδειγμα</b>
#Couple
#Ρόδος

🔎 Στην αναζήτηση του Telegram γράψε π.χ. #Ρόδος για να βρεις παρουσιάσεις από τη Ρόδο.
""".strip(),

    "couple": """
👫 <b>ΠΡΟΤΥΠΟ COUPLE</b>

Αντέγραψε, συμπλήρωσε και δημοσίευσε:

<code>#Couple
#Περιοχή

👫 Ηλικίες:

❤️ Αναζητούμε:

📝 Λίγα λόγια για εμάς:</code>
""".strip(),

    "single": """
👤 <b>ΠΡΟΤΥΠΟ SINGLE</b>

Διάλεξε #SingleM ή #SingleF.

<code>#SingleM ή #SingleF
#Περιοχή

🎂 Ηλικία:

❤️ Αναζητώ:

📝 Λίγα λόγια για εμένα:</code>
""".strip(),

    "bicouple": """
🌈 <b>ΠΡΟΤΥΠΟ BI COUPLE</b>

<code>#BiCouple
#Περιοχή

👫 Ηλικίες:

❤️ Αναζητούμε:

📝 Λίγα λόγια για εμάς:</code>
""".strip(),

    "bisingle": """
🩷 <b>ΠΡΟΤΥΠΟ BI SINGLE</b>

<code>#BiSingle
#Περιοχή

🎂 Ηλικία:

❤️ Αναζητώ:

📝 Λίγα λόγια για εμένα:</code>
""".strip(),

    "lesbian": """
👭 <b>ΠΡΟΤΥΠΟ LESBIAN</b>

<code>#Lesbian
#Περιοχή

🎂 Ηλικία:

❤️ Αναζητώ:

📝 Λίγα λόγια για εμένα:</code>
""".strip(),

    "gay": """
👬 <b>ΠΡΟΤΥΠΟ GAY</b>

<code>#Gay
#Περιοχή

🎂 Ηλικία:

❤️ Αναζητώ:

📝 Λίγα λόγια για εμένα:</code>
""".strip(),

    "safety": """
🛡️ <b>ΣΥΜΒΟΥΛΕΣ ΑΣΦΑΛΕΙΑΣ</b>

• Μίλησε πρώτα αρκετά με το άλλο μέλος.
• Η πρώτη συνάντηση καλό είναι να γίνεται σε δημόσιο χώρο.
• Ενημέρωσε ένα πρόσωπο εμπιστοσύνης για το πού βρίσκεσαι.
• Μην κοινοποιείς προσωπικά στοιχεία πριν δημιουργηθεί εμπιστοσύνη.
• Σεβάσου πάντα τα όρια και τη συναίνεση.
• Μην πιέζεις και μην αποδέχεσαι πίεση.
• Αν κάτι σε ανησυχήσει, σταμάτησε την επικοινωνία και ενημέρωσε τους admins.
""".strip(),

    "report": """
🚨 <b>ΑΝΑΦΟΡΑ ΜΕΛΟΥΣ</b>

Για να εξεταστεί γρήγορα μια αναφορά, στείλε στον admin:

• το username του μέλους
• σύντομη περιγραφή του περιστατικού
• screenshots, εφόσον υπάρχουν
• ημερομηνία ή ώρα του συμβάντος

Οι αναφορές εξετάζονται με διακριτικότητα.
""".strip(),

    "faq": """
❓ <b>ΣΥΧΝΕΣ ΕΡΩΤΗΣΕΙΣ</b>

<b>Είναι δωρεάν;</b>
Ναι, η κοινότητα είναι εντελώς δωρεάν.

<b>Είναι τα μέλη verified;</b>
Η κοινότητα εφαρμόζει διαδικασία επαλήθευσης των μελών.

<b>Πώς βρίσκω άτομα από την περιοχή μου;</b>
Πάτησε «Tags & Περιοχές» και αναζήτησε το αντίστοιχο hashtag στο Telegram.

<b>Επιτρέπονται επαγγελματικές υπηρεσίες;</b>
Όχι. Απαγορεύεται η προώθηση ή αναζήτηση επαγγελματικών σεξουαλικών υπηρεσιών.

<b>Πώς κάνω αναφορά;</b>
Πάτησε «Αναφορά» και ακολούθησε τις οδηγίες.
""".strip(),
}


def main_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📜 Κανόνες", callback_data="rules"),
            InlineKeyboardButton("🏷️ Tags & Περιοχές", callback_data="tags"),
        ],
        [
            InlineKeyboardButton("👫 Couple", callback_data="couple"),
            InlineKeyboardButton("👤 Single", callback_data="single"),
        ],
        [
            InlineKeyboardButton("🌈 Bi Couple", callback_data="bicouple"),
            InlineKeyboardButton("🩷 Bi Single", callback_data="bisingle"),
        ],
        [
            InlineKeyboardButton("👭 Lesbian", callback_data="lesbian"),
            InlineKeyboardButton("👬 Gay", callback_data="gay"),
        ],
        [
            InlineKeyboardButton("🛡️ Ασφάλεια", callback_data="safety"),
            InlineKeyboardButton("🚨 Αναφορά", callback_data="report"),
        ],
        [InlineKeyboardButton("❓ Συχνές ερωτήσεις", callback_data="faq")],
    ]

    external_buttons = []
    if ADMIN_USERNAME:
        external_buttons.append(
            InlineKeyboardButton(
                "👑 Επικοινωνία με Admin",
                url=f"https://t.me/{ADMIN_USERNAME}",
            )
        )
    if GROUP_URL:
        external_buttons.append(
            InlineKeyboardButton("🖤 Άνοιγμα Secret Club", url=GROUP_URL)
        )
    if external_buttons:
        rows.append(external_buttons)

    return InlineKeyboardMarkup(rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Επιστροφή στο μενού", callback_data="menu")]]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            WELCOME_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()
    selected = query.data

    if selected == "menu":
        await query.edit_message_text(
            WELCOME_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )
        return

    page_text = PAGES.get(selected)
    if not page_text:
        await query.edit_message_text(
            "Δεν βρέθηκε αυτή η επιλογή.",
            reply_markup=back_keyboard(),
        )
        return

    extra_rows = []
    if selected == "report" and ADMIN_USERNAME:
        extra_rows.append(
            [
                InlineKeyboardButton(
                    "📩 Μήνυμα στον Admin",
                    url=f"https://t.me/{ADMIN_USERNAME}",
                )
            ]
        )
    extra_rows.append(
        [InlineKeyboardButton("⬅️ Επιστροφή στο μενού", callback_data="menu")]
    )

    await query.edit_message_text(
        page_text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(extra_rows),
        disable_web_page_preview=True,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Σφάλμα κατά την επεξεργασία ενημέρωσης", exc_info=context.error)


def run() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "Λείπει η μεταβλητή BOT_TOKEN. Πρόσθεσέ την στις Variables του Railway."
        )

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_error_handler(error_handler)

    logger.info("Το Secret Club Assistant ξεκίνησε.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    run()
