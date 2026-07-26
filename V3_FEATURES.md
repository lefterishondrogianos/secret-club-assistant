# V3 Features

## Μέλη

- `/start`
- `/presentation`
- `/verify`
- `/ticket`
- `/report`
- `/ask`
- `/rank`
- `/top`
- `/privacy`
- `/deletemydata`

## Admin setup

- `/setupgroup`
- `/setupadminchat`
- `/setupstatus`
- `/feature`
- `/v3help`

## Admin dashboard / automation

- `/dashboard`
- `/announce κείμενο`
- `/schedule daily HH:MM | κείμενο`
- `/schedule weekly DAY HH:MM | κείμενο`
- `/schedule once YYYY-MM-DD HH:MM | κείμενο`
- `/schedules`
- `/delschedule ID`
- `/inactive`
- `/inactive_run`

## Moderation από v2

- `/warn`
- `/clearwarns`
- `/mute`
- `/unmute`
- `/kick`
- `/ban`
- `/purge`
- `/blockword`
- `/unblockword`
- `/exempt`
- `/unexempt`

## V3.5 — Manual Verification

Admin-only commands:

- `/verifyuser` — reply σε μήνυμα ή `/verifyuser TELEGRAM_ID`
- `/verifyid TELEGRAM_ID` — alias
- `/unverifyuser` — reply ή ID
- `/unverifyid TELEGRAM_ID` — alias
- `/toggleverify` — αλλάζει την τρέχουσα κατάσταση
- `/isverified` — έλεγχος με reply ή ID
- `/verifyallknown` — επαληθεύει όλα τα μέλη που γνωρίζει η βάση στην τρέχουσα ομάδα
- `/unverifyallknown CONFIRM` — μαζική αφαίρεση με υποχρεωτική επιβεβαίωση
- `/verifiedlist`
- `/unverifiedlist`
- `/verifystats`

Οι χειροκίνητες αλλαγές καταγράφονται στο admin log και στο `verification_audit`.
