"""Minimal Python execution view with no service source, credentials or network."""

from pathlib import Path
import shutil
import sys
import sysconfig


# No .pth / editable-install files or arbitrary site-packages are mounted.
PACKAGES = ("numpy", "numpy.libs", "PIL", "pillow.libs", "scipy", "scipy.libs",
            "trimesh", "networkx", "packaging", "typing_extensions")


# 根据已固定的工作区和脚本描述符构造隔离执行命令；调用方需保持描述符打开。
def python_command(workspace, script, *, workspace_fd, script_fd,
                   conversation_root, conversation_fd, argv=()):
    """Build a command from pinned workspace/script descriptors.

    The caller must keep all descriptors open and pass them via Popen pass_fds.
    Bubblewrap consumes them for mounts and closes them before running Python.
    """
    runner = shutil.which("bwrap")
    if runner is None:
        raise RuntimeError("Task-code sandbox requires bubblewrap (bwrap) on the backend service PATH")
    workspace = Path(workspace).absolute()
    script = Path(script).absolute()
    conversation_root = Path(conversation_root).absolute()
    if not workspace.is_relative_to(conversation_root) or '..' in workspace.parts:
        raise PermissionError("Task directory must be inside this conversation workspace")
    if not script.is_relative_to(workspace) or '..' in script.parts:
        raise PermissionError("Task script must be inside this conversation workspace")
    if any(type(descriptor) is not int or descriptor < 0
           for descriptor in (workspace_fd, script_fd, conversation_fd)):
        raise ValueError('Pinned workspace and script descriptors are required')
    command = [str(Path(runner).absolute()), "--die-with-parent", "--unshare-all", "--new-session",
               "--tmpfs", "/tmp", "--dir", "/home/task", "--dir", "/run",
               "--proc", "/proc", "--dev", "/dev"]
    mounted = set()

    # 去重添加存在路径的只读挂载，避免重复覆盖同一目标位置。
    def mount(source, destination=None):
        source = Path(source)
        if source.exists():
            target = str(destination or source)
            if target not in mounted:
                command.extend(["--ro-bind", str(source.resolve()), target])
                mounted.add(target)

    for system in ("/usr/lib", "/lib", "/lib64", "/etc/ld.so.cache"):
        mount(system)
    executable = Path(sys.executable).resolve()
    mount(executable)
    standard = Path(sysconfig.get_path("stdlib"))
    mount(standard)
    sites = {Path(sysconfig.get_path(name)) for name in ("purelib", "platlib")}
    for site in sorted(sites):
        command.extend(["--tmpfs", str(site)])
        for package in PACKAGES:
            candidate = site / package
            if not candidate.exists():
                candidate = site / (package + ".py")
            if candidate.is_symlink():
                raise RuntimeError("Task runtime cannot contain linked packages: " + package)
            mount(candidate)
    for library in Path(sys.prefix, "lib").glob("*.so*"):
        if library.is_file():
            mount(library)
    # All tools share the Host's absolute namespace. Conversation inputs are
    # readable; only this task's output directory is writable.
    command.extend(["--ro-bind-fd", str(conversation_fd), str(conversation_root),
                    "--bind-fd", str(workspace_fd), str(workspace),
                    "--ro-bind-data", str(script_fd), "/run/benchforge-task.py",
                    "--chdir", str(workspace),
                    "--setenv", "HOME", "/home/task", "--setenv", "PYTHONIOENCODING", "utf8",
                    str(executable), "-I", "/run/benchforge-task.py", *map(str, argv)])
    return command
