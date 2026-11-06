from setuptools import setup, find_packages

setup(
    name="classguard",
    version="2.2.0",
    description="ClassGuard — Multi-brand Camera Management Desktop App",
    packages=find_packages(exclude=["tests"]),
    py_modules=["main"],
    python_requires=">=3.9",
    install_requires=[
        "PyQt6>=6.5",
        "opencv-python-headless>=4.8,<5",
        "numpy>=1.24.0",
        "SQLAlchemy>=2.0.0",
        "onvif-zeep>=0.2.12",
        "WSDiscovery>=2.0",
        "requests>=2.28.0",
        "cryptography>=41",
        "av>=12",
    ],
    extras_require={
        "ai": [
            "setuptools<81",
            "dlib-bin>=19.24",
            "face_recognition_models>=0.3.0",
            "deepface>=0.0.89",
            "tf-keras",
        ]
    },
    entry_points={
        "console_scripts": [
            "classguard=main:main",
        ]
    },
)
