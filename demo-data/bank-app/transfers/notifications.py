"""Notifications liées aux virements."""


# REQ-012 : prévenir le client quand une vérification supplémentaire est déclenchée.
def notify_extra_verification(account):
    return f"SMS envoyé au {account.phone}"
