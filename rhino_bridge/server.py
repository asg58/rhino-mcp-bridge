from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

try:
    import pefile
except Exception:
    pefile = None

try:
    import dnfile
except Exception:
    dnfile = None

mcp = MCPServer(
    "Rhino MCP Bridge",
    instructions=(
        "Rhino root is read-only. Workspace root is editable. "
        "Never access paths outside configured roots or expose blocked secret files."
    ),
)

MAX_TEXT = int(os.getenv("RHINO_BRIDGE_MAX_TEXT_BYTES", str(4 * 1024 * 1024)))
MAX_RESULTS = int(os.getenv("RHINO_BRIDGE_MAX_RESULTS", "5000"))
MAX_SECONDS = int(os.getenv("RHINO_BRIDGE_MAX_COMMAND_SECONDS", "180"))
MAX_OUTPUT = int(os.getenv("RHINO_BRIDGE_MAX_OUTPUT_CHARS", "120000"))
ALLOW_SECRETS = os.getenv("RHINO_BRIDGE_ALLOW_SECRETS", "0") == "1"

SECRET_NAMES = {".env", ".env.local", ".env.production", ".env.development", "id_rsa", "id_ed25519", "credentials.json"}
SECRET_SUFFIXES = (".pem", ".p12", ".pfx", ".key", ".kdbx")
SAFE_EXE = {"python", "python3", "py", "pytest", "ruff", "mypy", "node", "npm", "npx", "pnpm", "yarn", "dotnet", "msbuild", "git", "cargo", "rustc", "go", "cmake", "ctest"}
BLOCKED = [
    r"\brm\s+-rf\b", r"\bdel\s+/[sqf]", r"\brmdir\s+/s\b", r"\bformat\b", r"\bdiskpart\b",
    r"\bshutdown\b", r"\breboot\b", r"\bmkfs\b", r"\bnetsh\b", r"\bgit\s+clean\s+-[a-z]*f",
    r"\bgit\s+reset\s+--hard\b", r"\bgit\s+checkout\s+--\s+\.", r"\bgit\s+restore\s+--source\b",
]
SHELL_META = re.compile(r"[;&|><]")


def _root(env_name: str) -> Path:
    raw = os.getenv(env_name, "").strip().strip('"')
    if not raw:
        raise RuntimeError(f"{env_name} is not configured")
    p = Path(raw).expanduser()
    if not p.exists() or not p.is_dir():
        raise RuntimeError(f"{env_name} is not a directory: {p}")
    return p.resolve()


def rhino_root() -> Path:
    return _root("RHINO_ROOT")


def workspace_root() -> Path:
    return _root("WORKSPACE_ROOT")


def _safe(root: Path, relative_path: str = "") -> Path:
    p = (root / (relative_path or "").replace("/", os.sep)).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise PermissionError(f"Path escapes configured root: {relative_path}")
    return p


def _safe_rhino(path: str = "") -> Path:
    return _safe(rhino_root(), path)


def _safe_ws(path: str = "") -> Path:
    return _safe(workspace_root(), path)


def _rel(root: Path, p: Path) -> str:
    return str(p.resolve().relative_to(root)).replace("\\", "/")


def _is_secret(p: Path) -> bool:
    name = p.name.lower()
    if name in SECRET_NAMES or any(name.endswith(s) for s in SECRET_SUFFIXES):
        return True
    return bool({x.lower() for x in p.parts} & {".ssh", ".aws", ".azure", ".gnupg", ".password-store"})


def _assert_not_secret(p: Path) -> None:
    if _is_secret(p) and not ALLOW_SECRETS:
        raise PermissionError("Secret/credential file blocked by policy")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _looks_text(p: Path) -> bool:
    try:
        head = p.read_bytes()[:4096]
    except OSError:
        return False
    if b"\x00" in head:
        return False
    if not head:
        return True
    printable = sum((32 <= b <= 126) or b in b"\r\n\t\f\b" or b >= 128 for b in head)
    return printable / len(head) > 0.85


