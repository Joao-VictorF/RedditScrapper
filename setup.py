from setuptools import find_packages, setup

setup(
    name="reddit-scrapper",
    version="0.1.0",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    py_modules=["main"],
    install_requires=["requests"],
    entry_points={
        "console_scripts": [
            "reddit-scrapper=main:main",
        ]
    },
)
