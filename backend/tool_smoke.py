import asyncio, json, os
from types import SimpleNamespace

# Import dei tool
from core.tools.sb_files_tool import SandboxFilesTool
from core.tools.sb_shell_tool import SandboxShellTool

# Base class richiede thread_manager: stub minimale
class DummyDB: client = None
class DummyThreadManager:
    db = DummyDB()

PROJECT_ID = "demo-proj"
WORKSPACE = "/workspace"
FILES_SUBDIR = "smoke_dir"

async def test_files_tool():
    print("\n=== FILES TOOL TESTS ===")
    tm = DummyThreadManager()
    tool = SandboxFilesTool(PROJECT_ID, tm)

    # 1) create_file
    print("-> create_file")
    res = await tool.create_file(
        file_path=f"{FILES_SUBDIR}/hello.txt",
        file_contents="ciao mondo"
    )
    print(res.success, res.output)

    # 2) str_replace
    print("-> str_replace")
    res = await tool.str_replace(
        file_path=f"{FILES_SUBDIR}/hello.txt",
        old_str="ciao",
        new_str="hello"
    )
    print(res.success, res.output)

    # 3) full_file_rewrite
    print("-> full_file_rewrite")
    res = await tool.full_file_rewrite(
        file_path=f"{FILES_SUBDIR}/hello.txt",
        file_contents="file riscritto\nlinea2\n"
    )
    print(res.success, res.output)

    # 4) get_workspace_state (snapshot dei file di /workspace)
    print("-> get_workspace_state (partial)")
    state = await tool.get_workspace_state()
    # stampa solo le prime 10 chiavi per non floodare
    some = list(state.keys())[:10]
    print("tot files in workspace:", len(state), "sample:", some)

    # 5) delete_file
    print("-> delete_file")
    res = await tool.delete_file(file_path=f"{FILES_SUBDIR}/hello.txt")
    print(res.success, res.output)

async def test_shell_tool():
    print("\n=== SHELL TOOL TESTS ===")
    tm = DummyThreadManager()
    tool = SandboxShellTool(PROJECT_ID, tm)

    # 1) execute_command blocking=true (semplice echo)
    print("-> execute_command (blocking=true)")
    res = await tool.execute_command(
        command='echo "hello from shell tool"',
        blocking=True,
        timeout=30
    )
    print(res.success, res.output)

    # 2) execute_command in una subfolder (blocking=true)
    print("-> execute_command in folder (blocking=true)")
    res = await tool.execute_command(
        command='pwd && ls -la',
        folder=FILES_SUBDIR,
        blocking=True,
        timeout=30
    )
    print(res.success)
    out = json.loads(res.output) if isinstance(res.output, str) and res.output.strip().startswith("{") else res.output
    print(out)

    # 3) Non-blocking con tmux: avvia sleep e poi controlla output
    print("-> execute_command non-blocking con tmux")
    res = await tool.execute_command(
        command='echo "start"; sleep 2; echo "done"',
        session_name="smoke_session",
        blocking=False
    )
    print(res.success, res.output)

    # aspetta un attimo e poi controlla output
    await asyncio.sleep(3)
    print("-> check_command_output")
    res = await tool.check_command_output(session_name="smoke_session", kill_session=True)
    print(res.success, res.output)

    # 4) list_commands (dovrebbe essere vuoto se la session è stata killata)
    print("-> list_commands")
    res = await tool.list_commands()
    print(res.success, res.output)

async def main():
    await test_files_tool()
    await test_shell_tool()

if __name__ == "__main__":
    asyncio.run(main())