def _tree(root: Path, start: Path, max_depth: int, max_entries: int) -> dict[str, Any]:
    max_depth = max(0, min(max_depth, 10))
    max_entries = max(1, min(max_entries, MAX_RESULTS))
    out: list[dict[str, Any]] = []

    def walk(cur: Path, depth: int) -> None:
        if depth > max_depth or len(out) >= max_entries:
            return
        try:
            children = sorted(cur.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except OSError:
            return
        for child in children:
            if len(out) >= max_entries:
                return
            try:
                rp = child.resolve()
                rp.relative_to(root)
            except Exception:
                continue
            item = {"path": _rel(root, rp), "type": "directory" if child.is_dir() else "file"}
            if child.is_file():
                try:
                    item["size"] = child.stat().st_size
                except OSError:
                    item["size"] = None
            out.append(item)
            if child.is_dir() and not child.is_symlink():
                walk(child, depth + 1)

    walk(start, 0)
    return {"entries": out, "truncated": len(out) >= max_entries}


def _read_text(root: Path, p: Path, start_line: int, end_line: int) -> dict[str, Any]:
    if not p.is_file():
        raise FileNotFoundError(str(p))
    if p.stat().st_size > MAX_TEXT:
        raise ValueError("File too large for text read")
    if not _looks_text(p):
        raise ValueError("File appears binary")
    start_line = max(1, start_line)
    end_line = max(start_line, min(end_line, start_line + 1999))
    lines = p.read_text("utf-8", errors="replace").splitlines()
    selected = lines[start_line - 1:end_line]
    return {
        "path": _rel(root, p),
        "start_line": start_line,
        "end_line": min(end_line, len(lines)),
        "total_lines": len(lines),
        "content": "\n".join(f"{i}: {line}" for i, line in enumerate(selected, start=start_line)),
    }


def _validate_command(argv: list[str]) -> list[str]:
    if not argv:
        raise ValueError("argv cannot be empty")
    exe = Path(argv[0]).name.lower()
    if exe.endswith(".exe"):
        exe = exe[:-4]
    if exe not in SAFE_EXE:
        raise PermissionError(f"Executable is not allowlisted: {argv[0]}")
    joined = " ".join(argv).lower()
    if SHELL_META.search(joined):
        raise PermissionError("Shell metacharacters are blocked")
    if any(re.search(pat, joined, re.I) for pat in BLOCKED):
        raise PermissionError("Destructive command blocked")
    return argv


def _run(argv: list[str], cwd: Path, timeout: int = 120) -> dict[str, Any]:
    timeout = max(1, min(timeout, MAX_SECONDS))
    started = time.time()
    cp = subprocess.run(argv, cwd=str(cwd), text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout, shell=False)
    return {
        "returncode": cp.returncode,
        "stdout": (cp.stdout or "")[-MAX_OUTPUT:],
        "stderr": (cp.stderr or "")[-MAX_OUTPUT:],
        "seconds": round(time.time() - started, 3),
        "argv": argv,
    }


@mcp.tool()
def bridge_status() -> dict[str, Any]:
    """Show configured roots and capabilities."""
    return {
        "rhino_root": str(rhino_root()),
        "workspace_root": str(workspace_root()),
        "rhino_mode": "read-only",
        "workspace_mode": "read-write",
        "pe_analysis": pefile is not None,
        "dotnet_analysis": dnfile is not None,
        "allow_secrets": ALLOW_SECRETS,
    }


@mcp.tool()
def rhino_list_tree(path: str = "", max_depth: int = 3, max_entries: int = 3000) -> dict[str, Any]:
    """List the read-only Rhino tree."""
    p = _safe_rhino(path)
    if not p.is_dir():
        raise NotADirectoryError(path)
    return _tree(rhino_root(), p, max_depth, max_entries)


@mcp.tool()
def rhino_glob(pattern: str = "*", path: str = "", recursive: bool = True, limit: int = 1000) -> dict[str, Any]:
    """Find Rhino files by filename glob."""
    root = rhino_root()
    base = _safe_rhino(path)
    iterator = base.rglob("*") if recursive else base.glob("*")
    out = []
    for p in iterator:
        if len(out) >= min(limit, MAX_RESULTS):
            break
        if p.is_file() and fnmatch.fnmatch(p.name, pattern):
            try:
                rp = p.resolve()
                rp.relative_to(root)
            except Exception:
                continue
            out.append({"path": _rel(root, rp), "size": rp.stat().st_size, "extension": rp.suffix.lower()})
    return {"pattern": pattern, "results": out, "truncated": len(out) >= min(limit, MAX_RESULTS)}


@mcp.tool()
def rhino_read_text(path: str, start_line: int = 1, end_line: int = 500) -> dict[str, Any]:
    """Read a text-like file from the Rhino root."""
    return _read_text(rhino_root(), _safe_rhino(path), start_line, end_line)


@mcp.tool()
def rhino_file_info(path: str) -> dict[str, Any]:
    """Return metadata and SHA-256 for one Rhino file."""
    p = _safe_rhino(path)
    if not p.is_file():
        raise FileNotFoundError(path)
    st = p.stat()
    return {"path": _rel(rhino_root(), p), "size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": _sha256(p), "extension": p.suffix.lower()}


@mcp.tool()
def rhino_analyze_pe(path: str) -> dict[str, Any]:
    """Statically inspect a Windows DLL/EXE/RHP/GHA without executing it."""
    if pefile is None:
        raise RuntimeError("pefile is not installed")
    p = _safe_rhino(path)
    if not p.is_file():
        raise FileNotFoundError(path)
    pe = pefile.PE(str(p), fast_load=False)
    imports = []
    for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        dll = entry.dll.decode(errors="replace") if isinstance(entry.dll, bytes) else str(entry.dll)
        names = []
        for imp in entry.imports[:300]:
            names.append(imp.name.decode(errors="replace") if imp.name else f"ordinal:{imp.ordinal}")
        imports.append({"dll": dll, "symbols": names})
    sections = [{"name": s.Name.rstrip(b"\x00").decode(errors="replace"), "virtual_size": s.Misc_VirtualSize, "raw_size": s.SizeOfRawData, "entropy": round(s.get_entropy(), 3)} for s in pe.sections]
    try:
        is_dotnet = pe.OPTIONAL_HEADER.DATA_DIRECTORY[14].VirtualAddress != 0
    except Exception:
        is_dotnet = False
    result = {"path": _rel(rhino_root(), p), "machine": hex(pe.FILE_HEADER.Machine), "is_dotnet": is_dotnet, "sections": sections, "imports": imports, "sha256": _sha256(p)}
    pe.close()
    return result


@mcp.tool()
def rhino_analyze_dotnet(path: str, max_types: int = 1000) -> dict[str, Any]:
    """Inspect .NET assembly metadata without loading/executing it."""
    if dnfile is None:
        raise RuntimeError("dnfile is not installed")
    p = _safe_rhino(path)
    if not p.is_file():
        raise FileNotFoundError(path)
    dn = dnfile.dnPE(str(p))
    if getattr(dn, "net", None) is None:
        try:
            dn.close()
        except Exception:
            pass
        return {"path": _rel(rhino_root(), p), "managed": False}
    tables = getattr(dn.net, "mdtables", None)
    def rows(name: str):
        table = getattr(tables, name, None) if tables is not None else None
        return list(getattr(table, "rows", []) or [])
    refs = []
    for r in rows("AssemblyRef")[:500]:
        refs.append({"name": str(getattr(r, "Name", "")), "version": ".".join(str(getattr(r, x, 0)) for x in ("MajorVersion", "MinorVersion", "BuildNumber", "RevisionNumber"))})
    types = []
    for r in rows("TypeDef")[:max_types]:
        name = str(getattr(r, "TypeName", ""))
        if name != "<Module>":
            types.append({"namespace": str(getattr(r, "TypeNamespace", "")), "name": name})
    result = {"path": _rel(rhino_root(), p), "managed": True, "assembly_references": refs, "type_count": len(rows("TypeDef")), "method_count": len(rows("MethodDef")), "types": types, "sha256": _sha256(p)}
    try:
        dn.close()
    except Exception:
        pass
    return result


@mcp.tool()
def rhino_dependency_summary(pattern: str = "*.dll", path: str = "", max_files: int = 1000) -> dict[str, Any]:
    """Summarize native PE import dependencies across Rhino binaries."""
    if pefile is None:
        raise RuntimeError("pefile is not installed")
    root = rhino_root()
    base = _safe_rhino(path)
    deps: Counter[str] = Counter()
    files = []
    scanned = 0
    for p in base.rglob("*"):
        if scanned >= max_files:
            break
        if not p.is_file() or not fnmatch.fnmatch(p.name, pattern):
            continue
        try:
            rp = p.resolve()
            rp.relative_to(root)
            pe = pefile.PE(str(rp), fast_load=True)
            try:
                pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
            except Exception:
                pass
            imported = []
            for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
                dll = entry.dll.decode(errors="replace") if isinstance(entry.dll, bytes) else str(entry.dll)
                imported.append(dll)
                deps[dll.lower()] += 1
            pe.close()
            scanned += 1
            files.append({"path": _rel(root, rp), "imports": sorted(set(imported), key=str.lower)})
        except Exception:
            continue
    return {"files_scanned": scanned, "dependencies": [{"dll": n, "referenced_by_files": c} for n, c in deps.most_common()], "files": files, "truncated": scanned >= max_files}


@mcp.tool()
def workspace_list_tree(path: str = "", max_depth: int = 4, max_entries: int = 3000) -> dict[str, Any]:
    """List the editable workspace tree."""
    p = _safe_ws(path)
    if not p.is_dir():
        raise NotADirectoryError(path)
    return _tree(workspace_root(), p, max_depth, max_entries)


@mcp.tool()
def workspace_read_text(path: str, start_line: int = 1, end_line: int = 500) -> dict[str, Any]:
    """Read a workspace text file."""
    p = _safe_ws(path)
    _assert_not_secret(p)
    return _read_text(workspace_root(), p, start_line, end_line)


@mcp.tool()
def workspace_search_text(query: str, path: str = "", filename_glob: str = "*", max_matches: int = 300) -> dict[str, Any]:
    """Literal full-text search in the workspace."""
    if not query:
        raise ValueError("query is required")
    root = workspace_root()
    base = _safe_ws(path)
    hits = []
    scanned = 0
    for p in base.rglob("*"):
        if len(hits) >= max_matches:
            break
        if not p.is_file() or not fnmatch.fnmatch(p.name, filename_glob):
            continue
        try:
            rp = p.resolve()
            rp.relative_to(root)
            if _is_secret(rp) and not ALLOW_SECRETS:
                continue
            if rp.stat().st_size > MAX_TEXT or not _looks_text(rp):
                continue
            scanned += 1
            with rp.open("r", encoding="utf-8", errors="replace") as f:
                for line_no, line in enumerate(f, 1):
                    if query.lower() in line.lower():
                        hits.append({"path": _rel(root, rp), "line": line_no, "text": line.rstrip()[:1200]})
                        if len(hits) >= max_matches:
                            break
        except Exception:
            continue
    return {"query": query, "files_scanned": scanned, "matches": hits, "truncated": len(hits) >= max_matches}


@mcp.tool()
def workspace_write_text(path: str, content: str, overwrite: bool = False) -> dict[str, Any]:
    """Create or overwrite a UTF-8 workspace file."""
    p = _safe_ws(path)
    _assert_not_secret(p)
    if p.exists() and not overwrite:
        raise FileExistsError("File exists; set overwrite=true")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": _rel(workspace_root(), p), "bytes": p.stat().st_size, "sha256": _sha256(p)}


@mcp.tool()
def workspace_replace_text(path: str, old: str, new: str, expected_replacements: int = 1) -> dict[str, Any]:
    """Replace exact text with an expected-match guard."""
    p = _safe_ws(path)
    _assert_not_secret(p)
    text = p.read_text("utf-8", errors="strict")
    count = text.count(old)
    if count != expected_replacements:
        raise ValueError(f"Expected {expected_replacements} matches, found {count}")
    p.write_text(text.replace(old, new), encoding="utf-8")
    return {"path": _rel(workspace_root(), p), "replacements": count, "sha256": _sha256(p)}


@mcp.tool()
def git_status() -> dict[str, Any]:
    """Show Git branch and working-tree status."""
    root = workspace_root()
    return {"branch": _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root), "status": _run(["git", "status", "--porcelain=v1", "--branch"], root)}


