"""Tests for function_calls module using LiteLLM models."""

import pytest
import os
import json
from unittest.mock import patch, MagicMock
from packagerix.function_calls import search_nixpkgs_for_package, web_search, fetch_url_content, search_nix_functions
from packagerix.source_function_calls import create_source_function_calls


class TestSearchNixpkgsForPackage:
    """Tests for search_nixpkgs_for_package function."""
    
    def test_search_returns_json(self):
        """Test that search returns valid JSON output."""
        # Mock the subprocess.run to avoid actual nix calls
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = json.dumps({
                "cowsay": {
                    "description": "Program which generates ASCII pictures of a cow with a message",
                    "pname": "cowsay",
                    "version": "3.8.4"
                }
            })
            mock_run.return_value = mock_result
            
            result = search_nixpkgs_for_package("cowsay")
            
            # Verify it returns JSON
            parsed = json.loads(result)
            assert "cowsay" in parsed
            assert parsed["cowsay"]["pname"] == "cowsay"
    
    def test_search_no_results(self):
        """Test search with no results."""
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stdout = ""
            mock_run.return_value = mock_result
            
            result = search_nixpkgs_for_package("nonexistentpackage123")
            assert "no results found" in result
    
    def test_search_command_format(self):
        """Test that the search command uses the correct format with jq."""
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "{}"
            mock_run.return_value = mock_result
            
            search_nixpkgs_for_package("test")
            
            # Verify the command includes nix search --json and jq pipeline
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "nix search --json nixpkgs test" in args
            assert "jq" in args
            assert "legacyPackages" in args

class TestListDirectoryContents:
    """Tests for list_directory_contents function."""
    functions = create_source_function_calls("/nix")
    
    def test_list_directory_contents_success(self):
        """Test successful listing of directory contents."""
        list_function = self.functions[0]  # TODO this could be improved

        result = list_function(".")
        assert "store" in result
    
    def test_list_directory_contents_invalid_path(self):
        """Test listing contents of an invalid path."""
        list_function = self.functions[0]

        result = list_function("absdjweflTHIS_WILL_NEVER_APPEAR")
        assert "Failed to list contents" in result

    def test_list_directory_contents_outside_store(self):
        """Test listing contents of an invalid path."""
        list_function = self.functions[0]

        result = list_function("../")
        assert "is outside the allowed root directory" in result

class TestWebSearch:
    """Tests for web_search function."""
    
    def test_web_search_success(self):
        """Test successful web search."""
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = json.dumps([
                {
                    "title": "Test Result",
                    "url": "https://example.com",
                    "abstract": "Test abstract"
                }
            ])
            mock_run.return_value = mock_result
            
            result = web_search("test query")
            assert "Test Result" in result
            assert "https://example.com" in result
    
    def test_web_search_timeout(self):
        """Test web search timeout handling."""
        with patch("subprocess.run") as mock_run:
            import subprocess
            mock_run.side_effect = subprocess.TimeoutExpired("ddgr", 30)
            
            result = web_search("test query")
            assert "search timed out" in result
    
    def test_web_search_tool_not_found(self):
        """Test when ddgr is not installed."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            
            result = web_search("test query")
            assert "search tool not found" in result
            assert "ddgr" in result


class TestFetchUrlContent:
    """Tests for fetch_url_content function."""
    
    def test_fetch_url_success(self):
        """Test successful URL fetch."""
        with patch("requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.text = "<html><body>Test content</body></html>"
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response
            
            result = fetch_url_content("https://example.com")
            assert "Test content" in result
            mock_get.assert_called_once_with("https://example.com", timeout=30)
    
    def test_fetch_url_error(self):
        """Test URL fetch with error."""
        with patch("requests.get") as mock_get:
            import requests
            mock_get.side_effect = requests.RequestException("Connection error")
            
            result = fetch_url_content("https://example.com")
            assert "Error fetching URL" in result
            assert "Connection error" in result


class TestSearchNixFunctions:
    """Tests for search_nix_functions function."""
    
    def test_search_nix_functions_no_env_var(self):
        """Test when NOOGLE_FUNCTION_NAMES is not set."""
        with patch.dict(os.environ, {}, clear=True):
            result = search_nix_functions("map")
            assert "NOOGLE_FUNCTION_NAMES environment variable not set" in result
    
    def test_search_nix_functions_file_not_found(self):
        """Test when the function names file doesn't exist."""
        with patch.dict(os.environ, {"NOOGLE_FUNCTION_NAMES": "/nonexistent/file"}):
            result = search_nix_functions("map")
            assert "Noogle function names file not found" in result
    
    def test_search_nix_functions_success(self):
        """Test successful function search using real NOOGLE data."""
        # Skip if NOOGLE_FUNCTION_NAMES not set in environment
        if not os.environ.get("NOOGLE_FUNCTION_NAMES"):
            pytest.skip("NOOGLE_FUNCTION_NAMES not set - run from nix develop shell")
        
        # Test with a common Nix function that should exist
        result = search_nix_functions("map")
        # Should return results or no matches, but not an error
        assert "Error searching Nix functions" not in result
        assert "fzf not found" not in result
    
    def test_search_nix_functions_no_results(self):
        """Test when no functions match."""
        # Skip if NOOGLE_FUNCTION_NAMES not set in environment
        if not os.environ.get("NOOGLE_FUNCTION_NAMES"):
            pytest.skip("NOOGLE_FUNCTION_NAMES not set - run from nix develop shell")
        
        # Use a very unlikely search term
        result = search_nix_functions("verylongnonexistentfunctionname123")
        assert "No Nix functions found" in result or result.strip() == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
