# This little module catalogues the (optional) dependencies of the pycavy
# package. Its `require` decorator is also used to lazy-load them at runtime,
# since some of these packages take several seconds to load.
#
# It's originally derived from the `cavy-python` code that made up the prototype
# implementation of Cavy, and now serves as part of the Python interface to the
# compiler.

import importlib
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import Any, Callable, Optional


class DependencyKind(Enum):
    """The sorts of things that might be
    """
    PYTHON_PKG = 'python package'
    UNSATISFIABLE = 'unsatisfiable'


@dataclass
class DependencySpec():
    """A simple wrapper around the pieces of data required to describe an optional
    dependency.
    """
    name: str
    kind: DependencyKind
    url: Optional[str]
    desc: str


# The following specs are all the dependencies used anywhere in the PyCavy system.
DEPENDENCIES = {
    'cirq': DependencySpec(
        name='cirq',
        kind=DependencyKind.PYTHON_PKG,
        url='https://cirq.readthedocs.io/en/stable/',
        desc="""A quantum circuits package"""
    ),
    'qiskit': DependencySpec(
        name='qiskit',
        kind=DependencyKind.PYTHON_PKG,
        url='https://qiskit.org/',
        desc="""A quantum simulation and circuits package"""
    ),
    'pylatex': DependencySpec(
        name='pylatex',
        kind=DependencyKind.PYTHON_PKG,
        url='https://jeltef.github.io/PyLaTeX/current/',
        desc="""A package for drawing LaTeX diagrams for python"""
    ),
    'labber': DependencySpec(
        # The capitalization is intentional!
        name='Labber',
        kind=DependencyKind.PYTHON_PKG,
        url='http://labber.org/online-doc/api/index.html',
        desc="""The Python API for the Labber automation toolkit"""
    ),
    'numpy': DependencySpec(
        # The capitalization is intentional!
        name='numpy',
        kind=DependencyKind.PYTHON_PKG,
        url='http://numpy.org',
        desc="""Python's de facto official numerical package"""
    ),
    '__unsatisfiable__': DependencySpec(
        name='unsatisfiable',
        kind=DependencyKind.UNSATISFIABLE,
        url=None,
        desc="""A dependency that always fails to load"""
    ),
}

LOADED_DEPENDENCIES = set()


class MissingDependencyError(Exception):
    def __init__(self, dep):
        assert dep in DEPENDENCIES
        self.dep = dep
        self.spec = DEPENDENCIES[dep]

    def __str__(self):
        fmt = """Error: this feature requires the missing dependency '{}'.
Please install the '{}' {} [{}]"""
        return fmt.format(self.dep, self.spec.name, self.spec.kind.value, self.spec.url)


def load_dependency(dep: str):
    """If a dependency is a Python package, try to load it. There might be some
    other action to be taken for other kinds of dependencies, so we'll call this
    function unconditionally for now; it will only do anything, though, for
    Python packages.
    """
    if DEPENDENCIES[dep].kind == DependencyKind.PYTHON_PKG:
        try:
            # TODO A clean namespace should be used for this, rather than one
            # that includes all kinds of clutter like `dependency_version` and
            # so on. It might be right to separate the management code in this
            # file from a module that contains the actual (loaded) dependencies.
            globals()[dep] = importlib.import_module(dep)
            LOADED_DEPENDENCIES.add(dep)
        except ModuleNotFoundError as e:
            raise e


def dependency_version(dep: str) -> str:
    """Try to get the version of a loaded dependency"""
    if dep not in LOADED_DEPENDENCIES:
        return "Not loaded"
    mod = globals()[dep]
    return getattr(mod, '__version__', 'No version found')


def require(*deps: str) -> Callable:
    """Annotation for a function that requires one or more dependencies. These
    dependencies are lazy-loaded on first use.
    """
    def require_dep(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapped(*args, **kwargs) -> Any:
            for dep in deps:
                if dep not in LOADED_DEPENDENCIES:
                    try:
                        load_dependency(dep)
                    except ModuleNotFoundError:
                        raise MissingDependencyError(dep)
            return fn(*args, **kwargs)
        return wrapped
    return require_dep
