from setuptools import setup, Extension
import numpy as _np
from pathlib import Path

root = Path(__file__).parent

evaluator_sources = [
    str(root / "Dyn-WNTR" / "mwntr" / "sim" / "aml" / "evaluator_wrap.cpp"),
    str(root / "Dyn-WNTR" / "mwntr" / "sim" / "aml" / "evaluator.cpp"),
]

network_isolation_sources = [
    str(root / "Dyn-WNTR" / "mwntr" / "sim" / "network_isolation" / "network_isolation_wrap.cpp"),
    str(root / "Dyn-WNTR" / "mwntr" / "sim" / "network_isolation" / "network_isolation.cpp"),
]

ext_modules = [
    Extension(
        name="mwntr.sim.aml._evaluator",
        sources=evaluator_sources,
        include_dirs=[_np.get_include()],
        language="c++",
    ),
    Extension(
        name="mwntr.sim.network_isolation._network_isolation",
        sources=network_isolation_sources,
        include_dirs=[_np.get_include()],
        language="c++",
    ),
]

setup(
    name="mwntr_native",
    version="0.1",
    package_dir={"mwntr": "Dyn-WNTR/mwntr"},
    ext_modules=ext_modules,
)
