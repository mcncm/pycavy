use paste::paste;
use pyo3::{class::basic::PyObjectProtocol, create_exception, prelude::*};

use cavy::{
    arch::{Arch, MeasurementMode},
    cavy_errors::ErrorBuf,
    circuit::{BaseGateQ, CircuitBuf, GateQ, Inst, Qbit},
    context::Context,
    session::{Config, OptConfig, OptFlags, Phase, PhaseConfig, Statistics},
    util::FmtWith,
};

create_exception!(pycavy, CavyError, pyo3::exceptions::PyException);

#[pyclass(subclass)]
struct Gate {}

#[pymethods]
impl Gate {
    #[new]
    fn new() -> Self {
        Self {}
    }
}

macro_rules! gates {
    ($module:ident < $($name:ident[$qbs:expr]),*) => {
        $(

        paste! {
            #[pyclass(extends=Gate, subclass)]
            /// A quantum gate implementing the named operation
            struct [<$name Gate>] {
                // Could consider adding a `set` to this
                #[pyo3(get)]
                qbs: [usize; $qbs],
            }

            impl [<$name Gate>] {
                fn pyobj<'p>(py: Python<'p>, qbs: [Qbit; $qbs]) -> &PyAny {
                    let mut new_qbs = [0; $qbs];
                    for i in 0..$qbs {
                        new_qbs[i] = <u32>::from(qbs[i]) as usize;
                    }
                    PyCell::new(py, Self::new(new_qbs))
                        .unwrap()
                        .as_ref()
                }
            }

            #[pymethods]
            impl [<$name Gate>] {
                #[new]
                fn new(qbs: [usize; $qbs]) -> (Self, Gate) {
                    (Self { qbs }, Gate::new())
                }
            }

            #[pyproto]
            impl PyObjectProtocol for [<$name Gate>] {
                fn __repr__(&self) -> PyResult<String> {
                    Ok(format!("{}{:?}", stringify!($name), self.qbs))
                }

                fn __str__(&self) -> PyResult<String> {
                    self.__repr__()
                }
            }
        }
        )*
    };
}

gates! { m <
    H[1], Z[1], X[1], T[1], TDag[1], CX[2], SWAP[2]
}

fn circuit_to_py(py: Python, circ: CircuitBuf) -> PyResult<Vec<&PyAny>> {
    let transcribe_base_gate = |gate| match gate {
        BaseGateQ::X(u) => XGate::pyobj(py, [u]),
        BaseGateQ::T(u) => TGate::pyobj(py, [u]),
        BaseGateQ::H(u) => HGate::pyobj(py, [u]),
        BaseGateQ::Z(u) => ZGate::pyobj(py, [u]),
        BaseGateQ::TDag(u) => TDagGate::pyobj(py, [u]),
        BaseGateQ::Cnot { tgt, ctrl } => CXGate::pyobj(py, [ctrl, tgt]),
        BaseGateQ::Swap(fst, snd) => SWAPGate::pyobj(py, [fst, snd]),
    };

    let transcribe_gate = |gate: GateQ| {
        let base = transcribe_base_gate(gate.base);
        if gate.ctrls.is_empty() {
            base
        } else {
            // FIXME not handled yet
            panic!();
        }
    };

    // What if there are infinitely many gates?
    let gates = circ
        .into_iter()
        .filter_map(|inst| match inst {
            Inst::CInit(_) => None,
            Inst::CFree(_, _) => None,
            Inst::QInit(_) => None,
            Inst::QFree(_, _) => None,
            Inst::QGate(gate) => Some(transcribe_gate(gate)),
            Inst::CGate(_) => {
                todo!()
            }
            Inst::Meas(_, _) => None,
            Inst::Out(_) => None,
        })
        .collect();
    Ok(gates)
}

fn get_meas_mode(mode: &str) -> Result<MeasurementMode, ()> {
    let mode = match mode {
        "nondemolition" => MeasurementMode::Nondemolition,
        "demolition" => MeasurementMode::Demolition,
        _ => {
            return Err(());
        }
    };
    Ok(mode)
}

fn get_phase(phase: Option<&str>) -> PhaseConfig {
    let last_phase = match phase {
        Some("tokenize") => Phase::Tokenize,
        Some("parse") => Phase::Parse,
        Some("typecheck") => Phase::Typecheck,
        Some("analysis") => Phase::Analysis,
        Some("optimization") => Phase::Optimization,
        Some("translation") => Phase::Translation,
        Some("codegen") => Phase::CodeGen,
        Some(_) => unreachable!(),
        None => Phase::CodeGen,
    };

    PhaseConfig {
        last_phase,
        typecheck: true,
    }
}

/// Convert an optional input to an opt flag value
fn opt_flag(input: Option<bool>) -> i8 {
    match input {
        Some(true) => 1,
        Some(false) => -1,
        None => 0,
    }
}

#[pyclass]
struct Session {
    conf: Config,
}

/// A Cavy compilation session, whose constructor accepts compiler options to
/// customize device architecture and code generation behavior.
#[pymethods]
impl Session {
    #[new]
    #[args(
        opt_level = "3",
        const_prop = "None",
        debug = "false",
        qb_count = "None",
        qram_size = "0",
        meas_mode = "\"nondemolition\"",
        feedback = "false",
        recursion = "false",
        phase = "None"
    )]
    fn new(
        opt_level: u8,
        const_prop: Option<bool>,
        debug: bool,
        // architecture options
        qb_count: Option<usize>,
        qram_size: usize,
        meas_mode: &str,
        feedback: bool,
        recursion: bool,
        phase: Option<&str>,
    ) -> Self {
        let phase_config = get_phase(phase);
        let meas_mode = get_meas_mode(meas_mode).unwrap();
        let arch = Arch {
            qb_count: qb_count.into(),
            qram_size,
            meas_mode,
            feedback,
            recursion,
        };
        let mut opt_flags = OptFlags::default();
        opt_flags.const_prop = opt_flag(const_prop);
        let opt = OptConfig {
            level: opt_level,
            flags: opt_flags,
        };
        let conf = Config {
            debug,
            arch,
            opt,
            phase_config,
        };
        Self { conf }
    }

    fn compile<'a>(&self, py: Python<'a>, src: String) -> PyResult<Vec<&'a PyAny>> {
        let mut stats = Statistics::new();
        let mut ctx = Context::new(&self.conf, &mut stats);

        match self.compile_inner(&mut ctx, src) {
            Ok(Some(circ)) => circuit_to_py(py, circ),
            Ok(None) => Ok(vec![]),
            Err(errs) => {
                let errs = format!("{}", errs.fmt_with(&ctx));
                let py_err = PyErr::new::<CavyError, _>(errs);
                Err(py_err)
            }
        }
    }
}

impl Session {
    fn compile_inner(
        &self,
        ctx: &mut Context,
        src: String,
    ) -> Result<Option<CircuitBuf>, ErrorBuf> {
        let id = ctx.srcs.insert_input(&src);
        cavy::compile::compile_circuit(id, ctx)
    }
}

/// the Python interface to the Cavylang compiler
#[pymodule]
fn pycavy(py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<Session>()?;
    m.add_class::<Gate>()?;
    m.add_class::<HGate>()?;
    m.add_class::<ZGate>()?;
    m.add_class::<XGate>()?;
    m.add_class::<TGate>()?;
    m.add_class::<CXGate>()?;

    m.add("CavyError", py.get_type::<CavyError>())?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
