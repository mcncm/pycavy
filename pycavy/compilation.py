# This module provides the guts of the `pycavy` wrapper around the Cavy
# compiler. For the time being, the implementation is simple: it expects a
# `cavy` binary on the PATH, and writes object code to a temporary file.
#
# It's originally derived from the `cavy-python` code that made up the prototype
# implementation of Cavy, and now serves as part of the Python interface to the
# compiler.

import os
import subprocess
import tempfile

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
            self.__obj_code = obj_file.read()
        # Clean up the source file
        os.unlink(obj_path)

    def to_qasm(self) -> str:
        """Return the object code as a QASM string
        """
        return self.__obj_code

    @deps.require('qiskit')
    def to_qiskit(self) -> "qiskit.QuantumCircuit":
        """Return the object code as a Qiskit circuit
        """
        from_qasm = deps.qiskit.QuantumCircuit.from_qasm_str
        return from_qasm(self.__obj_code)

    @deps.require('cirq')
    def to_cirq(self) -> "cirq.Circuit":
        """Return the object code as a Cirq circuit
        """
        cirq = deps.cirq
        from cirq.contrib.qasm_import import circuit_from_qasm
        return circuit_from_qasm(self.__obj_code)

    @deps.require('labber')
    def to_labber(self):
        """Transform this circuit to a Labber circuit that can be run
        on a physical machine.
        TODO implement this!
        """
        raise NotImplementedError

    @deps.require('cirq', 'pylatex')
    def to_diagram(self) -> str:
        """Returns latex source for a circuit diagram
        """
        to_latex = deps.cirq.contrib.qcircuit.circuit_to_latex_using_qcircuit
        circuit = self.to_cirq()
        return to_latex(circuit, circuit.all_qubits())
