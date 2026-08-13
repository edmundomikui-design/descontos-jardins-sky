from setuptools import setup, find_packages

setup(
    name="descontos-jardins-sky",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "flask",
        "flask-cors",
        "python-dotenv",
        "pillow",
    ],
    python_requires=">=3.8",
)
