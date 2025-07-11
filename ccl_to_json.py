#!/usr/bin/env python3
"""
Convert CCL (Custom Configuration Language) format to JSON.

The CCL format appears to use:
- Key-value pairs with "key = value"
- Nested structures with indentation
- Array-like structures with "= index =" markers
- References using @path/to/value syntax
- Multi-line strings with proper indentation
"""

import json
import re
import sys
from typing import Any, Dict, List, Union


class CCLParser:
    def __init__(self, lines: List[str]):
        self.lines = lines
        self.current_line = 0
        self.references: Dict[str, Any] = {}
        
    def parse(self) -> Dict[str, Any]:
        """Parse the entire CCL file."""
        result = {}
        while self.current_line < len(self.lines):
            self._parse_level(result, 0)
        return self._resolve_references(result)
    
    def _get_indent_level(self, line: str) -> int:
        """Get the indentation level of a line."""
        # Remove line numbers if present (format: "   123→content")
        line = re.sub(r'^\s*\d+→', '', line)
        return len(line) - len(line.lstrip())
    
    def _clean_line(self, line: str) -> str:
        """Remove line numbers and return clean content."""
        return re.sub(r'^\s*\d+→', '', line)
    
    def _parse_level(self, container: Union[Dict, List], expected_indent: int) -> None:
        """Parse a level of indentation into the container."""
        while self.current_line < len(self.lines):
            if not self.lines[self.current_line].strip():
                self.current_line += 1
                continue
                
            clean_line = self._clean_line(self.lines[self.current_line])
            indent = self._get_indent_level(clean_line)
            
            if indent < expected_indent:
                # We've dedented, return to parent level
                return
            elif indent > expected_indent:
                # Skip lines that are more indented than expected (handled by parent)
                self.current_line += 1
                continue
            
            line = clean_line.strip()
            
            # Check for array index marker
            if re.match(r'^=\s*\d+\s*=$', line):
                index = int(re.search(r'\d+', line).group())
                self.current_line += 1
                
                # Parse the array element
                if isinstance(container, dict):
                    # Convert dict to list if we encounter array syntax
                    if not isinstance(container.get('_array'), list):
                        container['_array'] = []
                    
                    element = {}
                    self._parse_level(element, expected_indent + 2)
                    # Ensure list is large enough
                    while len(container['_array']) <= index:
                        container['_array'].append(None)
                    container['_array'][index] = element
                else:
                    element = {}
                    self._parse_level(element, expected_indent + 2)
                    # Ensure list is large enough
                    while len(container) <= index:
                        container.append(None)
                    container[index] = element
            
            # Check for key = value
            elif '=' in line and not line.startswith('='):
                key_part, value_part = line.split('=', 1)
                key = key_part.strip()
                value = value_part.strip()
                
                if not value:
                    # Value is on next lines (nested structure)
                    self.current_line += 1
                    
                    # Check if next line is an array marker
                    if self.current_line < len(self.lines):
                        next_clean = self._clean_line(self.lines[self.current_line])
                        next_line = next_clean.strip()
                        if re.match(r'^=\s*\d+\s*=$', next_line):
                            # This is an array
                            container[key] = []
                            self._parse_level(container[key], expected_indent + 2)
                        else:
                            # This is a nested object
                            container[key] = {}
                            self._parse_level(container[key], expected_indent + 2)
                else:
                    # Inline value
                    self.current_line += 1
                    container[key] = self._parse_value(value)
                    
                    # Store reference path if this is a reference assignment
                    if value.startswith('@'):
                        self._store_reference_path(container, key, value)
            else:
                # Multi-line string content
                if isinstance(container, dict) and len(container) > 0:
                    # Get the last key added
                    last_key = list(container.keys())[-1]
                    if isinstance(container[last_key], str):
                        container[last_key] += '\n' + line
                self.current_line += 1
    
    def _parse_value(self, value: str) -> Any:
        """Parse a value string into appropriate type."""
        # Remove quotes if present
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            return value[1:-1]
        
        # Check for boolean
        if value.lower() == 'true':
            return True
        elif value.lower() == 'false':
            return False
        
        # Check for number
        try:
            if '.' in value:
                return float(value)
            else:
                return int(value)
        except ValueError:
            pass
        
        # Return as string
        return value
    
    def _store_reference_path(self, container: Dict, key: str, ref_path: str) -> None:
        """Store a reference for later resolution."""
        # This is a simplified reference storage - in practice, you'd need
        # to track the full path to this location for proper resolution
        pass
    
    def _resolve_references(self, data: Any) -> Any:
        """Resolve @references in the data structure."""
        # This is a simplified version - proper implementation would
        # traverse the structure and resolve references
        if isinstance(data, dict):
            result = {}
            for key, value in data.items():
                if key == '_array' and isinstance(value, list):
                    # Flatten array representation
                    return [self._resolve_references(item) for item in value]
                else:
                    result[key] = self._resolve_references(value)
            return result
        elif isinstance(data, list):
            return [self._resolve_references(item) for item in data]
        else:
            return data


def convert_ccl_to_json(input_file: str, output_file: str = None) -> None:
    """Convert a CCL file to JSON format."""
    with open(input_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    parser = CCLParser(lines)
    result = parser.parse()
    
    # Write output
    json_str = json.dumps(result, indent=2, ensure_ascii=False)
    
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(json_str)
        print(f"Converted {input_file} to {output_file}")
    else:
        print(json_str)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ccl_to_json.py <input.ccl> [output.json]")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    try:
        convert_ccl_to_json(input_file, output_file)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)