@mcp.tool()
def git_diff(path: str = "", staged: bool = False) -> dict[str, Any]:
    """Show Git diff."""
    args = ["git", "diff"]
    if staged:
        args.append("--cached")
    if path:
        args += ["--", _rel(workspace_root(), _safe_ws(path))]
    return _run(args, workspace_root())


@mcp.tool()
def git_log(max_count: int = 30) -> dict[str, Any]:
    """Show recent Git commits."""
    n = max(1, min(max_count, 200))
    return _run(["git", "log", f"-n{n}", "--date=iso-strict", "--pretty=format:%H%x09%ad%x09%an%x09%s"], workspace_root())


@mcp.tool()
def git_add(paths: list[str]) -> dict[str, Any]:
    """Stage selected workspace paths."""
    if not paths:
        raise ValueError("paths cannot be empty")
    rels = [_rel(workspace_root(), _safe_ws(p)) for p in paths]
    return _run(["git", "add", "--", *rels], workspace_root())


@mcp.tool()
def git_commit(message: str) -> dict[str, Any]:
    """Commit already-staged changes."""
    if not message.strip():
        raise ValueError("Commit message is required")
    return _run(["git", "commit", "-m", message], workspace_root())


@mcp.tool()
def git_pull(remote: str = "", branch: str = "") -> dict[str, Any]:
    """Perform a non-rebase pull."""
    args = ["git", "pull", "--no-rebase"]
    if remote:
        args.append(remote)
    if branch:
        args.append(branch)
    return _run(args, workspace_root())


