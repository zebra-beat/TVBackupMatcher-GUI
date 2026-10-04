from setuptools import setup, find_packages

setup(
    name="tv-backup-matcher-gui",
    version="2.1.0",
    description="Graphical interface for automated video title card extraction, OCR, and episode matching.",
    author="Kyle Jeffrey",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "tv-matcher-gui=gui_desktop:main",
            "tv-matcher-web=app:main",
        ],
    },
    python_requires=">=3.8",
)
