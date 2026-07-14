import shutil
import os
import git
from jinja2 import Template
from typing import Optional

from vibenix import config
from vibenix.ui.logging_config import logger
from vibenix.nixpkgs_lock import get_nixpkgs_lock_info

from pathlib import Path
import subprocess

def init_flake(reference_dir: Optional[Path] = None) -> None:

    logger.info(f"Creating flake at {config.flake_dir} from reference directory {config.template_dir}")

    # Create the flake directory
    os.makedirs(config.flake_dir, mode=0o755, exist_ok=True)

    # Create VM task directory for scripts and output
    vm_task_dir = config.flake_dir / "vm-task"
    os.makedirs(vm_task_dir, mode=0o755, exist_ok=True)

    # Copy default packages.nix to flake directory (not vm-task subdir)
    default_packages_src = config.template_dir / 'packages.nix'
    default_packages_dst = config.flake_dir / 'packages.nix'
    if default_packages_src.exists():
        shutil.copy2(default_packages_src, default_packages_dst)
        os.chmod(default_packages_dst, 0o644)

    # Copy package.nix directly
    package_nix_src = config.template_dir / 'package.nix'
    package_nix_dst = config.flake_dir / 'package.nix'
    if package_nix_src.exists():
        shutil.copy2(package_nix_src, package_nix_dst)
        os.chmod(package_nix_dst, 0o644)

    # Template flake.nix with the flake directory path and VM timeout
    flake_nix_src = config.template_dir / 'flake.nix'
    flake_nix_dst = config.flake_dir / 'flake.nix'
    if flake_nix_src.exists():
        with open(flake_nix_src, 'r') as f:
            template = Template(f.read())
        # Default VM timeout of 60 seconds (should be enough for most scripts after warmup)
        flake_content = template.render(flake_dir=str(config.flake_dir), vm_timeout=60)
        with open(flake_nix_dst, 'w') as f:
            f.write(flake_content)
        os.chmod(flake_nix_dst, 0o644)

    # Generate flake.lock from template
    template_path = config.template_dir / 'flake.lock.j2'
    with open(template_path, 'r') as f:
        template = Template(f.read())

    # Get nixpkgs lock info for the configured commit
    lock_info = get_nixpkgs_lock_info(config.nixpkgs_commit)

    # Render the template
    flake_lock_content = template.render(**lock_info)

    # Write flake.lock
    flake_lock_path = config.flake_dir / 'flake.lock'
    with open(flake_lock_path, 'w') as f:
        f.write(flake_lock_content)
    os.chmod(flake_lock_path, 0o644)

    ## If reference_dir is provided, copy its contents into the flake directory
    # Although this is very lenient, should still only be used with [package.nix, flake.lock]
    if reference_dir:
        ref_path = Path(reference_dir).resolve()
        logger.info(f"Copying contents from reference directory: {ref_path}")

        for item in ref_path.iterdir():
            dest = config.flake_dir / item.name
            if item.is_dir(): # TODO improve ts
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
            os.chmod(dest, 0o644 if dest.is_file() else 0o755)

    repo = git.Repo.init(config.flake_dir.as_posix())
    repo.git.add('-A')
    if not reference_dir:
        repo.index.commit("add empty template")
    else:
        repo.index.commit("initialize flake from reference directory")

def update_flake(new_content, commit_msg: str = "") -> str:
    file_path = config.flake_dir / "package.nix"

    # Open the file in write mode and overwrite it with new_content
    with open(file_path, 'w') as file:
        file.write(new_content)

    repo = git.Repo(config.flake_dir.as_posix())
    repo.git.add('-A')
    if commit_msg == "":
        return None
    commit = repo.index.commit(commit_msg)
    return commit.hexsha

def get_package_contents() -> str:
    file_path = config.flake_dir / "package.nix"
    with open(file_path, 'r') as file:
        return file.read()

def stage_all_files() -> None:
    repo = git.Repo(config.flake_dir.as_posix())
    repo.git.add('-A')

def get_package_path() -> str:
    file_path = config.flake_dir / "package.nix"
    return file_path.as_posix()

def revert_to_commit(commit_hash: str) -> None:
    """
    Revert the packaging files to a previous commit.
    Used to sync package.nix with the best known solution during rollbacks.
    
    Args:
        commit_hash: The commit hash to revert to
    """
    repo = git.Repo(config.flake_dir.as_posix())
    repo.git.reset('--hard', commit_hash)


current_system = None

def get_current_system() -> str:
    """Get the current Nix system."""
    global current_system
    if current_system:
        return current_system
    result = subprocess.run(
        ["nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem"],
        cwd=config.flake_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    current_system = result.stdout.strip()
    return current_system

def get_attr_pos(attr: str) -> Optional[int]:
    """Get the line number of an attribute in package.nix."""
    result = subprocess.run(
        ["nix", "eval", "--impure", "--apply", f'pkg: (builtins.unsafeGetAttrPos "{attr}" pkg).line'],
        cwd=config.flake_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"get_attr_pos: Failed to get attribute position: {result.stderr}")
    pos_str = result.stdout.strip()
    try:
        return int(pos_str)
    except ValueError:
        raise RuntimeError(f"get_attr_pos: Failed to parse attribute position: {pos_str}")

def stash_flake_lock() -> None:
    """Stash the flake.lock file to prevent it from being modified during package updates."""
    lock_path = config.flake_dir / "flake.lock"
    if lock_path.exists():
        lock_path.rename(config.flake_dir / "flake.lock.stash")

def unstash_flake_lock() -> None:
    """Unstash the flake.lock file after package updates."""
    stashed_lock_path = config.flake_dir / "flake.lock.stash"
    if stashed_lock_path.exists():
        stashed_lock_path.rename(config.flake_dir / "flake.lock")
