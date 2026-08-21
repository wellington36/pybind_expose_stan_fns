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


# ===========================================================================
# WINDOWS
# ===========================================================================

if IS_WINDOWS:

    CXX = "clang++.exe"

    CPP_DEFINES.extend([
        "_BOOST_LGAMMA",
        "TBB_INTERFACE_NEW",
    ])

    # -----------------------------------------------------------------------
    # Conda environment
    # -----------------------------------------------------------------------

    conda_prefix = os.environ.get("CONDA_PREFIX")

    if not conda_prefix:
        raise RuntimeError(
            "CONDA_PREFIX is not set. "
            "The Windows build requires a Conda environment."
        )

    CONDA_PATH = Path(conda_prefix)

    CONDA_INCLUDE = CONDA_PATH / "Library" / "include"
    CONDA_LIB = CONDA_PATH / "Library" / "lib"

    OTHER_INCLUDES.append(os.fspath(CONDA_INCLUDE))


    # -----------------------------------------------------------------------
    # Find the Python import library
    # -----------------------------------------------------------------------
    #
    # IMPORTANT:
    #
    # Do not use sysconfig.get_config_var("LIBRARY") directly.
    #
    # On Windows/Conda it may return:
    #
    #     python3.lib
    #
    # while the actual file is:
    #
    #     python312.lib
    #
    # or:
    #
    #     python310.lib
    #
    # We therefore construct the expected versioned name ourselves and
    # verify that the file exists.
    # -----------------------------------------------------------------------

    python_version = (
        f"python{sys.version_info.major}{sys.version_info.minor}.lib"
    )

    python_lib_candidates = [
        CONDA_PATH / "libs" / python_version,
        Path(sys.prefix) / "libs" / python_version,
        Path(sys.base_prefix) / "libs" / python_version,
    ]

    python_lib = None

    for candidate in python_lib_candidates:
        if candidate.exists():
            python_lib = candidate
            break

    # -----------------------------------------------------------------------
    # Fallback: search the usual Python library directories.
    # -----------------------------------------------------------------------

    if python_lib is None:

        possible_dirs = [
            CONDA_PATH / "libs",
            Path(sys.prefix) / "libs",
            Path(sys.base_prefix) / "libs",
        ]

        for directory in possible_dirs:
            if not directory.exists():
                continue

            candidates = sorted(
                directory.glob(
                    f"python{sys.version_info.major}*.lib"
                )
            )

            # Prefer the exact major/minor version.
            exact = [
                candidate
                for candidate in candidates
                if candidate.name.lower() == python_version.lower()
            ]

            if exact:
                python_lib = exact[0]
                break

            if candidates:
                python_lib = candidates[0]
                break


    if python_lib is None:
        raise RuntimeError(
            "Could not find Python import library.\n"
            f"Python: {sys.version}\n"
            f"Expected: {python_version}\n"
            f"Searched:\n"
            + "\n".join(
                f"  {path}"
                for path in python_lib_candidates
            )
        )

    PYTHON_LIB_DIR = python_lib.parent


    # -----------------------------------------------------------------------
    # Windows linker paths
    # -----------------------------------------------------------------------

    LDFLAGS.extend([
        f"-L{PYTHON_LIB_DIR}",
        f"-L{CONDA_LIB}",
        f"-L{SUNDIALS_LIB_DIR}",
    ])


    # -----------------------------------------------------------------------
    # Windows libraries
    # -----------------------------------------------------------------------
    #
    # Pass the Python import library using its ABSOLUTE PATH.
    #
    # This avoids clang trying to find a nonexistent "python3.lib".
    # -----------------------------------------------------------------------

    LDLIBS.extend([
        os.fspath(python_lib),
        "-ltbb",
        *[f"-l{lib}" for lib in LIBRARIES],
    ])


# ===========================================================================
# MACOS
# ===========================================================================

elif IS_MACOS:

    CXX = "clang++"

    CXX_FLAGS.extend([
        "-fPIC",
        "-fvisibility=hidden",
        "-dynamiclib",
        "-undefined",
        "dynamic_lookup",
    ])


    # -----------------------------------------------------------------------
    # Additional CmdStan includes
    # -----------------------------------------------------------------------

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


    # -----------------------------------------------------------------------
    # Library search paths
    # -----------------------------------------------------------------------

    LDFLAGS.extend([
        f"-L{TBB_DIR}",
        f"-L{SUNDIALS_LIB_DIR}",
        f"-Wl,-rpath,{TBB_DIR}",
        f"-Wl,-rpath,{SUNDIALS_LIB_DIR}",
    ])


    # -----------------------------------------------------------------------
    # Vendored TBB
    # -----------------------------------------------------------------------

    tbb_library = TBB_DIR / "libtbb.dylib"

    if not tbb_library.exists():
        raise RuntimeError(
            f"Could not find vendored TBB library:\n"
            f"  {tbb_library}"
        )

    LDLIBS.extend([
        os.fspath(tbb_library),
        *[f"-l{lib}" for lib in LIBRARIES],
    ])


# ===========================================================================
# LINUX
# ===========================================================================

elif IS_LINUX:

    CXX = "g++"

    CXX_FLAGS.extend([
        "-fPIC",
        "-fvisibility=hidden",
        "-shared",
    ])


    # -----------------------------------------------------------------------
    # Additional CmdStan includes
    # -----------------------------------------------------------------------

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


    # -----------------------------------------------------------------------
    # Library search paths
    # -----------------------------------------------------------------------

    LDFLAGS.extend([
        f"-L{TBB_DIR}",
        f"-L{SUNDIALS_LIB_DIR}",
        f"-Wl,-rpath,{TBB_DIR}",
        f"-Wl,-rpath,{SUNDIALS_LIB_DIR}",
    ])


    # -----------------------------------------------------------------------
    # CmdStan 2.39 TBB
    # -----------------------------------------------------------------------

    tbb_library = TBB_DIR / "libtbb.so.2"

    if not tbb_library.exists():
        raise RuntimeError(
            f"Could not find vendored TBB library:\n"
            f"  {tbb_library}"
        )

    LDLIBS.extend([
        os.fspath(tbb_library),
        *[f"-l{lib}" for lib in LIBRARIES],
    ])


# ===========================================================================
# Unsupported platform
# ===========================================================================

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
    """Print useful information when compilation fails."""

    print(
        "\n"
        "========== pybind_stan_fns build configuration =========="
    )

    print(f"Platform:         {SYSTEM}")
    print(f"Architecture:     {platform.machine()}")
    print(f"Python:           {sys.version}")
    print(f"Python executable:{sys.executable}")
    print(f"Python prefix:    {sys.prefix}")
    print(f"Compiler:         {CXX}")
    print(f"CmdStan:          {CMDSTAN}")
    print(f"TBB directory:    {TBB_DIR}")
    print(f"SUNDIALS library: {SUNDIALS_LIB_DIR}")
    print(f"Extension suffix: {EXT_SUFFIX}")

    if IS_WINDOWS:
        print(f"Python lib:       {python_lib}")

    print(
        "==========================================================\n"
    )


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
    print(
        " ".join(
            shlex.quote(str(x))
            for x in compile_command
        )
    )
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
            "Command:\n"
            + " ".join(
                shlex.quote(str(x))
                for x in compile_command
            )
            + "\n\n"
            "stdout:\n"
            + result.stdout
            + "\n\n"
            "stderr:\n"
            + result.stderr
        )


    # -----------------------------------------------------------------------
    # 5. Import generated module
    # -----------------------------------------------------------------------

    sys.path.insert(
        0,
        os.fspath(file_path.parent),
    )

    return importlib.import_module(file_path.stem)
