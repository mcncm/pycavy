"""the Python interface to the Cavy compiler.

The typical usage of this package is to create a Program object from some
literal Cavy source code, then compile it and emit some circuit representation
of the assembly code that can be executed on real or simulated hardware. For
example, the following snippet:

```
from pycavy import Program

circuit = Program('''
let x = ?false;
let y = ?false;
let z = ?false;
if z {
  if y {
    let x = ~x;
  }
}
''').compile(opt=3).to_cirq()

print(circuit)
```

should draw a circuit expansion for a Toffoli gate, something like (cirq 0.8.2):

```
q_0: ───H───X───T^-1───X───T───X───T^-1───X───T───H──────────
            │          │       │          │
q_1: ───────┼──────────@───────┼──────────@───@───T──────@───
            │                  │              │          │
q_2: ───────@──────────────────@───T──────────X───T^-1───X───
```

Similarly, if we replace `to_cirq` with `to_qiskit` (qiskit 0.15.1):

```
     ┌───┐┌───┐┌─────┐┌───┐┌───┐┌───┐┌─────┐┌───┐┌───┐ ┌───┐
q_0: ┤ H ├┤ X ├┤ TDG ├┤ X ├┤ T ├┤ X ├┤ TDG ├┤ X ├┤ T ├─┤ H ├──────
     └───┘└─┬─┘└─────┘└─┬─┘└───┘└─┬─┘└─────┘└─┬─┘└───┘ ├───┤
q_1: ───────┼───────────■─────────┼───────────■────■───┤ T ├───■──
            │                     │   ┌───┐      ┌─┴─┐┌┴───┴┐┌─┴─┐
q_2: ───────■─────────────────────■───┤ T ├──────┤ X ├┤ TDG ├┤ X ├
                                      └───┘      └───┘└─────┘└───┘
```

The `compile` function may take more optional arguments in the future, in
particular related to hardware-specific optimization. For now, it only takes an
optional `opt` argument for optimization level, which may be 0, 1, 2, or 3.

"""

from pycavy.compilation import Program
