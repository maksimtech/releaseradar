#!/bin/sh
# What the image actually contains, printed rather than taken from a scanner.
#
# Run by .github/workflows/docker-build-check.yml, which builds and publishes
# nothing:
#     docker run --rm -i --entrypoint sh releaseradar:build-check - < inspect.sh
#
# The sections that measure report and do not judge: a red step there would be a
# broken diagnostic, and what matters is whether the numbers and the record agree.
# The last section judges — it holds a decision, and says why there.
set -eu

echo "── build tooling, which the Dockerfile removes ──"
for pkg in pip setuptools wheel; do
    if version=$(python -c "import importlib.metadata as m, sys; sys.stdout.write(m.version('$pkg'))" 2>/dev/null); then
        echo "  <- $pkg $version IS STILL PRESENT, so the removal did not hold"
    else
        echo "  $pkg absent, as intended"
    fi
done

echo
echo "── anything left under pip/_vendor ──"
find / -path '*/pip/_vendor*' -name 'bom.cdx.json' 2>/dev/null | sed 's/^/  /' || true
echo "  (nothing above means the vendored path Scout reports is gone)"

echo
echo "── perl packages installed ──"
dpkg-query -W -f '  ${Package} ${Version} essential=${Essential} priority=${Priority}\n' \
    'perl*' 'libperl*' 2>/dev/null || echo "  none"

echo
echo "── the other packages the record names ──"
for pattern in 'zlib1g*' 'libattr1*' 'libacl1*' 'libstdc++6*'; do
    found=$(dpkg-query -W -f '  ${Package} ${Version} priority=${Priority}\n' "$pattern" 2>/dev/null || true)
    if [ -n "$found" ]; then
        echo "$found"
    else
        echo "  $pattern matched nothing installed"
    fi
done

echo
echo "── size of the installed set ──"
printf '  %s Debian packages\n' "$(dpkg-query -f '.\n' -W | wc -l)"
printf '  %s Python distributions\n' "$(python -c 'import importlib.metadata as m; print(len(list(m.distributions())))')"

# ── the MCP server is in the image ──
#
# This section judges. The `mcp` SDK is an optional extra of the package, kept
# out of a plain `pip install releaseradar` on purpose (24 distributions the CLI
# does not need); the image installs `releaseradar[mcp]` so that
# `docker run -i maksimtech/releaseradar mcp` is a working MCP server. An image
# built without the extra would pass every other check here and fail the one
# thing the README promises about it.
echo
echo "── the MCP server ──"
failed=0
if python -c "import mcp.server.mcpserver" 2>/dev/null; then
    echo "  mcp $(python -c 'import importlib.metadata as m; print(m.version("mcp"))') importable"
else
    echo "  <- the mcp package is MISSING: the image was built without the [mcp] extra"
    failed=1
fi
if python -c "import releaseradar.mcp_server" 2>/dev/null; then
    echo "  releaseradar.mcp_server imports"
else
    echo "  <- releaseradar.mcp_server does not import"
    failed=1
fi
if releaseradar --help 2>/dev/null | grep -q ' mcp '; then
    echo "  releaseradar mcp is a command"
else
    echo "  <- the CLI has no mcp command"
    failed=1
fi
echo "  running as $(id -un) (uid $(id -u))"
if [ "$(id -u)" -eq 0 ]; then
    echo "  <- the container runs as root"
    failed=1
fi

if [ "$failed" -ne 0 ]; then
    echo "  mcp section: FAILED"
    exit 1
fi
echo "  mcp section: ok"
