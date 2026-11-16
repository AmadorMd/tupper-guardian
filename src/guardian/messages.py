"""User-facing bot messages (Spanish) for every notification kind.

`build` returns the text to send, optional inline buttons, and an optional
plain-speech version used for the voice note.
"""

import os
from dataclasses import dataclass

# Callback codes carried in button data as "<code>|<tupper_id>".
BUTTON_EATEN = "e"
BUTTON_TOSSED = "t"
BUTTON_SNOOZE = "s"

BUTTON_REVIEW = "r"
BUTTON_SKIP_DATE = "x"

HELP_TEXT = (
    "Soy el Guardián de Tuppers. 🧊 Llevo la cuenta de lo que hay en el refri de tu "
    "familia y te aviso antes de que algo se eche a perder.\n\n"
    "• Cuéntame qué guardaste: «Guardé caldo de pollo en el refri».\n"
    "• O mándame una foto del alimento.\n"
    "• ¿Fuiste al súper? Mándame la lista o una foto del ticket y anoto todo.\n"
    "• Lo que no va al refri (latas, arroz): dime su fecha de caducidad o mándame "
    "una foto de la fecha.\n"
    "• Cuando se acabe: «Ya me comí el caldo» o «Ya tiré el caldo».\n"
    "• Para ver qué hay en el refri: /refri\n"
    "• Para compartir el refri con tu familia: /familiar"
)

FAMILY_INVITATION = (
    "💡 ¿Quieres que alguien de tu familia te eche una mano? Escribe /familiar y te doy "
    "un enlace para compartirle el refri. Así podrá ver qué hay y ayudarte a revisar lo "
    "que vence."
)


@dataclass
class Message:
    text: str
    buttons: list | None = None
    speech: str | None = None


def _days(count):
    return "1 día" if count == 1 else f"{count} días"


def _ate_overdue(data):
    """Health first: no celebration for eating food that is past its deadline.
    Spoken as well, because this is the message that matters most."""
    advice = (
        "Sé que cuesta tirar comida: a muchos nos enseñaron a no desperdiciar nada. "
        f"Pero «{data['food']}» ya había pasado su fecha, y comer algo así puede hacerte "
        "daño aunque se vea y huela bien, o aunque le quites la parte fea. Tirar a tiempo "
        "lo que ya caducó no es desperdicio: es cuidar tu salud. Si llegas a sentirte mal, "
        "avísale a alguien de confianza o a tu médico."
    )
    return Message(
        f"Gracias por decírmelo. 💛 {advice}", speech=f"Gracias por decírmelo. {advice}"
    )


def _date_question(question):
    """Ask for one food's printed expiry date; returns (text, buttons)."""
    if not question:
        return "", None
    text = (
        f"📅 ¿Cuál es la fecha de caducidad de «{question['food']}»? Escríbemela (por ejemplo "
        "15/03/2027) o mándame una foto de la fecha del empaque."
    )
    skip = f"{BUTTON_SKIP_DATE}|{question['tupper_id']}"
    return text, [[{"text": "⏭️ No registrar", "callback_data": skip}]]


def _with_question(text, question, speech=None):
    asked, buttons = _date_question(question)
    return Message("\n\n".join(part for part in (text, asked) if part), buttons=buttons, speech=speech)


def _date(iso):
    year, month, day = iso.split("-")
    return f"{day}/{month}/{year}"


def _dated(data):
    outcome, food = data.get("outcome"), data.get("food") or "ese alimento"
    if outcome == "past":
        return Message(
            f"Esa fecha ya pasó. Revisa el empaque de «{food}»: si ya caducó, por tu salud, mejor a la basura. "
            "Si me equivoqué, mándame la fecha otra vez."
        )
    if outcome != "dated":
        return Message(
            "No estoy esperando ninguna fecha. Si compraste algo que no va en el refri, "
            "cuéntame qué es y te pregunto cuándo caduca."
        )
    when = _date(data["date"])
    if data.get("tracked"):
        sentence = f"Anoté {food} con caducidad {when}. Te aviso antes de esa fecha."
        return _with_question(f"✅ {sentence}", data.get("next"), speech=sentence)
    text = (
        f"✅ Anoté {food} con caducidad {when}. Falta más de un año, así que no te mando "
        "avisos; la fecha queda guardada en /refri."
    )
    return _with_question(text, data.get("next"))


def _stored(data):
    if data.get("awaiting_date"):
        return _with_question(
            f"🥫 No necesita refrigeración: «{data['food']}». Consulta su fecha de caducidad.",
            data.get("ask_date"),
        )
    food, days = data["food"], data["days"]
    text = f"✅ Anotado: {food}. Dura aproximadamente {_days(days)} en el refri. Yo te aviso."
    speech = f"Anotado: {food}. Dura aproximadamente {_days(days)} en el refri. Yo te aviso."
    if data.get("default_used"):
        extra = " No tengo ese alimento en mi tabla, así que usé un tiempo corto por seguridad."
        text += extra
        speech += extra
    return Message(text, speech=speech)


