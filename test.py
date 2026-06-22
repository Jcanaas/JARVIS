from actions.whatsapp import fetch_messages, resolve_contact, get_conversation
print(fetch_messages(0)[:5])
print(resolve_contact("Rafa"))
print(get_conversation("Rafa", limit=10))