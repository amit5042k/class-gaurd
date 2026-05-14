#!/usr/bin/env bash
set -e
echo "=== ClassGuard Installation ==="

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

# Optional: full AI stack (face recognition needs cmake + dlib build tools)
read -p "Install AI features (face recognition + DeepFace)? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
    pip install cmake
    pip install dlib
    pip install face-recognition deepface scipy
fi

echo ""
echo "Installation complete. Run: python3 main.py"
