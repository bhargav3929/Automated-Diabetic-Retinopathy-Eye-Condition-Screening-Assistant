# RetinaGuard - Diabetic Retinopathy & Eye Condition Screening Assistant

RetinaGuard looks at a retinal (fundus) photo or an outside-of-the-eye photo, finds the visible signs of disease, grades the risk (ICDR stage 0-4 for retina photos), suggests how urgently the patient should see a doctor, and writes a referral letter. It is made for students, clinicians and anyone who wants to see how AI can help with eye screening.

> This is a preliminary AI screening aid, **not a medical diagnosis**. A real eye exam is always needed.

## What you need

- **Python 3.10 or newer** - https://www.python.org/downloads/ (on Windows, tick "Add python.exe to PATH").
- **A free Groq key** - https://console.groq.com/keys (about 1 minute, no card).
- An internet connection (for installing and for the AI).

## Run it (one command)

- **macOS / Linux:** `chmod +x start.sh && ./start.sh`
- **Windows:** double-click `start.bat`

The first run takes a minute while it installs. Then your browser opens at **http://127.0.0.1:8002**.

## Add your Groq key

Click **API Keys** in the app and paste your key (it starts with `gsk_`). Or open the `.env` file that the start script created and set `GROQ_API_KEY=gsk_...`.

## How to use it

1. Click one of the **sample** images. A saved result from a real earlier run appears straight away.
2. Click **Run Ocular Triage Pipeline** to analyse the same image live. You will see the 5 steps tick off.
3. Now try your own photo: drop a JPG, PNG or WEBP file, optionally fill in age, years with diabetes, HbA1c and symptoms, and run it.
4. Look at the **Workbench**, **Compare** (two screenings side by side), **Referral** (copy or download the letter) and **History** (past screenings).

Fair-use limit: 15 full screenings per hour. A scan that is rejected or fails does not use one.

## How it works

Photo -> **gatekeeper** (is it an eye? which type?) -> **one AI call** that finds lesions, grades the risk and suggests triage -> **safety rule** (urgency never lower than the medical minimum for that grade) -> **report** saved to a local database.

The AI is Groq (`qwen/qwen3.8-27b` for images). If a model is busy the app waits and retries, then switches to the backup model. The steps are in `app/pipeline.py`, and all AI calls are in `app/providers.py`.

## Project structure

```
.env.example        settings template (copied to .env on first run)
requirements.txt    pinned Python packages
start.sh / start.bat   one-command start
run.py              alternative start: python run.py
app/
  main.py           web server and routes
  config.py         reads settings from .env
  providers.py      Groq calls with retries and fallback
  pipeline.py       the 5-step screening pipeline
  db.py             local SQLite: history, saved key, hourly limit
  samples.py        sample images
  errors.py, demo_auth.py   error type and request checks
static/             the web page (index.html, app.js, app.css)
samples/            sample photos; samples/results/ holds their saved results
```

## Troubleshooting

- **Port already in use:** another program is using 8002. Close it, or change `PORT=` in `.env` and run the start script again.
- **"The AI service is busy":** the free Groq tier is overloaded or you hit its per-minute limit. Wait a minute and try again.
- **"Your Groq key was rejected":** open **API Keys** and paste a fresh key from https://console.groq.com/keys.
- **Python not found:** install Python 3.10+ from https://www.python.org/downloads/ and run the start script again.
- **Install failed:** check your internet connection and run the start script again; it continues where it stopped.
