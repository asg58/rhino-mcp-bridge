# Rhino MCP Bridge

Combined local MCP bridge for Rhino reverse-engineering and an editable VS Code workspace.

## Architecture

ChatGPT / Codex -> OpenAI tunnel-client -> rhino-bridge MCP -> two local roots

- Rhino root: read-only static inspection of files, DLL/EXE/RHP assemblies, hashes, PE imports and .NET metadata.
- Workspace root: read/write project access, Git, builds/tests, and a restricted developer-command runner.

No API keys or local machine secrets belong in this repository.

## Windows quick start

Open PowerShell in the cloned repository and run:

    Set-ExecutionPolicy -Scope Process Bypass
    .\setup.ps1 -RhinoRoot "C:\Program Files\Rhino 8" -WorkspaceRoot "D:\mijn\_app\rhino2"

The setup script creates a private virtual environment, installs dependencies, runs security tests, and writes a local .env.ps1 file. That file is gitignored.

Start the MCP locally:

    .\start-local.ps1

## Tunnel to ChatGPT

Download the supported OpenAI tunnel client:

    .\download-tunnel-client.ps1

Set the runtime key only in the current PowerShell session:

    $env:CONTROL_PLANE_API_KEY="YOUR_RUNTIME_KEY"

Then start the tunnel:

    .\start-tunnel.ps1 -TunnelId "tunnel_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

The script creates/uses a profile named rhino-bridge, runs doctor --explain, then keeps tunnel-client in the foreground. Leave that terminal open while ChatGPT uses the connector.

In ChatGPT open Settings -> Connectors, choose Connection: Tunnel, and select or paste the same tunnel_id.

## Security model

- Rhino root has no write/delete/exec tools.
- Workspace access is sandboxed to WORKSPACE_ROOT.
- Secret-like files (.env, SSH/private keys, PEM/PFX/KEY and credential files) are blocked by default.
- Command execution does not use a shell; only an allowlist of developer executables is accepted.
- Destructive Git/system commands are blocked.
- Command runtime and output are bounded.
- Runtime/admin API keys are never committed.

## First Rhino investigation

1. rhino_status
2. rhino_list_tree(max_depth=4)
3. rhino_glob("*.rhp")
4. rhino_glob("*.dll")
5. rhino_analyze_pe(...) and rhino_analyze_dotnet(...)
6. rhino_dependency_summary("*.dll")
7. Save research notes/results in the workspace and commit them with Git.