def _stored_list(data):
    """One confirmation for a whole shopping list, with each food's days."""
    items, skipped = data.get("items") or [], data.get("skipped") or []
    parts = []
    if items:
        count = "1 alimento" if len(items) == 1 else f"{len(items)} alimentos"
        lines = [
            f"• {item['food']}: {_days(item['days'])}"
            + (" (no está en mi tabla, usé un tiempo corto)" if item.get("default_used") else "")
            for item in items
        ]
        parts.append(
            f"🛒 Anoté {count} de tu lista. Días en el refri según la guía FoodKeeper "
            "del USDA, con el tiempo más corto que recomienda:\n" + "\n".join(lines)
        )
        parts.append("Te aviso uno por uno antes de que venzan.")
    if skipped:
        needs = "No necesita" if len(skipped) == 1 else "No necesitan"
        parts.append(
            f"🥫 {needs} refrigeración, consulta su fecha de caducidad: {', '.join(skipped)}."
        )
    dropped = int(data.get("dropped") or 0)
    if dropped:
        more = "1 alimento más" if dropped == 1 else f"{dropped} alimentos más"
        parts.append(
            f"Tu lista traía {more} de los que puedo anotar de una vez. "
            "Mándamelos en otro mensaje."
        )
    speech = f"Anoté {count} de tu lista. Te aviso antes de que venzan." if items else None
    return _with_question("\n\n".join(parts), data.get("ask_date"), speech=speech)


def _reminder(data):
    sentence = f"Mañana vence: {data['food']}. Aprovecha hoy."
    tupper_id = data["tupper_id"]
    # Lets people say it is already gone, before its date (L18).
    buttons = [
        [{"text": "😋 Ya me lo comí", "callback_data": f"{BUTTON_EATEN}|{tupper_id}"}],
        [{"text": "🗑️ Ya lo tiré", "callback_data": f"{BUTTON_TOSSED}|{tupper_id}"}],
    ]
    return Message(f"⏰ {sentence}", buttons=buttons, speech=sentence)


def _waiting(count):
    if not count:
        return ""
    more = "1 alimento más" if count == 1 else f"{count} alimentos más"
    return f"\n\nTienes {more} por revisar. Te pregunto uno por uno."


def _ask(data):
    tupper_id = data["tupper_id"]
    tossed = {"callback_data": f"{BUTTON_TOSSED}|{tupper_id}"}
    eaten = {"callback_data": f"{BUTTON_EATEN}|{tupper_id}"}
    final_buttons = [
        [{"text": "🤢 Ya me lo comí", **eaten}],
        [{"text": "🗑️ Ya lo tiré", **tossed}],
    ]
    more = _waiting(data.get("waiting", 0))
    if data.get("review"):
        # Asked from the list, maybe by a relative: neutral about whose food it is.
        sentence = (
            f"«{data['food']}» ya pasó su fecha límite y nadie me dijo qué pasó. "
            "¿Qué pasó con este alimento?"
        )
        return Message(f"🔎 {sentence}{more}", buttons=final_buttons)
    if data.get("last_chance"):
        # Final question: no snooze, so the user tells us how the food ended up.
        sentence = f"Último aviso: ya pasó la fecha límite de «{data['food']}». ¿Qué pasó?"
        return Message(f"⚠️ {sentence}{more}", buttons=final_buttons, speech=sentence)
    sentence = f"Llegó la fecha límite de «{data['food']}». ¿Qué pasó?"
    buttons = [
        [{"text": "😋 Ya me lo comí", **eaten}],
        [{"text": "🗑️ Lo tiré", **tossed}],
        [{"text": "🧊 Sigue ahí", "callback_data": f"{BUTTON_SNOOZE}|{tupper_id}"}],
    ]
    return Message(f"⚠️ {sentence}{more}", buttons=buttons, speech=sentence)


def _removed(data, eaten):
    if eaten and data.get("overdue"):
        return _ate_overdue(data)
    opening = "¡Buen provecho! Quité" if eaten else "Entendido, quité"
    return Message(f"{opening} {data.get('food') or 'ese alimento'} del refri.")


