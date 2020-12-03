# This module provides the guts of the `pycavy` wrapper around the Cavy
# compiler. For the time being, the implementation is simple: it expects a
# `cavy` binary on the PATH, and writes object code to a temporary file.
#
# It's originally derived from the `cavy-python` code that made up the prototype
# implementation of Cavy, and now serves as part of the Python interface to the
# compiler.

import json
import os
import subprocess
import tempfile
from abc import ABC, abstractmethod
from typing import Any, Dict, Tuple, List

import pycavy.dependencies as deps

# Shell command to invoke the compiler
cavy_cmd = "cavy"


class CavyError(RuntimeError):
    """A general exception type for errors emitted by the Cavy compiler
    """
    pass


class Program:
    """An abstraction of a Cavy source file.
    """

    def __init__(self, src: str):
        self.src_code = src

    def compile(self, opt: int = 0):

        # Write the Cavy source to a tempfile.
        # TODO rewrite this with tempfile context managers
        (src_fd, src_path) = tempfile.mkstemp()
        with open(src_path, 'w') as src_file:
            src_file.write(self.src_code)

        # Compile the source, calling a `cavy` binary on the PATH
        (src_fd, obj_path) = tempfile.mkstemp()
        proc = subprocess.run(
            cavy_cmd.strip().split() +
            [src_path, "-o", obj_path, "-O", str(opt)],
            capture_output=True
        )

        if proc.returncode != 0:
            os.unlink(src_path)
            raise CavyError(proc.stderr.decode().strip())

        # Clean up the source file
        os.unlink(src_path)

        return ObjectFile(src_fd, obj_path)


class ObjectFile:
    """An abstraction of a compiled Cavy program.
    """

    def __init__(self, src_fd, obj_path):
        # Read the file, which for the time being is assumed to be OpenQASM.
        # The attribute __obj_code is an implementation detail that will likely
        # disappear when I (eventually) move to using a compiled Cavy library
        # distributed with the Python package
        with open(obj_path, 'r') as obj_file:
            obj_code = obj_file.read()
        # Clean up the source file
        os.unlink(obj_path)
        self.__bindings, self.__qasm = ObjectFile.__parse_asm(obj_code)
        self.__prints = None

    @classmethod
    def __parse_asm(cls, qasm: str) -> Tuple[Dict, str]:
        # The first line is expected to be a comment containing a bindings
        # dictionary.
        bindings, qasm = qasm.split(sep='\n', maxsplit=1)
        bindings = json.loads(bindings.lstrip('//'))
        return bindings, qasm

    def to_qasm(self) -> str:
        """Return the object code as a QASM string
        """
        return self.__obj_code

    @deps.require('qiskit')
    def to_qiskit(self) -> "qiskit.QuantumCircuit":
        """Return the object code as a Qiskit circuit
        """
        from_qasm = deps.qiskit.QuantumCircuit.from_qasm_str
        circuit = from_qasm(self.__qasm)
        return QiskitRunnable(circuit, self.__bindings, self.__prints)

    @deps.require('cirq')
    def to_cirq(self) -> "cirq.Circuit":
        """Return the object code as a Cirq circuit
        """
        cirq = deps.cirq
        from cirq.contrib.qasm_import import circuit_from_qasm
        circuit = circuit_from_qasm(self.__qasm)
        return CirqRunnable(circuit, self.__bindings, self.__prints)

    @deps.require('labber')
    def to_labber(self):
        """Transform this circuit to a Labber circuit that can be run
        on a physical machine.
        TODO implement this!
        """
        raise NotImplementedError

    @deps.require('cirq')
    def to_diagram(self) -> str:
        """Returns latex source for a circuit diagram
        """
        to_latex = deps.cirq.contrib.qcircuit.circuit_to_latex_using_qcircuit
        circuit = self.to_cirq().circuit
        circuit_latex = to_latex(circuit, circuit.all_qubits())
        return Diagram(circuit_latex)


class Runnable(ABC):

    @abstractmethod
    def run(self) -> Dict[str, Any]:
        pass


