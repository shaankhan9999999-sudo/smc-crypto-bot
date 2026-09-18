name: BTC Swing Bot Cron

on:
  schedule:
    - cron: '0 * * * *' # Har 1 ghante me run hoga
  workflow_dispatch: # Manual run ke liye

jobs:
  run-swing-bot:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v2

      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.10'

      - name: Install Dependencies
        run: |
          pip install pandas requests

      - name: Run Swing Bot Script
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          python swing_bot.py
