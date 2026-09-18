"""Exercise captured native workers and settings, not replacement implementations."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from ccpatch.patches import (
    _PROVIDER_ENV_SNAPSHOT,
    BACKGROUND_PROVIDER_ENV_198,
    PatchError,
    _attribution_function,
    _initialize_provider_registry,
    _provider_env_207,
    _provider_key_sources,
    _provider_serialized_keys,
    _replace_provider_snapshot,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("source", ["keys", "$keys", "ke$ys"])
def test_provider_registry_renamed(source: str) -> None:
    script = (
        f'var $init=$once(()=>{{other=new Set(["}}"]);{source}=new Set(["KEY"]);}});'
        f'function _ccProviderKeys(){{if({source}==null)throw Error();}}'
    )
    pattern = re.compile(r"function _ccProviderKeys\(\)\{if\([^)]*\)")
    assert "if(($init()," in pattern.sub(_initialize_provider_registry, script)


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "var init=once(()=>{});",
        "var init=once(()=>{function nested(){",
        "var init=once(()=>{var other=once(()=>{});",
        "var init=once(()=>{keys=new Set([]);",
    ],
)
def test_provider_registry_rejects_ambiguous_scope(prefix: str) -> None:
    script = prefix + "keys=new Set([]);});function _ccProviderKeys(){if(keys==null)"
    pattern = re.compile(r"function _ccProviderKeys\(\)\{if\([^)]*\)")
    with pytest.raises(PatchError):
        pattern.sub(_initialize_provider_registry, script)


@pytest.mark.parametrize("entry", ["spare", "preclaim", "cold", "agents"])
def test_provider_207_real_startup(entry: str, tmp_path: Path) -> None:
    """Run the complete CLI settings path without daemon or network access."""
    binary = os.environ.get("CCPATCH_STARTUP_BINARY")
    if not binary:
        pytest.skip("set CCPATCH_STARTUP_BINARY to a patched native executable")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "CLAUDE_CONFIG_DIR": str(tmp_path),
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "HTTP_PROXY": "http://127.0.0.1:1",
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "CLAUDE_CODE_PROVIDER_ENV_TRANSIENT": json.dumps(
            {
                "ANTHROPIC_BASE_URL": "http://127.0.0.1:1",
                "ANTHROPIC_API_KEY": "synthetic-test-key",
                "CLAUDE_CONFIG_DIR": str(tmp_path),
            }
        ),
    }
    if entry in {"spare", "preclaim"}:
        del env["CLAUDE_CODE_PROVIDER_ENV_TRANSIENT"]
    if entry == "preclaim":
        env["CLAUDE_CODE_SESSION_KIND"] = "bg"
    if entry == "agents":
        env["CLAUDE_AGENTS_SELECT"] = "synthetic"
    # Missing verbosity exits after cold settings initialization, before requests.
    args = (
        ["--bg-spare"]
        if entry in {"spare", "preclaim"}
        else ["--setting-sources", "", "-p", "--output-format", "stream-json", "test"]
    )
    process = subprocess.Popen(  # noqa: S603 - Explicit opt-in local binary.
        [binary, *args],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=20)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
    assert "EPROVIDERENV" not in stderr, stderr[-2500:]
    assert "key registry is unavailable" not in stderr
    expected = (
        "missing claim sock path"
        if entry in {"spare", "preclaim"}
        else "requires --verbose"
    )
    assert expected in stdout + stderr, (stdout + stderr)[-2500:]


def _startup_binary() -> str:
    binary = os.environ.get("CCPATCH_STARTUP_BINARY")
    if not binary:
        pytest.skip("set CCPATCH_STARTUP_BINARY to a patched native executable")
    return str(Path(binary).resolve())


def _startup_env(home: Path) -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(home),
        "XDG_CONFIG_HOME": str(home),
        "XDG_CACHE_HOME": str(home / "cache"),
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "HTTP_PROXY": "http://127.0.0.1:1",
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "ALL_PROXY": "http://127.0.0.1:1",
        "CLAUDE_CODE_SESSION_KIND": "bg",
    }


def _startup_transport(home: Path) -> str:
    return json.dumps(
        {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:1",
            "ANTHROPIC_API_KEY": "synthetic-test-key",
            "CLAUDE_CONFIG_DIR": str(home),
        }
    )


def _startup_argv() -> list[str]:
    # Stop at argument validation, before a provider request.
    return ["--setting-sources", "", "-p", "--output-format", "stream-json", "test"]


@contextmanager
def _native_process(
    argv: list[str], home: Path, env: dict[str, str]
) -> Iterator[subprocess.Popen[str]]:
    with subprocess.Popen(  # noqa: S603 - Explicit opt-in local binary.
        argv,
        cwd=home,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as process:
        try:
            yield process
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=10)


def _connect_native(path: Path, process: subprocess.Popen[str]) -> socket.socket:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        client = socket.socket(socket.AF_UNIX)
        client.settimeout(20)
        try:
            client.connect(str(path))
            return client
        except (FileNotFoundError, ConnectionRefusedError):
            client.close()
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(
                f"native process exited before socket readiness: {stdout}{stderr}"
            )
        time.sleep(0.02)
    pytest.fail(f"native socket did not become ready: {path}")


@pytest.mark.parametrize(
    "payload",
    [
        True,
        False,
        "invalid-json",
        "null",
        '{"ANTHROPIC_API_KEY":42}',
        '{"UNTRUSTED_KEY":"value"}',
    ],
    ids=[
        "payload",
        "missing-payload",
        "invalid-json",
        "null",
        "invalid-value",
        "unknown-key",
    ],
)
@pytest.mark.parametrize(
    "reject_first", [False, True], ids=["claim", "reject-then-claim"]
)
def test_provider_207_real_spare_claim(
    payload: bool | str, reject_first: bool, tmp_path: Path
) -> None:
    """Cross the native settings, prewarm, Unix claim, and main-init boundaries."""
    binary = _startup_binary()
    env = _startup_env(tmp_path)
    env["CLAUDE_BG_CLAIM_AUTH"] = "synthetic-claim-auth"
    claim_env = (
        {
            "CLAUDE_CODE_PROVIDER_ENV_TRANSIENT": payload
            if isinstance(payload, str)
            else _startup_transport(tmp_path)
        }
        if payload
        else {}
    )
    # Keep the Unix socket path below the platform limit.
    with tempfile.TemporaryDirectory(prefix="cc-claim-") as directory:
        path = Path(directory) / "claim.sock"
        with _native_process(
            [binary, "--bg-spare", str(path)], tmp_path, env
        ) as process:
            assert "CLAUDE_CODE_PROVIDER_ENV_TRANSIENT" not in env
            if reject_first:
                with _connect_native(path, process) as rejected:
                    frame = {
                        "cwd": str(tmp_path),
                        "env": {"CLAUDE_CODE_PROVIDER_ENV_TRANSIENT": "invalid-json"},
                        "argv": _startup_argv(),
                        "auth": "wrong-claim-auth",
                    }
                    rejected.sendall((json.dumps(frame) + "\n").encode())
                    rejected.shutdown(socket.SHUT_WR)
                    assert rejected.recv(4096) == b""
                assert process.poll() is None, (
                    "rejected auth must not consume the spare"
                )
            with _connect_native(path, process) as client:
                assert process.poll() is None
                frame = {
                    "cwd": str(tmp_path),
                    "env": claim_env,
                    "argv": _startup_argv(),
                    "auth": env["CLAUDE_BG_CLAIM_AUTH"],
                }
                # DOo accepts one authenticated, newline-delimited JSON claim.
                client.sendall((json.dumps(frame) + "\n").encode())
                client.shutdown(socket.SHUT_WR)
            stdout, stderr = process.communicate(timeout=20)
    output = stdout + stderr
    assert process.returncode != 0
    assert "key registry is unavailable" not in output, output[-2500:]
    if payload is True:
        assert "EPROVIDERENV" not in output, output[-2500:]
        assert "requires --verbose" in output, output[-2500:]
    else:
        assert "EPROVIDERENV" in output, output[-2500:]
        assert "post-claim init failed" in output, output[-2500:]
        assert "requires --verbose" not in output, output[-2500:]


def _recv_exact(client: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = client.recv(size - len(result))
        assert chunk, "PTY socket closed before the exit frame"
        result.extend(chunk)
    return bytes(result)


def _native_208_bindings(source: str, version: int) -> str:
    """Normalize .209-.211 bindings only; retain captured native function bodies."""
    if version not in {209, 210, 211}:
        return source
    names = {
        'F8e': '$8e',
        'FI': '$I',
        'n3a': '$ja',
        'lfp': '$pp',
        'P8n': 'A8n',
        'ZE_': 'AE_',
        'rA_': 'CE_',
        'HRe': 'CRe',
        'Rkt': 'Ckt',
        'Mpe': 'Dpe',
        'Lr': 'Dr',
        'QE_': 'EE_',
        'wqe': 'Eqe',
        'Pzo': 'Ezo',
        'Xon': 'Fon',
        'mfp': 'Gpp',
        'nA_': 'HE_',
        'kt': 'Ht',
        'q6i': 'I6i',
        'U8o': 'I8o',
        'J9c': 'I9c',
        'M_t': 'I_t',
        'QH': 'JH',
        'O7b': 'JKb',
        'Xo': 'Jo',
        'Efp': 'Jpp',
        'w5y': 'K3y',
        'iLo': 'KRo',
        'Yle': 'Kle',
        'bfp': 'Kpp',
        'Rf': 'Lf',
        'Gzn': 'Lzn',
        'OIe': 'MIe',
        '$We': 'MWe',
        'O_': 'M_',
        '$it': 'Mit',
        'sfp': 'Mpp',
        'A7b': 'NKb',
        '$f': 'Nf',
        'cfp': 'Npp',
        'FUe': 'OUe',
        'r3a': 'Oja',
        'afp': 'Opp',
        'zuo': 'Ouo',
        'BMt': 'PMt',
        'Fwt': 'Pwt',
        'xD_': 'QL_',
        '$Os': 'SOs',
        'LQo': 'SQo',
        'tA_': 'TE_',
        'Hk': 'Tk',
        'Qon': 'Uon',
        'pfp': 'Upp',
        'Ytr': 'Utr',
        'E5y': 'V3y',
        'nLo': 'VRo',
        'n4r': 'VUr',
        'ji': 'Vi',
        'p3a': 'Vja',
        'yfp': 'Vpp',
        'hfp': 'Wpp',
        'aVr': 'X8r',
        'CD_': 'XL_',
        'J_': 'X_',
        'vfp': 'Xpp',
        'hQu': 'YJu',
        'sLo': 'YRo',
        'Sfp': 'Ypp',
        'k5y': 'Z3y',
        'lxr': 'ZHr',
        'g7s': 'ZKs',
        'bCe': '_Ce',
        'kZr': '_Zr',
        'A_t': '__t',
        'w7s': 'a7s',
        'RQo': 'bQo',
        'IZr': 'bZr',
        'C7s': 'c7s',
        'uHe': 'cHe',
        'PUd': 'cUd',
        'dye': 'cye',
        'x7s': 'd7s',
        'yDt': 'dDt',
        'OUd': 'dUd',
        'Scr': 'dcr',
        'l7t': 'e7t',
        'SQu': 'eQu',
        'Doy': 'eoy',
        'rue': 'eue',
        'dwo': 'ewo',
        'I7s': 'f7s',
        'UAp': 'gAp',
        'M5y': 'i5y',
        'g_n': 'i_n',
        'art': 'irt',
        'nus': 'jcs',
        'Zon': 'jon',
        'ffp': 'jpp',
        'Xtr': 'jtr',
        'Zuo': 'juo',
        'B8o': 'k8o',
        'iA_': 'kE_',
        'Ia': 'ka',
        'T7s': 'l7s',
        'DUd': 'lUd',
        'o0e': 'n0e',
        'd3t': 'n3t',
        'p3t': 'o3t',
        'kUd': 'oUd',
        'k7s': 'p7s',
        '$Ud': 'pUd',
        'dr': 'pr',
        'rVr': 'q8r',
        'rLo': 'qRo',
        'd3a': 'qja',
        'gfp': 'qpp',
        'a0t': 'r0t',
        'hcr': 'scr',
        'r2': 't2',
        'H7s': 'u7s',
        'MUd': 'uUd',
        'bcr': 'ucr',
        'dt': 'ut',
        'JE_': 'vE_',
        'LZr': 'vZr',
        'eA_': 'wE_',
        'Ae': 'we',
        'kre': 'xre',
        'b$': 'y$',
        '_4': 'y4',
        '__e': 'y_e',
        'A5y': 'z3y',
        'oVr': 'z8r',
        'oLo': 'zRo',
        'Yet': 'zet',
        '_fp': 'zpp',
        'bs': 'ws',
        'Iz': 'Rz',
        'Kon': '$on',
        'ye': 'ge',
        'Wt': 'qt',
        'Wtt': 'jtt',
        'o4': 'n4',
        'O_t': 'R_t',
        '_cr': 'ccr',
        'T5y': 'Y3y',
        'C5y': 'X3y',
        'H5y': 'J3y',
        'x5y': 'Q3y',
        'P5y': 'o5y',
        'O5y': 's5y',
        'cUt': 'tUt',
        'sd': 'ld',
        'LUd': 'aUd',
        'gS': 'hS',
        'di': 'mi',
    }
    if version == 210:
        names = {
            'Dqe': '$8e',
            'DI': '$I',
            'EWa': '$ja',
            'ABd': '$pp',
            'fzn': 'A8n',
            'aVy': 'AE_',
            'uVy': 'CE_',
            'rLe': 'CRe',
            'RDt': 'Ckt',
            'nfe': 'Dpe',
            'Lr': 'Dr',
            'sVy': 'EE_',
            'VWe': 'Eqe',
            'rYo': 'Ezo',
            'VQr': 'Fon',
            'kBd': 'Gpp',
            'dVy': 'HE_',
            'vt': 'Ht',
            'VQi': 'I6i',
            'mzo': 'I8o',
            'mJc': 'I9c',
            '$bt': 'I_t',
            'nx': 'JH',
            'PoS': 'JKb',
            'ti': 'Jo',
            '$Bd': 'Jpp',
            'cJg': 'K3y',
            '_wo': 'KRo',
            'Uce': 'Kle',
            'PBd': 'Kpp',
            'Yf': 'Lf',
            'pso': 'Lzn',
            'dRe': 'MIe',
            'Lqe': 'MWe',
            'P_': 'M_',
            'Olt': 'Mit',
            'EBd': 'Mpp',
            'EoS': 'NKb',
            'Of': 'Nf',
            'wBd': 'Npp',
            'b4e': 'OUe',
            'SWa': 'Oja',
            'vBd': 'Opp',
            'ywo': 'Ouo',
            'ODt': 'PMt',
            'ZTt': 'Pwt',
            'rO_': 'QL_',
            'cls': 'SOs',
            'nti': 'SQo',
            'cVy': 'TE_',
            'Hk': 'Tk',
            'YQr': 'Uon',
            'HBd': 'Upp',
            'Yar': 'Utr',
            'aJg': 'V3y',
            'mwo': 'VRo',
            '$Wr': 'VUr',
            'Ji': 'Vi',
            'IWa': 'Vja',
            'LBd': 'Vpp',
            'IBd': 'Wpp',
            'RXr': 'X8r',
            'eO_': 'XL_',
            'by': 'X_',
            'OBd': 'Xpp',
            'SBd': 'YJu',
            'bwo': 'YRo',
            'MBd': 'Ypp',
            'mJg': 'Z3y',
            'Zkr': 'ZHr',
            'Nks': 'ZKs',
            'i0e': '_Ce',
            'UVr': '_Zr',
            'Cbt': '__t',
            'zks': 'a7s',
            'rti': 'bQo',
            'jVr': 'bZr',
            'Yks': 'c7s',
            'cCe': 'cHe',
            'Rzu': 'cUd',
            'f_e': 'cye',
            'Jks': 'd7s',
            'Vkt': 'dDt',
            'Dzu': 'dUd',
            'rrr': 'dcr',
            'Jer': 'e7t',
            'BFd': 'eQu',
            'oVy': 'eoy',
            'jce': 'eue',
            'Ulo': 'ewo',
            'Zks': 'f7s',
            'Lvp': 'gAp',
            'SJg': 'i5y',
            '_Sn': 'i_n',
            'Tot': 'irt',
            'Rks': 'jcs',
            'XQr': 'jon',
            'xBd': 'jpp',
            'Xar': 'jtr',
            'QSo': 'juo',
            'fzo': 'k8o',
            'fVy': 'kE_',
            'Da': 'ka',
            'Kks': 'l7s',
            'Izu': 'lUd',
            'z0e': 'n0e',
            '_5t': 'n3t',
            'b5t': 'o3t',
            'Czu': 'oUd',
            'Qks': 'p7s',
            'Pzu': 'pUd',
            'ar': 'pr',
            'NQr': 'q8r',
            'fwo': 'qRo',
            'kWa': 'qja',
            'RBd': 'qpp',
            'txt': 'r0t',
            'Jtr': 'scr',
            'm2': 't2',
            'Xks': 'u7s',
            'Lzu': 'uUd',
            'trr': 'ucr',
            'ut': 'ut',
            'iVy': 'vE_',
            'WVr': 'vZr',
            'lVy': 'wE_',
            'Ae': 'we',
            'sne': 'xre',
            'O0': 'y$',
            'O4': 'y4',
            'fye': 'y_e',
            'lJg': 'z3y',
            'BQr': 'z8r',
            'gwo': 'zRo',
            'qtt': 'zet',
            'DBd': 'zpp',
            'Ts': 'ws',
            'Sz': 'Rz',
            'WQr': '$on',
            'he': 'ge',
            'qt': 'qt',
            'Ait': 'jtt',
            'L$': 'n4',
            'Nbt': 'R_t',
            'err': 'ccr',
            'uJg': 'Y3y',
            'dJg': 'X3y',
            'pJg': 'J3y',
            'fJg': 'Q3y',
            'bJg': 'o5y',
            'EJg': 's5y',
            'h4t': 'tUt',
            'ad': 'ld',
            'kzu': 'aUd',
            'yS': 'hS',
            'gi': 'mi',
            'hJ': '$J',
            'Mt': 'Ft',
            '$R': 'FR',
            'FM': 'LM',
            'iS': 'ey',
            'bS': 'FC',
            'yc': 'vc',
            'bg': 'Sg',
            '_g': 'bg',
            'Re': 'Le',
            'Se': 've',
            'xh': 'Rh',
            'Lj': 'bj',
            'jX': 'wJ',
            'we': 'Te',
            'Ie': 'Re',
            'le': 'ie',
            'Ft': 'Nt',
            '$': 'O',
            'ue': 'le',
        }
    if version == 211:
        names = {
            'Bqe': '$8e',
            'VI': '$I',
            'uVa': '$ja',
            'PNd': '$pp',
            'oXn': 'A8n',
            'w6y': 'AE_',
            'H6y': 'CE_',
            'TLe': 'CRe',
            'VDt': 'Ckt',
            'Efe': 'Dpe',
            'Ir': 'Dr',
            'A6y': 'EE_',
            '_6e': 'Eqe',
            'HJo': 'Ezo',
            'cZr': 'Fon',
            'UNd': 'Gpp',
            'x6y': 'HE_',
            'Ct': 'Ht',
            'Yes': 'I6i',
            'U7o': 'I8o',
            'pul': 'I9c',
            'gSt': 'I_t',
            'Ex': 'JH',
            'JlS': 'JKb',
            'Qo': 'Jo',
            'YNd': 'Jpp',
            'pey': 'K3y',
            'Uwo': 'KRo',
            'Xce': 'Kle',
            'VNd': 'Kpp',
            'nm': 'Lf',
            'nlo': 'Lzn',
            'MRe': 'MIe',
            'Fqe': 'MWe',
            'Ch': 'M_',
            'zlt': 'Mit',
            'LNd': 'Mpp',
            'NlS': 'NKb',
            'Yf': 'Nf',
            'MNd': 'Npp',
            'V4e': 'OUe',
            'cVa': 'Oja',
            'DNd': 'Opp',
            'Bwo': 'Ouo',
            'JDt': 'PMt',
            'D0t': 'Pwt',
            'ZN_': 'QL_',
            'Tni': 'SQo',
            'C6y': 'TE_',
            '$k': 'Tk',
            'pZr': 'Uon',
            'NNd': 'Upp',
            'Jar': 'Utr',
            'uey': 'V3y',
            '$wo': 'VRo',
            'Q6r': 'VUr',
            'zi': 'Vi',
            'bVa': 'Vja',
            'WNd': 'Vpp',
            'jNd': 'Wpp',
            'qXr': 'X8r',
            'JN_': 'XL_',
            'Py': 'X_',
            'KNd': 'Xpp',
            'RNd': 'YJu',
            'jwo': 'YRo',
            'zNd': 'Ypp',
            'yey': 'Z3y',
            'fRr': 'ZHr',
            'txs': 'ZKs',
            'w0e': '_Ce',
            'uzr': '_Zr',
            'iSt': '__t',
            'uxs': 'a7s',
            'wni': 'bQo',
            'dzr': 'bZr',
            'pxs': 'c7s',
            'SCe': 'cHe',
            'mYu': 'cUd',
            'y_e': 'cye',
            'mxs': 'd7s',
            'IIt': 'dDt',
            'gYu': 'dUd',
            'Urr': 'dcr',
            'Dtr': 'e7t',
            'J1d': 'eQu',
            '_6y': 'eoy',
            'Jce': 'eue',
            'Vuo': 'ewo',
            'gxs': 'f7s',
            'o0p': 'gAp',
            'Aey': 'i5y',
            'cvn': 'i_n',
            'nit': 'irt',
            'KHs': 'jcs',
            'fZr': 'jon',
            'BNd': 'jpp',
            'Qar': 'jtr',
            'TEo': 'juo',
            'B7o': 'k8o',
            'I6y': 'kE_',
            'Ha': 'ka',
            'dxs': 'l7s',
            'fYu': 'lUd',
            'oCe': 'n0e',
            'oGt': 'n3t',
            'iGt': 'o3t',
            'cYu': 'oUd',
            'hxs': 'p7s',
            'yYu': 'pUd',
            'dr': 'pr',
            'tZr': 'q8r',
            'Owo': 'qRo',
            '_Va': 'qja',
            'GNd': 'qpp',
            'Orr': 'scr',
            'w2': 't2',
            'fxs': 'u7s',
            'hYu': 'uUd',
            'Brr': 'ucr',
            'ut': 'ut',
            'b6y': 'vE_',
            'fzr': 'vZr',
            'T6y': 'wE_',
            'Ae': 'we',
            'Sne': 'xre',
            'J0': 'y$',
            'K4': 'y4',
            'Cye': 'y_e',
            'dey': 'z3y',
            'nZr': 'z8r',
            'Fwo': 'zRo',
            'Trt': 'zet',
            'qNd': 'zpp',
            'As': 'ws',
            'X5': 'Rz',
            'aZr': '$on',
            'me': 'ge',
            'zt': 'qt',
            'rst': 'jtt',
            'z$': 'n4',
            'ySt': 'R_t',
            'fey': 'Y3y',
            'mey': 'X3y',
            'hey': 'J3y',
            'gey': 'Q3y',
            'vey': 'o5y',
            'wey': 's5y',
            'ejt': 'tUt',
            'pd': 'ld',
            'pYu': 'aUd',
            'RS': 'hS',
            'mi': 'mi',
            'EJ': '$J',
            'Ut': 'Ft',
            'WR': 'FR',
            'qM': 'LM',
            'SS': 'ey',
            'DS': 'FC',
            'dc': 'vc',
            'Mg': 'bg',
            'Le': 'Le',
            'ye': 've',
            'Bh': 'Rh',
            'Kj': 'bj',
            'tJ': 'wJ',
            've': 'Te',
            'Ie': 'Re',
            'le': 'ie',
            'jt': 'Nt',
            'ce': 'le',
            'xe': 'ke',
            'N0': 'v0',
            'Lh': 'Eh',
            'O': 'O',
            'Lxt': 'r0t',
            'uus': 'SOs',
            'Frr': 'ccr',
            'ag': 'Sg',
        }
    # Move unrelated captured bindings away from the .208 harness names.
    collisions = set(names.values()) - names.keys()
    names.update({name: f"_cc{version}Original_{name}" for name in collisions})
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_$][\w$]*')

    def normalize(fragment: str) -> str:
        return tokens.sub(
            lambda match: (
                match[0]
                if match.start()
                and fragment[match.start() - 1] == "."
                and fragment[max(0, match.start() - 3) : match.start()] != "..."
                else (
                    match[0]
                    if match[0] == "$"
                    and fragment[match.end() : match.end() + 1] == "{"
                    else names.get(match[0], match[0])
                )
            ),
            fragment,
        )

    return "".join(
        normalize(fragment)
        for fragment in re.split(
            r"(?=(?:async )?function [\w$]+\(|class [\w$]+\{)", source
        )
    )


@pytest.mark.parametrize("version", [208, 209, 210, 211])
def test_provider_claim_bookkeeping_precedes_handoff(version: int) -> None:
    """A claimed pool slot does not prove that a spare received its claim."""
    source = ROOT / f"build/sweep-resume/2.1.{version}/linux-x64/original.js"
    runtime = shutil.which("node")
    if not source.is_file() or runtime is None:
        pytest.skip("requires captured .208/.209 source and node")
    original = _native_208_bindings(source.read_text(), version)
    patched = _provider_env_207(BACKGROUND_PROVIDER_ENV_198).apply(original)
    patched_start = patched.index("function Oja(")
    native = patched[patched_start : patched.index("function NKb(", patched_start)]
    assert "t.claimed=!0;" in native
    assert "_ccProviderEnv" in native
    script = (
        'const assert=require("node:assert/strict");'
        + 'let slot={claimed:false},sent=false;'
        + 'const eue={claim(){assert.equal(slot.claimed,true);throw Error("early-exit")}};'
        + 'const u7s=()=>{sent=true;throw Error("unexpected transport")};'
        + native
        + 'assert.throws(()=>Oja({},slot,()=>{},()=>{}),/early-exit/);'
        + 'assert.equal(slot.claimed,true);assert.equal(sent,false);'
    )
    result = subprocess.run(  # noqa: S603 - Native bookkeeping with an early-exit adapter.
        [runtime, "-e", script], capture_output=True, text=True, timeout=20, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _native_208_pty_launch(
    binary: str, path: Path, env: dict[str, str], drop_transport: bool, version: int
) -> tuple[list[str], dict[str, str]]:
    """Extract the native wrapper and PTY launcher, not replacement argv logic."""
    source = ROOT / f"build/sweep-resume/2.1.{version}/linux-x64/original.js"
    runtime = shutil.which("node")
    launcher = shutil.which("env")
    if not source.is_file() or runtime is None or launcher is None:
        pytest.skip("requires captured .208/.209 source, node, and env")
    original = _native_208_bindings(source.read_text(), version)
    wrapper = [launcher]
    if drop_transport:
        wrapper += ["-u", "CLAUDE_CODE_PROVIDER_ENV_TRANSIENT"]
    fragments = []
    for start, end in [
        ("function y$(", "async function e7t("),
        ("function c7s(", "function Vpp("),
    ]:
        offset = original.index(start)
        fragments.append(original[offset : original.index(end, offset)])
    wrapper_match = re.match(
        r"function [\w$]+\([^)]*\)\{return (?P<wrapper>[\w$]+)\(", fragments[0]
    )
    assert wrapper_match is not None, "native launcher must call its wrapper adapter"
    wrapper_adapter = wrapper_match["wrapper"]
    script = (
        'const assert=require("node:assert/strict");'
        + f"const binary={json.dumps(binary)},wrapper={json.dumps(wrapper)};"
        + f"const options={json.dumps({'cwd': env['HOME'], 'env': env, 'ptySock': str(path), 'cols': 80, 'rows': 24})};"
        + 'let launch;const X_=()=>wrapper,Ft=()=>"linux",FR=p=>p+".err";'
        + 'const ey=()=>true,Lzn=()=>{throw Error("must pin the host binary")};'
        + 'const process={execPath:binary,stdout:globalThis.process.stdout};'
        + 'const Bun={file:p=>p,spawn:(argv,options)=>{launch={argv,env:options.env};'
        + 'return {pid:1234,unref(){}}}},qRo=()=>({});'
        + "".join(fragments)
        + f"c7s()(binary,{json.dumps(_startup_argv())},options);"
        + 'assert.equal(launch.env,options.env);'
        + 'const already={cmd:wrapper[0],prefixArgs:[],target:"native"};'
        + f"const wrapNative={wrapper_adapter};"
        + 'assert.strictEqual(wrapNative(already),already);'
        + 'assert.equal(wrapNative({cmd:binary,prefixArgs:[],target:"native"}).target,"native");'
        + 'process.stdout.write(JSON.stringify(launch));'
    )
    result = subprocess.run(  # noqa: S603 - Captured launcher with a spawn recorder.
        [runtime, "-e", script], capture_output=True, text=True, timeout=20, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    launch = json.loads(result.stdout)
    assert launch["argv"][: len(wrapper) + 1] == [*wrapper, binary]
    assert (
        launch["env"]["CLAUDE_CODE_PROVIDER_ENV_TRANSIENT"]
        == env["CLAUDE_CODE_PROVIDER_ENV_TRANSIENT"]
    )
    return launch["argv"], launch["env"]


@pytest.mark.parametrize(
    "entry", ["payload", "missing-payload", "wrapper-forward", "wrapper-drop"]
)
@pytest.mark.parametrize("version", [208, 209, 210, 211])
def test_provider_207_real_pty_cold_child(
    entry: str, version: int, tmp_path: Path
) -> None:
    """Use Bun.Terminal and native framed output, without a daemon supervisor."""
    binary = _startup_binary()
    env = _startup_env(tmp_path)
    payload = entry != "missing-payload"
    if payload:
        env["CLAUDE_CODE_PROVIDER_ENV_TRANSIENT"] = _startup_transport(tmp_path)
    output = bytearray()
    controls: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="cc-pty-") as directory:
        path = Path(directory) / "pty.sock"
        argv = [binary, "--bg-pty-host", str(path), "80", "24", "--", binary]
        argv += _startup_argv()
        if entry.startswith("wrapper-"):
            argv, env = _native_208_pty_launch(
                binary, path, env, entry == "wrapper-drop", version
            )
        with _native_process(argv, tmp_path, env) as process:
            with _connect_native(path, process) as client:
                while True:
                    # uAo uses a four-byte BE length and one-byte frame kind.
                    header = _recv_exact(client, 5)
                    size = int.from_bytes(header[:4], "big")
                    assert size <= 1048576
                    body = _recv_exact(client, size)
                    if header[4] == 0:
                        output.extend(body)
                    else:
                        assert header[4] == 1
                        control = json.loads(body)
                        controls.append(control)
                        if control["t"] == "exit":
                            break
            stdout, stderr = process.communicate(timeout=20)
    text = output.decode(errors="replace")
    assert controls[0]["t"] == "hello"
    assert controls[0]["replPid"] != process.pid
    assert controls[-1]["code"] != 0
    # The unref'ed host can exit zero after it reports a nonzero child exit.
    assert process.returncode in {0, controls[-1]["code"]}
    assert "key registry is unavailable" not in text + stderr
    if payload and entry != "wrapper-drop":
        assert "EPROVIDERENV" not in text + stdout + stderr, (text + stderr)[-2500:]
        assert "requires --verbose" in text, text[-2500:]
    else:
        assert "EPROVIDERENV" in text, text[-2500:]
        assert "requires --verbose" not in text, text[-2500:]


@pytest.mark.parametrize(
    "platform", ["linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64"]
)
@pytest.mark.parametrize("version", [207, 208, 209, 210, 211])
def test_provider_207_settings_boundary(platform: str, version: int) -> None:
    path = ROOT / f"build/sweep-resume/2.1.{version}/{platform}/original.js"
    if not path.is_file():
        pytest.skip("requires captured pristine .207/.208/.209 source")
    runtime = shutil.which("node")
    if runtime is None:
        pytest.skip("requires node")
    original = path.read_text()
    patched = _provider_env_207(BACKGROUND_PROVIDER_ENV_198).apply(original)
    snapshot = _PROVIDER_ENV_SNAPSHOT.search(original)
    assert snapshot is not None
    registry_probe = re.sub(
        r"function _ccProviderKeys\(\)\{if\([^)]*\)",
        _initialize_provider_registry,
        original + _replace_provider_snapshot(snapshot),
    )
    call = re.search(
        r"function _ccProviderKeys\(\)\{if\(\(([\w$]+)\(\),", registry_probe
    )
    assert call is not None
    serialized_keys = json.loads(_provider_serialized_keys(original))
    assert "CLAUDE_CONFIG_DIR" in serialized_keys
    assert "ANTHROPIC_API_KEY" in serialized_keys
    module = re.search(rf"var {re.escape(call[1])}=([\w$]+)\(\(\)=>\{{", original)
    assert module is not None
    end = original.index("});", module.end()) + 3
    body = original[module.end() : end - 3]
    sources = _provider_key_sources(original)
    assert f"{sources[0]}=new Set(" in body
    wrapper = re.search(
        rf"(?<![\w$]){re.escape(module[1])}=\(e,t\)=>\(\)=>\(e&&\(t=e\(e=0\)\),t\)",
        original,
    )
    assert wrapper is not None
    dependencies = re.match(r"(?:[\w$]+\(\);)+", body)
    assert dependencies is not None
    stubs = "".join(
        f"function {name}(){{calls++;}}"
        for name in re.findall(r"([\w$]+)\(\)", dependencies[0])
    )
    script = (
        'const assert=require("node:assert/strict");let calls=0;'
        + f"var {wrapper[0]};"
        + "".join(f"var {name}=[];" for name in sources[1:])
        + stubs
        + original[module.start() : end]
        + f"{call[1]}();let first={sources[0]},count=calls;{call[1]}();"
        + f"assert.strictEqual(first,{sources[0]});assert.equal(calls,count);"
        + 'assert.ok(first.has("CLAUDE_CONFIG_DIR"));'
    )
    result = subprocess.run(  # noqa: S603 - Execute captured registry code with inert dependencies.
        [runtime, "-e", script], capture_output=True, text=True, check=False, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr
    snapshot = _PROVIDER_ENV_SNAPSHOT.search(original)
    assert snapshot is not None
    helpers = _replace_provider_snapshot(snapshot)
    patches = _provider_env_207(BACKGROUND_PROVIDER_ENV_198).patches
    install = next(
        p for p in patches if p.name == "install-provider-native-policy-boundary"
    )
    assert isinstance(install.replacement, str)
    helpers = install.pattern.sub(install.replacement, helpers)
    preserve = next(
        p for p in patches if p.name == "preserve-provider-transport-in-pty-host"
    )
    helpers = preserve.pattern.sub(preserve.replacement, helpers)
    host_script = (
        'const assert=require("node:assert/strict");'
        'process.argv[2]="--bg-pty-host";'
        'process.env.CLAUDE_CODE_SESSION_KIND="bg";'
        'const transport=JSON.stringify({ANTHROPIC_API_KEY:"synthetic"});'
        'process.env.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT=transport;'
        + helpers
        + '_ccProviderInitialize();'
        + 'assert.equal(_ccProviderWorkerEnv,null);'
        + 'assert.equal(process.env.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT,transport);'
        + 'process.argv[2]="--worker";'
        + 'assert.deepEqual(_ccProviderCaptureTransport(),{ANTHROPIC_API_KEY:"synthetic"});'
        + 'assert.equal(process.env.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT,undefined);'
    )
    result = subprocess.run(  # noqa: S603 - Run captured transport with synthetic credentials.
        [runtime, "-e", host_script],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for name, count in [
        ("initialize-provider-before-native-settings", 2),
        ("filter-provider-settings-with-native-policy", 1),
    ]:
        patch = next(p for p in patches if p.name == name)
        assert len(list(patch.pattern.finditer(original))) == count
    initialize = next(
        p for p in patches if p.name == "initialize-provider-before-native-settings"
    )
    filter_patch = next(
        p for p in patches if p.name == "filter-provider-settings-with-native-policy"
    )
    native_filter = filter_patch.pattern.search(original)
    assert native_filter is not None
    assert native_filter["native"] in patched
    assert not isinstance(filter_patch.replacement, str)
    filter_code = filter_patch.replacement(native_filter)
    assert isinstance(filter_code, str)
    filter_names = re.findall(r"([\w$]+)\(", native_filter["native"])
    filter_stubs = "".join(
        f"function {name}(env){{return env??{{}}}}" for name in filter_names
    )
    if version >= 208:
        launcher_start = original.index(f"function {filter_names[-1]}(")
        launcher_end = original.index("function ", launcher_start + 9)
        launcher_filter = original[launcher_start:launcher_end]
        scope_set = re.search(r"!([\w$]+)\.has\(t\)", launcher_filter)
        key_set = re.search(r"!([\w$]+)\.has\(n\.toUpperCase\(\)\)", launcher_filter)
        warned_set = re.search(r"!([\w$]+)\.has\(n\)", launcher_filter)
        logger = re.search(
            r'\.add\(n\),([\w$]+)\(`[^`]+`,\{level:"warn"\}\)', launcher_filter
        )
        assert scope_set and key_set and warned_set and logger
        launcher_script = (
            'const assert=require("node:assert/strict");'
            + f"const {logger[1]}=()=>{{}};"
            + f'const {scope_set[1]}=new Set(["projectSettings","localSettings"]);'
            + f'const {key_set[1]}=new Set(["CLAUDE_CODE_PROCESS_WRAPPER"]);'
            + f'const {warned_set[1]}=new Set();'
            + launcher_filter
            + 'const env={CLAUDE_CODE_PROCESS_WRAPPER:"/trusted/launcher",OTHER:"kept"};'
            + f'for(const scope of ["projectSettings","localSettings"])assert.deepEqual({filter_names[-1]}(env,scope),{{OTHER:"kept"}});'
            + f'for(const scope of ["userSettings","policySettings"])assert.strictEqual({filter_names[-1]}(env,scope),env);'
        )
        result = subprocess.run(  # noqa: S603 - Captured scope filter with inert logging.
            [runtime, "-e", launcher_script],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    hidden_filter = filter_names[-2] if version >= 208 else filter_names[-1]
    hidden_start = original.index(f"function {hidden_filter}(")
    hidden_end = original.index("function ", hidden_start + 9)
    filter_stubs = filter_stubs.replace(
        f"function {hidden_filter}(env){{return env??{{}}}}",
        original[hidden_start:hidden_end],
    )
    native_initializers: list[str] = []
    initializer_tests: list[str] = []
    for match in initialize.pattern.finditer(original):
        end = min(
            original.index("function ", match.end()),
            original.index("var ", match.end()),
        )
        body = original[match.start() : end]
        patched_body = initialize.pattern.sub(initialize.replacement, body)
        native_initializers.append(patched_body)
        initializer_tests.append(
            f"assert.throws(()=>{match['apply']}(),{{code:'EPROVIDERENV'}});"
        )
    policy_getter = match["settings"]
    raw_policy_test = (
        f"function {policy_getter}(){{return {{env:{{ANTHROPIC_BASE_URL:'https://managed'}}}}}}"
        + "".join(native_initializers)
        + "process.env.ANTHROPIC_API_KEY='untouched';"
        + "".join(initializer_tests)
        + "assert.equal(process.env.ANTHROPIC_API_KEY,'untouched');"
        + "assert.equal(_ccProviderInitialized,false);"
        + filter_stubs
        + filter_code
        + "process.env.ANTHROPIC_UNIX_SOCKET='/synthetic/host.sock';"
        + f"assert.deepEqual({hidden_filter}({{ANTHROPIC_BASE_URL:'https://managed'}}),{{}});"
        + f"assert.throws(()=>{native_filter['filter']}({{ANTHROPIC_BASE_URL:'https://managed'}},'policySettings'),{{code:'EPROVIDERENV'}});"
        + "assert.equal(process.env.ANTHROPIC_API_KEY,'untouched');"
    )
    admin_match = re.search(
        r'function ([\w$]+)\(\)\{([\w$]+)\(\{sonnet:([\w$]+)\.ANTHROPIC_DEFAULT_SONNET_MODEL'
        r'!==void 0&&!([\w$]+)\("sonnet"\),opus:[^}]+\}\)\}',
        original,
    )
    assert admin_match is not None
    residue_start = original.index(f"function {admin_match[4]}(")
    residue_end = min(
        original.index("function ", residue_start + 9),
        original.index("var ", residue_start + 9),
    )
    admin_code = (
        f"var {admin_match[3]}=process.env;let admin;"
        f"function {admin_match[2]}(value){{admin=value}}"
        + original[residue_start:residue_end]
        + admin_match[0]
    )
    registry = "".join(
        f'var {name}=["ANTHROPIC_API_KEY","ANTHROPIC_BASE_URL","ANTHROPIC_DEFAULT_OPUS_MODEL","CLAUDE_CODE_3P_PROBE_WROTE_OPUS_DEFAULT"];'
        for name in _provider_key_sources(original)
    )
    script = (
        'const assert=require("node:assert/strict");'
        'process.env.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT=JSON.stringify({'
        'ANTHROPIC_API_KEY:"requester",ANTHROPIC_BASE_URL:"https://requester",'
        'ANTHROPIC_DEFAULT_OPUS_MODEL:null});'
        + '_ccProviderInitialize();_ccProviderValidateManaged({},"policySettings");'
        + 'assert.deepEqual(_ccProviderFilterSettings({EARLY:"ok"},"userSettings"),{EARLY:"ok"});'
        + helpers
        + registry
        + raw_policy_test
        + admin_code
        + '''
        assert.equal(process.env.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT,undefined);
        _ccProviderInitialize();
        assert.equal(process.env.ANTHROPIC_API_KEY,"requester");
        assert.deepEqual(_ccProviderFilterSettings({ANTHROPIC_API_KEY:"local",OTHER:"kept"},"userSettings"),{OTHER:"kept"});
        assert.deepEqual(_ccProviderFilterSettings({ANTHROPIC_BASE_URL:"https://requester",OTHER:"managed"},"policySettings"),{ANTHROPIC_BASE_URL:"https://requester",OTHER:"managed"});
        assert.throws(()=>_ccProviderValidateManaged({ANTHROPIC_BASE_URL:"https://managed"},"policySettings"),{code:"EPROVIDERENV"});
        process.env.ANTHROPIC_DEFAULT_OPUS_MODEL="native-fallback";
        process.env.CLAUDE_CODE_3P_PROBE_WROTE_OPUS_DEFAULT="native-fallback";
        _ccProviderInitialize();
        assert.equal(process.env.ANTHROPIC_DEFAULT_OPUS_MODEL,"native-fallback");
        assert.equal(process.env.CLAUDE_CODE_3P_PROBE_WROTE_OPUS_DEFAULT,"native-fallback");
        _ccProviderWorkerEnv=Object.freeze({..._ccProviderWorkerEnv,ANTHROPIC_DEFAULT_OPUS_MODEL:"requester-explicit"});
        _ccProviderInitialized=false;
        _ccProviderInitialize();
        NATIVE_ADMIN();
        assert.equal(admin.opus,true);
        assert.throws(()=>_ccProviderInitialize({ANTHROPIC_DEFAULT_OPUS_MODEL:"managed-explicit"}),{code:"EPROVIDERENV"});
        process.env.ANTHROPIC_DEFAULT_OPUS_MODEL="native-fallback";
        process.env.CLAUDE_CODE_3P_PROBE_WROTE_OPUS_DEFAULT="native-fallback";
        _ccProviderInitialize();
        NATIVE_ADMIN();
        assert.equal(admin.opus,false);
        _ccProviderWorkerEnv=null;
        _ccProviderInitialized=false;
        process.env.CLAUDE_CODE_SESSION_KIND="bg";
        _ccProviderAwaitingClaim=true;
        _ccProviderInitialize();
        assert.equal(_ccProviderInitialized,false);
        _ccProviderAwaitingClaim=false;
        assert.throws(()=>_ccProviderInitialize(),{code:"EPROVIDERENV"});
        _ccProviderPtyHost=true;
        _ccProviderInitialize();
        assert.equal(_ccProviderInitialized,false);
        _ccProviderPtyHost=false;
        _ccProviderWorkerEnv={ANTHROPIC_API_KEY:"claimed"};
        _ccProviderInitialize();
        assert.equal(process.env.ANTHROPIC_API_KEY,"claimed");
        assert.equal(_ccProviderInitialized,true);
        _ccProviderWorkerEnv=null;
        const native={ANTHROPIC_API_KEY:"native"};
        assert.equal(_ccProviderFilterSettings(native,"policySettings"),native);
        '''.replace("NATIVE_ADMIN", admin_match[1])
    )
    result = subprocess.run(  # noqa: S603 - Run captured helpers with synthetic credentials.
        [runtime, "-e", script], capture_output=True, text=True, check=False, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _native_lifecycle_207(version: int = 207) -> dict[str, str]:
    path = ROOT / f"build/sweep-resume/2.1.{version}/linux-x64/original.js"
    if not path.is_file():
        pytest.skip(f"requires captured pristine .{version} linux-x64 source")
    original = _native_208_bindings(path.read_text(), version)
    patched = _provider_env_207(BACKGROUND_PROVIDER_ENV_198).apply(original)
    # Map the shared runtime namespace to the isolated harness helper bindings.
    patched = patched.replace("globalThis.__ccpatchRuntime.provider.", "")
    # Rename minified bindings to the harness names. Do not replace native bodies.
    names = {
        'Bce': 'Yce',
        'As': 'xs',
        'wnn': 'yin',
        '_sn': 'uln',
        'Y3_': '_8_',
        'rLp': 'M$p',
        'z3_': 'g8_',
        'Ca': 'Ia',
        'pc': 'uc',
        'oy': 'sy',
        'JRp': 'I$p',
        'Yua': 'cma',
        'ZHe': 'TCe',
        'V3_': 'h8_',
        'nLp': '$$p',
        'j3_': 'd8_',
        'tLp': 'P$p',
        'qua': 'ima',
        'upr': 'Nfr',
        'mMo': '$Oo',
        'wC': 'CC',
        'eLp': 'D$p',
        'ZRp': 'L$p',
        'X0': 'J0',
        'xDs': 'D$s',
        'Uua': 'tma',
        'CRo': 'zDo',
        'Wua': 'oma',
        'jua': 'rma',
        'OSt': 'Svt',
        'uJe': 'JJe',
        'Nt': 'Bt',
        'yM': 'NM',
        'Rgt': '_yt',
        'QRp': 'R$p',
        'mMt': 'a$t',
        'lJ': 'TJ',
        'hMo': 'OOo',
        'Vua': 'sma',
        'zua': 'ama',
        'k_p': 'sAp',
        'gD': 'CD',
        'wta': 'Moa',
        'yke': 'Bke',
        '_gt': 'syt',
        'Qk': 'aI',
        'dV': 'Qj',
        'fr': 'pr',
        'u8e': 'O8e',
        '$le': 'Vle',
        'yXr': 'cQr',
        'XRp': 'k$p',
        'W3_': 'f8_',
        'YRp': 'x$p',
        'G3_': 'p8_',
        'cKo': 'xYo',
        'uKo': 'kYo',
        'jh': 'Wh',
        'JT': 'KA',
        'Gua': 'nma',
        'KRp': 'C$p',
        'Yo': 'Ko',
        'h8e': 'j8e',
        'SQe': 'lZe',
        'U3_': 'u8_',
        'qRp': 'w$p',
        'G$e': 'mOe',
        'S2e': 'N2e',
        'q3_': 'm8_',
        'bx_': 'BP_',
        'yx_': 'OP_',
        'Dr': 'Mr',
        'u$r': 'n1r',
        'S$i': 'V1i',
        'WRp': 'A$p',
        'Re': 'ke',
        'Bm': 'qm',
        'aRo': 'CDo',
        'Ft': 'Nt',
        'GRp': 'E$p',
        'jRp': 'v$p',
        'w_p': 'tAp',
        'qC_': 'dP_',
        'bsn': 'dln',
        'Kua': 'lma',
        'cTr': 'J0r',
        'VUt': 'V4t',
        'Rde': 'Qde',
        'ut': 'ct',
        'qUt': 'q4t',
        'rOa': 'mBa',
        'L4': 'Y4',
        'Tce': 'Mce',
        'umn': 'Xhn',
        'A5o': 'MWo',
        'kF': 'XF',
        'nNb': 'Vjb',
        'zut': 'Fdt',
        'cMt': 'r$t',
        'dOa': 'ABa',
        'd4': 'C4',
        'w_e': 'tbe',
        'vEt': 'iAt',
        'AI': 'II',
        'QTe': 'w0e',
        'Yxd': 'kDd',
        'Jxy': 'bMy',
        'Te': 'be',
        'ZWe': 'C6e',
        'xCs': 'DIs',
        'bir': 'Vsr',
        'gir': 'Gsr',
        'Sir': 'zsr',
        'ekd': 'PDd',
        'tye': 'Iye',
        'j7r': 'PXr',
        'Kyo': 'mSo',
        'vir': 'Ksr',
        'eky': 'HMy',
        'dg': 'fg',
        'sky': 'LMy',
        'tkd': 'MDd',
        'Zxy': 'vMy',
        'U7r': 'DXr',
        '_X': '$X',
        'Qxy': 'SMy',
        'lRt': 'QRt',
        'W7r': '$Xr',
        'rkd': '$Dd',
        'Ee': 'He',
        'Zxd': 'DDd',
        'Ct': 'xt',
        'cpr': 'Ofr',
        'HRo': 'VDo',
        'Ann': 'gin',
        'vnn': 'min',
        'gYe': 'QYe',
        'uOa': 'EBa',
    }
    names.update({"m6o": "L8o", "KSc": "Hwc", "s2n": "e4n"})
    if version >= 208:
        names = {
            'Bce': 'eue',
            'As': 'ws',
            'wnn': 'Fon',
            '_sn': 'Uon',
            'Y3_': 'kE_',
            'rLp': 'Xpp',
            'z3_': 'HE_',
            'Ca': 'ka',
            'pc': 'vc',
            'oy': 'Sg',
            'JRp': 'Wpp',
            'Yua': 'f7s',
            'ZHe': 'cHe',
            'V3_': 'CE_',
            'nLp': 'Jpp',
            'j3_': 'EE_',
            'tLp': 'Ypp',
            'qua': 'c7s',
            'upr': 'jtr',
            'mMo': 'qRo',
            'wC': 'JH',
            'eLp': 'zpp',
            'ZRp': 'Vpp',
            'X0': 'v0',
            'xDs': 'juo',
            'Uua': 'ZKs',
            'CRo': 'zRo',
            'Wua': 'l7s',
            'jua': 'KRo',
            'OSt': 'Pwt',
            'uJe': 'zet',
            'Nt': 'Ft',
            'yM': 'LM',
            'Rgt': 'I_t',
            'QRp': 'qpp',
            'mMt': 'PMt',
            'lJ': '$J',
            'hMo': 'YRo',
            'Vua': 'u7s',
            'zua': 'd7s',
            'k_p': 'eQu',
            'gD': 'FR',
            'wta': 'jcs',
            'yke': 'MIe',
            '_gt': '__t',
            'Qk': '$I',
            'dV': 'y$',
            'fr': 'pr',
            'u8e': 'MWe',
            '$le': 'xre',
            'yXr': 'X8r',
            'XRp': 'Gpp',
            'W3_': 'wE_',
            'YRp': 'jpp',
            'G3_': 'AE_',
            'cKo': 'bQo',
            'uKo': 'SQo',
            'jh': 'Rh',
            'JT': 'M_',
            'Gua': 'a7s',
            'KRp': 'Upp',
            'Yo': 'Jo',
            'h8e': '$8e',
            'SQe': 'irt',
            'U3_': 'vE_',
            'qRp': 'Npp',
            'G$e': 'n0e',
            'S2e': 'OUe',
            'q3_': 'TE_',
            'bx_': 'QL_',
            'yx_': 'XL_',
            'Dr': 'Dr',
            'u$r': 'VUr',
            'S$i': 'I6i',
            'WRp': '$pp',
            'Re': 'Le',
            'Bm': 'Lf',
            'aRo': 'Ouo',
            'Ft': 'Nt',
            'GRp': 'Opp',
            'jRp': 'Mpp',
            'w_p': 'YJu',
            'qC_': 'eoy',
            'bsn': 'jon',
            'Kua': 'p7s',
            'cTr': 'ZHr',
            'VUt': 'o3t',
            'Rde': 'Dpe',
            'ut': 'ut',
            'qUt': 'n3t',
            'rOa': '$ja',
            'L4': 'bj',
            'Tce': 'Kle',
            'umn': 'i_n',
            'A5o': 'I8o',
            'kF': 't2',
            'nNb': 'JKb',
            'zut': 'Mit',
            'cMt': 'Ckt',
            'dOa': 'Vja',
            'd4': 'y4',
            'w_e': 'cye',
            'vEt': 'r0t',
            'AI': 'Tk',
            'QTe': '_Ce',
            'Yxd': 'oUd',
            'Jxy': 'V3y',
            'Te': 've',
            'ZWe': 'Eqe',
            'xCs': 'SOs',
            'bir': 'ccr',
            'gir': 'scr',
            'Sir': 'ucr',
            'ekd': 'cUd',
            'tye': 'y_e',
            'j7r': 'bZr',
            'Kyo': 'ewo',
            'vir': 'dcr',
            'eky': 'Z3y',
            'dg': 'bg',
            'sky': 'i5y',
            'tkd': 'uUd',
            'Zxy': 'K3y',
            'U7r': '_Zr',
            '_X': 'wJ',
            'Qxy': 'z3y',
            'lRt': 'dDt',
            'W7r': 'vZr',
            'rkd': 'dUd',
            'Ee': 'we',
            'Zxd': 'lUd',
            'Ct': 'Ht',
            'cpr': 'Utr',
            'HRo': 'VRo',
            'gYe': 'CRe',
            'uOa': 'qja',
            'm6o': 'Ezo',
            'KSc': 'I9c',
            's2n': 'A8n',
            'xe': 'Te',
            'Pe': 'Re',
            'Ce': 'ke',
            'fs': 'Vi',
            'ne': 'ie',
            'Lf': 'Nf',
            'Ann': 'z8r',
            'vnn': 'q8r',
        }
    reverse = {new: old for old, new in names.items()}
    assert len(reverse) == len(names)
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_$][\w$]*')

    def extract(start: str, end: str) -> str:
        offset = patched.index(start)
        fragment = patched[offset : patched.index(end, offset)]
        return tokens.sub(
            lambda match: (
                match[0]
                if match.start()
                and fragment[match.start() - 1] == "."
                and fragment[max(0, match.start() - 3) : match.start()] != "..."
                else reverse.get(match[0], match[0])
            ),
            fragment,
        )

    snapshot = _PROVIDER_ENV_SNAPSHOT.search(original)
    assert snapshot is not None
    helpers = _replace_provider_snapshot(snapshot)
    install = next(
        p
        for p in _provider_env_207(BACKGROUND_PROVIDER_ENV_198).patches
        if p.name == "install-provider-native-policy-boundary"
    )
    helpers = install.pattern.sub(install.replacement, helpers)
    registry = "".join(
        f'var {name}=["ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN","ANTHROPIC_BASE_URL"];'
        for name in _provider_key_sources(original)
    )
    if version >= 208:
        # Adapt manager locals only. Keep worker and transport bodies intact.
        if version == 211:
            adoption = extract(
                "await Promise.all(Object.entries(x.workers)", ",I+R+k>0)"
            )
            locals_map = {"x": "A", "I": "T", "R": "x", "k": "I", "v": "_"}
        elif version == 210:
            adoption = extract(
                "await Promise.all(Object.entries(x.workers)", ",k+R+I>0)"
            )
            locals_map = {"x": "A", "k": "T", "R": "x", "I": "I", "E": "_"}
        else:
            adoption = extract(
                "await Promise.all(Object.entries(T.workers)", ",x+k+R>0)"
            )
            locals_map = {"T": "A", "x": "T", "k": "x", "R": "I", "b": "_"}
        adoption = tokens.sub(
            lambda match: locals_map.get(match[0], match[0]), adoption
        )
        worker = extract("class eue{", "var Uon,a7s,$J,YRo,")
        if version == 211:
            # Keep the new notice framing and revival guard from the native capture.
            captured = path.read_text()
            constants = []
            for name in ("zXr", "E6y", "v6y"):
                match = re.search(rf'{name}=("(?:\\.|[^"\\])*"|\d+)', captured)
                assert match is not None
                constants.append(f"const {match[0]};")
            exit_codes = re.search(r"S6y=new Set\(\[[\d,]+\]\)", captured)
            assert exit_codes is not None
            constants.append(f"const {exit_codes[0]};")
            worker = (
                "".join(constants)
                + "const et=(_key,fallback)=>fallback;"
                + _attribution_function(captured, "KXr")
                + _attribution_function(captured, "FNd")
                + worker
            )
        assert "onExit(e,t,r){" in worker
        return {
            "version": str(version),
            # No external launcher is configured in the extracted-role harness.
            "worker": 'const FC=()=>globalThis.wrapperRefusal??null,X_=()=>globalThis.wrapperCommand??[],Rz=v=>v,Eh=v=>v,n4="CLAUDE_CODE_PROCESS_WRAPPER";'
            + extract("function Kpp(", "async function u7s(")
            + worker,
            "stall": extract("function QL_(", "async function gAp("),
            "client": extract("function Npp(", "var $pp,"),
            "lines": extract("function Ouo(", "var YJu,"),
            "environment": extract("function zpp(", "function Kpp("),
            "sweeps": extract("async function $ja(", "var k8o,")
            + extract("async function JKb(", "var Nf,"),
            "auth": extract("function _Ce(", "var oUd,"),
            "endpoint": extract("async function V3y(", "async function pUd("),
            "server": extract("function K3y(", "async function pUd("),
            "adoption": adoption,
            "helpers": registry + helpers,
        }
    adoption = extract("await Promise.all(Object.entries(A.workers)", ",T+x+k>0)")
    adoption = adoption.replace("k++", "I++")
    return {
        "worker": extract("class Yce{", "var uln,nma,TJ,OOo,"),
        "stall": extract("function BP_(", "async function WAp("),
        "client": extract("function w$p(", "var A$p,"),
        "lines": extract("function CDo(", "var tAp,"),
        "environment": extract("function D$p(", "async function sma("),
        "sweeps": extract("async function mBa(", "function $jb(")
        + extract("async function Vjb(", "var Lf,fIf,ABa,"),
        "auth": extract("function w0e(", "var kDd,"),
        "endpoint": extract("async function bMy(", "async function ODd("),
        "server": extract("function vMy(", "async function ODd("),
        "adoption": adoption,
        "helpers": registry + helpers,
    }


@pytest.fixture(scope="module", params=[206, 207, 208, 209, 210, 211])
def native_lifecycle_source(request: pytest.FixtureRequest) -> dict[str, str]:
    if request.param in {207, 208, 209, 210, 211}:
        return _native_lifecycle_207(request.param)
    path = ROOT / "build/sweep-resume/2.1.206/linux-x64/original.js"
    if not path.is_file():
        pytest.skip("requires captured pristine .206 linux-x64 source")
    original = path.read_text()
    patched = BACKGROUND_PROVIDER_ENV_198.apply(original)
    start = patched.index("class Bce{")
    end = patched.index("var _sn,Gua,lJ,hMo,", start)
    worker = patched[start:end]
    assert "static async adopt(" in worker
    assert "static spawn(" in worker
    assert "onExit(e,t){" in worker
    assert worker.endswith("}")
    snapshot = _PROVIDER_ENV_SNAPSHOT.search(original)
    assert snapshot is not None
    patch = next(
        item
        for item in BACKGROUND_PROVIDER_ENV_198.patches
        if item.name == "snapshot-transient-provider-env"
    )
    assert not isinstance(patch.replacement, str)
    helpers = patch.replacement(snapshot)
    registry = "".join(
        f'const {name}=["ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN","ANTHROPIC_BASE_URL"];'
        for name in _provider_key_sources(original)
    )
    # Use the native orphan sweeps and manager adoption block without edits.
    orphan_start = patched.index("async function rOa(")
    orphan_end = patched.index("function z1b(", orphan_start)
    manager_start = patched.index("async function nNb(")
    manager_end = patched.index("var Lf,y0f,dOa,", manager_start)
    adoption_start = patched.index("await Promise.all(Object.entries(A.workers)")
    adoption_end = patched.index(")", patched.index("})),T+x+I", adoption_start))
    adoption = patched[adoption_start : adoption_end + 2]
    return {
        "stall": patched[
            patched.index("function bx_(") : patched.index("async function fbp(")
        ],
        "worker": worker,
        "client": patched[patched.index("function qRp(") : patched.index("var WRp,")],
        "lines": patched[patched.index("function aRo(") : patched.index("var w_p,")],
        "environment": patched[
            patched.index("function eLp(") : patched.index("async function Vua(")
        ],
        "helpers": registry + helpers,
        "sweeps": patched[orphan_start:orphan_end] + patched[manager_start:manager_end],
        "adoption": adoption,
        "auth": patched[patched.index("function QTe(") : patched.index("var Yxd,")],
        "endpoint": patched[
            patched.index("async function Jxy(") : patched.index("async function nkd(")
        ],
        "server": patched[
            patched.index("function Zxy(") : patched.index("async function nkd(")
        ],
    }


@pytest.mark.parametrize(
    "scenario",
    [
        "replaced-socket",
        "stall-unresolved",
        "stall-recovered",
        "constructor",
        "cold",
        "adopt-map-sweep",
        "dead",
        "identity-mismatch",
        "pending-upgrade",
        "rekey",
        "stale",
        "retire",
        "crash",
        "handoff",
        "wrong-mac",
        "wrong-proto",
        "wrong-version",
        "wrong-session",
        "wrong-nonce",
        "partial",
        "timeout",
        "replay",
        "reconnect",
        "crash-response",
        "missing-auth",
        "wrong-auth",
        "unauthenticated",
        "unsupported",
    ],
)
def test_native_provider_lifecycle(
    native_lifecycle_source: dict[str, str], scenario: str, tmp_path: Path
) -> None:
    runtime = shutil.which("node")
    if runtime is None:
        pytest.skip("requires node")
    fixture = tmp_path / "native.json"
    fixture.write_text(json.dumps(native_lifecycle_source))
    result = subprocess.run(  # noqa: S603 - Run the extracted native code in a mock context.
        [
            runtime,
            str(Path(__file__).with_name("provider_native_lifecycle.mjs")),
            str(fixture),
            scenario,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "scenario",
    [
        "wrapper-refusal",
        "wrapper-ENOENT",
        "wrapper-EACCES",
        "wrapper-EPERM",
        "wrapper-fork-exit",
        "wrapper-host-stderr",
    ],
)
def test_native_208_wrapper_failures(
    native_wrapper_source: dict[str, str], scenario: str, tmp_path: Path
) -> None:
    test_native_provider_lifecycle(native_wrapper_source, scenario, tmp_path)


@pytest.fixture(scope="module", params=[208, 209, 210, 211])
def native_wrapper_source(request: pytest.FixtureRequest) -> dict[str, str]:
    return _native_lifecycle_207(request.param)


@pytest.mark.skipif(
    os.environ.get("CCPATCH_PROCESS_INTEGRATION") != "1",
    reason="opt in with CCPATCH_PROCESS_INTEGRATION=1; extracted native Unix transport",
)
@pytest.mark.parametrize("scenario", ["handoff", "wrong-auth"])
def test_native_provider_process_takeover(
    native_lifecycle_source: dict[str, str], scenario: str
) -> None:
    """Run native transport in child processes, not the full CLI or PTY host.

    CLI startup also initializes telemetry, auth, and other integrations. This
    bounded harness does not run that startup path. No provider request is needed
    to test the memory-only snapshot protocol. All credentials are synthetic.
    """
    runtime = shutil.which("node")
    if runtime is None or sys.platform != "linux":
        pytest.skip("requires node, Linux /proc, and Unix sockets")
    # Keep socket paths below the Unix socket length limit.
    with tempfile.TemporaryDirectory(prefix="ccp-") as directory:
        scratch = Path(directory)
        fixture = scratch / "native.json"
        fixture.write_text(json.dumps(native_lifecycle_source))
        env = {"PATH": str(Path(runtime).parent), "LANG": "C.UTF-8"}
        for key in (
            "HOME",
            "CLAUDE_CONFIG_DIR",
            "XDG_CONFIG_HOME",
            "XDG_STATE_HOME",
            "XDG_CACHE_HOME",
            "XDG_RUNTIME_DIR",
            "TMPDIR",
        ):
            target = scratch / key.lower()
            target.mkdir(mode=0o700)
            env[key] = str(target)
        with subprocess.Popen(  # noqa: S603 - Isolated local test processes only.
            [
                runtime,
                str(Path(__file__).with_name("provider_process_lifecycle.mjs")),
                str(fixture),
                scenario,
            ],
            cwd=scratch,
            env=env,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as process:
            try:
                stdout, stderr = process.communicate(timeout=30)
                assert process.returncode == 0, stdout + stderr
            finally:
                # Kill the test process group even if the controller has exited.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)


def test_provider_207_native_settings_and_policy() -> None:
    """Run native filters, both settings initializers, capture, and policy enforcement."""
    path = ROOT / "build/sweep-resume/2.1.207/linux-x64/original.js"
    if not path.is_file():
        pytest.skip("requires captured pristine .207 linux-x64 source")
    runtime = shutil.which("node")
    if runtime is None:
        pytest.skip("requires node")
    original = path.read_text()
    patches = _provider_env_207(BACKGROUND_PROVIDER_ENV_198).patches
    snapshot = _PROVIDER_ENV_SNAPSHOT.search(original)
    assert snapshot is not None
    helpers = _replace_provider_snapshot(snapshot)
    install = next(
        p for p in patches if p.name == "install-provider-native-policy-boundary"
    )
    helpers = install.pattern.sub(install.replacement, helpers)
    names = [
        "Bnt",
        "KY",
        "mQt",
        "f1g",
        "g1g",
        "y1g",
        "A1g",
        "_1g",
        "nNu",
        "Zro",
        "ACt",
        "zGl",
        "d8o",
        "p8o",
        "CPn",
        "Rj",
    ]
    fragments: list[str] = []
    for name in names:
        start = original.index(f"function {name}(")
        end = original.index("function ", start + 9)
        fragment = original[start:end].removesuffix("async ")
        if "}var " in fragment:
            fragment = fragment[: fragment.index("}var ") + 1]
        for patch in patches:
            if patch.name in {
                "initialize-provider-before-native-settings",
                "filter-provider-settings-with-native-policy",
            }:
                fragment = patch.pattern.sub(patch.replacement, fragment)
        fragments.append(fragment)
    registry = "".join(
        f'var {name}=["ANTHROPIC_API_KEY","ANTHROPIC_BASE_URL","ANTHROPIC_DEFAULT_OPUS_MODEL"];'
        for name in _provider_key_sources(original)
    )
    script = (
        r'''const assert=require("node:assert/strict");
    process.env.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT=JSON.stringify({ANTHROPIC_API_KEY:"requester",ANTHROPIC_BASE_URL:"https://requester",ANTHROPIC_DEFAULT_OPUS_MODEL:null});
    const be=process.env; let policy=null, failures=[], policyReadable=true, Lyn;
    let Nnt={},dWr,hQt={},checks=0;
    const m1g=new Set(["policySettings","projectSettings","localSettings"]),h1g=new Set(["HOST_SECRET"]);
    const b1g=new Set(["CLAUDE_CODE_REMOTE"]),S1g=new Set(),v1g=new Set(),E1g=new Set(),z4t=new Set(),b1=new Set();
    const w1g=["userSettings","flagSettings","policySettings"];
    const ct=v=>v==="1",LQ=()=>false,g0n=k=>k==="ANTHROPIC_API_KEY",Aol=()=>false,y0n=()=>false;
    const Tr=s=>s==="policySettings"?policy:{env:{ANTHROPIC_API_KEY:"borrowed",OTHER:"local",HOST_SECRET:"hidden",CLAUDE_CODE_REMOTE:"hidden"}};
    const vt=()=>({env:{ANTHROPIC_API_KEY:"global"}}),Ph=()=>true,JA=()=>w1g,oNu=()=>{},jqn=()=>{};
    const check=()=>{assert.equal(be.ANTHROPIC_API_KEY,"requester");assert.equal(be.ANTHROPIC_BASE_URL,"https://requester");checks++};
    const pge=check,zHr=check,hHn=check,RYe=check,I2e=check,Voi=()=>({}),gjt=async()=>false,bjt=async()=>false,Ce=e=>{throw e};
    const FHr=()=>failures,UHr=()=>policyReadable,n2e=()=>"file",w=()=>{};
    const sl=(model,context)=>context.allowlist.includes(model),Ox=()=>false,rm=()=>false;
    '''
        + helpers
        + registry
        + "".join(fragments)
        + r'''
    policy={env:{ANTHROPIC_BASE_URL:"https://managed"}};
    assert.throws(()=>Bnt(),{code:"EPROVIDERENV"});
    assert.equal(be.ANTHROPIC_API_KEY,undefined);
    policy=null; Bnt(); Zro();
    assert.deepEqual(zGl(),{sonnet:false,opus:false});
    assert.equal(be.OTHER,"local");assert.equal(be.HOST_SECRET,undefined);assert.equal(be.CLAUDE_CODE_REMOTE,undefined);
    be.ANTHROPIC_DEFAULT_OPUS_MODEL="probe";be.CLAUDE_CODE_3P_PROBE_WROTE_OPUS_DEFAULT="probe";
    KY(); Zro();assert.equal(be.ANTHROPIC_DEFAULT_OPUS_MODEL,"probe");assert.equal(zGl().opus,false);
    be.ANTHROPIC_DEFAULT_OPUS_MODEL="explicit";Zro();assert.equal(zGl().opus,true);
    assert.ok(checks>3);
    Nnt.managedByHost=true;assert.deepEqual(mQt({ANTHROPIC_API_KEY:"requester",OTHER:"ok"},"policySettings"),{OTHER:"ok"});
    Nnt.managedByHost=false;dWr=new Set(["OTHER"]);assert.deepEqual(mQt({OTHER:"blocked"},"userSettings"),{});
    dWr=null;be.ANTHROPIC_UNIX_SOCKET="/native.sock";
    assert.deepEqual(mQt({ANTHROPIC_API_KEY:"requester",ANTHROPIC_BASE_URL:"https://requester"},"policySettings"),{});
    assert.throws(()=>mQt({ANTHROPIC_BASE_URL:"https://managed"},"policySettings"),{code:"EPROVIDERENV"});
    delete be.ANTHROPIC_UNIX_SOCKET;
    policy={availableModels:["allowed"],enforceAvailableModels:true,modelOverrides:{}};
    assert.equal(Rj("allowed"),true);assert.equal(Rj("explicit"),false);
    failures=["invalid policy"];policyReadable=false;assert.deepEqual(CPn(),{state:"refused"});assert.equal(Rj("allowed"),false);
    '''
    )
    result = subprocess.run(  # noqa: S603 - Run native functions with synthetic settings.
        [runtime, "-e", script], capture_output=True, text=True, check=False, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr
