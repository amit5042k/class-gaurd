from setuptools import setup, find_packages

setup(
    name="classguard",
    version="1.0.0",
    description="ClassGuard — Multi-brand Camera Management Desktop App",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "PyQt6>=6.4.0",
        "opencv-python>=4.7.0",
        "numpy>=1.24.0",
        "SQLAlchemy>=2.0.0",
        "onvif-zeep>=0.2.12",
        "requests>=2.28.0",
        "Pillow>=9.4.0",
        "python-dateutil>=2.8.2",
    ],
    extras_require={
        "ai": [
            "face-recognition>=1.3.0",
            "deepface>=0.0.79",
            "scipy>=1.10.0",
        ]
    },
    entry_points={
        "console_scripts": [
            "classguard=main:main",
        ]
    },
)
