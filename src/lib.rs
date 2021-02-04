use paste::paste;
use pyo3::{class::basic::PyObjectProtocol, create_exception, prelude::*};

use cavy::{
    analysis,
    arch::{Arch, MeasurementMode},
    cavy_errors::ErrorBuf,
    circuit::{self, Circuit},
    codegen,
    context::{Context, CtxFmt},
    lowering, parser, scanner,
    session::{Config, Phase, PhaseConfig},
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

macro_rules! drop_token {
    (inv) => {};
}

macro_rules! gates {
    ($module:ident < $($name:ident[$qbs:expr] $($inv:ident)?),*) => {
        $(
        // Ensures that this token is literally `inv`
        $(drop_token! { $inv })?

        paste! {
            #[pyclass(extends=Gate, subclass)]
            struct [<$name Gate>] {
                // Could consider adding a `set` to this
                #[pyo3(get)]
                qbs: [usize; $qbs],
                $(
                // Also, macro expansion fails if we don't use this token.
                $inv: bool,
                )?
            }

            #[pymethods]
            impl [<$name Gate>] {
                #[new]
                fn new(qbs: [usize; $qbs] $(, $inv: bool)?) -> (Self, Gate) {
                    (Self { qbs $(, $inv)? }, Gate::new())
                }
            }

            #[pyproto]
            impl PyObjectProtocol for [<$name Gate>] {
                fn __repr__(&self) -> PyResult<String> {
                    #![allow(unused_variables)] // suppress warning for `inv`
                    let inv = "";
                    $(
                        // declared again here to appease linters that don't
                        // handle macros very well
                        let mut inv = "";
                        drop_token! { $inv }
                        if self.inv {
                            inv = "+";
                        }
                    )?
                    Ok(format!("{}{}{:?}", stringify!($name), inv, self.qbs))
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
    H[1], Z[1], X[1], T[1] inv, CX[2]
}

fn circuit_to_py(py: Python, circ: Circuit) -> PyResult<Vec<&PyAny>> {
    let transcribe_gate = |gate| match gate {
        circuit::Gate::X(qb) => PyCell::new(py, HGate::new([qb])).unwrap().as_ref(),
        circuit::Gate::T { tgt, conj } => {
            PyCell::new(py, TGate::new([tgt], conj)).unwrap().as_ref()
        }
        circuit::Gate::H(qb) => PyCell::new(py, HGate::new([qb])).unwrap().as_ref(),
        circuit::Gate::Z(qb) => PyCell::new(py, ZGate::new([qb])).unwrap().as_ref(),
        circuit::Gate::CX { tgt, ctrl } => {
            PyCell::new(py, CXGate::new([ctrl, tgt])).unwrap().as_ref()
        }
        circuit::Gate::M(_) => todo!(),
    };

    let gates = circ.circ_buf.into_iter().map(transcribe_gate).collect();

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
        opt = "3",
        debug = "false",
        qb_count = "None",
        qram_size = "0",
        meas_mode = "\"nondemolition\"",
        feedback = "false"
    )]
    fn new(
        opt: u8,
        debug: bool,
        // architecture options
        qb_count: Option<usize>,
        qram_size: usize,
        meas_mode: &str,
        feedback: bool,
    ) -> Self {
        let phase_config = PhaseConfig {
            typecheck: true,
            last_phase: Phase::Evaluate,
        };
        let meas_mode = get_meas_mode(meas_mode).unwrap();
        let arch = Arch {
            qb_count: qb_count.into(),
            qram_size,
            meas_mode,
            feedback,
        };
        let conf = Config {
            debug,
            arch,
            // This should be replaced with a "bare circuit" target, at which
            // point we can replace the body of `compile_inner` with
            // `cavy::compile::compile`.
            target: Box::new(cavy::target::null::NullTarget {}),
            opt,
            phase_config,
        };
        Self { conf }
    }

    fn compile<'a>(&self, py: Python<'a>, src: String) -> PyResult<Vec<&'a PyAny>> {
        let mut ctx = Context::new(&self.conf);

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
    // Can I get a `Vec<Gate>`?
    /// What this function should actually do is build a `Context` from the data
    /// passed to it when setting parameters, then in this `compile` function,
    /// it just calls `cavy::compile::compile`, which should return a
    /// `CodeObject` which is ideally just a `Vec<Gate>`. Then we map over it
    /// and return a list (or generator).
    fn compile_inner(&self, ctx: &mut Context, src: String) -> Result<Option<Circuit>, ErrorBuf> {
        let id = ctx.srcs.insert_input(&src);

        let tokens = scanner::tokenize(id, ctx)?;

        let ast = parser::parse(tokens, ctx)?;
        if ctx.conf.debug && ctx.last_phase() == &Phase::Parse {
            println!("{:#?}", ast);
            return Ok(None);
        }

        let mir = lowering::lower(ast, ctx)?;
        if ctx.conf.debug && ctx.last_phase() == &Phase::Typecheck {
            println!("{}", mir.fmt_with(&ctx));
            return Ok(None);
        }

        analysis::check(&mir, &ctx)?;

        let circ = codegen::codegen(&mir, &ctx);
        Ok(Some(circ))
    }
}

/// A Python module implemented in Rust.
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
    Ok(())
}
