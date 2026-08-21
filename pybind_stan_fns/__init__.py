import importlib
import os
import platform
import shlex
import subprocess
import sys
import sysconfig
from pathlib import Path

import cmdstanpy
import pybind11

from . import preprocess


# ---------------------------------------------------------------------------
# Python / pybind11 configuration
# ---------------------------------------------------------------------------

def get_pybind_includes():
    """Return Python and pybind11 include directories."""
    dirs = [
        sysconfig.get_path("include"),
        sysconfig.get_path("platinclude"),
        pybind11.get_include(),
    ]

    unique_dirs = []
    for directory in dirs:
        if directory and directory not in unique_dirs:
            unique_dirs.append(directory)

    return unique_dirs


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------

SYSTEM = platform.system()

IS_WINDOWS = SYSTEM == "Windows"
IS_MACOS = SYSTEM == "Darwin"
IS_LINUX = SYSTEM == "Linux"


# ---------------------------------------------------------------------------
# CmdStan
# ---------------------------------------------------------------------------

CMDSTAN = Path(cmdstanpy.cmdstan_path())

STANC = CMDSTAN / "bin" / "stanc"

if IS_WINDOWS:
    STANC = STANC.with_suffix(".exe")


# ---------------------------------------------------------------------------
# Common compiler configuration
# ---------------------------------------------------------------------------

CPP_DEFINES = [
    "_REENTRANT",
    "BOOST_DISABLE_ASSERTS",
]

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
]


# ---------------------------------------------------------------------------
# CmdStan library directories
# ---------------------------------------------------------------------------

STAN_MATH_LIB = (
    CMDSTAN
    / "stan"
    / "lib"
    / "stan_math"
    / "lib"
)

TBB_DIR = STAN_MATH_LIB / "tbb"

SUNDIALS_DIR = STAN_MATH_LIB / "sundials_6.1.1"

SUNDIALS_LIB_DIR = SUNDIALS_DIR / "lib"

SUNDIALS_INCLUDE_DIR = SUNDIALS_DIR / "include"

SUNDIALS_SRC_DIR = SUNDIALS_DIR / "src" / "sundials"


# ---------------------------------------------------------------------------
# Platform-specific compiler/linker configuration
# ---------------------------------------------------------------------------

LDFLAGS = []
LDLIBS = []


if IS_WINDOWS:
    # -----------------------------------------------------------------------
    # Windows
    #
    # Windows CI uses the Conda environment. TBB and SUNDIALS therefore
    # come from Conda rather than CmdStan's vendored libraries.
    # -----------------------------------------------------------------------

    CXX = "clang++.exe"

    CPP_DEFINES.extend([
        "_BOOST_LGAMMA",
        "TBB_INTERFACE_NEW",
    ])

    conda_prefix = os.environ.get("CONDA_PREFIX")

    if not conda_prefix:
        raise RuntimeError(
            "CONDA_PREFIX is not set. "
            "The Windows build requires the Conda environment."
        )

    CONDA_PATH = Path(conda_prefix)

    CONDA_INCLUDE = CONDA_PATH / "Library" / "include"
    CONDA_LIB = CONDA_PATH / "Library" / "lib"
    CONDA_BIN = CONDA_PATH / "Library" / "bin"

    OTHER_INCLUDES.append(os.fspath(CONDA_INCLUDE))

    # Conda clang++ uses the library directory with -L.
    LDFLAGS.extend([
        f"-L{CONDA_LIB}",
        f"-L{SUNDIALS_LIB_DIR}",
    ])

    # TBB + SUNDIALS from Conda / CmdStan-compatible environment.
    LDLIBS.extend([
        "-ltbb",
        *[f"-l{lib}" for lib in LIBRARIES],
    ])

    CXX_FLAGS.extend([
        "-DWIN32",
    ])


elif IS_MACOS:
    # -----------------------------------------------------------------------
    # macOS
    #
    # Important:
    #
    # Python extension modules should NOT be linked against libpython on
    # macOS. Python itself provides the Python C API symbols at load time.
    #
    # Therefore we use:
    #
    #     -undefined dynamic_lookup
    #
    # This is the standard pybind11/manual-extension approach.
    # -----------------------------------------------------------------------

    CXX = "clang++"

    CXX_FLAGS.extend([
        "-fPIC",
        "-fvisibility=hidden",
        "-dynamiclib",
        "-undefined",
        "dynamic_lookup",
    ])

    CMDSTAN_SUB_INCLUDES.extend([
        (
            "stan",
            "lib",
            "stan_math",
            "lib",
            "tbb_2020.3",
            "include",
        ),
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
    ])

    # Search paths.
    LDFLAGS.extend([
        f"-L{TBB_DIR}",
        f"-L{SUNDIALS_LIB_DIR}",

        # Make the extension find CmdStan's vendored libraries at runtime.
        f"-Wl,-rpath,{TBB_DIR}",
        f"-Wl,-rpath,{SUNDIALS_LIB_DIR}",
    ])

    # Explicitly link CmdStan's TBB.
    #
    # Do NOT use -lpython here.
    tbb_library = TBB_DIR / "libtbb.dylib"

    if not tbb_library.exists():
        raise RuntimeError(
            f"Could not find vendored TBB library: {tbb_library}"
        )

    LDLIBS.extend([
        os.fspath(tbb_library),
        *[f"-l{lib}" for lib in LIBRARIES],
    ])


