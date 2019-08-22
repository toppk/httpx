#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
from pathlib import Path

from setuptools import setup


def get_version(package):
    """
    Return package version as listed in `__version__` in `init.py`.
    """
    version = Path(package, "__version__.py").read_text()
    return re.search("__version__ = ['\"]([^'\"]+)['\"]", version).group(1)


def get_long_description():
    """
    Return the README.
    """
    long_description = ""
    with open("README.md", encoding="utf8") as f:
        long_description += f.read()
    long_description += "\n\n"
    with open("CHANGELOG.md", encoding="utf8") as f:
        long_description += f.read()
    return long_description


def get_packages(package):
    """
    Return root package and all sub-packages.
    """
    return [str(path.parent) for path in Path(package).glob("**/__init__.py")]


setup(
    name="httpx",
    python_requires=">=3.6",
    version=get_version("httpx"),
    url="https://github.com/encode/httpx",
    license="BSD",
    description="The next generation HTTP client.",
    long_description=get_long_description(),
    long_description_content_type="text/markdown",
    author="Tom Christie",
    author_email="tom@tomchristie.com",
    package_data={"httpx": ["py.typed"]},
    packages=get_packages("httpx"),
    include_package_data=True,
    install_requires=[
        "certifi",
        "chardet==3.*",
        "h11==0.8.*",
        "h2==3.*",
        "hstspreload",
        "idna==2.*",
        "rfc3986==1.*",
    ],
    extras_require={"trio": ["trio"]},
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License",
        "Operating System :: OS Independent",
        "Topic :: Internet :: WWW/HTTP",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
    ],
)
