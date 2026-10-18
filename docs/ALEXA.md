# FireDev on Alexa

Say *"Alexa, ask fire dev why the build is failing"* into the Fire TV remote (or any Alexa device on your
account). Alexa answers in a sentence; the TV shows the detail. The skill is a custom skill hosted by FireDev
itself at `https://<your-host>/firedev/alexa`. It stays in the **Development** stage: it never has to be
certified or published to work on devices registered to your Amazon developer account.

## How it works

- **Who's calling.** Every request must be signed by Alexa. FireDev checks the certificate URL
  (`https://s3.amazonaws.com/echo.api/…`, port 443), the certificate (valid now, named `echo-api.amazon.com`,
  chained to a trusted root), the `Signature-256` header against the exact body, a timestamp within 150 seconds,
  and that the request is for *your* skill ID. Anything else gets `400 Bad Request` and is never read.
- **Linking.** The first time an Alexa account talks to FireDev, Alexa reads out a 4-digit code and the TV asks
  to link it. Hold OK on the remote for two seconds to approve. Until then Alexa can only say how to link.
  Unlink with `./firedev devices` and `./firedev unpair <id>`.
- **Long tasks.** Alexa allows about 8 seconds per answer; agent tasks take minutes. A task starts and Alexa
  answers at once ("Asking Codex. I'll show the progress on the TV…"). Ask *"how's it going"* or *"what was the
  result"* whenever you like. If something takes longer than 1.5 seconds (connecting to the machine), Alexa
  says "one moment" (a progressive response) and FireDev answers within 6.5 seconds either way; the work carries on.
- **Approvals stay on the TV.** Saying "approve" to Alexa never approves anything; it tells you to use the remote.

## What to say

| Say | What happens |
|---|---|
| Alexa, ask fire dev why the build is failing | The agent investigates; the TV shows its steps |
| Alexa, ask fire dev to fix the failing tests | A coding task (also: *to add …*, *to change …*, *to ask the agent to …*) |
| Alexa, ask fire dev to run the tests / build the app | Runs the project action |
| Alexa, ask fire dev how it's going | A one-sentence progress update |
| Alexa, ask fire dev what was the result | The agent's summary, files changed and tests passed |
| Alexa, ask fire dev to deploy the preview | Waits for your approval on the TV |
| Alexa, ask fire dev to stop | Stops the current task |
| Alexa, open fire dev | Status, then listens for one command |

On Fire TV, prefer the one-shot *"ask fire dev …"* form: after an answer the remote's microphone closes, so a
back-and-forth conversation means pressing the mic button again. If *"open fire dev"* opens the FireDev app
instead of the skill, say *"open fire dev skill"*.

## Set it up (Alexa developer console)

You need: the FireDev service reachable over HTTPS with a publicly trusted certificate (for example
`https://musastudios.co.uk/firedev`), and an Amazon developer account (developer.amazon.com).

1. Go to <https://developer.amazon.com/alexa/console/ask> and sign in with your developer account.
2. **Create Skill.**
   - Name: `FireDev`
   - Primary locale: your Alexa/Fire TV's language, e.g. **English (UK)** for a UK device. The skill only
     answers devices whose language matches one of its locales.
   - Experience: **Other** · Model: **Custom** · Hosting: **Provision your own**
   - Template: **Start from scratch** (or "Start from Scratch" / no template). Create.
3. **Build → Interaction Model → JSON Editor.** Replace everything with the contents of
   `alexa/interaction-model.json`. **Save**, then **Build skill**. Wait for "Build Successful".
4. **Build → Endpoint.**
   - Select **HTTPS**.
   - Default Region: `https://musastudios.co.uk/firedev/alexa`
   - SSL certificate type: **My development endpoint has a certificate from a trusted certificate authority**.
   - **Save**.
5. Copy the **Skill ID** (top of the Endpoint page, or the skill list: *Copy Skill ID*; it starts
   `amzn1.ask.skill.`). On the server, add it under `server:` in `~/.config/firedev/firedev.yaml`:
   ```yaml
   server:
     alexa_skill_id: amzn1.ask.skill.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```
   and restart: `sudo systemctl restart firedev`.
6. **Test** tab: set *Skill testing is enabled in* to **Development**. Type `ask fire dev how it's going`.
   The first answer is the link code; the TV (FireDev open and paired) shows the link sheet. Hold OK to approve.
   Type it again: you should hear the status.
7. **Your Fire TV**: Settings → Account & Profile Settings (or My Account) must show the **same Amazon account
   as your developer account**, and Settings → Preferences → Language must match the skill's locale. Then
   press the remote's microphone button and say *"Alexa, ask fire dev how it's going"*.

## What has been tested, and what hasn't

| | Status |
|---|---|
| Signature, certificate, timestamp and skill-ID checks | Unit-tested with a throwaway CA standing in for Amazon's (every rule, pass and fail); chain logic checked against Amazon's real `echo-api-cert-12.pem` chain; live endpoint refuses unsigned, forged and badly addressed requests with 400 |
| Linking on the TV, tasks, progress, result, approvals by voice refused | Browser test with signed requests over HTTP against a real FireDev service and TV interface |
| Answers within Alexa's time limit, progressive "one moment" | Unit-tested (slow work, request shape, only to Alexa's API hosts) |
| Interaction model | Every intent handled (test); not yet built in the Alexa console |
| **A real request from Alexa (console simulator or a device)** | **Not yet**: needs the console steps above |
| **The Fire TV remote's microphone** | **Not yet**: needs the console steps and your Fire TV |

The phone page's *Alexa (simulated)* button still works as a fallback: it sends the same requests with a
FireDev token instead of Alexa's signature.
