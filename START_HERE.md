# START HERE — getting CLICKMINT running

Written for someone who doesn't use the terminal much. No prior knowledge assumed.
If a step doesn't work, the last section tells you what to do.

---

## What you're about to do, in plain words

You have three bots. Right now they're just code sitting in a folder. To bring them to
life you need to do two things:

1. **Get three passwords from Telegram** (called *tokens*) and tell your computer your
   own Telegram id number.
2. **Leave the code running** on some computer that stays on.

That's it. There is no website to configure, no database to install, no money to pay.

**A word on the terminal.** The terminal is a window where you type a line and press
Enter. It looks intimidating but you only ever need a handful of lines here, and they're
all written out below — you can copy and paste them. You cannot break anything by typing
these; the worst that happens is an error message that tells you what went wrong.

- **Windows:** you need "Git Bash" (comes with [Git for Windows](https://git-scm.com/download/win))
  or WSL. Right-click inside the project folder → "Git Bash Here".
- **Mac:** press ⌘+Space, type `Terminal`, press Enter. Then type `cd ` (with a space),
  drag the project folder onto the window, press Enter.
- **Linux / Raspberry Pi:** open Terminal, then `cd` into the project folder.

---

## Part 1 — Collect four things from Telegram (5 minutes, no terminal)

This part happens entirely inside the Telegram app.

### The three tokens

1. Open Telegram and search for **@BotFather** (the one with the blue tick).
2. Send it `/newbot`.
3. It asks for a **name** — this is what people see. Type: `CLICKMINT Reward`
4. It asks for a **username** — must end in `bot` and be unused, e.g. `ClickMintRewardBot`.
5. It replies with a message containing a long line like:
   `8123456789:AAF9xQ2mKp7vLzR3tYuIoP1aSdFgHjKlZxC`
   **That is the token.** Copy the whole line.
6. **Repeat twice more**, for `CLICKMINT Partnership` and `CLICKMINT Admin`.

You should end up with **three different tokens**. Keep them somewhere private for a
moment — a token is the bot's password. Anyone who has it controls that bot.

### Your own id number

7. Search for **@userinfobot** in Telegram and send it anything.
8. It replies with `Id: 123456789`. Copy that number.

That number is how the bots recognise you as the owner: you post for free, no daily
limit, and only you can invite admins.

---

## Part 2 — Set it up (one command)

Open the terminal in the project folder (see above) and type this, then press Enter:

```bash
bash setup.sh
```

It will ask you four questions, one at a time:

```
Question 1 of 4 — the REWARD bot's token
  Paste the token here: ▌
```

Paste the matching token, press Enter, repeat. Then it asks for your id number. If you
paste something wrong it tells you and asks again — you can't get it into a broken state.

**Pasting into a terminal:** Ctrl+Shift+V on Windows/Linux, ⌘+V on Mac. Right-click also
works in most terminals. You won't see the text highlighted — that's normal.

When it finishes you'll see:

```
✓ REWARD_BOT_TOKEN → @ClickMintRewardBot (CLICKMINT Reward) — reward_bot.py
✓ PARTNER_BOT_TOKEN → @ClickMintPartnerBot (CLICKMINT Partnership) — partnership_bot.py
✓ ADMIN_BOT_TOKEN → @ClickMintAdminBot (CLICKMINT Admin) — admin_bot.py
You're ready.
```

That last block means Telegram itself confirmed all three tokens are real and working.

Your answers are saved in a file called `.env`. It never leaves your machine and it's
excluded from GitHub, so your tokens can't be published by accident.

---

## Part 3 — Turn the bots on

```bash
bash run_bots.sh start
```

```
✓ reward   running (pid 4302) → logs/reward.log
✓ partner  running (pid 4309) → logs/partner.log
✓ admin    running (pid 4316) → logs/admin.log
```

Four commands, that's the whole set:

| Type this | What happens |
|---|---|
| `bash run_bots.sh start` | Turns all three bots on |
| `bash run_bots.sh status` | Tells you whether they're on |
| `bash run_bots.sh logs` | Shows what they're doing, live. **Ctrl+C stops watching, not the bots** |
| `bash run_bots.sh stop` | Turns them off |

### Check it worked

In Telegram, open the **admin** bot you created and send `/start`. You should see:

```
🛠 CLICKMINT ADMIN DASHBOARD
  • Registered channels: 0
  ...
```

If instead you see *"🔒 This is the owner's admin panel"*, the id number from step 8
doesn't match the account you're messaging from. Run `bash setup.sh` again and re-enter it.

---

## Part 4 — One thing the code cannot do for you

**A bot can only post into a channel where it has been made an admin.** That's a Telegram
rule and there's no way around it. For each channel:

Channel → Administrators → Add Administrator → search your bot → enable **Post Messages**.

Until you do that, the bot can offer posts to members but cannot deliver into a channel.

---

## Part 5 — Keeping it on

While the terminal window is open and your computer is awake, the bots are alive. Close
the laptop and they stop.

For always-on, you have two free options:

- **A Raspberry Pi or an old laptop** left plugged in at home. Same commands as above.
- **Oracle Cloud Always Free** — a real server that never sleeps, free forever.
  See `DEPLOY_FREE.md`, which also explains why Render/Vercel *won't* work for this.

On a proper Linux server you'd use the systemd services instead of `run_bots.sh`, so the
bots restart automatically after a reboot or a crash. `DEPLOY_FREE.md` has that too, and
`bash deploy.sh <your-repo-url>` does the whole thing in one go.

---

## If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `bash: setup.sh: No such file or directory` | You're not in the project folder | `cd` into the folder (drag it onto the terminal after typing `cd `) |
| `Python 3 is not installed` | Missing Python | Ubuntu/Pi: `sudo apt install python3 python3-pip` · Mac: `brew install python3` · Windows: [python.org](https://www.python.org/downloads/) |
| `✗ ... was REJECTED by Telegram` | Wrong or revoked token | In @BotFather: `/token`, pick the bot, copy the new one, run `bash setup.sh` again |
| `✗ ... could not reach Telegram` | No internet on that machine, or it's blocked | Check the connection and try again |
| `✗ ADMIN_BOT_TOKEN is the SAME token as ...` | You pasted one token twice | Each bot needs its own — re-run `bash setup.sh` |
| `did NOT stay running` | The bot started then quit | The reason is printed underneath, and the full text is in `logs/<name>.log` |
| Bot doesn't reply at all | Another copy is already running with the same token | `bash run_bots.sh stop`, then `start` |
| You get the member menu, not the owner one | `OWNER_USER_ID` doesn't match your account | Re-check with @userinfobot, re-run `bash setup.sh` |

**Want to see the bots work before touching any of this?** You can, with no tokens and no
internet:

```bash
python3 simulate.py
```

It plays a full 19-step session — members registering, posts circulating, a scam being
refused, a report being judged — and prints every screen. The saved transcript is in
`docs/DRY_RUN.md`.