@mcp.tool()
def git_push(remote: str = "", branch: str = "") -> dict[str, Any]:
    """Push the current or named branch."""
    args = ["git", "push"]
    if remote:
        args.append(remote)
    if branch:
        args.append(branch)
    return _run(args, workspace_root())


@mcp.tool()
def run_command(argv: list[str], cwd: str = "", timeout_seconds: int = 120) -> dict[str, Any]:
    """Run an allowlisted developer executable without a shell."""
    workdir = _safe_ws(cwd)
    if not workdir.is_dir():
        raise NotADirectoryError(cwd)
    return _run(_validate_command(argv), workdir, timeout_seconds)


@mcp.tool()
def detect_project() -> dict[str, Any]:
    """Detect common project/build systems."""
    root = workspace_root()
    found = {}
    checks = {
        "python": ["pyproject.toml", "requirements.txt", "pytest.ini"],
        "node": ["package.json"],
        "dotnet": ["*.sln", "*.csproj"],
        "rust": ["Cargo.toml"],
        "go": ["go.mod"],
        "cmake": ["CMakeLists.txt"],
    }
    for kind, pats in checks.items():
        hits = []
        for pat in pats:
            if "*" in pat:
                hits.extend(_rel(root, p) for p in root.rglob(pat) if p.is_file())
            elif (root / pat).exists():
                hits.append(pat)
        if hits:
            found[kind] = hits[:100]
    return {"workspace": str(root), "detected": found}