class CirqRunnable(Runnable):

    def __init__(self, circuit, bindings, prints):
        opt = 0
        if opt > 0:
            self.circuit = deps.cirq.google.optimized_for_xmon(circuit)
        else:
            self.circuit = circuit
        self.bindings = bindings
        self.prints = prints

    def sample_circuit(self):
        def meas_index(measurements, i: int):
            return measurements.get(str((i, 0))).transpose()[0]
        results = deps.cirq.sample(self.circuit,
                                   dtype=bool,
                                   repetitions=1)
        # Convert the pandas series to a dictionary.
        # For now we're only doing a single repetition, hence the zero index.
        values = results.data.values[0]
        names = [int(name.split('_')[1]) for name in results.data]
        measurements = dict(zip(names, values))
        return measurements

    def run(self) -> Dict[str, Any]:
        measurements = self.sample_circuit()
        des = Deserializer(measurements)
        # Each value in the bindings dictionary is assumed to be a map from a
        # type string to its data.
        return {name: des.deserialize(value)
                for name, value in self.bindings.items()}


class QiskitRunnable(Runnable):

    def __init__(self, circuit, bindings, prints):
        self.circuit = circuit
        self.bindings = bindings
        self.prints = prints

    def run(self) -> Dict[str, Any]:
        raise NotImplementedError


class Diagram:

    def __init__(self, circuit: str):
        self.circuit = self.fixup_circuit(circuit)

    def fixup_circuit(self, circuit: str):
        """The LaTeX circuit comes out with some ugly labels on the left-hand-side.
        Let's get rid of those with a simple regex. Of course, we don't know if
        the API producing the circuit is stable, so this method might need to
        change next time we upgrade Cirq.
        """
        import re

        # You might want to write the names of things in braces at
        # the end of the circuit or something
        pattern = r'&\\lstick{\\text{q\\_\d+}}& \\qw'
        circuit, _ = re.subn(pattern, '', circuit)
        return circuit

    @deps.require('pylatex')
    def to_pdf(self, file_path: str):
        from pylatex import Document, Package, NoEscape

        doc = Document(documentclass='standalone')
        doc.packages.append(Package('qcircuit'))
        doc.packages.append(Package('physics'))
        doc.packages.append(Package('amsmath'))
        doc.append(NoEscape(self.circuit))

        doc.generate_pdf(file_path)



class Deserializer:
    def __init__(self,  measurements: Dict[str, Dict[str, Any]]):
        self.measurements = measurements
        # A dispatch table mapping type names to deserialization functions.
        self.d_table = {
            'Bool': self.deserialize_classical,
            'Q_Bool': self.deserialize_q_bool,
            'Q_U8': self.deserialize_q_unsigned,
            'Q_U16': self.deserialize_q_unsigned,
            'Q_U32': self.deserialize_q_unsigned,
            'Array': self.deserialize_array,
            'Measured': self.deserialize_measured,
        }

    def deserialize(self, value: Dict[str, Any]) -> Any:
        typ, data = self.split_value(value)
        func = self.d_table.get(typ, self.deserialize_default)
        return func(data)

    def split_value(self, value: Dict[str, Any]) -> (str, Any):
        """The values contained in the bindings dictionary are assumed to be maps from
        type strings to the contained data; that is, dictionaries with a single
        key-value pair.
        """
        return next(iter(value.items()))

    def deserialize_classical(self, data: Any) -> Any:
        return data

    def deserialize_q_bool(self, data: int) -> bool:
        bit = self.measurements[data]
        return bool(bit)

    def deserialize_q_unsigned(self, data: Any) -> int:
        bits = [self.measurements[qb] for qb in data]
        num = 0
        for i, bit in enumerate(bits):
            num += (1 << i) * bit
        return num

    def deserialize_array(self, data: List) -> int:
        return [self.deserialize(item) for item in data]

    def deserialize_measured(self, data: Dict[str, Any]) -> Any:
        return self.deserialize(data)

    def deserialize_default(self, data: Any) -> Any:
        raise NotImplementedError
