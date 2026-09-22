"""Import a service's `main.py` under a name of its own.

Every service keeps its entry point in a module called `main`, so a plain
`import main` from two different test files puts whichever ran first into
`sys.modules["main"]` and hands the second one the wrong module. That made
the form4 and fda-catalysts suites pass or fail depending on collection
order. Loading under an alias removes the shared name.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SERVICES = Path(__file__).resolve().parent.parent

if str(SERVICES) not in sys.path:
    sys.path.insert(0, str(SERVICES))


def load_service_main(package: str, alias: str) -> ModuleType:
    """Load `services/<package>/main.py` as `alias`.

    The service directory goes on `sys.path` too, because a service may
    import a sibling of its own (form4_insider/form4_common.py).
    """
    directory = SERVICES / package
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

    if alias in sys.modules:
        return sys.modules[alias]

    spec = importlib.util.spec_from_file_location(alias, directory / "main.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {directory / 'main.py'}")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec_module so a module that imports itself, or that
    # fails partway, does not get loaded a second time under the same alias.
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module
