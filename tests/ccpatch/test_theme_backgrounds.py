"""Check custom diff colors through the native syntax renderer."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.patches import CATPPUCCIN_SYNTAX


@pytest.mark.parametrize("patched", [False, True], ids=["native", "catppuccin"])
def test_native_diff_background_overrides(patched: bool, tmp_path: Path) -> None:
    path = os.environ.get("CCPATCH_288_SOURCE")
    if not path:
        pytest.skip("set CCPATCH_288_SOURCE to pristine 2.1.288 source")
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")
    source = Path(path).read_text()
    assert 'VERSION:"2.1.288"' in source
    if patched:
        source = CATPPUCCIN_SYNTAX.apply(source)
    anchor = source.index("function ke(e,n,t){let i=_")
    start = source.rindex("/* ccpatch-module:", 0, anchor)
    end = source.index("/* ccpatch-module:", anchor)
    renderer = re.sub(r"import[^;]+;", "", source[start:end])
    renderer = re.sub(r"export\{[^}]+\};", "", renderer)
    cache_start = source.index('var oe=["diffAdded","diffRemoved"')
    cache_end = source.index("function ee(d)", cache_start)
    cache = source[cache_start:cache_end]
    script = tmp_path / "theme-backgrounds.cjs"
    harness = Path(__file__).with_name("theme_backgrounds_regression.cjs").read_text()
    script.write_text(
        harness.replace("/* NATIVE_RENDERER */", renderer).replace(
            "/* NATIVE_CACHE */", cache
        )
    )
    result = subprocess.run(  # noqa: S603 - Run local regression code.
        [node, str(script)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
