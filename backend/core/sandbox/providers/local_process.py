# backend/core/sandbox/providers/local_process.py
from __future__ import annotations
import asyncio, os, sys, tempfile, textwrap, venv, stat, time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any

SHELL_PATH = os.environ.get("SANDBOX_SHELL", "/bin/sh")  # usa /bin/bash se installata

# ---- FileInfo per list_files ----
@dataclass
class _FileInfo:
    name: str          # relativo alla root passata in list_files
    is_dir: bool
    size: int
    mod_time: float

# ---- FS adapter compatibile con self.sandbox.fs.* usato dai tool Daytona ----
class _LocalFS:
    def __init__(self, workspace_root: str):
        self.root = Path(workspace_root)

    def _abs(self, path: str) -> Path:
        # path può essere già assoluto (/workspace/...), normalizziamo
        p = Path(path)
        return p if p.is_absolute() else (self.root / path)

    async def get_file_info(self, path: str) -> _FileInfo:
        p = self._abs(path)
        st = await asyncio.to_thread(p.stat)
        rel = str(p).replace(str(self.root).rstrip("/") + "/", "")
        return _FileInfo(
            name=rel, is_dir=p.is_dir(), size=st.st_size, mod_time=st.st_mtime
        )

    async def list_files(self, base: str) -> List[_FileInfo]:
        basep = self._abs(base)
        if not basep.exists():
            return []
        infos: List[_FileInfo] = []
        # lista “piatta” dei primi livelli (come spesso fa Daytona); se vuoi ricorsivo usa rglob
        for entry in await asyncio.to_thread(list, basep.iterdir()):
            st = await asyncio.to_thread(entry.stat)
            rel = str(entry).replace(str(self.root).rstrip("/") + "/", "")
            infos.append(_FileInfo(
                name=rel, is_dir=entry.is_dir(), size=st.st_size, mod_time=st.st_mtime
            ))
        return infos

    async def download_file(self, path: str) -> bytes:
        p = self._abs(path)
        return await asyncio.to_thread(p.read_bytes)

    async def upload_file(self, data: bytes, path: str) -> None:
        p = self._abs(path)
        # ✅ usa keyword per parents/exist_ok
        await asyncio.to_thread(p.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(p.write_bytes, data)


    async def create_folder(self, path: str, permissions: str = "755") -> None:
        p = self._abs(path)
        # ✅ idem qui
        await asyncio.to_thread(p.mkdir, parents=True, exist_ok=True)
        await self.set_file_permissions(str(p), permissions)

    async def set_file_permissions(self, path: str, permissions: str) -> None:
        p = self._abs(path)
        mode = int(permissions, 8)
        await asyncio.to_thread(os.chmod, p, mode)

    async def delete_file(self, path: str) -> None:
        p = self._abs(path)
        if p.is_dir():
            # eliminazione ricorsiva robusta
            for sub in await asyncio.to_thread(list, p.rglob("*")):
                if sub.is_file():
                    sub.unlink(missing_ok=True)
            # prova a rimuovere eventuali dir vuote dal fondo
            for sub in sorted(p.rglob("*"), reverse=True):
                if sub.is_dir():
                    sub.rmdir()
            await asyncio.to_thread(p.rmdir)
        else:
            await asyncio.to_thread(p.unlink, True)

# ---- compat process API (no-op sufficiente per start_supervisord e affini) ----
class _ProcessAPI:
    async def create_session(self, session_id: str) -> None:
        return  # no-op

    async def delete_session(self, session_id: str) -> None:
        return  # no-op

    async def execute_session_command(self, session_id: str, req) -> Dict[str, Any]:
        cmd = getattr(req, "command", None) if not isinstance(req, str) else req
        if not cmd:
            return {"code": 1, "stdout": "", "stderr": "no command provided", "cmd_id": ""}
        proc = await asyncio.create_subprocess_shell(
            cmd, cwd="/workspace",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            executable=SHELL_PATH,
        )
        out, err = await proc.communicate()
        # finta cmd_id per compatibilità
        return {"code": proc.returncode, "stdout": out.decode(), "stderr": err.decode(), "cmd_id": "local"}

    async def get_session_command_logs(self, session_id: str, command_id: str) -> str:
        # Non avendo buffer persistente, restituiamo stringa vuota (i tool aggiornati non lo useranno)
        return ""

# ---- sandbox ----
@dataclass
class LocalProcessSandbox:
    id: str
    workspace_root: str = "/workspace"
    state: str = "RUNNING"

    def __post_init__(self):
        self._root = Path(self.workspace_root)
        self._root.mkdir(parents=True, exist_ok=True)
        # Create .venvs in the workspace root for Python virtual environments
        self._venvs = self._root / ".venvs"
        self._venvs.mkdir(parents=True, exist_ok=True)
        self.process = _ProcessAPI()
        self.fs = _LocalFS(self.workspace_root)   # FS interface for tools

    def _venv_path(self, project_id: str) -> Path:
        return self._venvs / project_id

    def _ensure_venv(self, project_id: str) -> Path:
        v = self._venv_path(project_id)
        if not (v / "pyvenv.cfg").exists():
            venv.EnvBuilder(with_pip=True, clear=False).create(v)
        return v
    
    async def get_preview_link(self, port: int):
        """Stub per compatibilità con Daytona. In locale non esistono preview."""
        class DummyLink:
            def __init__(self):
                self.url = None
                self.token = None
        return DummyLink()

    def _bin(self, venv_dir: Path, exe: str) -> str:
        return str(venv_dir / "bin" / exe)

    async def exec(self, cmd: str | List[str], cwd: Optional[str] = None, timeout: int = 900) -> Dict[str, Any]:
        if isinstance(cmd, list):
            cmd = " ".join([str(c) for c in cmd])
        proc = await asyncio.create_subprocess_shell(
            cmd, cwd=cwd or self.workspace_root,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            executable=SHELL_PATH,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"code": 124, "stdout": "", "stderr": "timeout"}
        return {"code": proc.returncode, "stdout": out.decode(), "stderr": err.decode()}

    async def run_python(self, code: str, project_id: str, requirements: Optional[List[str]] = None,
                         workdir: Optional[str] = None, timeout: int = 900, python_bin: Optional[str] = None) -> Dict[str, Any]:
        venv_dir = self._ensure_venv(project_id)
        py = python_bin or self._bin(venv_dir, "python")
        pip = [py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
        wd = Path(workdir or (self._root / project_id)); wd.mkdir(parents=True, exist_ok=True)
        if requirements:
            inst = await self.exec(" ".join([*pip, *requirements]), cwd=str(wd), timeout=timeout)
            if inst["code"] != 0:
                return {"code": inst["code"], "stdout": inst["stdout"], "stderr": inst["stderr"]}
        with tempfile.NamedTemporaryFile(dir=wd, suffix=".py", delete=False, mode="w") as f:
            f.write(textwrap.dedent(code)); script = f.name
        proc = await asyncio.create_subprocess_exec(py, script, cwd=str(wd),
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"code": 124, "stdout": "", "stderr": "timeout", "script": script, "workdir": str(wd)}
        return {"code": proc.returncode, "stdout": out.decode(), "stderr": err.decode(), "script": script, "workdir": str(wd)}

    async def destroy(self) -> bool:
        self.state = "ARCHIVED"; return True