def _resolved(data):
    action = data.get("action")
    if action in ("eaten", "tossed"):
        return _removed(data, eaten=action == "eaten")
    if action == "snooze":
        return Message(
            "De acuerdo, te vuelvo a preguntar mañana. Si huele o se ve raro, mejor no te arriesgues."
        )
    if action == "pending":
        return Message(
            f"«{data.get('food') or 'Ese alimento'}» sigue en el refri, en la lista de vencidos. "
            "Escribe /refri para revisar."
        )
    if action == "skip_date":
        return _with_question(f"Listo. No registré {data.get('food')}.", data.get("next"))
    if action == "review":
        if data.get("review_id"):
            return Message("Vamos uno por uno. 👇")
        return Message("No hay alimentos vencidos por revisar. ✅")
    return Message("Ese alimento ya estaba resuelto.")


def _consumed(data):
    if data.get("found"):
        return _removed(data, eaten=data.get("action") != "tossed")
    return Message("No encontré ese alimento en el refri. Escribe /refri para ver qué hay.")


def _list_line(item):
    left = item["days_left"]
    when = "ya venció" if left <= 0 else f"vence en {_days(left)}"
    who = f" (guardó {item['stored_by_name']})" if item.get("stored_by_name") else ""
    pantry = "🥫 " if item.get("pantry") else ""
    return f"• {pantry}{item['food']}: {when}{who}"


def _list(data):
    items = data.get("items") or []
    if not items:
        return Message("El refri está vacío. Cuéntame qué guardaste o mándame una foto.")
    groups = [
        ("⚠️ Vencidos, por revisar:", [i for i in items if i["days_left"] <= 0]),
        ("⏰ Vencen pronto:", [i for i in items if i["days_left"] == 1]),
        ("✅ En buen estado:", [i for i in items if i["days_left"] > 1]),
    ]
    lines = ["🧊 En el refri:"]
    for title, group in groups:
        if group:
            lines += ["", title, *map(_list_line, group)]
    pending = data.get("review_count", 0)
    buttons = None
    if pending:
        label = f"🔎 Revisar vencidos ({pending})"
        buttons = [[{"text": label, "callback_data": f"{BUTTON_REVIEW}|0"}]]
    return Message("\n".join(lines), buttons=buttons)


def _invite(data):
    username = os.environ.get("BOT_USERNAME", "")
    link = f"https://t.me/{username}?start=care_{data['code']}"
    return Message(
        "Comparte este enlace con alguien de tu familia. Quien lo abra podrá ver qué hay "
        "en el refri, anotar alimentos y marcar lo que ya se comió o se tiró. Además, si "
        "un alimento vence y no me contestas, le aviso para que te ayude a revisarlo.\n"
        f"{link}\n\n"
        "El enlace sirve una sola vez. Pide otro con /familiar para cada persona."
    )


def _link(data):
    if data.get("linked"):
        name = data.get("owner_name") or "tu familia"
        return Message(
            f"Listo, ya compartes el refri de {name}. Escribe /refri para ver qué hay. "
            "Te avisaré cuando un alimento venza y nadie lo haya atendido."
        )
    own_items = data.get("own_items", 0)
    if own_items:
        food = "1 alimento" if own_items == 1 else f"{own_items} alimentos"
        return Message(
            f"Todavía tienes {food} en tu refri, y al cambiarte de refri nadie los vería. "
            "Escribe /refri, dime qué pasó con cada uno y después abre este mismo enlace "
            "otra vez."
        )
    return Message(
        "Ese enlace ya no es válido, o ya estás en ese refri. Pide uno nuevo con /familiar."
    )


def _unanswered(data):
    text = (
        f"No supe qué pasó con «{data['food']}», así que sigue en el refri, en la lista "
        "de vencidos. Cuando puedas, escribe /refri para revisar."
    )
    if not data.get("others"):
        text += f"\n\n{FAMILY_INVITATION}"
    return Message(text)


def _escalation(data):
    name = data.get("owner_name") or "Alguien de tu familia"
    return Message(
        f"👋 {name} tiene algo vencido en el refri y no ha respondido: {data['food']}. "
        "Escribe /refri para revisar, o pregúntale qué pasó."
    )


_BUILDERS = {
    "stored": _stored,
    "stored_list": _stored_list,
    "dated": _dated,
    "reminder": _reminder,
    "ask": _ask,
    "resolved": _resolved,
    "consumed": _consumed,
    "list": _list,
    "invite": _invite,
    "link": _link,
    "unanswered": _unanswered,
    "escalation": _escalation,
    "help": lambda data: Message(HELP_TEXT),
    "unknown": lambda data: Message("No te entendí bien. 🤔\n\n" + HELP_TEXT),
    "voice_unsupported": lambda data: Message(
        "Por ahora entiendo texto y fotos. Escríbeme qué guardaste o mándame una foto."
    ),
    "error": lambda data: Message("Tuve un problema para procesar tu mensaje. Inténtalo de nuevo."),
}


def build(kind, data=None):
    builder = _BUILDERS.get(kind, _BUILDERS["unknown"])
    return builder(data or {})
