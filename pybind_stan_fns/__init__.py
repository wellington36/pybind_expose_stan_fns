import importlib
import os
import platform
import subprocess
import sys
import sysconfig
from pathlib import Path

import cmdstanpy
import pybind11

from . import preprocess


def get_pybind_includes():
    # copied from pybind11.__main__
    dirs = [
        sysconfig.get_path("include"),
        sysconfig.get_path("platinclude"),
        pybind11.get_include(),
    ]

    # Make unique but preserve order
    unique_dirs = []
    for d in dirs:
        if d and d not in unique_dirs:
            unique_dirs.append(d)
    return unique_dirs


CMDSTAN = Path(cmdstanpy.cmdstan_path())
STANC = CMDSTAN / "bin" / "stanc"

CPP_DEFINES = ["_REENTRANT", "BOOST_DISABLE_ASSERTS"]

LIBRARIES = [
    "sundials_nvecserial",
    "sundials_cvodes",
    "sundials_idas",
    "sundials_kinsol",
]

CMDSTAN_SUB_INCLUDES = [
    ("stan", "src"),
    ("stan", "lib", "rapidjson_1.1.0"),
    ("stan", "lib", "stan_math"),
    ("stan", "lib", "stan_math", "lib", "eigen_3.4.0"),
    ("stan", "lib", "stan_math", "lib", "boost_1.87.0"),
]

OTHER_INCLUDES = []

CXX_FLAGS = [
    "-std=c++17",
    "-O3",
    "-Wno-sign-compare",
    "-Wno-deprecated-builtins",
    "-Wno-ignored-attributes",
    "-shared",
]

CXX = "g++"

TBB_DIR = CMDSTAN / "stan" / "lib" / "stan_math" / "lib" / "tbb"


if platform.system() == "Windows":
    CXX = "clang++.exe"
    STANC = STANC.with_suffix(".exe")

    CPP_DEFINES.extend(["_BOOST_LGAMMA", "TBB_INTERFACE_NEW"])

    CONDA_PATH = Path(os.environ["CONDA_PREFIX"])

    OTHER_INCLUDES.append(
        os.fspath(CONDA_PATH / "Library" / "include")
    )

    LDFLAGS = [
        f'-Wl,/LIBPATH:{CONDA_PATH / "Library" / "lib"}',
        f'-Wl,/LIBPATH:{CONDA_PATH / "libs"}',
    ]

    # Windows uses the Conda TBB installation.
    LDLIBS = [f"-l{lib}" for lib in LIBRARIES] + ["-ltbb"]


else:
    # Linux and macOS use CmdStan's vendored TBB/SUNDIALS.
    CXX_FLAGS.extend(["-fPIC", "-fvisibility=hidden"])

    LDFLAGS = [
        f"-Wl,-L,{TBB_DIR}",
        f"-Wl,-L,{CMDSTAN}/stan/lib/stan_math/lib/sundials_6.1.1/lib",
        f"-Wl,-rpath,{TBB_DIR}",
    ]

    CMDSTAN_SUB_INCLUDES.extend(
        [
            ("stan", "lib", "stan_math", "lib", "tbb_2020.3", "include"),
            (
                "stan",
                "lib",
                "stan_math",
                "lib",
                "sundials_6.1.1",
                "include",
            ),
            (
                "stan",
                "lib",
                "stan_math",
                "lib",
                "sundials_6.1.1",
                "src",
                "sundials",
            ),
        ]
    )

    if platform.system() == "Darwin":
        CXX = "clang++"
        CXX_FLAGS.extend(["-undefined", "dynamic_lookup"])

        # Find the vendored TBB library on macOS.
        tbb_libraries = list(TBB_DIR.glob("*.dylib"))

        if tbb_libraries:
            TBB_LIBRARY = tbb_libraries[0]
        else:
            raise RuntimeError(
                f"Could not find vendored TBB library in {TBB_DIR}"
            )

        LDLIBS = [
            os.fspath(TBB_LIBRARY),
            *[f"-l{lib}" for lib in LIBRARIES],
        ]

    else:
        # Linux: CmdStan 2.39 vendors TBB 2020.3 as libtbb.so.2.
        TBB_LIBRARY = TBB_DIR / "libtbb.so.2"

        if not TBB_LIBRARY.exists():
            raise RuntimeError(
                f"Could not find vendored TBB library: {TBB_LIBRARY}"
            )

        LDLIBS = [
            os.fspath(TBB_LIBRARY),
            *[f"-l{lib}" for lib in LIBRARIES],
        ]


CMDSTAN_INCLUDE_PATHS = [
    os.fspath(CMDSTAN.joinpath(*sub))
    for sub in CMDSTAN_SUB_INCLUDES
]

CPP_FLAGS = [f"-D{define}" for define in CPP_DEFINES] + [
    f"-I{path}"
    for path in CMDSTAN_INCLUDE_PATHS
    + OTHER_INCLUDES
    + get_pybind_includes()
]

EXT_SUFFIX = sysconfig.get_config_var("EXT_SUFFIX")


def expose(file: str):
    file_path = Path(file).resolve()

    # Create .cpp file and add pybind-specific code.
    subprocess.run(
        [
            os.fspath(STANC),
            "--standalone-functions",
            f"--include-paths={file_path.parent}",
            f"--o={file_path.parent / file_path.with_suffix('.cpp-pre')}",
            os.fspath(file_path),
        ],
        check=True,
    )

    preprocess.preprocess(
        os.fspath(file_path.parent / file_path.with_suffix(".cpp-pre")),
        out=os.fspath(file_path.parent / file_path.with_suffix(".cpp")),
    )

    # Invoke compiler.
    compile_command = (
        [CXX]
        + CXX_FLAGS
        + CPP_FLAGS
        + [
            f"-o{file_path.parent / file_path.with_suffix(EXT_SUFFIX)}",
            os.fspath(file_path.parent / file_path.with_suffix(".cpp")),
        ]
        + LDFLAGS
        + LDLIBS
    )

    res = subprocess.run(
        compile_command,
        check=False,
        capture_output=True,
        text=True,
    )

    if res.returncode:
        raise RuntimeError(
            "Build failed!\n"
            + " ".join(compile_command)
            + "\n"
            + res.stderr
        )

    sys.path.append(os.fspath(file_path.parent))

    return importlib.import_module(file_path.stem)
