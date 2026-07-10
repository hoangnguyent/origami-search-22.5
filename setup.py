from pathlib import Path

from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext

ROOT = Path(__file__).resolve().parent

ext_modules = [
    Pybind11Extension(
        name="math225_core",
        sources=[str(ROOT / "src/core/math225_core.cpp")],
        cxx_std=17,
        include_dirs=[str(ROOT / "src/core")],
    ),
]

setup(
    name="search22_5",
    version="0.1.0",
    cmdclass={"build_ext": build_ext},
    ext_modules=ext_modules,
)
