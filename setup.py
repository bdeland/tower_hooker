from setuptools import setup, find_packages

setup(
    name="tower_hooker",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "frida",
        "structlog",
        "duckdb",
        "psycopg2-binary",
        "questdb",
        "python-dotenv",
    ],
) 