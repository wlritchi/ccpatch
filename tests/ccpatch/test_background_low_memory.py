"""Let the environment set the background daemon's low-memory threshold."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.patches import (
    BACKGROUND_LOW_MEMORY_THRESHOLD,
    BG_LOW_MEM_ENV,
    PatchError,
    default_patch_sets,
)

# The low-memory module as minified in 2.1.274 through 2.1.301.
_FUNCTION = re.compile(
    r"function (?P<name>[\w$]+)\(\)\{let (?P<threshold>[\w$]+)=[\s\S]{1,400}?"
    r'if\((?P<platform>[\w$]+)\(\)!=="macos"\)return\{lowMem:(?P<freemem>[\w$]+)\(\)'
    r"<(?P=threshold),level:void 0\};let (?P<level>[\w$]+)=(?P<pressure>[\w$]+)\(\);"
    r"return\{lowMem:(?P=level)!==void 0&&(?P=level)>=(?P<critical>[\w$]+),"
    r"level:(?P=level)\}\}"
)


def _fixture(suffix: str = "") -> str:
    return (
        f'import{{k{suffix}}}from"/$bunfs/root/chunk-a.js";'
        f'import{{freemem as o{suffix}}}from"os";'
        f'function V{suffix}(){{let e{suffix}=k{suffix}("tengu_bg_low_mem_mb",1024)'
        f"*1024*1024;if(e{suffix}<=0)return{{lowMem:!1,level:void 0}};"
        f'if(O{suffix}()!=="macos")return{{lowMem:o{suffix}()<e{suffix},level:void 0}};'
        f"let n{suffix}=m{suffix}();"
        f"return{{lowMem:n{suffix}!==void 0&&n{suffix}>=l{suffix},level:n{suffix}}}}}"
        f"function s{suffix}(){{return V{suffix}().lowMem}}"
    )


def _function(source: str) -> re.Match[str]:
    matches = list(_FUNCTION.finditer(source))
    assert len(matches) == 1
    return matches[0]


def _node(tmp_path: Path, source: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")
    script = tmp_path / "bg-low-memory.mjs"
    script.write_text('import assert from "node:assert/strict";\n' + source)
    result = subprocess.run(  # noqa: S603 - Run local regression code.
        [node, str(script)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _check_thresholds(tmp_path: Path, function: re.Match[str], flag: str) -> None:
    names = function.groupdict()
    _node(
        tmp_path,
        f"""
let flagMb=1024,freeMb=900,platform="linux",flagReads=0;
const {flag}=(name,fallback)=>{{assert.equal(name,"tengu_bg_low_mem_mb");
 assert.equal(fallback,1024);flagReads++;return flagMb}};
const {names["platform"]}=()=>platform,{names["freemem"]}=()=>freeMb*1024*1024;
const {names["pressure"]}=()=>4,{names["critical"]}=4;
{function[0]}
const check=(env,lowMem,reads)=>{{
 if(env===undefined)delete process.env[{json.dumps(BG_LOW_MEM_ENV)}];
 else process.env[{json.dumps(BG_LOW_MEM_ENV)}]=env;
 flagReads=0;
 assert.equal({names["name"]}().lowMem,lowMem,String(env));
 assert.equal(flagReads,reads,String(env));
}};
// Unset, empty, and non-numeric values keep the flag threshold.
for(const env of [undefined,"",  "  ","abc","12MB","NaN","Infinity"])check(env,true,1);
flagMb=0;check(undefined,false,1);check("abc",false,1);flagMb=1024;
// A numeric value replaces the flag threshold.
check("512",false,0);check(" 2048 ",true,0);check("1e3",true,0);check("0x400",true,0);
// Zero or less disables the guard.
for(const env of ["0","-1","-0.5"])check(env,false,0);
freeMb=0;check("0",false,0);check(undefined,true,1);
// macOS uses memory pressure, but the threshold can still disable the guard.
platform="macos";check(undefined,true,1);check("1",true,0);check("0",false,0);
""",
    )


@pytest.mark.parametrize("suffix", ["", "$", "Renamed"])
def test_environment_threshold_structural_variants(suffix: str, tmp_path: Path) -> None:
    source = _fixture(suffix)
    patched = BACKGROUND_LOW_MEMORY_THRESHOLD.apply(source)
    assert f"process.env.{BG_LOW_MEM_ENV}" in patched
    assert patched.endswith(f"function s{suffix}(){{return V{suffix}().lowMem}}")
    _check_thresholds(tmp_path, _function(patched), f"k{suffix}")


def test_environment_threshold_version_and_anchor_checks() -> None:
    assert BACKGROUND_LOW_MEMORY_THRESHOLD not in default_patch_sets((2, 1, 273))
    assert BACKGROUND_LOW_MEMORY_THRESHOLD not in default_patch_sets(None)
    for version in [(2, 1, 274), (2, 1, 301), (2, 1, 999)]:
        assert BACKGROUND_LOW_MEMORY_THRESHOLD in default_patch_sets(version)
    for source in [
        "unknown upstream",
        _fixture() + _fixture("$"),
        _fixture().replace("tengu_bg_low_mem_mb", "tengu_bg_other_mb"),
        _fixture().replace("if(e<=0)", "if(e<0)"),
    ]:
        with pytest.raises(PatchError):
            BACKGROUND_LOW_MEMORY_THRESHOLD.apply(source)
    with pytest.raises(PatchError):
        BACKGROUND_LOW_MEMORY_THRESHOLD.apply(
            BACKGROUND_LOW_MEMORY_THRESHOLD.apply(_fixture())
        )


@pytest.mark.parametrize("version", ["274", "280", "288", "301"])
def test_native_low_memory_threshold(version: str, tmp_path: Path) -> None:
    path = os.environ.get(f"CCPATCH_{version}_SOURCE")
    if not path:
        pytest.skip(f"set CCPATCH_{version}_SOURCE to pristine 2.1.{version} source")
    source = Path(path).read_text()
    assert f'VERSION:"2.1.{version}"' in source
    native = _function(source)
    flag = re.search(r'=([\w$]+)\("tengu_bg_low_mem_mb",1024\)\*1024\*1024;', native[0])
    assert flag is not None
    patched = BACKGROUND_LOW_MEMORY_THRESHOLD.apply(source)
    assert len(patched) > len(source)
    function = _function(patched)
    assert function["name"] == native["name"]
    assert f"process.env.{BG_LOW_MEM_ENV}" in function[0]
    _check_thresholds(tmp_path, function, flag[1])