elif IS_LINUX:
    # -----------------------------------------------------------------------
    # Linux
    # -----------------------------------------------------------------------

    CXX = "g++"

    CXX_FLAGS.extend([
        "-fPIC",
        "-fvisibility=hidden",
        "-shared",
    ])

    CMDSTAN_SUB_INCLUDES.extend([
        (
            "stan",
            "lib",
            "stan_math",
            "lib",
            "tbb_2020.3",
            "include",
        ),
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
    ])

    LDFLAGS.extend([
        f"-L{TBB_DIR}",
        f"-L{SUNDIALS_LIB_DIR}",
        f"-Wl,-rpath,{TBB_DIR}",
        f"-Wl,-rpath,{SUNDIALS_LIB_DIR}",
    ])

    # CmdStan 2.39 uses the old TBB 2020.3 library ABI.
    tbb_library = TBB_DIR / "libtbb.so.2"

    if not tbb_library.exists():
        raise RuntimeError(
            f"Could not find vendored TBB library: {tbb_library}"
        )

    LDLIBS.extend([
        os.fspath(tbb_library),
        *[f"-l{lib}" for lib in LIBRARIES],
    ])


else:
    raise RuntimeError(
        f"Unsupported operating system: {SYSTEM}"
    )


# ---------------------------------------------------------------------------
# Include paths
# ---------------------------------------------------------------------------

CMDSTAN_INCLUDE_PATHS = [
    os.fspath(CMDSTAN.joinpath(*sub))
    for sub in CMDSTAN_SUB_INCLUDES
]

CPP_FLAGS = (
    [f"-D{define}" for define in CPP_DEFINES]
    + [
        f"-I{path}"
        for path in (
            CMDSTAN_INCLUDE_PATHS
            + OTHER_INCLUDES
            + get_pybind_includes()
        )
    ]
)


# ---------------------------------------------------------------------------
# Python extension suffix
# ---------------------------------------------------------------------------

EXT_SUFFIX = sysconfig.get_config_var("EXT_SUFFIX")

if not EXT_SUFFIX:
    raise RuntimeError(
        "Could not determine Python extension suffix."
    )


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def _print_build_configuration():
    """Print useful build information when compilation fails."""

    print("\n========== pybind_stan_fns build configuration ==========")
    print(f"Platform:        {SYSTEM}")
    print(f"Architecture:    {platform.machine()}")
    print(f"Python:          {sys.version}")
    print(f"Python prefix:   {sys.prefix}")
    print(f"Compiler:        {CXX}")
    print(f"CmdStan:         {CMDSTAN}")
    print(f"TBB directory:   {TBB_DIR}")
    print(f"Extension suffix:{EXT_SUFFIX}")
    print("==========================================================\n")


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def expose(file: str):
    """
    Compile a Stan file into a Python extension module.

    Parameters
    ----------
    file:
        Path to the .stan file.
    """

    file_path = Path(file).resolve()

    if not file_path.exists():
        raise FileNotFoundError(
            f"Stan file does not exist: {file_path}"
        )

    # -----------------------------------------------------------------------
    # 1. Run stanc
    # -----------------------------------------------------------------------

    cpp_pre = file_path.with_suffix(".cpp-pre")

    stanc_command = [
        os.fspath(STANC),
        "--standalone-functions",
        f"--include-paths={file_path.parent}",
        f"--o={cpp_pre}",
        os.fspath(file_path),
    ]

    subprocess.run(
        stanc_command,
        check=True,
    )

    # -----------------------------------------------------------------------
    # 2. Preprocess generated C++
    # -----------------------------------------------------------------------

    cpp_file = file_path.with_suffix(".cpp")

    preprocess.preprocess(
        os.fspath(cpp_pre),
        out=os.fspath(cpp_file),
    )

    # -----------------------------------------------------------------------
    # 3. Output extension
    # -----------------------------------------------------------------------

    extension_file = file_path.with_suffix(EXT_SUFFIX)

    # -----------------------------------------------------------------------
    # 4. Compile + link
    # -----------------------------------------------------------------------

    compile_command = (
        [CXX]
        + CXX_FLAGS
        + CPP_FLAGS
        + [
            "-o",
            os.fspath(extension_file),
            os.fspath(cpp_file),
        ]
        + LDFLAGS
        + LDLIBS
    )

    print("\nBuild command:")
    print(" ".join(shlex.quote(str(x)) for x in compile_command))
    print()

    result = subprocess.run(
        compile_command,
        check=False,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        _print_build_configuration()

        raise RuntimeError(
            "Build failed!\n\n"
            + "Command:\n"
            + " ".join(
                shlex.quote(str(x))
                for x in compile_command
            )
            + "\n\n"
            + "stdout:\n"
            + result.stdout
            + "\n\n"
            + "stderr:\n"
            + result.stderr
        )

    # -----------------------------------------------------------------------
    # 5. Import generated module
    # -----------------------------------------------------------------------

    sys.path.insert(0, os.fspath(file_path.parent))

    return importlib.import_module(file_path.stem)
