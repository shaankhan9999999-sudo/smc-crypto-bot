name: Coinbase SMC Scalping Bot

on:
  push:
    branches: [ "main", "master" ]
  workflow_dispatch:

jobs:
  run-bot:
    runs-on: ubuntu-latest

    steps:
    - name: Checkout Code
      uses: actions/checkout@v3

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.10'

    - name: Install Dependencies Directly
      run: |
        python -m pip install --upgrade pip
        pip install ccxt pandas requests numpy

    - name: Run Scalping Bot
      run: |
        python bot.py
