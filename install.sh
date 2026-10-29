#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "=== ClassGuard installation ==="
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

read -r -p "Install face recognition (needed for attendance)? [Y/n] " ans
if [[ ! "$ans" =~ ^[Nn]$ ]]; then
    python -m pip install "setuptools<81" dlib-bin face_recognition_models Click Pillow
    python -m pip install --no-deps face_recognition
fi
read -r -p "Install emotion analysis (DeepFace, ~1 GB)? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
    python -m pip install deepface tf-keras
fi
echo
echo "Done. Start the app with: ./run.sh"
