#!/bin/bash
# Oracle Cloud VM (Ubuntu) ustida botni doimiy ishlaydigan qilib sozlash skripti.
# Ishga tushirishdan oldin: bot.py, requirements.txt va zakazbot.service fayllarini
# shu VM ichidagi ~/BOT papkasiga joylashtiring (scp yoki git clone orqali).
set -e

echo "=== 1/4: Tizim paketlari yangilanmoqda ==="
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

echo "=== 2/4: Python virtual environment va kutubxonalar ==="
cd ~/BOT
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo "=== 3/4: systemd xizmati (service) o'rnatilmoqda ==="
sudo cp ~/BOT/zakazbot.service /etc/systemd/system/zakazbot.service
sudo systemctl daemon-reload
sudo systemctl enable zakazbot

echo "=== 4/4: Bot ishga tushirilmoqda ==="
sudo systemctl restart zakazbot

echo ""
echo "=== TAYYOR! ==="
echo "Holatni tekshirish:   sudo systemctl status zakazbot"
echo "Loglarni ko'rish:     journalctl -u zakazbot -f"
echo "Botni qayta ishga tushirish: sudo systemctl restart zakazbot"
