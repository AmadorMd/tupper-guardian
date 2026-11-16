# Tupper Guardian — demo video timeline (HyperFrames)

Requirement: a public video (YouTube or Vimeo), **under 3 minutes**, linked in
the submission's "demo" field, **showing the build running end to end**.
Every claim in the narration is shown working on screen.

- Total: **2:52** (172 s), 1920×1080, 30 fps.
- Narration in English. The bot speaks Spanish, so every bot message on screen
  gets an English subtitle.
- Recorded in demo mode (1 "day" = 20 s, an unanswered question escalates after
  45 s). Scene 4 says so on screen.

## Tracks

| Track | Content |
|---|---|
| 0 | Video clips: kitchen shots and screen recordings (`assets/*.mp4`) |
| 1 | Overlays: titles, labels, English subtitles for bot messages |
| 2 | Narration (`assets/narration/s01.mp3` … one file per scene) |
| 3 | Background music at low volume (optional) |

## Assets to record

| File | What | How |
|---|---|---|
| `kitchen-cheese.mp4` | Hand takes the slice of cheese in a plain bag out of the fridge; close-up with no label or date | Phone, vertical framing avoided: shoot landscape |
| `granel.mp4` | Ham or cheese weighed at a counter, or a tomato with the bad part cut off | Phone, landscape |
| `phone-store-text.mp4` | Telegram: "Guardé queso panela en el refri" → confirmation + voice note playing | Phone screen recording |
| `phone-store-photo.mp4` | Telegram: photo of a leftovers container → confirmation | Phone screen recording |
| `phone-list.mp4` | Telegram: shopping list → confirmation with days + 🥫 line → date question → photo of a can's printed date → "✅ Anoté…" | Phone screen recording |
| `phone-question.mp4` | Telegram: "⚠️ Llegó la fecha límite de «…». ¿Qué pasó?" → tap "😋 Ya me lo comí" → "¡Buen provecho!" | Phone screen recording, **same moment** as the next one |
| `console-question.mp4` | AWS console, Step Functions execution graph paused on **AskWhatHappened**, then moving on after the tap | Mac screen recording (⌘⇧5), browser zoomed so states are readable |
| `phone-family.mp4` | Second phone: "👋 … tiene algo vencido en el refri y no ha respondido: …" → `/refri` with expired items first and "🔎 Revisar vencidos" | Second phone screen recording |
| `console-map.mp4` | Executions list showing one `tg-<update>-<id>` child per shopping-list food | Mac screen recording |
| `architecture.png` | Figure 1 from `docs/builder-center-writeup.html` (Download PNG) | Export |
| `kitchen-close.mp4` | The cheese goes into the trash; phone shows the fridge list | Phone, landscape |

Hide personal data before recording: Telegram shows account names, and the
console shows the AWS account id in the top bar.

## Scenes

| # | Start | Dur. | Track 0 (clip) | Track 1 (overlay) | Narration (track 2) |
|---|---|---|---|---|---|
| 1 | 0:00 | 8 s | `kitchen-cheese.mp4` (open fridge, hand takes the bag) | — | "One morning, my mother and my sister found this in our fridge." |
| 2 | 0:08 | 12 s | `kitchen-cheese.mp4` (close-up, no label, no date) | "No label. No date." | "A slice of cheese in a plain bag, with what looked like mold. Nobody knew how long it had been there, and I realized we'd had this exact conversation before." |
| 3 | 0:20 | 18 s | `granel.mp4` | "In Mexico, a lot of food is sold by weight — with no expiry date." | "In Mexico, a lot of food is bought by weight and handed over in a plastic bag, with no date. And many people, like my parents, grew up with little to spare: if it doesn't smell bad, they eat it. This isn't about saving money. It's about health." |
| 4 | 0:38 | 8 s | Title card (solid background) | **Tupper Guardian** · "A family fridge that remembers for you" · small: "Demo mode: 1 day = 20 seconds" | "So I built Tupper Guardian. No app to install. Just a chat." |
| 5 | 0:46 | 16 s | `phone-store-text.mp4` | Subtitle: "Got it: queso panela. Lasts about 5 days in the fridge. I'll let you know." | "You tell it what you stored, in your own words. It answers in text and in voice, for people who'd rather listen than read." |
| 6 | 1:02 | 14 s | `phone-store-photo.mp4` | Label: "Amazon Bedrock: understands · USDA FoodKeeper: decides" | "Or just send a photo. Amazon Bedrock recognizes the food, but it never decides how long it's safe. That comes from USDA food-safety data." |
| 7 | 1:16 | 24 s | `phone-list.mp4` | Subtitles for the list confirmation, "No refrigeration needed — check its expiry date", and the date question | "A whole shopping list works too: one message, every food tracked. Food that doesn't go in the fridge has a printed date, so the bot asks for it, and you can simply take a picture of the label." |
| 8 | 1:40 | 28 s | Split screen: `phone-question.mp4` left, `console-question.mp4` right | Label on the console: "Paused on a task token — waiting for a human" → after the tap: "Resumed" | "When food reaches its date, the bot asks what happened. Behind that question is an AWS Step Functions execution, paused on a task token. It costs nothing while it waits for a person. One tap, and the same execution wakes up and finishes the job." |
| 9 | 2:08 | 15 s | `phone-family.mp4` | Subtitle: "Mamá has something expired in the fridge and hasn't answered" | "And if nobody answers, the food doesn't vanish from the list. It stays flagged, and the rest of the family is told, so anyone can help check the fridge." |
| 10 | 2:23 | 20 s | `architecture.png` (slow zoom), then `console-map.mp4` | Service labels appear as they are named | "It's 100% serverless, with no pipeline. API Gateway starts Step Functions directly. Three small Lambda functions handle language, data and messages. DynamoDB is the source of truth, and Amazon Polly gives it a voice. When nobody uses it, nothing runs." |
| 11 | 2:43 | 9 s | `kitchen-close.mp4` | End card: "github.com/AmadorMd/tupper-guardian" · "Telegram today, WhatsApp next" | "Food has a shelf life, often shorter than we think. Tupper Guardian helps families take care of each other." |

Narration pace: about 2.5 words per second. Each line above fits its scene.
If a recorded line runs long, extend that scene and trim the waits in the
screen recordings; keep the total under 3:00.

## Building it with HyperFrames

1. Install FFmpeg: `brew install ffmpeg`.
2. Install the skills (interactive picker, choose **Core Skills**):
   `npx skills add heygen-com/hyperframes`
3. Create the project next to the repo:
   `npx hyperframes init tupper-guardian-video` (in `~/Sites/personal`).
4. Put the recordings in `tupper-guardian-video/assets/`, named as above, and
   one narration file per scene in `assets/narration/`.
5. Compose each scene as a `class="clip"` element with `data-start`,
   `data-duration` and `data-track-index` from the table, and animate overlays
   with a paused GSAP timeline registered in `window.__timelines`.
6. Preview with `npx hyperframes preview`, render with `npx hyperframes render`.
7. Upload the MP4 to YouTube as **Public**, then paste the link in the
   submission's "demo" field and in `docs/builder-center-writeup.html`.
