"""All user prompts for vibenix.

This module contains all functions decorated with @ask_user that interact with the user.
"""

from vibenix.ui.conversation import ask_user, coordinator_error


@ask_user("""@user Welcome to Vibenix! 🚀

I'm your friendly Nix packaging assistant. I can help you:
• Package projects from GitHub
• Build derivations with mkDerivation
• Identify and resolve dependencies
• Iteratively fix build errors

To get started, please provide the GitHub URL of the project you'd like to package.

💡 Tip: Press Ctrl+L to toggle the log window and see application output.""")
def get_project_url(user_input: str) -> str:
    """Get and validate the project URL from user."""
    if not user_input.startswith("https://github.com/"):
        coordinator_error("URL must start with https://github.com/")
        return get_project_url()  # Ask again
    return user_input

