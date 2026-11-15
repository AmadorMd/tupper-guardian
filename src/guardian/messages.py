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

HELP_TEXT = (
    "Soy el Guardián de Tuppers. 🧊\n\n"
    "• Cuéntame qué guardaste: «Guardé caldo de pollo en el refri».\n"
    "• O mándame una foto del tupper.\n"
    "• Cuando te lo acabes: «Ya me comí el caldo».\n"
    "• Para ver tu lista: «¿Qué tengo en el refri?».\n"
    "• Para que un familiar reciba avisos: /familiar"
)


@dataclass
class Message:
    text: str
    buttons: list | None = None
    speech: str | None = None


def _days(count):
    return "1 día" if count == 1 else f"{count} días"


def _rescued(count):
    return "1 tupper rescatado" if count == 1 else f"{count} tuppers rescatados"


def _stored(data):
    food, days = data["food"], data["days"]
    text = f"✅ Anotado: {food}. Dura aproximadamente {_days(days)} en el refri. Yo te aviso."
    speech = f"Anotado: {food}. Dura aproximadamente {_days(days)} en el refri. Yo te aviso."
    if data.get("default_used"):
        extra = " No tengo ese alimento en mi tabla, así que usé un tiempo corto por seguridad."
        text += extra
        speech += extra
    return Message(text, speech=speech)


def _reminder(data):
    sentence = f"Tu {data['food']} vence mañana. Aprovéchalo hoy."
    return Message(f"⏰ {sentence}", speech=sentence)


def _ask(data):
    sentence = f"Tu {data['food']} ya llegó a su fecha límite. ¿Qué pasó con él?"
    tupper_id = data["tupper_id"]
    buttons = [
        [{"text": "😋 Ya me lo comí", "callback_data": f"{BUTTON_EATEN}|{tupper_id}"}],
        [{"text": "🗑️ Lo tiré", "callback_data": f"{BUTTON_TOSSED}|{tupper_id}"}],
        [{"text": "🧊 Sigue ahí", "callback_data": f"{BUTTON_SNOOZE}|{tupper_id}"}],
    ]
    return Message(f"⚠️ {sentence}", buttons=buttons, speech=sentence)


def _resolved(data):
    action = data.get("action")
    if action == "eaten":
        return Message(f"¡Muy bien! Llevas {_rescued(data.get('rescued_count', 0))}. 🎉")
    if action == "tossed":
        return Message("Entendido, lo quité de tu lista.")
    if action == "snooze":
        return Message(
            "De acuerdo, te vuelvo a preguntar mañana. Si huele o se ve raro, mejor tíralo."
        )
    return Message("Ese tupper ya estaba resuelto.")


def _consumed(data):
    if data.get("found"):
        return Message(
            f"¡Buen provecho! Quité {data['food']} de tu lista. "
            f"Llevas {_rescued(data.get('rescued_count', 0))}."
        )
    return Message("No encontré ese alimento en tu lista. Escribe «qué tengo» para verla.")


def _list(data):
    items = data.get("items") or []
    if not items:
        text = "No tienes tuppers registrados."
    else:
        lines = ["🧊 En tu refri:"]
        for item in items:
            left = item["days_left"]
            when = "ya venció" if left <= 0 else f"vence en {_days(left)}"
            lines.append(f"• {item['food']}: {when}")
        text = "\n".join(lines)
    rescued = data.get("rescued_count", 0)
    if rescued:
        text += f"\n\nLlevas {_rescued(rescued)}."
    return Message(text)


def _invite(data):
    username = os.environ.get("BOT_USERNAME", "")
    link = f"https://t.me/{username}?start=care_{data['code']}"
    return Message(
        "Comparte este enlace con tu familiar. Cuando lo abra, le avisaré si dejas "
        f"un alimento vencido sin atender:\n{link}"
    )


def _link(data):
    if data.get("linked"):
        name = data.get("owner_name") or "tu familiar"
        return Message(f"Listo. Te avisaré si {name} deja un alimento vencido sin atender.")
    return Message("Ese enlace ya no es válido. Pide uno nuevo con /familiar.")


def _expired(data):
    return Message(f"Marqué tu {data['food']} como vencido. Si sigue en el refri, mejor tíralo.")


def _escalation(data):
    name = data.get("owner_name") or "Tu familiar"
    return Message(
        f"👋 {name} tiene {data['food']} vencido en el refri y no lo ha atendido. "
        "Quizá convenga llamarle."
    )


_BUILDERS = {
    "stored": _stored,
    "reminder": _reminder,
    "ask": _ask,
    "resolved": _resolved,
    "consumed": _consumed,
    "list": _list,
    "invite": _invite,
    "link": _link,
    "expired": _expired,
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
