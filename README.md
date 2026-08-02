# 9adam bot

## Run locally

1. Create and activate a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Start the bot with `python 9adam.py`.

## Deploy to a 24/7 host

- Use a platform such as Railway, Render, or Heroku.
- Set the following environment variables:
  - DISCORD_TOKEN
  - OPENAI_API_KEY
- The included Procfile starts the bot as a worker process.