@mcp.tool()
def run_tests(kind: str = "auto", extra_args: list[str] | None = None, timeout_seconds: int = 180) -> dict[str, Any]:
    """Run a conventional project test command."""
    root = workspace_root()
    extra_args = extra_args or []
    if kind == "auto":
        if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists():
            kind = "python"
        elif (root / "package.json").exists():
            kind = "node"
        elif list(root.glob("*.sln")) or list(root.rglob("*.csproj")):
            kind = "dotnet"
        elif (root / "Cargo.toml").exists():
            kind = "rust"
        elif (root / "go.mod").exists():
            kind = "go"
        else:
            raise RuntimeError("Could not detect a supported test system")
    commands = {"python": ["python", "-m", "pytest"], "node": ["npm", "test", "--"], "dotnet": ["dotnet", "test"], "rust": ["cargo", "test"], "go": ["go", "test", "./..."]}
    if kind not in commands:
        raise ValueError(f"Unsupported test kind: {kind}")
    return _run(_validate_command(commands[kind] + extra_args), root, timeout_seconds)


@mcp.tool()
def run_build(kind: str = "auto", extra_args: list[str] | None = None, timeout_seconds: int = 180) -> dict[str, Any]:
    """Run a conventional project build command."""
    root = workspace_root()
    extra_args = extra_args or []
    if kind == "auto":
        if (root / "package.json").exists():
            kind = "node"
        elif list(root.glob("*.sln")) or list(root.rglob("*.csproj")):
            kind = "dotnet"
        elif (root / "Cargo.toml").exists():
            kind = "rust"
        elif (root / "go.mod").exists():
            kind = "go"
        elif (root / "CMakeLists.txt").exists():
            kind = "cmake"
        else:
            raise RuntimeError("Could not detect a supported build system")
    commands = {"node": ["npm", "run", "build", "--"], "dotnet": ["dotnet", "build"], "rust": ["cargo", "build"], "go": ["go", "build", "./..."], "cmake": ["cmake", "--build", "build"]}
    if kind not in commands:
        raise ValueError(f"Unsupported build kind: {kind}")
    return _run(_validate_command(commands[kind] + extra_args), root, timeout_seconds)


if __name__ == "__main__":
    mcp.run()
