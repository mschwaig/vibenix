import shutil
import os
import git
from jinja2 import Template

from vibenix import config
from vibenix.ui.logging_config import logger
from vibenix.nixpkgs_lock import get_nixpkgs_lock_info


def init_flake():

    logger.info(f"Creating flake at {config.flake_dir} from reference directory {config.template_dir}")

    # Create the flake directory
    os.makedirs(config.flake_dir, mode=0o755, exist_ok=True)

    # Copy only the necessary files
    files_to_copy = ['flake.nix', 'package.nix']

    for filename in files_to_copy:
        src = config.template_dir / filename
        dst = config.flake_dir / filename
        if src.exists():
            shutil.copy2(src, dst)
            os.chmod(dst, 0o644)

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

    repo = git.Repo.init(config.flake_dir.as_posix())
    repo.git.add('-A')
    repo.index.commit("add empty template")

def update_flake(new_content, do_commit: bool = False) -> str:
    file_path = config.flake_dir / "package.nix"

    # Open the file in write mode and overwrite it with new_content
    with open(file_path, 'w') as file:
        file.write(new_content)

    repo = git.Repo(config.flake_dir.as_posix())
    repo.git.add('-A')
    if not do_commit:
        return None
    commit = repo.index.commit("build step")
    return commit.hexsha

def get_package_contents() -> str:
    file_path = config.flake_dir / "package.nix"
    with open(file_path, 'r') as file:
        return file.read()

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
