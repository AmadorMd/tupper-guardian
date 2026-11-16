# Tupper Guardian — demo video script

Requirement: a public video (YouTube or Vimeo), under 3 minutes, linked in the
"demo" field, **showing the build running end to end**. So every claim in the
narration is shown working: a real message in Telegram, the real execution in
the AWS console, and the real reply on the phone.

Target length: 2:45. Narration in English; the bot speaks Spanish, so add
English subtitles for the bot's messages and voice notes.

Deploy in demo mode before recording (one "day" = 20 s, unanswered question
escalates after 45 s), and say so on screen.

## Before recording

- Deployed with `SECONDS_PER_DAY=20 ASK_TIMEOUT_SECONDS=45`.
- Telegram in light mode, notifications on, chat cleared or a fresh test
  account. Phone screen recording on.
- AWS console open in a browser, signed in, region us-east-2:
  Step Functions → the `GuardianStateMachine-…` state machine → Executions.
- Props: a slice of cheese in a plain plastic bag (the real story), a container
  of leftovers, a can or a bag of rice with a printed expiry date, a shopping
  list or receipt.
- The architecture diagram exported as PNG from `docs/builder-center-writeup.html`.

## Shots

| Time | On screen | Narration |
|---|---|---|
| 0:00–0:20 | **Hook.** Kitchen, morning. A hand pulls a slice of cheese in a plain plastic bag out of the fridge. Close-up: no label, no date. | "One morning, my mother and my sister found this in our fridge: a slice of cheese in a plain bag, with what looked like mold. Nobody knew how long it had been there. And I realized we'd had this exact conversation before." |
| 0:20–0:40 | Market counter or a cutting board: ham and cheese weighed and bagged. Then a tomato with the bad part cut off. | "In Mexico, a lot of food is bought by weight. It comes in a plastic bag with no expiry date. And many people, like my parents, grew up with little to spare: if it doesn't smell bad, they eat it. This isn't about saving money. It's about health." |
| 0:40–0:50 | Title card: **Tupper Guardian** — "a family fridge that remembers for you". Text: "Demo mode: 1 day = 20 seconds". | "So I built Tupper Guardian. No app to install, no website. Just a chat." |
| 0:50–1:05 | **Text.** Phone: type "Guardé queso panela en el refri". Reply "✅ Anotado: queso panela. Dura aproximadamente 5 días…" plus the voice note playing (subtitle it). | "You tell it what you stored, in your own words. It answers in text and in voice, for people who'd rather listen than read." |
| 1:05–1:20 | **Photo.** Take a photo of the leftovers container and send it. Reply with the recognized food and its days. | "Or just send a photo. Amazon Bedrock recognizes the food, but it never decides how long it's safe: that comes from USDA food-safety data." |
| 1:20–1:40 | **Shopping list.** Send "Lista del súper: leche, huevos, fresas, jitomate, arroz, atún en lata". Reply: one line per food with its days, "🥫 No necesitan refrigeración, consulta su fecha de caducidad: arroz, atún en lata", then "📅 ¿Cuándo caduca tu arroz?". Send a **photo of the printed date** on the package. Reply "✅ Anoté arroz con caducidad…". | "A whole shopping list works too. Food that doesn't go in the fridge has a printed date, so the bot asks for it — and you can just take a picture of it." |
| 1:40–2:05 | **The key moment.** Split screen: phone on one side, the Step Functions execution graph on the other, paused on **AskWhatHappened** (blue, "waiting"). The phone receives "⚠️ Tu fresas ya llegó a su fecha límite. ¿Qué pasó con él?" with buttons. Tap **"😋 Ya me lo comí"**. The graph moves on to the next state and the phone shows the confirmation. | "When food expires, it asks what happened. That question is a Step Functions execution paused on a task token. It costs nothing while it waits for a human. One tap, and the same execution wakes up and finishes." |
| 2:05–2:20 | **Nothing disappears by silence.** Don't answer a question for 45 s (cut the wait). The bot says it stays in the fridge for review and explains /familiar. Type `/refri`: expired items first, with the "🔎 Revisar vencidos" button. | "If nobody answers, the food doesn't vanish from the list. It stays flagged, the family is told, and anyone can review it. (If you have a second phone: show the relative receiving the alert here.)" |
| 2:20–2:40 | **Architecture.** The diagram (Figure 1), then a quick pan of the console: the executions list showing one child execution per shopping-list food (`tg-…-…`). | "It's 100% serverless, with no pipeline: API Gateway starts Step Functions directly, three small Lambdas handle language, data and messages, DynamoDB is the source of truth, and Polly gives it a voice. When nobody uses it, nothing runs." |
| 2:40–2:50 | Back in the kitchen: the cheese goes in the trash; the phone shows the fridge list. | "Food has a shelf life, often shorter than we think. Tupper Guardian helps families take care of each other. Telegram today, WhatsApp next." |

## Recording tips

- Record the phone and the console separately and edit them side by side; the
  20-second "day" makes the waits short, and you can cut them.
- For the key moment, store the food first, open its execution in the console
  (Executions → the newest `tg-…` running), and wait on the graph until the
  question arrives.
- Keep personal names and chat ids out of the frame. Telegram shows your
  account name at the top of the chat.
- Subtitle every bot message in English; the jury may not speak Spanish.
- Upload as **Public** (or Unlisted if the rules allow it; the requirement says
  public) and paste the link in the "demo" field and in the write-up.
