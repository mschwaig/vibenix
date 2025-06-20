import re

import requests
from bs4 import BeautifulSoup
import re
import json
from urllib.parse import urlparse
import subprocess

from diskcache import Cache
from packagerix.ui.logging_config import logger

cache = Cache("cachedir")

@cache.memoize()
def scrape_and_process(url):
    # Fetch the webpage content
    response = requests.get(url)
    html = response.text

    # Parse the HTML content
    soup = BeautifulSoup(html, 'html.parser')

    # Extract text from the webpage
    # You might need to adjust the selection to your specific needs
    text = ' '.join(soup.stripped_strings)

    # Basic cleanup to remove unwanted characters or sections
    cleaned_text = re.sub(r'\s+', ' ', text)  # Remove extra whitespaces

    return cleaned_text

@cache.memoize()
def fetch_github_release_data(url):
    """Fetch release data from GitHub API for the given repository URL."""
    # Parse the GitHub URL to extract owner and repo
    parsed_url = urlparse(url)
    if parsed_url.netloc != 'github.com':
        logger.warning(f"Not a GitHub URL: {url}")
        return None
    
    path_parts = parsed_url.path.strip('/').split('/')
    if len(path_parts) < 2:
        logger.warning(f"Invalid GitHub URL format: {url}")
        return None
    
    owner = path_parts[0]
    repo = path_parts[1]
    
    # Fetch latest release data from GitHub API
    # NOTE: To simplify the threat model, we only fetch auto-generated release artifacts
    # (tarball_url, zipball_url) and not user-uploaded assets. This ensures we're only
    # getting source code directly from GitHub's servers.
    api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    
    try:
        response = requests.get(api_url, headers={'Accept': 'application/vnd.github.v3+json'})
        if response.status_code == 404:
            # No releases found, try tags instead
            logger.info(f"No releases found for {owner}/{repo}, trying tags")
            tags_url = f"https://api.github.com/repos/{owner}/{repo}/tags"
            tags_response = requests.get(tags_url, headers={'Accept': 'application/vnd.github.v3+json'})
            if tags_response.status_code == 200:
                tags = tags_response.json()
                if tags:
                    # Use the first (latest) tag
                    latest_tag = tags[0]
                    return {
                        'tag_name': latest_tag['name'],
                        'tarball_url': latest_tag['tarball_url'],
                        'zipball_url': latest_tag['zipball_url'],
                        'commit': latest_tag['commit'],
                        'source': 'tags'
                    }
            return None
        
        response.raise_for_status()
        release_data = response.json()
        
        # Extract relevant information
        # Only include auto-generated GitHub artifacts (tarball/zipball URLs)
        # Exclude user-uploaded assets for security reasons
        return {
            'tag_name': release_data.get('tag_name'),
            'name': release_data.get('name'),
            'published_at': release_data.get('published_at'),
            'tarball_url': release_data.get('tarball_url'),
            'zipball_url': release_data.get('zipball_url'),
            'html_url': release_data.get('html_url'),
            'source': 'releases'
        }
    except requests.RequestException as e:
        logger.error(f"Failed to fetch release data from GitHub API: {e}")
        return None

def fill_src_attribute(template, project_url, rev_string):
    """Fill the pname, version and src attributes in the template."""
    # Run nurl on the project URL to get src attribute
    command = ["nurl", project_url, rev_string]
    nurl_output = subprocess.run(command, capture_output=True, text=True)
    src_attr = str(nurl_output.stdout)

    # From the revision string, extract the version (SemVer) 
    pattern = r"(\d+(\.\d+)*(-[0-9a-zA-Z-.]*)?)"
    match = re.search(pattern, rev_string)
    if match:
        version, rev_string = match.group(0), rev_string.replace(match.group(0), "${version}", 1)
    else:
        logger.error(f"Could not extract version from revision string: {rev_string}")
        raise ValueError(f"Could not extract version from revision string: {rev_string}")

    # Replace rev = ... with ${version}
    src_attr = re.sub(r'rev\s*=\s*".*?"', f'rev = \"{rev_string}\"', src_attr)
    # Search for the hash in the output
    nurl_hash = re.search(r'hash\s*=\s*"(.*?)"', src_attr)
    if nurl_hash:
        nurl_hash = nurl_hash.group(1)
    else:
        logger.error("Could not extract hash from nurl output")
        raise ValueError("Could not extract hash from nurl output")

    store_path = subprocess.run(["nix-store", "--print-fixed-path", "sha256",
                                 "--recursive", nurl_hash, "source"],
                                 capture_output=True, text=True)
    store_path = str(store_path.stdout).strip()

    # Indent properly
    lines = src_attr.splitlines()
    for i in range(1, len(lines)):
        lines[i] = "  " + lines[i]
    src_attr = "\n".join(lines)
    logger.debug(f"nurl output: {src_attr}")

    if not (match := re.search(r'repo\s*=\s*"(.*?)"', src_attr)):
        logger.error("Could not extract repo")
        raise ValueError("Could not extract repo from nurl output")
    else:
        repo = match.group(1)
    # Fill in the "pname = ...", "version = ..." attributes in the template
    filled_template = template.replace("pname = ...", f"pname = \"{repo}\"")
    filled_template = filled_template.replace("version = ...", f"version = \"{version}\"")

    # Replace the src attribute in the template with the extracted src
    pattern = r"fetchFromGitHub\s*\{.*?\}"
    filled_template = re.sub(pattern, src_attr, filled_template, flags=re.DOTALL)
    logger.debug(f"Final filled template: {filled_template}")
    
    return filled_template, store_path

def fetch_combined_project_data(url):
    """Fetch both HTML and API data for a GitHub project."""
    # Get HTML content
    html_content = scrape_and_process(url)
    
    # Get API release data
    release_data = fetch_github_release_data(url)
    
    # Combine the data
    combined_data = f"{html_content}\n\n"
    
    if release_data:
        combined_data += "--- GitHub Release Information ---\n"
        combined_data += f"Latest release/tag: {release_data.get('tag_name', 'N/A')}\n"
        
        if release_data.get('name'):
            combined_data += f"Release name: {release_data['name']}\n"
        
        if release_data.get('published_at'):
            combined_data += f"Published at: {release_data['published_at']}\n"
        
        combined_data += f"Source tarball: {release_data.get('tarball_url', 'N/A')}\n"
        combined_data += f"Source zipball: {release_data.get('zipball_url', 'N/A')}\n"
        
        if release_data.get('assets'):
            combined_data += "\nRelease assets:\n"
            for asset in release_data['assets']:
                combined_data += f"  - {asset['name']}: {asset['browser_download_url']}\n"
        
        combined_data += f"\nData source: {release_data.get('source', 'unknown')}\n"
    else:
        combined_data += "\n--- No GitHub Release Information Available ---\n"
    
    return combined_data

def extract_updated_code(model_reply):
    pattern = r"^```nix\n(.*?)\n```$"

    matches = list(re.finditer(pattern, model_reply, re.DOTALL | re.MULTILINE))
    if len(matches) == 0:
        error_msg = "No section delimited by triple backticks was found in the model's reply"
        logger.error(error_msg)
        raise ValueError(error_msg)
    elif len(matches) > 1:
        logger.warning(f"Reply contained {len(matches)} quoted sections, using the first one")
    
    return matches[0].group(1)
